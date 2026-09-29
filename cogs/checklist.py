"""
/checklist - what you can still do today for house points, with reset timers.

Pulls live counters from the other cogs (beans, duels, explore, boards…).
Calendar days use Discord relative timestamps for the next midnight;
rolling 24h caps show when the next slot frees.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.checklist")

try:
    from zoneinfo import ZoneInfo
    CHICAGO = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    CHICAGO = dt.timezone.utc


def _next_midnight(tz: dt.tzinfo, now: Optional[float] = None) -> int:
    now_dt = dt.datetime.fromtimestamp(now if now is not None else time.time(), tz)
    nxt = (now_dt + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(nxt.timestamp())


def _next_utc_midnight(now: Optional[float] = None) -> int:
    return _next_midnight(dt.timezone.utc, now)


def _next_chicago_midnight(now: Optional[float] = None) -> int:
    return _next_midnight(CHICAGO, now)


def _next_rolling_slot(stamps: list[float], window: float, cap: int, now: float) -> Optional[int]:
    """When the next slot frees under a rolling window (at cap), else when
    the oldest stamp clears (if any), else None (never used)."""
    live = sorted(t for t in stamps if now - t < window)
    if not live:
        return None
    if len(live) >= cap:
        return int(live[0] + window)
    return int(live[0] + window)


def _when_points_free(payments: list[tuple[float, int]], window: float,
                      cap: int, now: float) -> Optional[int]:
    """For a rolling points cap: when enough of the oldest payments age out
    that at least 1 point of room opens (if currently at cap)."""
    live = sorted(((t, p) for t, p in payments if now - t < window), key=lambda r: r[0])
    used = sum(p for _, p in live)
    if used < cap:
        return int(live[0][0] + window) if live else None
    # Drop oldest until room exists; that payment's expiry is the answer.
    running = used
    for t, p in live:
        running -= p
        if running < cap:
            return int(t + window)
    return int(live[0][0] + window) if live else None


def _line(available: bool, label: str, detail: str, reset_ts: Optional[int],
          reset_note: str = "resets") -> str:
    mark = "✅" if available else "⬜"
    timer = f"{reset_note} <t:{reset_ts}:R>" if reset_ts else "ready now"
    return f"{mark} **{label}** — {detail} · {timer}"


class Checklist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _build(self, member: discord.Member) -> discord.Embed:
        now = time.time()
        lines: list[str] = []
        utc_reset = _next_utc_midnight(now)
        chi_reset = _next_chicago_midnight(now)

        # ---- Explore (calendar Central) ----
        world_cog = self.bot.get_cog("World")
        if world_cog:
            from cogs.world import DEFAULT_LIMITS
            from cogs.world_engine import POINTS_PER_DAY
            student = world_cog.student(member)
            day = world_cog.world.day(student, now)
            pts_used = int(day.get("points", 0))
            pts_left = max(0, POINTS_PER_DAY - pts_used)
            explore_cap = DEFAULT_LIMITS.get("explore", 10)
            forage_cap = DEFAULT_LIMITS.get("forage", 5)
            explore_left = max(0, explore_cap - int(day.get("explore", 0)))
            forage_left = max(0, forage_cap - int(day.get("forage", 0)))
            lines.append(_line(
                pts_left > 0, "Explore points",
                f"**{pts_left}/{POINTS_PER_DAY}** pts left", chi_reset, "day resets"))
            lines.append(_line(
                explore_left > 0, "Explore visits",
                f"**{explore_left}/{explore_cap}** left", chi_reset, "day resets"))
            lines.append(_line(
                forage_left > 0, "Forage",
                f"**{forage_left}/{forage_cap}** left", chi_reset, "day resets"))

        # ---- Beans (rolling 24h) ----
        beans = self.bot.get_cog("Beans")
        if beans:
            from cogs.beans import BEANS_PER_DAY, WINDOW
            eaten = beans.recent(member.id, now)
            left = max(0, BEANS_PER_DAY - len(eaten))
            reset = _next_rolling_slot(eaten, WINDOW, BEANS_PER_DAY, now)
            lines.append(_line(
                left > 0, "Beans",
                f"**{left}/{BEANS_PER_DAY}** left", reset,
                "next bean" if left == 0 else "oldest clears"))

        # ---- Duels (rolling 24h) ----
        duels = self.bot.get_cog("Duels")
        if duels:
            from cogs.duels import (
                REWARDED_PER_DAY, TRIO_REWARDED_PER_DAY, GRAND_REWARDED_PER_DAY, WINDOW,
            )
            for label, bucket, cap in (
                ("Duels (1v1)", "rewarded", REWARDED_PER_DAY),
                ("Trio duels", "trio_rewarded", TRIO_REWARDED_PER_DAY),
                ("Grand duels", "grand_rewarded", GRAND_REWARDED_PER_DAY),
            ):
                stamps = [t for t in duels.state.get(bucket, {}).get(str(member.id), [])
                          if now - t < WINDOW]
                left = max(0, cap - len(stamps))
                reset = _next_rolling_slot(stamps, WINDOW, cap, now)
                lines.append(_line(
                    left > 0, label,
                    f"**{left}/{cap}** paid wins left", reset,
                    "next slot" if left == 0 else "oldest clears"))

        # ---- Chess / Checkers (UTC calendar) ----
        chess = self.bot.get_cog("Chess")
        if chess:
            from cogs.chess import DAILY_WIN_CAP, today_str, POINTS_PER_WIN
            rec = chess.state.get("players", {}).get(str(member.id), {})
            daily = rec.get("daily") or {}
            used = int(daily.get("count", 0)) if daily.get("date") == today_str() else 0
            left = max(0, DAILY_WIN_CAP - used)
            lines.append(_line(
                left > 0, "Chess",
                f"**{left}/{DAILY_WIN_CAP}** point wins left ({POINTS_PER_WIN} pts each)",
                utc_reset, "day resets"))

        checkers = self.bot.get_cog("Checkers")
        if checkers:
            from cogs.checkers import DAILY_WIN_CAP, today_str, POINTS_PER_WIN
            rec = checkers.state.get("players", {}).get(str(member.id), {})
            daily = rec.get("daily") or {}
            used = int(daily.get("count", 0)) if daily.get("date") == today_str() else 0
            left = max(0, DAILY_WIN_CAP - used)
            lines.append(_line(
                left > 0, "Checkers",
                f"**{left}/{DAILY_WIN_CAP}** point wins left ({POINTS_PER_WIN} pt each)",
                utc_reset, "day resets"))

        # ---- Quidditch house matches (UTC calendar) ----
        quidditch = self.bot.get_cog("Quidditch")
        if quidditch:
            from cogs.quidditch import DAILY_HOUSE_WIN_CAP, HOUSE_POINTS_PER_WIN, today_str
            rec = quidditch.state.get("players", {}).get(str(member.id), {})
            daily = rec.get("daily") or {}
            used = int(daily.get("count", 0)) if daily.get("date") == today_str() else 0
            left = max(0, DAILY_HOUSE_WIN_CAP - used)
            lines.append(_line(
                left > 0, "Quidditch (house match)",
                f"**{left}/{DAILY_HOUSE_WIN_CAP}** point wins left ({HOUSE_POINTS_PER_WIN} pts each)",
                utc_reset, "day resets"))

        # ---- Beasts (rolling points + study) ----
        beasts = self.bot.get_cog("Beasts")
        if beasts:
            from cogs.beasts import DAILY_POINT_CAP, WINDOW, STUDY_WINDOW
            used = beasts.points_today(member.id, now)
            left = max(0, DAILY_POINT_CAP - used)
            payments = [(r[0], r[1]) for r in beasts.state.get("paid", {}).get(str(member.id), [])
                        if now - r[0] < WINDOW]
            reset = _when_points_free(payments, WINDOW, DAILY_POINT_CAP, now)
            lines.append(_line(
                left > 0, "Beast befriends",
                f"**{left}/{DAILY_POINT_CAP}** pts left", reset,
                "cap frees" if left == 0 else "oldest clears"))

            study_rec = beasts.state.get("study_day", {}).get(str(member.id), {})
            last_study = float(study_rec.get("at", 0) or 0)
            study_ready = (now - last_study) >= STUDY_WINDOW or last_study <= 0
            study_reset = None if study_ready else int(last_study + STUDY_WINDOW)
            lines.append(_line(
                study_ready, "Beast study",
                "ready (`/study`)" if study_ready else "done for now",
                study_reset, "ready"))

        # ---- Market sell (UTC calendar) ----
        market = self.bot.get_cog("Marketplace")
        if market:
            from cogs.marketplace import SELL_DAILY_CAP
            used = market._sell_earned_today(member.id)
            left = max(0, SELL_DAILY_CAP - used)
            lines.append(_line(
                left > 0, "Market sell",
                f"**{left}/{SELL_DAILY_CAP}** pts left from selling",
                utc_reset, "day resets"))

        # ---- Familiar care / scout ----
        familiars = self.bot.get_cog("Familiars")
        if familiars:
            from cogs.world_engine import today as fam_today
            rec = familiars.state.get("members", {}).get(str(member.id))
            if rec and rec.get("species"):
                day = rec.get("day") or {}
                if day.get("date") != fam_today(now):
                    day = {}
                care_left = sum(1 for k in ("fed", "pet", "played") if not day.get(k))
                scout_ready = not day.get("scouted")
                lines.append(_line(
                    care_left > 0, "Familiar care",
                    f"**{care_left}/3** left (feed/pet/play)", chi_reset, "day resets"))
                lines.append(_line(
                    scout_ready, "Familiar scout",
                    "ready (small chance of bonus pts)" if scout_ready else "already sent",
                    chi_reset, "day resets"))
            else:
                lines.append("⬜ **Familiar** — none yet · adopt with `/familiar`")

        # ---- Open challenges ----
        quests = self.bot.get_cog("Quests")
        if quests:
            from cogs.quests import TIERS
            open_bits = []
            for tier in ("daily", "trial", "rite"):
                meta = TIERS[tier]
                round_ = quests.state.get("active", {}).get(tier)
                if not round_ or round_.get("closed"):
                    continue
                attempted = member.id in (round_.get("attempted") or [])
                won = any(w.get("id") == member.id for w in (round_.get("winners") or []))
                if won:
                    open_bits.append(f"✅ {meta['label']} (you scored)")
                elif attempted:
                    open_bits.append(f"⬜ {meta['label']} (attempt used)")
                else:
                    open_bits.append(f"✅ {meta['label']} open — try it!")
            if open_bits:
                lines.append("**Challenges open now**\n" + "\n".join(open_bits))
            else:
                lines.append("⬜ **Challenges** — none open · `/challengestatus` for next times")

        embed = discord.Embed(
            title=f"{member.display_name}'s daily checklist",
            description=(
                "What you can still do for house points (and a few daily habits).\n"
                "✅ = something left · ⬜ = done / capped for now\n\n"
                + "\n".join(lines)
            ),
            color=0x6C5CE7,
        )
        embed.set_footer(
            text="Explore/familiar day = Central · Boards/market/Quidditch = UTC midnight · "
                 "Duels/beans/beasts = rolling 24h"
        )
        return embed

    @app_commands.command(
        name="checklist",
        description="What you can still do today for house points, with reset timers.",
    )
    async def checklist(self, interaction: discord.Interaction):
        # Public so the channel can see it — but only your own board.
        # Checking someone else isn't offered; they run /checklist themselves.
        await interaction.response.send_message(embed=self._build(interaction.user))


async def setup(bot: commands.Bot):
    await bot.add_cog(Checklist(bot))
