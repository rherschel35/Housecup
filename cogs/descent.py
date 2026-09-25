"""
The Descent - a 100-floor solo dungeon crawl.

    /descend             - fight the next monster on your current Descent floor
    /descend floor:<n>   - replay a floor you've already cleared, for practice/loot
    /descentstatus        - your floor, stats, AP, and lockout status

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

Floor replay: once you've cleared a floor, `/descend floor:<n>` lets you
refight a single monster from it any time (not boss floors) - for loot,
or to grind. Loot always has a chance to drop. A shot at a stat point,
though, only exists if that floor is in your current zone or the zone
right before it - grinding floor 1 while you're on floor 85 nets you
items, not power.
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

log = logging.getLogger("velmora.descent")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "descent_state.json"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "monster_art_assets"

# The Descent only runs in this one channel - keeps the fight embeds and
# spam out of every other channel in the server.
DESCENT_CHANNEL_ID = 1553089612849877053

# ------------------------------------------------------------- elements

ELEMENTS = ["fire", "ice", "lightning", "poison", "light"]
ELEMENT_EMOJI = {"fire": "🔥", "ice": "❄️", "lightning": "⚡", "poison": "☠️", "light": "✨"}
ELEMENT_NAME = {"fire": "Fire", "ice": "Ice", "lightning": "Lightning", "poison": "Poison", "light": "Light"}
# the attack spell each element is cast as - shown on the buttons and in
# the round log, with the element emoji doing the job of telling players
# which element it actually is
SPELL_NAME = {"fire": "Incedio", "ice": "Glacius", "lightning": "Fulgur",
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
    40: ("Stormcaller Vessel", "⚡", "orb"),
    50: ("The Hollow Saint", "✨", "humanoid"),
    60: ("The Sovereign, Reborn", "🍄", "blob"),
    70: ("Ashgrave, Undying", "🔥", "humanoid"),
    80: ("The Rime Empress, Unbound", "❄️", "humanoid"),
    90: ("Vessel of the Last Storm", "⚡", "orb"),
    100: ("The Hollow Saint, Ascendant", "✨", "humanoid"),
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
BOSS_IMAGE = {
    10: "Floor_10_The_Bloated_Sovereign.png",
    20: "Floor_20_Cinderlord_Ashgrave.png",
    30: "Floor_30_The_Rime_Empress.png",
    40: "Floor_40_Stormcaller_Vessel.png",
    50: "Floor_50_The_Hollow_Saint.png",
    60: "Floor_60_The_Sovereign_Reborn.png",
    70: "Floor_70_Ashgrave_Undying.png",
    80: "Floor_80_The_Rime_Empress_Unbound.png",
    90: "Floor_90_Vessel_of_the_Last_Storm.png",
    100: "Floor_100_The_Hollow_Saint_Ascendant.png",
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

MONSTER_DROP_CHANCE = 0.25   # any regular win
FLOOR_CLEAR_GUARANTEED = 2   # material given on a full floor clear
PRACTICE_STATUP_CHANCE = 0.20  # in-window practice win: chance at a stat point

# ----------------------------------------------------------- difficulty
#
# Long HP-attrition fights are unforgiving: even a small, steady edge in
# damage-per-round compounds hard over a dozen rounds, so this curve is
# naturally "mostly a sure win" or "mostly a sure loss" rather than a
# gentle slope. These constants are tuned (by simulation, not just guessed)
# so floors 1-3 are close to a guaranteed clear, floor 4 starts costing you
# real losses, and floors 7+ demand the extra stat points only repeated,
# failed attempts actually bank - i.e. a genuine grind, never a hard,
# un-crossable wall (damage never floors below 1, so persistence always
# eventually gets there). Adding AP actions and two-element floors makes
# every floor harder than this curve alone implies - expect to retune
# after real playtesting.

def monster_stats(floor: int, is_boss: bool) -> tuple[int, int, int]:
    hp = 24 + floor * 11
    atk = 5 + floor * 1.7
    df = 2 + floor * 1.1
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
DEFEND_AP_COST = 1
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
        "pending_statup": False,
        "max_ap": STARTING_MAX_AP,
    }


def bar(current: int, maximum: int, width: int = 12) -> str:
    maximum = max(maximum, 1)
    filled = max(0, min(width, round(width * current / maximum)))
    return "█" * filled + "░" * (width - filled)


def eligible_for_statup(rec: dict, practiced_floor: int) -> bool:
    """A practice win only has a shot at a stat point if it's in your
    current zone or the zone right before it - older floors are loot-only."""
    return zone_index(practiced_floor) >= zone_index(rec["floor"]) - 1


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

    def embed(self, member: discord.Member) -> discord.Embed:
        title = f"{self.emoji} Floor {self.floor} — {self.name}"
        if self.is_boss:
            title += " (Boss)"
        elif self.is_practice:
            title += " (Practice)"
        e = discord.Embed(
            title=title,
            description=f"Monster {self.monster_index}/{MONSTERS_PER_FLOOR}" if not self.is_practice
                        else "Practice fight — no floor progress at stake",
            color=0x8B5FBF if not self.is_boss else 0xE0A526,
        )
        e.set_image(url="attachment://monster.png")
        e.add_field(
            name=f"{member.display_name}",
            value=(f"{bar(self.p_hp, self.p_hp_max)} {self.p_hp}/{self.p_hp_max}\n"
                   f"⚡ {self.ap}/{self.ap_max} AP"),
            inline=False,
        )
        e.add_field(name=self.name, value=f"{bar(self.m_hp, self.m_hp_max)} {self.m_hp}/{self.m_hp_max}",
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


class StatButton(discord.ui.Button):
    def __init__(self, stat: str, label: str, emoji: str):
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.primary)
        self.stat = stat

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your stat point to spend.", ephemeral=True)
            return
        await self.view.cog.pick_stat(interaction, self.stat)


class StatUpView(discord.ui.View):
    def __init__(self, cog: "Descent", owner_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.owner_id = owner_id
        self.add_item(StatButton("hp", "Max HP", "❤️"))
        self.add_item(StatButton("atk", "Attack", "⚔️"))
        self.add_item(StatButton("def", "Defense", "🛡️"))


class Descent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self.fights: dict[int, Fight] = {}

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
        return rec

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

    async def _start_fight(self, interaction: discord.Interaction, rec: dict):
        floor, idx = rec["floor"], rec["monster_index"]
        name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def = self._make_monster(floor, idx)
        p_hp, p_atk, p_def = player_stats(rec)
        fight = Fight(interaction.user.id, floor, idx, is_boss, name, emoji, element, kind, weak,
                      m_hp, m_atk, m_def, p_hp, p_atk, p_def, ap_max=rec["max_ap"])
        self.fights[interaction.user.id] = fight
        file = await self._monster_file(fight)
        await interaction.response.send_message(embed=fight.embed(interaction.user),
                                                view=FightView(self, fight), file=file)

    async def _start_practice_fight(self, interaction: discord.Interaction, rec: dict, floor: int):
        name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def = self._make_monster(floor, 1)
        p_hp, p_atk, p_def = player_stats(rec)
        fight = Fight(interaction.user.id, floor, 0, False, name, emoji, element, kind, weak,
                      m_hp, m_atk, m_def, p_hp, p_atk, p_def, ap_max=rec["max_ap"], is_practice=True)
        self.fights[interaction.user.id] = fight
        file = await self._monster_file(fight)
        await interaction.response.send_message(embed=fight.embed(interaction.user),
                                                view=FightView(self, fight), file=file)

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
            healed = round(missing * HEAL_FRACTION)
            fight.p_hp = min(fight.p_hp_max, fight.p_hp + healed)
            lines.append(f"💚 You heal for **{healed}** (-{HEAL_AP_COST} AP, you're exposed)")
        elif action == "defend":
            if fight.ap < DEFEND_AP_COST:
                await interaction.followup.send("Not enough AP to defend.", ephemeral=True)
                return
            fight.ap -= DEFEND_AP_COST
            counter_mult = DEFEND_DMG_MULT
            lines.append(f"🛡️ You brace to defend (-{DEFEND_AP_COST} AP)")
        elif action == "rest":
            fight.ap = fight.ap_max
            counter_mult = REST_DMG_MULT
            lines.append("😮‍💨 You catch your breath - AP fully restored (unguarded)")
        else:
            return

        if dmg_dealt:
            fight.m_hp -= dmg_dealt
        fight.log.extend(lines)

        if fight.m_hp <= 0:
            await self._on_win(interaction, fight)
            return

        back = mitigate(fight.m_atk, counter_mult, fight.p_def)
        fight.p_hp -= back
        tag = " (reduced)" if counter_mult < 1 else (" (extra!)" if counter_mult > 1 else "")
        fight.log.append(f"{fight.emoji} {fight.name} hits back for **{back}**{tag}")

        if fight.p_hp <= 0:
            await self._on_loss(interaction, fight)
            return

        fight.ap = min(fight.ap_max, fight.ap + AP_REGEN_PER_TURN)
        await interaction.edit_original_response(embed=fight.embed(interaction.user), view=FightView(self, fight))

    async def _drop_loot(self, member: discord.Member, element: str, n: int = 1):
        world_cog = self.bot.get_cog("World")
        if not world_cog:
            return
        item_id = ZONE_ITEM[element]
        if item_id not in world_cog.world.items:
            return
        async with world_cog.lock:
            student = world_cog.student(member)
            world_cog.world.give(student, item_id, n)
            world_cog.save()

    async def _drop_boss_item(self, member: discord.Member):
        world_cog = self.bot.get_cog("World")
        if not world_cog or BOSS_ITEM not in world_cog.world.items:
            return
        async with world_cog.lock:
            student = world_cog.student(member)
            world_cog.world.give(student, BOSS_ITEM, 1)
            world_cog.save()

    async def _on_win(self, interaction: discord.Interaction, fight: Fight):
        del self.fights[interaction.user.id]
        rec = self.record(interaction.user.id)
        member = interaction.user

        if fight.is_practice:
            await self._on_practice_win(interaction, fight, rec, member)
            return

        if fight.is_boss:
            await self._drop_boss_item(member)
        elif random.random() < MONSTER_DROP_CHANCE:
            await self._drop_loot(member, fight.element)

        cleared_index = fight.monster_index
        if cleared_index >= MONSTERS_PER_FLOOR:
            # floor cleared
            floor = rec["floor"]
            rec["highest_cleared"] = max(rec["highest_cleared"], floor)
            rec["floor"] = min(floor + 1, MAX_FLOOR)
            rec["monster_index"] = 1
            rec["losses"] = 0
            rec["max_ap"] += 1
            await self._drop_loot(member, zone_for(floor)["element"], FLOOR_CLEAR_GUARANTEED)
            self.save()

            desc = f"**Floor {floor} cleared!** Max AP is now **{rec['max_ap']}**."
            if fight.is_boss:
                store = self.bot.get_cog("Store")
                house = store.member_house(member) if store else None
                if house:
                    store.record(house=house, delta=5, actor_id=self.bot.user.id if self.bot.user else 0,
                                 target_id=member.id, reason=f"Descent: floor {floor} boss defeated")
                desc += f"\n🏆 You defeated **{fight.name}** and earned House {house or 'points (unassigned)'} 5 points."
            if floor >= MAX_FLOOR:
                desc += "\n\n👑 **The Descent is complete.** There is nothing further down."
            embed = discord.Embed(title=f"{fight.emoji} Victory!", description=desc, color=0x2ECC71)
            await interaction.edit_original_response(embed=embed, view=None)
            return

        rec["monster_index"] = cleared_index + 1
        self.save()

        embed = discord.Embed(
            title=f"{fight.emoji} Victory!",
            description=f"**{fight.name}** falls. On to monster {rec['monster_index']}/{MONSTERS_PER_FLOOR}.",
            color=0x2ECC71,
        )
        if cleared_index == STATUP_AT_MONSTER:
            rec["pending_statup"] = True
            self.save()
            embed.description += "\n\nYou've earned a stat point - pick where it goes."
            await interaction.edit_original_response(embed=embed, view=StatUpView(self, interaction.user.id))
        else:
            embed.description += "\nUse `/descend` to keep going."
            await interaction.edit_original_response(embed=embed, view=None)

    async def _on_practice_win(self, interaction: discord.Interaction, fight: Fight, rec: dict, member: discord.Member):
        got_loot = random.random() < MONSTER_DROP_CHANCE
        if got_loot:
            await self._drop_loot(member, fight.element)

        desc = f"**{fight.name}** falls. This was a practice fight - your floor progress hasn't changed."
        desc += "\nA material dropped!" if got_loot else "\nNo material dropped this time."

        if eligible_for_statup(rec, fight.floor) and not rec.get("pending_statup") \
                and random.random() < PRACTICE_STATUP_CHANCE:
            rec["pending_statup"] = True
            self.save()
            desc += "\n\nThis one was close enough to your depth to sharpen you too - pick a stat to raise."
            embed = discord.Embed(title=f"{fight.emoji} Practice victory!", description=desc, color=0x2ECC71)
            await interaction.edit_original_response(embed=embed, view=StatUpView(self, interaction.user.id))
            return

        if not eligible_for_statup(rec, fight.floor):
            desc += "\n\nThis floor is far enough behind your progress that it's loot-only now - no stat gains."
        self.save()
        embed = discord.Embed(title=f"{fight.emoji} Practice victory!", description=desc, color=0x2ECC71)
        await interaction.edit_original_response(embed=embed, view=None)

    async def _on_loss(self, interaction: discord.Interaction, fight: Fight):
        del self.fights[interaction.user.id]

        if fight.is_practice:
            embed = discord.Embed(
                title="Defeated",
                description=f"**{fight.name}** gets the better of you. It was only practice - no penalty, "
                            f"no lockout. Use `/descend` to try again.",
                color=0xC0392B,
            )
            await interaction.edit_original_response(embed=embed, view=None)
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
            desc = f"**{fight.name}** finishes you off. Use `/descend` to try that monster again."
        embed = discord.Embed(title="Defeated", description=desc, color=0xC0392B)
        await interaction.edit_original_response(embed=embed, view=None)

    async def pick_stat(self, interaction: discord.Interaction, stat: str):
        rec = self.record(interaction.user.id)
        if not rec.get("pending_statup"):
            await interaction.response.send_message("Nothing to spend right now.", ephemeral=True)
            return
        rec["stat_points"][stat] += 1
        rec["pending_statup"] = False
        self.save()
        label = {"hp": "Max HP", "atk": "Attack", "def": "Defense"}[stat]
        await interaction.response.edit_message(
            embed=discord.Embed(title="Stat raised", description=f"**+1 {label}**. Use `/descend` to keep going.",
                                color=0x6C5CE7),
            view=None,
        )

    # ----------------------------------------------------------- commands

    @app_commands.command(name="descend", description="Fight the next monster on your current Descent floor.")
    @app_commands.describe(floor="Replay a floor you've already cleared, for practice/loot (not boss floors).")
    async def descend(self, interaction: discord.Interaction, floor: Optional[int] = None):
        if interaction.channel_id != DESCENT_CHANNEL_ID:
            await interaction.response.send_message(
                f"The Descent can only be played in <#{DESCENT_CHANNEL_ID}>.", ephemeral=True)
            return

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
            await self._start_practice_fight(interaction, rec, floor)
            return

        now = time.time()
        if rec["locked_until"] > now:
            left = int(rec["locked_until"] - now)
            h, m = left // 3600, (left % 3600) // 60
            await interaction.response.send_message(
                f"Floor {rec['floor']} is locked after 3 losses. Try again in {h}h {m}m.", ephemeral=True)
            return
        if rec.get("pending_statup"):
            await interaction.response.send_message(
                "Pick your stat point first.", embed=discord.Embed(
                    title="Choose a stat to raise", color=0x6C5CE7), view=StatUpView(self, interaction.user.id))
            return
        if interaction.user.id in self.fights:
            fight = self.fights[interaction.user.id]
            file = await self._monster_file(fight)
            await interaction.response.send_message(embed=fight.embed(interaction.user),
                                                    view=FightView(self, fight), file=file)
            return
        if rec["floor"] > MAX_FLOOR:
            await interaction.response.send_message("You've already conquered the Descent.", ephemeral=True)
            return
        await self._start_fight(interaction, rec)

    @app_commands.command(name="descentstatus", description="Your Descent progress: floor, stats, and lockout.")
    async def descentstatus(self, interaction: discord.Interaction):
        if interaction.channel_id != DESCENT_CHANNEL_ID:
            await interaction.response.send_message(
                f"The Descent can only be played in <#{DESCENT_CHANNEL_ID}>.", ephemeral=True)
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

        embed = discord.Embed(title=f"{interaction.user.display_name}'s Descent", description="\n".join(desc),
                              color=0x6C5CE7)
        embed.add_field(name="❤️ Max HP", value=str(p_hp))
        embed.add_field(name="⚔️ Attack", value=str(p_atk))
        embed.add_field(name="🛡️ Defense", value=str(p_def))
        embed.add_field(name="⚡ Max AP", value=str(rec["max_ap"]))
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Descent(bot))
