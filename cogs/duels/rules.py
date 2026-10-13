"""Duel rules, spell resolution, favoring, and rank tables."""

from pathlib import Path
import datetime as dt
import logging
import os
import random
import time
from discord import app_commands
from cogs.config import (
    COMMUNITY_PING_ROLE_NAMES,
    DUEL_BOUNTY_POINTS,
    DUEL_CHAMPION_ROLE_NAME_DEFAULT,
    DUEL_GIFT_POINTS,
    DUEL_GRAND_REWARDED_PER_DAY,
    DUEL_GRAND_REWARD_POINTS,
    DUEL_REWARDED_PER_DAY,
    DUEL_REWARD_POINTS,
    DUEL_RIVAL_BONUS,
    DUEL_TRIO_HOUSE_COMPLETION_POINTS,
    DUEL_TRIO_HOUSE_WIN_GOAL,
    DUEL_TRIO_REWARD_POINTS,
    DUEL_WEEKLY_POINTS,
    ROOT_DIR,
)

log = logging.getLogger("velmora.duels")

DATA_DIR = ROOT_DIR / "data"
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
REWARD_POINTS = DUEL_REWARD_POINTS
REWARDED_PER_DAY = DUEL_REWARDED_PER_DAY
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


def apply_liquid_luck_tie(
    result: int,
    line: str,
    a_id: int,
    b_id: int,
    bot,
    luck_used: dict,
) -> tuple[int, str]:
    """Felix Felicis: each drinker wins one spell tie per match."""
    if result != 0 or bot is None:
        return result, line
    potions = bot.get_cog("Potions")
    if not potions:
        return result, line
    a_ok = potions.has_liquid_luck(a_id) and not luck_used.get(a_id)
    b_ok = potions.has_liquid_luck(b_id) and not luck_used.get(b_id)
    if a_ok and not b_ok:
        luck_used[a_id] = True
        return 1, f"{line} 🥇 Liquid Luck tips the scales!"
    if b_ok and not a_ok:
        luck_used[b_id] = True
        return 2, f"{line} 🥇 Liquid Luck tips the scales!"
    return result, line


def apply_tie_breakers(
    result: int,
    line: str,
    a_id: int,
    b_id: int,
    bot,
    luck_used: dict,
) -> tuple[int, str]:
    result, line = apply_deadlock_keep(result, line, a_id, b_id, bot)
    return apply_liquid_luck_tie(result, line, a_id, b_id, bot, luck_used)


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
BOUNTY_POINTS = DUEL_BOUNTY_POINTS  # paid to whoever breaks it (outside the daily cap)

RIVAL_AFTER = 5              # duels between the same two people (point bonus threshold)
RIVAL_BONUS = DUEL_RIVAL_BONUS  # extra point for beating your rival (uses a cap slot)
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
# House-level daily set: 5 trio wins for that house → +5 house points once.
TRIO_HOUSE_WIN_GOAL = DUEL_TRIO_HOUSE_WIN_GOAL
TRIO_HOUSE_COMPLETION_POINTS = DUEL_TRIO_HOUSE_COMPLETION_POINTS
TRIO_REWARD_POINTS = DUEL_TRIO_REWARD_POINTS  # legacy alias
TRIO_REWARDED_PER_DAY = TRIO_HOUSE_WIN_GOAL  # legacy alias / checklist
TRIO_TITLE_TIERS = [         # (trio wins, title) - highest first
    (50, "Three's Company"),
    (30, "Pack Hunter"),
    (15, "Triangle Terror"),
    (5, "Triad Novice"),
]

GRAND_MIN_WINS = 50          # 1v1 wins required on both sides
GRAND_SLOTS = 10
GRAND_TO_WIN = 6             # first to 6 of 10 (or sudden death on any tie after 10)
GRAND_REWARD_POINTS = DUEL_GRAND_REWARD_POINTS
GRAND_REWARDED_PER_DAY = DUEL_GRAND_REWARDED_PER_DAY
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
GIFT_POINTS = DUEL_GIFT_POINTS

WEEKLY_POINTS = DUEL_WEEKLY_POINTS  # Duelist of the Week's house bonus
CHAMPION_ROLE_NAME = os.getenv("DUEL_CHAMPION_ROLE_NAME", DUEL_CHAMPION_ROLE_NAME_DEFAULT)
# Staff pick 30 or 60 minutes when starting; auto-ends at ends_at.
DUEL_NIGHT_PRESETS = {
    30: "30 minutes",
    60: "1 hour",
}
DUEL_NIGHT_COLOR = 0xB8434F
DUEL_NIGHT_WARN_MINUTES = 5
DUEL_NIGHT_SCHEDULE_MAX = 3
# Same role names as Attack warn/start unless overridden.
_DEFAULT_NIGHT_PING_NAMES = COMMUNITY_PING_ROLE_NAMES
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
