"""
Wizard duels. Best of three by default. Optional best-of 5/7/9/51 series:
each set is first to 2, and the series winner banks 3/4/5/26 wins.

    /duel @member              - challenge someone (1v1)
    /duelrecord [member]       - rank, wins, streak, rivals, trio/grand, points
    /duelend [member]          - clear a stuck duel lock (self, or staff for others)
    /houseduels                - each house's overall win/loss duelling record
    /staff duels night start|end  - House Duel Night (30 min or 1 hour)
    /staff duels nightschedule    - 1–3 weekly nights (30m/1h), each with its own time
    /trio scramble             - open 3v3 signup (any houses)
    /trio housematch h1 h2     - house-gated 3v3 signup
    /grand @member             - Grand Duel (both need 50+ 1v1 wins)

Rewards: ranks by total wins, hidden Dueling Circle reputation (flourish,
wand glow, one-time gift), win streaks with bounties, rivals, stacked
signature-spell titles, trio/grand records and titles, and a weekly
Duelist of the Week (role + house points). Only wins, ranks, streaks and
titles are ever shown - never a win rate.

Five spells. Each beats exactly two others and loses to the other two, so
there is no safe pick - only reading your opponent. Both duelists open a
private cast board (spell buttons under the board, like broom races), then
both spells are revealed at once.

The winner's house earns 1 point, up to 3 duel points per person per day.
Duels past that still count toward the win/loss record. Duels between two
members of the SAME house never award points - otherwise two housemates
could simply trade wins and mint points for their house. Same-house wins
also never grow a win streak or pay a streak-bounty bonus.

House Duel Night doubles every cross-house win with no daily cap, tracks a
night tally, and when it ends crowns the winning house (plus an MVP bonus)
the same way a Wild Threat Attack tallies up.

Wands are cosmetic: they're shown in the duel, but they don't affect it.

Cast resolution mutates state under a lock, then publishes the board *after*
releasing it. A board generation counter drops superseded edits so a slow
"waiting on…" update can't overwrite a round that already resolved — the
usual "I cast but it's still waiting on me / frozen" failure when both
sides click fast.
"""

import asyncio
import datetime as dt
import json
import logging
import os
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.velmora_channels import (
    EVENT_ANNOUNCE_CHANNEL_ID,
    channel_mentions,
    duel_home_channels,
)

log = logging.getLogger("velmora.duels")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
DUELS_PATH = STATE_DIR / "duels.json"

ROUNDS_TO_WIN = 2          # each set is first to 2 (a normal duel)
MAX_ROUNDS = 9             # tie cap within one set
# /duel best_of: 3 = one set (1 win); 5/7/9/51 = series of sets → 3/4/5/26 wins
BEST_OF_OPTIONS = (3, 5, 7, 9, 51)
ACCEPT_TIMEOUT = 120


def series_to_win_for(best_of: int) -> int:
    """How many first-to-2 sets you need. Bo3 = 1 set; Bo5/7/9/51 = 3/4/5/26."""
    n = int(best_of)
    if n <= 3:
        return 1
    return n // 2 + 1


def max_rounds_for(best_of: int) -> int:
    """Tie-round budget across the whole match (sets × per-set cap)."""
    return MAX_ROUNDS * series_to_win_for(best_of) * 2


ROUND_TIMEOUT = 60
GRAND_LOCK_TIMEOUT = ACCEPT_TIMEOUT  # 2 min to lock both sequences, same as accept
REWARD_POINTS = 1
REWARDED_PER_DAY = 3
WINDOW = 24 * 3600

# Optional: a single Discord user ID who quietly wins duel exchanges more
# often than the spells alone would give them. Applies to 1v1 rounds, trio
# pairings, and Grand steps/sudden death. Needs DUEL_FAVORED_USER_ID in the
# environment AND FAVORED_ENABLED True. Flip FAVORED_ENABLED to pause/resume
# without clearing the Railway variable.
FAVORED_ENABLED = True  # on — set False to pause the edge
_raw_favored = os.getenv("DUEL_FAVORED_USER_ID")
FAVORED_USER_ID = (
    int(_raw_favored)
    if FAVORED_ENABLED and _raw_favored and _raw_favored.isdigit()
    else None
)
# Chance, PER exchange, that the favored user's side is decided in their
# favor regardless of what either side cast. Exchanges that don't trigger
# this fall through to the real rock-paper-scissors-of-five above, which
# is roughly a coin flip against an unpredictable opponent. There's no
# clean closed form from a per-round bias to a match-level win rate (best
# of 3, ties replay), so this default was tuned by simulation against a
# random opponent: 0.455 per round -> ~85% of 1v1 matches won overall,
# 0.342 -> ~78%, 0.30 -> ~75% (the current setting). A more adversarial
# opponent who could somehow read your picks would knock this down toward
# the raw per-exchange number, never above it - the bias never makes you
# invincible, only likely. Override with DUEL_FAVORED_BIAS if needed.
FAVORED_ROUND_BIAS = float(os.getenv("DUEL_FAVORED_BIAS", "0.30"))

SPELLS = {
    "hex":    {"name": "Hex",    "emoji": "⚡"},
    "ward":   {"name": "Ward",   "emoji": "\U0001F6E1️"},
    "disarm": {"name": "Disarm", "emoji": "\U0001FA84"},
    "bind":   {"name": "Bind",   "emoji": "⛓️"},
    "mirror": {"name": "Mirror", "emoji": "\U0001FA9E"},
}

# (winner, loser) -> what happened. Every pair appears exactly once, and each
# spell wins twice and loses twice.
BEATS = {
    ("hex", "disarm"):   "The hex lands before they can reach for a wand.",
    ("hex", "bind"):     "The hex tears straight through the binding.",
    ("ward", "hex"):     "The ward swallows the hex whole.",
    ("ward", "mirror"):  "A ward gives a mirror nothing to throw back.",
    ("disarm", "ward"):  "Their wand is gone before the ward can form.",
    ("disarm", "mirror"): "A mirror can't reflect a wand that's already flying.",
    ("bind", "ward"):    "The binding coils round the ward and squeezes it shut.",
    ("bind", "disarm"):  "Bound hands can't disarm anyone.",
    ("mirror", "hex"):   "The hex rebounds off the mirror into its own caster.",
    ("mirror", "bind"):  "The binding rebounds and ties up the one who cast it.",
}


def resolve(a: str, b: str) -> tuple[int, str]:
    """0 = tie, 1 = first spell wins, 2 = second spell wins; plus the line."""
    if a == b:
        return 0, f"Both cast {SPELLS[a]['name']}. The spells cancel out."
    if (a, b) in BEATS:
        return 1, BEATS[(a, b)]
    return 2, BEATS[(b, a)]


def apply_deadlock_keep(result: int, line: str, a_id: int, b_id: int, bot) -> tuple[int, str]:
    """Deadlock Keep castle perk: holder wins spell ties."""
    if result != 0 or bot is None:
        return result, line
    castles = bot.get_cog("Castles")
    if not castles:
        return result, line
    a_has = castles.deadlock_wins_ties(a_id)
    b_has = castles.deadlock_wins_ties(b_id)
    if a_has and not b_has:
        return 1, f"{line} Deadlock Keep breaks the stalemate!"
    if b_has and not a_has:
        return 2, f"{line} Deadlock Keep breaks the stalemate!"
    return result, line


def favor_display_spells(a_id: int, b_id: int, a_spell: str, b_spell: str,
                         rng: random.Random | None = None) -> tuple[str, str]:
    """Maybe rewrite one side's shown spell so resolve() favors FAVORED_USER_ID.

    Same trick as classic 1v1: credit the favored caster with a legitimate
    counter to the opponent's real pick, so the public line looks like a
    normal matchup. No-ops when the env var is unset, neither id matches,
    or the bias roll misses. Used by 1v1, trio pairings, and Grand.
    """
    if FAVORED_USER_ID is None or FAVORED_USER_ID not in (a_id, b_id):
        return a_spell, b_spell
    roll = (rng or random).random()
    if roll >= FAVORED_ROUND_BIAS:
        return a_spell, b_spell
    if FAVORED_USER_ID == a_id:
        counters = [w for (w, l) in BEATS if l == b_spell]
        if not counters:
            return a_spell, b_spell
        return (rng or random).choice(counters), b_spell
    counters = [w for (w, l) in BEATS if l == a_spell]
    if not counters:
        return a_spell, b_spell
    return a_spell, (rng or random).choice(counters)


# ------------------------------------------------------------ duel rewards
#
# Ranks come from all-time wins. Everything shown publicly is wins, rank,
# streaks and titles - never a win rate or a loss count.

RANKS = [            # (minimum wins, title) - highest first
    (1000, "Golden God"),
    (800, "Myth Made Flesh"),
    (600, "Unanswerable"),
    (400, "Terror of the Dueling Floor"),
    (250, "Wandlord"),
    (150, "Archmage of the Circle"),
    (100, "Legend"),
    (60, "Master of the Circle"),
    (30, "Spellblade"),
    (15, "Duelist"),
    (5, "Apprentice"),
    (1, "Novice"),
    (0, "Untested"),
]

# Signature titles STACK — keep every lower tier when a higher one unlocks.
# (round-wins with that spell, {spell: title})
SIGNATURE_TIERS = [
    (10, {
        "hex": "the Hexer",
        "ward": "the Wall",
        "disarm": "the Quickdraw",
        "bind": "the Binder",
        "mirror": "the Trickster",
    }),
    (50, {
        "hex": "the Hex Addict",
        "ward": "the Immovable Object",
        "disarm": "Twitchy",
        "bind": "the Rope Guy",
        "mirror": "Smoke and Mirrors",
    }),
    (150, {
        "hex": "Certified Hex Menace",
        "ward": "Human Fortress",
        "disarm": "Faster Than Your Wand",
        "bind": "Basically a Boy Scout",
        "mirror": "The Reflection Nobody Asked For",
    }),
    (500, {
        "hex": "The One-Trick Nightmare",
        "ward": "The Wall Has Feelings Now",
        "disarm": "Too Fast, Too Furious",
        "bind": "Knot Theory PhD",
        "mirror": "Mirror, Mirror, Shut Up",
    }),
]
# Flat lookup for adornments / legacy callers (spell -> lowest-tier title).
SIGNATURE_TITLES = SIGNATURE_TIERS[0][1]
SIGNATURE_AT = SIGNATURE_TIERS[0][0]

STREAK_ANNOUNCE = 3          # wins in a row before the channel hears about it
STREAK_EVERY = 5             # past the bounty, only announce every 5th win in a row
ANNOUNCE_RANKS_FROM = 5      # Novice (first win) isn't worth a post; Apprentice is
BOUNTY_AT = 5                # wins in a row before a bounty goes up
BOUNTY_POINTS = 2            # paid to whoever breaks it (outside the daily cap)

