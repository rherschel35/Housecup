"""
Potions class - brew from materials found across the Garden, the Woods,
the Library, the Dungeons, the Observatory, and the Descent, then drink
what you've made for a temporary edge. No dueling boosts live here by
design: everything a potion does either helps you with beasts or helps
you in the Descent.

    /brew potion:<name>    - spend two ingredients and try to brew it
    /potions               - your Potion Rep, discovered recipes, and satchel of brewed potions
    /drink potion:<name>   - drink a brewed potion to activate it

Brewing is a short 3-round minigame: each round the cauldron does
something and you pick Stir / Add Heat / Let it Simmer. Every recipe has
a hidden correct action per round - guess blind the first time, but once
you've gotten all three right, that recipe's sequence is yours for good.
Ingredients are spent the moment you commit, whether the brew works or not.

Potion Rep rises with every successful brew (more for rarer potions) and
lengthens how long - or how many fights - your potions' effects last.
Rare and legendary recipes also need a high enough Rep to even attempt.
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

log = logging.getLogger("velmora.potions")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "potions_state.json"

# Potions only runs in this one channel.
POTIONS_CHANNEL_ID = 1553253009398693958

# ------------------------------------------------------------- Potion Rep

# (xp threshold, rank name, duration/charge multiplier)
REP_RANKS = [
    (0, "Dabbler", 1.0),
    (50, "Apprentice", 1.25),
    (150, "Adept", 1.5),
    (350, "Master", 1.75),
    (700, "Potioneer", 2.0),
]

TIER_XP = {"common": 8, "uncommon": 12, "rare": 20, "legendary": 35}
TIER_MIN_RANK = {"common": 0, "uncommon": 0, "rare": 2, "legendary": 3}  # index into REP_RANKS
TIER_COLOR = {"common": 0x7FA66A, "uncommon": 0x4F8FC0, "rare": 0x9B59B6, "legendary": 0xE0A526}
TIER_LABEL = {"common": "Common", "uncommon": "Uncommon", "rare": "Rare", "legendary": "Legendary"}

BASE_WINDOW_SECONDS = 600  # 10 minutes, before Rep scaling - for beast-luck potions

# ------------------------------------------------------------ the cauldron

ACTIONS = {"stir": "🥄 Stir", "heat": "🔥 Add Heat", "simmer": "💧 Let it Simmer"}
ROUND_FLAVOR = [
    "The brew turns a cloudy amber and starts to hiss.",
    "It thickens fast and gives off a sharp, unfamiliar smell.",
    "The surface goes still, then shivers once.",
]

# ---------------------------------------------------------------- recipes
#
# effect types:
#   beast_bonus    - a private beast encounter just for the drinker (skew: None/"rare_up"/"choose_place"/"nibbler")
#   atk_mult       - multiplies p_atk for the fight(s)
#   def_mult       - multiplies p_def for the fight(s)
#   ap_bonus       - flat bonus to max AP for the fight(s)
#   heal_mult      - multiplies the fraction Heal restores
#   defend_mult    - overrides how much of the incoming hit Defend blocks
#   rest_no_penalty- Rest no longer adds +10% incoming damage that round
#   hp_bonus       - flat bonus to max/current HP for the fight(s)
#   reveal_weakness- shows the monster's weak element up front
#   boss_dmg_mult  - extra incoming-damage multiplier, boss fights only
#   loot_boost     - bonus material(s) on your next floor clear (one-shot)
#   revive_once    - survive at 1 HP the first time you'd be defeated (one-shot)

RECIPES = {
    "beastcallers_draught": {
        "name": "Beastcaller's Draught", "emoji": "🐾", "tier": "common",
        "ingredients": ["moth_dust", "sunbell"],
        "effect": {"type": "beast_bonus", "skew": None},
        "sequence": ["stir", "heat", "simmer"],
        "blurb": "A private beast encounter, just for you.",
    },
    "whiskers_luck_draught": {
        "name": "Whisker's Luck Draught", "emoji": "🐇", "tier": "uncommon",
        "ingredients": ["hare_whisker", "starthistle"],
        "effect": {"type": "beast_bonus", "skew": "rare_up"},
        "sequence": ["heat", "stir", "stir"],
        "blurb": "A private encounter, skewed toward something rarer.",
    },
    "draught_of_the_hunter": {
        "name": "Draught of the Hunter", "emoji": "🏹", "tier": "rare",
        "ingredients": ["raven_feather", "comet_dust_vial"],
        "effect": {"type": "beast_bonus", "skew": "choose_place"},
        "sequence": ["simmer", "heat", "stir"],
        "blurb": "A private encounter from a place you pick.",
    },
    "nibblers_bait": {
        "name": "Nibbler's Bait", "emoji": "✨", "tier": "rare",
        "ingredients": ["frog_gold", "glowshroom"],
        "effect": {"type": "beast_bonus", "skew": "nibbler"},
        "sequence": ["stir", "simmer", "heat"],
        "blurb": "A private encounter, guaranteed to be a shiny-stealing Nibbler.",
    },
    "draught_of_fury": {
        "name": "Draught of Fury", "emoji": "🔥", "tier": "common",
        "ingredients": ["descent_ember_shard", "emberleaf"],
        "effect": {"type": "atk_mult", "value": 1.15},
        "sequence": ["heat", "heat", "stir"],
        "blurb": "Bonus Strike/Cast damage in your next Descent fight(s).",
    },
    "frost_ward_draught": {
        "name": "Frost Ward Draught", "emoji": "❄️", "tier": "common",
        "ingredients": ["descent_frost_core", "cracked_crystal"],
        "effect": {"type": "def_mult", "value": 1.20},
        "sequence": ["simmer", "stir", "heat"],
        "blurb": "Extra incoming-damage reduction in your next Descent fight(s).",
    },
    "storm_focus_draught": {
        "name": "Storm Focus Draught", "emoji": "⚡", "tier": "uncommon",
        "ingredients": ["descent_storm_relic", "moonstone_chip"],
        "effect": {"type": "ap_bonus", "value": 1},
        "sequence": ["stir", "heat", "heat"],
        "blurb": "+1 max AP in your next Descent fight(s).",
    },
    "draught_of_vigor": {
        "name": "Draught of Vigor", "emoji": "💚", "tier": "uncommon",
        "ingredients": ["descent_light_dust", "dew_diamond"],
        "effect": {"type": "heal_mult", "value": 1.5},
        "sequence": ["simmer", "simmer", "stir"],
        "blurb": "Heal restores more in your next Descent fight(s).",
    },
    "steady_hand_draught": {
        "name": "Steady Hand Draught", "emoji": "🛡️", "tier": "common",
        "ingredients": ["silverleaf", "dewmint"],
        "effect": {"type": "defend_mult", "value": 0.25},
        "sequence": ["stir", "stir", "heat"],
        "blurb": "Defend blocks 75% instead of 50% in your next Descent fight(s).",
    },
    "draught_of_second_wind": {
        "name": "Draught of Second Wind", "emoji": "😮‍💨", "tier": "uncommon",
        "ingredients": ["sleeping_acorn", "wandering_seed"],
        "effect": {"type": "rest_no_penalty", "value": True},
        "sequence": ["heat", "simmer", "stir"],
        "blurb": "Rest no longer leaves you exposed, in your next Descent fight(s).",
    },
    "giants_draught": {
        "name": "Giant's Draught", "emoji": "💪", "tier": "rare",
        "ingredients": ["dragon_scale", "tree_amber"],
        "effect": {"type": "hp_bonus", "value": 25},
        "sequence": ["heat", "stir", "simmer"],
        "blurb": "Extra max HP in your next Descent fight(s).",
    },
    "owls_eye_draught": {
        "name": "Owl's Eye Draught", "emoji": "🦉", "tier": "common",
        "ingredients": ["owl_feather", "moonglass_lens"],
        "effect": {"type": "reveal_weakness", "value": True},
        "sequence": ["simmer", "heat", "heat"],
        "blurb": "See the monster's weak element up front, in your next Descent fight.",
    },
    "ironhide_draught": {
        "name": "Ironhide Draught", "emoji": "🦾", "tier": "uncommon",
        "ingredients": ["shed_fang", "standing_stone_chip"],
        "effect": {"type": "boss_dmg_mult", "value": 0.85, "boss_only": True},
        "sequence": ["stir", "heat", "simmer"],
        "blurb": "Extra damage reduction specifically vs. bosses, next boss fight.",
    },
    "draught_of_fortune": {
        "name": "Draught of Fortune", "emoji": "🍀", "tier": "rare",
        "ingredients": ["snail_pearl", "star_chart_fragment"],
        "effect": {"type": "loot_boost", "value": 2},
        "sequence": ["simmer", "stir", "stir"],
        "blurb": "Boosted material drop on your next floor clear.",
    },
    "phoenix_tears": {
        "name": "Phoenix Tears", "emoji": "🪽", "tier": "legendary",
        "ingredients": ["phoenix_down", "ghost_orchid"],
        "effect": {"type": "revive_once", "value": True},
        "sequence": ["heat", "simmer", "heat"],
        "blurb": "Survive at 1 HP the first time you'd be defeated in the Descent.",
    },
}

BEAST_EFFECT_TYPES = {"beast_bonus"}
FIGHT_EFFECT_TYPES = {"atk_mult", "def_mult", "ap_bonus", "heal_mult", "defend_mult",
                      "rest_no_penalty", "hp_bonus", "reveal_weakness", "boss_dmg_mult"}
ONE_SHOT_TYPES = {"loot_boost", "revive_once"}


def rank_info(xp: int) -> tuple[int, str, float]:
    """(index, name, multiplier) for the highest rank this xp qualifies for."""
    idx = 0
    for i, (threshold, _name, _mult) in enumerate(REP_RANKS):
        if xp >= threshold:
            idx = i
    return idx, REP_RANKS[idx][1], REP_RANKS[idx][2]


def next_rank(idx: int) -> Optional[tuple[int, str]]:
    if idx + 1 >= len(REP_RANKS):
        return None
    return REP_RANKS[idx + 1][0], REP_RANKS[idx + 1][1]


def blank_player() -> dict:
    return {"rep_xp": 0, "discovered": [], "inventory": {}, "active": []}


class BrewView(discord.ui.View):
    def __init__(self, cog: "Potions", owner_id: int, recipe_id: str, correct_so_far: int, round_index: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.owner_id = owner_id
        self.recipe_id = recipe_id
        self.correct_so_far = correct_so_far
        self.round_index = round_index
        for action, label in ACTIONS.items():
            self.add_item(BrewButton(action, label))


class BrewButton(discord.ui.Button):
    def __init__(self, action: str, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        view: BrewView = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("That's not your cauldron - use `/brew` to start your own.",
                                                     ephemeral=True)
            return
        await view.cog.brew_round(interaction, view.recipe_id, view.correct_so_far, view.round_index, self.action)


class Potions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read %s", STATE_PATH)
            state = {}
        state.setdefault("players", {})
        return state

    def save(self):
        tmp = STATE_PATH.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save %s", STATE_PATH)

    def record(self, user_id: int) -> dict:
        rec = self.state["players"].setdefault(str(user_id), blank_player())
        rec.setdefault("active", [])
        return rec

    # -------------------------------------------------------- ingredients

    def items_line(self, item_ids: list[str]) -> str:
        world = self.bot.get_cog("World")
        if not world:
            return " + ".join(item_ids)
        return " + ".join(world.world.item_line(i) for i in item_ids)

    async def _has_ingredients(self, member, ingredients: list[str]) -> bool:
        world = self.bot.get_cog("World")
        if not world:
            return False
        student = world.student(member)
        have = student.get("items", {})
        return all(have.get(i, 0) >= 1 for i in ingredients)

    async def _consume_ingredients(self, member, ingredients: list[str]) -> bool:
        world = self.bot.get_cog("World")
        if not world:
            return False
        async with world.lock:
            student = world.student(member)
            have = student.get("items", {})
            if any(have.get(i, 0) < 1 for i in ingredients):
                return False
            for i in ingredients:
                world.world.take(student, i)
            world.save()
        return True

    # ------------------------------------------------------------- brewing

    @app_commands.command(name="brew", description="Spend two ingredients and try to brew a potion.")
    @app_commands.choices(potion=[
        app_commands.Choice(name=f"{r['emoji']} {r['name']} ({TIER_LABEL[r['tier']]})", value=key)
        for key, r in RECIPES.items()
    ])
    async def brew(self, interaction: discord.Interaction, potion: app_commands.Choice[str]):
        if interaction.channel_id != POTIONS_CHANNEL_ID:
            await interaction.response.send_message(
                f"Potions can only be brewed in <#{POTIONS_CHANNEL_ID}>.", ephemeral=True)
            return
        recipe_id = potion.value
        recipe = RECIPES[recipe_id]
        rec = self.record(interaction.user.id)
        idx, rank_name, _mult = rank_info(rec["rep_xp"])

        if idx < TIER_MIN_RANK[recipe["tier"]]:
            need = REP_RANKS[TIER_MIN_RANK[recipe["tier"]]][1]
            await interaction.response.send_message(
                f"**{recipe['name']}** is a {TIER_LABEL[recipe['tier']].lower()} recipe - you need to be at least "
                f"**{need}** in Potion Rep first (you're **{rank_name}**). Use `/potions` to check your progress.",
                ephemeral=True)
            return

        if not await self._has_ingredients(interaction.user, recipe["ingredients"]):
            await interaction.response.send_message(
                f"You need {self.items_line(recipe['ingredients'])} to brew that.", ephemeral=True)
            return

        if not await self._consume_ingredients(interaction.user, recipe["ingredients"]):
            await interaction.response.send_message("Something moved in your satchel - try again.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"{recipe['emoji']} Brewing {recipe['name']}",
            description=f"{ROUND_FLAVOR[0]}\n\nWhat do you do?",
            color=TIER_COLOR[recipe["tier"]],
        )
        embed.set_footer(text="Round 1 of 3")
        await interaction.response.send_message(embed=embed, view=BrewView(self, interaction.user.id, recipe_id, 0, 0))

    async def brew_round(self, interaction: discord.Interaction, recipe_id: str, correct_so_far: int,
                         round_index: int, action: str):
        recipe = RECIPES[recipe_id]
        was_correct = recipe["sequence"][round_index] == action
        correct_so_far += 1 if was_correct else 0
        feedback = ("✅ The color settles - good call." if was_correct
                   else "⚠️ It spits and darkens - that wasn't it.")

        if round_index + 1 < 3:
            embed = discord.Embed(
                title=f"{recipe['emoji']} Brewing {recipe['name']}",
                description=f"{feedback}\n\n{ROUND_FLAVOR[round_index + 1]}\n\nWhat do you do?",
                color=TIER_COLOR[recipe["tier"]],
            )
            embed.set_footer(text=f"Round {round_index + 2} of 3")
            await interaction.response.edit_message(
                embed=embed, view=BrewView(self, interaction.user.id, recipe_id, correct_so_far, round_index + 1))
            return

        await self._finish_brew(interaction, recipe_id, correct_so_far, feedback)

    async def _finish_brew(self, interaction: discord.Interaction, recipe_id: str, correct: int, last_feedback: str):
        recipe = RECIPES[recipe_id]
        rec = self.record(interaction.user.id)

        if correct < 2:
            embed = discord.Embed(
                title=f"{recipe['emoji']} The cauldron curdles",
                description=f"{last_feedback}\n\nThe brew is ruined - no potion this time. The ingredients are gone.",
                color=0xC0392B,
            )
            await interaction.response.edit_message(embed=embed, view=None)
            return

        perfect = correct == 3
        xp = TIER_XP[recipe["tier"]]
        if perfect:
            xp = round(xp * 1.5)
        rec["rep_xp"] += xp
        rec["inventory"][recipe_id] = rec["inventory"].get(recipe_id, 0) + 1

        newly_discovered = recipe_id not in rec["discovered"]
        if perfect and newly_discovered:
            rec["discovered"].append(recipe_id)
        self.save()

        lines = [last_feedback, "", f"**{'Perfect brew!' if perfect else 'Passable brew.'}** "
                f"You've got a {recipe['emoji']} **{recipe['name']}** in your satchel now.",
                f"+{xp} Potion Rep."]
        if perfect and newly_discovered:
            lines.append(f"You've cracked the recipe - the correct sequence for **{recipe['name']}** is yours for good.")
        embed = discord.Embed(title=f"{recipe['emoji']} Brew complete", description="\n".join(lines),
                              color=TIER_COLOR[recipe["tier"]])
        await interaction.response.edit_message(embed=embed, view=None)

    # -------------------------------------------------------------- drink

    @app_commands.command(name="drink", description="Drink a brewed potion from your satchel to activate it.")
    @app_commands.choices(potion=[
        app_commands.Choice(name=f"{r['emoji']} {r['name']} ({TIER_LABEL[r['tier']]})", value=key)
        for key, r in RECIPES.items()
    ])
    async def drink(self, interaction: discord.Interaction, potion: app_commands.Choice[str]):
        if interaction.channel_id != POTIONS_CHANNEL_ID:
            await interaction.response.send_message(
                f"Potions can only be drunk in <#{POTIONS_CHANNEL_ID}>.", ephemeral=True)
            return
        recipe_id = potion.value
        recipe = RECIPES[recipe_id]
        rec = self.record(interaction.user.id)

        if rec["inventory"].get(recipe_id, 0) < 1:
            await interaction.response.send_message(
                f"You don't have a {recipe['name']} brewed. Use `/brew` first.", ephemeral=True)
            return

        effect = recipe["effect"]
        if effect["type"] in BEAST_EFFECT_TYPES:
            await self._drink_beast_potion(interaction, recipe_id, effect)
            return

        rec["inventory"][recipe_id] -= 1
        if rec["inventory"][recipe_id] <= 0:
            del rec["inventory"][recipe_id]

        _idx, _name, mult = rank_info(rec["rep_xp"])
        if effect["type"] in ONE_SHOT_TYPES:
            charges = 1
        else:
            charges = max(1, round(mult))

        entry = {"effect": effect["type"], "value": effect["value"], "charges": charges}
        if effect.get("boss_only"):
            entry["boss_only"] = True
        rec["active"].append(entry)
        self.save()

        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{recipe['emoji']} You drink the {recipe['name']}",
                description=f"{recipe['blurb']} ({charges} fight{'s' if charges != 1 else ''} in the Descent).",
                color=TIER_COLOR[recipe["tier"]],
            ))

    async def _drink_beast_potion(self, interaction: discord.Interaction, recipe_id: str, effect: dict):
        beasts = self.bot.get_cog("Beasts")
        world = self.bot.get_cog("World")
        if not beasts or not world:
            await interaction.response.send_message("The satchels are out of reach right now.", ephemeral=True)
            return

        rec = self.record(interaction.user.id)
        skew = effect.get("skew")
        if skew == "choose_place":
            await interaction.response.send_message(
                "Which place should Draught of the Hunter draw from?",
                view=HunterPlaceView(self, interaction.user.id, recipe_id), ephemeral=True)
            return

        rec["inventory"][recipe_id] -= 1
        if rec["inventory"][recipe_id] <= 0:
            del rec["inventory"][recipe_id]
        self.save()
        await self._grant_private_encounter(interaction, recipe_id, skew, place=None)

    async def _grant_private_encounter(self, interaction: discord.Interaction, recipe_id: str,
                                       skew: Optional[str], place: Optional[str]):
        recipe = RECIPES[recipe_id]
        beasts = self.bot.get_cog("Beasts")
        rec = self.record(interaction.user.id)
        _idx, _name, mult = rank_info(rec["rep_xp"])
        window = round(BASE_WINDOW_SECONDS * mult)

        key = self._pick_private_beast(beasts, skew, place)
        if key is None:
            msg = "Nothing stirs for you right now - try again in a bit."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return

        expires = time.time() + window
        b = beasts.beasts[key]
        embed = discord.Embed(
            title=f"{b['emoji']} A {b['name']} appears - just for you",
            description=(f"{b['sighting']}\n\n**It wants:** {beasts.items_line(b['wants'])}\n\n"
                         f"Only you can see this. Approach before it slips away <t:{int(expires)}:R>."),
            color=discord.Color.from_rgb(46, 139, 68),
        )
        view = PrivateEncounterView(self, beasts, interaction.user.id, key, expires)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    def _pick_private_beast(self, beasts, skew: Optional[str], place: Optional[str]) -> Optional[str]:
        pool = dict(beasts.beasts)
        if place:
            pool = {k: b for k, b in pool.items() if b["place"] == place}
        if skew == "nibbler":
            pool = {k: b for k, b in pool.items() if k.endswith("_nibbler")}
        if not pool:
            return None
        if skew == "rare_up":
            weights = {"common": 20, "uncommon": 35, "rare": 30, "legendary": 15}
        else:
            weights = {"common": 50, "uncommon": 30, "rare": 15, "legendary": 5}
        rarities = [r for r in weights if any(b["rarity"] == r for b in pool.values())]
        rarity = random.choices(rarities, weights=[weights[r] for r in rarities], k=1)[0]
        choices = sorted(k for k, b in pool.items() if b["rarity"] == rarity)
        return random.choice(choices)

    # -------------------------------------------------------------- info

    @app_commands.command(name="potions", description="Your Potion Rep, discovered recipes, and brewed potions.")
    async def potions_cmd(self, interaction: discord.Interaction):
        if interaction.channel_id != POTIONS_CHANNEL_ID:
            await interaction.response.send_message(
                f"Potions only runs in <#{POTIONS_CHANNEL_ID}>.", ephemeral=True)
            return
        rec = self.record(interaction.user.id)
        idx, name, mult = rank_info(rec["rep_xp"])
        nxt = next_rank(idx)

        desc = [f"**{name}** • {rec['rep_xp']} Rep", f"Buff duration/charges ×{mult:g}"]
        if nxt:
            desc.append(f"{nxt[0] - rec['rep_xp']} Rep to **{nxt[1]}**")

        embed = discord.Embed(title=f"{interaction.user.display_name}'s Potions", description="\n".join(desc),
                              color=0x6C5CE7)

        inv_lines = [f"{RECIPES[k]['emoji']} {RECIPES[k]['name']} ×{n}" for k, n in rec["inventory"].items() if n > 0]
        embed.add_field(name="🧪 Satchel", value="\n".join(inv_lines) if inv_lines else "*Nothing brewed yet.*",
                        inline=False)

        world = self.bot.get_cog("World")
        have = world.student(interaction.user).get("items", {}) if world else {}

        known_lines = []
        for key, r in RECIPES.items():
            gated = idx < TIER_MIN_RANK[r["tier"]]
            known = key in rec["discovered"]
            tag = "🔓 known" if known else ("🔒 locked" if gated else "❔ undiscovered")
            ready = all(have.get(i, 0) >= 1 for i in r["ingredients"])
            readiness = " • 🟢 you have everything for this" if ready else ""
            known_lines.append(f"{r['emoji']} **{r['name']}** ({TIER_LABEL[r['tier']]}) - {tag}{readiness}\n"
                              f"　{self.items_line(r['ingredients'])} - {r['blurb']}")
        embed.add_field(name="📖 Recipes (🟢 = you can brew this right now)", value="\n".join(known_lines),
                        inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------- Descent hooks

    def consume_for_fight(self, user_id: int, is_boss: bool) -> dict:
        """Called once when a real (non-practice) Descent fight starts.
        Pops and returns the combined modifiers from active potions,
        decrementing charges - boss-only effects are left untouched
        (and unspent) on non-boss fights."""
        rec = self.record(user_id)
        mods: dict = {}
        remaining = []
        for entry in rec["active"]:
            if entry["effect"] not in FIGHT_EFFECT_TYPES:
                remaining.append(entry)
                continue
            if entry.get("boss_only") and not is_boss:
                remaining.append(entry)
                continue
            mods[entry["effect"]] = entry["value"]
            entry["charges"] -= 1
            if entry["charges"] > 0:
                remaining.append(entry)
        rec["active"] = remaining
        self.save()
        return mods

    def consume_loot_boost(self, user_id: int) -> Optional[int]:
        """Called on a floor clear - pops one loot_boost charge if active."""
        rec = self.record(user_id)
        for i, entry in enumerate(rec["active"]):
            if entry["effect"] == "loot_boost":
                value = entry["value"]
                rec["active"].pop(i)
                self.save()
                return value
        return None

    def has_revive(self, user_id: int) -> bool:
        rec = self.record(user_id)
        return any(e["effect"] == "revive_once" for e in rec["active"])

    def consume_revive(self, user_id: int):
        rec = self.record(user_id)
        rec["active"] = [e for e in rec["active"] if e["effect"] != "revive_once"]
        self.save()


class HunterPlaceView(discord.ui.View):
    def __init__(self, cog: Potions, owner_id: int, recipe_id: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.owner_id = owner_id
        self.recipe_id = recipe_id
        places = {"garden": "🌿 Garden", "library": "📚 Library", "dungeons": "🕯️ Dungeons",
                 "forbidden_woods": "🌲 Forbidden Woods", "observatory": "🔭 Observatory"}
        for place_key, label in places.items():
            self.add_item(HunterPlaceButton(place_key, label))


class HunterPlaceButton(discord.ui.Button):
    def __init__(self, place_key: str, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.place_key = place_key

    async def callback(self, interaction: discord.Interaction):
        view: HunterPlaceView = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("That's not your potion.", ephemeral=True)
            return
        rec = view.cog.record(interaction.user.id)
        rec["inventory"][view.recipe_id] -= 1
        if rec["inventory"][view.recipe_id] <= 0:
            del rec["inventory"][view.recipe_id]
        view.cog.save()
        await interaction.response.edit_message(content=f"Drawing from the {self.label}...", embed=None, view=None)
        await view.cog._grant_private_encounter(interaction, view.recipe_id, "choose_place", place=self.place_key)


class PrivateEncounterView(discord.ui.View):
    def __init__(self, cog: Potions, beasts_cog, owner_id: int, beast_key: str, expires: float):
        super().__init__(timeout=max(1, int(expires - time.time())))
        self.cog = cog
        self.beasts_cog = beasts_cog
        self.owner_id = owner_id
        self.beast_key = beast_key
        self.expires = expires
        self.add_item(ApproachButton())


class ApproachButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Approach", emoji="🐾", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        view: PrivateEncounterView = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("This encounter isn't yours.", ephemeral=True)
            return
        if time.time() >= view.expires:
            await interaction.response.edit_message(
                content="It slipped away.", embed=None, view=None)
            return

        beasts = view.beasts_cog
        world = view.cog.bot.get_cog("World")
        b = beasts.beasts[view.beast_key]
        async with world.lock:
            student = world.student(interaction.user)
            have = student.get("items", {})
            missing = [i for i in b["wants"] if have.get(i, 0) < 1]
            if missing:
                await interaction.response.send_message(
                    f"The {b['name']} sniffs at you and waits. It wants {beasts.items_line(b['wants'])} - "
                    f"you're missing {beasts.items_line(missing)}.", ephemeral=True)
                return
            for i in b["wants"]:
                world.world.take(student, i)
            world.save()
        out = beasts.befriend(interaction.user, view.beast_key, time.time())
        beasts.save()

        from cogs.store import HOUSES
        lines = [f"{b['emoji']} You offer {beasts.items_line(b['wants'])} and befriend the **{b['name']}**!",
                f"*{b['desc']}*"]
        if out["first"]:
            if out["points"]:
                h = HOUSES.get(out["house"], {})
                lines.append(f"+{out['points']} to {h.get('emoji', '')} {h.get('name', out['house'])}"
                             + (" (daily beast points reached)" if out["capped"] else ""))
        else:
            lines.append(f"*You've befriended a {b['name']} before - it's added to your bestiary again.*")
        lines += out["notes"]
        await interaction.response.edit_message(
            embed=discord.Embed(description="\n".join(lines), color=0x2E8B44), view=None)
        adorn = view.cog.bot.get_cog("Adornments")
        if adorn:
            try:
                await adorn.check_member(interaction.user)
            except Exception:
                log.exception("Gear check after a private encounter befriending failed")


async def setup(bot: commands.Bot):
    await bot.add_cog(Potions(bot))
