"""
The Descent - a 100-floor solo dungeon crawl.

    /descend             - fight the next monster on your current Descent floor
    /descend auto:True   - keep posting the next monster after each win
    /descend floor:<n>   - replay a floor you've already cleared, for practice/loot
    /descentstatus        - your floor, stats, AP, and lockout status
    /staff descent unlock - clear the 3-loss lockout without wiping progress

Ten zones of ten floors each, one primary element per zone plus a second
element mixed in (about 30% of non-boss monsters), so a floor is never a
single-element gimme. Every floor is a gauntlet of 10 monsters fought in
order:

    - Win all 10 -> advance a floor and level up (level = deepest floor
      cleared): a stat point (HP/Attack/Defense) at monster #5, and your
      max AP goes up by 1 every time you clear a new floor.
    - Lose a fight -> refight that same monster; you don't lose progress
      on the floor for a single loss.
    - Lose 3 times on the same floor -> locked out of it for 24 hours.
      When the lockout clears you restart that floor at monster #1.
    - Every 10th floor (10, 20, ... 100) ends in a boss: tougher, weak to
      two elements instead of one, and worth a floor-clear bonus.

Combat runs on an AP (action point) economy, not just "pick a spell every
round": you start each fight with your current max AP (and full HP),
gaining 1 AP back automatically each round. Actions:

    - Cast a named spell (3 AP) - each tied to an element (the emoji on
      the button says which). A monster's home element resists that same
      element (half damage) and is weak to one other (double damage) -
      neither is shown up front, so you learn it by testing spells.
    - Strike (free) - a guaranteed, unglamorous hit for resisted-tier
      damage. Always available even at 0 AP.
    - Heal (3 AP) - restore half your missing HP, but you're not
      defending yourself that round.
    - Defend (1 AP) - deal no damage, but halve the monster's hit back.
    - Rest (free) - fully refill your AP, but you take 10% extra damage
      that round for being unguarded.
    - One-shot (Headmaster only) - ephemeral private button that instantly
      wins with full loot / floor / trophy rewards. Normal actions stay on
      the public board; spectators never see One-shot.

Floor replay: once you've cleared a floor, `/descend floor:<n>` lets you
refight a single monster from it any time (not boss floors) - for loot,
or to grind. Loot always has a chance to drop. Stat-point odds on
practice clears scale with how far behind you are: close floors are more
likely to sharpen you, deep-behind floors still have a small chance.
Practice fights ignore the floor-clear path and never count toward lockouts.

Every monster beaten (real or practice) is bound into your army ledger with
its floor and combat stats — foundation for army PVP.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.velmora_channels import channel_mentions, with_study_hall

log = logging.getLogger("velmora.descent")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "descent_state.json"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "monster_art_assets"

# The Descent only runs in these channels - keeps the fight embeds and
# spam out of every other channel in the server. Study hall included for
# new-student practice.
DESCENT_CHANNEL_IDS = with_study_hall(
    1553089612849877053,  # original Descent channel
    1553861330988044308,
    1553861650732421202,
    1552401261184155648,
    1554579738943815712,
    1554579829452701786,
    1554579862176665630,
)


def _descent_channel_hint() -> str:
    return f"The Descent can only be played in {channel_mentions(DESCENT_CHANNEL_IDS)}."


# Headmaster convenience: a One-shot button on THEIR Descent fights only
# (still grants full win rewards). Override with DESCENT_ONESHOT_USER_ID;
# unset/0 disables. Same default id as CAST_AUTO_USER_ID.
_raw_oneshot = os.getenv("DESCENT_ONESHOT_USER_ID", "555141900802457630")
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

# ----------------------------------------------------------- difficulty
#
# Floors 1–50 use doubled growth vs the original curve. Floors 51–100 keep
# that same linear formula evaluated at the floor, then apply a late-game
# ramp so depth feels punishing without a hard wall. Boss multipliers are
# applied after the curve (unchanged).

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
    return round(hp), round(atk), round(df)


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
STATUP_AT_MONSTER = 5
MONSTERS_PER_FLOOR = 10
MAX_FLOOR = 100

# ---------------------------------------------------------------------- AP

STARTING_MAX_AP = 5
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
        "name": fight.name,
        "emoji": fight.emoji,
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
    """Mark whose Descent board/result this is (shared channels get busy)."""
    icon = None
    try:
        icon = member.display_avatar.url
    except Exception:
        icon = None
    embed.set_author(name=f"{member.display_name}'s Descent", icon_url=icon)
    owned = f"For {member.mention}"
    if embed.description:
        if owned not in embed.description:
            embed.description = f"{owned}\n{embed.description}"
    else:
        embed.description = owned
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
    Floors 1–50: always a single hit. Regular monsters on 51+: alternate
    Fury (2–4) and single, starting with Fury on the first swing. Fury
    bosses (60/70/80/90/100): every swing is Fury (3–6). Earlier bosses
    are unchanged (single hits)."""
    if fight.is_boss and fight.floor in FURY_BOSS_FLOORS:
        return random.randint(*FURY_BOSS_HITS)
    if not fight.is_boss and fight.floor >= FURY_START_FLOOR:
        if fight.fury_next:
            fight.fury_next = False
            return random.randint(*FURY_REGULAR_HITS)
        fight.fury_next = True
        return 1
    return 1