RIVAL_AFTER = 5              # duels between the same two people (point bonus threshold)
RIVAL_BONUS = 1              # extra point for beating your rival (uses a cap slot)
# Rival titles by total meetings — both players earn each tier when crossed.
RIVAL_TITLE_TIERS = [        # (meetings, title) - highest first
    (100, "Rivals Turned Lovers"),
    (75, "The Unfinished Duel"),
    (50, "Eternal Opposition"),
    (30, "Bound by Sparks"),
    (15, "Nemeses"),
    (5, "Rivals"),
]

# Silly lines for the duel board when a pair already holds a rivalry title.
# {a}/{b} = display names, {title} = rivalry title, {n} = meetings so far.
RIVAL_BOARD_BANTER = {
    "Rivals": [
        "**{a}** and **{b}** are **{title}**. The portraits have started keeping score.",
        "Title check: **{title}**. That's {n} meetings and counting — somebody buy them a snack.",
        "**{title}** enter the floor. The Circle mutters 'not these two again' fondly.",
    ],
    "Nemeses": [
        "**{a}** vs **{b}**: **{title}**. The stands brought snacks for rematch number {n}.",
        "Announcing **{title}**. If this were a play, act fifteen just began.",
        "**{title}** clash again. Somewhere, a betting pool updates in real time.",
    ],
    "Bound by Sparks": [
        "**{a}** and **{b}** are **{title}**. Sparks, paperwork, and unresolved eye contact.",
        "**{title}** ({n} meetings). The air between them has a loyalty program.",
        "Board note: **{title}**. Their wands recognize each other on sight.",
    ],
    "Eternal Opposition": [
        "**{title}**: **{a}** and **{b}**. The floor has a reserved groove for them.",
        "{n} meetings later and they're still **{title}**. Persistence is a love language.",
        "The Circle clears its throat. **{title}** have entered the chat.",
    ],
    "The Unfinished Duel": [
        "**{a}** and **{b}** — **{title}**. Spoiler: it still isn't finished.",
        "**{title}** rematch #{n}. Historians have given up waiting for a clean ending.",
        "Tonight's feature: **{title}**. Bring a pillow; this saga has seasons.",
    ],
    "Rivals Turned Lovers": [
        "**{a}** and **{b}** are **{title}**. The Circle is blushing. Rudely.",
        "**{title}** ({n} meetings). Please duel responsibly — or don't.",
        "Announcing **{title}**. Someone in the stands just dropped their popcorn.",
    ],
}

TRIO_SIZE = 3
TRIO_SIGNUP_TIMEOUT = 90
TRIO_ROUNDS_TO_WIN = 2       # best of three
TRIO_REWARD_POINTS = 1
TRIO_REWARDED_PER_DAY = 3
TRIO_TITLE_TIERS = [         # (trio wins, title) - highest first
    (50, "Three's Company"),
    (30, "Pack Hunter"),
    (15, "Triangle Terror"),
    (5, "Triad Novice"),
]

GRAND_MIN_WINS = 50          # 1v1 wins required on both sides
GRAND_SLOTS = 10
GRAND_TO_WIN = 6             # first to 6 of 10 (or sudden death on any tie after 10)
GRAND_REWARD_POINTS = 2
GRAND_REWARDED_PER_DAY = 3
GRAND_PLAY_DELAY = 5         # seconds between announced rounds
GRAND_TITLE_TIERS = [        # (grand wins, title) - highest first
    (1000, "Grandmaster of Inevitability"),
    (750, "I Knew You'd Pick That"),
    (500, "Destiny's Ghostwriter"),
    (250, "The Script Is Already Written"),
    (100, "Prophet of the Tenth Step"),
    (50, "Grand Architect"),
    (30, "Ten Steps Ahead"),
    (15, "Pattern Mage"),
    (5, "Sequencer"),
]
GRAND_RIVAL_TITLE_TIERS = [  # (grand meetings, title) - highest first
    (100, "Married In The Eyes Of The Circle"),
    (75, "The Longest Grudge"),
    (50, "Mutual Destruction Pact"),
    (30, "We Need To Stop Meeting Like This"),
    (15, "Calendar Nemeses"),
    (5, "Scheduled Enemies"),
]

GRAND_RIVAL_BOARD_BANTER = {
    "Scheduled Enemies": [
        "**{a}** and **{b}** are **{title}**. Their calendar app has trust issues.",
        "**{title}** ({n} Grand meetings). Penciled in: mutual chaos.",
    ],
    "Calendar Nemeses": [
        "**{title}**: **{a}** vs **{b}**. Even the dates are taking sides.",
        "{n} Grand meetings. **{title}**. RSVP: dramatic.",
    ],
    "We Need To Stop Meeting Like This": [
        "**{a}** and **{b}** — **{title}**. Narrator: they will not stop.",
        "**{title}** ({n} times). The Grand floor has a frequent-duelist punch card.",
    ],
    "Mutual Destruction Pact": [
        "**{title}** in effect. **{a}** and **{b}** signed in sparkles.",
        "Clause 1 of **{title}**: show up. Clause 2: make it weird. ({n} meetings.)",
    ],
    "The Longest Grudge": [
        "**{a}** vs **{b}**: **{title}**. This grudge has its own subplot.",
        "**{title}** after {n} Grand meetings. The portraits brought opera glasses.",
    ],
    "Married In The Eyes Of The Circle": [
        "**{title}**: **{a}** and **{b}**. The Circle demands a seating chart.",
        "{n} Grand meetings later — **{title}**. Someone prepare the confetti (and the wards).",
    ],
}

GRAND_THEATRE = [
    "The Circle holds its breath.",
    "Wands rise as one.",
    "A hush falls over the floor.",
    "Somewhere, a portrait covers its eyes.",
    "The stones remember this kind of duel.",
    "Fate sharpens its quill.",
    "Two spells leave two wands. Only one will land.",
    "The air tastes like ozone and bad decisions.",
    "Someone in the stands has already started a betting pool.",
    "A first-year whispers 'oh no' a little too loudly.",
    "The torches lean in, nosy as ever.",
    "Time stretches. Spells do not.",
    "The floor itself seems to brace.",
    "One of them smiles. The other notices.",
    "Destiny clears its throat.",
    "A moth chooses this exact moment to fly between them.",
    "The referee has left the building. There is no referee.",
    "History is taking notes. History has terrible handwriting.",
    "Both duelists blink. Neither yields.",
    "The next second will be very opinionated.",
]

# Hidden Dueling Circle reputation.
REP_WIN, REP_LOSS = 2, 1
REP_FLOURISH = 10            # your wins get a personal flourish
REP_GLOW = 20                # wand glow on your profile
REP_GIFT = 30                # one-time gift from the Circle
GIFT_POINTS = 3

WEEKLY_POINTS = 5            # Duelist of the Week's house bonus
CHAMPION_ROLE_NAME = os.getenv("DUEL_CHAMPION_ROLE_NAME", "Champion of the Circle")
DUEL_NIGHT_MULTIPLIER = 2
# Staff pick 30 or 60 minutes when starting; auto-ends at ends_at.
DUEL_NIGHT_PRESETS = {
    30: "30 minutes",
    60: "1 hour",
}
DUEL_NIGHT_MVP_BONUS = 5     # like Wild Threat Attack MVP bonus
DUEL_NIGHT_HOUSE_BONUS = 10  # winning house bonus at end of night
DUEL_NIGHT_COLOR = 0xB8434F
DUEL_NIGHT_WARN_MINUTES = 5
DUEL_NIGHT_SCHEDULE_MAX = 3
# Same role names as Attack warn/start unless overridden.
_DEFAULT_NIGHT_PING_NAMES = ("Champions", "WIZARDS AND WITCHES")
WEEKDAY_CHOICES = [
    app_commands.Choice(name="Monday", value=0),
    app_commands.Choice(name="Tuesday", value=1),
    app_commands.Choice(name="Wednesday", value=2),
    app_commands.Choice(name="Thursday", value=3),
    app_commands.Choice(name="Friday", value=4),
    app_commands.Choice(name="Saturday", value=5),
    app_commands.Choice(name="Sunday", value=6),
]
WEEKDAY_LABELS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)

FLOURISHES = [
    "The Circle hums as {name} lowers their wand.",
    "Sparks drift up from {name}'s wand like it's showing off.",
    "{name} bows to the Circle. The Circle, somehow, bows back.",
    "The torches around the Circle flare for {name}.",
    "A ripple of silver runs through the floor under {name}'s feet.",
]

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    TZ = dt.timezone.utc


def _chicago_now(ts: float | None = None) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts if ts is not None else time.time(), TZ)


