"""Descent elements, floors, drops, and combat math."""

from pathlib import Path
import logging
import os
import random
import time
import discord
from cogs.config import (
    DESCENT_HOME_CHANNEL_IDS,
    HEADMASTER_USER_ID_DEFAULT,
    ROOT_DIR,
)
from cogs.velmora_channels import channel_mentions, with_study_hall

log = logging.getLogger("velmora.descent")

DATA_DIR = ROOT_DIR / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "descent_state.json"
CLAIMS_PATH = STATE_DIR / "descent_restore_claims.json"
ASSETS_DIR = ROOT_DIR / "monster_art_assets"
WILD_THREAT_ASSETS = ROOT_DIR / "wild_threat_art_assets"
SHADOW_IMAGE = "Shadow_Wolf.png"

# The Descent only runs in these channels - keeps the fight embeds and
# spam out of every other channel in the server. Study hall included for
# new-student practice.
DESCENT_CHANNEL_IDS = with_study_hall(*DESCENT_HOME_CHANNEL_IDS)


def _descent_channel_hint() -> str:
    return f"The Descent can only be played in {channel_mentions(DESCENT_CHANNEL_IDS)}."


# Headmaster convenience: a One-shot button on THEIR Descent fights only
# (still grants full win rewards). Override with DESCENT_ONESHOT_USER_ID;
# unset/0 disables. Same default id as CAST_AUTO_USER_ID.
_raw_oneshot = os.getenv("DESCENT_ONESHOT_USER_ID", HEADMASTER_USER_ID_DEFAULT)
DESCENT_ONESHOT_USER_ID = (
    int(_raw_oneshot) if _raw_oneshot and str(_raw_oneshot).isdigit() else None
)

# ------------------------------------------------------------- elements

ELEMENTS = ["fire", "ice", "lightning", "poison", "light"]
ELEMENT_EMOJI = {"fire": "🔥", "ice": "❄️", "lightning": "⚡", "poison": "☠️", "light": "✨"}
ELEMENT_NAME = {"fire": "Fire", "ice": "Ice", "lightning": "Lightning", "poison": "Poison", "light": "Light"}
# the attack spell each element is cast as - shown on the buttons and in
# the round log, with the element emoji doing the job of telling players
# which element it actually is
SPELL_NAME = {"fire": "Incendio", "ice": "Glacius", "lightning": "Fulgur",
              "poison": "Draught", "light": "Lumos Solem"}
# what a monster of this element is weak to (takes double damage from) -
# never shown to the player; the point is to learn it by fighting
WEAK_TO = {"poison": "light", "fire": "ice", "ice": "lightning", "lightning": "poison", "light": "fire"}

# a floor's secondary element - about 30% of its non-boss monsters are
# drawn from this instead of the zone's primary element, so you can't
# lean on one "safe" spell for the whole floor
SECONDARY_ELEMENT = {"poison": "fire", "fire": "ice", "ice": "lightning", "lightning": "light", "light": "poison"}
SECONDARY_CHANCE = 0.30

# ---------------------------------------------------------------- zones

ZONES = [
    {"name": "The Overgrown Entrance", "element": "poison", "start": 1, "end": 10},
    {"name": "The Ember Vaults", "element": "fire", "start": 11, "end": 20},
    {"name": "The Frozen Depths", "element": "ice", "start": 21, "end": 30},
    {"name": "The Storm Cistern", "element": "lightning", "start": 31, "end": 40},
    {"name": "The Hollow Sanctum", "element": "light", "start": 41, "end": 50},
    {"name": "The Overgrown Entrance — Lower Reach", "element": "poison", "start": 51, "end": 60},
    {"name": "The Ember Vaults — Deep Forge", "element": "fire", "start": 61, "end": 70},
    {"name": "The Frozen Depths — Abyssal Ice", "element": "ice", "start": 71, "end": 80},
    {"name": "The Storm Cistern — Undertow", "element": "lightning", "start": 81, "end": 90},
    {"name": "The Hollow Sanctum — Last Vault", "element": "light", "start": 91, "end": 100},
]


