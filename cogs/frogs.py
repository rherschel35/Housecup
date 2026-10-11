"""
Chocolate Frog Cards — daily collectibles.

    /frog open              - unwrap today's frog (once / day)
    /frog album [member]    - your collection
    /frog give @member card - gift a duplicate

Cards are Velmora figures (ghosts, founders, school legends).
Duplicates can be gifted. No house points from opening.
"""

from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.velmora_channels import channel_mentions, with_study_hall_and_duels_potions
from cogs.world_engine import today

log = logging.getLogger("velmora.frogs")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "frogs_state.json"

FROG_CHANNEL_IDS = with_study_hall_and_duels_potions()
GOLD = 0xD4A84A

# id -> card meta. rarity weights used when opening.
CARDS = {
    "mordy": {
        "name": "Mordy Velmora",
        "blurb": "Founder of the school. Sharp tongue, softer history.",
        "rarity": "legendary",
        "emoji": "🏰",
    },
    "vida": {
        "name": "Vida Vashara",
        "blurb": "Oakmont's founder-ghost. Healing given freely.",
        "rarity": "legendary",
        "emoji": "🐻",
    },
    "maynard": {
        "name": "Maynard Moonveil",
        "blurb": "AsterWilde's happy mad scientist. No tragedy — on purpose.",
        "rarity": "legendary",
        "emoji": "🐿️",
    },
    "finley": {
        "name": "Finley Veyren",
        "blurb": "Veyren's founder. Loved loudly. Left too soon.",
        "rarity": "legendary",
        "emoji": "🦌",
    },
    "sebastian": {
        "name": "Sebastian",
        "blurb": "Finley's shadow and equal. Still watching the maze.",
        "rarity": "rare",
        "emoji": "🕯️",
    },
    "cassy": {
        "name": "Cassy Caldrin",
        "blurb": "Caldrin's patent storm. Fixed what others endured.",
        "rarity": "legendary",
        "emoji": "🐒",
    },
    "pip": {
        "name": "Pip Wick",
        "blurb": "Hearthling help desk. Answers before you finish asking.",
        "rarity": "uncommon",
        "emoji": "🫖",
    },
    "gon": {
        "name": "Gon Vale",
        "blurb": "Headmaster. When something breaks, he already knows.",
        "rarity": "rare",
        "emoji": "🪄",
    },
    "niffler": {
        "name": "The First Niffler",
        "blurb": "Shiny things were never safe again.",
        "rarity": "uncommon",
        "emoji": "🦡",
    },
    "sir_hopsalot": {
        "name": "Sir Hopsalot",
        "blurb": "A prefect's toad. A legend. A menace.",
        "rarity": "common",
        "emoji": "🐸",
    },
    "oakmont_bear": {
        "name": "The Oakmont Bear",
        "blurb": "Patient. Relentless. Purple.",
        "rarity": "common",
        "emoji": "🐻",
    },
    "aster_squirrel": {
        "name": "The AsterWilde Squirrel",
        "blurb": "Beautiful chaos with a nut budget.",
        "rarity": "common",
        "emoji": "🐿️",
    },
    "veyren_deer": {
        "name": "The Veyren Deer",
        "blurb": "Some bonds need no words. Or cards. (Have one anyway.)",
        "rarity": "common",
        "emoji": "🦌",
    },
    "caldrin_lemur": {
        "name": "The Caldrin Lemur",
        "blurb": "Measured, not meek. Still steals biscuits.",
        "rarity": "common",
        "emoji": "🐒",
    },
    "thornmere_wolf": {
        "name": "The Thornmere Wolf",
        "blurb": "Loyalty earned. Advantage taken. Card collected.",
        "rarity": "common",
        "emoji": "🐺",
    },
    "sorting_hat_adjacent": {
        "name": "The Sorting Net",
        "blurb": "It does not shout. It quietly files you.",
        "rarity": "uncommon",
        "emoji": "🧵",
    },
    "broom_fitter": {
        "name": "The Broom-Fitter",
        "blurb": "Reads three words. Hands you a destiny with bristles.",
        "rarity": "uncommon",
        "emoji": "🧹",
    },
    "kitchen_elf_nix": {
        "name": "Nix of the Kitchens",
        "blurb": "Opinions. Butter. Absolute power.",
        "rarity": "uncommon",
        "emoji": "🍞",
    },
    "prefect_marlow": {
        "name": "Prefect Marlow",
        "blurb": "Badge heavy. Patience thin. Still trying.",
        "rarity": "common",
        "emoji": "🎖️",
    },
    "hollow_portrait": {
        "name": "The Hollow Portrait",
        "blurb": "Gives directions. Sometimes correct ones.",
        "rarity": "rare",
        "emoji": "🖼️",
    },
    "lake_figure": {
        "name": "The Hooded Shore",
        "blurb": "Accepts vials. Offers no names.",
        "rarity": "rare",
        "emoji": "🌙",
    },
    "chocolate_frog_self": {
        "name": "Chocolate Frog (Meta)",
        "blurb": "A card of a frog on a card. Philosophy majors love this one.",
        "rarity": "rare",
        "emoji": "🃏",
    },
    "room_of_requirement": {
        "name": "The Room",
        "blurb": "Appears when needed. Leaves when gossip arrives.",
        "rarity": "uncommon",
        "emoji": "🚪",
    },
    "wild_threat": {
        "name": "A Wild Threat",
        "blurb": "It wandered in. Someone cast. Points happened.",
        "rarity": "common",
        "emoji": "👻",
    },
}

