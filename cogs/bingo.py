"""
Wizard Bingo — lobby Join → Headmaster Start → auto-calls every 20s.

    /bingo card              - re-show your card (or join if a lobby is open)
    /bingo mark              - mark called squares (select menu)
    /bingo called            - what's been called so far
    /bingo status            - round status + your progress

Staff (via /staffgame bingo):
    start (pick mode) / call (optional manual) / end / winners
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.bingo_board import render_card
from cogs.bingo_patterns import (
    MODE_CHOICES,
    MODE_RANDOM_PATTERN,
    MODE_TRADITIONAL,
    has_bingo,
    mode_label,
    pick_random_pattern,
    preview_for_round,
)
from cogs.velmora_channels import BINGO_CHANNEL_ID

log = logging.getLogger("velmora.bingo")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "bingo_state.json"
SQUARES_PATH = DATA_DIR / "bingo_squares.json"

GOLD = 0xD4A84A
BINGO_POINTS = 5
MAX_WINNERS = 5
ROWS = COLS = 5
CALL_INTERVAL_SEC = 20
PHASE_LOBBY = "lobby"
PHASE_CALLING = "calling"


def _channel_hint() -> str:
    return f"Wizard Bingo only runs in <#{BINGO_CHANNEL_ID}>."


def _blank() -> dict:
    return {"active": None, "cards": {}, "history": []}


def _load_pool() -> list[str]:
    try:
        with open(SQUARES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        pool = [str(s).strip() for s in data if str(s).strip()]
        seen, out = set(), []
        for s in pool:
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out
    except (OSError, json.JSONDecodeError):
        log.exception("Bingo square pool unreadable")
        return []


def _build_grid(pool: list[str], round_id: str, user_id: int) -> list[list[str]]:
    if len(pool) < 24:
        raise RuntimeError("Bingo square pool needs at least 24 entries.")
    seed = hashlib.sha256(f"bingo:{round_id}:{user_id}".encode()).digest()
    rng = random.Random(seed)
    picks = rng.sample(pool, 24)
    grid = [[""] * COLS for _ in range(ROWS)]
    i = 0
    for r in range(ROWS):
        for c in range(COLS):
            if r == 2 and c == 2:
                grid[r][c] = "FREE"
            else:
                grid[r][c] = picks[i]
                i += 1
    return grid


class Bingo(commands.Cog):
    bingo = app_commands.Group(
        name="bingo", description="Wizard Bingo — your card for the open round."
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.pool = _load_pool()
        self._call_task: asyncio.Task | None = None
        self._lobby_views: dict[str, LobbyView] = {}

    async def cog_load(self) -> None:
        round_ = self.active()
        if not round_:
            return
        rid = round_["round_id"]
        # Re-attach lobby buttons after restart
        if round_.get("phase") == PHASE_LOBBY:
            view = LobbyView(self, rid, round_.get("started_by"))
            self._lobby_views[rid] = view
            self.bot.add_view(view)
        elif round_.get("phase") == PHASE_CALLING:
            self._ensure_call_loop(rid)

    async def cog_unload(self) -> None:
        if self._call_task and not self._call_task.done():
            self._call_task.cancel()
            try:
                await self._call_task
            except asyncio.CancelledError:
                pass

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return _blank()
        except (OSError, json.JSONDecodeError):
            log.exception("Bingo state unreadable — starting empty.")
            return _blank()
        data.setdefault("active", None)
        data.setdefault("cards", {})
        data.setdefault("history", [])
        return data

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save bingo state.")

    def active(self) -> Optional[dict]:
        return self.state.get("active")

    def _in_bingo_channel(self, interaction: discord.Interaction) -> bool:
        return interaction.channel_id == BINGO_CHANNEL_ID

    async def _require_bingo_channel(self, interaction: discord.Interaction) -> bool:
        if self._in_bingo_channel(interaction):
            return True
        await interaction.response.send_message(_channel_hint(), ephemeral=True)
        return False

    def _is_host(self, user: discord.abc.User, round_: dict | None = None) -> bool:
        round_ = round_ or self.active()
        if not round_:
            return False
        if user.id == round_.get("started_by"):
            return True
        store = self.bot.get_cog("Store")
        return bool(store and store.is_staff(user))

    def card_of(self, user_id: int) -> Optional[dict]:
        round_ = self.active()
        if not round_:
            return None
        rec = self.state["cards"].get(str(user_id))
        if not rec or rec.get("round_id") != round_["round_id"]:
            return None
        return rec

    def ensure_card(self, user_id: int) -> dict:
        round_ = self.active()
        if not round_:
            raise RuntimeError(
                f"No Wizard Bingo round is open. Staff start one in <#{BINGO_CHANNEL_ID}>."
            )
        existing = self.card_of(user_id)
        if existing:
            return existing
        grid = _build_grid(self.pool, round_["round_id"], user_id)
        marked = [[False] * COLS for _ in range(ROWS)]
        marked[2][2] = True  # FREE
        rec = {
            "round_id": round_["round_id"],
            "grid": grid,
            "marked": marked,
            "bingo_at": None,
        }
        self.state["cards"][str(user_id)] = rec
        players = round_.setdefault("players", [])
        if user_id not in players:
            players.append(user_id)
        self.save()
        return rec

    def _png_for(self, member, rec: dict) -> discord.File:
        round_ = self.active() or {}
        called = set(round_.get("called") or [])
        png = render_card(
            grid=rec["grid"],
            marked=rec["marked"],
            called=called,
            player_name=member.display_name,
            round_label=round_.get("name") or "Wizard Bingo",
        )
        return discord.File(io.BytesIO(png), filename="bingo_card.png")

    def _lobby_embed(self, round_: dict) -> discord.Embed:
        players = round_.get("players") or []
        mode = round_.get("mode") or MODE_TRADITIONAL
        pattern_id = round_.get("pattern_id")
        names = mode_label(mode, pattern_id)
        preview = preview_for_round(mode, pattern_id)
        joined = (
            ", ".join(f"<@{uid}>" for uid in players[:30])
            if players
            else "_Nobody yet — tap **Join**_"
        )
        if len(players) > 30:
            joined += f" …+{len(players) - 30}"
        embed = discord.Embed(
            title=f"🃏 {round_.get('name') or 'Wizard Bingo'} — signup",
            description=(
                f"**Mode:** {names}\n\n"
                f"{preview}\n\n"
                "Tap **Join** for your unique card (ephemeral).\n"
                "When everyone’s ready, the host taps **Start** — "
                f"then squares auto-call every **{CALL_INTERVAL_SEC}s**.\n"
                "Mark matches with `/bingo mark`.\n"
                f"First **{MAX_WINNERS}** BINGOs earn **{BINGO_POINTS}** house points each."
            ),
            color=GOLD,
        )
        embed.add_field(
            name=f"Joined ({len(players)})",
            value=joined[:1024],
            inline=False,
        )
        embed.set_footer(text="Bingo channel only · Host Start begins calling")
        return embed

    async def _refresh_lobby_message(self, round_: dict) -> None:
        mid = round_.get("lobby_message_id")
        if not mid:
            return
        ch = self.bot.get_channel(BINGO_CHANNEL_ID)
        if ch is None or not hasattr(ch, "fetch_message"):
            return
        try:
            msg = await ch.fetch_message(mid)
            view = self._lobby_views.get(round_["round_id"])
            await msg.edit(embed=self._lobby_embed(round_), view=view)
        except discord.DiscordException:
            log.debug("Could not refresh bingo lobby message", exc_info=True)

    def _award_bingo(self, member: discord.Member, rec: dict) -> Optional[str]:
        round_ = self.active()
        if not round_ or rec.get("bingo_at"):
            return None
        mode = round_.get("mode") or MODE_TRADITIONAL
        # random_pattern stores resolved pattern_id; win check uses pattern cells
        check_mode = mode
        if mode == MODE_RANDOM_PATTERN:
            check_mode = "pattern"
        if not has_bingo(
            rec["grid"], rec["marked"], check_mode, round_.get("pattern_id")
        ):
            return None
        winners = round_.setdefault("winners", [])
        if any(w.get("id") == member.id for w in winners):
            rec["bingo_at"] = time.time()
            self.save()
            return None
        if len(winners) >= MAX_WINNERS:
            rec["bingo_at"] = time.time()
            self.save()
            return (
                f"🎉 **BINGO!** {member.mention} completed the pattern — "
                f"winner slots are full this round (first {MAX_WINNERS})."
            )
        rec["bingo_at"] = time.time()
        place = len(winners) + 1
        winners.append({"id": member.id, "at": rec["bingo_at"], "place": place})
        self.save()

        store = self.bot.get_cog("Store")
        house = store.member_house(member) if store else None
        awarded = False
        if store and house:
            entry = store.record(
                house=house,
                delta=BINGO_POINTS,
                actor_id=member.id,
                target_id=member.id,
                reason=f"Wizard Bingo #{place}",
            )
            awarded = entry is not None
        pts = f" **+{BINGO_POINTS}** house points!" if awarded else ""
        return f"🎉 **BINGO!** {member.mention} is winner #{place}.{pts}"

    def _ensure_call_loop(self, round_id: str) -> None:
        if self._call_task and not self._call_task.done():
            return
        self._call_task = asyncio.create_task(
            self._auto_call_loop(round_id), name=f"bingo-call-{round_id}"
        )

    def _stop_call_loop(self) -> None:
        if self._call_task and not self._call_task.done():
            self._call_task.cancel()
        self._call_task = None

    async def _auto_call_loop(self, round_id: str) -> None:
        # Short beat so Start feedback lands first
        await asyncio.sleep(3)
        try:
            while True:
                round_ = self.active()
                if (
                    not round_
                    or round_.get("round_id") != round_id
                    or round_.get("phase") != PHASE_CALLING
                ):
                    return
                label = self._pick_next_call(round_)
                if label is None:
                    ch = self.bot.get_channel(BINGO_CHANNEL_ID)
                    if ch and hasattr(ch, "send"):
                        try:
                            await ch.send(
                                "📣 **All squares called.** "
                                "Mark what you can — host can `/staffgame bingo end` when ready."
                            )
                        except discord.DiscordException:
                            log.exception("Bingo pool-exhausted announce failed")
                    return
                await self._announce_call(round_, label)
                await asyncio.sleep(CALL_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Bingo auto-call loop crashed")

    def _pick_next_call(self, round_: dict) -> str | None:
        called = set(round_.get("called") or [])
        remaining = [s for s in self.pool if s not in called]
        if not remaining:
            return None
        # Deterministic-ish shuffle per round so restarts don't re-order wildly
        rng = random.Random(f"{round_['round_id']}:{len(called)}")
        return rng.choice(remaining)

    async def _announce_call(self, round_: dict, label: str) -> None:
        called = round_.setdefault("called", [])
        if label in called:
            return
        called.append(label)
        round_["last_call_at"] = time.time()
        self.save()
        embed = discord.Embed(
            title="📣 Bingo!",
            description=(
                f"**{label}**\n\n"
                f"Mark it with `/bingo mark` if it's on your card.\n"
                f"_Next call in ~{CALL_INTERVAL_SEC}s._"
            ),
            color=GOLD,
        )
        embed.set_footer(
            text=(
                f"{len(called)} called · "
                f"{mode_label(round_.get('mode') or MODE_TRADITIONAL, round_.get('pattern_id'))} · "
                f"{round_.get('name', 'Wizard Bingo')}"
            )
        )
        ch = self.bot.get_channel(BINGO_CHANNEL_ID)
        if ch and hasattr(ch, "send"):
            try:
                await ch.send(embed=embed)
            except discord.DiscordException:
                log.exception("Bingo call announce failed")

    # ================================================================ player

    @bingo.command(name="card", description="See your Wizard Bingo card (joins if a round is open).")
    async def bingo_card(self, interaction: discord.Interaction):
        if not await self._require_bingo_channel(interaction):
            return
        round_ = self.active()
        if round_ is None:
            await interaction.response.send_message(
                "No Wizard Bingo round is open right now.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            rec = self.ensure_card(interaction.user.id)
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if round_.get("phase") == PHASE_LOBBY:
            await self._refresh_lobby_message(round_)
        file = self._png_for(interaction.user, rec)
        called_n = len(round_.get("called") or [])
        mode = mode_label(round_.get("mode") or MODE_TRADITIONAL, round_.get("pattern_id"))
        embed = discord.Embed(
            title="🃏 Your Wizard Bingo card",
            description=(
                f"**Mode:** {mode}\n"
                f"**{called_n}** square(s) called so far. "
                "Use `/bingo mark` when one of yours is called. "
                "FREE in the center already counts."
            ),
            color=GOLD,
        )
        embed.set_image(url="attachment://bingo_card.png")
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    @bingo.command(name="mark", description="Mark called squares on your bingo card.")
    async def bingo_mark(self, interaction: discord.Interaction):
        if not await self._require_bingo_channel(interaction):
            return
        round_ = self.active()
        if round_ is None:
            await interaction.response.send_message(
                "No Wizard Bingo round is open right now.", ephemeral=True
            )
            return
        if round_.get("phase") != PHASE_CALLING:
            await interaction.response.send_message(
                "Calling hasn’t started yet — wait for the host to tap **Start**.",
                ephemeral=True,
            )
            return
        rec = self.card_of(interaction.user.id)
        if not rec:
            await interaction.response.send_message(
                "Join first — tap **Join** on the lobby, or `/bingo card`.",
                ephemeral=True,
            )
            return
        called = set(round_.get("called") or [])
        options = []
        for r in range(ROWS):
            for c in range(COLS):
                label = rec["grid"][r][c]
                if label.upper() == "FREE":
                    continue
                if label not in called:
                    continue
                if rec["marked"][r][c]:
                    continue
                options.append(
                    discord.SelectOption(
                        label=label[:100],
                        value=f"{r},{c}",
                        description="Called — tap to mark",
                    )
                )
        if not options:
            await interaction.response.send_message(
                "Nothing new to mark. Wait for the next auto-call, "
                "or everything called on your card is already marked.",
                ephemeral=True,
            )
            return
        view = MarkView(self, interaction.user.id, options[:25])
        await interaction.response.send_message(
            "Pick the called squares that are on your card:",
            view=view,
            ephemeral=True,
        )

    @bingo.command(name="called", description="List squares called this Wizard Bingo round.")
    async def bingo_called(self, interaction: discord.Interaction):
        if not await self._require_bingo_channel(interaction):
            return
        round_ = self.active()
        if round_ is None:
            await interaction.response.send_message(
                "No Wizard Bingo round is open right now.", ephemeral=True
            )
            return
        called = round_.get("called") or []
        if not called:
            await interaction.response.send_message(
                "Nothing called yet — wait for **Start** / the auto-caller.",
                ephemeral=True,
            )
            return
        lines = [f"**{i}.** {s}" for i, s in enumerate(reversed(called), 1)]
        text = "\n".join(lines[:40])
        if len(called) > 40:
            text += f"\n…and {len(called) - 40} earlier."
        embed = discord.Embed(
            title=f"📣 Called — {round_.get('name', 'Wizard Bingo')}",
            description=text,
            color=GOLD,
        )
        await interaction.response.send_message(embed=embed)

    @bingo.command(name="status", description="Is a Wizard Bingo round open? How are you doing?")
    async def bingo_status(self, interaction: discord.Interaction):
        if not await self._require_bingo_channel(interaction):
            return
        round_ = self.active()
        if round_ is None:
            await interaction.response.send_message(
                "No Wizard Bingo round is open.", ephemeral=True
            )
            return
        rec = self.card_of(interaction.user.id)
        called_n = len(round_.get("called") or [])
        winners = round_.get("winners") or []
        phase = round_.get("phase") or PHASE_LOBBY
        mode = mode_label(round_.get("mode") or MODE_TRADITIONAL, round_.get("pattern_id"))
        parts = [
            f"**{round_.get('name', 'Wizard Bingo')}** — _{phase}_",
            f"**Mode:** {mode}",
            f"**{called_n}** square(s) called · **{len(winners)}/{MAX_WINNERS}** winner slots filled.",
            f"**{len(round_.get('players') or [])}** joined.",
        ]
        if rec:
            marks = sum(
                1
                for r in range(ROWS)
                for c in range(COLS)
                if rec["marked"][r][c] or rec["grid"][r][c].upper() == "FREE"
            )
            parts.append(f"Your card: **{marks}/25** marked.")
            if rec.get("bingo_at"):
                parts.append("You've already hit **BINGO** this round.")
        else:
            parts.append("You don't have a card yet — tap **Join** or `/bingo card`.")
        await interaction.response.send_message("\n".join(parts), ephemeral=True)

    # ================================================================ lobby actions

    async def join_from_button(self, interaction: discord.Interaction) -> None:
        if not self._in_bingo_channel(interaction):
            await interaction.response.send_message(_channel_hint(), ephemeral=True)
            return
        round_ = self.active()
        if not round_:
            await interaction.response.send_message(
                "No round open right now.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            rec = self.ensure_card(interaction.user.id)
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if round_.get("phase") == PHASE_LOBBY:
            await self._refresh_lobby_message(round_)
        file = self._png_for(interaction.user, rec)
        mode = mode_label(round_.get("mode") or MODE_TRADITIONAL, round_.get("pattern_id"))
        phase_note = (
            "Wait for the host to tap **Start**."
            if round_.get("phase") == PHASE_LOBBY
            else "Calling is live — `/bingo mark` when you hear yours."
        )
        embed = discord.Embed(
            title="🃏 You're in — here's your card",
            description=f"**Mode:** {mode}\n{phase_note}",
            color=GOLD,
        )
        embed.set_image(url="attachment://bingo_card.png")
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    async def start_from_button(self, interaction: discord.Interaction) -> None:
        if not self._in_bingo_channel(interaction):
            await interaction.response.send_message(_channel_hint(), ephemeral=True)
            return
        round_ = self.active()
        if not round_:
            await interaction.response.send_message(
                "No round open right now.", ephemeral=True
            )
            return
        if not self._is_host(interaction.user, round_):
            await interaction.response.send_message(
                "Only the host (or staff) can **Start** calling.",
                ephemeral=True,
            )
            return
        if round_.get("phase") == PHASE_CALLING:
            await interaction.response.send_message(
                "Calling already started.", ephemeral=True
            )
            return
        players = round_.get("players") or []
        if not players:
            await interaction.response.send_message(
                "Nobody has joined yet — need at least one card.",
                ephemeral=True,
            )
            return

        round_["phase"] = PHASE_CALLING
        round_["calling_started_at"] = time.time()
        self.save()

        # Disable lobby Start; keep Join for latecomers
        view = LobbyView(
            self, round_["round_id"], round_.get("started_by"), calling=True
        )
        self._lobby_views[round_["round_id"]] = view
        self.bot.add_view(view)

        mode = mode_label(round_.get("mode") or MODE_TRADITIONAL, round_.get("pattern_id"))
        embed = discord.Embed(
            title=f"🃏 {round_.get('name') or 'Wizard Bingo'} — calling!",
            description=(
                f"**Mode:** {mode}\n"
                f"Squares will auto-call every **{CALL_INTERVAL_SEC} seconds**.\n"
                "Use `/bingo mark` when one lands on your card.\n"
                f"**{len(players)}** player(s) joined · late **Join** still works."
            ),
            color=GOLD,
        )
        await interaction.response.send_message(
            f"Started — auto-calling every {CALL_INTERVAL_SEC}s.",
            ephemeral=True,
        )
        mid = round_.get("lobby_message_id")
        ch = self.bot.get_channel(BINGO_CHANNEL_ID)
        if mid and ch and hasattr(ch, "fetch_message"):
            try:
                msg = await ch.fetch_message(mid)
                await msg.edit(embed=embed, view=view)
            except discord.DiscordException:
                if ch and hasattr(ch, "send"):
                    await ch.send(embed=embed, view=view)
        elif ch and hasattr(ch, "send"):
            await ch.send(embed=embed, view=view)

        self._ensure_call_loop(round_["round_id"])

    # ================================================================ staff

    async def staff_start(
        self,
        interaction: discord.Interaction,
        name: str | None = None,
        mode: str = MODE_TRADITIONAL,
        channel: discord.TextChannel | None = None,
    ):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if not await self._require_bingo_channel(interaction):
            return
        if channel is not None and channel.id != BINGO_CHANNEL_ID:
            await interaction.response.send_message(
                f"Wizard Bingo stays in <#{BINGO_CHANNEL_ID}> — don't pick another channel.",
                ephemeral=True,
            )
            return
        if len(self.pool) < 24:
            await interaction.response.send_message(
                "Bingo square pool is too small — check `data/bingo_squares.json`.",
                ephemeral=True,
            )
            return
        if self.active():
            await interaction.response.send_message(
                "A round is already open. `/staffgame bingo end` it first.",
                ephemeral=True,
            )
            return

        mode = (mode or MODE_TRADITIONAL).strip().lower()
        valid = {m for m, _ in MODE_CHOICES}
        if mode not in valid:
            await interaction.response.send_message(
                "Unknown mode. Pick Traditional, 4 Corners, Diagonals, or Random pattern.",
                ephemeral=True,
            )
            return

        pattern_id = None
        if mode == MODE_RANDOM_PATTERN:
            pattern_id = pick_random_pattern()

        round_id = uuid.uuid4().hex[:10]
        label = (name or "").strip() or "Wizard Bingo"
        self._stop_call_loop()
        self.state["active"] = {
            "round_id": round_id,
            "name": label,
            "mode": mode,
            "pattern_id": pattern_id,
            "phase": PHASE_LOBBY,
            "channel_id": BINGO_CHANNEL_ID,
            "started_at": time.time(),
            "started_by": interaction.user.id,
            "players": [],
            "called": [],
            "winners": [],
            "lobby_message_id": None,
        }
        self.state["cards"] = {}
        self.save()

        view = LobbyView(self, round_id, interaction.user.id)
        self._lobby_views[round_id] = view
        self.bot.add_view(view)

        target = self.bot.get_channel(BINGO_CHANNEL_ID) or interaction.channel
        await interaction.response.send_message(
            f"Lobby opened in <#{BINGO_CHANNEL_ID}> "
            f"({mode_label(mode, pattern_id)}).",
            ephemeral=True,
        )
        try:
            if target and hasattr(target, "send"):
                msg = await target.send(
                    embed=self._lobby_embed(self.state["active"]), view=view
                )
                self.state["active"]["lobby_message_id"] = msg.id
                self.save()
        except discord.DiscordException:
            log.exception("Could not announce bingo lobby")

    async def staff_call(self, interaction: discord.Interaction, square: str):
        """Optional manual call — auto-caller is the main path."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if not await self._require_bingo_channel(interaction):
            return
        round_ = self.active()
        if not round_:
            await interaction.response.send_message(
                "No open round. `/staffgame bingo start` first.", ephemeral=True
            )
            return
        if round_.get("phase") != PHASE_CALLING:
            await interaction.response.send_message(
                "Tap **Start** on the lobby before calling.", ephemeral=True
            )
            return
        label = (square or "").strip()
        match = next((s for s in self.pool if s.lower() == label.lower()), None)
        if match is None:
            match = next(
                (s for s in self.pool if label.lower() in s.lower()),
                None,
            )
        if match is None:
            await interaction.response.send_message(
                f"**{label}** isn't in the square pool. Start typing to search.",
                ephemeral=True,
            )
            return
        called = round_.setdefault("called", [])
        if match in called:
            await interaction.response.send_message(
                f"**{match}** was already called.", ephemeral=True
            )
            return
        called.append(match)
        round_["last_call_at"] = time.time()
        self.save()

        embed = discord.Embed(
            title="📣 Bingo! (manual)",
            description=f"**{match}**\n\nMark it with `/bingo mark` if it's on your card.",
            color=GOLD,
        )
        embed.set_footer(text=f"{len(called)} called · {round_.get('name', 'Wizard Bingo')}")
        await interaction.response.send_message(embed=embed)

    async def staff_end(self, interaction: discord.Interaction):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if not await self._require_bingo_channel(interaction):
            return
        round_ = self.active()
        if not round_:
            await interaction.response.send_message("No open round to end.", ephemeral=True)
            return
        self._stop_call_loop()
        rid = round_.get("round_id")
        if rid:
            self._lobby_views.pop(rid, None)
        winners = round_.get("winners") or []
        summary = {
            "round_id": round_["round_id"],
            "name": round_.get("name"),
            "mode": round_.get("mode"),
            "pattern_id": round_.get("pattern_id"),
            "ended_at": time.time(),
            "called": list(round_.get("called") or []),
            "winners": winners,
            "players": list(round_.get("players") or []),
        }
        hist = self.state.setdefault("history", [])
        hist.append(summary)
        self.state["history"] = hist[-20:]
        self.state["active"] = None
        self.state["cards"] = {}
        self.save()

        if winners:
            lines = "\n".join(
                f"#{w.get('place', i + 1)} — <@{w['id']}>"
                for i, w in enumerate(winners)
            )
        else:
            lines = "No BINGOs claimed."
        mode = mode_label(summary.get("mode") or MODE_TRADITIONAL, summary.get("pattern_id"))
        embed = discord.Embed(
            title=f"🃏 {summary.get('name') or 'Wizard Bingo'} ended",
            description=(
                f"**Mode:** {mode}\n"
                f"**{len(summary['called'])}** squares called · "
                f"**{len(summary.get('players') or [])}** played.\n\n{lines}"
            ),
            color=GOLD,
        )
        await interaction.response.send_message(embed=embed)

    async def staff_winners(self, interaction: discord.Interaction):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if not await self._require_bingo_channel(interaction):
            return
        round_ = self.active()
        if not round_:
            await interaction.response.send_message("No open round.", ephemeral=True)
            return
        winners = round_.get("winners") or []
        if not winners:
            await interaction.response.send_message(
                "No winners yet this round.", ephemeral=True
            )
            return
        lines = "\n".join(
            f"#{w.get('place', i + 1)} — <@{w['id']}>"
            for i, w in enumerate(winners)
        )
        await interaction.response.send_message(
            f"**Winners** ({len(winners)}/{MAX_WINNERS}):\n{lines}",
            ephemeral=True,
        )

    async def call_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        round_ = self.active()
        called = set((round_ or {}).get("called") or [])
        needle = (current or "").strip().lower()
        choices = []
        for s in self.pool:
            if s in called:
                continue
            if needle and needle not in s.lower():
                continue
            choices.append(app_commands.Choice(name=s[:100], value=s))
            if len(choices) >= 25:
                break
        return choices