def zone_for(floor: int) -> dict:
    for z in ZONES:
        if z["start"] <= floor <= z["end"]:
            return z
    return ZONES[-1]


def zone_index(floor: int) -> int:
    """0-based index of which 10-floor zone this floor falls in."""
    return (max(1, floor) - 1) // 10


# ------------------------------------------------------------- monsters

# (name, emoji, art-kind) - art-kind is unused now that every monster has
# real art (kept for logging/back-compat only)
MONSTER_NAMES = {
    "poison": [("Bloatcap Crawler", "🍄", "blob"), ("Weeping Adder", "🐍", "serpent"),
              ("Fen Wretch", "🧟", "humanoid")],
    "fire": [("Ember Hound", "🐕", "quadruped"), ("Cinder Wisp", "🔥", "orb"),
            ("Forge Golem", "🗿", "humanoid")],
    "ice": [("Frostbite Wraith", "👻", "humanoid"), ("Glacier Stalker", "🐺", "quadruped"),
           ("Rime Widow", "🕷️", "blob")],
    "lightning": [("Static Hollow", "⚡", "orb"), ("Storm-Touched Raven", "🐦‍⬛", "flier"),
                 ("Volt Serpent", "🐉", "serpent")],
    "light": [("Hollow Choirling", "🕊️", "flier"), ("Radiant Husk", "💀", "humanoid"),
             ("Vault Warden", "🛡️", "humanoid")],
}

BOSS_NAMES = {
    10: ("The Bloated Sovereign", "🍄", "blob"),
    20: ("Cinderlord Ashgrave", "🔥", "humanoid"),
    30: ("The Rime Empress", "❄️", "humanoid"),
    40: ("Stormcaller Vessel", "⚡", "humanoid"),
    50: ("The Hollow Saint", "✨", "humanoid"),
    # Lower Reach+ — unique bosses (not reskins of floors 10–50)
    60: ("Blightmother Veil", "☠️", "humanoid"),
    70: ("Slagheart Tyrant", "🔥", "humanoid"),
    80: ("The Glacier Colossus", "🧊", "humanoid"),
    90: ("The Arc Spire", "⚡", "orb"),
    100: ("The Vault Eternal", "🚪", "humanoid"),
}

