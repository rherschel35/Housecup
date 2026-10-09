"""
Living weekly event schedule board.

Staff posts one message with `/staff setup eventboard`. The bot keeps editing
that same message so the channel always shows the current Attack, Duel Night,
and Challenge schedule — including the next upcoming time for each.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.velmora_channels import EVENT_BOARD_CHANNEL_ID

log = logging.getLogger("velmora.event_board")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "event_board.json"

try:
    CHICAGO = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    CHICAGO = dt.timezone.utc

WEEKDAY_LABELS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)
BOARD_COLOR = 0x6C5CE7
REFRESH_SECONDS = 90


def _chicago_now() -> dt.datetime:
    return dt.datetime.now(CHICAGO)


def bump_event_board(bot) -> None:
    """Mark dirty and redraw soon so schedule edits show up without waiting."""
    cog = bot.get_cog("EventBoard") if bot else None
    if cog is None:
        return
    cog.mark_dirty()
    if getattr(bot, "is_ready", lambda: False)() and cog.state.get("channel_id"):
        try:
            bot.loop.create_task(cog.refresh(force=True))
        except Exception:  # pragma: no cover
            log.debug("Event board bump refresh not scheduled", exc_info=True)


class EventBoard(commands.Cog):
    """One editable Discord message that mirrors the weekly event calendar."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self._dirty = True
        self._last_body = ""

    async def cog_load(self):
        self.refresh_loop.start()

    async def cog_unload(self):
        self.refresh_loop.cancel()

    # -------------------------------------------------------------- storage

    def _blank(self) -> dict:
        return {
            "channel_id": None,
            "message_id": None,
        }

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            for key, value in self._blank().items():
                state.setdefault(key, value)
            return state
        except FileNotFoundError:
            return self._blank()
        except (OSError, json.JSONDecodeError):
            log.exception("Event board state unreadable — starting fresh.")
            return self._blank()

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Couldn't save event board state.")

    def mark_dirty(self) -> None:
        """Call after Attack / Duel Night / challenge schedule changes."""
        self._dirty = True

    # -------------------------------------------------------------- content

    def _attack_section(self) -> str:
        cog = self.bot.get_cog("Dementors")
        if not cog:
            return "_Attack schedule unavailable._"
        sched = cog._attack_schedule()
        if not sched.get("enabled"):
            return "**Attack on Velmora** — off this week.\nStaff: `/staffworld dementor eventschedule`"
        default_mins = cog._slot_attack_minutes(None)
        slots = cog._normalize_schedule_slots(
            sched.get("slots") or [], default_minutes=default_mins,
        )
        if not slots:
            return "**Attack on Velmora** — on, but no slots set."
        from cogs.dementors import EVENT_PRESETS, EVENT_WARN_MINUTES, _preset_kill_pool

        lines = [
            f"**Attack on Velmora** — **{len(slots)}**/week · "
            f"America/Chicago · warn {EVENT_WARN_MINUTES} min early"
        ]
        for s in sorted(slots, key=lambda x: (x["weekday"], x["hour"], x["minute"])):
            mins = cog._slot_attack_minutes(s)
            preset = EVENT_PRESETS[mins]
            pool = _preset_kill_pool(preset)
            lines.append(
                f"· **{WEEKDAY_LABELS[s['weekday']]}** "
                f"**{s['hour']:02d}:{s['minute']:02d}** · "
                f"{preset['label']} · top {preset['reward_top']} share {pool}"
            )
        nxt = cog._next_attack_slot()
        if nxt:
            when = nxt[0]
            lines.append(f"Next: <t:{int(when.timestamp())}:F> (<t:{int(when.timestamp())}:R>)")
        return "\n".join(lines)

    def _duel_night_section(self) -> str:
        cog = self.bot.get_cog("Duels")
        if not cog:
            return "_Duel Night schedule unavailable._"
        sched = cog._night_schedule()
        if not sched.get("enabled"):
            return "**House Duel Night** — off this week.\nStaff: `/staffgame duels nightschedule`"
        slots = cog._normalize_night_slots(sched.get("slots") or [])
        if not slots:
            return "**House Duel Night** — on, but no slots set."
        from cogs.duels import DUEL_NIGHT_PRESETS, DUEL_NIGHT_WARN_MINUTES

        minutes = int(sched.get("minutes") or 60)
        if minutes not in DUEL_NIGHT_PRESETS:
            minutes = 60
        label = DUEL_NIGHT_PRESETS[minutes]
        lines = [
            f"**House Duel Night** — **{len(slots)}**/week · "
            f"**{label}** each · America/Chicago · warn {DUEL_NIGHT_WARN_MINUTES} min early"
        ]
        for s in sorted(slots, key=lambda x: (x["weekday"], x["hour"], x["minute"])):
            lines.append(
                f"· **{WEEKDAY_LABELS[s['weekday']]}** "
                f"**{s['hour']:02d}:{s['minute']:02d}**"
            )
        nxt = cog._next_night_slot()
        if nxt:
            when = nxt[0]
            lines.append(f"Next: <t:{int(when.timestamp())}:F> (<t:{int(when.timestamp())}:R>)")
        announce = cog._night_announce_channel_id()
        if announce:
            lines.append(f"Announces in <#{announce}>")
        return "\n".join(lines)

    def _challenge_section(self) -> str:
        cog = self.bot.get_cog("Quests")
        if not cog:
            return "_Challenge schedule unavailable._"
        from cogs.quests import TIERS, WINNERS_WANTED, _due_after

        settings = cog.settings
        hour = int(settings.get("hour", 18))
        weekday = int(settings.get("weekday", 6))
        ch = settings.get("channel_id")
        where = f"<#{ch}>" if ch else "_(challenge channel not set)_"
        lines = [
            f"**House Cup Challenges** — first {WINNERS_WANTED} correct win points · {where}",
            f"· **Daily** — every 24 hours · 1 pt · pings @everyone",
            f"· **Trial** — about every 3 days at **{hour:02d}:00 UTC** · 3 pts",
            f"· **Weekly Rite** — **{WEEKDAY_LABELS[weekday]}s** at **{hour:02d}:00 UTC** · 5 pts",
        ]
        now = time.time()
        for tier in ("daily", "trial", "rite"):
            meta = TIERS[tier]
            active = cog.state.get("active", {}).get(tier)
            if active and not active.get("closed"):
                left = WINNERS_WANTED - len(active.get("winners") or [])
                lines.append(
                    f"Open now: **{meta['label']}** — {left} place"
                    f"{'' if left == 1 else 's'} left"
                )
                continue
            last = cog.state.get("last_posted", {}).get(tier)
            if last:
                nxt = float(last) + _due_after(tier)
                if nxt > now:
                    lines.append(
                        f"Next **{meta['label']}**: <t:{int(nxt)}:R>"
                    )
        return "\n".join(lines)

    def build_embed(self) -> discord.Embed:
        now = _chicago_now()
        embed = discord.Embed(
            title="Velmora — This Week's Events",
            description=(
                "Living schedule — this message updates itself when staff change "
                "Attack / Duel Night / Challenge timing.\n"
                f"Board time: **{now.strftime('%A %H:%M')}** America/Chicago "
                f"(<t:{int(now.timestamp())}:F>)"
            ),
            color=BOARD_COLOR,
            timestamp=dt.datetime.now(dt.timezone.utc),
        )
        embed.add_field(name="⚔️ Attack", value=self._attack_section()[:1024], inline=False)
        embed.add_field(name="🗡️ Duel Night", value=self._duel_night_section()[:1024], inline=False)
        embed.add_field(name="📜 Challenges", value=self._challenge_section()[:1024], inline=False)
        embed.set_footer(text="Always current · staff: /staff setup eventboard")
        return embed

    # -------------------------------------------------------------- publish

    async def _get_channel(self) -> discord.abc.Messageable | None:
        cid = self.state.get("channel_id")
        if not cid:
            return None
        channel = self.bot.get_channel(int(cid))
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(int(cid))
        except discord.HTTPException:
            return None

    async def refresh(self, *, force: bool = False) -> str:
        """Edit or (re)post the board. Returns a short status for staff."""
        channel = await self._get_channel()
        if channel is None:
            return "No event board channel set — run `/staff setup eventboard`."

        embed = self.build_embed()
        # Cheap skip when nothing meaningful changed (field values).
        body = "|".join(f"{f.name}:{f.value}" for f in embed.fields)
        if not force and not self._dirty and body == self._last_body:
            return "Board already up to date."

        mid = self.state.get("message_id")
        message = None
        if mid:
            try:
                message = await channel.fetch_message(int(mid))
            except discord.HTTPException:
                message = None

        try:
            if message is not None:
                await message.edit(embed=embed)
            else:
                message = await channel.send(embed=embed)
                self.state["message_id"] = message.id
                self.save()
                try:
                    await message.pin(reason="Velmora living event schedule")
                except discord.HTTPException:
                    pass
        except discord.HTTPException:
            log.exception("Could not refresh event board in %s", getattr(channel, "id", "?"))
            return "Couldn't update the board — check bot permissions."

        self._last_body = body
        self._dirty = False
        return f"Event board updated in <#{channel.id}>."

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def refresh_loop(self):
        if not self.state.get("channel_id"):
            return
        await self.refresh(force=False)

    @refresh_loop.before_loop
    async def before_refresh_loop(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------- staff

    async def eventboard(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return

        target = channel
        if target is None:
            # Prefer the channel they're in; else the official schedule board.
            if isinstance(interaction.channel, discord.TextChannel):
                target = interaction.channel
            else:
                raw = os.getenv("EVENT_BOARD_CHANNEL_ID", "").strip()
                cid = int(raw) if raw.isdigit() else EVENT_BOARD_CHANNEL_ID
                fetched = self.bot.get_channel(cid)
                if fetched is None:
                    try:
                        fetched = await self.bot.fetch_channel(cid)
                    except discord.HTTPException:
                        fetched = None
                if not isinstance(fetched, discord.TextChannel):
                    await interaction.response.send_message(
                        "Pick a text channel for the living schedule.",
                        ephemeral=True,
                    )
                    return
                target = fetched

        # If moving channels, clear the old message id so we post fresh.
        if self.state.get("channel_id") and int(self.state["channel_id"]) != target.id:
            self.state["message_id"] = None
        self.state["channel_id"] = target.id
        self.save()
        self._dirty = True
        await interaction.response.defer(ephemeral=True)
        status = await self.refresh(force=True)
        await interaction.followup.send(
            f"Living weekly schedule is in {target.mention}. {status}",
            ephemeral=True,
        )

    async def eventboardoff(self, interaction: discord.Interaction):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        self.state["channel_id"] = None
        self.state["message_id"] = None
        self.save()
        self._last_body = ""
        await interaction.response.send_message(
            "Living event schedule board is off. The old message stays in the "
            "channel until someone deletes it.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(EventBoard(bot))
