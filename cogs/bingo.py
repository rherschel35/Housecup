"""
Wizard Bingo — staff-called round with a unique 5×5 card per player.

    /bingo card              - your PNG card for the open round
    /bingo mark              - mark called squares (select menu)
    /bingo called            - what's been called so far
    /bingo status            - round status + your progress

Staff (via /staffgame bingo):
    start / call / end / winners
"""

from __future__ import annotations

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


def _channel_hint() -> str:
    return f"Wizard Bingo only runs in <#{BINGO_CHANNEL_ID}>."


def _blank() -> dict:
    return {"active": None, "cards": {}, "history": []}


def _load_pool() -> list[str]:
    try:
        with open(SQUARES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        pool = [str(s).strip() for s in data if str(s).strip()]
        # Dedup preserve order
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


def _lines() -> list[list[tuple[int, int]]]:
    lines = []
    for r in range(ROWS):
        lines.append([(r, c) for c in range(COLS)])
    for c in range(COLS):
        lines.append([(r, c) for r in range(ROWS)])
    lines.append([(i, i) for i in range(ROWS)])
    lines.append([(i, COLS - 1 - i) for i in range(ROWS)])
    return lines


WIN_LINES = _lines()


def _has_bingo(grid: list[list[str]], marked: list[list[bool]]) -> bool:
    for line in WIN_LINES:
        if all(marked[r][c] or grid[r][c].upper() == "FREE" for r, c in line):
            return True
    return False


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
    bingo = app_commands.Group(name="bingo", description="Wizard Bingo — your card for the open round.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.pool = _load_pool()

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
        """True if we're in the bingo room (already replied if not)."""
        if self._in_bingo_channel(interaction):
            return True
        await interaction.response.send_message(_channel_hint(), ephemeral=True)
        return False

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

    def _award_bingo(self, member: discord.Member, rec: dict) -> Optional[str]:
        """Mark winner + award points once. Returns public blurb or None."""
        round_ = self.active()
        if not round_ or rec.get("bingo_at"):
            return None
        if not _has_bingo(rec["grid"], rec["marked"]):
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
                f"🎉 **BINGO!** {member.mention} completed a line — "
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

    # ================================================================ player

    @bingo.command(name="card", description="See your Wizard Bingo card for the open round.")
    async def bingo_card(self, interaction: discord.Interaction):
        if not await self._require_bingo_channel(interaction):
            return
        if self.active() is None:
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
        file = self._png_for(interaction.user, rec)
        called_n = len((self.active() or {}).get("called") or [])
        embed = discord.Embed(
            title="🃏 Your Wizard Bingo card",
            description=(
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
        try:
            rec = self.ensure_card(interaction.user.id)
        except RuntimeError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
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
                "Nothing new to mark. Wait for `/staffgame bingo call`, "
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
                "Nothing called yet — listen for staff.", ephemeral=True
            )
            return
        # Newest first, chunk if long
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
        parts = [
            f"**{round_.get('name', 'Wizard Bingo')}** is open.",
            f"**{called_n}** square(s) called · **{len(winners)}/{MAX_WINNERS}** winner slots filled.",
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
            parts.append("You don't have a card yet — `/bingo card`.")
        await interaction.response.send_message("\n".join(parts), ephemeral=True)

    # ================================================================ staff

    async def staff_start(
        self,
        interaction: discord.Interaction,
        name: str | None = None,
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

        round_id = uuid.uuid4().hex[:10]
        label = (name or "").strip() or "Wizard Bingo"
        target = self.bot.get_channel(BINGO_CHANNEL_ID) or interaction.channel
        self.state["active"] = {
            "round_id": round_id,
            "name": label,
            "channel_id": BINGO_CHANNEL_ID,
            "started_at": time.time(),
            "started_by": interaction.user.id,
            "called": [],
            "winners": [],
        }
        # Drop stale cards from prior rounds
        self.state["cards"] = {}
        self.save()

        embed = discord.Embed(
            title=f"🃏 {label}",
            description=(
                "A new Wizard Bingo round is open!\n"
                "• `/bingo card` — get your unique 5×5 card\n"
                "• Staff calls squares aloud\n"
                "• `/bingo mark` when one of yours is called\n"
                f"• First **{MAX_WINNERS}** BINGOs earn **{BINGO_POINTS}** house points each\n"
                f"• Everything Bingo stays in <#{BINGO_CHANNEL_ID}>"
            ),
            color=GOLD,
        )
        await interaction.response.send_message(
            f"Round started in <#{BINGO_CHANNEL_ID}>.",
            ephemeral=True,
        )
        try:
            if target and hasattr(target, "send"):
                await target.send(embed=embed)
        except discord.DiscordException:
            log.exception("Could not announce bingo start")

    async def staff_call(self, interaction: discord.Interaction, square: str):
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
        label = (square or "").strip()
        # Resolve case-insensitive against pool
        match = next((s for s in self.pool if s.lower() == label.lower()), None)
        if match is None:
            # Allow calling something already on cards but typed freely
            match = next(
                (
                    s
                    for s in self.pool
                    if label.lower() in s.lower()
                ),
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
        self.save()

        embed = discord.Embed(
            title="📣 Bingo!",
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
        winners = round_.get("winners") or []
        summary = {
            "round_id": round_["round_id"],
            "name": round_.get("name"),
            "ended_at": time.time(),
            "called": list(round_.get("called") or []),
            "winners": winners,
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
        embed = discord.Embed(
            title=f"🃏 {summary.get('name') or 'Wizard Bingo'} ended",
            description=f"**{len(summary['called'])}** squares called.\n\n{lines}",
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
                "You don't have a card — `/bingo card` first.", ephemeral=True
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
