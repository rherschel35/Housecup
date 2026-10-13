"""Duels cog: records, rewards, and slash commands."""

import asyncio
import datetime as dt
import json
import os
import time
from discord import app_commands
from discord.ext import commands, tasks
import discord
from cogs.config import (
    DUEL_NIGHT_HOUSE_BONUS,
    DUEL_NIGHT_MULTIPLIER,
    DUEL_NIGHT_MVP_BONUS,
)
from cogs.velmora_channels import (
    EVENT_ANNOUNCE_CHANNEL_ID,
    channel_mentions,
    duel_home_channels,
)

from cogs.duels.rules import (
    ANNOUNCE_RANKS_FROM,
    BEST_OF_OPTIONS,
    BOUNTY_AT,
    BOUNTY_POINTS,
    CHAMPION_ROLE_NAME,
    DUELS_PATH,
    DUEL_NIGHT_COLOR,
    DUEL_NIGHT_PRESETS,
    DUEL_NIGHT_SCHEDULE_MAX,
    DUEL_NIGHT_WARN_MINUTES,
    FLOURISHES,
    GIFT_POINTS,
    GRAND_MIN_WINS,
    GRAND_REWARDED_PER_DAY,
    GRAND_REWARD_POINTS,
    GRAND_RIVAL_BOARD_BANTER,
    GRAND_RIVAL_TITLE_TIERS,
    GRAND_TITLE_TIERS,
    REP_FLOURISH,
    REP_GIFT,
    REP_GLOW,
    REP_LOSS,
    REP_WIN,
    REWARDED_PER_DAY,
    REWARD_POINTS,
    RIVAL_AFTER,
    RIVAL_BOARD_BANTER,
    RIVAL_TITLE_TIERS,
    SIGNATURE_TITLES,
    SPELLS,
    STREAK_ANNOUNCE,
    STREAK_EVERY,
    TRIO_HOUSE_COMPLETION_POINTS,
    TRIO_HOUSE_WIN_GOAL,
    TRIO_TITLE_TIERS,
    TZ,
    WEEKDAY_LABELS,
    WEEKLY_POINTS,
    WINDOW,
    _chicago_now,
    _night_ping_role_names,
    highest_title,
    log,
    pair_key,
    rank_for,
    rank_index,
    rival_banter_line,
    signature_unlocks_for,
    titles_at_or_below,
    week_key,
)