def _night_ping_role_names() -> list[str]:
    raw = os.getenv("DUEL_NIGHT_PING_ROLE_NAMES", "").strip()
    if not raw:
        raw = os.getenv("ATTACK_PING_ROLE_NAMES", "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return list(_DEFAULT_NIGHT_PING_NAMES)


def rank_for(wins: int) -> str:
    for threshold, title in RANKS:
        if wins >= threshold:
            return title
    return "Untested"


def rank_index(wins: int) -> int:
    """0 = lowest rank. Used to spot a rank-up."""
    idx = 0
    for i, (threshold, _) in enumerate(sorted(RANKS)):
        if wins >= threshold:
            idx = i
    return idx


def week_key(now: float = None) -> str:
    import datetime as dt
    d = dt.datetime.fromtimestamp(now if now is not None else time.time(), TZ)
    y, w, _ = d.isocalendar()   # ISO weeks start Monday
    return f"{y}-W{w:02d}"


def pair_key(a: int, b: int) -> str:
    return f"{min(a, b)}-{max(a, b)}"


def titles_at_or_below(tiers: list, count: int) -> list[str]:
    """All titles from a highest-first (threshold, title) list that count unlocks."""
    return [title for threshold, title in reversed(tiers) if count >= threshold]


def highest_title(tiers: list, count: int):
    for threshold, title in tiers:
        if count >= threshold:
            return title
    return None


def rival_banter_line(
    a_name: str,
    b_name: str,
    title: str,
    meetings: int,
    banter: dict,
) -> str:
    """Pick a silly board line for an earned pair title (stable caller picks once)."""
    templates = banter.get(title) or [
        "**{a}** and **{b}** are **{title}** ({n} meetings). The Circle is watching fondly."
    ]
    return random.choice(templates).format(
        a=a_name, b=b_name, title=title, n=meetings
    )


def signature_unlocks_for(tally: dict) -> list[tuple[str, int, str]]:
    """(spell, threshold, title) for every unlocked signature tier across spells."""
    out = []
    for spell, n in (tally or {}).items():
        for threshold, titles in SIGNATURE_TIERS:
            if n >= threshold and spell in titles:
                out.append((spell, threshold, titles[spell]))
    return out


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
        state.setdefault("trio_rewarded", {})
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
        return self._rewarded_count("trio_rewarded", user_id, now)

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
        outcome = {
            "awarded": 0, "reason": None, "house": w_house, "notes": notes,
            "night": night, "rival": rivals, "match_wins": match_wins,
            "winner_sets": winner_sets, "loser_sets": loser_sets,
        }
        if not store or not w_house:
            outcome["reason"] = "no-house"
        elif same_house:
            outcome["reason"] = "same-house"
        elif night:
            # Duel Night: no daily cap. Every banked series win pays double
            # (rival meetings still double again). Scoreboard tallies the
            # full best-of bank in one credit so MVP / house totals match.
            per_bank = 2 if rivals else 1
            paid_units = match_wins * per_bank
            total_pts = paid_units * REWARD_POINTS * DUEL_NIGHT_MULTIPLIER
            self._credit_duel_night(
                winner.id, w_house, total_pts, wins=match_wins,
            )
            why = f"Duel win over {loser.display_name}"
            if match_wins > 1:
                why += f" (best-of series ×{match_wins})"
            if rivals:
                why += " (rival)"
            why += " (Duel Night)"
            self._award(store, w_house, total_pts, winner.id, why)
            outcome["awarded"] = total_pts
        else:
            total_pts = 0
            paid_units = 0
            for _ in range(match_wins):
                slots = REWARDED_PER_DAY - self.rewarded_today(winner.id, now)
                if slots <= 0:
                    if paid_units == 0:
                        outcome["reason"] = "daily-cap"
                    break
                wins_paid = 1 + (1 if rivals and slots >= 2 else 0)
                pts = wins_paid * REWARD_POINTS
                total_pts += pts
                paid_units += wins_paid
                self.state["rewarded"].setdefault(wid, []).extend([now] * wins_paid)
            if total_pts:
                why = f"Duel win over {loser.display_name}"
                if match_wins > 1:
                    why += f" (best-of series ×{match_wins})"
                if rivals and paid_units > match_wins:
                    why += " (rival)"
                self._award(store, w_house, total_pts, winner.id, why)
                outcome["awarded"] = total_pts
            elif outcome.get("reason") is None:
                outcome["reason"] = "daily-cap"

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
        trio_left = max(0, TRIO_REWARDED_PER_DAY - self.trio_rewarded_today(member.id))
        grand_left = max(0, GRAND_REWARDED_PER_DAY - self.grand_rewarded_today(member.id))

        title = member.display_name
        sig = self.signature_of(member.id)
        if sig:
            title += f" {sig[1]}"
        embed = discord.Embed(title=f"{title} — duelling record", color=0x6C5CE7)
        rec = self.record_of(member.id)
        losses = rec.get("l", 0)
        embed.add_field(name="Rank", value=rank_for(wins))
        embed.add_field(name="Wins", value=str(wins))
        embed.add_field(name="Losses", value=str(losses))
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
            value=f"{rec.get('trio_w', 0)}W — {rec.get('trio_l', 0)}L"
                  + (f"\n{', '.join(self.trio_titles_of(member.id))}" if self.trio_titles_of(member.id) else ""),
            inline=True,
        )
        embed.add_field(
            name="Grand",
            value=f"{rec.get('grand_w', 0)}W — {rec.get('grand_l', 0)}L"
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
        footer = (f"{left}/{REWARDED_PER_DAY} 1v1 • {trio_left}/{TRIO_REWARDED_PER_DAY} trio • "
                  f"{grand_left}/{GRAND_REWARDED_PER_DAY} grand points left today")
        if self.duel_night_on():
            footer += " • ⚔️ Duel Night: double points, no daily cap, no same-house points"
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="houseduels", description="Each house's overall duelling record.")
    async def houseduels(self, interaction: discord.Interaction):
        from cogs.store import HOUSES
        store = self.bot.get_cog("Store")
        guild = interaction.guild

        totals = {key: {"w": 0, "l": 0} for key in HOUSES}
        for uid, rec in self.state.get("records", {}).items():
            member = guild.get_member(int(uid)) if guild else None
            if member is None:
                continue
            house = store.member_house(member) if store else None
            if house not in totals:
                continue
            totals[house]["w"] += rec.get("w", 0)
            totals[house]["l"] += rec.get("l", 0)

        ranked = sorted(totals.items(), key=lambda kv: (-kv[1]["w"], kv[1]["l"]))

        lines = []
        for i, (house_key, rec) in enumerate(ranked, start=1):
            meta = HOUSES[house_key]
            w, l = rec["w"], rec["l"]
            lines.append(f"**{i}. {meta['emoji']} {meta['name']}** — {w}-{l}")

        embed = discord.Embed(
            title="⚔️ House Duelling Record",
            description="\n".join(lines) if lines else "No duels have been fought yet.",
            color=HOUSES[ranked[0][0]]["color"] if ranked and (ranked[0][1]["w"] or ranked[0][1]["l"]) else 0x6C5CE7,
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
        """Record a trio result. Never touches 1v1 w/l, streaks, signatures, or pairs."""
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
        paid_names: list[str] = []
        capped_names: list[str] = []
        if store and not same_house_teams:
            for member in winners:
                house = store.member_house(member)
                if not house:
                    continue
                if (not night) and self.trio_rewarded_today(member.id, now) >= TRIO_REWARDED_PER_DAY:
                    capped_names.append(member.display_name)
                    continue
                pts = TRIO_REWARD_POINTS * (DUEL_NIGHT_MULTIPLIER if night else 1)
                why = f"Trio win"
                if night:
                    why += " (Duel Night)"
                self._award(store, house, pts, member.id, why)
                if night:
                    self._credit_duel_night(member.id, house, pts)
                else:
                    self.state["trio_rewarded"].setdefault(str(member.id), []).append(now)
                awarded += pts
                paid_names.append(member.display_name)

        for member in winners:
            uid = str(member.id)
            wins = self.record_of(member.id)["trio_w"]
            prev = highest_title(TRIO_TITLE_TIERS, wins - 1)
            now_title = highest_title(TRIO_TITLE_TIERS, wins)
            if now_title and now_title != prev:
                notes.append(f"🔺 **{member.display_name}** is now **{now_title}** ({wins} trio wins).")

        self.save()
        return {
            "awarded": awarded, "night": night, "notes": notes,
            "same_house": same_house_teams, "paid": paid_names, "capped": capped_names,
            "house": house_w,
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


class Duel:
    """One duel, held in memory. Duels last a couple of minutes, so they
    aren't saved - a redeploy mid-duel simply ends it."""

    def __init__(self, cog: Duels, channel, challenger, opponent, *, best_of: int = 3):
        self.cog = cog
        self.channel = channel
        self.a = challenger
        self.b = opponent
        self.message = None
        self.best_of = int(best_of) if best_of in BEST_OF_OPTIONS else 3
        # Series of first-to-2 sets. Bo3 = one set (1 win); Bo5/7/9 = first to 3/4/5 sets.
        self.series_to_win = series_to_win_for(self.best_of)
        self.match_wins = self.series_to_win  # record wins for series champion
        self.max_rounds = max_rounds_for(self.best_of)
        self.set_score = {challenger.id: 0, opponent.id: 0}
        self.score = {challenger.id: 0, opponent.id: 0}  # round score within current set
        self.set_no = 1
        self.picks: dict[int, str] = {}
        self.round = 0  # round within the current set
        self.total_rounds = 0  # across the whole series (tie / timeout budget)
        self.history: list[str] = []
        self.state = "pending"     # pending -> active -> done
        self.lock = asyncio.Lock()
        self.publish_lock = asyncio.Lock()  # one board edit at a time
        self._timer = None
        self.board_gen = 0        # bumps on every planned board publish
        # Private cast boards (broom-race style): spell buttons live under
        # each duelist's ephemeral board, which edits in place each round.
        self.private_boards: dict[int, discord.Message] = {}
        self.opened: set[int] = set()
        # Fixed for this duel so board edits don't reshuffle the joke.
        self.rival_line = self.cog.rivalry_board_line(challenger, opponent)

    # -------------------------------------------------------------- display

    def challenge_embed(self) -> discord.Embed:
        if self.series_to_win <= 1:
            length = (
                "**Best of three** (first to 2). Counts as **1 win**."
            )
        else:
            length = (
                f"**Best of {self.best_of}** — first to **{self.series_to_win}** "
                f"duel wins (each duel is first to 2). Series winner banks "
                f"**{self.match_wins} wins**."
            )
        desc = (
            f"{self.cog._duelist_label(self.a)} challenges "
            f"{self.cog._duelist_label(self.b)}.\n\n"
            f"{length} Spells are chosen in secret and revealed together."
        )
        if self.rival_line:
            desc += f"\n\n⚔️ {self.rival_line}"
        embed = discord.Embed(
            title="A duel is called",
            description=desc,
            color=0xB8434F,
        )
        embed.set_footer(text=f"{self.b.display_name} has {ACCEPT_TIMEOUT // 60} minutes to answer")
        return embed

    def board_embed(self, footer: str = None) -> discord.Embed:
        a_r, b_r = self.score[self.a.id], self.score[self.b.id]
        a_s, b_s = self.set_score[self.a.id], self.set_score[self.b.id]
        if self.series_to_win <= 1:
            lines = [
                f"{self.cog._duelist_label(self.a)} **{a_r}** — "
                f"**{b_r}** {self.cog._duelist_label(self.b)}",
            ]
        else:
            lines = [
                f"Best of {self.best_of} · first to {self.series_to_win} "
                f"(each set first to {ROUNDS_TO_WIN})",
                f"Series: {self.cog._duelist_label(self.a)} **{a_s}** — "
                f"**{b_s}** {self.cog._duelist_label(self.b)}",
                f"This set: **{a_r}** — **{b_r}**",
            ]
        if self.rival_line:
            lines.append(f"⚔️ {self.rival_line}")
        if self.history:
            lines.append("")
            lines.extend(self.history[-4:])
        if self.state == "active":
            waiting = [m.display_name for m in (self.a, self.b) if m.id not in self.picks]
            lines.append("")
            if self.series_to_win > 1:
                round_label = f"**Set {self.set_no} · Round {self.round}**"
            else:
                round_label = f"**Round {self.round}**"
            lines.append(
                f"{round_label} — "
                + ("waiting on " + " and ".join(waiting) if waiting else "revealing…")
            )
        embed = discord.Embed(title="The duel", description="\n".join(lines), color=0xB8434F)
        embed.set_footer(text=footer or (
            f"Both duelists: Open my cast board (again if dismissed) • {ROUND_TIMEOUT}s per round"
            if self.state == "active"
            else f"{ROUND_TIMEOUT}s per round"
        ))
        return embed

    def private_embed(self, user_id: int, footer: str = None) -> discord.Embed:
        """Same scoreboard as public, with a footer aimed at this caster."""
        embed = self.board_embed(footer)
        if footer is not None:
            return embed
        if self.state == "active" and user_id in self.picks:
            spell = SPELLS[self.picks[user_id]]["name"]
            embed.set_footer(text=f"You cast {spell}. Waiting on your opponent…")
        elif self.state == "active":
            embed.set_footer(
                text=f"Pick a spell below • Open again if dismissed • {ROUND_TIMEOUT}s per round"
            )
        return embed

    def _public_view(self):
        if self.state == "active":
            return OpenCastBoardView(self)
        return None

    def _private_view_for(self, user_id: int):
        if self.state != "active":
            return None
        if user_id in self.picks:
            return None
        return PrivateSpellView(self)

    def _snapshot(self, view=None, footer=None):
        """Bump board gen and capture embed+view. Call while holding the lock
        (or when no concurrent publisher can race)."""
        self.board_gen += 1
        if view is None and self.state == "active":
            view = self._public_view()
        return self.board_embed(footer), view, self.board_gen, footer

    async def _publish(self, embed, view, gen, footer=None):
        """Edit the public duel message and any private cast boards if current.

        publish_lock serializes Discord edits so a slow stale write can't land
        *after* a newer board without the newer one getting to write last.
        """
        if self.message is None and not self.private_boards:
            return
        async with self.publish_lock:
            async with self.lock:
                if gen != self.board_gen:
                    return
                picks = dict(self.picks)
                state = self.state
                boards = dict(self.private_boards)
                round_no = self.round
            if self.message is not None:
                try:
                    await self.message.edit(content=None, embed=embed, view=view)
                except discord.DiscordException:
                    log.exception("Could not update the duel message.")
            for uid, msg in boards.items():
                try:
                    priv_footer = footer
                    if priv_footer is None and state == "active" and uid in picks:
                        priv_footer = (
                            f"You cast {SPELLS[picks[uid]]['name']}. "
                            "Waiting on your opponent…"
                        )
                    elif priv_footer is None and state == "active":
                        priv_footer = (
                            f"Pick a spell below • Open again if dismissed "
                            f"• {ROUND_TIMEOUT}s per round"
                        )
                    priv_embed = discord.Embed(
                        title=embed.title,
                        description=embed.description,
                        color=embed.color,
                    )
                    if priv_footer:
                        priv_embed.set_footer(text=priv_footer)
                    elif embed.footer and embed.footer.text:
                        priv_embed.set_footer(text=embed.footer.text)
                    priv_view = None
                    if state == "active" and uid not in picks:
                        priv_view = PrivateSpellView(self, round_no=round_no)
                    await msg.edit(embed=priv_embed, view=priv_view)
                except discord.DiscordException:
                    log.exception("Could not update private cast board for %s", uid)

    async def _update(self, view=None, footer=None):
        """Convenience: snapshot under the lock, then publish unlocked."""
        async with self.lock:
            snap = self._snapshot(view=view, footer=footer)
        await self._publish(*snap)

    # --------------------------------------------------------------- timing

    def _arm(self, coro):
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.create_task(coro)

    def start_accept_timer(self):
        self._arm(self._accept_timeout())

    async def _accept_timeout(self):
        await asyncio.sleep(ACCEPT_TIMEOUT)
        async with self.lock:
            if self.state != "pending":
                return
            self.cog.release_match(self)
        try:
            await self.message.edit(
                content=None,
                embed=discord.Embed(description=f"{self.b.display_name} never answered. "
                                                "The duel is off.", color=0x7A7A7A),
                view=None,
            )
        except discord.DiscordException:
            pass

    async def _round_timeout(self, round_no: int):
        await asyncio.sleep(ROUND_TIMEOUT)
        finish = None
        fizzle = None
        async with self.lock:
            if self.state != "active" or self.round != round_no:
                return
            cast = [m for m in (self.a, self.b) if m.id in self.picks]
            if len(cast) == 1:
                winner = cast[0]
                loser = self.b if winner is self.a else self.a
                self.history.append(f"*{loser.display_name} froze and never cast.*")
                finish = (winner, loser)
            else:
                self.cog.release_match(self)
                fizzle = self._snapshot(
                    view=None, footer="Neither duelist cast. The duel fizzles out.")
        if finish:
            await self._finish(finish[0], finish[1], forfeit=True)
        elif fizzle:
            await self._publish(*fizzle)

    # ------------------------------------------------------------ the rounds

    async def _next_round(self):
        arm_round = None
        async with self.lock:
            self.round += 1
            self.total_rounds += 1
            self.picks = {}
            if self.total_rounds > self.max_rounds:
                self.cog.release_match(self)
                snap = self._snapshot(view=None, footer="Too evenly matched — declared a draw.")
            else:
                snap = self._snapshot(view=OpenCastBoardView(self))
                arm_round = self.round
        await self._publish(*snap)
        if arm_round is not None:
            self._arm(self._round_timeout(arm_round))

    async def open_cast_board(self, interaction: discord.Interaction) -> None:
        """Send a private board with spells under it.

        Always allowed to open again — dismissing an ephemeral used to
        leave fat-fingered duelists stuck until they forfeited the round.
        """
        uid = interaction.user.id
        if uid not in self.score:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        async with self.lock:
            if self.state != "active":
                await interaction.response.send_message(
                    "This duel is over.", ephemeral=True
                )
                return
            self.opened.add(uid)
            embed = self.private_embed(uid)
            view = self._private_view_for(uid)
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )
        try:
            self.private_boards[uid] = await interaction.original_response()
        except discord.HTTPException:
            # Keep opened so publish still knows they participate; they
            # can press Open again for a fresh board.
            pass

    async def cast(self, member, spell: str, round_no: int = None) -> str:
        """Lock in a spell. Returns a message for the caster.

        State changes happen under the lock; Discord edits happen after release
        so a slow board update can't freeze the opponent's cast.
        """
        publish = None
        finish = None
        arm_round = None
        async with self.lock:
            if self.state != "active":
                return "This duel is over."
            if round_no is not None and round_no != self.round:
                return "That round is already over — pick again on your cast board."
            if member.id not in self.score:
                return "You're not in this duel."
            if member.id in self.picks:
                return f"You've already cast {SPELLS[self.picks[member.id]]['name']} this round."
            self.picks[member.id] = spell

            if len(self.picks) < 2:
                publish = self._snapshot(view=OpenCastBoardView(self))
                reply = f"You cast **{SPELLS[spell]['name']}**. Waiting on your opponent…"
            else:
                a_spell, b_spell = self.picks[self.a.id], self.picks[self.b.id]
                disp_a, disp_b = favor_display_spells(self.a.id, self.b.id, a_spell, b_spell)
                result, line = resolve(disp_a, disp_b)
                result, line = apply_deadlock_keep(
                    result, line, self.a.id, self.b.id, self.cog.bot,
                )
                reveal = (f"S{self.set_no}R{self.round}: {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                          f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {line}"
                          if self.series_to_win > 1 else
                          f"R{self.round}: {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                          f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {line}")
                if result == 1:
                    self.score[self.a.id] += 1
                    self.cog.note_round_win(self.a.id, disp_a)
                elif result == 2:
                    self.score[self.b.id] += 1
                    self.cog.note_round_win(self.b.id, disp_b)
                self.history.append(reveal)

                set_winner = None
                if self.score[self.a.id] >= ROUNDS_TO_WIN:
                    set_winner = self.a
                elif self.score[self.b.id] >= ROUNDS_TO_WIN:
                    set_winner = self.b

                if set_winner is not None:
                    self.set_score[set_winner.id] += 1
                    set_loser = self.b if set_winner.id == self.a.id else self.a
                    if self.series_to_win > 1:
                        self.history.append(
                            f"✅ **{set_winner.display_name}** takes set {self.set_no} "
                            f"({self.set_score[self.a.id]}–{self.set_score[self.b.id]})"
                        )
                    if self.set_score[set_winner.id] >= self.series_to_win:
                        finish = (set_winner, set_loser, False)
                    else:
                        # Next set: reset round score, keep going.
                        self.set_no += 1
                        self.score = {self.a.id: 0, self.b.id: 0}
                        self.round = 1
                        self.total_rounds += 1
                        self.picks = {}
                        if self.total_rounds > self.max_rounds:
                            self.cog.release_match(self)
                            publish = self._snapshot(
                                view=None, footer="Too evenly matched — declared a draw.")
                        else:
                            publish = self._snapshot(view=OpenCastBoardView(self))
                            arm_round = self.round
                else:
                    self.round += 1
                    self.total_rounds += 1
                    self.picks = {}
                    if self.total_rounds > self.max_rounds:
                        self.cog.release_match(self)
                        publish = self._snapshot(
                            view=None, footer="Too evenly matched — declared a draw.")
                    else:
                        publish = self._snapshot(view=OpenCastBoardView(self))
                        arm_round = self.round
                reply = f"You cast **{SPELLS[spell]['name']}**."

        if finish:
            await self._finish(finish[0], finish[1], forfeit=finish[2])
        elif publish:
            await self._publish(*publish)
            if arm_round is not None:
                self._arm(self._round_timeout(arm_round))
        return reply

    async def _finish(self, winner, loser, forfeit: bool = False):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self.cog.release_match(self)
        winner_sets = int(self.set_score.get(winner.id, 0) or 0)
        loser_sets = int(self.set_score.get(loser.id, 0) or 0)
        # Normal first-to-2: one set played → 1–0. Series: use the real set tally
        # so a 4–1 best-of-7 banks +4W/+1L for the champ and +1W/+4L for the other.
        if self.series_to_win <= 1:
            winner_sets = max(1, winner_sets)
            loser_sets = 0
        outcome = self.cog.settle(
            winner, loser,
            match_wins=self.match_wins,
            winner_sets=winner_sets,
            loser_sets=loser_sets,
        )

        from cogs.store import HOUSES
        if outcome.get("flourish"):
            self.history.append(f"*{outcome['flourish']}*")
        if outcome["awarded"]:
            h = HOUSES[outcome["house"]]
            tail = f"+{outcome['awarded']} to {h['emoji']} {h['name']}"
            if outcome.get("night"):
                tail += " (Duel Night!)"
        elif outcome["reason"] == "same-house":
            tail = "Housemates — no points change hands"
        elif outcome["reason"] == "daily-cap":
            tail = f"{winner.display_name} has taken today's {REWARDED_PER_DAY} duel points already"
        else:
            tail = "No house to credit"
        if forfeit:
            verb = "wins by forfeit"
        elif self.match_wins > 1:
            w_sets = outcome.get("winner_sets", self.match_wins)
            l_sets = outcome.get("loser_sets", 0)
            if l_sets:
                verb = (
                    f"wins the series (+{w_sets} wins, +{l_sets} loss"
                    f"{'' if l_sets == 1 else 'es'}; "
                    f"{loser.display_name} +{l_sets} win"
                    f"{'' if l_sets == 1 else 's'}, +{w_sets} losses)"
                )
            else:
                verb = (
                    f"wins the series (+{w_sets} wins; "
                    f"{loser.display_name} +{w_sets} losses)"
                )
        else:
            verb = "wins the duel"
        async with self.lock:
            snap = self._snapshot(view=None, footer=f"{winner.display_name} {verb} • {tail}")
        await self._publish(*snap)
        notes = outcome.get("notes") or []
        if notes and self.channel is not None:
            try:
                await self.channel.send("\n".join(notes))
            except discord.DiscordException:
                log.exception("Could not post duel announcements.")


# ------------------------------------------------------------------ the views

class AcceptView(discord.ui.View):
    def __init__(self, duel: Duel):
        super().__init__(timeout=ACCEPT_TIMEOUT + 5)
        self.duel = duel

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.duel
        if interaction.user.id != d.b.id:
            await interaction.response.send_message("This challenge isn't yours to answer.",
                                                    ephemeral=True)
            return
        async with d.lock:
            if d.state != "pending":
                await interaction.response.send_message("Too late for that.", ephemeral=True)
                return
            d.state = "active"
        await interaction.response.defer()
        d.round = 0
        await d._next_round()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.duel
        if interaction.user.id not in (d.a.id, d.b.id):
            await interaction.response.send_message("This isn't your duel.", ephemeral=True)
            return
        async with d.lock:
            if d.state != "pending":
                await interaction.response.send_message("Too late for that.", ephemeral=True)
                return
            d.cog.release_match(d)
        who = "withdraws" if interaction.user.id == d.a.id else "declines"
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(description=f"{interaction.user.display_name} {who}. "
                                            "No duel today.", color=0x7A7A7A),
            view=None,
        )