RARITY_WEIGHT = {
    "common": 50,
    "uncommon": 30,
    "rare": 15,
    "legendary": 5,
}

RARITY_ORDER = ("common", "uncommon", "rare", "legendary")


def _channel_hint() -> str:
    return f"Chocolate Frogs open in {channel_mentions(FROG_CHANNEL_IDS)}."


class Frogs(commands.Cog):
    frog = app_commands.Group(
        name="frog",
        description="Chocolate Frog Cards — daily collectibles.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.rng = random.Random()

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"players": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Frogs state unreadable — starting empty.")
            return {"players": {}}
        data.setdefault("players", {})
        return data

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save frogs state.")

    async def _require_channel(self, interaction: discord.Interaction) -> bool:
        if interaction.channel_id in FROG_CHANNEL_IDS:
            return True
        await interaction.response.send_message(_channel_hint(), ephemeral=True)
        return False

    def record(self, user_id: int) -> dict:
        rec = self.state.setdefault("players", {}).setdefault(str(user_id), {})
        rec.setdefault("cards", {})  # id -> count
        rec.setdefault("opened_on", "")
        return rec

    def counts(self, user_id: int) -> dict[str, int]:
        return {
            k: int(v)
            for k, v in (self.record(user_id).get("cards") or {}).items()
            if int(v) > 0 and k in CARDS
        }

    def owned_total(self, user_id: int) -> tuple[int, int]:
        counts = self.counts(user_id)
        unique = len(counts)
        copies = sum(counts.values())
        return unique, copies

    def roll(self) -> str:
        pool = []
        weights = []
        for cid, meta in CARDS.items():
            pool.append(cid)
            weights.append(RARITY_WEIGHT.get(meta["rarity"], 10))
        return self.rng.choices(pool, weights=weights, k=1)[0]

    def add_card(self, user_id: int, card_id: str) -> tuple[int, bool]:
        rec = self.record(user_id)
        cards = rec.setdefault("cards", {})
        before = int(cards.get(card_id, 0))
        cards[card_id] = before + 1
        self.save()
        return cards[card_id], before == 0

    @frog.command(name="open", description="Unwrap today's Chocolate Frog (once per day).")
    async def frog_open(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        rec = self.record(interaction.user.id)
        day = today()
        if rec.get("opened_on") == day:
            await interaction.response.send_message(
                "You've already opened today's frog. Come back tomorrow — "
                "or show off with `/frog album`.",
                ephemeral=True,
            )
            return
        card_id = self.roll()
        meta = CARDS[card_id]
        count, is_new = self.add_card(interaction.user.id, card_id)
        rec["opened_on"] = day
        self.save()
        unique, copies = self.owned_total(interaction.user.id)
        flag = "**NEW!**" if is_new else f"Duplicate ×{count}"
        embed = discord.Embed(
            title=f"🍫 Chocolate Frog — {meta['emoji']} {meta['name']}",
            description=(
                f"*{meta['blurb']}*\n\n"
                f"**{meta['rarity'].title()}** · {flag}\n"
                f"Album: **{unique}/{len(CARDS)}** unique · **{copies}** cards total"
            ),
            color=GOLD,
        )
        embed.set_footer(text="Gift extras with /frog give · Display one in /room")
        await interaction.response.send_message(embed=embed)

    @frog.command(name="album", description="Your Chocolate Frog album (or someone else's).")
    @app_commands.describe(member="Whose album to view")
    async def frog_album(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ):
        if not await self._require_channel(interaction):
            return
        target = member or interaction.user
        counts = self.counts(target.id)
        unique, copies = self.owned_total(target.id)
        if not counts:
            await interaction.response.send_message(
                f"{target.display_name} hasn't opened a frog yet. `/frog open` once a day.",
                ephemeral=True,
            )
            return
        lines = []
        for rarity in RARITY_ORDER:
            chunk = [
                f"{CARDS[cid]['emoji']} **{CARDS[cid]['name']}** ×{counts[cid]}"
                for cid in sorted(counts, key=lambda c: CARDS[c]["name"])
                if CARDS[cid]["rarity"] == rarity
            ]
            if chunk:
                lines.append(f"__{rarity.title()}__\n" + "\n".join(chunk))
        missing = len(CARDS) - unique
        embed = discord.Embed(
            title=f"🃏 {target.display_name}'s Frog Album",
            description="\n\n".join(lines),
            color=GOLD,
        )
        embed.set_footer(
            text=f"{unique}/{len(CARDS)} unique · {copies} cards · {missing} still missing"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @frog.command(name="give", description="Gift a duplicate Chocolate Frog card to someone.")
    @app_commands.describe(member="Who receives the card", card="Card to gift (must have a spare)")
    async def frog_give(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        card: str,
    ):
        if not await self._require_channel(interaction):
            return
        if member.id == interaction.user.id:
            await interaction.response.send_message(
                "Gifting yourself a frog is just holding it differently.",
                ephemeral=True,
            )
            return
        if member.bot:
            await interaction.response.send_message(
                "Bots don't collect frogs.", ephemeral=True
            )
            return
        key = card.strip().lower().replace(" ", "_")
        # allow name match
        if key not in CARDS:
            for cid, meta in CARDS.items():
                if meta["name"].lower() == card.strip().lower():
                    key = cid
                    break
        if key not in CARDS:
            await interaction.response.send_message(
                "Unknown card. Check `/frog album` for names.", ephemeral=True
            )
            return
        mine = self.counts(interaction.user.id)
        if mine.get(key, 0) < 2:
            await interaction.response.send_message(
                f"You need a **duplicate** of {CARDS[key]['name']} to gift one "
                "(keep at least one for your album).",
                ephemeral=True,
            )
            return
        rec = self.record(interaction.user.id)
        rec["cards"][key] = int(rec["cards"][key]) - 1
        self.add_card(member.id, key)
        await interaction.response.send_message(
            f"{interaction.user.mention} gifted "
            f"{CARDS[key]['emoji']} **{CARDS[key]['name']}** to {member.mention}!"
        )

    @frog_give.autocomplete("card")
    async def card_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        counts = self.counts(interaction.user.id)
        needle = (current or "").lower()
        out = []
        for cid, n in sorted(counts.items(), key=lambda kv: CARDS[kv[0]]["name"]):
            if n < 2:
                continue
            name = CARDS[cid]["name"]
            if needle and needle not in name.lower() and needle not in cid:
                continue
            out.append(app_commands.Choice(name=f"{name} (×{n})", value=cid))
            if len(out) >= 25:
                break
        return out


async def setup(bot: commands.Bot):
    await bot.add_cog(Frogs(bot))