# real art, supplied by the user - filename per monster name / boss floor
MONSTER_IMAGE = {
    "Bloatcap Crawler": "Bloatcap_Crawler.png",
    "Weeping Adder": "Weeping_Adder.png",
    "Fen Wretch": "Fen_Wretch.png",
    "Ember Hound": "Ember_Hound.png",
    "Cinder Wisp": "Cinder_Wisp.png",
    "Forge Golem": "Forge_Golem.png",
    "Frostbite Wraith": "Frostbite_Wraith.png",
    "Glacier Stalker": "Glacier_Stalker.png",
    "Rime Widow": "Rime_Widow.png",
    "Static Hollow": "Static_Hollow.png",
    "Storm-Touched Raven": "Storm-Touched_Raven.png",
    "Volt Serpent": "Volt_Serpent.png",
    "Hollow Choirling": "Hollow_Choirling.png",
    "Radiant Husk": "Radiant_Husk.png",
    "Vault Warden": "Vault_Warden.png",
}
# Dramatic /summon flourish lines for each boss trophy, written to that
# boss's own character rather than a shared template - the whole point is
# that these read as a flex, not a reused line everyone else's also has.
BOSS_SUMMONS = {
    10: [
        "{owner} calls, and the ground splits - the Bloated Sovereign rises, dripping poison that curls the grass a full room away.",
        "The air turns thick and sour the instant the Bloated Sovereign answers {owner}'s call. Even the torches seem to lean away from it.",
    ],
    20: [
        "{owner} snaps their fingers and Cinderlord Ashgrave erupts from the floor in a column of fire that scorches the ceiling.",
        "The temperature in the room jumps twenty degrees the instant Cinderlord Ashgrave answers {owner}'s call.",
    ],
    30: [
        "Frost creeps across every surface as the Rime Empress steps out of thin air at {owner}'s call, and everyone's breath turns visible.",
        "The Rime Empress arrives in a hush of falling snow, and bows - only ever - to {owner}.",
    ],
    40: [
        "Lightning forks across the ceiling with no storm behind it - Stormcaller Vessel has answered {owner}, and the lights flicker for a full ten seconds.",
        "The hair on everyone's arms stands up at once. Stormcaller Vessel has arrived, and it only ever comes for {owner}.",
    ],
    50: [
        "A column of pure light drops from nowhere, and the Hollow Saint stands within it, waiting on {owner}'s word.",
        "Every candle in the room burns gold instead of orange the moment the Hollow Saint appears for {owner}.",
    ],
    60: [
        "The veils part before anyone sees her move - Blightmother Veil answers {owner}, and the air fills with the sweet rot of a hundred funeral blooms.",
        "Moss creeps up the walls the instant Blightmother Veil arrives for {owner}. She offers a dripping chalice to no one else.",
    ],
    70: [
        "The floor groans like a furnace door - Slagheart Tyrant settles onto its throne of slag at {owner}'s call, molten heart blazing open.",
        "Heat rolls off Slagheart Tyrant in waves. The anvil in its fist glows white for {owner} alone.",
    ],
    80: [
        "The room drops twenty degrees as The Glacier Colossus answers {owner} - a walking cliff of ancient ice, bones frozen deep inside it.",
        "Snow doesn't fall so much as arrive already settled. The Glacier Colossus has come, and it only ever comes for {owner}.",
    ],
    90: [
        "A needle of black crystal punches up through the floor - The Arc Spire answers {owner}, its single storm-eye already crackling.",
        "Every metal thing in the room hums. The Arc Spire has arrived for {owner}, lightning crowning it like a living antenna.",
    ],
    100: [
        "The last lock at the bottom of the Descent wakes for {owner} alone. The Vault Eternal's doors part a finger's width - light pours out, and the whole room feels judged.",
        "Key-sigils orbit in silence as The Vault Eternal answers {owner}. Not a saint. Not a monster. The door itself.",
    ],
}

BOSS_IMAGE = {
    10: "Floor_10_The_Bloated_Sovereign.png",
    20: "Floor_20_Cinderlord_Ashgrave.png",
    30: "Floor_30_The_Rime_Empress.png",
    40: "Floor_40_Stormcaller_Vessel.png",
    50: "Floor_50_The_Hollow_Saint.png",
    60: "Floor_60_Blightmother_Veil.png",
    70: "Floor_70_Slagheart_Tyrant.png",
    80: "Floor_80_The_Glacier_Colossus.png",
    90: "Floor_90_The_Arc_Spire.png",
    100: "Floor_100_The_Vault_Eternal.png",
}

# material each zone element drops, and the one-off boss drop
ZONE_ITEM = {
    "poison": "descent_poison_ichor",
    "fire": "descent_ember_shard",
    "ice": "descent_frost_core",
    "lightning": "descent_storm_relic",
    "light": "descent_light_dust",
}
BOSS_ITEM = "descent_sigil"

MONSTER_DROP_CHANCE = 0.40   # any regular win
FLOOR_CLEAR_GUARANTEED = 3   # material given on a full floor clear

# Spireheart Necklace (floor-90 boss earn): free Descent stat picks while worn.
SPIREHEART_PERK = "spireheart"
SPIREHEART_STAT_FLOOR = 45
SPIREHEART_STAT_CHANCE = 0.20

# Practice clear: chance of a free stat pick scales with how far the
# practiced floor sits behind your current Descent floor.
PRACTICE_STATUP_CLOSE = 0.20   # distance ≤ 5
PRACTICE_STATUP_MID = 0.10     # distance 6–20
PRACTICE_STATUP_FAR = 0.05     # distance 21+

# Late-game Fury bosses (every swing is a multi-hit burst)
FURY_BOSS_FLOORS = frozenset({60, 70, 80, 90, 100})
FURY_START_FLOOR = 51          # regular monsters: alternating Fury from here
FURY_REGULAR_HITS = (2, 4)     # inclusive randint range for regular Fury
FURY_BOSS_HITS = (3, 6)        # inclusive randint range for Fury-boss swings

