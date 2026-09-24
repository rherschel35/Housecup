"""
Familiars. Adopt one, name it, care for it every day, and it brings things
back.

    /familiar [choose] [name]   - adopt one for good, name or rename it,
                                   or just see the one you have
    /feed                       - once a day, raises friendship
    /pet                        - once a day, raises friendship
    /play                       - once a day, raises friendship
    /scout                      - once a day; sends it out into Velmora

Three familiars to choose from - a Salamander, a Raven, a Fox - one per
person, for good, like a patronus. Feed/pet/play each raise friendship
once per day no matter how many times you run them; the friendship you've
built decides how often /scout comes back with something, and how good it
is. Scouted finds land in your satchel (the same one /forage fills), so a
devoted familiar is worth having even outside the Garden.
"""

import json
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.world_engine import today

log = logging.getLogger("velmora.familiars")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "familiars.json"

FRIENDSHIP_PER_ACTION = 2      # feed, pet, play each give this once a day
FRIENDSHIP_CAP = 100
SCOUT_POINT_CHANCE = 0.12      # on top of an item, a small chance of bonus house points
NAME_MAX_LEN = 24

WARM = 0xD98A4B

FAMILIARS = {
    "salamander": {"label": "Salamander", "emoji": "🦎", "article": "a"},
    "raven":      {"label": "Raven",      "emoji": "🐦", "article": "a"},
    "fox":        {"label": "Fox",        "emoji": "🦊", "article": "a"},
}

# (friendship floor, tier name, chance /scout finds anything, rarity weights)
TIERS = [
    (0,  "Wary",         0.55, {"common": 90, "uncommon": 10}),
    (10, "Curious",      0.70, {"common": 70, "uncommon": 25, "rare": 5}),
    (25, "Fond",         0.80, {"common": 50, "uncommon": 32, "rare": 16, "very_rare": 2}),
    (50, "Devoted",      0.90, {"common": 30, "uncommon": 34, "rare": 28, "very_rare": 8}),
    (80, "Inseparable",  0.97, {"common": 15, "uncommon": 30, "rare": 35, "very_rare": 18, "legendary": 2}),
]

FEED_LINES = {
    "salamander": [
        "{fam} snaps up the warm coal you offer without singeing you - progress.",
        "You feed {fam} a handful of dried peppers. Smoke curls happily from its nose.",
    ],
    "raven": [
        "{fam} inspects the crust you offer from three angles before accepting it, formally.",
        "You toss {fam} a strip of dried meat. It's gone before it hits the ground.",
    ],
    "fox": [
        "{fam} takes the egg from your hand so gently you barely feel it leave.",
        "You feed {fam} table scraps. It eats like it's being watched, which it is.",
    ],
}
PET_LINES = {
    "salamander": [
        "You run a finger along {fam}'s spine. It's warmer than it has any right to be.",
        "{fam} climbs into your sleeve and stays there, radiating heat like a small stove.",
    ],
    "raven": [
        "{fam} allows exactly four seconds of head-scratches before deciding that's enough.",
        "You smooth {fam}'s feathers back into place. It mutters something that sounds rude.",
    ],
    "fox": [
        "{fam} flops over for a belly rub with zero dignity and total commitment.",
        "You scratch behind {fam}'s ears. Its tail thumps twice against the floor.",
    ],
}
PLAY_LINES = {
    "salamander": [
        "You dangle a bit of string near {fam} and it pounces like the string owes it money.",
        "{fam} chases its own tail in a slow, deliberate circle, very pleased with itself.",
    ],
    "raven": [
        "You hide a button and {fam} finds it in under a minute, smug about it.",
        "{fam} drops a pebble on your head from the rafters. That's the game, apparently.",
    ],
    "fox": [
        "You throw a stick and {fam} brings back an entirely different stick, on principle.",
        "{fam} play-bows at you until you chase it around the room. You lose, obviously.",
    ],
}
SCOUT_FLAVOR = {
    "salamander": "{fam} slips out through a crack under the door and returns hours later, smelling of ash.",
    "raven": "{fam} launches from the windowsill and is a speck over the rooftops within seconds.",
    "fox": "{fam} noses the door open and trots off into the dark like it already knows where it's going.",
}
SCOUT_EMPTY = {
    "salamander": "{fam} comes back a little sooty and empty-clawed. Not every trip pays off.",
    "raven": "{fam} returns and lands on your shoulder with nothing but an opinion about your day.",
    "fox": "{fam} slinks back in, shakes off, and curls up. Whatever it found, it isn't sharing.",
}
SCOUT_RETURN = {
    "salamander": "{fam} drops {item} at your feet, still faintly warm.",
    "raven": "{fam} lands and spits {item} out of its beak with visible pride.",
    "fox": "{fam} trots up and sets {item} down like a gift it's very proud of.",
}


