"""Wild Threat creatures, spawn tuning, and Attack award math."""

from pathlib import Path
import datetime as dt
import logging
import os
import time
from discord import app_commands
from cogs.config import (
    COMMUNITY_PING_ROLE_NAMES,
    EXTRA_THREAT_CHANNEL_ID,
    HEADMASTER_USER_ID_DEFAULT,
    ROOT_DIR,
    THREAT_EVENT_HOUSE_TURNOUT_BONUS,
    THREAT_EVENT_KILL_POOL,
    THREAT_EVENT_KILL_REP_TIE_BONUS,
    THREAT_EVENT_MVP_BONUS,
    THREAT_EVENT_PRESETS,
    THREAT_REWARD_POINTS,
)

try:
    from zoneinfo import ZoneInfo
    CHICAGO = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover - Windows without tzdata
    CHICAGO = dt.timezone.utc

from cogs.velmora_channels import (
    EVENT_ANNOUNCE_CHANNEL_ID,
    EVERYTHING_LOUNGE_CHANNEL_IDS,
    RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS,
    SHARED_GAMES_CHANNEL_ID,
    STUDY_HALL_CHANNEL_ID,
    SWARM_PRACTICE_CHANNEL_IDS,
)
from cogs.world_engine import today

# Summon/practice rooms — Attack waves never flood these (except everything
# lounges, which opt into full threat traffic below).
PRACTICE_SUMMON_CHANNEL_IDS = frozenset({
    STUDY_HALL_CHANNEL_ID,
    SHARED_GAMES_CHANNEL_ID,
    *SWARM_PRACTICE_CHANNEL_IDS,
    *EVERYTHING_LOUNGE_CHANNEL_IDS,
})

# Practice rooms + restricted open lounges: no ambient / Attack floods.
# Everything lounges stay open to threats on purpose.
NO_THREAT_SPAWN_CHANNEL_IDS = frozenset({
    *(PRACTICE_SUMMON_CHANNEL_IDS - EVERYTHING_LOUNGE_CHANNEL_IDS),
    *RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS,
})

# Always included in ambient spawns + Attack waves (alongside staff channels).
EXTRA_THREAT_CHANNEL_IDS = frozenset({EXTRA_THREAT_CHANNEL_ID})

# Restricted open lounges stay spawn-free even for house practice.
# Study hall / games lounge / swarm practice / everything lounges may host
# practice swarms on purpose.
PRACTICE_BLOCKED_CHANNEL_IDS = frozenset({
    *RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS,
})

log = logging.getLogger("velmora.dementors")

DATA_DIR = ROOT_DIR / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "dementors.json"
ASSETS_DIR = ROOT_DIR / "wild_threat_art_assets"

SPAWNS_PER_DAY = 2
TICK_MINUTES = 5
TICKS_PER_DAY = (24 * 60) // TICK_MINUTES
SPAWN_CHANCE = SPAWNS_PER_DAY / TICKS_PER_DAY   # per tick, while under the daily cap

WANDER_MINUTES = 10   # how long a creature that can wander waits before it bolts
FINDER_EXCLUSIVE_SECONDS = 25  # explorer who stirred one up gets first crack

REWARD_POINTS = THREAT_REWARD_POINTS  # kept for backward compatibility; see CREATURES["dementor"]["points"]
DARK = 0x0B0B12
THREAT_ALERT_COLOR = 0xC0392B  # every "X has appeared" alert, regardless of creature, so it never blends in

EVENT_WAVE_SECONDS = 23
EVENT_DEFAULT_MINUTES = 5
# House practice can override length (not locked to Attack's 5/10 presets).
# Cap high enough for multi-hour drills; Attack still uses EVENT_PRESETS only.
EVENT_MAX_MINUTES = 240
EVENT_MVP_BONUS = THREAT_EVENT_MVP_BONUS
# When two+ wizards share both the top kill count and the same top rep.
EVENT_KILL_REP_TIE_BONUS = THREAT_EVENT_KILL_REP_TIE_BONUS
EVENT_KILL_POOL = THREAT_EVENT_KILL_POOL  # legacy base; presets set kill_pool explicitly
EVENT_HOUSE_TURNOUT_BONUS = THREAT_EVENT_HOUSE_TURNOUT_BONUS  # house with the most fighters who registered a kill
# length minutes → kill-share cut size and absolute pool
EVENT_PRESETS = THREAT_EVENT_PRESETS
EVENT_COLOR = 0x8A2F2F
EVENT_WARN_MINUTES = 5
ATTACK_SCHEDULE_MAX_DAYS = 3
# Role names pinged on warn + start (case-insensitive). Override with env:
# ATTACK_PING_ROLE_NAMES=Champions,WIZARDS AND WITCHES
_DEFAULT_PING_NAMES = COMMUNITY_PING_ROLE_NAMES
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
ATTACK_LENGTH_CHOICES = [
    app_commands.Choice(name="5 minutes — top 7 share 100", value=5),
    app_commands.Choice(name="10 minutes — top 10 share 300", value=10),
]