class Fight:
    """One in-progress monster encounter. Purely in-memory - like duels,
    an interrupted fight (e.g. a bot restart) just needs restarting via
    /descend rather than surviving forever."""

    def __init__(self, user_id: int, floor: int, monster_index: int, is_boss: bool,
                 name: str, emoji: str, element: str, kind: str, weak: list[str],
                 m_hp: int, m_atk: int, m_def: int, p_hp: int, p_atk: int, p_def: int,
                 ap_max: int, is_practice: bool = False):
        self.user_id = user_id
        self.floor = floor
        self.monster_index = monster_index
        self.is_boss = is_boss
        self.name = name
        self.emoji = emoji
        self.element = element
        self.kind = kind
        self.weak = weak
        self.m_hp_max = self.m_hp = m_hp
        self.m_atk = m_atk
        self.m_def = m_def
        self.p_hp_max = self.p_hp = p_hp
        self.p_atk = p_atk
        self.p_def = p_def
        self.ap_max = self.ap = ap_max
        self.is_practice = is_practice
        self.round = 0
        self.log: list[str] = []
        # Regular floors 51+: first monster swing is Fury, then alternate.
        # Toggled by fury_hit_count; unused on floors without alternating Fury.
        self.fury_next = True
        # potion buffs - defaults are "no effect"; _apply_potion_mods and
        # _start_fight may override these right after construction
        self.heal_mult = 1.0
        self.defend_mult = DEFEND_DMG_MULT
        self.rest_no_penalty = False
        self.reveal_weakness = False
        self.boss_dmg_mult = 1.0
        self.revive_available = False
        self.message: Optional[discord.Message] = None
        self.oneshot_message: Optional[discord.Message] = None
        self.auto = False  # chain into the next monster after a win

    def embed(self, member: discord.Member) -> discord.Embed:
        title = f"{self.emoji} Floor {self.floor} — {self.name}"
        if self.is_boss:
            title += " (Boss)"
        elif self.is_practice:
            title += " (Practice)"
        desc = (
            f"Monster {self.monster_index}/{MONSTERS_PER_FLOOR}"
            if not self.is_practice
            else "Practice fight — no floor progress at stake"
        )
        if self.auto:
            desc += "\n⚡ Auto-play on — next monster posts after a win."
        e = discord.Embed(
            title=title,
            description=desc,
            color=0x8B5FBF if not self.is_boss else 0xE0A526,
        )
        stamp_owner(e, member)
        e.set_image(url="attachment://monster.png")
        e.add_field(
            name=f"{member.display_name}'s HP",
            value=(f"{bar(self.p_hp, self.p_hp_max)} {self.p_hp}/{self.p_hp_max}\n"
                   f"⚡ {self.ap}/{self.ap_max} AP"),
            inline=False,
        )
        e.add_field(name=self.name, value=f"{bar(self.m_hp, self.m_hp_max)} {self.m_hp}/{self.m_hp_max}",
                    inline=False)
        if self.reveal_weakness and self.weak:
            e.add_field(name="🦉 Owl's Eye", value=f"Weak to {' / '.join(ELEMENT_EMOJI[w] for w in self.weak)}",
                       inline=False)
        if self.log:
            e.add_field(name="Last round", value="\n".join(self.log[-2:]), inline=False)
        return e


class ElementButton(discord.ui.Button):
    def __init__(self, element: str, disabled: bool):
        super().__init__(label=SPELL_NAME[element], emoji=ELEMENT_EMOJI[element],
                          style=discord.ButtonStyle.secondary, disabled=disabled, row=0)
        self.element = element

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "cast", element=self.element)


class StrikeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Strike", emoji="🗡️", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "strike")


class HealButton(discord.ui.Button):
    def __init__(self, disabled: bool):
        super().__init__(label="Heal", emoji="💚", style=discord.ButtonStyle.success,
                          disabled=disabled, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "heal")


class DefendButton(discord.ui.Button):
    def __init__(self, disabled: bool):
        super().__init__(label="Defend", emoji="🛡️", style=discord.ButtonStyle.primary,
                          disabled=disabled, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "defend")


class RestButton(discord.ui.Button):
    def __init__(self, disabled: bool):
        super().__init__(label="Rest", emoji="😮‍💨", style=discord.ButtonStyle.danger,
                          disabled=disabled, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "rest")


