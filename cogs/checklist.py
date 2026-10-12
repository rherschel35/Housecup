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


def _line(done: bool, label: str, detail: str, reset_ts: Optional[int],
          reset_note: str = "resets") -> str:
    mark = "✅" if done else "⬜"
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
                pts_left == 0, "Explore points",
                f"**{pts_left}/{POINTS_PER_DAY}** pts left", chi_reset, "day resets"))
            lines.append(_line(
                explore_left == 0, "Explore visits",
                f"**{explore_left}/{explore_cap}** left", chi_reset, "day resets"))
            lines.append(_line(
                forage_left == 0, "Forage",
                f"**{forage_left}/{forage_cap}** left", chi_reset, "day resets"))

        # ---- Beans (rolling 24h) ----
        beans = self.bot.get_cog("Beans")
        if beans:
            from cogs.beans import BEANS_PER_DAY, WINDOW
            eaten = beans.recent(member.id, now)
            left = max(0, BEANS_PER_DAY - len(eaten))
            reset = _next_rolling_slot(eaten, WINDOW, BEANS_PER_DAY, now)
            lines.append(_line(
                left == 0, "Beans",
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
                    left == 0, label,
                    f"**{left}/{cap}** paid wins left", reset,
                    "next slot" if left == 0 else "oldest clears"))

        # ---- Chess / Checkers (UTC week, Mon–Sun) ----
        chess = self.bot.get_cog("Chess")
        if chess:
            from cogs.chess import (
                WEEKLY_WIN_CAP, week_str, POINTS_PER_WIN, next_week_reset_ts,
            )
            rec = chess.state.get("players", {}).get(str(member.id), {})
            weekly = rec.get("weekly") or {}
            used = int(weekly.get("count", 0)) if weekly.get("week") == week_str() else 0
            left = max(0, WEEKLY_WIN_CAP - used)
            week_reset = next_week_reset_ts(now)
            lines.append(_line(
                left == 0, "Chess",
                f"**{left}/{WEEKLY_WIN_CAP}** point wins left ({POINTS_PER_WIN} pts each)",
                week_reset, "week resets"))

        checkers = self.bot.get_cog("Checkers")
        if checkers:
            from cogs.checkers import (
                WEEKLY_WIN_CAP, week_str, POINTS_PER_WIN, next_week_reset_ts,
            )
            rec = checkers.state.get("players", {}).get(str(member.id), {})
            weekly = rec.get("weekly") or {}
            used = int(weekly.get("count", 0)) if weekly.get("week") == week_str() else 0
            left = max(0, WEEKLY_WIN_CAP - used)
            week_reset = next_week_reset_ts(now)
            lines.append(_line(
                left == 0, "Checkers",
                f"**{left}/{WEEKLY_WIN_CAP}** point wins left ({POINTS_PER_WIN} pt each)",
                week_reset, "week resets"))

        # ---- Quidditch house matches (UTC calendar) ----
        quidditch = self.bot.get_cog("Quidditch")
        if quidditch:
            from cogs.quidditch import DAILY_HOUSE_WIN_CAP, HOUSE_POINTS_PER_WIN, today_str
            rec = quidditch.state.get("players", {}).get(str(member.id), {})
            daily = rec.get("daily") or {}
            used = int(daily.get("count", 0)) if daily.get("date") == today_str() else 0
            left = max(0, DAILY_HOUSE_WIN_CAP - used)
            lines.append(_line(
                left == 0, "Quidditch (house match)",
                f"**{left}/{DAILY_HOUSE_WIN_CAP}** point wins left ({HOUSE_POINTS_PER_WIN} pts each)",
                utc_reset, "day resets"))

        # ---- Broom races (UTC calendar) ----
        broom_race = self.bot.get_cog("BroomRace")
        if broom_race:
            from cogs.broom_race import (
                CHALLENGE_POINT_CAP,
                CHALLENGE_POINTS,
                SOLO_COMPLETION_POINTS,
                SOLO_DAILY_CAP,
            )
            solo_left = broom_race.solo_left(member.id)
            solo_done = broom_race.solo_used(member.id)
            solo_bonus = bool(broom_race.daily_rec(member.id).get("solo_bonus"))
            chall_left = broom_race.challenge_pts_left(member.id)
            solo_detail = (
                f"**{solo_done}/{SOLO_DAILY_CAP}** done · "
                + (
                    f"+{SOLO_COMPLETION_POINTS} pts claimed"
                    if solo_bonus
                    else f"+{SOLO_COMPLETION_POINTS} pts when all 5 finish"
                )
            )
            lines.append(_line(
                solo_left == 0 and solo_bonus, "Broom race (solo)",
                solo_detail, utc_reset, "day resets"))
            lines.append(_line(
                chall_left == 0, "Broom race (challenge wins)",
                f"**{chall_left}/{CHALLENGE_POINT_CAP}** point wins left "
                f"({CHALLENGE_POINTS} pts each · challenges always open)",
                utc_reset, "day resets"))

        # ---- Beasts (UTC day befriend set + study) ----
        beasts = self.bot.get_cog("Beasts")
        if beasts:
            from cogs.beasts import (
                BEFRIEND_COMPLETION_POINTS,
                BEFRIEND_GOAL,
                STUDY_WINDOW,
            )
            day = beasts.befriend_day_rec(member.id)
            done = int(day.get("count") or 0)
            bonus = bool(day.get("bonus"))
            detail = (
                f"**{min(done, BEFRIEND_GOAL)}/{BEFRIEND_GOAL}** befriends · "
                + (
                    f"+{BEFRIEND_COMPLETION_POINTS} pts claimed"
                    if bonus
                    else f"+{BEFRIEND_COMPLETION_POINTS} pts at {BEFRIEND_GOAL}"
                )
            )
            lines.append(_line(
                bonus, "Beast befriends", detail, utc_reset, "day resets"))

            study_rec = beasts.state.get("study_day", {}).get(str(member.id), {})
            last_study = float(study_rec.get("at", 0) or 0)
            study_ready = (now - last_study) >= STUDY_WINDOW or last_study <= 0
            study_reset = None if study_ready else int(last_study + STUDY_WINDOW)
            lines.append(_line(
                not study_ready, "Beast study",
                "ready (`/study`)" if study_ready else "done for now",
                study_reset, "ready"))

        # ---- Market sell (UTC calendar) ----
        market = self.bot.get_cog("Marketplace")
        if market:
            from cogs.marketplace import SELL_DAILY_CAP
            used = market._sell_earned_today(member.id)
            left = max(0, SELL_DAILY_CAP - used)
            lines.append(_line(
                left == 0, "Market sell",
                f"**{left}/{SELL_DAILY_CAP}** pts left from selling",
                utc_reset, "day resets"))

        # ---- Potions (brew set + brewable / bottled / active) ----
        potions = self.bot.get_cog("Potions")
        if potions:
            from cogs.potions import (
                BREW_COMPLETION_POINTS,
                BREW_GOAL,
                RECIPES,
                TIER_MIN_RANK,
                rank_info,
            )
            prec = potions.record(member.id)
            brew_day = prec.get("brew_day") or {}
            brewed = int(brew_day.get("count") or 0)
            brew_bonus = bool(brew_day.get("bonus"))
            brew_detail = (
                f"**{min(brewed, BREW_GOAL)}/{BREW_GOAL}** brewed · "
                + (
                    f"+{BREW_COMPLETION_POINTS} pts claimed"
                    if brew_bonus
                    else f"+{BREW_COMPLETION_POINTS} pts at {BREW_GOAL}"
                )
            )
            lines.append(_line(
                brew_bonus, "Potion brews", brew_detail, utc_reset, "day resets"))

            p_idx, _, _ = rank_info(prec.get("rep_xp", 0))
            have = potions._satchel_items(member)
            brewable = sum(
                1 for r in RECIPES.values()
                if p_idx >= TIER_MIN_RANK[r["tier"]]
                and all(have.get(i, 0) >= 1 for i in r["ingredients"])
            )
            bottled = sum(int(n) for n in (prec.get("inventory") or {}).values() if int(n) > 0)
            active_n = len(prec.get("active") or [])
            bits = [f"**{brewable}** brewable", f"**{bottled}** ready to drink"]
            if active_n:
                bits.append(f"**{active_n}** active")
            mark = "✅" if brewable == 0 and bottled == 0 else "⬜"
            lines.append(f"{mark} **Potions satchel** — {' · '.join(bits)} · `/brew` / `/drink`")

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
                    care_left == 0, "Familiar care",
                    f"**{care_left}/3** left (`/familiar` feed·pet·play)", chi_reset, "day resets"))
                lines.append(_line(
                    not scout_ready, "Familiar scout",
                    "ready (small chance of bonus pts)" if scout_ready else "already sent",
                    chi_reset, "day resets"))
            else:
                lines.append("⬜ **Familiar** — none yet · `/familiar adopt`")

        # ---- Frog / fire (Chicago calendar) ----
        frogs = self.bot.get_cog("Frogs")
        if frogs:
            from cogs.world_engine import today as chi_today
            rec = frogs.record(member.id)
            opened = rec.get("opened_on") == chi_today(now)
            lines.append(_line(
                opened, "Chocolate Frog",
                "opened today" if opened else "ready — `/frog open`",
                chi_reset, "day resets"))

        fire = self.bot.get_cog("HouseFire")
        if fire:
            from cogs.world_engine import today as chi_today
            store = self.bot.get_cog("Store")
            house = store.member_house(member) if store else None
            if house:
                stoked = member.id in fire.stokers(house, chi_today(now))
                lines.append(_line(
                    stoked, "House fire",
                    "stoked today" if stoked else "ready — `/stoke`",
                    chi_reset, "day resets"))

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
                    open_bits.append(f"✅ {meta['label']} (attempt used)")
                else:
                    open_bits.append(f"⬜ {meta['label']} open — try it!")
            if open_bits:
                lines.append("**Challenges open now**\n" + "\n".join(open_bits))
            else:
                lines.append("⬜ **Challenges** — none open · `/challengestatus` for next times")

        embed = discord.Embed(
            title=f"{member.display_name}'s daily checklist",
            description=(
                "What you can still do for house points (and a few daily habits).\n"
                "✅ = done / capped · ⬜ = still open\n\n"
                + "\n".join(lines)
            ),
            color=0x6C5CE7,
        )
        embed.set_footer(
            text="Explore/familiar day = Central · Chess/Checkers = UTC week (Mon) · "
                 "Market/Quidditch = UTC midnight · Duels/beans/beasts = rolling 24h"
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