class Duels(commands.Cog):
    trio = app_commands.Group(name="trio", description="3v3 wizard duels.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.busy: set[int] = set()   # members currently in a duel / trio / grand
        self.active: dict[int, object] = {}  # user_id -> Duel / GrandDuel / etc.

    async def cog_load(self):
        self.weekly.start()
        self.night_schedule_tick.start()

    async def cog_unload(self):
        self.weekly.cancel()
        self.night_schedule_tick.cancel()

    @staticmethod
    def _blank_night_schedule() -> dict:
        return {
            "enabled": False,
            "slots": [],  # [{weekday, hour, minute}, ...]
            "minutes": 60,  # 30 or 60
            "channel_id": None,
            "role_ids": [],
            "last_warn_key": None,
            "last_start_key": None,
        }

    def _night_schedule(self) -> dict:
        sched = self.state.setdefault("duel_night_schedule", self._blank_night_schedule())
        for key, value in self._blank_night_schedule().items():
            sched.setdefault(key, value)
        return sched

    @staticmethod
    def _normalize_night_slots(raw_slots: list) -> list[dict]:
        """Deduplicate by weekday (first wins); keep up to 3. Skips empty slots."""
        out: list[dict] = []
        seen: set[int] = set()
        for raw in raw_slots:
            if not raw:
                continue
            try:
                day = int(raw["weekday"])
                hour = int(raw["hour"])
                minute = int(raw.get("minute", 0))
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= day <= 6 and 0 <= hour <= 23 and 0 <= minute <= 59):
                continue
            if day in seen:
                continue
            seen.add(day)
            out.append({"weekday": day, "hour": hour, "minute": minute})
            if len(out) >= DUEL_NIGHT_SCHEDULE_MAX:
                break
        return out

    def mark_busy(self, match, *members) -> None:
        for m in members:
            self.busy.add(m.id)
            self.active[m.id] = match

    def release_match(self, match) -> None:
        """Mark a match done and free everyone locked to it."""
        if getattr(match, "state", None) not in (None, "done"):
            match.state = "done"
        timer = getattr(match, "_timer", None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        seq_task = getattr(match, "_sequence_task", None)
        if seq_task is not None and not seq_task.done():
            try:
                seq_task.cancel()
            except Exception:
                pass
        for attr in ("a", "b"):
            m = getattr(match, attr, None)
            if m is None:
                continue
            self.busy.discard(m.id)
            if self.active.get(m.id) is match:
                self.active.pop(m.id, None)

    def clear_user(self, user_id: int) -> str:
        """End whatever has this user marked busy. Returns a short status."""
        match = self.active.get(user_id)
        if match is not None:
            partner = None
            for attr in ("a", "b"):
                m = getattr(match, attr, None)
                if m is not None and m.id != user_id:
                    partner = m
            self.release_match(match)
            if partner is not None:
                return f"ended (also freed {partner.display_name})"
            return "ended"
        if user_id in self.busy:
            self.busy.discard(user_id)
            return "cleared a stuck lock"
        return "not_busy"

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(DUELS_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        except (OSError, json.JSONDecodeError):
            log.exception("Duel records unreadable - starting fresh.")
            state = {}
        state.setdefault("records", {})
        state.setdefault("rewarded", {})
        state.setdefault("trio_rewarded", {})  # legacy individual stamps
        state.setdefault("trio_house_day", {})  # house -> {date, wins, bonus}
        state.setdefault("grand_rewarded", {})
        for key in ("rep", "streaks", "bounties", "pairs", "spell_wins", "signature",
                    "rank_seen", "gifted", "rivals_announced", "signature_announced",
                    "grand_pairs", "grand_rivals_announced"):
            state.setdefault(key, {})
        state.setdefault("week", {"key": week_key(), "wins": {}})
        state.setdefault("champion", None)      # {"user_id", "week"}
        state.setdefault("duel_night", None)    # {"by", "at", "tally": {uid: {wins, pts, house}}}
        state.setdefault("duel_night_schedule", self._blank_night_schedule())
        if not state.get("rewards_backfilled"):
            self._backfill(state)
        # Quietly mark already-earned signature titles as announced so a redeploy
        # doesn't flood the channel with every historical unlock at once.
        if not state.get("signature_announced_backfilled"):
            for uid, tally in state.get("spell_wins", {}).items():
                have = set(state["signature_announced"].setdefault(uid, []))
                for _, _, title in signature_unlocks_for(tally):
                    if title not in have:
                        state["signature_announced"][uid].append(title)
                        have.add(title)
            state["signature_announced_backfilled"] = True
        if not state.get("rival_tiers_backfilled"):
            for pk, n in list(state.get("pairs", {}).items()):
                best = 0
                for thresh, _ in RIVAL_TITLE_TIERS:
                    if n >= thresh:
                        best = thresh
                        break
                raw = state["rivals_announced"].get(pk)
                if best:
                    state["rivals_announced"][pk] = best
                elif raw is True:
                    state["rivals_announced"][pk] = RIVAL_AFTER
            state["rival_tiers_backfilled"] = True
        return state

    @staticmethod
    def _backfill(state: dict) -> None:
        """First run of the reward system: everyone starts at the rank and
        Circle reputation their existing record has already earned. Ranks
        are set quietly (no flood of announcements); anyone already past
        the gift threshold receives it on their next duel."""
        for uid, rec in state["records"].items():
            w, l = rec.get("w", 0), rec.get("l", 0)
            state["rep"][uid] = w * REP_WIN + l * REP_LOSS
            state["rank_seen"][uid] = rank_index(w)
        state["rewards_backfilled"] = True
        log.info("Duel rewards backfilled for %d duellists", len(state["records"]))

    def save(self) -> None:
        try:
            DUELS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = DUELS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, DUELS_PATH)
        except OSError:
            log.exception("Could not save duel records.")

    def record_of(self, user_id: int) -> dict:
        rec = self.state["records"].setdefault(str(user_id), {"w": 0, "l": 0})
        rec.setdefault("w", 0)
        rec.setdefault("l", 0)
        rec.setdefault("trio_w", 0)
        rec.setdefault("trio_l", 0)
        rec.setdefault("grand_w", 0)
        rec.setdefault("grand_l", 0)
        return rec

    def rewarded_today(self, user_id: int, now: float = None) -> int:
        return self._rewarded_count("rewarded", user_id, now)

    def trio_rewarded_today(self, user_id: int, now: float = None) -> int:
        """Legacy individual counter (unused for payouts)."""
        return self._rewarded_count("trio_rewarded", user_id, now)

    @staticmethod
    def _utc_day(now: float | None = None) -> str:
        ts = now if now is not None else time.time()
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")

    def trio_house_day_rec(self, house: str, now: float | None = None) -> dict:
        bag = self.state.setdefault("trio_house_day", {})
        day = self._utc_day(now)
        rec = bag.get(house) or {}
        if rec.get("date") != day:
            rec = {"date": day, "wins": 0, "bonus": False}
            bag[house] = rec
        rec.setdefault("wins", 0)
        rec.setdefault("bonus", False)
        return rec

    def trio_house_wins_today(self, house: str, now: float | None = None) -> int:
        return int(self.trio_house_day_rec(house, now).get("wins") or 0)

    def trio_house_bonus_claimed(self, house: str, now: float | None = None) -> bool:
        return bool(self.trio_house_day_rec(house, now).get("bonus"))

    def grant_all_houses_trio_set_today(self, actor_id: int) -> list[dict]:
        """Mark every house's trio set complete for today; award +5 if unpaid.

        UTC midnight naturally starts a fresh day (wins/bonus reset on date mismatch).
        """
        from cogs.store import HOUSE_KEYS, HOUSES

        store = self.bot.get_cog("Store")
        now = time.time()
        results: list[dict] = []
        for house in HOUSE_KEYS:
            day = self.trio_house_day_rec(house, now)
            already = bool(day.get("bonus"))
            day["wins"] = TRIO_HOUSE_WIN_GOAL
            awarded = 0
            if not already:
                if store:
                    self._award(
                        store,
                        house,
                        TRIO_HOUSE_COMPLETION_POINTS,
                        actor_id,
                        f"Staff grant: house trio set complete ({TRIO_HOUSE_WIN_GOAL} wins)",
                    )
                    awarded = TRIO_HOUSE_COMPLETION_POINTS
                day["bonus"] = True
            results.append({
                "house": house,
                "name": HOUSES[house]["name"],
                "awarded": awarded,
                "already": already,
            })
        self.save()
        return results

    def grand_rewarded_today(self, user_id: int, now: float = None) -> int:
        return self._rewarded_count("grand_rewarded", user_id, now)

    def _rewarded_count(self, bucket: str, user_id: int, now: float = None) -> int:
        now = now if now is not None else time.time()
        stamps = [t for t in self.state[bucket].get(str(user_id), []) if now - t < WINDOW]
        self.state[bucket][str(user_id)] = stamps
        return len(stamps)

    def _duel_channel_ids(self) -> set[int] | None:
        """Allowed duel channels, or None if unrestricted.

        When DUEL_CHANNEL_ID is set, study hall, the Potions/Duels room,
        and any EXTRA_DUEL_CHANNEL_IDS are also allowed.
        """
        arena = os.getenv("DUEL_CHANNEL_ID", "")
        if not arena.isdigit():
            return None
        return set(duel_home_channels(int(arena)))

    def _in_duel_channel(self, interaction: discord.Interaction):
        allowed = self._duel_channel_ids()
        if allowed is None:
            return True, ""
        if interaction.channel_id in allowed:
            return True, ""
        return False, channel_mentions(allowed)

    # ------------------------------------------------------- public helpers

    def wins_of(self, user_id: int) -> int:
        return self.state["records"].get(str(user_id), {}).get("w", 0)

    def trio_wins_of(self, user_id: int) -> int:
        return self.state["records"].get(str(user_id), {}).get("trio_w", 0)

    def grand_wins_of(self, user_id: int) -> int:
        return self.state["records"].get(str(user_id), {}).get("grand_w", 0)

    def rep_of(self, user_id: int) -> int:
        return self.state["rep"].get(str(user_id), 0)

    def streak_of(self, user_id: int) -> int:
        return self.state["streaks"].get(str(user_id), 0)

    def has_bounty(self, user_id: int) -> bool:
        return str(user_id) in self.state["bounties"]

    def signature_titles_of(self, user_id: int) -> list[str]:
        """Every unlocked signature title across spells and tiers (stacked)."""
        tally = self.state["spell_wins"].get(str(user_id), {})
        return [title for _, _, title in signature_unlocks_for(tally)]

    def signature_of(self, user_id: int):
        """Best (spell key, title) for display compatibility, else None."""
        tally = self.state["spell_wins"].get(str(user_id), {})
        unlocks = signature_unlocks_for(tally)
        if not unlocks:
            key = self.state["signature"].get(str(user_id))
            return (key, SIGNATURE_TITLES[key]) if key in SIGNATURE_TITLES else None
        best = max(unlocks, key=lambda row: (row[1], tally.get(row[0], 0), row[0]))
        return (best[0], best[2])

    def rivals_of(self, user_id: int) -> list[int]:
        """Everyone they've duelled RIVAL_AFTER+ times, most duelled first."""
        me = str(user_id)
        rows = []
        for key, n in self.state["pairs"].items():
            a, b = key.split("-")
            if n >= RIVAL_AFTER and me in (a, b):
                rows.append((n, int(b if a == me else a)))
        rows.sort(reverse=True)
        return [uid for _, uid in rows]

    def rival_titles_of(self, user_id: int) -> list[str]:
        """All rival-pair titles unlocked with any opponent (deduped, stacked)."""
        me = str(user_id)
        got: list[str] = []
        seen: set[str] = set()
        for key, n in self.state["pairs"].items():
            a, b = key.split("-")
            if me not in (a, b):
                continue
            for title in titles_at_or_below(RIVAL_TITLE_TIERS, n):
                if title not in seen:
                    seen.add(title)
                    got.append(title)
        return got

    def trio_titles_of(self, user_id: int) -> list[str]:
        return titles_at_or_below(TRIO_TITLE_TIERS, self.trio_wins_of(user_id))

    def grand_titles_of(self, user_id: int) -> list[str]:
        return titles_at_or_below(GRAND_TITLE_TIERS, self.grand_wins_of(user_id))

    def grand_rival_titles_of(self, user_id: int) -> list[str]:
        me = str(user_id)
        got: list[str] = []
        seen: set[str] = set()
        for key, n in self.state["grand_pairs"].items():
            a, b = key.split("-")
            if me not in (a, b):
                continue
            for title in titles_at_or_below(GRAND_RIVAL_TITLE_TIERS, n):
                if title not in seen:
                    seen.add(title)
                    got.append(title)
        return got

    @staticmethod
    def _announced_tier(raw) -> int:
        """rivals_announced used to be bool; now stores the highest threshold posted."""
        if raw is True:
            return RIVAL_AFTER
        if raw is False or raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def is_champion(self, user_id: int) -> bool:
        c = self.state.get("champion")
        return bool(c and c.get("user_id") == user_id)

    def duel_night_on(self, now: float = None) -> bool:
        night = self.state.get("duel_night")
        if not night:
            return False
        now = now if now is not None else time.time()
        ends = float(night.get("ends_at") or 0)
        if ends > 0:
            return now < ends
        # Legacy nights (pre-length presets): treat as already expired.
        return False

    def pop_expired_duel_night(self, now: float = None) -> dict | None:
        """If Duel Night timed out, clear it and return the finished night for tallying."""
        night = self.state.get("duel_night")
        if not night:
            return None
        now = now if now is not None else time.time()
        ends = float(night.get("ends_at") or 0)
        if ends <= 0 or now < ends:
            return None
        self.state["duel_night"] = None
        self.save()
        return night

    def _credit_duel_night(
        self,
        winner_id: int,
        house: str,
        pts: int,
        *,
        wins: int = 1,
    ) -> None:
        """Add banked duel wins + points to the live Duel Night scoreboard.

        ``wins`` is how many record/series wins this result is worth (1 for a
        normal duel; 3/4/5/26 for best-of 5/7/9/51). Points should already
        include the night multiplier (and rival bonus, if any).
        """
        night = self.state.get("duel_night")
        if not night or pts <= 0 or not house:
            return
        win_n = max(0, int(wins))
        if win_n <= 0:
            return
        entry = night.setdefault("tally", {}).setdefault(
            str(winner_id), {"wins": 0, "pts": 0, "house": house}
        )
        entry["wins"] = int(entry.get("wins", 0)) + win_n
        entry["pts"] = int(entry.get("pts", 0)) + int(pts)
        entry["house"] = house

    def note_round_win(self, user_id: int, spell: str) -> None:
        """A round won with this spell. Called as each round resolves."""
        tally = self.state["spell_wins"].setdefault(str(user_id), {})
        tally[spell] = tally.get(spell, 0) + 1

    # -------------------------------------------------------------- settle

    def _roll_week(self, now: float) -> None:
        key = week_key(now)
        if self.state["week"].get("key") != key:
            # The loop normally crowns the champion first; if it missed the
            # boundary (bot was down), the old week's tally is kept aside so
            # it can still be crowned.
            if self.state["week"].get("wins"):
                self.state["pending_week"] = self.state["week"]
            self.state["week"] = {"key": key, "wins": {}}

    def _award(self, store, house, delta, target_id, reason):
        store.record(house=house, delta=delta,
                     actor_id=self.bot.user.id if self.bot.user else 0,
                     target_id=target_id, reason=reason)

    def _pay_banked_wins(
        self,
        store,
        member,
        house: str | None,
        *,
        banks: int,
        opponent_name: str,
        same_house: bool,
        night: bool,
        rivals: bool,
        now: float,
        series: bool,
        role: str,
    ) -> tuple[int, str | None]:
        """Pay house points for banked set/series wins. Returns (pts, reason).

        ``role`` is ``\"win\"`` (series/duel champion) or ``\"sets\"`` (sets taken
        in a best-of series even if they lost the series). Off-night payouts
        fill the daily duel checklist (``rewarded`` stamps) up to
        ``REWARDED_PER_DAY``.
        """
        banks = max(0, int(banks))
        if banks <= 0:
            return 0, None
        if not store or not house:
            return 0, "no-house"
        if same_house:
            return 0, "same-house"

        uid = str(member.id)
        if role == "win":
            why = f"Duel win over {opponent_name}"
        else:
            why = f"Duel set wins vs {opponent_name}"
        if series:
            why += f" (best-of series ×{banks})"

        if night:
            per_bank = 2 if rivals else 1
            paid_units = banks * per_bank
            total_pts = paid_units * REWARD_POINTS * DUEL_NIGHT_MULTIPLIER
            self._credit_duel_night(member.id, house, total_pts, wins=banks)
            if rivals:
                why += " (rival)"
            why += " (Duel Night)"
            self._award(store, house, total_pts, member.id, why)
            return total_pts, None

        total_pts = 0
        paid_units = 0
        for _ in range(banks):
            slots = REWARDED_PER_DAY - self.rewarded_today(member.id, now)
            if slots <= 0:
                break
            wins_paid = 1 + (1 if rivals and slots >= 2 else 0)
            total_pts += wins_paid * REWARD_POINTS
            paid_units += wins_paid
            self.state["rewarded"].setdefault(uid, []).extend([now] * wins_paid)
        if total_pts:
            if rivals and paid_units > banks:
                why += " (rival)"
            self._award(store, house, total_pts, member.id, why)
            return total_pts, None
        return 0, "daily-cap"

    def settle(self, winner, loser, now: float = None, *, match_wins: int = 1,
               winner_sets: int | None = None, loser_sets: int | None = None) -> dict:
        """Record the result, award points, and work out every reward it
        triggers. Returns what happened so the announcement can say so.

        ``match_wins`` is how many house-point / streak / weekly units the
        series is worth (1 for a normal duel; 3/4/5/26 for best-of 5/7/9/51).

        ``winner_sets`` / ``loser_sets`` are the set score for W/L record.
        When omitted (normal duel), the winner gets ``match_wins`` wins and
        the loser ``match_wins`` losses. For a series ending 4–1, pass those
        so both players' set wins *and* set losses land on the record.

        In a best-of series, the loser's set wins also fill their daily duel
        checklist / house points (same daily cap as a normal duel win).
        """
        now = now if now is not None else time.time()
        match_wins = max(1, int(match_wins))
        if winner_sets is None:
            winner_sets = match_wins
        if loser_sets is None:
            loser_sets = 0
        winner_sets = max(0, int(winner_sets))
        loser_sets = max(0, int(loser_sets))
        wid, lid = str(winner.id), str(loser.id)
        self._roll_week(now)

        w_rec = self.record_of(winner.id)
        l_rec = self.record_of(loser.id)
        w_rec["w"] += winner_sets
        w_rec["l"] += loser_sets
        l_rec["w"] += loser_sets
        l_rec["l"] += winner_sets
        notes: list[str] = []

        store = self.bot.get_cog("Store")
        w_house = store.member_house(winner) if store else None
        l_house = store.member_house(loser) if store else None
        same_house = bool(w_house and l_house and w_house == l_house)
        night = self.duel_night_on(now)

        # rivalry (one meeting per series / duel, not per set)
        pk = pair_key(winner.id, loser.id)
        self.state["pairs"][pk] = self.state["pairs"].get(pk, 0) + 1
        meetings = self.state["pairs"][pk]
        rivals = meetings >= RIVAL_AFTER
        announced = self._announced_tier(self.state["rivals_announced"].get(pk))
        # lowest-first so announcements climb the ladder in order
        for threshold, title in reversed(RIVAL_TITLE_TIERS):
            if meetings >= threshold > announced:
                self.state["rivals_announced"][pk] = threshold
                announced = threshold
                notes.append(
                    f"⚔️ **{winner.display_name}** and **{loser.display_name}** have met "
                    f"{meetings} times. They are now **{title}**."
                )

        # ------------------------------------------------ house points (per banked win, capped)
        series = match_wins > 1 or winner_sets > 1 or loser_sets > 0
        w_pts, w_reason = self._pay_banked_wins(
            store, winner, w_house,
            banks=match_wins,
            opponent_name=loser.display_name,
            same_house=same_house,
            night=night,
            rivals=rivals,
            now=now,
            series=series and match_wins > 1,
            role="win",
        )
        # Best-of: sets the loser took still count toward their daily duel
        # checklist + house points (capped like a normal win).
        l_pts, l_reason = (0, None)
        if loser_sets > 0 and (match_wins > 1 or winner_sets > 1):
            l_pts, l_reason = self._pay_banked_wins(
                store, loser, l_house,
                banks=loser_sets,
                opponent_name=winner.display_name,
                same_house=same_house,
                night=night,
                rivals=rivals,
                now=now,
                series=True,
                role="sets",
            )
        outcome = {
            "awarded": w_pts, "reason": w_reason, "house": w_house, "notes": notes,
            "night": night, "rival": rivals, "match_wins": match_wins,
            "winner_sets": winner_sets, "loser_sets": loser_sets,
            "loser_awarded": l_pts, "loser_reason": l_reason, "loser_house": l_house,
        }

        # ------------------------------------------------ Triple Tithe castle perk
        castles = self.bot.get_cog("Castles")
        tithe = castles.triple_tithe_bonus(winner.id) if castles else 0
        if tithe and store and w_house and not same_house:
            self._award(store, w_house, tithe, winner.id, "Triple Tithe")
            outcome["awarded"] = int(outcome.get("awarded") or 0) + tithe
            notes.append(f"🏰 Triple Tithe: +{tithe} for **{winner.display_name}**.")

        # ------------------------------------------------ bounty on the loser
        bounty = self.state["bounties"].pop(lid, None)
        if bounty:
            broken = self.state["streaks"].get(lid, 0) or bounty.get("streak", BOUNTY_AT)
            if same_house:
                notes.append(
                    f"💰 **{winner.display_name}** broke **{loser.display_name}**'s "
                    f"{broken}-win streak — housemates, so no bounty points."
                )
            elif store and w_house:
                self._award(store, w_house, BOUNTY_POINTS, winner.id,
                            f"Broke {loser.display_name}'s {bounty.get('streak', BOUNTY_AT)}-win streak")
                from cogs.store import HOUSES
                h = HOUSES[w_house]
                notes.append(
                    f"💰 **{winner.display_name}** broke **{loser.display_name}**'s "
                    f"{broken}-win streak and claimed the bounty! "
                    f"+{BOUNTY_POINTS} to {h['emoji']} {h['name']}."
                )
            else:
                notes.append(
                    f"💰 **{winner.display_name}** broke **{loser.display_name}**'s "
                    f"{broken}-win streak (no house to credit for the bounty)."
                )

        # ------------------------------------------------ streaks
        # A loss always ends the loser's streak. Same-house wins do not grow
        # the winner's streak or put a bounty on their head — only cross-house
        # wins count toward killing-streak bonuses.
        self.state["streaks"][lid] = 0
        if not same_house:
            streak = self.state["streaks"].get(wid, 0) + match_wins
            self.state["streaks"][wid] = streak
            if streak >= BOUNTY_AT and wid not in self.state["bounties"]:
                self.state["bounties"][wid] = {"since": now, "streak": streak}
                notes.append(f"🎯 **{winner.display_name}** has won {streak} in a row. "
                             f"**A bounty is on their head** - beat them for +{BOUNTY_POINTS} points.")
            elif wid in self.state["bounties"]:
                self.state["bounties"][wid]["streak"] = streak
                if streak % STREAK_EVERY == 0:
                    notes.append(f"🔥 **{winner.display_name}** is on a {streak}-win streak. "
                                 "The bounty still stands.")
            elif streak >= STREAK_ANNOUNCE and streak - match_wins < STREAK_ANNOUNCE:
                notes.append(f"🔥 **{winner.display_name}** is on a {streak}-win streak.")

        # ------------------------------------------------ Circle reputation
        self.state["rep"][wid] = self.state["rep"].get(wid, 0) + REP_WIN
        self.state["rep"][lid] = self.state["rep"].get(lid, 0) + REP_LOSS
        for member, uid in ((winner, wid), (loser, lid)):
            if self.state["rep"][uid] >= REP_GIFT and not self.state["gifted"].get(uid):
                self.state["gifted"][uid] = True
                house = store.member_house(member) if store else None
                if store and house:
                    self._award(store, house, GIFT_POINTS, member.id, "A gift from the Dueling Circle")
                    notes.append(f"🎁 The Dueling Circle has been watching **{member.display_name}**. "
                                 f"It sends a gift: +{GIFT_POINTS} to their house.")
                else:
                    notes.append(f"🎁 The Dueling Circle has been watching **{member.display_name}**, "
                                 "and it approves.")
        outcome["flourish"] = None
        if self.state["rep"][wid] >= REP_FLOURISH:
            outcome["flourish"] = FLOURISHES[winner.id % len(FLOURISHES)].format(name=winner.display_name)

        # ------------------------------------------------ ranks
        wins = self.record_of(winner.id)["w"]
        idx = rank_index(wins)
        if idx > self.state["rank_seen"].get(wid, 0):
            self.state["rank_seen"][wid] = idx
            if wins >= ANNOUNCE_RANKS_FROM:
                notes.append(f"⬆️ **{winner.display_name}** has risen to **{rank_for(wins)}** "
                             f"({wins} wins).")

        # ------------------------------------------------ signature spells (stacked tiers)
        for member in (winner, loser):
            uid = str(member.id)
            tally = self.state["spell_wins"].get(uid, {})
            if not tally:
                continue
            unlocks = signature_unlocks_for(tally)
            if not unlocks:
                continue
            # Keep legacy single-signature pointer on the strongest spell.
            best = max(unlocks, key=lambda row: (row[1], tally.get(row[0], 0), row[0]))
            self.state["signature"][uid] = best[0]
            seen = set(self.state["signature_announced"].setdefault(uid, []))
            # Announce newly crossed tiers in ascending threshold order.
            for spell, threshold, title in sorted(unlocks, key=lambda r: (r[1], r[0])):
                if title in seen:
                    continue
                seen.add(title)
                self.state["signature_announced"][uid].append(title)
                notes.append(
                    f"✨ **{member.display_name}** has unlocked a signature title with "
                    f"{SPELLS[spell]['name']}: **{title}** "
                    f"({threshold} round wins with that spell)."
                )

        # ------------------------------------------------ this week's tally
        week = self.state["week"]["wins"]
        entry = week.get(wid, [0, now])
        week[wid] = [entry[0] + match_wins, now]   # [wins, when they reached that count]

        self.save()
        return outcome

    def _duelist_label(self, member) -> str:
        wands = self.bot.get_cog("Wands")
        wand = wands.short_name(member.id) if wands else None
        label = f"**{member.display_name}**"
        sig = self.signature_of(member.id)
        if sig:
            label += f" {sig[1]}"
        if self.has_bounty(member.id):
            label += " 🎯"
        return label + (f" *({wand})*" if wand else "")

    def pair_meetings(self, a_id: int, b_id: int) -> int:
        return int(self.state["pairs"].get(pair_key(a_id, b_id), 0))

    def grand_pair_meetings(self, a_id: int, b_id: int) -> int:
        return int(self.state["grand_pairs"].get(pair_key(a_id, b_id), 0))

    def rivalry_board_line(self, a, b) -> str | None:
        """Silly rivalry title line for a 1v1 pair, or None if untitled yet."""
        n = self.pair_meetings(a.id, b.id)
        title = highest_title(RIVAL_TITLE_TIERS, n)
        if not title:
            return None
        return rival_banter_line(
            a.display_name, b.display_name, title, n, RIVAL_BOARD_BANTER
        )

    def grand_rivalry_board_line(self, a, b) -> str | None:
        """Silly grand-rivalry title line, or None if untitled yet."""
        n = self.grand_pair_meetings(a.id, b.id)
        title = highest_title(GRAND_RIVAL_TITLE_TIERS, n)
        if not title:
            return None
        return rival_banter_line(
            a.display_name, b.display_name, title, n, GRAND_RIVAL_BOARD_BANTER
        )

    # ------------------------------------------------- Duelist of the Week

    @tasks.loop(minutes=5)
    async def weekly(self):
        """Crowns last week's Duelist of the Week once the week turns over
        (Monday, midnight Central)."""
        try:
            self._roll_week(time.time())
            pending = self.state.pop("pending_week", None)
            if pending:
                self.save()
                await self._crown(pending)
            # a forgotten duel night times out and gets tallied like a swarm Attack
            finished_night = self.pop_expired_duel_night()
            if finished_night:
                await self._finalize_duel_night(finished_night, reason="time")
        except Exception:
            log.exception("Weekly duel check failed")

    @weekly.before_loop
    async def _before_weekly(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def pick_champion(wins: dict):
        """Most wins; a tie goes to whoever reached that count first."""
        if not wins:
            return None
        return min(wins.items(), key=lambda kv: (-kv[1][0], kv[1][1]))

    async def _crown(self, week: dict):
        best = self.pick_champion(week.get("wins", {}))
        if not best:
            return
        uid, (count, _) = int(best[0]), best[1]
        arena = os.getenv("DUEL_CHANNEL_ID", "")
        channel = self.bot.get_channel(int(arena)) if arena.isdigit() else None
        guild = channel.guild if channel else (self.bot.guilds[0] if self.bot.guilds else None)
        member = None
        if guild:
            member = guild.get_member(uid)
            if member is None:
                try:
                    member = await guild.fetch_member(uid)
                except discord.DiscordException:
                    member = None

        previous = self.state.get("champion")
        self.state["champion"] = {"user_id": uid, "week": week.get("key")}
        self.save()

        store = self.bot.get_cog("Store")
        house = store.member_house(member) if (store and member) else None
        paid = ""
        if store and house:
            self._award(store, house, WEEKLY_POINTS, uid, f"Duelist of the Week ({week.get('key')})")
            from cogs.store import HOUSES
            h = HOUSES[house]
            paid = f" +{WEEKLY_POINTS} to {h['emoji']} {h['name']}."

        # the role: taken from last week's champion, given to this one
        role_note = ""
        role = discord.utils.get(guild.roles, name=CHAMPION_ROLE_NAME) if guild else None
        if role:
            try:
                for holder in list(role.members):
                    if holder.id != uid:
                        await holder.remove_roles(role, reason="New Duelist of the Week")
                if member and role not in member.roles:
                    await member.add_roles(role, reason="Duelist of the Week")
            except discord.DiscordException:
                log.exception("Could not hand over the %s role", CHAMPION_ROLE_NAME)
                role_note = ""
        elif guild:
            log.warning("No '%s' role found - champion announced without a role", CHAMPION_ROLE_NAME)

        name = member.display_name if member else f"<@{uid}>"
        if channel:
            try:
                await channel.send(embed=discord.Embed(
                    title="🏆 Duelist of the Week",
                    description=(f"**{name}** won the most duels last week - **{count}** of them - "
                                 f"and is **{CHAMPION_ROLE_NAME}** for the week ahead.{paid}{role_note}\n\n"
                                 "The tally starts fresh now. Go take it from them."),
                    color=0xD4A017,
                ))
            except discord.DiscordException:
                log.exception("Could not announce the Duelist of the Week")

    # ------------------------------------------------------------ commands

    @app_commands.command(name="duel", description="Challenge someone to a wizard's duel.")
    @app_commands.describe(
        opponent="Who you're challenging",
        best_of="Series length: 3 = one duel (default). 5/7/9/51 = play sets of first-to-2 until someone banks 3/4/5/26 wins.",
    )
    @app_commands.choices(best_of=[
        app_commands.Choice(name="Best of 3 (1 win)", value=3),
        app_commands.Choice(name="Best of 5 (first to 3 · 3 wins)", value=5),
        app_commands.Choice(name="Best of 7 (first to 4 · 4 wins)", value=7),
        app_commands.Choice(name="Best of 9 (first to 5 · 5 wins)", value=9),
        app_commands.Choice(name="Best of 51 — extreme (first to 26 · 26 wins)", value=51),
    ])
    async def duel(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        best_of: app_commands.Choice[int] = None,
    ):
        me = interaction.user
        ok, arena = self._in_duel_channel(interaction)
        if not ok:
            await interaction.response.send_message(
                f"⚔️ Duels are fought in {arena}.", ephemeral=True
            )
            return
        if opponent.id == me.id:
            await interaction.response.send_message("You can't duel yourself.", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message(
                "The ghosts don't duel. They've seen enough of that.", ephemeral=True
            )
            return
        if me.id in self.busy:
            await interaction.response.send_message("You're already in a duel.", ephemeral=True)
            return
        if opponent.id in self.busy:
            await interaction.response.send_message(
                f"{opponent.display_name} is already in a duel.", ephemeral=True
            )
            return

        length = int(best_of.value) if best_of is not None else 3
        if length not in BEST_OF_OPTIONS:
            length = 3
        duel = Duel(self, interaction.channel, me, opponent, best_of=length)
        self.mark_busy(duel, me, opponent)
        await interaction.response.send_message(
            content=opponent.mention,
            embed=duel.challenge_embed(),
            view=AcceptView(duel),
        )
        duel.message = await interaction.original_response()
        duel.start_accept_timer()

    @app_commands.command(
        name="duelend",
        description="End a stuck duel lock so you (or someone) can fight again.",
    )
    @app_commands.describe(member="Whose lock to clear (staff only; leave blank for yourself)")
    async def duelend(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        if target.id != interaction.user.id:
            store = self.bot.get_cog("Store")
            if not (store and store.is_staff(interaction.user)):
                await interaction.response.send_message(
                    "Only staff can end someone else's duel. Use `/duelend` with no one tagged for yourself.",
                    ephemeral=True,
                )
                return
        status = self.clear_user(target.id)
        if status == "not_busy":
            who = "You aren't" if target.id == interaction.user.id else f"{target.display_name} isn't"
            await interaction.response.send_message(
                f"{who} marked as in a duel right now.", ephemeral=True
            )
            return
        who = "Your" if target.id == interaction.user.id else f"{target.display_name}'s"
        await interaction.response.send_message(
            f"{who} duel lock is cleared ({status}). They can `/duel` or `/grand` again.",
            ephemeral=True,
        )

    @app_commands.command(name="duelrecord", description="Duel wins, rank, streak, trio/grand, and today's duel points.")
    @app_commands.describe(member="Whose record (leave blank for your own)")
    async def duelrecord(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        wins = self.wins_of(member.id)
        left = max(0, REWARDED_PER_DAY - self.rewarded_today(member.id))
        grand_left = max(0, GRAND_REWARDED_PER_DAY - self.grand_rewarded_today(member.id))
        store = self.bot.get_cog("Store")
        house = store.member_house(member) if store else None
        if house:
            trio_wins = self.trio_house_wins_today(house)
            trio_bonus = self.trio_house_bonus_claimed(house)
            trio_bit = (
                f"{min(trio_wins, TRIO_HOUSE_WIN_GOAL)}/{TRIO_HOUSE_WIN_GOAL} house trio wins"
                + (" (+5 claimed)" if trio_bonus else "")
            )
        else:
            trio_bit = "no house for trio set"

        title = member.display_name
        sig = self.signature_of(member.id)
        if sig:
            title += f" {sig[1]}"
        embed = discord.Embed(title=f"{title} — duelling record", color=0x6C5CE7)
        rec = self.record_of(member.id)
        embed.add_field(name="Rank", value=rank_for(wins))
        embed.add_field(name="Wins", value=str(wins))
        streak = self.streak_of(member.id)
        embed.add_field(name="Streak", value=(f"🔥 {streak}" if streak >= 2 else str(streak))
                        + (" 🎯 bounty" if self.has_bounty(member.id) else ""))
        this_week = self.state["week"]["wins"].get(str(member.id), [0])[0] \
            if self.state["week"].get("key") == week_key() else 0
        embed.add_field(name="This week", value=f"{this_week} wins")
        rivals = self.rivals_of(member.id)
        if rivals:
            names = []
            for rid in rivals[:3]:
                m = interaction.guild.get_member(rid) if interaction.guild else None
                names.append(m.display_name if m else f"<@{rid}>")
            embed.add_field(name="Rivals", value=", ".join(names))
        rival_titles = self.rival_titles_of(member.id)
        if rival_titles:
            embed.add_field(name="Rival titles", value=", ".join(rival_titles), inline=False)
        sig_titles = self.signature_titles_of(member.id)
        if sig_titles:
            embed.add_field(name="Signature titles", value=", ".join(sig_titles), inline=False)
        elif sig:
            embed.add_field(name="Signature spell",
                            value=f"{SPELLS[sig[0]]['emoji']} {SPELLS[sig[0]]['name']}")
        embed.add_field(
            name="Trio",
            value=f"{rec.get('trio_w', 0)} wins"
                  + (f"\n{', '.join(self.trio_titles_of(member.id))}" if self.trio_titles_of(member.id) else ""),
            inline=True,
        )
        embed.add_field(
            name="Grand",
            value=f"{rec.get('grand_w', 0)} wins"
                  + (f"\n{', '.join(self.grand_titles_of(member.id))}" if self.grand_titles_of(member.id) else ""),
            inline=True,
        )
        grand_rivals = self.grand_rival_titles_of(member.id)
        if grand_rivals:
            embed.add_field(name="Grand rival titles", value=", ".join(grand_rivals), inline=False)
        if self.is_champion(member.id):
            embed.add_field(name="Honours", value=f"🏆 {CHAMPION_ROLE_NAME}", inline=False)
        wands = self.bot.get_cog("Wands")
        wand = wands.wand_of(member.id) if wands else None
        if wand:
            glow = " ✨ *(it glows)*" if self.rep_of(member.id) >= REP_GLOW else ""
            embed.add_field(name="Wand", value=f"{wand['wood']}, {wand['core'].lower()}{glow}",
                            inline=False)
        footer = (
            f"{left}/{REWARDED_PER_DAY} 1v1 • {trio_bit} • "
            f"{grand_left}/{GRAND_REWARDED_PER_DAY} grand points left today"
        )
        if self.duel_night_on():
            footer += " • ⚔️ Duel Night: double points, no daily cap, no same-house points"
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="houseduels", description="Each house's overall duel wins.")
    async def houseduels(self, interaction: discord.Interaction):
        from cogs.store import HOUSES
        store = self.bot.get_cog("Store")
        guild = interaction.guild

        totals = {key: 0 for key in HOUSES}
        for uid, rec in self.state.get("records", {}).items():
            member = guild.get_member(int(uid)) if guild else None
            if member is None:
                continue
            house = store.member_house(member) if store else None
            if house not in totals:
                continue
            totals[house] += int(rec.get("w", 0))

        ranked = sorted(totals.items(), key=lambda kv: (-kv[1], HOUSES[kv[0]]["name"]))

        lines = []
        for i, (house_key, wins) in enumerate(ranked, start=1):
            meta = HOUSES[house_key]
            lines.append(f"**{i}. {meta['emoji']} {meta['name']}** — **{wins}** wins")

        top_wins = ranked[0][1] if ranked else 0
        embed = discord.Embed(
            title="⚔️ House Duelling Wins",
            description="\n".join(lines) if lines else "No duels have been fought yet.",
            color=HOUSES[ranked[0][0]]["color"] if ranked and top_wins else 0x6C5CE7,
        )
        embed.set_footer(text="Ranked by total wins • same-house duels count too")
        await interaction.response.send_message(embed=embed)

    def _duel_night_intro_embed(self, minutes: int) -> discord.Embed:
        label = DUEL_NIGHT_PRESETS.get(minutes) or f"{minutes} minutes"
        return discord.Embed(
            title="⚔️ House Duel Night",
            description=(
                f"**{label}** — every **cross-house** duel win counts **double** "
                f"for your house with **no daily win cap**. Same-house duels earn "
                f"**no** house points.\n\n"
                f"**Best-of** series count in full: win a best of 5/7/9/51 and the "
                f"night scoreboard banks **3 / 4 / 5 / 26** wins (and double points "
                f"for each).\n\n"
                f"When the night ends, the house with the most points gets a "
                f"**+{DUEL_NIGHT_HOUSE_BONUS}** bonus, and the night's MVP gets "
                f"**+{DUEL_NIGHT_MVP_BONUS}**.\n\n"
                f"Ends in **{label}**, or when staff call it early."
            ),
            color=DUEL_NIGHT_COLOR,
        )

    def _begin_duel_night(
        self,
        *,
        minutes: int,
        by: int,
        channel_id: int | None,
        guild_id: int | None,
    ) -> tuple[bool, str, dict | None]:
        if self.state.get("duel_night") and self.duel_night_on():
            return False, "A Duel Night is already running. End it first.", None
        if minutes not in DUEL_NIGHT_PRESETS:
            return False, "Pick a length: **30 minutes** or **1 hour**.", None
        now = time.time()
        night = {
            "by": by,
            "at": now,
            "ends_at": now + minutes * 60,
            "minutes": minutes,
            "tally": {},
            "channel_id": channel_id,
            "guild_id": guild_id,
        }
        self.state["duel_night"] = night
        self.save()
        label = DUEL_NIGHT_PRESETS[minutes]
        return True, f"House Duel Night is on for **{label}**.", night

    async def duelnight(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        length: app_commands.Choice[int] | None = None,
    ):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if action.value == "start":
            if length is None or int(length.value) not in DUEL_NIGHT_PRESETS:
                await interaction.response.send_message(
                    "Pick a length: **30 minutes** or **1 hour**.", ephemeral=True
                )
                return
            ok, msg, _night = self._begin_duel_night(
                minutes=int(length.value),
                by=interaction.user.id,
                channel_id=interaction.channel_id,
                guild_id=interaction.guild_id,
            )
            if not ok:
                await interaction.response.send_message(msg, ephemeral=True)
                return
            await interaction.response.send_message(
                embed=self._duel_night_intro_embed(int(length.value)),
            )
        else:
            night = self.state.get("duel_night")
            if not night:
                await interaction.response.send_message(
                    "There's no Duel Night running.", ephemeral=True)
                return
            self.state["duel_night"] = None
            self.save()
            await interaction.response.defer(ephemeral=False)
            embed = await self._finalize_duel_night(night, reason="staff")
            await interaction.followup.send(embed=embed)

    def _resolve_night_ping_roles(self, guild: discord.Guild | None) -> list[discord.Role]:
        if guild is None:
            return []
        sched = self._night_schedule()
        roles: list[discord.Role] = []
        seen: set[int] = set()
        for rid in sched.get("role_ids") or []:
            try:
                role = guild.get_role(int(rid))
            except (TypeError, ValueError):
                continue
            if role and role.id not in seen:
                roles.append(role)
                seen.add(role.id)
        # Always also name-match defaults so Champions + WIZARDS AND WITCHES
        # both ping even when role_ids only listed one of them.
        wanted = {n.lower() for n in _night_ping_role_names()}
        for role in guild.roles:
            if role.name.lower() in wanted and role.id not in seen:
                roles.append(role)
                seen.add(role.id)
        return roles

    def _night_announce_channel_id(self) -> int:
        """Shared board for Duel Night warn / start / results."""
        raw = os.getenv("EVENT_ANNOUNCE_CHANNEL_ID", "").strip()
        if raw.isdigit():
            return int(raw)
        return EVENT_ANNOUNCE_CHANNEL_ID

    async def _post_night_notice(
        self,
        *,
        guild: discord.Guild | None,
        embed: discord.Embed,
        ping: bool,
        channel_id: int | None = None,
    ) -> None:
        cid = channel_id or self._night_announce_channel_id()
        if not cid:
            return
        channel = self.bot.get_channel(cid)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(cid)
            except discord.HTTPException:
                return
        content = None
        if ping:
            roles = self._resolve_night_ping_roles(guild or getattr(channel, "guild", None))
            if roles:
                content = " ".join(r.mention for r in roles)
        allowed = discord.AllowedMentions(roles=True, users=False, everyone=False)
        try:
            await channel.send(
                content=content,
                embed=embed,
                allowed_mentions=allowed if content else discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.exception("Could not post Duel Night notice in %s", cid)

    def _next_night_slot(self, now: dt.datetime | None = None) -> tuple[dt.datetime, str] | None:
        sched = self._night_schedule()
        if not sched.get("enabled"):
            return None
        slots = self._normalize_night_slots(sched.get("slots") or [])
        if not slots:
            return None
        by_day = {int(s["weekday"]): s for s in slots}
        now = now or _chicago_now()
        best: dt.datetime | None = None
        for add in range(0, 8):
            day = (now + dt.timedelta(days=add)).date()
            spec = by_day.get(day.weekday())
            if not spec:
                continue
            candidate = dt.datetime(
                day.year, day.month, day.day,
                int(spec["hour"]), int(spec["minute"]),
                tzinfo=TZ,
            )
            if add == 0 and candidate + dt.timedelta(seconds=120) < now:
                continue
            if best is None or candidate < best:
                best = candidate
        if best is None:
            return None
        return best, best.strftime("%Y-%m-%dT%H:%M")

    @tasks.loop(seconds=30)
    async def night_schedule_tick(self):
        sched = self._night_schedule()
        if not sched.get("enabled"):
            return
        slot = self._next_night_slot()
        if slot is None:
            return
        target, key = slot
        now = _chicago_now()
        warn_at = target - dt.timedelta(minutes=DUEL_NIGHT_WARN_MINUTES)
        minutes = int(sched.get("minutes") or 60)
        if minutes not in DUEL_NIGHT_PRESETS:
            minutes = 60
        label = DUEL_NIGHT_PRESETS[minutes]
        channel_id = self._night_announce_channel_id()
        guild = self.bot.guilds[0] if self.bot.guilds else None

        if warn_at <= now < target and sched.get("last_warn_key") != key:
            embed = discord.Embed(
                title="⚔️ House Duel Night — 5 minutes",
                description=(
                    f"Duel Night opens <t:{int(target.timestamp())}:t> "
                    f"(<t:{int(target.timestamp())}:R>) for **{label}**. "
                    f"Cross-house wins pay double — get ready."
                ),
                color=DUEL_NIGHT_COLOR,
            )
            await self._post_night_notice(
                guild=guild, embed=embed, ping=True, channel_id=channel_id,
            )
            sched["last_warn_key"] = key
            self.save()
            log.info("Posted Duel Night warn for slot %s", key)

        if target <= now < target + dt.timedelta(seconds=120) and sched.get("last_start_key") != key:
            by = self.bot.user.id if self.bot.user else 0
            ok, msg, _night = self._begin_duel_night(
                minutes=minutes,
                by=by,
                channel_id=channel_id,
                guild_id=guild.id if guild else None,
            )
            sched["last_start_key"] = key
            self.save()
            if ok:
                await self._post_night_notice(
                    guild=guild,
                    embed=self._duel_night_intro_embed(minutes),
                    ping=True,
                    channel_id=channel_id,
                )
                log.info("Scheduled Duel Night started for slot %s", key)
            else:
                log.warning("Scheduled Duel Night did not start for %s: %s", key, msg)

    @night_schedule_tick.before_loop
    async def before_night_schedule_tick(self):
        await self.bot.wait_until_ready()

    async def nightschedule(
        self,
        interaction: discord.Interaction,
        day_a: app_commands.Choice[int],
        hour_a: int,
        minute_a: int = 0,
        day_b: app_commands.Choice[int] | None = None,
        hour_b: int | None = None,
        minute_b: int = 0,
        day_c: app_commands.Choice[int] | None = None,
        hour_c: int | None = None,
        minute_c: int = 0,
        length: app_commands.Choice[int] | None = None,
        channel: discord.TextChannel | None = None,
    ):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        raw_slots: list[dict | None] = [
            {"weekday": day_a.value, "hour": hour_a, "minute": minute_a},
        ]
        if day_b is not None:
            if hour_b is None:
                await interaction.response.send_message(
                    "Set **hour_b** when you pick a second day.", ephemeral=True,
                )
                return
            raw_slots.append(
                {"weekday": day_b.value, "hour": hour_b, "minute": minute_b},
            )
        if day_c is not None:
            if hour_c is None:
                await interaction.response.send_message(
                    "Set **hour_c** when you pick a third day.", ephemeral=True,
                )
                return
            raw_slots.append(
                {"weekday": day_c.value, "hour": hour_c, "minute": minute_c},
            )
        slots = self._normalize_night_slots(raw_slots)
        if not slots:
            await interaction.response.send_message(
                "Need at least one valid weekday + time.", ephemeral=True,
            )
            return
        minutes = int(length.value) if length and int(length.value) in DUEL_NIGHT_PRESETS else 60
        sched = self._night_schedule()
        sched["enabled"] = True
        sched["slots"] = slots
        sched["minutes"] = minutes
        # Always board in the shared event announce channel (channel option kept
        # for compatibility but no longer redirects warns/starts elsewhere).
        sched["channel_id"] = self._night_announce_channel_id()
        if channel is not None and channel.id != sched["channel_id"]:
            log.info(
                "Ignoring nightschedule channel=%s; announces use %s",
                channel.id, sched["channel_id"],
            )
        sched["last_warn_key"] = None
        sched["last_start_key"] = None
        self.save()
        from cogs.event_board import bump_event_board
        bump_event_board(self.bot)
        slot_lines = ", ".join(
            f"**{WEEKDAY_LABELS[s['weekday']]}** {s['hour']:02d}:{s['minute']:02d}"
            for s in slots
        )
        next_slot = self._next_night_slot()
        next_note = (
            f" Next: <t:{int(next_slot[0].timestamp())}:F>."
            if next_slot else ""
        )
        cid = self._night_announce_channel_id()
        where = f" Announces in <#{cid}>."
        roles = self._resolve_night_ping_roles(interaction.guild)
        role_note = (
            " Pings: " + ", ".join(r.mention for r in roles) + "."
            if roles else
            " (No Champions / WIZARDS AND WITCHES roles — "
            "`/staff duels nightscheduleroles`.)"
        )
        await interaction.response.send_message(
            f"Duel Night schedule on (America/Chicago): {slot_lines}. "
            f"**{len(slots)}**/week · **{DUEL_NIGHT_PRESETS[minutes]}** each · 5-minute warn."
            f"{where}{next_note}{role_note}",
            ephemeral=True,
        )

    async def nightscheduleoff(self, interaction: discord.Interaction):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        sched = self._night_schedule()
        sched["enabled"] = False
        self.save()
        from cogs.event_board import bump_event_board
        bump_event_board(self.bot)
        await interaction.response.send_message(
            "Weekly Duel Night schedule is off. Manual `/staff duels night` still works.",
            ephemeral=True,
        )

    async def nightscheduleroles(
        self,
        interaction: discord.Interaction,
        champions: discord.Role,
        wizards_and_witches: discord.Role,
    ):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        sched = self._night_schedule()
        ids: list[int] = []
        for role in (champions, wizards_and_witches):
            if role.id not in ids:
                ids.append(role.id)
        sched["role_ids"] = ids
        self.save()
        await interaction.response.send_message(
            "Duel Night pings will mention "
            + ", ".join(r.mention for r in (champions, wizards_and_witches))
            + " on the 5-minute warning and again when the night starts.",
            ephemeral=True,
        )

    async def nightschedulestatus(self, interaction: discord.Interaction):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        sched = self._night_schedule()
        if not sched.get("enabled"):
            await interaction.response.send_message(
                "Weekly Duel Night schedule is **off**.", ephemeral=True,
            )
            return
        slots = self._normalize_night_slots(sched.get("slots") or [])
        slot_block = "\n".join(
            f"· **{WEEKDAY_LABELS[s['weekday']]}** at "
            f"**{s['hour']:02d}:{s['minute']:02d}**"
            for s in slots
        ) if slots else "· (no slots)"
        minutes = int(sched.get("minutes") or 60)
        if minutes not in DUEL_NIGHT_PRESETS:
            minutes = 60
        label = DUEL_NIGHT_PRESETS[minutes]
        next_slot = self._next_night_slot()
        next_note = (
            f"\nNext: <t:{int(next_slot[0].timestamp())}:F> "
            f"(<t:{int(next_slot[0].timestamp())}:R>)."
            if next_slot else "\nNext: not scheduled."
        )
        cid = self._night_announce_channel_id()
        where = f"\nAnnounces in <#{cid}>." if cid else "\nAnnounce channel: not set."
        roles = self._resolve_night_ping_roles(interaction.guild)
        role_note = (
            "\nPings: " + ", ".join(r.mention for r in roles)
            if roles else
            "\nPings: (none set)"
        )
        await interaction.response.send_message(
            f"**On** — America/Chicago · **{len(slots)}**/week · **{label}** each:\n{slot_block}\n"
            f"Warn {DUEL_NIGHT_WARN_MINUTES} min early."
            f"{where}{next_note}{role_note}",
            ephemeral=True,
        )

    async def _finalize_duel_night(self, night: dict, *, reason: str = "staff") -> discord.Embed:
        """End-of-night scoreboard + MVP / winning-house bonuses (swarm Attack style)."""
        from cogs.store import HOUSES

        store = self.bot.get_cog("Store")
        tally = night.get("tally") or {}
        rows = []
        for uid_str, entry in tally.items():
            try:
                uid = int(uid_str)
            except (TypeError, ValueError):
                continue
            house = entry.get("house")
            rows.append({
                "uid": uid,
                "wins": int(entry.get("wins", 0)),
                "pts": int(entry.get("pts", 0)),
                "house": house,
            })
        rows.sort(key=lambda r: (-r["pts"], -r["wins"], r["uid"]))

        house_totals: dict[str, int] = {}
        for r in rows:
            if r["house"]:
                house_totals[r["house"]] = house_totals.get(r["house"], 0) + r["pts"]

        top_pts = rows[0]["pts"] if rows else 0
        mvp_uids = {r["uid"] for r in rows if r["pts"] == top_pts and r["pts"] > 0}
        top_house_pts = max(house_totals.values()) if house_totals else 0
        winning_houses = {h for h, p in house_totals.items() if p == top_house_pts and p > 0}

        # Bonus payouts (credited on top of points already earned during the night)
        if store and rows:
            actor = self.bot.user.id if self.bot.user else 0
            for r in rows:
                if r["uid"] in mvp_uids and r["house"]:
                    store.record(
                        house=r["house"],
                        delta=DUEL_NIGHT_MVP_BONUS,
                        actor_id=actor,
                        target_id=r["uid"],
                        reason="Duel Night MVP bonus",
                    )
                    house_totals[r["house"]] = house_totals.get(r["house"], 0) + DUEL_NIGHT_MVP_BONUS
            for house in winning_houses:
                # Credit the house bonus through that house's top earner tonight.
                top = next((r for r in rows if r["house"] == house), None)
                if not top:
                    continue
                store.record(
                    house=house,
                    delta=DUEL_NIGHT_HOUSE_BONUS,
                    actor_id=actor,
                    target_id=top["uid"],
                    reason="Duel Night winning house bonus",
                )
                house_totals[house] = house_totals.get(house, 0) + DUEL_NIGHT_HOUSE_BONUS

        minutes = int(night.get("minutes") or 0)
        label = DUEL_NIGHT_PRESETS.get(minutes) or (f"{minutes} minutes" if minutes else "time")
        how = "Staff ended the night" if reason == "staff" else f"Time's up ({label})"
        lines = [
            f"{how}. Cross-house wins paid double all night — same-house duels never did. "
            f"Best-of series banked in full (3/4/5/26 for best of 5/7/9/51)."
        ]
        if rows:
            lines.append("")
            lines.append(f"**{sum(r['wins'] for r in rows)}** banked win(s) by **{len(rows)}** duelist(s).")
            lines.append("")
            for r in rows[:10]:
                crown = "👑 " if r["uid"] in mvp_uids else ""
                bonus_note = f" (+{DUEL_NIGHT_MVP_BONUS} MVP)" if r["uid"] in mvp_uids else ""
                house_note = (
                    f" — {HOUSES[r['house']]['emoji']} {HOUSES[r['house']]['name']}"
                    if r["house"] and r["house"] in HOUSES else " — no house"
                )
                lines.append(
                    f"{crown}<@{r['uid']}>: **{r['pts']}** pts, {r['wins']} win(s)"
                    f"{house_note}{bonus_note}"
                )
        else:
            lines.append("")
            lines.append("Nobody banked a cross-house win. The Circle goes quiet.")

        if house_totals:
            lines.append("")
            ranked = sorted(house_totals.items(), key=lambda kv: -kv[1])
            lines.append(" • ".join(
                f"{'🏆 ' if h in winning_houses else ''}"
                f"{HOUSES[h]['emoji']} {HOUSES[h]['name']}: **+{p}**"
                for h, p in ranked if h in HOUSES
            ))
            if winning_houses:
                names = ", ".join(HOUSES[h]["name"] for h in winning_houses if h in HOUSES)
                lines.append(
                    f"\n🏆 **{names}** take the night "
                    f"(+{DUEL_NIGHT_HOUSE_BONUS} winning-house bonus)."
                )

        embed = discord.Embed(
            title="🏳️ House Duel Night has ended",
            description="\n".join(lines),
            color=DUEL_NIGHT_COLOR,
        )

        # Auto-expire posts to the shared event announce board.
        channel_id = night.get("channel_id") or self._night_announce_channel_id()
        if reason != "staff" and channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel is not None:
                try:
                    await channel.send(embed=embed)
                except discord.DiscordException:
                    log.exception("Could not post Duel Night finale.")
        return embed

    # -------------------------------------------------------- trio / grand settle

    def _unanimous_house(self, members) -> str | None:
        store = self.bot.get_cog("Store")
        if not store or not members:
            return None
        houses = {store.member_house(m) for m in members}
        houses.discard(None)
        return next(iter(houses)) if len(houses) == 1 else None

    def settle_trio(self, winners: list, losers: list, now: float = None) -> dict:
        """Record a trio result. House points: 5 wins/day for the house → +5 once."""
        now = now if now is not None else time.time()
        notes: list[str] = []
        for m in winners:
            self.record_of(m.id)["trio_w"] += 1
        for m in losers:
            self.record_of(m.id)["trio_l"] += 1

        store = self.bot.get_cog("Store")
        night = self.duel_night_on(now)
        house_w = self._unanimous_house(winners)
        house_l = self._unanimous_house(losers)
        same_house_teams = bool(house_w and house_l and house_w == house_l)

        awarded = 0
        house_wins = 0
        house_bonus = False
        if store and house_w and not same_house_teams:
            day = self.trio_house_day_rec(house_w, now)
            if not day.get("bonus"):
                day["wins"] = int(day.get("wins") or 0) + 1
            house_wins = int(day.get("wins") or 0)
            house_bonus = bool(day.get("bonus"))
            if house_wins >= TRIO_HOUSE_WIN_GOAL and not house_bonus:
                self._award(
                    store,
                    house_w,
                    TRIO_HOUSE_COMPLETION_POINTS,
                    winners[0].id if winners else None,
                    f"House trio set complete ({TRIO_HOUSE_WIN_GOAL} wins)",
                )
                day["bonus"] = True
                house_bonus = True
                awarded = TRIO_HOUSE_COMPLETION_POINTS

        for member in winners:
            wins = self.record_of(member.id)["trio_w"]
            prev = highest_title(TRIO_TITLE_TIERS, wins - 1)
            now_title = highest_title(TRIO_TITLE_TIERS, wins)
            if now_title and now_title != prev:
                notes.append(f"🔺 **{member.display_name}** is now **{now_title}** ({wins} trio wins).")

        self.save()
        return {
            "awarded": awarded,
            "night": night,
            "notes": notes,
            "same_house": same_house_teams,
            "house": house_w,
            "house_wins": house_wins,
            "house_goal": TRIO_HOUSE_WIN_GOAL,
            "house_bonus": house_bonus,
            "paid": [],
            "capped": [],
        }

    def settle_grand(self, winner, loser, now: float = None) -> dict:
        """Record a Grand Duel. Never touches 1v1/trio/signature/streak/bounty."""
        now = now if now is not None else time.time()
        notes: list[str] = []
        self.record_of(winner.id)["grand_w"] += 1
        self.record_of(loser.id)["grand_l"] += 1

        pk = pair_key(winner.id, loser.id)
        self.state["grand_pairs"][pk] = self.state["grand_pairs"].get(pk, 0) + 1
        meetings = self.state["grand_pairs"][pk]
        announced = self._announced_tier(self.state["grand_rivals_announced"].get(pk))
        for threshold, title in reversed(GRAND_RIVAL_TITLE_TIERS):
            if meetings >= threshold > announced:
                self.state["grand_rivals_announced"][pk] = threshold
                announced = threshold
                notes.append(
                    f"📜 **{winner.display_name}** and **{loser.display_name}** have Grand-Duelled "
                    f"{meetings} times. They are now **{title}**."
                )

        store = self.bot.get_cog("Store")
        w_house = store.member_house(winner) if store else None
        l_house = store.member_house(loser) if store else None
        night = self.duel_night_on(now)
        awarded = 0
        reason = None
        if not store or not w_house:
            reason = "no-house"
        elif w_house == l_house:
            reason = "same-house"
        elif (not night) and self.grand_rewarded_today(winner.id, now) >= GRAND_REWARDED_PER_DAY:
            reason = "daily-cap"
        else:
            pts = GRAND_REWARD_POINTS * (DUEL_NIGHT_MULTIPLIER if night else 1)
            why = f"Grand Duel win over {loser.display_name}"
            if night:
                why += " (Duel Night)"
            self._award(store, w_house, pts, winner.id, why)
            if night:
                self._credit_duel_night(winner.id, w_house, pts)
            else:
                self.state["grand_rewarded"].setdefault(str(winner.id), []).append(now)
            awarded = pts

        wins = self.record_of(winner.id)["grand_w"]
        prev = highest_title(GRAND_TITLE_TIERS, wins - 1)
        now_title = highest_title(GRAND_TITLE_TIERS, wins)
        if now_title and now_title != prev:
            notes.append(f"🏛️ **{winner.display_name}** is now **{now_title}** ({wins} Grand wins).")

        self.save()
        return {
            "awarded": awarded, "reason": reason, "house": w_house,
            "night": night, "notes": notes,
        }

    # ---------------------------------------------------------------- trio

    @trio.command(name="scramble", description="Open a casual 3v3 trio duel — any houses, either side.")
    async def trio_scramble(self, interaction: discord.Interaction):
        await self._start_trio_signup(interaction, is_house=False)

    @trio.command(name="housematch", description="Open a house-vs-house 3v3 trio duel.")
    @app_commands.describe(house1="First house", house2="Second house")
    async def trio_housematch(self, interaction: discord.Interaction, house1: str, house2: str):
        from cogs.store import HOUSES
        h1, h2 = house1.lower(), house2.lower()
        if h1 not in HOUSES or h2 not in HOUSES:
            await interaction.response.send_message(
                "Pick two real houses: " + ", ".join(HOUSES[k]["name"] for k in HOUSES),
                ephemeral=True,
            )
            return
        if h1 == h2:
            await interaction.response.send_message("Pick two different houses.", ephemeral=True)
            return
        await self._start_trio_signup(interaction, is_house=True, house_a=h1, house_b=h2)

    @trio_housematch.autocomplete("house1")
    @trio_housematch.autocomplete("house2")
    async def _trio_house_ac(self, interaction: discord.Interaction, current: str):
        from cogs.store import HOUSES
        cur = (current or "").lower()
        return [
            app_commands.Choice(name=meta["name"], value=key)
            for key, meta in HOUSES.items()
            if cur in key or cur in meta["name"].lower()
        ][:25]

    async def _start_trio_signup(self, interaction: discord.Interaction, is_house: bool,
                                 house_a: str = None, house_b: str = None):
        ok, arena = self._in_duel_channel(interaction)
        if not ok:
            await interaction.response.send_message(
                f"⚔️ Duels are fought in {arena}.", ephemeral=True)
            return
        if interaction.user.id in self.busy:
            await interaction.response.send_message("You're already in a duel.", ephemeral=True)
            return
        signup = TrioSignup(self, is_house, house_a, house_b)
        await interaction.response.send_message(embed=signup.embed(), view=TrioSignupView(signup))
        signup.message = await interaction.original_response()
        signup.timer_task = asyncio.create_task(signup._timeout())

    # ---------------------------------------------------------------- grand

    @app_commands.command(name="grand", description="Challenge someone to a Grand Duel (10-spell sequences).")
    @app_commands.describe(member="Who you're challenging")
    async def grand(self, interaction: discord.Interaction, member: discord.Member):
        me = interaction.user
        ok, arena = self._in_duel_channel(interaction)
        if not ok:
            await interaction.response.send_message(
                f"⚔️ Duels are fought in {arena}.", ephemeral=True)
            return
        if member.id == me.id:
            await interaction.response.send_message("You can't Grand Duel yourself.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message(
                "The ghosts don't Grand Duel.", ephemeral=True)
            return
        if me.id in self.busy:
            await interaction.response.send_message("You're already in a duel.", ephemeral=True)
            return
        if member.id in self.busy:
            await interaction.response.send_message(
                f"{member.display_name} is already in a duel.", ephemeral=True)
            return
        if self.wins_of(me.id) < GRAND_MIN_WINS:
            await interaction.response.send_message(
                f"You need at least **{GRAND_MIN_WINS}** 1v1 wins before calling a Grand Duel "
                f"(you have {self.wins_of(me.id)}).", ephemeral=True)
            return
        if self.wins_of(member.id) < GRAND_MIN_WINS:
            await interaction.response.send_message(
                f"{member.display_name} needs at least **{GRAND_MIN_WINS}** 1v1 wins "
                f"(they have {self.wins_of(member.id)}).", ephemeral=True)
            return

        gd = GrandDuel(self, interaction.channel, me, member)
        self.mark_busy(gd, me, member)
        await interaction.response.send_message(
            content=member.mention,
            embed=gd.challenge_embed(),
            view=GrandAcceptView(gd),
        )
        gd.message = await interaction.original_response()
        gd.start_accept_timer()

# Loaded after this class exists so sibling modules can annotate it.
from cogs.duels.match import (
    AcceptView,
    Duel,
)
from cogs.duels.trio import (
    TrioSignup,
    TrioSignupView,
)
from cogs.duels.grand import (
    GrandAcceptView,
    GrandDuel,
)