class OpenCastBoardView(discord.ui.View):
    """Public prompt — same idea as broom race's Open my race board."""

    def __init__(self, duel: Duel):
        super().__init__(timeout=ROUND_TIMEOUT * duel.max_rounds + 60)
        self.duel = duel

    @discord.ui.button(
        label="Open my cast board",
        style=discord.ButtonStyle.primary,
        emoji="\U0001FA84",
    )
    async def open_board(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.duel.open_cast_board(interaction)


class PrivateSpellView(discord.ui.View):
    """Spell buttons attached under the private cast board (edits in place)."""

    def __init__(self, duel: Duel, round_no: int | None = None):
        super().__init__(timeout=ROUND_TIMEOUT + 5)
        self.duel = duel
        self.round_no = duel.round if round_no is None else round_no
        for i, (key, spell) in enumerate(SPELLS.items()):
            self.add_item(SpellButton(key, spell, row=0 if i < 3 else 1))


class SpellButton(discord.ui.Button):
    def __init__(self, key: str, spell: dict, row: int = 0):
        super().__init__(
            label=spell["name"],
            emoji=spell["emoji"],
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        # Discord allows 3 seconds to answer a click; resolving a round also
        # edits boards, so acknowledge first. Success refreshes this private
        # board in place (options stay under the duel); errors get a followup.
        d = self.view.duel
        if interaction.user.id not in d.score:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        await interaction.response.defer()
        before = d.board_gen
        reply = await d.cast(interaction.user, self.key, self.view.round_no)
        if d.board_gen == before:
            try:
                await interaction.followup.send(reply, ephemeral=True)
            except discord.DiscordException:
                pass


# ==================================================================== Trio 3v3

class TrioSignup:
    def __init__(self, cog: Duels, is_house: bool, house_a: str = None, house_b: str = None):
        self.cog = cog
        self.is_house = is_house
        self.house_a = house_a
        self.house_b = house_b
        self.team_a: list = []   # members
        self.team_b: list = []
        self.started = False
        self.cancelled = False
        self.message = None
        self.timer_task = None

    def _side_name(self, side: str) -> str:
        from cogs.store import HOUSES
        if self.is_house:
            key = self.house_a if side == "a" else self.house_b
            return HOUSES[key]["name"]
        return "Team A" if side == "a" else "Team B"

    def embed(self) -> discord.Embed:
        from cogs.store import HOUSES
        if self.is_house:
            title = (f"⚔️ Trio: {HOUSES[self.house_a]['emoji']} {HOUSES[self.house_a]['name']} vs "
                     f"{HOUSES[self.house_b]['emoji']} {HOUSES[self.house_b]['name']}")
        else:
            title = "⚔️ Trio Scramble (3v3)"
        e = discord.Embed(title=title, description="Join a side. Best of three rounds once both are full.",
                          color=0xB8434F)
        for side, team in (("a", self.team_a), ("b", self.team_b)):
            e.add_field(
                name=f"{self._side_name(side)} ({len(team)}/{TRIO_SIZE})",
                value="\n".join(m.display_name for m in team) or "—",
                inline=True,
            )
        e.set_footer(text=f"Signup closes in {TRIO_SIGNUP_TIMEOUT}s")
        return e

    async def _timeout(self):
        try:
            await asyncio.sleep(TRIO_SIGNUP_TIMEOUT)
        except asyncio.CancelledError:
            return
        if self.started or self.cancelled or self.message is None:
            return
        self.cancelled = True
        for m in self.team_a + self.team_b:
            self.cog.busy.discard(m.id)
        try:
            await self.message.edit(
                embed=discord.Embed(
                    title="Trio signup expired",
                    description="Not enough duelists joined in time.",
                    color=0x7A7A7A,
                ),
                view=None,
            )
        except discord.DiscordException:
            pass

    async def join(self, interaction: discord.Interaction, side: str):
        if self.cancelled or self.started:
            await interaction.response.send_message("This signup is closed.", ephemeral=True)
            return
        uid = interaction.user.id
        already = any(m.id == uid for m in self.team_a + self.team_b)
        if uid in self.cog.busy and not already:
            await interaction.response.send_message("You're already in a duel.", ephemeral=True)
            return
        if already:
            await interaction.response.send_message("You're already signed up.", ephemeral=True)
            return
        target = self.team_a if side == "a" else self.team_b
        if len(target) >= TRIO_SIZE:
            await interaction.response.send_message("That side is full.", ephemeral=True)
            return
        if self.is_house:
            store = self.cog.bot.get_cog("Store")
            required = self.house_a if side == "a" else self.house_b
            member_house = store.member_house(interaction.user) if store else None
            if member_house != required:
                from cogs.store import HOUSES
                await interaction.response.send_message(
                    f"Only members of House {HOUSES[required]['name']} can join this side.",
                    ephemeral=True,
                )
                return
        target.append(interaction.user)
        self.cog.busy.add(uid)

        if len(self.team_a) >= TRIO_SIZE and len(self.team_b) >= TRIO_SIZE:
            self.started = True
            if self.timer_task:
                self.timer_task.cancel()
            match = TrioMatch(self)
            match.message = interaction.message
            match.round = 1
            await interaction.response.edit_message(
                embed=match.board_embed(), view=OpenTrioCastBoardView(match)
            )
            match._arm(match._round_timeout(1))
        else:
            await interaction.response.edit_message(embed=self.embed(), view=TrioSignupView(self))


class TrioJoinButton(discord.ui.Button):
    def __init__(self, side: str, label: str):
        super().__init__(label=label,
                         style=discord.ButtonStyle.success if side == "a" else discord.ButtonStyle.primary)
        self.side = side

    async def callback(self, interaction: discord.Interaction):
        await self.view.signup.join(interaction, self.side)


class TrioSignupView(discord.ui.View):
    def __init__(self, signup: TrioSignup):
        super().__init__(timeout=TRIO_SIGNUP_TIMEOUT + 10)
        self.signup = signup
        from cogs.store import HOUSES
        if signup.is_house:
            a = f"Join {HOUSES[signup.house_a]['name']}"
            b = f"Join {HOUSES[signup.house_b]['name']}"
        else:
            a, b = "Join Team A", "Join Team B"
        self.add_item(TrioJoinButton("a", a))
        self.add_item(TrioJoinButton("b", b))


class TrioMatch:
    def __init__(self, signup: TrioSignup):
        self.cog = signup.cog
        self.is_house = signup.is_house
        self.house_a = signup.house_a
        self.house_b = signup.house_b
        self.team_a = list(signup.team_a)
        self.team_b = list(signup.team_b)
        # Fixed random pairings for the whole match.
        a = list(self.team_a)
        b = list(self.team_b)
        random.shuffle(a)
        random.shuffle(b)
        self.pairings = list(zip(a, b))
        self.score_a = 0
        self.score_b = 0
        self.round = 0
        self.picks: dict[int, str] = {}
        self.history: list[str] = []
        self.state = "active"
        self.lock = asyncio.Lock()
        self.publish_lock = asyncio.Lock()
        self.message = None
        self._timer = None
        self.board_gen = 0
        self.channel = signup.message.channel if signup.message else None
        self.private_boards: dict[int, discord.Message] = {}
        self.opened: set[int] = set()

    def all_players(self):
        return self.team_a + self.team_b

    def side_name(self, side: str) -> str:
        from cogs.store import HOUSES
        if self.is_house:
            key = self.house_a if side == "a" else self.house_b
            return HOUSES[key]["name"]
        return "Team A" if side == "a" else "Team B"

    def board_embed(self, footer: str = None) -> discord.Embed:
        lines = [f"**{self.side_name('a')}** {self.score_a} — {self.score_b} **{self.side_name('b')}**"]
        lines.append("Pairings: " + ", ".join(
            f"{a.display_name} vs {b.display_name}" for a, b in self.pairings
        ))
        if self.history:
            lines.append("")
            lines.extend(self.history[-5:])
        if self.state == "active":
            waiting = [m.display_name for m in self.all_players() if m.id not in self.picks]
            lines.append("")
            lines.append(f"**Round {self.round}** — "
                         + ("waiting on " + ", ".join(waiting) if waiting else "revealing…"))
        embed = discord.Embed(title="Trio Duel", description="\n".join(lines), color=0xB8434F)
        embed.set_footer(text=footer or (
            f"Duelists: Open my cast board (again if dismissed) • {ROUND_TIMEOUT}s per round"
            if self.state == "active"
            else f"{ROUND_TIMEOUT}s per round"
        ))
        return embed

    def private_embed(self, user_id: int, footer: str = None) -> discord.Embed:
        embed = self.board_embed(footer)
        if footer is not None:
            return embed
        pick = self.picks.get(user_id)
        if self.state == "active" and pick:
            embed.set_footer(text=f"You cast {SPELLS[pick]['name']}. Waiting…")
        elif self.state == "active":
            embed.set_footer(
                text=f"Pick a spell below • Open again if dismissed • {ROUND_TIMEOUT}s per round"
            )
        return embed

    def start_round(self):
        self.round += 1
        self.picks = {}
        self._arm(self._round_timeout(self.round))

    def _arm(self, coro):
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.create_task(coro)

    def _snapshot(self, view=None, footer=None):
        self.board_gen += 1
        if view is None and self.state == "active":
            view = OpenTrioCastBoardView(self)
        return self.board_embed(footer), view, self.board_gen, footer

    async def _publish(self, embed, view, gen, footer=None):
        if self.message is None and not self.private_boards:
            return
        async with self.publish_lock:
            async with self.lock:
                if gen != self.board_gen:
                    return
                picks = dict(self.picks)
                state = self.state
                boards = dict(self.private_boards)
                round_no = self.round
            if self.message is not None:
                try:
                    await self.message.edit(content=None, embed=embed, view=view)
                except discord.DiscordException:
                    log.exception("Could not update trio message.")
            for uid, msg in boards.items():
                try:
                    priv_footer = footer
                    pick = picks.get(uid)
                    if priv_footer is None and state == "active" and pick:
                        priv_footer = f"You cast {SPELLS[pick]['name']}. Waiting…"
                    elif priv_footer is None and state == "active":
                        priv_footer = (
                            f"Pick a spell below • Open again if dismissed "
                            f"• {ROUND_TIMEOUT}s per round"
                        )
                    priv_embed = discord.Embed(
                        title=embed.title,
                        description=embed.description,
                        color=embed.color,
                    )
                    if priv_footer:
                        priv_embed.set_footer(text=priv_footer)
                    elif embed.footer and embed.footer.text:
                        priv_embed.set_footer(text=embed.footer.text)
                    priv_view = None
                    if state == "active" and uid not in picks:
                        priv_view = TrioPrivateSpellView(self, round_no=round_no)
                    await msg.edit(embed=priv_embed, view=priv_view)
                except discord.DiscordException:
                    log.exception("Could not update trio private cast board for %s", uid)

    async def _update(self, view=None, footer=None):
        async with self.lock:
            snap = self._snapshot(view=view, footer=footer)
        await self._publish(*snap)

    async def open_cast_board(self, interaction: discord.Interaction) -> None:
        """Send/reopen private board — dismiss is recoverable (no forfeit trap)."""
        uid = interaction.user.id
        ids = {p.id for p in self.all_players()}
        if uid not in ids:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        async with self.lock:
            if self.state != "active":
                await interaction.response.send_message(
                    "This trio is over.", ephemeral=True
                )
                return
            self.opened.add(uid)
            embed = self.private_embed(uid)
            view = (
                None
                if uid in self.picks
                else TrioPrivateSpellView(self, round_no=self.round)
            )
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )
        try:
            self.private_boards[uid] = await interaction.original_response()
        except discord.HTTPException:
            pass

    async def _round_timeout(self, round_no: int):
        await asyncio.sleep(ROUND_TIMEOUT)
        resolve = False
        fizzle = None
        async with self.lock:
            if self.state != "active" or self.round != round_no:
                return
            # Non-casters forfeit their pairing.
            for a, b in self.pairings:
                if a.id not in self.picks and b.id in self.picks:
                    self.picks[a.id] = None  # marker: auto-loss
                elif b.id not in self.picks and a.id in self.picks:
                    self.picks[b.id] = None
            if len([m for m in self.all_players() if m.id in self.picks]) < 2:
                self.state = "done"
                for m in self.all_players():
                    self.cog.busy.discard(m.id)
                fizzle = self._snapshot(view=None, footer="Too few casts — the trio fizzles out.")
            else:
                resolve = True
                follow = self._resolve_round_locked()
        if fizzle:
            await self._publish(*fizzle)
            return
        if resolve:
            await self._apply_trio_followup(follow)

    async def cast(self, member, spell: str, round_no: int = None) -> str:
        publish = None
        follow = None
        async with self.lock:
            if self.state != "active":
                return "This trio is over."
            if round_no is not None and round_no != self.round:
                return "That round is already over — pick again on your cast board."
            ids = {m.id for m in self.all_players()}
            if member.id not in ids:
                return "You're not in this trio."
            if member.id in self.picks:
                prev = self.picks[member.id]
                if prev:
                    return f"You've already cast {SPELLS[prev]['name']} this round."
                return "You've already cast this round."
            self.picks[member.id] = spell
            if len(self.picks) < len(self.all_players()):
                publish = self._snapshot(view=OpenTrioCastBoardView(self))
            else:
                follow = self._resolve_round_locked()
            reply = (
                f"You cast **{SPELLS[spell]['name']}**. Waiting on your team…"
                if publish else f"You cast **{SPELLS[spell]['name']}**."
            )
        if publish:
            await self._publish(*publish)
        elif follow:
            await self._apply_trio_followup(follow)
        return reply

    def _resolve_round_locked(self) -> dict:
        """Score the round under the lock; return Discord work for outside."""
        if self._timer:
            self._timer.cancel()
        a_pair_wins = 0
        b_pair_wins = 0
        bits = []
        for pa, pb in self.pairings:
            sa, sb = self.picks.get(pa.id), self.picks.get(pb.id)
            if sa is None and sb is None:
                bits.append(f"{pa.display_name} & {pb.display_name} both froze.")
                continue
            if sa is None:
                b_pair_wins += 1
                bits.append(f"{pa.display_name} froze — point to {pb.display_name}.")
                continue
            if sb is None:
                a_pair_wins += 1
                bits.append(f"{pb.display_name} froze — point to {pa.display_name}.")
                continue
            disp_a, disp_b = favor_display_spells(pa.id, pb.id, sa, sb)
            result, line = resolve(disp_a, disp_b)
            result, line = apply_deadlock_keep(
                result, line, pa.id, pb.id, self.cog.bot,
            )
            bits.append(
                f"{SPELLS[disp_a]['emoji']} {pa.display_name} vs {SPELLS[disp_b]['emoji']} "
                f"{pb.display_name} — {line}"
            )
            if result == 1:
                a_pair_wins += 1
            elif result == 2:
                b_pair_wins += 1
        self.history.append(f"**R{self.round}:** " + " | ".join(bits))
        if a_pair_wins > b_pair_wins:
            self.score_a += 1
            self.history.append(f"→ **{self.side_name('a')}** takes the round ({a_pair_wins}-{b_pair_wins}).")
        elif b_pair_wins > a_pair_wins:
            self.score_b += 1
            self.history.append(f"→ **{self.side_name('b')}** takes the round ({b_pair_wins}-{a_pair_wins}).")
        else:
            self.history.append(f"→ Round tied ({a_pair_wins}-{b_pair_wins}). Replaying.")

        if self.score_a >= TRIO_ROUNDS_TO_WIN:
            return {"finish": (self.team_a, self.team_b)}
        if self.score_b >= TRIO_ROUNDS_TO_WIN:
            return {"finish": (self.team_b, self.team_a)}
        if self.round >= MAX_ROUNDS:
            self.state = "done"
            for m in self.all_players():
                self.cog.busy.discard(m.id)
            return {
                "publish": self._snapshot(view=None, footer="Too evenly matched — declared a draw."),
            }
        self.round += 1
        self.picks = {}
        arm = self.round
        return {
            "publish": self._snapshot(view=OpenTrioCastBoardView(self)),
            "arm": arm,
        }

    async def _apply_trio_followup(self, follow: dict):
        if follow.get("finish"):
            await self._finish(follow["finish"][0], follow["finish"][1])
            return
        if follow.get("publish"):
            await self._publish(*follow["publish"])
        if follow.get("arm") is not None:
            self._arm(self._round_timeout(follow["arm"]))

    async def _finish(self, winners, losers):
        self.state = "done"
        if self._timer:
            self._timer.cancel()
        for m in self.all_players():
            self.cog.busy.discard(m.id)
        outcome = self.cog.settle_trio(winners, losers)
        from cogs.store import HOUSES
        win_ids = {m.id for m in winners}
        win_side = "a" if win_ids == {m.id for m in self.team_a} else "b"
        if outcome["awarded"]:
            h = HOUSES.get(outcome.get("house") or "", {})
            if h:
                tail = f"+{outcome['awarded']} to {h.get('emoji', '')} {h.get('name', '')}"
            else:
                tail = f"+{outcome['awarded']} house points"
            if outcome.get("night"):
                tail += " (Duel Night!)"
        elif outcome.get("same_house"):
            tail = "Same-house teams — no points"
        elif outcome.get("capped") and not outcome.get("paid"):
            tail = "Daily trio point cap reached"
        else:
            tail = "No house points this match"
        async with self.lock:
            snap = self._snapshot(
                view=None,
                footer=f"{self.side_name(win_side)} wins the trio • {tail}",
            )
        await self._publish(*snap)
        notes = outcome.get("notes") or []
        if notes and self.channel is not None:
            try:
                await self.channel.send("\n".join(notes))
            except discord.DiscordException:
                log.exception("Could not post trio announcements.")


class OpenTrioCastBoardView(discord.ui.View):
    def __init__(self, match: TrioMatch):
        super().__init__(timeout=ROUND_TIMEOUT * MAX_ROUNDS + 60)
        self.match = match

    @discord.ui.button(
        label="Open my cast board",
        style=discord.ButtonStyle.primary,
        emoji="\U0001FA84",
    )
    async def open_board(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.match.open_cast_board(interaction)


class TrioPrivateSpellView(discord.ui.View):
    def __init__(self, match: TrioMatch, round_no: int | None = None):
        super().__init__(timeout=ROUND_TIMEOUT + 5)
        self.match = match
        self.round_no = match.round if round_no is None else round_no
        for i, (key, spell) in enumerate(SPELLS.items()):
            self.add_item(TrioSpellButton(key, spell, row=0 if i < 3 else 1))


class TrioSpellButton(discord.ui.Button):
    def __init__(self, key: str, spell: dict, row: int = 0):
        super().__init__(
            label=spell["name"],
            emoji=spell["emoji"],
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        m = self.view.match
        ids = {p.id for p in m.all_players()}
        if interaction.user.id not in ids:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        await interaction.response.defer()
        before = m.board_gen
        reply = await m.cast(interaction.user, self.key, self.view.round_no)
        if m.board_gen == before:
            try:
                await interaction.followup.send(reply, ephemeral=True)
            except discord.DiscordException:
                pass


# ==================================================================== Grand Duel

class GrandDuel:
    def __init__(self, cog: Duels, channel, challenger, opponent):
        self.cog = cog
        self.channel = channel
        self.a = challenger
        self.b = opponent
        self.message = None
        self.state = "pending"   # pending -> locking -> playing -> sudden -> done
        self.sequences: dict[int, list] = {challenger.id: [], opponent.id: []}
        self.locked: set[int] = set()
        self.score = {challenger.id: 0, opponent.id: 0}
        self.round = 0
        self.history: list[str] = []
        self.sudden_picks: dict[int, str] = {}
        self.lock = asyncio.Lock()
        self.publish_lock = asyncio.Lock()
        self._timer = None
        self._sequence_task: asyncio.Task | None = None
        self.board_gen = 0
        self.private_boards: dict[int, discord.Message] = {}
        self.opened: set[int] = set()
        self.rival_line = self.cog.grand_rivalry_board_line(challenger, opponent)

    def challenge_embed(self) -> discord.Embed:
        desc = (
            f"**{self.a.display_name}** challenges **{self.b.display_name}** to a Grand Duel.\n\n"
            f"Both lock a blind sequence of **{GRAND_SLOTS}** spells. First to **{GRAND_TO_WIN}** "
            f"round wins. Same spell = neither scores. A tie after 10 goes to live sudden death."
        )
        if self.rival_line:
            desc += f"\n\n⚔️ {self.rival_line}"
        embed = discord.Embed(
            title="A Grand Duel is called",
            description=desc,
            color=0xD4A017,
        )
        embed.set_footer(text=f"{self.b.display_name} has {ACCEPT_TIMEOUT // 60} minutes to answer")
        return embed

    def board_embed(self, footer: str = None) -> discord.Embed:
        a_s, b_s = self.score[self.a.id], self.score[self.b.id]
        lines = [f"**{self.a.display_name}** {a_s} — {b_s} **{self.b.display_name}**"]
        if self.rival_line:
            lines.append(f"⚔️ {self.rival_line}")
        if self.state == "locking":
            for m in (self.a, self.b):
                status = "sequence locked ✓" if m.id in self.locked else "building their sequence…"
                lines.append(f"• {m.display_name}: {status}")
        if self.history:
            lines.append("")
            lines.extend(self.history[-6:])
        title = "Grand Duel" + (" — Sudden Death" if self.state == "sudden" else "")
        embed = discord.Embed(title=title, description="\n".join(lines), color=0xD4A017)
        embed.set_footer(text=footer or "The Circle is watching.")
        return embed

    def _arm(self, coro):
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.create_task(coro)

    def start_accept_timer(self):
        self._arm(self._accept_timeout())

    async def _accept_timeout(self):
        await asyncio.sleep(ACCEPT_TIMEOUT)
        async with self.lock:
            if self.state != "pending":
                return
            self.cog.release_match(self)
        try:
            await self.message.edit(
                content=None,
                embed=discord.Embed(description=f"{self.b.display_name} never answered. "
                                                "The Grand Duel is off.", color=0x7A7A7A),
                view=None,
            )
        except discord.DiscordException:
            pass

    async def _locking_timeout(self):
        await asyncio.sleep(GRAND_LOCK_TIMEOUT)
        async with self.lock:
            if self.state != "locking":
                return
            self.cog.release_match(self)
            snap = self._snapshot(
                view=None,
                footer="Sequences were never locked in time. The Grand Duel dissolves.",
            )
        await self._publish(*snap)

    def _snapshot(self, view=None, footer=None):
        self.board_gen += 1
        return self.board_embed(footer), view, self.board_gen, footer

    async def _publish(self, embed, view, gen, footer=None):
        if self.message is None and not self.private_boards:
            return
        async with self.publish_lock:
            async with self.lock:
                if gen != self.board_gen:
                    return
                state = self.state
                sudden = dict(self.sudden_picks)
                boards = dict(self.private_boards)
            if self.message is not None:
                try:
                    await self.message.edit(content=None, embed=embed, view=view)
                except discord.DiscordException:
                    log.exception("Could not update Grand Duel message.")
            # Private boards are only used in sudden death (sequence builder
            # is its own ephemeral opened from the lock prompt).
            if state == "sudden" or state == "done":
                for uid, msg in boards.items():
                    try:
                        priv_footer = footer
                        pick = sudden.get(uid)
                        if priv_footer is None and state == "sudden" and pick:
                            priv_footer = (
                                f"You cast {SPELLS[pick]['name']}. Waiting…"
                            )
                        elif priv_footer is None and state == "sudden":
                            priv_footer = "Sudden death — pick a spell below."
                        priv_embed = discord.Embed(
                            title=embed.title,
                            description=embed.description,
                            color=embed.color,
                        )
                        if priv_footer:
                            priv_embed.set_footer(text=priv_footer)
                        elif embed.footer and embed.footer.text:
                            priv_embed.set_footer(text=embed.footer.text)
                        priv_view = None
                        if state == "sudden" and uid not in sudden:
                            priv_view = GrandSuddenPrivateSpellView(self)
                        await msg.edit(embed=priv_embed, view=priv_view)
                    except discord.DiscordException:
                        log.exception(
                            "Could not update Grand sudden board for %s", uid
                        )

    async def _update(self, view=None, footer=None):
        async with self.lock:
            snap = self._snapshot(view=view, footer=footer)
        await self._publish(*snap)

    async def begin_locking(self):
        self.state = "locking"
        self._arm(self._locking_timeout())
        await self._update(
            view=GrandLockPromptView(self),
            footer=(
                f"Both duelists: Open my sequence board "
                f"({GRAND_LOCK_TIMEOUT // 60} min)."
            ),
        )

    async def open_sudden_board(self, interaction: discord.Interaction) -> None:
        """Send/reopen sudden-death board — dismiss is recoverable."""
        uid = interaction.user.id
        if uid not in self.score:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        async with self.lock:
            if self.state != "sudden":
                await interaction.response.send_message(
                    "Sudden death isn't open.", ephemeral=True
                )
                return
            self.opened.add(uid)
            embed = self.board_embed()
            if uid in self.sudden_picks:
                embed.set_footer(
                    text=(
                        f"You cast {SPELLS[self.sudden_picks[uid]]['name']}. "
                        "Waiting…"
                    )
                )
                view = None
            else:
                embed.set_footer(text="Sudden death — pick a spell below.")
                view = GrandSuddenPrivateSpellView(self)
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )
        try:
            self.private_boards[uid] = await interaction.original_response()
        except discord.HTTPException:
            pass

    def try_lock(self, user_id: int, sequence: list[str]) -> str:
        if self.state != "locking":
            return "Locking is closed."
        if user_id not in self.sequences:
            return "You're not in this Grand Duel."
        if user_id in self.locked:
            return "You've already locked your sequence."
        if len(sequence) != GRAND_SLOTS or any(s not in SPELLS for s in sequence):
            return f"You need exactly {GRAND_SLOTS} spells."
        self.sequences[user_id] = list(sequence)
        self.locked.add(user_id)
        return "locked"

    async def maybe_start_play(self):
        if len(self.locked) < 2:
            await self._update(view=GrandLockPromptView(self))
            return
        # Only one sequence playback — both submit callbacks can race here.
        if self.state == "playing" or (
            self._sequence_task is not None and not self._sequence_task.done()
        ):
            return
        self.state = "playing"
        if self._timer:
            self._timer.cancel()
            self._timer = None
        await self._update(view=None, footer="Sequences locked. The Circle begins…")
        # Keep a strong reference — the loop only weakly refs tasks; without
        # this, GC can cancel mid-playback and skip sudden death after a tie.
        self._sequence_task = asyncio.create_task(
            self._play_sequence(),
            name=f"grand-sequence-{self.a.id}-{self.b.id}",
        )

    async def _play_sequence(self):
        try:
            await self._play_sequence_inner()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Grand Duel sequence crashed (%s vs %s)",
                self.a.id, self.b.id,
            )
            try:
                async with self.lock:
                    if self.state not in ("done", "sudden"):
                        self.cog.release_match(self)
                        snap = self._snapshot(
                            view=None,
                            footer="The Grand sequence broke — duel dissolved. Call /grand again.",
                        )
                    else:
                        snap = None
                if snap:
                    await self._publish(*snap)
            except Exception:
                log.exception("Could not recover Grand Duel after sequence crash.")

    async def _play_sequence_inner(self):
        seq_a = self.sequences[self.a.id]
        seq_b = self.sequences[self.b.id]
        for i in range(GRAND_SLOTS):
            await asyncio.sleep(GRAND_PLAY_DELAY)
            async with self.lock:
                if self.state != "playing":
                    return
                self.round = i + 1
                sa, sb = seq_a[i], seq_b[i]
                disp_a, disp_b = favor_display_spells(self.a.id, self.b.id, sa, sb)
                line = random.choice(GRAND_THEATRE)
                result, detail = resolve(disp_a, disp_b)
                result, detail = apply_deadlock_keep(
                    result, detail, self.a.id, self.b.id, self.cog.bot,
                )
                who = ""
                if result == 1:
                    self.score[self.a.id] += 1
                    who = f"**{self.a.display_name}** scores."
                elif result == 2:
                    self.score[self.b.id] += 1
                    who = f"**{self.b.display_name}** scores."
                else:
                    who = "Neither scores."
                self.history.append(
                    f"*{line}*\n"
                    f"**Step {self.round}:** {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                    f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {detail} {who}"
                )
                snap = self._snapshot(view=None)
            await self._publish(*snap)

        finish = None
        sudden = None
        arm_sudden = None
        async with self.lock:
            if self.state != "playing":
                return
            a_s, b_s = self.score[self.a.id], self.score[self.b.id]
            if a_s > b_s and a_s >= GRAND_TO_WIN:
                finish = (self.a, self.b)
            elif b_s > a_s and b_s >= GRAND_TO_WIN:
                finish = (self.b, self.a)
            elif a_s == b_s:
                self.state = "sudden"
                self.sudden_picks = {}
                self.history.append(
                    f"**{a_s}–{b_s}.** Sudden death. One spell each, until someone lands a hit."
                )
                # Fresh private boards for sudden death (sequence boards are done).
                self.private_boards = {}
                self.opened = set()
                sudden_view = OpenGrandSuddenBoardView(self)
                sudden = self._snapshot(
                    view=sudden_view,
                    footer="Sudden death — Open my cast board (again if dismissed).",
                )
                arm_sudden = self.round
            elif a_s > b_s:
                finish = (self.a, self.b)
            else:
                finish = (self.b, self.a)
        if finish:
            await self._finish(finish[0], finish[1])
            return
        if sudden:
            await self._publish(*sudden)
            # Belt-and-suspenders: if the board edit was dropped, post a fresh one.
            if self.state == "sudden" and self.channel is not None:
                embed, view, _, _ = sudden
                try:
                    if self.message is not None:
                        await self.message.edit(content=None, embed=embed, view=view)
                    else:
                        self.message = await self.channel.send(embed=embed, view=view)
                except discord.DiscordException:
                    log.exception("Grand sudden-death board edit failed; posting fallback.")
                    try:
                        self.message = await self.channel.send(embed=embed, view=view)
                    except discord.DiscordException:
                        log.exception("Could not post fallback Grand sudden-death board.")
                try:
                    await self.channel.send(
                        f"⚡ **Sudden death!** "
                        f"**{self.a.display_name}** vs **{self.b.display_name}** — "
                        f"open your cast board on the Grand Duel message."
                    )
                except discord.DiscordException:
                    log.exception("Could not announce Grand sudden death.")
            self._arm(self._sudden_timeout(arm_sudden))

    async def _sudden_timeout(self, marker: int):
        await asyncio.sleep(ROUND_TIMEOUT)
        finish = None
        dissolve = None
        async with self.lock:
            if self.state != "sudden" or self.round != marker:
                return
            cast = [m for m in (self.a, self.b) if m.id in self.sudden_picks]
            if len(cast) == 1:
                winner = cast[0]
                loser = self.b if winner is self.a else self.a
                self.history.append(f"*{loser.display_name} froze in sudden death.*")
                finish = (winner, loser)
            else:
                self.cog.release_match(self)
                dissolve = self._snapshot(
                    view=None, footer="Neither cast. The Grand Duel dissolves.")
        if finish:
            await self._finish(finish[0], finish[1])
        elif dissolve:
            await self._publish(*dissolve)

    async def sudden_cast(self, member, spell: str) -> str:
        publish = None
        finish = None
        arm_round = None
        async with self.lock:
            if self.state != "sudden":
                return "Sudden death isn't running."
            if member.id not in self.score:
                return "You're not in this Grand Duel."
            if member.id in self.sudden_picks:
                return f"You've already cast {SPELLS[self.sudden_picks[member.id]]['name']}."
            self.sudden_picks[member.id] = spell
            if len(self.sudden_picks) < 2:
                publish = self._snapshot(view=OpenGrandSuddenBoardView(self))
                reply = f"You cast **{SPELLS[spell]['name']}**. Waiting…"
            else:
                sa, sb = self.sudden_picks[self.a.id], self.sudden_picks[self.b.id]
                self.round += 1
                line = random.choice(GRAND_THEATRE)
                disp_a, disp_b = favor_display_spells(self.a.id, self.b.id, sa, sb)
                result, detail = resolve(disp_a, disp_b)
                result, detail = apply_deadlock_keep(
                    result, detail, self.a.id, self.b.id, self.cog.bot,
                )
                self.sudden_picks = {}
                if result == 0:
                    self.history.append(
                        f"*{line}*\n"
                        f"**Sudden {self.round}:** {SPELLS[disp_a]['emoji']} vs "
                        f"{SPELLS[disp_b]['emoji']} — {detail} Again."
                    )
                    publish = self._snapshot(view=OpenGrandSuddenBoardView(self))
                    arm_round = self.round
                elif result == 1:
                    self.score[self.a.id] += 1
                    self.history.append(
                        f"*{line}*\n"
                        f"**Sudden {self.round}:** {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                        f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {detail}"
                    )
                    finish = (self.a, self.b)
                else:
                    self.score[self.b.id] += 1
                    self.history.append(
                        f"*{line}*\n"
                        f"**Sudden {self.round}:** {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                        f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {detail}"
                    )
                    finish = (self.b, self.a)
                reply = f"You cast **{SPELLS[spell]['name']}**."

        if finish:
            await self._finish(finish[0], finish[1])
        elif publish:
            await self._publish(*publish)
            if arm_round is not None:
                self._arm(self._sudden_timeout(arm_round))
        return reply

    async def _finish(self, winner, loser):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self.cog.release_match(self)
        outcome = self.cog.settle_grand(winner, loser)
        from cogs.store import HOUSES
        if outcome["awarded"]:
            h = HOUSES[outcome["house"]]
            tail = f"+{outcome['awarded']} to {h['emoji']} {h['name']}"
            if outcome.get("night"):
                tail += " (Duel Night!)"
        elif outcome["reason"] == "same-house":
            tail = "Housemates — no points"
        elif outcome["reason"] == "daily-cap":
            tail = f"{winner.display_name} has taken today's Grand points already"
        else:
            tail = "No house to credit"
        async with self.lock:
            snap = self._snapshot(
                view=None, footer=f"{winner.display_name} wins the Grand Duel • {tail}")
        await self._publish(*snap)
        notes = outcome.get("notes") or []
        if notes and self.channel is not None:
            try:
                await self.channel.send("\n".join(notes))
            except discord.DiscordException:
                log.exception("Could not post Grand Duel announcements.")


class GrandAcceptView(discord.ui.View):
    def __init__(self, gd: GrandDuel):
        super().__init__(timeout=ACCEPT_TIMEOUT + 5)
        self.gd = gd

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.gd
        if interaction.user.id != d.b.id:
            await interaction.response.send_message("This challenge isn't yours to answer.",
                                                    ephemeral=True)
            return
        async with d.lock:
            if d.state != "pending":
                await interaction.response.send_message("Too late for that.", ephemeral=True)
                return
            d.state = "locking"
        await interaction.response.defer()
        await d.begin_locking()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.gd
        if interaction.user.id not in (d.a.id, d.b.id):
            await interaction.response.send_message("This isn't your duel.", ephemeral=True)
            return
        async with d.lock:
            if d.state != "pending":
                await interaction.response.send_message("Too late for that.", ephemeral=True)
                return
            d.cog.release_match(d)
        who = "withdraws" if interaction.user.id == d.a.id else "declines"
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(description=f"{interaction.user.display_name} {who}. "
                                            "No Grand Duel today.", color=0x7A7A7A),
            view=None,
        )