def _preset_kill_pool(preset: dict) -> int:
    if "kill_pool" in preset:
        return int(preset["kill_pool"])
    # Older in-memory shape used point_mult × EVENT_KILL_POOL.
    return int(EVENT_KILL_POOL * int(preset.get("point_mult") or 1))


def _event_kill_pool(event: dict) -> int:
    if event.get("kill_pool") is not None:
        return int(event["kill_pool"])
    return int(EVENT_KILL_POOL * int(event.get("point_mult") or 1))


def _chicago_now(ts: float | None = None) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts if ts is not None else time.time(), CHICAGO)


def _attack_ping_role_names() -> list[str]:
    raw = os.getenv("ATTACK_PING_ROLE_NAMES", "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return list(_DEFAULT_PING_NAMES)


def kill_share_awards(
    by_kills: list[dict],
    *,
    pool: int,
    top_n: int,
    total_kills: int,
) -> dict[int, int]:
    """Top ``top_n`` by kills: floor((kills / total_swarm_kills) × pool).

    Only the cut is paid; leftover from rounding or from kills outside the
    top N stays unspent.
    """
    if pool <= 0 or total_kills <= 0 or top_n <= 0:
        return {}
    out: dict[int, int] = {}
    for r in by_kills[:top_n]:
        kills = int(r.get("kills") or 0)
        if kills <= 0:
            continue
        pts = int(pool * kills / total_kills)  # truncate like 17.5 → 17
        if pts > 0:
            out[int(r["uid"])] = pts
    return out

ATTACK_INTRO = [
    "Something has broken through the wards. Fight back - every monster you put down counts "
    "toward the tally. Nobody's told which spell beats which. Work it out, or find someone who knows.",
    "The castle's defenses are down, all at once, everywhere. Hold the line - Velmora will "
    "remember who answered the call, and by how much.",
]

ARRIVALS = [
    "The room goes cold. Every candle nearby gutters at once, and something that isn't "
    "quite shadow settles into the corner.",
    "Somewhere in the room, joy just left. It's hard to say exactly where. It doesn't matter - "
    "it's spreading.",
    "The temperature drops so fast someone's drink frosts over. A Dementor has found its way in.",
    "You didn't hear it arrive. You never do. But the laughter in the room just stopped, all at once.",
]

BANISHED = [
    "{patronus} bursts from {member}'s wand in a wash of silver light, and the Dementor flees "
    "before it, shrieking, back into whatever brought it here.",
    "A silver {animal} charges out of nowhere at {member}'s command. The Dementor doesn't even "
    "try to fight it - it's already gone.",
    "{member} raises their wand, and {patronus} drives the cold out of the room like it was "
    "never there at all.",
]

# ---------------------------------------------------------------- creatures
#
# "weak": the /cast spell value that actually works. "patronus" is handled
#   by the original dementor path below (it needs an already-cast patronus,
#   not just the right pick). Everything else uses the five duel spells
#   from cogs/duels.py: hex, ward, disarm, bind, mirror.
# "pack": how many DIFFERENT people need to land that spell before it's
#   actually gone. 1 means one clean hit does it.
# "points": awarded to the house of EVERY contributor once it's defeated
#   (not split - a pack of 2 means two people each get the full amount).
#   During an event, this same number is what's earned as personal rep
#   instead, and only gets converted to house points at the very end.
# "wander": if true, an uncaught one relocates to another configured
#   channel after WANDER_MINUTES instead of waiting to be found. (Outside
#   an event only - during an event, waves already replace it every 15s.)

CREATURES = {
    "dementor": {
        "name": "Dementor", "emoji": "🖤", "color": DARK, "weak": "patronus",
        "pack": 1, "points": 3, "wander": False,
        "image": "Dementor.png",
        "defeat_image": "Dementor_Defeat.png",
        "arrivals": ARRIVALS,
    },
    "shadow_wolf": {
        "name": "Shadow Wolf", "emoji": "🐺", "color": 0x2B2B33, "weak": "hex",
        "pack": 1, "points": 3, "wander": False,
        "image": "Shadow_Wolf.png",
        "defeat_image": "Shadow_Wolf_Defeat.png",
        "arrivals": [
            "Something low and fast slips between people's feet, all teeth and no sound.",
            "A shadow peels off the wall and starts circling. It hasn't decided who yet.",
            "You hear it before you see it - claws on stone, moving quick.",
        ],
        "victory": [
            "{member}'s spell catches it mid-lunge. It yelps once and is gone.",
            "{member} doesn't wait for it to strike first. One clean hit and it scatters into smoke.",
        ],
        "progress": [],  # pack 1: never shown, resolves in one hit
    },
    "wisp_swarm": {
        "name": "Wisp Swarm", "emoji": "🌫️", "color": 0x9AA7B0, "weak": "mirror",
        "pack": 1, "points": 3, "wander": False,
        "image": "Wisp_Swarm.png",
        "defeat_image": "Wisp_Swarm_Defeat.png",
        "arrivals": [
            "A cluster of pale lights drifts in through the window, humming faintly.",
            "The air fills with soft floating wisps. They don't seem hostile. They also don't leave.",
            "Little cold lights blink into being, one by one, until there are far too many.",
        ],
        "victory": [
            "{member}'s spell throws every wisp's own glow straight back at it, and the whole swarm winks out at once.",
            "{member} turns the swarm's light against itself. It scatters like blown-out candles.",
        ],
        "progress": [],
    },
    "storm_sprite": {
        "name": "Storm Sprite", "emoji": "⚡", "color": 0x4B6EF5, "weak": "ward",
        "pack": 1, "points": 5, "wander": True,
        "image": "Storm_Sprite.png",
        "defeat_image": "Storm_Sprite_Defeat.png",
        "arrivals": [
            "A crackle of static runs along the ceiling and drops, grinning, into the room.",
            "The lights flicker once. Something small, bright, and extremely pleased with itself has arrived.",
        ],
        "victory": [
            "{member}'s ward holds against it, and for once it can't wriggle free. Caught.",
            "{member} boxes it in before it can bolt again. It sparks once, sulking, and fades.",
        ],
        "wander_lines": [
            "It's gone before anyone gets close - and turned up somewhere else entirely.",
            "It bolts through the wall the second it feels a spell coming. It'll be back. Elsewhere.",
        ],
        "progress": [],
    },
    "stone_golem": {
        "name": "Stone Golem", "emoji": "🪨", "color": 0x6E6455, "weak": "bind",
        "pack": 2, "points": 4, "wander": False,
        "image": "Stone_Golem.png",
        "defeat_image": "Stone_Golem_Defeat.png",
        "arrivals": [
            "Something heavy grinds to a halt in the doorway. It's not going anywhere on its own.",
            "A shape built out of loose stone hauls itself upright and just... stands there. Ominously.",
        ],
        "victory": [
            "{member} finishes what {other} started, and the golem folds back into a pile of ordinary rock.",
        ],
        "progress": [
            "{member}'s binding holds - the golem staggers, but it's still standing. It'll take someone else, too.",
        ],
    },
    "nightweaver": {
        "name": "Nightweaver", "emoji": "🕷️", "color": 0x2E1A33, "weak": "disarm",
        "pack": 2, "points": 4, "wander": False,
        "image": "Nightweaver.png",
        "defeat_image": "Nightweaver_Defeat.png",
        "arrivals": [
            "Thread-thin shadows stitch themselves across the ceiling, and something with too many legs waits at the center.",
            "A web that wasn't there a moment ago now stretches corner to corner. Something's home.",
        ],
        "victory": [
            "{member} strips away the last of its silk after {other} tore the first of it loose. It drops, and vanishes.",
        ],
        "progress": [
            "{member} tears its silk loose, but it spins more before anyone else can finish it.",
        ],
    },
}

SPAWN_WEIGHTS = {
    "dementor": 4, "shadow_wolf": 3, "wisp_swarm": 3,
    "stone_golem": 2, "nightweaver": 2, "storm_sprite": 1,
}

_DUEL_SPELL_NAMES = {"hex": "Hex", "ward": "Ward", "disarm": "Disarm", "bind": "Bind", "mirror": "Mirror"}
SPELL_ORDER = ("patronus", "hex", "ward", "disarm", "bind", "mirror")
SPELL_LABEL = {"patronus": "Patronus", **_DUEL_SPELL_NAMES}
CREATURE_CHOICES = [app_commands.Choice(name=c["name"], value=key) for key, c in CREATURES.items()]

# Headmaster convenience: /cast auto-picks the right weakness (no buttons).
# Everyone else gets ephemeral spell buttons. Override with CAST_AUTO_USER_ID;
# unset/0 disables.
_raw_cast_auto = os.getenv("CAST_AUTO_USER_ID", HEADMASTER_USER_ID_DEFAULT)
CAST_AUTO_USER_ID = (
    int(_raw_cast_auto) if _raw_cast_auto and str(_raw_cast_auto).isdigit() else None
)