class LobbyView(discord.ui.View):
    def __init__(
        self,
        cog: Bingo,
        round_id: str,
        host_id: int | None,
        *,
        calling: bool = False,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.round_id = round_id
        self.host_id = host_id
        self.add_item(JoinButton(round_id))
        self.add_item(StartButton(round_id, disabled=calling))


class JoinButton(discord.ui.Button):
    def __init__(self, round_id: str):
        super().__init__(
            label="Join",
            style=discord.ButtonStyle.success,
            custom_id=f"bingo:join:{round_id}",
        )
        self.round_id = round_id

    async def callback(self, interaction: discord.Interaction):
        cog: Bingo = interaction.client.get_cog("Bingo")  # type: ignore
        if not cog:
            await interaction.response.send_message("Bingo isn't loaded.", ephemeral=True)
            return
        round_ = cog.active()
        if not round_ or round_.get("round_id") != self.round_id:
            await interaction.response.send_message(
                "That lobby already ended.", ephemeral=True
            )
            return
        await cog.join_from_button(interaction)


class StartButton(discord.ui.Button):
    def __init__(self, round_id: str, *, disabled: bool = False):
        super().__init__(
            label="Start",
            style=discord.ButtonStyle.danger,
            custom_id=f"bingo:start:{round_id}",
            disabled=disabled,
        )
        self.round_id = round_id

    async def callback(self, interaction: discord.Interaction):
        cog: Bingo = interaction.client.get_cog("Bingo")  # type: ignore
        if not cog:
            await interaction.response.send_message("Bingo isn't loaded.", ephemeral=True)
            return
        round_ = cog.active()
        if not round_ or round_.get("round_id") != self.round_id:
            await interaction.response.send_message(
                "That lobby already ended.", ephemeral=True
            )
            return
        await cog.start_from_button(interaction)


class MarkView(discord.ui.View):
    def __init__(self, cog: Bingo, user_id: int, options: list[discord.SelectOption]):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.add_item(MarkSelect(cog, user_id, options))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id


class MarkSelect(discord.ui.Select):
    def __init__(self, cog: Bingo, user_id: int, options: list[discord.SelectOption]):
        self.cog = cog
        self.user_id = user_id
        super().__init__(
            placeholder="Mark called squares on your card…",
            min_values=1,
            max_values=min(25, len(options)),
            options=options,
            custom_id=f"bingo:mark:{user_id}:{int(time.time()) % 10**7}",
        )

    async def callback(self, interaction: discord.Interaction):
        if not self.cog._in_bingo_channel(interaction):
            await interaction.response.send_message(_channel_hint(), ephemeral=True)
            return
        round_ = self.cog.active()
        if not round_:
            await interaction.response.send_message(
                "That round already ended.", ephemeral=True
            )
            return
        rec = self.cog.card_of(interaction.user.id)
        if not rec:
            await interaction.response.send_message(
                "You don't have a card — tap **Join** or `/bingo card`.",
                ephemeral=True,
            )
            return
        called = set(round_.get("called") or [])
        newly = 0
        for val in self.values:
            try:
                r_s, c_s = val.split(",", 1)
                r, c = int(r_s), int(c_s)
            except ValueError:
                continue
            if r < 0 or r >= ROWS or c < 0 or c >= COLS:
                continue
            label = rec["grid"][r][c]
            if label.upper() == "FREE":
                continue
            if label not in called:
                continue
            if not rec["marked"][r][c]:
                rec["marked"][r][c] = True
                newly += 1
        self.cog.save()

        blurb = self.cog._award_bingo(interaction.user, rec)
        file = self.cog._png_for(interaction.user, rec)
        embed = discord.Embed(
            title="🃏 Card updated",
            description=f"Marked **{newly}** square(s).",
            color=GOLD,
        )
        embed.set_image(url="attachment://bingo_card.png")
        await interaction.response.edit_message(
            content=None, embed=embed, attachments=[file], view=None
        )
        if blurb:
            try:
                await interaction.followup.send(blurb)
            except discord.DiscordException:
                ch_id = round_.get("channel_id")
                ch = interaction.client.get_channel(ch_id) if ch_id else interaction.channel
                if ch is not None:
                    try:
                        await ch.send(blurb)
                    except discord.DiscordException:
                        log.exception("Could not announce bingo win")


async def setup(bot: commands.Bot):
    await bot.add_cog(Bingo(bot))
