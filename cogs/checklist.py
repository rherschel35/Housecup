"""
/checklist - what you can still do today for house points, with reset timers.
/reportcard - public letter grade from your average checklist completion.
/gpa - school-wide GPA ranking from those averages.

Pulls live counters from the other cogs (beans, duels, explore, boards…).
Calendar days use Discord relative timestamps for the next midnight;
rolling 24h caps show when the next slot frees.

Every fully completed checklist day counts toward a streak of clears;
every 10 full clears awards +5 house points once.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.checklist")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "checklist_state.json"

FULL_CLEAR_BONUS_EVERY = 10
FULL_CLEAR_BONUS_POINTS = 5
GPA_RANK_LIMIT = 25

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
    running = used
    for t, p in live:
        running -= p
        if running < cap:
            return int(t + window)
    return int(live[0][0] + window) if live else None


def utc_day(now: Optional[float] = None) -> str:
    return dt.datetime.fromtimestamp(
        now if now is not None else time.time(), dt.timezone.utc
    ).strftime("%Y-%m-%d")


def letter_grade(pct: float) -> str:
    if pct >= 90:
        return "A"
    if pct >= 80:
        return "B"
    if pct >= 70:
        return "C"
    if pct >= 60:
        return "D"
    return "F"


def gpa_from_pct(pct: float) -> float:
    """4.0 scale from average completion percent."""
    return round(max(0.0, min(100.0, pct)) / 100.0 * 4.0, 2)


def _grade_block(letter: str) -> str:
    pad = f" {letter} "
    inner = f"│{pad:^5}│"
    return "\n".join(("╭─────╮", inner, "╰─────╯"))


@dataclass
class CheckItem:
    label: str
    done: bool
    detail: str
    reset_ts: Optional[int] = None
    reset_note: str = "resets"
    scored: bool = True
    raw: Optional[str] = None  # if set, rendered as-is (no ✅/⬜ wrapper)


def _line(item: CheckItem) -> str:
    if item.raw is not None:
        return item.raw
    mark = "✅" if item.done else "⬜"
    timer = f"{item.reset_note} <t:{item.reset_ts}:R>" if item.reset_ts else "ready now"
    return f"{mark} **{item.label}** — {item.detail} · {timer}"


def _score(items: list[CheckItem]) -> tuple[int, int, float]:
    scored = [i for i in items if i.scored]
    total = len(scored)
    done = sum(1 for i in scored if i.done)
    pct = (done / total * 100.0) if total else 0.0
    return done, total, pct


class Checklist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"players": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Checklist state unreadable — starting empty.")
            return {"players": {}}
        if not isinstance(data, dict):
            return {"players": {}}
        data.setdefault("players", {})
        return data

    def save(self) -> None:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save checklist state.")

    def player(self, user_id: int) -> dict:
        rec = self.state.setdefault("players", {}).setdefault(str(user_id), {})
        rec.setdefault("full_completions", 0)
        rec.setdefault("bonuses_paid", 0)
        rec.setdefault("pct_sum", 0.0)
        rec.setdefault("day_count", 0)
        rec.setdefault("today", {})
        return rec

    def average_pct(self, user_id: int, now: Optional[float] = None) -> float:
        rec = self.player(user_id)
        day = utc_day(now)
        total = float(rec.get("pct_sum") or 0)
        n = int(rec.get("day_count") or 0)
        today = rec.get("today") or {}
        if today.get("date") == day and int(today.get("total") or 0) > 0:
            total += float(today.get("pct") or 0)
            n += 1
        return (total / n) if n else 0.0

    def gpa(self, user_id: int, now: Optional[float] = None) -> float:
        return gpa_from_pct(self.average_pct(user_id, now))

    def _finalize_today_if_stale(self, rec: dict, day: str) -> None:
        today = rec.get("today") or {}
        prev = today.get("date")
        if not prev or prev == day:
            return
        if int(today.get("total") or 0) > 0:
            rec["pct_sum"] = float(rec.get("pct_sum") or 0) + float(today.get("pct") or 0)
            rec["day_count"] = int(rec.get("day_count") or 0) + 1
        rec["today"] = {}

    def sync_progress(self, member: discord.Member, now: Optional[float] = None) -> dict:
        """Snapshot today's checklist %, count full clears, pay decade bonuses.

        Returns a small status dict for UI (bonus_pts, newly_full, …).
        """
        now = time.time() if now is None else now
        day = utc_day(now)
        items = self._collect(member, now)
        done, total, pct = _score(items)
        full = total > 0 and done >= total

        rec = self.player(member.id)
        self._finalize_today_if_stale(rec, day)
        today = rec.get("today") or {}
        if today.get("date") != day:
            today = {
                "date": day,
                "done": done,
                "total": total,
                "pct": pct,
                "full": full,
                "counted_full": False,
            }
        else:
            today["done"] = done
            today["total"] = total
            today["pct"] = pct
            today["full"] = full
        rec["today"] = today

        newly_full = False
        if full and not today.get("counted_full"):
            today["counted_full"] = True
            rec["full_completions"] = int(rec.get("full_completions") or 0) + 1
            newly_full = True

        bonus_pts = 0
        full_n = int(rec.get("full_completions") or 0)
        paid = int(rec.get("bonuses_paid") or 0)
        expected = full_n // FULL_CLEAR_BONUS_EVERY
        if expected > paid:
            store = self.bot.get_cog("Store")
            house = store.member_house(member) if store else None
            if store and house:
                for _ in range(expected - paid):
                    store.record(
                        house=house,
                        delta=FULL_CLEAR_BONUS_POINTS,
                        actor_id=self.bot.user.id if self.bot.user else 0,
                        target_id=member.id,
                        reason=(
                            f"Completed {FULL_CLEAR_BONUS_EVERY} full daily checklists"
                        ),
                    )
                    bonus_pts += FULL_CLEAR_BONUS_POINTS
                rec["bonuses_paid"] = expected

        self.save()
        return {
            "done": done,
            "total": total,
            "pct": pct,
            "full": full,
            "newly_full": newly_full,
            "bonus_pts": bonus_pts,
            "full_completions": full_n,
            "avg_pct": self.average_pct(member.id, now),
            "gpa": self.gpa(member.id, now),
            "letter": letter_grade(self.average_pct(member.id, now)),
        }

    def _collect(self, member: discord.Member, now: float) -> list[CheckItem]:
        items: list[CheckItem] = []
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
            items.append(CheckItem(
                "Explore points", pts_left == 0,
                f"**{pts_left}/{POINTS_PER_DAY}** pts left", chi_reset, "day resets"))
            items.append(CheckItem(
                "Explore visits", explore_left == 0,
                f"**{explore_left}/{explore_cap}** left", chi_reset, "day resets"))
            items.append(CheckItem(
                "Forage", forage_left == 0,
                f"**{forage_left}/{forage_cap}** left", chi_reset, "day resets"))

        # ---- Beans (rolling 24h) ----
        beans = self.bot.get_cog("Beans")
        if beans:
            from cogs.beans import BEANS_PER_DAY, WINDOW
            eaten = beans.recent(member.id, now)
            left = max(0, BEANS_PER_DAY - len(eaten))
            reset = _next_rolling_slot(eaten, WINDOW, BEANS_PER_DAY, now)
            items.append(CheckItem(
                "Beans", left == 0,
                f"**{left}/{BEANS_PER_DAY}** left", reset,
                "next bean" if left == 0 else "oldest clears"))

        # ---- Duels (rolling 24h 1v1/grand; UTC house trio set) ----
        store = self.bot.get_cog("Store")
        member_house = store.member_house(member) if store else None
        from cogs.store import HOUSES as STORE_HOUSES
        house_label = (
            f"{STORE_HOUSES[member_house]['emoji']} {STORE_HOUSES[member_house]['name']}"
            if member_house and member_house in STORE_HOUSES
            else None
        )

        duels = self.bot.get_cog("Duels")
        if duels:
            from cogs.duels import (
                GRAND_REWARDED_PER_DAY,
                REWARDED_PER_DAY,
                TRIO_HOUSE_COMPLETION_POINTS,
                TRIO_HOUSE_WIN_GOAL,
                WINDOW,
            )
            for label, bucket, cap in (
                ("Duels (1v1)", "rewarded", REWARDED_PER_DAY),
                ("Grand duels", "grand_rewarded", GRAND_REWARDED_PER_DAY),
            ):
                stamps = [t for t in duels.state.get(bucket, {}).get(str(member.id), [])
                          if now - t < WINDOW]
                left = max(0, cap - len(stamps))
                reset = _next_rolling_slot(stamps, WINDOW, cap, now)
                items.append(CheckItem(
                    label, left == 0,
                    f"**{left}/{cap}** paid wins left", reset,
                    "next slot" if left == 0 else "oldest clears"))

            if member_house:
                twin = duels.trio_house_wins_today(member_house)
                tbonus = duels.trio_house_bonus_claimed(member_house)
                items.append(CheckItem(
                    f"Trio ({house_label})", tbonus,
                    f"**{min(twin, TRIO_HOUSE_WIN_GOAL)}/{TRIO_HOUSE_WIN_GOAL}** "
                    f"house wins · "
                    + (
                        f"+{TRIO_HOUSE_COMPLETION_POINTS} pts claimed"
                        if tbonus
                        else f"+{TRIO_HOUSE_COMPLETION_POINTS} pts at {TRIO_HOUSE_WIN_GOAL}"
                    ),
                    utc_reset, "day resets"))
            else:
                items.append(CheckItem(
                    "Trio (house)", False,
                    "join a house to share the daily trio set",
                    scored=False,
                    raw="⬜ **Trio (house)** — join a house to share the daily trio set",
                ))

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
            items.append(CheckItem(
                "Chess", left == 0,
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
            items.append(CheckItem(
                "Checkers", left == 0,
                f"**{left}/{WEEKLY_WIN_CAP}** point wins left ({POINTS_PER_WIN} pt each)",
                week_reset, "week resets"))

        # ---- Quidditch house matches (UTC house set) ----
        quidditch = self.bot.get_cog("Quidditch")
        if quidditch:
            from cogs.quidditch import HOUSE_COMPLETION_POINTS, HOUSE_WIN_GOAL
            if member_house:
                qwins = quidditch.house_wins_today(member_house)
                qbonus = quidditch.house_bonus_claimed(member_house)
                items.append(CheckItem(
                    f"Quidditch ({house_label})", qbonus,
                    f"**{min(qwins, HOUSE_WIN_GOAL)}/{HOUSE_WIN_GOAL}** "
                    f"house-match wins · "
                    + (
                        f"+{HOUSE_COMPLETION_POINTS} pts claimed"
                        if qbonus
                        else f"+{HOUSE_COMPLETION_POINTS} pts at {HOUSE_WIN_GOAL}"
                    ),
                    utc_reset, "day resets"))
            else:
                items.append(CheckItem(
                    "Quidditch (house)", False,
                    "join a house to share the daily house-match set",
                    scored=False,
                    raw="⬜ **Quidditch (house)** — join a house to share the daily house-match set",
                ))

        # ---- Broom races (UTC calendar) ----
        broom_race = self.bot.get_cog("BroomRace")
        if broom_race:
            from cogs.broom_race import (
                CHALLENGE_POINT_CAP,
                CHALLENGE_POINTS,
                SOLO_COMPLETION_POINTS,
                SOLO_DAILY_CAP,
            )
            # Retry +3 if they finished all 5 but an earlier payout missed.
            broom_race.try_award_solo_completion(member)
            solo_left = broom_race.solo_left(member.id)
            solo_done = broom_race.solo_used(member.id)
            solo_bonus = bool(broom_race.daily_rec(member.id).get("solo_bonus"))
            chall_left = broom_race.challenge_pts_left(member.id)
            if solo_bonus:
                solo_tail = f"+{SOLO_COMPLETION_POINTS} pts claimed"
            elif solo_done >= SOLO_DAILY_CAP:
                solo_tail = (
                    f"+{SOLO_COMPLETION_POINTS} pts ready — need a house?"
                )
            else:
                solo_tail = f"+{SOLO_COMPLETION_POINTS} pts when all 5 finish"
            solo_detail = f"**{solo_done}/{SOLO_DAILY_CAP}** finished · {solo_tail}"
            items.append(CheckItem(
                "Broom race (solo)", solo_bonus,
                solo_detail, utc_reset, "day resets"))
            items.append(CheckItem(
                "Broom race (challenge wins)", chall_left == 0,
                f"**{chall_left}/{CHALLENGE_POINT_CAP}** point awards left "
                f"({CHALLENGE_POINTS} pts each · win or +0s tie · always open)",
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
            items.append(CheckItem(
                "Beast befriends", bonus, detail, utc_reset, "day resets"))

            study_rec = beasts.state.get("study_day", {}).get(str(member.id), {})
            last_study = float(study_rec.get("at", 0) or 0)
            study_ready = (now - last_study) >= STUDY_WINDOW or last_study <= 0
            study_reset = None if study_ready else int(last_study + STUDY_WINDOW)
            items.append(CheckItem(
                "Beast study", not study_ready,
                "ready (`/study`)" if study_ready else "done for now",
                study_reset, "ready"))

        # ---- Market sell (UTC calendar) ----
        market = self.bot.get_cog("Marketplace")
        if market:
            from cogs.marketplace import SELL_DAILY_CAP
            used = market._sell_earned_today(member.id)
            left = max(0, SELL_DAILY_CAP - used)
            items.append(CheckItem(
                "Market sell", left == 0,
                f"**{left}/{SELL_DAILY_CAP}** pts left from selling",
                utc_reset, "day resets"))

        # ---- Potions (UTC day brew set) ----
        potions = self.bot.get_cog("Potions")
        if potions:
            from cogs.potions import BREW_COMPLETION_POINTS, BREW_GOAL
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
            items.append(CheckItem(
                "Potion brews", brew_bonus, brew_detail, utc_reset, "day resets"))

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
                items.append(CheckItem(
                    "Familiar care", care_left == 0,
                    f"**{care_left}/3** left (`/familiar` feed·pet·play)",
                    chi_reset, "day resets"))
                items.append(CheckItem(
                    "Familiar scout", not scout_ready,
                    "ready (small chance of bonus pts)" if scout_ready else "already sent",
                    chi_reset, "day resets"))
            else:
                items.append(CheckItem(
                    "Familiar", False, "none yet · `/familiar adopt`",
                    scored=False,
                    raw="⬜ **Familiar** — none yet · `/familiar adopt`"))

        # ---- Army binds floor 40+ (Chicago calendar) ----
        castles = self.bot.get_cog("Castles")
        if castles:
            from cogs.castles import (
                BIND_BONUS_GOAL,
                BIND_BONUS_MIN_FLOOR,
                BIND_BONUS_POINTS,
            )
            bound = castles.bind40_today(member.id)
            claimed = castles.bind40_bonus_claimed(member.id)
            detail = (
                f"**{min(bound, BIND_BONUS_GOAL)}/{BIND_BONUS_GOAL}** "
                f"floor-{BIND_BONUS_MIN_FLOOR}+ bound · "
                + (
                    f"+{BIND_BONUS_POINTS} pts claimed"
                    if claimed
                    else f"+{BIND_BONUS_POINTS} pts at {BIND_BONUS_GOAL}"
                )
            )
            items.append(CheckItem(
                "Army binds (F40+)", claimed, detail, chi_reset, "day resets"))

        # ---- Frog / fire (Chicago calendar) ----
        frogs = self.bot.get_cog("Frogs")
        if frogs:
            from cogs.world_engine import today as chi_today
            rec = frogs.record(member.id)
            opened = rec.get("opened_on") == chi_today(now)
            items.append(CheckItem(
                "Frog", opened,
                "Chocolate Frog opened today" if opened else "ready — `/frog open`",
                chi_reset, "day resets"))

        fire = self.bot.get_cog("HouseFire")
        if fire:
            from cogs.world_engine import today as chi_today
            store = self.bot.get_cog("Store")
            house = store.member_house(member) if store else None
            if house:
                stoked = member.id in fire.stokers(house, chi_today(now))
                items.append(CheckItem(
                    "House fire", stoked,
                    "stoked today" if stoked else "ready — `/stoke`",
                    chi_reset, "day resets"))

        return items

    def _build_checklist_embed(
        self, member: discord.Member, progress: dict
    ) -> discord.Embed:
        now = time.time()
        items = self._collect(member, now)
        lines = [_line(item) for item in items]

        rec = self.player(member.id)
        full_n = int(rec.get("full_completions") or 0)
        next_at = ((full_n // FULL_CLEAR_BONUS_EVERY) + 1) * FULL_CLEAR_BONUS_EVERY
        toward = full_n % FULL_CLEAR_BONUS_EVERY

        header = (
            f"Today **{progress['done']}/{progress['total']}** "
            f"({progress['pct']:.0f}%) · "
            f"Report card **{progress['letter']}** · "
            f"GPA **{progress['gpa']:.2f}**\n"
            f"Full clears **{full_n}** · "
            f"+{FULL_CLEAR_BONUS_POINTS} pts every {FULL_CLEAR_BONUS_EVERY} "
            f"({toward}/{FULL_CLEAR_BONUS_EVERY} toward {next_at})"
        )
        extras = []
        if progress.get("newly_full"):
            extras.append("🎉 **Full checklist clear** counted for today.")
        if progress.get("bonus_pts"):
            extras.append(
                f"🏆 **+{progress['bonus_pts']}** house points — "
                f"{FULL_CLEAR_BONUS_EVERY} full clears!"
            )
        extra = ("\n" + "\n".join(extras) + "\n") if extras else "\n"

        embed = discord.Embed(
            title=f"{member.display_name}'s daily checklist",
            description=(
                f"{header}{extra}\n"
                "What you can still do for house points (and a few daily habits).\n"
                "✅ = done / capped · ⬜ = still open\n"
                f"See `/reportcard` · school ranks `/gpa`\n\n"
                + "\n".join(lines)
            ),
            color=0x6C5CE7,
        )
        embed.set_footer(
            text="Explore/familiar/frog/fire/army-binds day = Central · "
                 "Chess/Checkers = UTC week (Mon) · Market/Quidditch = UTC midnight · "
                 "Duels/beans = rolling 24h · Report-card day = UTC"
        )
        return embed

    def _build_report_card(
        self, member: discord.Member, progress: dict
    ) -> discord.Embed:
        rec = self.player(member.id)
        full_n = int(rec.get("full_completions") or 0)
        day_n = int(rec.get("day_count") or 0)
        today = rec.get("today") or {}
        if today.get("date") == utc_day() and int(today.get("total") or 0) > 0:
            day_n += 1
        next_at = ((full_n // FULL_CLEAR_BONUS_EVERY) + 1) * FULL_CLEAR_BONUS_EVERY
        toward = full_n % FULL_CLEAR_BONUS_EVERY
        letter = progress["letter"]
        block = _grade_block(letter)

        embed = discord.Embed(
            title=f"📜 Report Card — {member.display_name}",
            description=(
                f"```\n{block}\n```\n"
                f"**Grade {letter}** · **GPA {progress['gpa']:.2f}**\n"
                f"Average checklist completion · **{progress['avg_pct']:.1f}%**\n"
                f"Tracked days · **{day_n}**\n"
                f"Today · **{progress['done']}/{progress['total']}** "
                f"({progress['pct']:.0f}%)"
                + (" · ✅ full clear" if progress.get("full") else "")
            ),
            color={
                "A": 0x2ECC71,
                "B": 0x3498DB,
                "C": 0xF1C40F,
                "D": 0xE67E22,
                "F": 0xE74C3C,
            }.get(letter, 0x6C5CE7),
        )
        embed.add_field(
            name="Full clears",
            value=(
                f"**{full_n}** complete days\n"
                f"`{toward}/{FULL_CLEAR_BONUS_EVERY}` toward next "
                f"+{FULL_CLEAR_BONUS_POINTS} at **{next_at}**"
            ),
            inline=False,
        )
        extras = []
        if progress.get("newly_full"):
            extras.append("Full checklist clear counted for today.")
        if progress.get("bonus_pts"):
            extras.append(f"+{progress['bonus_pts']} house points for decade clears!")
        if extras:
            embed.add_field(name="Just now", value="\n".join(extras), inline=False)
        embed.set_footer(
            text="GPA = average % complete ÷ 100 × 4.0 · A≥90 · B≥80 · C≥70 · D≥60 · else F"
        )
        return embed

    def _gpa_rows(self, guild: discord.Guild, now: float) -> list[tuple[int, float, float, str]]:
        rows: list[tuple[int, float, float, str]] = []
        for uid_s, rec in (self.state.get("players") or {}).items():
            try:
                uid = int(uid_s)
            except (TypeError, ValueError):
                continue
            day_n = int(rec.get("day_count") or 0)
            today = rec.get("today") or {}
            if today.get("date") == utc_day(now) and int(today.get("total") or 0) > 0:
                day_n += 1
            if day_n <= 0:
                continue
            avg = self.average_pct(uid, now)
            rows.append((uid, gpa_from_pct(avg), avg, letter_grade(avg)))
        rows.sort(key=lambda r: (-r[1], -r[2], r[0]))
        return rows

    def _build_gpa_embed(self, guild: discord.Guild, viewer_id: int) -> discord.Embed:
        now = time.time()
        rows = self._gpa_rows(guild, now)
        if not rows:
            return discord.Embed(
                title="GPA ranking",
                description=(
                    "No report cards yet — run `/checklist` or `/reportcard` "
                    "to start your average."
                ),
                color=0xF1C40F,
            )
        lines = []
        viewer_line = None
        for i, (uid, gpa, avg, letter) in enumerate(rows[:GPA_RANK_LIMIT]):
            member = guild.get_member(uid) if guild else None
            name = member.display_name if member else f"Student {uid}"
            lead = ("🥇", "🥈", "🥉")[i] if i < 3 else f"`#{i + 1}`"
            line = f"{lead} **{name}** — **{letter}** · GPA `{gpa:.2f}` ({avg:.0f}%)"
            lines.append(line)
            if uid == viewer_id:
                viewer_line = f"Your rank · **#{i + 1}** of {len(rows)}"
        if viewer_line is None:
            for i, (uid, gpa, avg, letter) in enumerate(rows):
                if uid == viewer_id:
                    viewer_line = (
                        f"Your rank · **#{i + 1}** of {len(rows)} — "
                        f"**{letter}** · GPA `{gpa:.2f}` ({avg:.0f}%)"
                    )
                    break
        desc = "\n".join(lines)
        if viewer_line:
            desc += f"\n\n{viewer_line}"
        if len(rows) > GPA_RANK_LIMIT:
            desc += f"\n\n_Showing top {GPA_RANK_LIMIT} of {len(rows)}._"
        return discord.Embed(
            title="GPA ranking",
            description=desc,
            color=0xF1C40F,
        ).set_footer(text="Based on average % of daily checklist items completed (UTC days)")

    @app_commands.command(
        name="checklist",
        description="What you can still do today for house points, with reset timers.",
    )
    async def checklist(self, interaction: discord.Interaction):
        # Public so the channel can see it — but only your own board.
        progress = self.sync_progress(interaction.user)
        await interaction.response.send_message(
            embed=self._build_checklist_embed(interaction.user, progress)
        )

    @app_commands.command(
        name="reportcard",
        description="Public report card — big letter grade from average checklist completion.",
    )
    @app_commands.describe(member="Whose report card to show (default: you)")
    async def reportcard(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        target = member or interaction.user
        progress = self.sync_progress(target)
        await interaction.response.send_message(
            embed=self._build_report_card(target, progress)
        )

    @app_commands.command(
        name="gpa",
        description="School-wide GPA ranking from average checklist completion.",
    )
    async def gpa_rank(self, interaction: discord.Interaction):
        # Refresh the viewer so they appear if this is their first look.
        self.sync_progress(interaction.user)
        await interaction.response.send_message(
            embed=self._build_gpa_embed(interaction.guild, interaction.user.id)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Checklist(bot))
