"""
Your Own Room — personal dorm you decorate.

    /room [member]     - view a dorm
    /room find         - discover one decor item (once / day)
    /room place        - put an owned item into a slot
    /room clear        - clear a slot
    /room stash        - list decor you own

Five starter decor pieces; the rest unlock when you pull the matching
Chocolate Frog card (added to your stash). Staff can prize decor.
Frog cards can also sit on the shelf. Purely cosmetic.
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

from cogs.world_engine import today

log = logging.getLogger("velmora.dorm")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "dorm_state.json"

COZY = 0x6B4F3A

SLOTS = ("bed", "wall", "desk", "shelf", "window")
SLOT_LABEL = {
    "bed": "Bed",
    "wall": "Wall",
    "desk": "Desk",
    "shelf": "Shelf",
    "window": "Window",
}

# Decor catalog: 5 starters + 50 unlocked by Chocolate Frog cards.
# unlock_card=None → available at the start; else must own that frog card once.
DECOR = {
    "patchwork_quilt": {
        "name": "Patchwork Quilt",
        "slot": "bed",
        "blurb": "Every square is a different excuse for staying up late.",
        "emoji": "🛏️",
        "unlock_card": None,
    },
    "house_banner": {
        "name": "House Banner",
        "slot": "wall",
        "blurb": "Crooked on purpose. Or by talent.",
        "emoji": "🏳️",
        "unlock_card": None,
    },
    "tea_tray": {
        "name": "Perpetual Tea Tray",
        "slot": "desk",
        "blurb": "Still warm. Always. Suspicious.",
        "emoji": "🍵",
        "unlock_card": None,
    },
    "plant_that_bites": {
        "name": "Plant That Bites",
        "slot": "shelf",
        "blurb": "Friendly. Mostly. Water from a distance.",
        "emoji": "🪴",
        "unlock_card": None,
    },
    "window_charm_rain": {
        "name": "Rain Charm Pane",
        "slot": "window",
        "blurb": "Soft rain against the glass even in drought.",
        "emoji": "🌧️",
        "unlock_card": None,
    },
    "star_mobile": {
        "name": "Star Mobile",
        "slot": "bed",
        "blurb": "Turns slowly. Judges your sleep schedule.",
        "emoji": "✨",
        "unlock_card": "accio",
    },
    "cloud_duvet": {
        "name": "Cloud Duvet",
        "slot": "bed",
        "blurb": "Sleeps like fog. Weighs like a rumor.",
        "emoji": "☁️",
        "unlock_card": "alohomora",
    },
    "moving_sketch": {
        "name": "Moving Sketch",
        "slot": "wall",
        "blurb": "The subject keeps changing hats.",
        "emoji": "🖼️",
        "unlock_card": "blackcap_squirrel",
    },
    "quidditch_poster": {
        "name": "Quidditch Finals Poster",
        "slot": "wall",
        "blurb": "Creased. Loved. Historically inaccurate.",
        "emoji": "🏆",
        "unlock_card": "button_magpie",
    },
    "night_sky_mural": {
        "name": "Night-Sky Mural Charm",
        "slot": "wall",
        "blurb": "Constellations rearrange into gossip.",
        "emoji": "🌠",
        "unlock_card": "cistern_toad",
    },
    "crest_embroidery": {
        "name": "Crest Embroidery Hoop",
        "slot": "wall",
        "blurb": "Your house animal, slightly judgmental.",
        "emoji": "🪡",
        "unlock_card": "colloportus",
    },
    "duel_score_slate": {
        "name": "Duel Score Slate",
        "slot": "wall",
        "blurb": "Keeps your wins. Forgets your losses. Kind.",
        "emoji": "⚔️",
        "unlock_card": "descendo",
    },
    "tapestry_forest": {
        "name": "Forest Tapestry",
        "slot": "wall",
        "blurb": "Wind moves the threads. Leaves fall out.",
        "emoji": "🌲",
        "unlock_card": "duskpup",
    },
    "spell_diagram": {
        "name": "Framed Spell Diagram",
        "slot": "wall",
        "blurb": "Wrong on purpose to catch cheaters.",
        "emoji": "📐",
        "unlock_card": "episkey",
    },
    "broom_mount": {
        "name": "Wall-Mounted Broom",
        "slot": "wall",
        "blurb": "Decorative. Mostly. Don't sit on it.",
        "emoji": "🧹",
        "unlock_card": "field_cow",
    },
    "ink_stained_desk": {
        "name": "Ink-Stained Desk Set",
        "slot": "desk",
        "blurb": "The quill has opinions about your essays.",
        "emoji": "🖋️",
        "unlock_card": "finite",
    },
    "mini_cauldron": {
        "name": "Desk Mini-Cauldron",
        "slot": "desk",
        "blurb": "For tea. Officially.",
        "emoji": "🫖",
        "unlock_card": "impedimenta",
    },
    "candle_cluster": {
        "name": "Candle Cluster",
        "slot": "desk",
        "blurb": "Never drip on homework. Will drip on pride.",
        "emoji": "🕯️",
        "unlock_card": "lumos",
    },
    "potion_sample_rack": {
        "name": "Sample Vial Rack",
        "slot": "desk",
        "blurb": "Labeled. Mislabelled. Both true.",
        "emoji": "⚗️",
        "unlock_card": "orbitcat",
    },
    "desk_globe_velmora": {
        "name": "Velmora Desk Globe",
        "slot": "desk",
        "blurb": "Spins to the lake when you're homesick.",
        "emoji": "🌍",
        "unlock_card": "reparo",
    },
    "paperweight_frog": {
        "name": "Chocolate Frog Paperweight",
        "slot": "desk",
        "blurb": "Does not hop. Mentally hops.",
        "emoji": "🐸",
        "unlock_card": "riddikulus",
    },
    "timetable_crystal": {
        "name": "Timetable Crystal",
        "slot": "desk",
        "blurb": "Glows when you're in the wrong class.",
        "emoji": "🔮",
        "unlock_card": "silencio",
    },
    "shelf_of_oddities": {
        "name": "Shelf of Oddities",
        "slot": "shelf",
        "blurb": "None of these objects agree on a theme.",
        "emoji": "📦",
        "unlock_card": "hopsalot",
    },
    "jar_of_fireflies": {
        "name": "Jar of Fireflies",
        "slot": "shelf",
        "blurb": "They blink in Morse. Mostly insults.",
        "emoji": "🫙",
        "unlock_card": "ventus",
    },
    "snow_globe_castle": {
        "name": "Castle Snow Globe",
        "slot": "shelf",
        "blurb": "Shake for weather. Overuse for detention.",
        "emoji": "❄️",
        "unlock_card": "wild_threat",
    },
    "tiny_broom_display": {
        "name": "Tiny Broom Display",
        "slot": "shelf",
        "blurb": "1:12 scale. Full attitude.",
        "emoji": "🧹",
        "unlock_card": "wingardium",
    },
    "crystal_geode": {
        "name": "Split Crystal Geode",
        "slot": "shelf",
        "blurb": "Purple inside. Ambition outside.",
        "emoji": "💎",
        "unlock_card": "arresto",
    },
    "orb_of_static": {
        "name": "Orb of Pleasant Static",
        "slot": "shelf",
        "blurb": "White noise for anxious brains.",
        "emoji": "🔮",
        "unlock_card": "bombarda",
    },
    "mushroom_lamp": {
        "name": "Mushroom Shelf Lamp",
        "slot": "shelf",
        "blurb": "Soft glow. Soft spores. Soft threats.",
        "emoji": "🍄",
        "unlock_card": "ember_hound",
    },
    "frost_sheet_set": {
        "name": "Frost Sheet Set",
        "slot": "bed",
        "blurb": "Cool side of the pillow, forever.",
        "emoji": "❄️",
        "unlock_card": "glacius",
    },
    "compass_that_points_home": {
        "name": "Homeward Compass",
        "slot": "shelf",
        "blurb": "Points to common room. Or biscuits.",
        "emoji": "🧭",
        "unlock_card": "lanternback_stag",
    },
    "window_charm_aurora": {
        "name": "Aurora Pane",
        "slot": "window",
        "blurb": "Northern lights on demand. Show-off setting included.",
        "emoji": "🌌",
        "unlock_card": "moonglass_owl",
    },
    "crystal_paperweight": {
        "name": "Crystal Paperweight",
        "slot": "desk",
        "blurb": "Contains a tiny storm. Office-safe.",
        "emoji": "💎",
        "unlock_card": "niffler",
    },
    "moon_catcher": {
        "name": "Moon Catcher",
        "slot": "window",
        "blurb": "Catches moonlight. Releases gossip.",
        "emoji": "🌙",
        "unlock_card": "obliviate",
    },
    "snowfall_loop": {
        "name": "Looping Snowfall",
        "slot": "window",
        "blurb": "Never drifts inside. Almost.",
        "emoji": "❄️",
        "unlock_card": "petrificus",
    },
    "thunderstorm_pane": {
        "name": "Thunderstorm Pane",
        "slot": "window",
        "blurb": "Rumbles softly during essays. Supportive.",
        "emoji": "⛈️",
        "unlock_card": "stormcaller",
    },
    "stained_house_glass": {
        "name": "House Stained Glass",
        "slot": "window",
        "blurb": "Your crest in light. Slightly biased.",
        "emoji": "🪟",
        "unlock_card": "stupefy",
    },
    "quidditch_pitch_view": {
        "name": "Pitch View Charm",
        "slot": "window",
        "blurb": "Live score smudges on the glass.",
        "emoji": "🏟️",
        "unlock_card": "volt_serpent",
    },
    "aster_chaos_blanket": {
        "name": "AsterWilde Chaos Blanket",
        "slot": "bed",
        "blurb": "No two folds agree. Neither do you.",
        "emoji": "🐿️",
        "unlock_card": "crest_asterwilde",
    },
    "caldrin_measured_sheets": {
        "name": "Caldrin Measured Sheets",
        "slot": "bed",
        "blurb": "Hospital corners. Emotional support optional.",
        "emoji": "📐",
        "unlock_card": "crest_caldrin",
    },
    "oakmont_coverlet": {
        "name": "Oakmont Coverlet",
        "slot": "bed",
        "blurb": "Purple stitching. Patient warmth.",
        "emoji": "🐻",
        "unlock_card": "crest_oakmont",
    },
    "bookend_wolves": {
        "name": "Wolf Bookends",
        "slot": "shelf",
        "blurb": "Growl if you shelve out of order.",
        "emoji": "🐺",
        "unlock_card": "crest_thornmere",
    },
    "veyren_wool_throw": {
        "name": "Veyren Wool Throw",
        "slot": "bed",
        "blurb": "Quiet as a deer. Soft as a promise.",
        "emoji": "🦌",
        "unlock_card": "crest_veyren",
    },
    "midnight_canopy": {
        "name": "Midnight Canopy",
        "slot": "bed",
        "blurb": "Stars painted underneath. Some move.",
        "emoji": "🌌",
        "unlock_card": "dementor",
    },
    "open_sky_charm": {
        "name": "Open Sky Charm",
        "slot": "window",
        "blurb": "No glass. Just breeze and bravery.",
        "emoji": "🌤️",
        "unlock_card": "expelliarmus",
    },
    "ember_bed_warmer": {
        "name": "Ember Bed Warmer",
        "slot": "bed",
        "blurb": "Safe heat. Unsafe temptation to never rise.",
        "emoji": "🔥",
        "unlock_card": "incendio",
    },
    "focus_charm_lamp": {
        "name": "Focus Charm Lamp",
        "slot": "desk",
        "blurb": "Brightens only what you're avoiding.",
        "emoji": "💡",
        "unlock_card": "protego",
    },
    "thornmere_wolf_pelt": {
        "name": "Thornmere Wolf Pelt",
        "slot": "bed",
        "blurb": "Loyalty earned. Naps taken.",
        "emoji": "🐺",
        "unlock_card": "shadow_wolf",
    },
    "beast_figurine": {
        "name": "Beast Figurine Set",
        "slot": "shelf",
        "blurb": "Rearranges overnight into a tableau.",
        "emoji": "🦁",
        "unlock_card": "chimera_saro",
    },
    "patronus_silhouette": {
        "name": "Patronus Silhouette",
        "slot": "wall",
        "blurb": "Silver outline of courage on quiet nights.",
        "emoji": "🦌",
        "unlock_card": "expecto",
    },
    "lake_view_charm": {
        "name": "Lake View Charm",
        "slot": "window",
        "blurb": "Shows the shore. Hides the homework.",
        "emoji": "🌊",
        "unlock_card": "kraken_lumi",
    },
    "owlery_glimpse": {
        "name": "Owlery Glimpse",
        "slot": "window",
        "blurb": "Feathers against the glass. Letters inbound.",
        "emoji": "🦉",
        "unlock_card": "griffin",
    },
    "velmora_map": {
        "name": "Annotated Velmora Map",
        "slot": "wall",
        "blurb": "Shortcuts in red. Detours in denial.",
        "emoji": "🗺️",
        "unlock_card": "sorting_net",
    },
    "wax_seal_kit": {
        "name": "Wax Seal Kit",
        "slot": "desk",
        "blurb": "Crest stamps included. Taste debated.",
        "emoji": "🔏",
        "unlock_card": "velmora_seal",
    },
    "starfield_glass": {
        "name": "Starfield Glass",
        "slot": "window",
        "blurb": "Constellations of your choosing.",
        "emoji": "✨",
        "unlock_card": "lumen_aegis",
    },
}

# card_id -> decor_id for frog unlocks
DECOR_BY_CARD = {
    "accio": "star_mobile",
    "alohomora": "cloud_duvet",
    "blackcap_squirrel": "moving_sketch",
    "button_magpie": "quidditch_poster",
    "cistern_toad": "night_sky_mural",
    "colloportus": "crest_embroidery",
    "descendo": "duel_score_slate",
    "duskpup": "tapestry_forest",
    "episkey": "spell_diagram",
    "field_cow": "broom_mount",
    "finite": "ink_stained_desk",
    "impedimenta": "mini_cauldron",
    "lumos": "candle_cluster",
    "orbitcat": "potion_sample_rack",
    "reparo": "desk_globe_velmora",
    "riddikulus": "paperweight_frog",
    "silencio": "timetable_crystal",
    "hopsalot": "shelf_of_oddities",
    "ventus": "jar_of_fireflies",
    "wild_threat": "snow_globe_castle",
    "wingardium": "tiny_broom_display",
    "arresto": "crystal_geode",
    "bombarda": "orb_of_static",
    "ember_hound": "mushroom_lamp",
    "glacius": "frost_sheet_set",
    "lanternback_stag": "compass_that_points_home",
    "moonglass_owl": "window_charm_aurora",
    "niffler": "crystal_paperweight",
    "obliviate": "moon_catcher",
    "petrificus": "snowfall_loop",
    "stormcaller": "thunderstorm_pane",
    "stupefy": "stained_house_glass",
    "volt_serpent": "quidditch_pitch_view",
    "crest_asterwilde": "aster_chaos_blanket",
    "crest_caldrin": "caldrin_measured_sheets",
    "crest_oakmont": "oakmont_coverlet",
    "crest_thornmere": "bookend_wolves",
    "crest_veyren": "veyren_wool_throw",
    "dementor": "midnight_canopy",
    "expelliarmus": "open_sky_charm",
    "incendio": "ember_bed_warmer",
    "protego": "focus_charm_lamp",
    "shadow_wolf": "thornmere_wolf_pelt",
    "chimera_saro": "beast_figurine",
    "expecto": "patronus_silhouette",
    "kraken_lumi": "lake_view_charm",
    "griffin": "owlery_glimpse",
    "sorting_net": "velmora_map",
    "velmora_seal": "wax_seal_kit",
    "lumen_aegis": "starfield_glass",
}

STARTER_DECOR = {cid for cid, d in DECOR.items() if d.get("unlock_card") is None}
assert len(STARTER_DECOR) == 5
assert len(DECOR) == 55
assert len(DECOR_BY_CARD) == 50


class Dorm(commands.Cog):
    room = app_commands.Group(
        name="room",
        description="Your dorm — find decor, place it, show it off.",
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
            log.exception("Dorm state unreadable — starting empty.")
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
            log.exception("Could not save dorm state.")

    def record(self, user_id: int) -> dict:
        rec = self.state.setdefault("players", {}).setdefault(str(user_id), {})
        rec.setdefault("stash", {})  # decor_id -> count
        rec.setdefault("slots", {})  # slot -> {"kind":"decor"|"frog", "id":...}
        rec.setdefault("unlocked", [])  # decor ids unlocked via frogs / staff (beyond starters)
        rec.setdefault("found_on", "")
        return rec

    def unlocked_ids(self, user_id: int) -> set[str]:
        """Starters + permanently unlocked card decor."""
        rec = self.record(user_id)
        out = set(STARTER_DECOR)
        for did in rec.get("unlocked") or []:
            if did in DECOR:
                out.add(did)
        # Backfill: owning a frog card counts as unlocked
        frogs = self.bot.get_cog("Frogs")
        if frogs:
            for card_id, n in frogs.counts(user_id).items():
                if n < 1:
                    continue
                did = DECOR_BY_CARD.get(card_id)
                if did:
                    out.add(did)
        return out

    def unlock_from_frog(self, user_id: int, card_id: str, *, save: bool = True) -> str | None:
        """Unlock + stash the decor tied to this frog card. Returns a note or None."""
        decor_id = DECOR_BY_CARD.get(card_id)
        if not decor_id or decor_id not in DECOR:
            return None
        rec = self.record(user_id)
        unlocked = rec.setdefault("unlocked", [])
        first = decor_id not in unlocked and decor_id not in STARTER_DECOR
        if decor_id not in unlocked:
            unlocked.append(decor_id)
        stash = rec.setdefault("stash", {})
        # First unlock grants one copy to place; later pulls of same card don't spam stash
        if not first:
            if save:
                self.save()
            return None
        stash[decor_id] = int(stash.get(decor_id, 0)) + 1
        d = DECOR[decor_id]
        note = (
            f"🏠 Unlocked decor: {d['emoji']} **{d['name']}** "
            f"({SLOT_LABEL[d['slot']]}) — `/room place`"
        )
        rec.setdefault("pending_unlock_notes", []).append(note)
        if save:
            self.save()
        return note

    def pop_unlock_notes(self, user_id: int) -> list[str]:
        rec = self.record(user_id)
        notes = list(rec.pop("pending_unlock_notes", []) or [])
        if notes:
            self.save()
        return notes

    def grant_decor(
        self, user_id: int, decor_id: str, *, unlock: bool = True, qty: int = 1
    ) -> str | None:
        """Staff/prize: unlock and add decor to stash."""
        if decor_id not in DECOR:
            return None
        rec = self.record(user_id)
        if unlock and decor_id not in STARTER_DECOR:
            unlocked = rec.setdefault("unlocked", [])
            if decor_id not in unlocked:
                unlocked.append(decor_id)
        stash = rec.setdefault("stash", {})
        stash[decor_id] = int(stash.get(decor_id, 0)) + max(1, qty)
        self.save()
        d = DECOR[decor_id]
        return f"{d['emoji']} **{d['name']}** ({SLOT_LABEL[d['slot']]})"

    def ensure_starters(self, user_id: int) -> None:
        """Give every player the five free starter pieces once."""
        rec = self.record(user_id)
        dirty = False
        if not rec.get("starters_seeded"):
            stash = rec.setdefault("stash", {})
            for did in STARTER_DECOR:
                stash[did] = int(stash.get(did, 0)) + 1
            rec["starters_seeded"] = True
            dirty = True
        # One-time backfill unlocks for frog cards owned before this feature
        if not rec.get("card_unlocks_synced"):
            frogs = self.bot.get_cog("Frogs")
            if frogs:
                for card_id, n in frogs.counts(user_id).items():
                    if n >= 1:
                        self.unlock_from_frog(user_id, card_id, save=False)
            rec["card_unlocks_synced"] = True
            dirty = True
        if dirty:
            self.save()

    def _frog_meta(self, card_id: str) -> dict | None:
        frogs = self.bot.get_cog("Frogs")
        if not frogs:
            return None
        from cogs.frogs import CARDS
        return CARDS.get(card_id)

    def embed_room(self, member: discord.abc.User, rec: dict) -> discord.Embed:
        lines = []
        for slot in SLOTS:
            placed = (rec.get("slots") or {}).get(slot)
            label = SLOT_LABEL[slot]
            if not placed:
                lines.append(f"**{label}:** _empty_")
                continue
            kind = placed.get("kind")
            pid = placed.get("id")
            if kind == "decor" and pid in DECOR:
                d = DECOR[pid]
                lines.append(f"**{label}:** {d['emoji']} {d['name']} — *{d['blurb']}*")
            elif kind == "frog":
                meta = self._frog_meta(pid)
                if meta:
                    lines.append(
                        f"**{label}:** {meta['emoji']} Frog card — **{meta['name']}**"
                    )
                else:
                    lines.append(f"**{label}:** 🃏 Frog card (`{pid}`)")
            else:
                lines.append(f"**{label}:** _something odd_")
        stash_n = sum(int(v) for v in (rec.get("stash") or {}).values())
        embed = discord.Embed(
            title=f"🏠 {member.display_name}'s Room",
            description="\n".join(lines),
            color=COZY,
        )
        embed.set_footer(
            text=f"Stash: {stash_n} decor · /room find · /room place · shelf holds frog cards too"
        )
        return embed

    @room.command(name="view", description="Look at a dorm room.")
    @app_commands.describe(member="Whose room (default: yours)")
    async def room_view(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ):
        target = member or interaction.user
        if target.id == interaction.user.id:
            self.ensure_starters(target.id)
        rec = self.record(target.id)
        await interaction.response.send_message(
            embed=self.embed_room(target, rec),
            ephemeral=True,
        )

    @room.command(
        name="find",
        description="Find a duplicate of unlocked decor for your stash (once per day).",
    )
    async def room_find(self, interaction: discord.Interaction):
        self.ensure_starters(interaction.user.id)
        rec = self.record(interaction.user.id)
        day = today()
        if rec.get("found_on") == day:
            await interaction.response.send_message(
                "You've already found something for your room today. "
                "Try `/room place` or `/room view`.",
                ephemeral=True,
            )
            return
        unlocked = self.unlocked_ids(interaction.user.id)
        if not unlocked:
            await interaction.response.send_message(
                "No unlocked decor yet.", ephemeral=True
            )
            return
        stash = rec.setdefault("stash", {})
        item_id = self.rng.choice(sorted(unlocked))
        stash[item_id] = int(stash.get(item_id, 0)) + 1
        rec["found_on"] = day
        self.save()
        d = DECOR[item_id]
        embed = discord.Embed(
            title=f"Found: {d['emoji']} {d['name']}",
            description=(
                f"*{d['blurb']}*\n\n"
                f"Best on the **{SLOT_LABEL[d['slot']]}**. "
                f"Place it with `/room place`.\n\n"
                f"Unlocked: **{len(unlocked)}/{len(DECOR)}** "
                f"(pull Chocolate Frogs to unlock more)"
            ),
            color=COZY,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @room.command(name="catalog", description="Browse decor for a slot — locked until you pull the frog card.")
    @app_commands.describe(slot="Which furniture slot to list")
    @app_commands.choices(
        slot=[app_commands.Choice(name=SLOT_LABEL[s], value=s) for s in SLOTS]
    )
    async def room_catalog(
        self, interaction: discord.Interaction, slot: app_commands.Choice[str]
    ):
        self.ensure_starters(interaction.user.id)
        slot_key = slot.value
        rec = self.record(interaction.user.id)
        stash = rec.get("stash") or {}
        unlocked = self.unlocked_ids(interaction.user.id)
        placed_id = None
        placed = (rec.get("slots") or {}).get(slot_key)
        if placed and placed.get("kind") == "decor":
            placed_id = placed.get("id")
        from cogs.frogs import CARDS

        lines = []
        for cid, d in sorted(
            ((i, DECOR[i]) for i in DECOR if DECOR[i]["slot"] == slot_key),
            key=lambda kv: kv[1]["name"],
        ):
            if cid == placed_id:
                mark = " · placed"
            elif int(stash.get(cid, 0)) > 0:
                mark = " · in stash"
            elif cid in unlocked:
                mark = " · unlocked"
            elif d.get("unlock_card"):
                card = CARDS.get(d["unlock_card"], {})
                mark = f" · 🔒 {card.get('name', d['unlock_card'])}"
            else:
                mark = " · starter"
            lines.append(f"{d['emoji']} **{d['name']}**{mark}")
        embed = discord.Embed(
            title=f"🏠 {SLOT_LABEL[slot_key]} decor",
            description="\n".join(lines),
            color=COZY,
        )
        embed.set_footer(
            text="5 starters free · rest unlock from Chocolate Frog cards"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @room.command(name="stash", description="Decor waiting in your stash.")
    async def room_stash(self, interaction: discord.Interaction):
        self.ensure_starters(interaction.user.id)
        rec = self.record(interaction.user.id)
        stash = {
            k: int(v)
            for k, v in (rec.get("stash") or {}).items()
            if int(v) > 0 and k in DECOR
        }
        if not stash:
            await interaction.response.send_message(
                "Stash is empty. `/room find` once a day.", ephemeral=True
            )
            return
        lines = [
            f"{DECOR[i]['emoji']} **{DECOR[i]['name']}** ×{n} "
            f"({SLOT_LABEL[DECOR[i]['slot']]})"
            for i, n in sorted(stash.items(), key=lambda kv: DECOR[kv[0]]["name"])
        ]
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Room stash",
                description="\n".join(lines),
                color=COZY,
            ),
            ephemeral=True,
        )

    @room.command(name="place", description="Place decor (or a frog card on the shelf).")
    @app_commands.describe(
        item="Decor from your stash, or a frog card id/name for the shelf",
        slot="Where to put it (frog cards → shelf)",
    )
    @app_commands.choices(
        slot=[app_commands.Choice(name=SLOT_LABEL[s], value=s) for s in SLOTS]
    )
    async def room_place(
        self,
        interaction: discord.Interaction,
        item: str,
        slot: app_commands.Choice[str],
    ):
        self.ensure_starters(interaction.user.id)
        rec = self.record(interaction.user.id)
        slot_key = slot.value
        key = item.strip().lower().replace(" ", "_")

        # Decor path
        if key in DECOR or any(
            DECOR[d]["name"].lower() == item.strip().lower() for d in DECOR
        ):
            if key not in DECOR:
                key = next(
                    d
                    for d in DECOR
                    if DECOR[d]["name"].lower() == item.strip().lower()
                )
            d = DECOR[key]
            if d["slot"] != slot_key:
                await interaction.response.send_message(
                    f"**{d['name']}** belongs on the **{SLOT_LABEL[d['slot']]}**, "
                    f"not the {SLOT_LABEL[slot_key]}.",
                    ephemeral=True,
                )
                return
            stash = rec.setdefault("stash", {})
            if int(stash.get(key, 0)) < 1:
                await interaction.response.send_message(
                    "You don't have that in your stash. `/room find` or check `/room stash`.",
                    ephemeral=True,
                )
                return
            # return previous decor to stash if any
            prev = (rec.get("slots") or {}).get(slot_key)
            if prev and prev.get("kind") == "decor" and prev.get("id") in DECOR:
                pid = prev["id"]
                stash[pid] = int(stash.get(pid, 0)) + 1
            stash[key] = int(stash[key]) - 1
            if stash[key] <= 0:
                stash.pop(key, None)
            rec.setdefault("slots", {})[slot_key] = {"kind": "decor", "id": key}
            self.save()
            await interaction.response.send_message(
                embed=self.embed_room(interaction.user, rec),
                ephemeral=True,
            )
            return

        # Frog card path — shelf only
        from cogs.frogs import CARDS

        frog_id = key if key in CARDS else None
        if not frog_id:
            for cid, meta in CARDS.items():
                if meta["name"].lower() == item.strip().lower():
                    frog_id = cid
                    break
        if not frog_id:
            await interaction.response.send_message(
                "Unknown item. Use a decor name from `/room stash`, "
                "or a frog card from `/frog album` for the shelf.",
                ephemeral=True,
            )
            return
        if slot_key != "shelf":
            await interaction.response.send_message(
                "Frog cards only display on the **Shelf**.", ephemeral=True
            )
            return
        frogs = self.bot.get_cog("Frogs")
        if not frogs or frogs.counts(interaction.user.id).get(frog_id, 0) < 1:
            await interaction.response.send_message(
                "You don't have that frog card.", ephemeral=True
            )
            return
        # previous shelf decor returns to stash; previous frog just swaps (cards stay in album)
        prev = (rec.get("slots") or {}).get("shelf")
        if prev and prev.get("kind") == "decor" and prev.get("id") in DECOR:
            pid = prev["id"]
            stash = rec.setdefault("stash", {})
            stash[pid] = int(stash.get(pid, 0)) + 1
        rec.setdefault("slots", {})["shelf"] = {"kind": "frog", "id": frog_id}
        self.save()
        await interaction.response.send_message(
            embed=self.embed_room(interaction.user, rec),
            ephemeral=True,
        )

    @room_place.autocomplete("item")
    async def place_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        rec = self.record(interaction.user.id)
        needle = (current or "").lower()
        out: list[app_commands.Choice[str]] = []
        for did, n in (rec.get("stash") or {}).items():
            if int(n) < 1 or did not in DECOR:
                continue
            name = DECOR[did]["name"]
            if needle and needle not in name.lower():
                continue
            out.append(app_commands.Choice(name=f"{name} (decor)", value=did))
        frogs = self.bot.get_cog("Frogs")
        if frogs:
            from cogs.frogs import CARDS
            for cid, n in frogs.counts(interaction.user.id).items():
                if n < 1:
                    continue
                name = CARDS[cid]["name"]
                if needle and needle not in name.lower():
                    continue
                out.append(app_commands.Choice(name=f"{name} (frog → shelf)", value=cid))
        return out[:25]

    @room.command(name="clear", description="Clear a furniture slot (decor returns to stash).")
    @app_commands.choices(
        slot=[app_commands.Choice(name=SLOT_LABEL[s], value=s) for s in SLOTS]
    )
    async def room_clear(
        self, interaction: discord.Interaction, slot: app_commands.Choice[str]
    ):
        rec = self.record(interaction.user.id)
        slot_key = slot.value
        prev = (rec.get("slots") or {}).pop(slot_key, None)
        if not prev:
            await interaction.response.send_message(
                f"The {SLOT_LABEL[slot_key]} is already empty.", ephemeral=True
            )
            return
        if prev.get("kind") == "decor" and prev.get("id") in DECOR:
            stash = rec.setdefault("stash", {})
            pid = prev["id"]
            stash[pid] = int(stash.get(pid, 0)) + 1
        self.save()
        await interaction.response.send_message(
            embed=self.embed_room(interaction.user, rec),
            ephemeral=True,
        )

    async def staff_prize(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        decor: str,
    ) -> None:
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        key = decor.strip().lower().replace(" ", "_")
        if key not in DECOR:
            for did, meta in DECOR.items():
                if meta["name"].lower() == decor.strip().lower():
                    key = did
                    break
        if key not in DECOR:
            await interaction.response.send_message(
                "Unknown decor. Check `/room catalog`.", ephemeral=True
            )
            return
        if member.bot:
            await interaction.response.send_message(
                "Bots don't decorate dorms.", ephemeral=True
            )
            return
        self.ensure_starters(member.id)
        label = self.grant_decor(member.id, key, unlock=True, qty=1)
        await interaction.response.send_message(
            f"{interaction.user.mention} awarded room decor {label} "
            f"to {member.mention}!",
        )

    async def staff_prize_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        needle = (current or "").lower()
        out = []
        for did, meta in sorted(DECOR.items(), key=lambda kv: kv[1]["name"]):
            label = f"{meta['name']} ({SLOT_LABEL[meta['slot']]})"
            if needle and needle not in label.lower() and needle not in did:
                continue
            out.append(app_commands.Choice(name=label[:100], value=did))
            if len(out) >= 25:
                break
        return out


async def setup(bot: commands.Bot):
    await bot.add_cog(Dorm(bot))