class OneShotButton(discord.ui.Button):
    """Headmaster-only finisher — full rewards, no counterattack."""

    def __init__(self):
        super().__init__(
            label="One-shot",
            emoji="⚡",
            style=discord.ButtonStyle.danger,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message(
                "That's not your fight - use `/descend` to start your own.",
                ephemeral=True,
            )
            return
        if (
            DESCENT_ONESHOT_USER_ID is None
            or interaction.user.id != DESCENT_ONESHOT_USER_ID
        ):
            await interaction.response.send_message(
                "That button isn't for you.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "oneshot")


class OneShotView(discord.ui.View):
    """Ephemeral panel — only the sealed user receives this message."""

    def __init__(self, cog: "Descent", fight: Fight):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = fight.user_id
        self.add_item(OneShotButton())


class StopAutoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Stop Auto",
            emoji="⏹️",
            style=discord.ButtonStyle.danger,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message(
                "That's not your fight - use `/descend` to start your own.",
                ephemeral=True,
            )
            return
        cog: Descent = self.view.cog
        cog.stop_auto(interaction.user.id)
        fight = cog.fights.get(interaction.user.id)
        if fight is not None:
            fight.auto = False
            await interaction.response.edit_message(
                embed=fight.embed(interaction.user),
                view=FightView(cog, fight),
            )
            await interaction.followup.send(
                "⏹️ Auto-play stopped. Finish this fight, then `/descend` for the next.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "⏹️ Auto-play stopped.",
            ephemeral=True,
        )


class FightView(discord.ui.View):
    def __init__(self, cog: "Descent", fight: Fight):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = fight.user_id
        for el in ELEMENTS:
            self.add_item(ElementButton(el, disabled=fight.ap < CAST_AP_COST))
        self.add_item(StrikeButton())
        self.add_item(HealButton(disabled=fight.ap < HEAL_AP_COST))
        self.add_item(DefendButton(disabled=fight.ap < DEFEND_AP_COST))
        self.add_item(RestButton(disabled=fight.ap >= fight.ap_max))
        if fight.auto:
            self.add_item(StopAutoButton())


class StatButton(discord.ui.Button):
    def __init__(self, stat: str, label: str, emoji: str):
        # Stable custom_ids so the picker survives bot restarts (registered
        # once in Descent.cog_load). Spending always applies to the clicker.
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.primary,
                         custom_id=f"descent:statup:{stat}")
        self.stat = stat

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.pick_stat(interaction, self.stat)


class StatUpView(discord.ui.View):
    """Persistent view — unspent Descent picks must still be clickable after
    a redeploy, otherwise players grind practice forever with a stuck
    pending_statup and never roll another +1."""

    def __init__(self, cog: "Descent"):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(StatButton("hp", "Max HP", "❤️"))
        self.add_item(StatButton("atk", "Attack", "⚔️"))
        self.add_item(StatButton("def", "Defense", "🛡️"))


class Descent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self.fights: dict[int, Fight] = {}
        # In-memory auto-play: after a win, post the next monster without /descend.
        self.auto_play: set[int] = set()
        self.auto_practice_floor: dict[int, int] = {}

    async def cog_load(self):
        self.bot.add_view(StatUpView(self))

    def is_auto(self, user_id: int) -> bool:
        return user_id in self.auto_play

    def set_auto(self, user_id: int, enabled: bool) -> None:
        if enabled:
            self.auto_play.add(user_id)
        else:
            self.stop_auto(user_id)

    def stop_auto(self, user_id: int) -> None:
        self.auto_play.discard(user_id)
        self.auto_practice_floor.pop(user_id, None)

    def _load(self) -> dict:
        if STATE_PATH.exists():
            try:
                return json.loads(STATE_PATH.read_text())
            except Exception:
                log.exception("Failed to load descent state")
        return {"players": {}}

    def save(self):
        STATE_PATH.write_text(json.dumps(self.state, indent=2))

    def record(self, user_id: int) -> dict:
        rec = self.state["players"].setdefault(str(user_id), blank_record())
        rec.setdefault("max_ap", STARTING_MAX_AP)  # back-compat for records saved before AP existed
        rec.setdefault("bosses_bound", [])          # back-compat for records saved before boss trophies existed
        rec.setdefault("army", [])                  # back-compat for records saved before army tracking
        rec.setdefault("army_seq", len(rec.get("army") or []))
        return rec

    def boss_trophies(self, user_id: int) -> dict:
        """Descent boss trophies this player has bound, keyed for /summon and
        /bestiary in the Beasts cog. Read-only - never creates a record."""
        rec = self.state["players"].get(str(user_id))
        if not rec:
            return {}
        out = {}
        for floor in rec.get("bosses_bound", []):
            name, emoji, _ = BOSS_NAMES.get(floor, (f"Floor {floor} Boss", "👑", None))
            image = BOSS_IMAGE.get(floor)
            out[f"boss:{floor}"] = {
                "name": name,
                "emoji": emoji,
                "floor": floor,
                "image_path": (ASSETS_DIR / image) if image else None,
                "summons": BOSS_SUMMONS.get(floor, [f"{{owner}}'s {name} answers the call."]),
            }
        return out

    # -------------------------------------------------------- fight setup

    def _make_monster(self, floor: int, monster_index: int):
        zone = zone_for(floor)
        primary = zone["element"]
        is_boss = (floor % 10 == 0 and monster_index == MONSTERS_PER_FLOOR)
        if is_boss:
            element = primary
            name, emoji, kind = BOSS_NAMES[floor]
            weak = [WEAK_TO[element], WEAK_TO[WEAK_TO[element]]]
        else:
            secondary = SECONDARY_ELEMENT[primary]
            element = secondary if random.random() < SECONDARY_CHANCE else primary
            name, emoji, kind = random.choice(MONSTER_NAMES[element])
            weak = [WEAK_TO[element]]
        m_hp, m_atk, m_def = monster_stats(floor, is_boss)
        return name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def

    async def _monster_file(self, fight: "Fight") -> discord.File:
        filename = BOSS_IMAGE[fight.floor] if fight.is_boss else MONSTER_IMAGE[fight.name]
        path = ASSETS_DIR / filename
        return discord.File(path, filename="monster.png")

    def _apply_potion_mods(self, fight: "Fight", user_id: int, is_boss: bool):
        """Pull any active potion buffs for a real fight and fold them
        into this Fight - a no-op if the Potions cog isn't loaded."""
        potions = self.bot.get_cog("Potions")
        if not potions:
            return
        mods = potions.consume_for_fight(user_id, is_boss)
        if not mods:
            return
        if "atk_mult" in mods:
            fight.p_atk = round(fight.p_atk * mods["atk_mult"])
        if "def_mult" in mods:
            fight.p_def = round(fight.p_def * mods["def_mult"])
        if "ap_bonus" in mods:
            fight.ap_max += mods["ap_bonus"]
            fight.ap = fight.ap_max
        if "hp_bonus" in mods:
            fight.p_hp_max += mods["hp_bonus"]
            fight.p_hp += mods["hp_bonus"]
        if "heal_mult" in mods:
            fight.heal_mult = mods["heal_mult"]
        if "defend_mult" in mods:
            fight.defend_mult = mods["defend_mult"]
        if "rest_no_penalty" in mods:
            fight.rest_no_penalty = True
        if "reveal_weakness" in mods:
            fight.reveal_weakness = True
        if "boss_dmg_mult" in mods:
            fight.boss_dmg_mult = mods["boss_dmg_mult"]
        if potions.has_revive(user_id):
            fight.revive_available = True
            potions.consume_revive(user_id)

    async def _publish_board(
        self,
        interaction: discord.Interaction,
        fight: Fight,
        embed: discord.Embed,
        view: Optional[discord.ui.View],
    ) -> None:
        """Edit the public fight post (needed when One-shot is ephemeral)."""
        owner = interaction.user if interaction.user.id == fight.user_id else None
        if owner is None and interaction.guild is not None:
            owner = interaction.guild.get_member(fight.user_id)
        if owner is not None:
            stamp_owner(embed, owner)
        if fight.message is not None:
            try:
                await fight.message.edit(embed=embed, view=view, attachments=[])
                return
            except discord.DiscordException:
                log.exception("Could not update Descent fight board.")
        await interaction.edit_original_response(embed=embed, view=view)

    async def _dismiss_oneshot(
        self,
        fight: Fight,
        interaction: Optional[discord.Interaction] = None,
        *,
        used: bool = False,
    ) -> None:
        text = "⚡ One-shot — done." if used else "Fight over."
        if interaction is not None and interaction.message is not None:
            oneshot = fight.oneshot_message
            if oneshot is not None and interaction.message.id == oneshot.id:
                try:
                    await interaction.edit_original_response(
                        content=text, embed=None, view=None
                    )
                except discord.DiscordException:
                    pass
                fight.oneshot_message = None
                return
        if fight.oneshot_message is not None:
            try:
                await fight.oneshot_message.edit(content=text, embed=None, view=None)
            except discord.DiscordException:
                pass
            fight.oneshot_message = None

    async def _offer_oneshot(
        self, interaction: discord.Interaction, fight: Fight
    ) -> None:
        if (
            DESCENT_ONESHOT_USER_ID is None
            or fight.user_id != DESCENT_ONESHOT_USER_ID
        ):
            return
        try:
            msg = await interaction.followup.send(
                "⚡ Private finisher — only you can see this.",
                view=OneShotView(self, fight),
                ephemeral=True,
            )
            fight.oneshot_message = msg
        except discord.DiscordException:
            log.exception("Could not send Descent one-shot panel.")

    async def _post_fight_board(
        self,
        interaction: discord.Interaction,
        fight: Fight,
        *,
        followup: bool = False,
    ) -> None:
        file = await self._monster_file(fight)
        embed = fight.embed(interaction.user)
        view = FightView(self, fight)
        use_followup = followup or interaction.response.is_done()
        if use_followup:
            try:
                msg = await interaction.followup.send(embed=embed, view=view, file=file)
                fight.message = msg
            except discord.DiscordException:
                log.exception("Could not post Descent fight board via followup.")
                fight.message = None
        else:
            await interaction.response.send_message(embed=embed, view=view, file=file)
            try:
                fight.message = await interaction.original_response()
            except discord.HTTPException:
                fight.message = None
        await self._offer_oneshot(interaction, fight)

    def _apply_prelude_bastion(self, fight: Fight, user_id: int) -> None:
        """Prelude Bastion: Descent monsters start at half attack."""
        castles = self.bot.get_cog("Castles")
        if castles and castles.prelude_halves_monster_atk(user_id):
            fight.m_atk = max(1, int(fight.m_atk) // 2)

    def _arm_fight(self, fight: Fight) -> None:
        fight.auto = self.is_auto(fight.user_id)
        self.fights[fight.user_id] = fight

    async def _start_fight(
        self,
        interaction: discord.Interaction,
        rec: dict,
        *,
        followup: bool = False,
    ):
        floor, idx = rec["floor"], rec["monster_index"]
        name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def = self._make_monster(floor, idx)
        p_hp, p_atk, p_def = player_stats(rec)
        fight = Fight(interaction.user.id, floor, idx, is_boss, name, emoji, element, kind, weak,
                      m_hp, m_atk, m_def, p_hp, p_atk, p_def, ap_max=rec["max_ap"])
        self._apply_potion_mods(fight, interaction.user.id, is_boss)
        self._apply_prelude_bastion(fight, interaction.user.id)
        # Real runs clear any practice auto target.
        self.auto_practice_floor.pop(interaction.user.id, None)
        self._arm_fight(fight)
        await self._post_fight_board(interaction, fight, followup=followup)

    async def _start_practice_fight(
        self,
        interaction: discord.Interaction,
        rec: dict,
        floor: int,
        *,
        followup: bool = False,
    ):
        name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def = self._make_monster(floor, 1)
        p_hp, p_atk, p_def = player_stats(rec)
        fight = Fight(interaction.user.id, floor, 0, False, name, emoji, element, kind, weak,
                      m_hp, m_atk, m_def, p_hp, p_atk, p_def, ap_max=rec["max_ap"], is_practice=True)
        self._apply_prelude_bastion(fight, interaction.user.id)
        if self.is_auto(interaction.user.id):
            self.auto_practice_floor[interaction.user.id] = floor
        self._arm_fight(fight)
        await self._post_fight_board(interaction, fight, followup=followup)

    async def _try_auto_continue(self, interaction: discord.Interaction) -> bool:
        """If auto-play is on, start the next fight. Returns True when a fight was posted."""
        uid = interaction.user.id
        if not self.is_auto(uid) or uid in self.fights:
            return False
        rec = self.record(uid)
        if pending_statup_picks(rec) > 0:
            return False
        now = time.time()
        if rec["locked_until"] > now:
            self.stop_auto(uid)
            try:
                await interaction.followup.send(
                    "⏹️ Auto-play stopped — floor lockout is active.",
                    ephemeral=True,
                )
            except discord.DiscordException:
                pass
            return False
        practice_floor = self.auto_practice_floor.get(uid)
        try:
            if practice_floor is not None:
                await self._start_practice_fight(
                    interaction, rec, practice_floor, followup=True,
                )
                return True
            if rec["floor"] > MAX_FLOOR:
                self.stop_auto(uid)
                await interaction.followup.send(
                    "⏹️ Auto-play stopped — the Descent is complete.",
                    ephemeral=True,
                )
                return False
            await self._start_fight(interaction, rec, followup=True)
            return True
        except discord.DiscordException:
            log.exception("Descent auto-continue failed for %s", uid)
            return False

    # ------------------------------------------------------------ combat

    async def take_action(self, interaction: discord.Interaction, action: str, element: Optional[str] = None):
        fight = self.fights.get(interaction.user.id)
        if not fight:
            await interaction.followup.send("That fight isn't active anymore - use `/descend` to start again.",
                                            ephemeral=True)
            return
        fight.round += 1
        dmg_dealt = 0
        counter_mult = BASELINE_DMG_MULT
        lines: list[str] = []

        if action == "cast":
            if fight.ap < CAST_AP_COST:
                await interaction.followup.send("Not enough AP for that spell.", ephemeral=True)
                return
            fight.ap -= CAST_AP_COST
            if element == fight.element:
                mult, note = 0.5, "resisted"
            elif element in fight.weak:
                mult, note = 2.0, "super effective"
            else:
                mult, note = 1.0, None
            dmg_dealt = mitigate(fight.p_atk, mult, fight.m_def)
            lines.append(f"{ELEMENT_EMOJI[element]} You cast **{SPELL_NAME[element]}** for **{dmg_dealt}**"
                        + (f" ({note})" if note else "") + f" (-{CAST_AP_COST} AP)")
        elif action == "strike":
            dmg_dealt = mitigate(fight.p_atk, STRIKE_MULT, fight.m_def)
            lines.append(f"🗡️ You strike for **{dmg_dealt}** (no AP used)")
        elif action == "heal":
            if fight.ap < HEAL_AP_COST:
                await interaction.followup.send("Not enough AP to heal.", ephemeral=True)
                return
            fight.ap -= HEAL_AP_COST
            missing = fight.p_hp_max - fight.p_hp
            healed = round(missing * HEAL_FRACTION * fight.heal_mult)
            fight.p_hp = min(fight.p_hp_max, fight.p_hp + healed)
            lines.append(f"💚 You heal for **{healed}** (-{HEAL_AP_COST} AP, you're exposed)")
        elif action == "defend":
            if fight.ap < DEFEND_AP_COST:
                await interaction.followup.send("Not enough AP to defend.", ephemeral=True)
                return
            fight.ap -= DEFEND_AP_COST
            counter_mult = fight.defend_mult
            lines.append("🛡️ You brace to defend (free action)" if DEFEND_AP_COST == 0
                         else f"🛡️ You brace to defend (-{DEFEND_AP_COST} AP)")
        elif action == "rest":
            fight.ap = fight.ap_max
            counter_mult = 1.0 if fight.rest_no_penalty else REST_DMG_MULT
            lines.append("😮‍💨 You catch your breath - AP fully restored"
                        + (" and unbothered" if fight.rest_no_penalty else " (unguarded)"))
        elif action == "oneshot":
            if (
                DESCENT_ONESHOT_USER_ID is None
                or interaction.user.id != DESCENT_ONESHOT_USER_ID
            ):
                await interaction.followup.send(
                    "That button isn't for you.", ephemeral=True
                )
                return
            dmg_dealt = max(fight.m_hp, 1)
            lines.append(
                f"⚡ You end it in one motion — **{fight.name}** never gets a turn."
            )
        else:
            return

        if dmg_dealt:
            fight.m_hp -= dmg_dealt
        fight.log.extend(lines)

        if fight.m_hp <= 0:
            await self._on_win(interaction, fight, from_oneshot=(action == "oneshot"))
            return

        counter_mult *= fight.boss_dmg_mult if fight.is_boss else 1.0
        hits = fury_hit_count(fight)
        hit_rolls: list[int] = []
        for _ in range(hits):
            roll = mitigate(fight.m_atk, counter_mult, fight.p_def)
            fight.p_hp -= roll
            hit_rolls.append(roll)
        tag = " (reduced)" if counter_mult < 1 else (" (extra!)" if counter_mult > 1 else "")
        if hits > 1:
            rolls_txt = ", ".join(f"**{r}**" for r in hit_rolls)
            fight.log.append(
                f"{fight.emoji} {fight.name} strikes in a **Fury** ({hits} hits: {rolls_txt})"
                f" — **{sum(hit_rolls)}** total{tag}"
            )
        else:
            fight.log.append(f"{fight.emoji} {fight.name} hits back for **{hit_rolls[0]}**{tag}")

        if fight.p_hp <= 0:
            if fight.revive_available:
                fight.revive_available = False
                fight.p_hp = 1
                fight.log.append("🪽 Phoenix Tears flares - you're pulled back from the brink at **1 HP**.")
            else:
                await self._on_loss(interaction, fight)
                return

        fight.ap = min(fight.ap_max, fight.ap + AP_REGEN_PER_TURN)
        await self._publish_board(
            interaction,
            fight,
            fight.embed(interaction.user),
            FightView(self, fight),
        )

    async def _drop_loot(self, member: discord.Member, element: str, n: int = 1) -> Optional[str]:
        world_cog = self.bot.get_cog("World")
        if not world_cog:
            return None
        item_id = ZONE_ITEM[element]
        item = world_cog.world.items.get(item_id)
        if not item:
            return None
        async with world_cog.lock:
            student = world_cog.student(member)
            world_cog.world.give(student, item_id, n)
            world_cog.save()
        qty = f" x{n}" if n > 1 else ""
        return f"{item.get('emoji', '')} **{item['name']}**{qty}".strip()

    async def _drop_boss_item(self, member: discord.Member) -> Optional[str]:
        world_cog = self.bot.get_cog("World")
        item = world_cog.world.items.get(BOSS_ITEM) if world_cog else None
        if not world_cog or not item:
            return None
        async with world_cog.lock:
            student = world_cog.student(member)
            world_cog.world.give(student, BOSS_ITEM, 1)
            world_cog.save()
        return f"{item.get('emoji', '')} **{item['name']}**".strip()

    async def _on_win(
        self,
        interaction: discord.Interaction,
        fight: Fight,
        *,
        from_oneshot: bool = False,
    ):
        del self.fights[interaction.user.id]
        rec = self.record(interaction.user.id)
        member = interaction.user
        await self._dismiss_oneshot(fight, interaction, used=from_oneshot)
        castles = self.bot.get_cog("Castles")
        allowed = castles.recruit_allowed(member.id, 1) if castles else 1
        unit = None
        if allowed > 0:
            unit = recruit_from_fight(rec, fight)
            if castles:
                castles.note_recruits(member.id, 1)
                bonus = castles.fifth_muster_bonus(member.id)
                if bonus and castles.recruit_allowed(member.id, bonus) > 0:
                    clone_army_unit(rec, unit)
                    castles.note_recruits(member.id, bonus)
        elif castles:
            # Still tick Fifth Muster progress even when the daily cap blocks a bind.
            castles.fifth_muster_bonus(member.id)
        self.save()

        if fight.is_practice:
            await self._on_practice_win(interaction, fight, rec, member)
            return

        drop_text = None
        if fight.is_boss:
            drop_text = await self._drop_boss_item(member)
        elif random.random() < MONSTER_DROP_CHANCE:
            drop_text = await self._drop_loot(member, fight.element)

        cleared_index = fight.monster_index
        if cleared_index >= MONSTERS_PER_FLOOR:
            # floor cleared
            floor = rec["floor"]
            rec["highest_cleared"] = max(rec["highest_cleared"], floor)
            rec["floor"] = min(floor + 1, MAX_FLOOR)
            rec["monster_index"] = 1
            rec["losses"] = 0
            ap_increased = floor % 10 == 0
            if ap_increased:
                rec["max_ap"] += 1
            clear_drop = await self._drop_loot(member, zone_for(floor)["element"], FLOOR_CLEAR_GUARANTEED)
            # Fury bosses (60/70/80/90/100) bank three free picks; everything else one.
            clear_picks = 3 if fight.is_boss and floor in FURY_BOSS_FLOORS else 1
            rec["pending_statup"] = clear_picks
            self.save()

            potions = self.bot.get_cog("Potions")
            fortune_drop = None
            if potions:
                bonus_n = potions.consume_loot_boost(member.id)
                if bonus_n:
                    fortune_drop = await self._drop_loot(member, zone_for(floor)["element"], bonus_n)

            desc = f"**Floor {floor} cleared!**"
            if ap_increased:
                desc += f" Max AP is now **{rec['max_ap']}**."
            if drop_text:
                desc += f"\n📦 The kill dropped {drop_text}."
            if clear_drop:
                desc += f"\n📦 Clearing the floor also dropped {clear_drop}."
            if fortune_drop:
                desc += f"\n🍀 Draught of Fortune paid off: {fortune_drop}."
            if fight.is_boss:
                store = self.bot.get_cog("Store")
                house = store.member_house(member) if store else None
                if house:
                    store.record(house=house, delta=5, actor_id=self.bot.user.id if self.bot.user else 0,
                                 target_id=member.id, reason=f"Descent: floor {floor} boss defeated")
                desc += f"\n🏆 You defeated **{fight.name}** and earned House {house or 'points (unassigned)'} 5 points."
                if floor not in rec["bosses_bound"]:
                    rec["bosses_bound"].append(floor)
                    self.save()
                desc += (f"\n🐲 **{fight.name}** now answers to you. See it with `/bestiary`, "
                        f"call it with `/summon`.")
            if floor >= MAX_FLOOR:
                desc += "\n\n👑 **The Descent is complete.** There is nothing further down."
                self.stop_auto(member.id)
            if clear_picks > 1:
                desc += (f"\n\nYou've earned **{clear_picks}** stat points for clearing this Fury boss "
                         f"— pick where each one goes.")
            else:
                desc += "\n\nYou've earned a stat point for clearing the floor - pick where it goes."
            if self.is_auto(member.id) and floor < MAX_FLOOR:
                desc += "\n⚡ Auto paused for your stat pick — next monster follows when you're done."
            embed = discord.Embed(title=f"{fight.emoji} Victory!", description=desc, color=0x2ECC71)
            await self._publish_board(interaction, fight, embed, StatUpView(self))
            return

        rec["monster_index"] = cleared_index + 1
        self.save()

        desc = f"**{fight.name}** falls. On to monster {rec['monster_index']}/{MONSTERS_PER_FLOOR}."
        if drop_text:
            desc += f"\n📦 It dropped {drop_text}."
        embed = discord.Embed(
            title=f"{fight.emoji} Victory!",
            description=desc,
            color=0x2ECC71,
        )
        if cleared_index == STATUP_AT_MONSTER:
            rec["pending_statup"] = 1
            self.save()
            embed.description += "\n\nYou've earned a stat point - pick where it goes."
            if self.is_auto(member.id):
                embed.description += "\n⚡ Auto paused for your stat pick — next monster follows when you're done."
            await self._publish_board(interaction, fight, embed, StatUpView(self))
        elif self.is_auto(member.id):
            embed.description += "\n⚡ Auto — next monster incoming…"
            await self._publish_board(interaction, fight, embed, None)
            await self._try_auto_continue(interaction)
        else:
            embed.description += "\nUse `/descend` to keep going."
            await self._publish_board(interaction, fight, embed, None)

    async def _on_practice_win(self, interaction: discord.Interaction, fight: Fight, rec: dict, member: discord.Member):
        got_loot = random.random() < MONSTER_DROP_CHANCE
        if got_loot:
            await self._drop_loot(member, fight.element)

        desc = f"**{fight.name}** falls. This was a practice fight - your floor progress hasn't changed."
        desc += "\nA material dropped!" if got_loot else "\nNo material dropped this time."

        chance = practice_statup_chance(rec["floor"], fight.floor)
        pct = max(1, int(round(chance * 100)))
        pending = pending_statup_picks(rec)
        if pending > 0:
            # Should be rare (practice is gated on spend), but keep the picker
            # attached so a stuck pick can still be cleared from a win message.
            desc += (f"\n\nYou still have **{pending}** unspent stat pick"
                     f"{'s' if pending != 1 else ''} — spend "
                     f"{'them' if pending != 1 else 'it'} before practice can sharpen you again.")
        elif random.random() < chance:
            rec["pending_statup"] = 1
            pending = 1
            desc += "\n\nThis grind sharpened you - pick a stat to raise."
        else:
            desc += f"\n\nNo free stat this time ({pct}% chance on this floor)."

        self.save()
        embed = discord.Embed(title=f"{fight.emoji} Practice victory!", description=desc, color=0x2ECC71)
        if pending > 0:
            if self.is_auto(member.id):
                desc += "\n⚡ Auto paused for your stat pick — practice continues when you're done."
                embed.description = desc
            await self._publish_board(interaction, fight, embed, StatUpView(self))
        elif self.is_auto(member.id):
            embed.description = desc + "\n⚡ Auto — another practice incoming…"
            await self._publish_board(interaction, fight, embed, None)
            await self._try_auto_continue(interaction)
        else:
            await self._publish_board(interaction, fight, embed, None)

    async def _on_loss(self, interaction: discord.Interaction, fight: Fight):
        del self.fights[interaction.user.id]
        await self._dismiss_oneshot(fight, interaction)
        was_auto = self.is_auto(interaction.user.id)
        # Loss breaks the chain; re-enable with `/descend auto:True`.
        self.stop_auto(interaction.user.id)

        if fight.is_practice:
            tip = (
                "Use `/descend floor:` again"
                if not was_auto
                else "Auto stopped — `/descend floor:… auto:True` to grind again"
            )
            embed = discord.Embed(
                title="Defeated",
                description=f"**{fight.name}** gets the better of you. It was only practice - no penalty, "
                            f"no lockout. {tip}.",
                color=0xC0392B,
            )
            await self._publish_board(interaction, fight, embed, None)
            return

        rec = self.record(interaction.user.id)
        rec["losses"] += 1
        locked = rec["losses"] >= MAX_LOSSES
        if locked:
            rec["locked_until"] = time.time() + LOCKOUT_SECONDS
            rec["losses"] = 0
            rec["monster_index"] = 1
        self.save()

        if locked:
            desc = (f"**{fight.name}** finishes you off. That's 3 losses on floor {fight.floor} - "
                     f"you're locked out for 24 hours. When you're back, floor {fight.floor} restarts "
                     f"at monster 1.")
        else:
            tip = "Use `/descend` to try that monster again."
            if was_auto:
                tip = "Auto stopped — `/descend auto:True` to keep chaining after a win."
            desc = f"**{fight.name}** finishes you off. {tip}"
        embed = discord.Embed(title="Defeated", description=desc, color=0xC0392B)
        await self._publish_board(interaction, fight, embed, None)

    async def pick_stat(self, interaction: discord.Interaction, stat: str):
        rec = self.record(interaction.user.id)
        remaining = pending_statup_picks(rec)
        if remaining <= 0:
            await interaction.response.send_message("Nothing to spend right now.", ephemeral=True)
            return
        rec["stat_points"][stat] += 1
        remaining -= 1
        rec["pending_statup"] = remaining
        self.save()
        label = {"hp": "Max HP", "atk": "Attack", "def": "Defense"}[stat]
        if remaining > 0:
            embed = discord.Embed(
                title="Stat raised",
                description=(f"**+1 {label}**. {remaining} pick"
                             f"{'s' if remaining != 1 else ''} left — choose another."),
                color=0x6C5CE7,
            )
            stamp_owner(embed, interaction.user)
            await interaction.response.edit_message(embed=embed, view=StatUpView(self))
            return
        if self.is_auto(interaction.user.id):
            embed = discord.Embed(
                title="Stat raised",
                description=f"**+1 {label}**. ⚡ Auto — next monster incoming…",
                color=0x6C5CE7,
            )
            stamp_owner(embed, interaction.user)
            await interaction.response.edit_message(embed=embed, view=None)
            await self._try_auto_continue(interaction)
            return
        embed = discord.Embed(
            title="Stat raised",
            description=f"**+1 {label}**. Use `/descend` to keep going.",
            color=0x6C5CE7,
        )
        stamp_owner(embed, interaction.user)
        await interaction.response.edit_message(embed=embed, view=None)

    async def _prompt_statup(self, interaction: discord.Interaction, rec: dict) -> bool:
        """If the player still owes a pick, show the picker and return True."""
        left = pending_statup_picks(rec)
        if left <= 0:
            return False
        tip = "Pick your remaining stat points first." if left > 1 else "Pick your stat point first."
        desc = f"{left} picks remaining." if left > 1 else None
        embed = discord.Embed(title="Choose a stat to raise", description=desc, color=0x6C5CE7)
        stamp_owner(embed, interaction.user)
        await interaction.response.send_message(
            tip,
            embed=embed,
            view=StatUpView(self),
        )
        return True

    # ----------------------------------------------------------- commands

    @app_commands.command(name="descend", description="Fight the next monster on your current Descent floor.")
    @app_commands.describe(
        floor="Replay a floor you've already cleared, for practice/loot (not boss floors).",
        auto="After each win, post the next monster automatically (Stop Auto on the board to cancel).",
    )
    async def descend(
        self,
        interaction: discord.Interaction,
        floor: Optional[int] = None,
        auto: Optional[bool] = None,
    ):
        if interaction.channel_id not in DESCENT_CHANNEL_IDS:
            await interaction.response.send_message(_descent_channel_hint(), ephemeral=True)
            return

        if auto is True:
            self.set_auto(interaction.user.id, True)
        elif auto is False:
            self.set_auto(interaction.user.id, False)

        rec = self.record(interaction.user.id)

        if floor is not None:
            if interaction.user.id in self.fights:
                await interaction.response.send_message("Finish your current fight first.", ephemeral=True)
                return
            if floor < 1 or floor > rec["highest_cleared"]:
                await interaction.response.send_message(
                    f"You can only replay a floor you've already cleared (up to floor {rec['highest_cleared']}).",
                    ephemeral=True)
                return
            if floor % 10 == 0:
                await interaction.response.send_message("Boss floors can't be replayed for practice.",
                                                         ephemeral=True)
                return
            # Same gate as a real descend: an unspent pick silently blocked
            # practice +1 rolls forever, which looked like the grind was broken.
            if await self._prompt_statup(interaction, rec):
                return
            await self._start_practice_fight(interaction, rec, floor)
            return

        now = time.time()
        if rec["locked_until"] > now:
            left = int(rec["locked_until"] - now)
            h, m = left // 3600, (left % 3600) // 60
            await interaction.response.send_message(
                f"Floor {rec['floor']} is locked after 3 losses. Try again in {h}h {m}m.", ephemeral=True)
            return
        if await self._prompt_statup(interaction, rec):
            return
        if interaction.user.id in self.fights:
            fight = self.fights[interaction.user.id]
            # Re-arm auto on an open board if they toggled it with this command.
            if auto is not None:
                fight.auto = self.is_auto(interaction.user.id)
            await self._post_fight_board(interaction, fight)
            return
        if rec["floor"] > MAX_FLOOR:
            await interaction.response.send_message("You've already conquered the Descent.", ephemeral=True)
            return
        await self._start_fight(interaction, rec)

    @app_commands.command(name="descentstatus", description="Your Descent progress: floor, stats, and lockout.")
    async def descentstatus(self, interaction: discord.Interaction):
        if interaction.channel_id not in DESCENT_CHANNEL_IDS:
            await interaction.response.send_message(_descent_channel_hint(), ephemeral=True)
            return
        rec = self.record(interaction.user.id)
        p_hp, p_atk, p_def = player_stats(rec)
        zone = zone_for(rec["floor"])
        now = time.time()

        desc = [f"**Floor {rec['floor']}** — {zone['name']}",
                f"Monster {rec['monster_index']}/{MONSTERS_PER_FLOOR} • {rec['losses']}/{MAX_LOSSES} losses"]
        if rec["locked_until"] > now:
            left = int(rec["locked_until"] - now)
            h, m = left // 3600, (left % 3600) // 60
            desc.append(f"🔒 Locked for {h}h {m}m")
        if rec["highest_cleared"]:
            desc.append(f"Deepest floor cleared: **{rec['highest_cleared']}**")

        army = rec.get("army") or []
        if army:
            counts = army_floor_counts(army)
            floors = sorted(counts)
            band = ", ".join(
                f"F{fl}×{counts[fl]}" for fl in floors[-8:]  # newest-depth slice
            )
            if len(floors) > 8:
                band = "… " + band
            desc.append(
                f"⚔️ Army: **{len(army)}** bound"
                + (f" ({band})" if band else "")
            )

        embed = discord.Embed(title=f"{interaction.user.display_name}'s Descent", description="\n".join(desc),
                              color=0x6C5CE7)
        embed.add_field(name="❤️ Max HP", value=str(p_hp))
        embed.add_field(name="⚔️ Attack", value=str(p_atk))
        embed.add_field(name="🛡️ Defense", value=str(p_def))
        embed.add_field(name="⚡ Max AP", value=str(rec["max_ap"]))
        if army:
            last = army[-1]
            embed.add_field(
                name="Last bound",
                value=(
                    f"{last.get('emoji', '')} **{last.get('name', '?')}** "
                    f"(floor {last.get('floor', '?')}) — "
                    f"HP {last.get('hp', '?')} · ATK {last.get('atk', '?')} · "
                    f"DEF {last.get('def', '?')}"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    async def descentreset(self, interaction: discord.Interaction, member: discord.Member = None):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        target = member or interaction.user
        self.fights.pop(target.id, None)
        self.state["players"][str(target.id)] = blank_record()
        self.save()
        who = "Your" if target.id == interaction.user.id else f"{target.display_name}'s"
        await interaction.response.send_message(
            f"{who} Descent progress has been wiped - back to floor 1, monster 1.", ephemeral=True)

    async def descentunlock(self, interaction: discord.Interaction, member: discord.Member = None):
        """Lift the 24h floor lockout and reset the loss counter.

        Keeps floor, monster progress, stats, Max AP, and highest cleared.
        """
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        target = member or interaction.user
        rec = self.record(target.id)
        was_locked = rec.get("locked_until", 0) > time.time()
        prior_losses = int(rec.get("losses", 0) or 0)
        rec["locked_until"] = 0.0
        rec["losses"] = 0
        # Drop a stuck fight so the next /descend starts clean on this floor.
        self.fights.pop(target.id, None)
        self.save()

        who = "Your" if target.id == interaction.user.id else f"{target.display_name}'s"
        if was_locked:
            detail = "24h lockout lifted"
        elif prior_losses:
            detail = f"loss counter cleared ({prior_losses}/{MAX_LOSSES})"
        else:
            detail = "no lockout or losses were active"
        await interaction.response.send_message(
            f"{who} Descent is unlocked — {detail}. "
            f"Still on floor **{rec['floor']}**, monster "
            f"**{rec['monster_index']}/{MONSTERS_PER_FLOOR}**. "
            f"Stats and Max AP untouched.",
            ephemeral=True,
        )

    async def descentboost(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        hp: int = 0,
        attack: int = 0,
        defense: int = 0,
    ):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if hp == 0 and attack == 0 and defense == 0:
            await interaction.response.send_message(
                "Give at least one of `hp`, `attack`, or `defense` (can be negative to remove).",
                ephemeral=True,
            )
            return

        rec = self.record(member.id)
        pts = rec["stat_points"]
        pts["hp"] = max(0, pts.get("hp", 0) + hp)
        pts["atk"] = max(0, pts.get("atk", 0) + attack)
        pts["def"] = max(0, pts.get("def", 0) + defense)
        # Active fight keeps old stats; drop it so the next /descend uses the boost.
        self.fights.pop(member.id, None)
        self.save()

        p_hp, p_atk, p_def = player_stats(rec)
        await interaction.response.send_message(
            f"Boosted **{member.display_name}**'s Descent stats "
            f"(+{hp} HP pts, +{attack} ATK pts, +{defense} DEF pts).\n"
            f"Now: ❤️ **{p_hp}** · ⚔️ **{p_atk}** · 🛡️ **{p_def}** "
            f"(points: hp={pts['hp']}, atk={pts['atk']}, def={pts['def']}).",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Descent(bot))