def _tier(friendship: int):
    tier = TIERS[0]
    for t in TIERS:
        if friendship >= t[0]:
            tier = t
    return tier


class Familiars(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rng_module = __import__("random").Random()
        self.state = self._load()
        self.state.setdefault("members", {})

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.exception("Familiars state unreadable - starting empty.")
            return {}

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Couldn't save familiars state.")

    def record(self, user_id: int) -> dict:
        rec = self.state["members"].setdefault(str(user_id), {})
        rec.setdefault("species", None)
        rec.setdefault("name", None)
        rec.setdefault("friendship", 0)
        rec.setdefault("day", {"date": ""})
        if rec["day"].get("date") != today():
            rec["day"] = {"date": today()}
        return rec

    def familiar_of(self, user_id: int) -> dict | None:
        """Public lookup for other cogs (e.g. /profile) - None if not adopted."""
        rec = self.state["members"].get(str(user_id))
        return rec if rec and rec.get("species") else None

    def fam_word(self, rec: dict) -> str:
        meta = FAMILIARS[rec["species"]]
        if rec.get("name"):
            return f"**{rec['name']}** the {meta['label']}"
        return f"{meta['article']} {meta['label']}"

    # ------------------------------------------------------------ /familiar

    @app_commands.command(name="familiar", description="Adopt, name, or check in on your familiar.")
    @app_commands.describe(choose="Adopt this familiar for good, if you don't have one yet",
                           name="Give it a name (or change the one it has)")
    @app_commands.choices(choose=[
        app_commands.Choice(name="🦎 Salamander", value="salamander"),
        app_commands.Choice(name="🐦 Raven", value="raven"),
        app_commands.Choice(name="🦊 Fox", value="fox"),
    ])
    async def familiar(self, interaction: discord.Interaction,
                        choose: app_commands.Choice[str] = None, name: str = None):
        rec = self.record(interaction.user.id)

        if name is not None:
            name = name.strip()
            if not (1 <= len(name) <= NAME_MAX_LEN):
                await interaction.response.send_message(
                    f"Names need to be 1-{NAME_MAX_LEN} characters.", ephemeral=True)
                return
            if not rec["species"] and not choose:
                await interaction.response.send_message(
                    "Adopt a familiar first with the `choose` option, then name it.", ephemeral=True)
                return

        if choose:
            if rec["species"]:
                await interaction.response.send_message(
                    f"You already have {self.fam_word(rec)} - familiars bond for good.",
                    ephemeral=True)
                return
            rec["species"] = choose.value
            rec["friendship"] = 0
            if name:
                rec["name"] = name
            self.save()
            meta = FAMILIARS[choose.value]
            embed = discord.Embed(
                title=f"{meta['emoji']} A bond is formed",
                description=(f"{self.fam_word(rec)} has chosen to stay with "
                              f"{interaction.user.mention}.\n\nFeed it, pet it, and play with it - once a day "
                              "each raises its friendship. The more it trusts you, the more it brings back "
                              "when you send it out with `/scout`."
                              + ("" if name else " Give it a name any time with `/familiar name:`.")),
                color=WARM,
            )
            await interaction.response.send_message(embed=embed)
            return

        if not rec["species"]:
            await interaction.response.send_message(
                "You haven't adopted a familiar yet - run `/familiar` and pick one with the `choose` option.",
                ephemeral=True)
            return

        if name is not None:
            rec["name"] = name
            self.save()
            await interaction.response.send_message(
                f"From now on, {FAMILIARS[rec['species']]['label'].lower()} answers to **{name}**.")
            return

        meta = FAMILIARS[rec["species"]]
        floor, tier_name, _, _ = _tier(rec["friendship"])
        day = rec["day"]
        checklist = "  ".join(
            f"{'✅' if day.get(k) else '⬜'} {k.capitalize()}" for k in ("fed", "pet", "played", "scouted")
        )
        embed = discord.Embed(
            title=f"{meta['emoji']} {self.fam_word(rec)}",
            description=(f"Friendship: **{rec['friendship']}** ({tier_name})\n\nToday: {checklist}\n\n"
                         "`/feed` · `/pet` · `/play` · `/scout`" +
                         ("" if rec.get("name") else "\n\nGive it a name with `/familiar name:`.")),
            color=WARM,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------ feed/pet/play

    async def _care(self, interaction: discord.Interaction, action: str, lines: dict, already: str):
        rec = self.record(interaction.user.id)
        if not rec["species"]:
            await interaction.response.send_message(
                "You don't have a familiar yet - run `/familiar` to adopt one.", ephemeral=True)
            return
        if rec["day"].get(action):
            await interaction.response.send_message(already, ephemeral=True)
            return
        rec["day"][action] = True
        adorn = self.bot.get_cog("Adornments")
        bonus = adorn.friendship_bonus(interaction.user.id) if adorn else 0
        rec["friendship"] = min(FRIENDSHIP_CAP, rec["friendship"] + FRIENDSHIP_PER_ACTION + bonus)
        self.save()
        fam = self.fam_word(rec)
        line = self.rng_module.choice(lines[rec["species"]]).format(fam=fam)
        extra = adorn.bell_line(interaction.user.id, rec["species"], fam) if adorn else None
        if extra:
            line += f"\n{extra}"
        if bonus:
            line += "\n-# 🧶 Your Kindred Bracelet tightens a little. You two are closer than ever."
        await interaction.response.send_message(line)
        if adorn:
            try:
                await adorn.check_member(interaction.user)
            except Exception:
                log.exception("Gear check after familiar care failed")

    @app_commands.command(name="feed", description="Feed your familiar. Once a day.")
    async def feed(self, interaction: discord.Interaction):
        await self._care(interaction, "fed", FEED_LINES, "Already fed today - its belly's full.")

    @app_commands.command(name="pet", description="Pet your familiar. Once a day.")
    async def pet(self, interaction: discord.Interaction):
        await self._care(interaction, "pet", PET_LINES, "Already had its fill of pets today.")

    @app_commands.command(name="play", description="Play with your familiar. Once a day.")
    async def play(self, interaction: discord.Interaction):
        await self._care(interaction, "played", PLAY_LINES, "It's already worn out from playing today.")

    # ------------------------------------------------------------ /scout

    @app_commands.command(name="scout", description="Send your familiar out to bring something back. Once a day.")
    async def scout(self, interaction: discord.Interaction):
        rec = self.record(interaction.user.id)
        if not rec["species"]:
            await interaction.response.send_message(
                "You don't have a familiar yet - run `/familiar` to adopt one.", ephemeral=True)
            return
        if rec["day"].get("scouted"):
            await interaction.response.send_message(
                "It's already out (or resting from its last trip) - try again tomorrow.", ephemeral=True)
            return
        rec["day"]["scouted"] = True
        self.save()

        species = rec["species"]
        fam = self.fam_word(rec)
        _, tier_name, find_chance, weights = _tier(rec["friendship"])
        rng = self.rng_module

        world_cog = self.bot.get_cog("World")
        found_item = None
        if world_cog and rng.random() < find_chance:
            rarity = rng.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]
            pool = [iid for iid, it in world_cog.world.items.items() if it.get("rarity") == rarity]
            if pool:
                found_item = rng.choice(pool)

        lines = [SCOUT_FLAVOR[species].format(fam=fam)]
        awarded = 0

        if found_item and world_cog:
            student = world_cog.student(interaction.user)
            world_cog.world.give(student, found_item)
            world_cog.save()
            item_line = world_cog.world.item_line(found_item)
            lines.append(SCOUT_RETURN[species].format(fam=fam, item=item_line))
            lines.append(f"*{world_cog.world.items[found_item]['desc']}*")

            bonus = world_cog.world.reward_points(found_item)
            if bonus and rng.random() < SCOUT_POINT_CHANCE + bonus * 0.1:
                store = self.bot.get_cog("Store")
                house = store.member_house(interaction.user) if store else None
                if store and house:
                    store.record(house=house, delta=bonus, actor_id=self.bot.user.id if self.bot.user else 0,
                                 target_id=interaction.user.id, reason=f"{fam} scouted something rare")
                    awarded = bonus
        else:
            lines.append(SCOUT_EMPTY[species].format(fam=fam))

        embed = discord.Embed(title=f"{FAMILIARS[species]['emoji']} Scouting report",
                              description="\n".join(lines), color=WARM)
        embed.set_footer(text=f"Friendship: {tier_name}" + (f"  ·  +{awarded} bonus points" if awarded else ""))
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Familiars(bot))