# Floors 90–100: shadow-veiled monsters, every swing is a full Fury.
ENDGAME_FLOOR_START = 90
ENDGAME_MIN_HP = 8001
ENDGAME_F100_BOSS_HP = 20000
ENDGAME_FURY_HITS = 6
ENDGAME_F100_BOSS_FURY = 10
SHADOW_NAME = "Shadow Remnant"
SHADOW_EMOJI = "🌑"

# Floors 95–100: hard HP pin, outgoing damage spike, and second-wind heals.
RAGE_FLOOR_START = 95
RAGE_PIN_HP = 13000
RAGE_DMG_MULT = 3.0
VAULT_ETERNAL_DMG_MULT = 5.0
SECOND_WIND_PCT = 0.20
RAGE_SECOND_WIND_HEALS = 1
VAULT_ETERNAL_SECOND_WIND_HEALS = 2

# ----------------------------------------------------------- difficulty
#
# Floors 1–50 use doubled growth vs the original curve. Floors 51–100 keep
# that same linear formula evaluated at the floor, then apply a late-game
# ramp so depth feels punishing without a hard wall. Boss multipliers are
# applied after the curve (unchanged). Floors 90–100 then enforce a hard
# HP floor (95–100 pin to 13k; the floor-100 boss stays at 20k).

def monster_stats(floor: int, is_boss: bool) -> tuple[int, int, int]:
    # Doubled growth baseline (floors 1–50, and the pre-ramp term for 51+)
    hp = 24 + floor * 22
    atk = 5 + floor * 3.4
    df = 2 + floor * 2.2
    if floor > 50:
        ramp = 1 + 1.5 * (floor - 50) / 50
        hp *= ramp
        atk *= ramp
        df *= ramp
    if is_boss:
        hp *= 2.0
        atk *= 1.3
        df *= 1.2
    hp, atk, df = round(hp), round(atk), round(df)
    if is_boss and floor == 100:
        hp = ENDGAME_F100_BOSS_HP
    elif floor >= RAGE_FLOOR_START:
        hp = RAGE_PIN_HP
    elif floor >= ENDGAME_FLOOR_START:
        hp = max(ENDGAME_MIN_HP, hp)
    return hp, atk, df


BASE_HP, BASE_ATK, BASE_DEF = 55, 8, 3
HP_PER_POINT, ATK_PER_POINT, DEF_PER_POINT = 12, 2, 1

DEF_MITIGATION_K = 16   # defense this high cuts incoming damage roughly in half
DAMAGE_VARIANCE = 0.30  # each hit rolls ±30%, so no fight is fully predictable


def mitigate(attack: int, multiplier: float, defense: int) -> int:
    """Percentage-based damage: defense reduces damage proportionally
    (never fully zeroes it), and every hit has real variance - so being
    behind on stats lowers your odds without making a fight literally
    unwinnable."""
    reduction = defense / (defense + DEF_MITIGATION_K)
    raw = attack * multiplier * (1 - reduction)
    return max(1, round(raw * random.uniform(1 - DAMAGE_VARIANCE, 1 + DAMAGE_VARIANCE)))


MAX_LOSSES = 3
LOCKOUT_SECONDS = 24 * 3600
# No button press for this long → fight ends as a loss (practice: no penalty).
IDLE_TIMEOUT = 3 * 60
STATUP_AT_MONSTER = 5
MONSTERS_PER_FLOOR = 10
MAX_FLOOR = 100

# ---------------------------------------------------------------------- AP

STARTING_MAX_AP = 5
# +1 Max AP at each decade clear (10, 20, … 100) → at most +10 over a full run.
AP_PER_TEN_FLOORS = 10


def max_ap_for_depth(depth: int) -> int:
    """Max AP after having cleared `depth` floors ( +1 per 10 floors )."""
    depth = max(0, min(MAX_FLOOR, int(depth)))
    return STARTING_MAX_AP + depth // AP_PER_TEN_FLOORS


