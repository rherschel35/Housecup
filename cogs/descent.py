"""
The Descent - a 100-floor solo dungeon crawl.

    /descend        - fight the next monster on your current floor
    /descentstatus  - your floor, stats, and lockout status

Ten zones of ten floors each, one element per zone. Every floor is a
gauntlet of 10 monsters fought in order:

    - Win all 10 -> advance a floor and level up (level = deepest floor
      cleared).
    - Lose a fight -> refight that same monster; you don't lose progress
      on the floor for a single loss.
    - Lose 3 times on the same floor -> locked out of it for 24 hours.
      When the lockout clears you restart that floor at monster #1.
    - Beat monster #5 on any attempt -> pick a stat to raise (HP, Attack,
      or Defense) before continuing. Since a hard floor may take several
      restarted attempts, this is real, repeatable progression, not a
      one-shot level-up - grinding through a wall floor is the point.
    - Every 10th floor (10, 20, ... 100) ends in a boss: tougher, weak to
      two elements instead of one, and worth a floor-clear bonus.

Combat: each round you cast one of 5 named spells, each tied to an
element (the emoji on the button tells you which). A monster's home
element resists that same element (half damage) and is weak to one
other element (double damage) - neither is shown up front, so you learn
each monster's weakness by testing it in the fight, not by reading it
off the card.
"""

import asyncio
import io
import json
import logging
import os
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs import monster_art

log = logging.getLogger("velmora.descent")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "descent_state.json"

# The Descent only runs in this one channel - keeps the fight embeds and
# spam out of every other channel in the server.
DESCENT_CHANNEL_ID = 1553046260401840239

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


# ------------------------------------------------------------- monsters

# (name, emoji, art-kind) - the art-kind picks which silhouette monster_art.py draws
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
# eventually gets there). If floor 4-6 plays easier or harder than
# intended once real people hit it, DEF_B and ATK_B below are the levers
# to move first - small changes here go a long way.

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
    }


def bar(current: int, maximum: int, width: int = 12) -> str:
    maximum = max(maximum, 1)
    filled = max(0, min(width, round(width * current / maximum)))
    return "█" * filled + "░" * (width - filled)


class Fight:
    """One in-progress monster encounter. Purely in-memory - like duels,
    an interrupted fight (e.g. a bot restart) just needs restarting via
    /descend rather than surviving forever."""

    def __init__(self, user_id: int, floor: int, monster_index: int, is_boss: bool,
                 name: str, emoji: str, element: str, kind: str, weak: list[str],
                 m_hp: int, m_atk: int, m_def: int, p_hp: int, p_atk: int, p_def: int):
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
        self.round = 0
        self.log: list[str] = []

    def embed(self, member: discord.Member) -> discord.Embed:
        title = f"{self.emoji} Floor {self.floor} — {self.name}"
        if self.is_boss:
            title += " (Boss)"
        e = discord.Embed(
            title=title,
            description=f"Monster {self.monster_index}/{MONSTERS_PER_FLOOR}",
            color=0x8B5FBF if not self.is_boss else 0xE0A526,
        )
        e.set_image(url="attachment://monster.png")
        e.add_field(name=f"{member.display_name}", value=f"{bar(self.p_hp, self.p_hp_max)} {self.p_hp}/{self.p_hp_max}",
                    inline=False)
        e.add_field(name=self.name, value=f"{bar(self.m_hp, self.m_hp_max)} {self.m_hp}/{self.m_hp_max}",
                    inline=False)
        if self.log:
            e.add_field(name="Last round", value="\n".join(self.log[-2:]), inline=False)
        return e


class ElementButton(discord.ui.Button):
    def __init__(self, element: str):
        super().__init__(label=SPELL_NAME[element], emoji=ELEMENT_EMOJI[element],
                          style=discord.ButtonStyle.secondary)
        self.element = element

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_turn(interaction, self.element)


class FightView(discord.ui.View):
    def __init__(self, cog: "Descent", owner_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        for el in ELEMENTS:
            self.add_item(ElementButton(el))


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
        return self.state["players"].setdefault(str(user_id), blank_record())

    # -------------------------------------------------------- fight setup

    def _make_monster(self, floor: int, monster_index: int):
        zone = zone_for(floor)
        element = zone["element"]
        is_boss = (floor % 10 == 0 and monster_index == MONSTERS_PER_FLOOR)
        if is_boss:
            name, emoji, kind = BOSS_NAMES[floor]
            weak = [WEAK_TO[element], WEAK_TO[WEAK_TO[element]]]
        else:
            name, emoji, kind = random.choice(MONSTER_NAMES[element])
            weak = [WEAK_TO[element]]
        m_hp, m_atk, m_def = monster_stats(floor, is_boss)
        return name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def

    async def _monster_file(self, fight: "Fight") -> discord.File:
        loop = asyncio.get_running_loop()
        png = await loop.run_in_executor(
            None, lambda: monster_art.render(fight.name, fight.element, fight.kind, fight.is_boss))
        return discord.File(io.BytesIO(png), filename="monster.png")

    async def _start_fight(self, interaction: discord.Interaction, rec: dict):
        floor, idx = rec["floor"], rec["monster_index"]
        name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def = self._make_monster(floor, idx)
        p_hp, p_atk, p_def = player_stats(rec)
        fight = Fight(interaction.user.id, floor, idx, is_boss, name, emoji, element, kind, weak,
                      m_hp, m_atk, m_def, p_hp, p_atk, p_def)
        self.fights[interaction.user.id] = fight
        file = await self._monster_file(fight)
        await interaction.response.send_message(embed=fight.embed(interaction.user),
                                                view=FightView(self, interaction.user.id), file=file)

    # ------------------------------------------------------------ combat

    async def take_turn(self, interaction: discord.Interaction, element: str):
        fight = self.fights.get(interaction.user.id)
        if not fight:
            await interaction.followup.send("That fight isn't active anymore - use `/descend` to start again.",
                                            ephemeral=True)
            return
        fight.round += 1
        if element == fight.element:
            mult, note = 0.5, "resisted"
        elif element in fight.weak:
            mult, note = 2.0, "super effective"
        else:
            mult, note = 1.0, None
        dmg = mitigate(fight.p_atk, mult, fight.m_def)
        fight.m_hp -= dmg
        line = (f"{ELEMENT_EMOJI[element]} You cast **{SPELL_NAME[element]}** for **{dmg}**"
               + (f" ({note})" if note else ""))
        fight.log.append(line)

        if fight.m_hp <= 0:
            await self._on_win(interaction, fight)
            return

        back = mitigate(fight.m_atk, 1.0, fight.p_def)
        fight.p_hp -= back
        fight.log.append(f"{fight.emoji} {fight.name} hits back for **{back}**")

        if fight.p_hp <= 0:
            await self._on_loss(interaction, fight)
            return

        await interaction.edit_original_response(embed=fight.embed(interaction.user), view=FightView(self, fight.user_id))

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
            await self._drop_loot(member, zone_for(floor)["element"], FLOOR_CLEAR_GUARANTEED)
            self.save()

            desc = f"**Floor {floor} cleared!**"
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

    async def _on_loss(self, interaction: discord.Interaction, fight: Fight):
        del self.fights[interaction.user.id]
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
    async def descend(self, interaction: discord.Interaction):
        if interaction.channel_id != DESCENT_CHANNEL_ID:
            await interaction.response.send_message(
                f"The Descent can only be played in <#{DESCENT_CHANNEL_ID}>.", ephemeral=True)
            return
        rec = self.record(interaction.user.id)
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
                                                    view=FightView(self, interaction.user.id), file=file)
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
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Descent(bot))