class GrandLockPromptView(discord.ui.View):
    def __init__(self, gd: GrandDuel):
        super().__init__(timeout=GRAND_LOCK_TIMEOUT + 5)
        self.gd = gd

    @discord.ui.button(
        label="Open my sequence board",
        style=discord.ButtonStyle.primary,
        emoji="📜",
    )
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.gd
        if interaction.user.id not in d.sequences:
            await interaction.response.send_message("You're watching, not duelling.", ephemeral=True)
            return
        if interaction.user.id in d.locked:
            await interaction.response.send_message("You've already locked your sequence.", ephemeral=True)
            return
        if d.state != "locking":
            await interaction.response.send_message("Locking is closed.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Build your {GRAND_SLOTS}-spell sequence. Only you can see this.\n"
            f"**Slots filled: 0/{GRAND_SLOTS}**",
            view=GrandSequenceView(d, interaction.user.id),
            ephemeral=True,
        )


class GrandSequenceView(discord.ui.View):
    def __init__(self, gd: GrandDuel, user_id: int):
        super().__init__(timeout=ACCEPT_TIMEOUT)
        self.gd = gd
        self.user_id = user_id
        self.seq: list[str] = []
        for key, spell in SPELLS.items():
            self.add_item(GrandSeqSpellButton(key, spell))
        self.add_item(GrandSeqUndoButton())
        self.add_item(GrandSeqSubmitButton())

    def status_text(self) -> str:
        filled = " → ".join(SPELLS[s]["emoji"] + SPELLS[s]["name"] for s in self.seq) or "—"
        return (f"Build your {GRAND_SLOTS}-spell sequence. Only you can see this.\n"
                f"**Slots filled: {len(self.seq)}/{GRAND_SLOTS}**\n{filled}")