AP_REGEN_PER_TURN = 1
CAST_AP_COST = 3
DEFEND_AP_COST = 0
HEAL_AP_COST = 3
STRIKE_MULT = 0.5    # a free hit always lands at "resisted"-tier damage
DEFEND_DMG_MULT = 0.5   # incoming damage while defending
REST_DMG_MULT = 1.10    # incoming damage while resting (unguarded)
BASELINE_DMG_MULT = 1.0
HEAL_FRACTION = 0.5      # fraction of missing HP a heal restores


def player_stats(rec: dict) -> tuple[int, int, int]:
    pts = rec["stat_points"]
    return (BASE_HP + pts["hp"] * HP_PER_POINT,
            BASE_ATK + pts["atk"] * ATK_PER_POINT,
            BASE_DEF + pts["def"] * DEF_PER_POINT)


def combat_to_points(hp: int, attack: int, defense: int) -> dict[str, int]:
    """Convert displayed combat totals (broke report / status) into stat points."""
    return {
        "hp": max(0, (int(hp) - BASE_HP) // HP_PER_POINT),
        "atk": max(0, (int(attack) - BASE_ATK) // ATK_PER_POINT),
        "def": max(0, (int(defense) - BASE_DEF) // DEF_PER_POINT),
    }


def blank_record() -> dict:
    return {
        "floor": 1,
        "monster_index": 1,
        "losses": 0,
        "locked_until": 0.0,
        "highest_cleared": 0,
        "stat_points": {"hp": 0, "atk": 0, "def": 0},
        "pending_statup": 0,
        "max_ap": STARTING_MAX_AP,
        "bosses_bound": [],
        # Every monster beaten (real or practice) — seed for army PVP.
        # Each entry keeps floor + combat stats from the fight.
        "army": [],
        "army_seq": 0,
    }


def recruit_from_fight(rec: dict, fight: "Fight") -> dict:
    """Append one army unit from a won fight. Returns the unit dict."""
    rec.setdefault("army", [])
    seq = int(rec.get("army_seq", 0) or 0) + 1
    rec["army_seq"] = seq
    unit = {
        "id": seq,
        "name": getattr(fight, "true_name", None) or fight.name,
        "emoji": getattr(fight, "true_emoji", None) or fight.emoji,
        "element": fight.element,
        "kind": fight.kind,
        "floor": fight.floor,
        "boss": bool(fight.is_boss),
        "practice": bool(fight.is_practice),
        "hp": int(fight.m_hp_max),
        "atk": int(fight.m_atk),
        "def": int(fight.m_def),
        "at": int(time.time()),
    }
    rec["army"].append(unit)
    return unit


def clone_army_unit(rec: dict, unit: dict) -> dict:
    """Duplicate a bound unit into the army (Fifth Muster bonus)."""
    rec.setdefault("army", [])
    seq = int(rec.get("army_seq", 0) or 0) + 1
    rec["army_seq"] = seq
    clone = dict(unit)
    clone["id"] = seq
    clone["at"] = int(time.time())
    rec["army"].append(clone)
    return clone


def army_floor_counts(army: list) -> dict[int, int]:
    """floor -> how many units bound from that floor."""
    counts: dict[int, int] = {}
    for unit in army:
        fl = int(unit.get("floor") or 0)
        counts[fl] = counts.get(fl, 0) + 1
    return counts


def cleared_depth(rec: dict) -> int:
    """Best known cleared floor: highest_cleared, else one below current floor."""
    highest = int(rec.get("highest_cleared") or 0)
    floor_now = int(rec.get("floor") or 1)
    return max(highest, max(0, floor_now - 1))


def append_army_unit(
    rec: dict,
    *,
    name: str,
    emoji: str,
    element: str,
    kind: str,
    floor: int,
    is_boss: bool,
    m_hp: int,
    m_atk: int,
    m_def: int,
    backfill: bool = False,
) -> dict:
    """Append one army unit. Never clears existing troops."""
    rec.setdefault("army", [])
    seq = int(rec.get("army_seq", 0) or 0) + 1
    rec["army_seq"] = seq
    unit = {
        "id": seq,
        "name": name,
        "emoji": emoji,
        "element": element,
        "kind": kind,
        "floor": int(floor),
        "boss": bool(is_boss),
        "practice": False,
        "hp": int(m_hp),
        "atk": int(m_atk),
        "def": int(m_def),
        "at": int(time.time()),
    }
    if backfill:
        unit["backfill"] = True
    rec["army"].append(unit)
    return unit


def progress_backfill_plan(rec: dict) -> dict:
    """Compute additive boss + army credit from cleared depth (does not mutate).

    For each floor 1..depth, tops army count up to MONSTERS_PER_FLOOR (one
    clear's worth). Existing units are kept — only the deficit is filled.
    Boss trophies: every 10th floor ≤ depth missing from bosses_bound.
    """
    depth = min(MAX_FLOOR, cleared_depth(rec))
    army = list(rec.get("army") or [])
    counts = army_floor_counts(army)
    bound = {int(f) for f in (rec.get("bosses_bound") or [])}

    bosses_to_add = [fl for fl in range(10, depth + 1, 10) if fl not in bound]
    floors_needed: dict[int, int] = {}
    for fl in range(1, depth + 1):
        have = counts.get(fl, 0)
        need = max(0, MONSTERS_PER_FLOOR - have)
        if need:
            floors_needed[fl] = need
    return {
        "depth": depth,
        "bosses_to_add": bosses_to_add,
        "floors_needed": floors_needed,
        "units_to_add": sum(floors_needed.values()),
        "army_before": len(army),
        "bosses_before": len(bound),
    }


def bar(current: int, maximum: int, width: int = 12) -> str:
    maximum = max(maximum, 1)
    filled = max(0, min(width, round(width * current / maximum)))
    return "█" * filled + "░" * (width - filled)


def practice_statup_chance(current_floor: int, practiced_floor: int) -> float:
    """Chance of a free stat pick on a practice clear, by how far behind
    the practiced floor is relative to your current Descent floor."""
    distance = current_floor - practiced_floor
    if distance <= 5:
        return PRACTICE_STATUP_CLOSE
    if distance <= 20:
        return PRACTICE_STATUP_MID
    return PRACTICE_STATUP_FAR


def stamp_owner(embed: discord.Embed, member: discord.Member) -> discord.Embed:
    """Mark whose Descent board/result this is (shared channels get busy).

    Name + avatar only — no @mention, so busy channels don't ping every round.
    """
    icon = None
    try:
        icon = member.display_avatar.url
    except Exception:
        icon = None
    embed.set_author(name=f"{member.display_name}'s Descent", icon_url=icon)
    return embed


def pending_statup_picks(rec: dict) -> int:
    """How many free stat picks the player still owes. Supports legacy
    True/False saves as well as an integer pick count."""
    v = rec.get("pending_statup", 0)
    if v is True:
        return 1
    if v is False or v is None:
        return 0
    return max(0, int(v))


def fury_hit_count(fight: "Fight") -> int:
    """How many separate hit rolls the monster makes this counterattack.

    Floors 90–100: every swing is a full Fury (6 hits; floor-100 boss = 10).
    Floors 1–50: always a single hit. Regular monsters on 51–89: alternate
    Fury (2–4) and single, starting with Fury on the first swing. Fury
    bosses (60/70/80): every swing is Fury (3–6). Earlier bosses
    are unchanged (single hits).
    """
    if fight.floor >= ENDGAME_FLOOR_START:
        if fight.is_boss and fight.floor == 100:
            return ENDGAME_F100_BOSS_FURY
        return ENDGAME_FURY_HITS
    if fight.is_boss and fight.floor in FURY_BOSS_FLOORS:
        return random.randint(*FURY_BOSS_HITS)
    if not fight.is_boss and fight.floor >= FURY_START_FLOOR:
        if fight.fury_next:
            fight.fury_next = False
            return random.randint(*FURY_REGULAR_HITS)
        fight.fury_next = True
        return 1
    return 1