class GrandSeqSpellButton(discord.ui.Button):
    def __init__(self, key: str, spell: dict):
        super().__init__(label=spell["name"], emoji=spell["emoji"],
                         style=discord.ButtonStyle.secondary, row=0 if key in ("hex", "ward", "disarm") else 1)
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        v: GrandSequenceView = self.view
        if interaction.user.id != v.user_id:
            await interaction.response.send_message("Not your sequence.", ephemeral=True)
            return
        if len(v.seq) >= GRAND_SLOTS:
            await interaction.response.send_message("Sequence full — hit Submit.", ephemeral=True)
            return
        v.seq.append(self.key)
        await interaction.response.edit_message(content=v.status_text(), view=v)


class GrandSeqUndoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Undo", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        v: GrandSequenceView = self.view
        if interaction.user.id != v.user_id:
            await interaction.response.send_message("Not your sequence.", ephemeral=True)
            return
        if v.seq:
            v.seq.pop()
        await interaction.response.edit_message(content=v.status_text(), view=v)


class GrandSeqSubmitButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Submit sequence", style=discord.ButtonStyle.success, row=2)

    async def callback(self, interaction: discord.Interaction):
        v: GrandSequenceView = self.view
        if interaction.user.id != v.user_id:
            await interaction.response.send_message("Not your sequence.", ephemeral=True)
            return
        if len(v.seq) != GRAND_SLOTS:
            await interaction.response.send_message(
                f"Need {GRAND_SLOTS} spells — you have {len(v.seq)}.", ephemeral=True)
            return
        result = v.gd.try_lock(v.user_id, v.seq)
        if result != "locked":
            await interaction.response.send_message(result, ephemeral=True)
            return
        await interaction.response.edit_message(
            content="Sequence locked. Waiting on your opponent…", view=None)
        await v.gd.maybe_start_play()


class OpenGrandSuddenBoardView(discord.ui.View):
    def __init__(self, gd: GrandDuel):
        super().__init__(timeout=ROUND_TIMEOUT * 6 + 60)
        self.gd = gd

    @discord.ui.button(
        label="Open my cast board",
        style=discord.ButtonStyle.danger,
        emoji="\U0001FA84",
    )
    async def open_board(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.gd.open_sudden_board(interaction)


class GrandSuddenPrivateSpellView(discord.ui.View):
    def __init__(self, gd: GrandDuel):
        super().__init__(timeout=ROUND_TIMEOUT + 5)
        self.gd = gd
        for i, (key, spell) in enumerate(SPELLS.items()):
            self.add_item(GrandSuddenSpellButton(key, spell, row=0 if i < 3 else 1))


class GrandSuddenSpellButton(discord.ui.Button):
    def __init__(self, key: str, spell: dict, row: int = 0):
        super().__init__(
            label=spell["name"],
            emoji=spell["emoji"],
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        d = self.view.gd
        if interaction.user.id not in d.score:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        await interaction.response.defer()
        before = d.board_gen
        reply = await d.sudden_cast(interaction.user, self.key)
        if d.board_gen == before:
            try:
                await interaction.followup.send(reply, ephemeral=True)
            except discord.DiscordException:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Duels(bot))
