"""
Familiars. Adopt one, name it, care for it every day, and it brings things
back.

    /familiar status            - see the one you have (friendship, today's checklist)
    /familiar adopt             - adopt one for good (salamander / raven / fox)
    /familiar name              - give it a name (or rename)
    /familiar look              - change its appearance (30 looks per species)
    /familiar looks             - browse the look catalog for your species
    /familiar feed              - once a day, raises friendship
    /familiar pet               - once a day, raises friendship
    /familiar play              - once a day, raises friendship
    /familiar scout             - once a day; sends it out into Velmora

Three familiars to choose from - a Salamander, a Raven, a Fox - one per
person, for good, like a patronus. Each species has thirty cosmetic looks
you can switch anytime. Feed/pet/play each raise friendship once per day;
friendship decides how often /familiar scout comes back with something.
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
ASSETS_DIR = Path(__file__).resolve().parent.parent / "familiar_art_assets"

FRIENDSHIP_PER_ACTION = 2
FRIENDSHIP_CAP = 100
SCOUT_POINT_CHANCE = 0.12
NAME_MAX_LEN = 24

WARM = 0xD98A4B

FAMILIARS = {
    "salamander": {"label": "Salamander", "emoji": "🦎", "article": "a"},
    "raven":      {"label": "Raven",      "emoji": "🐦", "article": "a"},
    "fox":        {"label": "Fox",        "emoji": "🦊", "article": "a"},
}

LOOKS: dict[str, dict[str, dict[str, str]]] = {
    "salamander": {
        "ember_coil": {"label": "Ember Coil", "blurb": "coiled copper-red with a soft belly glow"},
        "ash_stripe": {"label": "Ash Stripe", "blurb": "grey-ash scales with a charcoal dorsal stripe"},
        "cinder_freckle": {"label": "Cinder Freckle", "blurb": "warm orange freckled with soot spots"},
        "molten_ridge": {"label": "Molten Ridge", "blurb": "dark body, glowing orange ridge along the spine"},
        "smoke_veil": {"label": "Smoke Veil", "blurb": "pale smoke-grey with translucent frills"},
        "coal_heart": {"label": "Coal Heart", "blurb": "matte black with a faint ember pulse at the chest"},
        "saffron_tail": {"label": "Saffron Tail", "blurb": "golden-saffron body, brighter tip of the tail"},
        "brimstone_band": {"label": "Brimstone Band", "blurb": "olive-brown with a bright sulfur band at the neck"},
        "hearth_glow": {"label": "Hearth Glow", "blurb": "rust-red, looks lit from within like banked coals"},
        "pepper_scale": {"label": "Pepper Scale", "blurb": "speckled black-and-cream like cracked pepper"},
        "lava_ripple": {"label": "Lava Ripple", "blurb": "dark lava-rock texture with orange ripples"},
        "dawn_ember": {"label": "Dawn Ember", "blurb": "soft peach-orange, gentler than a true fire"},
        "obsidian_spark": {"label": "Obsidian Spark", "blurb": "glossy black with tiny white spark flecks"},
        "rust_frill": {"label": "Rust Frill", "blurb": "rusty copper frills framing a warm face"},
        "kindling_stripe": {"label": "Kindling Stripe", "blurb": "tan body with thin kindling-colored stripes"},
        "forge_blush": {"label": "Forge Blush", "blurb": "iron-grey with a rosy forge blush on the cheeks"},
        "tinder_dot": {"label": "Tinder Dot", "blurb": "sandy beige dotted with tinder-red spots"},
        "glowworm_trail": {"label": "Glowworm Trail", "blurb": "dusky body with a soft bioluminescent undertrail"},
        "pumice_pale": {"label": "Pumice Pale", "blurb": "light pumice-grey, almost stone-colored"},
        "chili_flare": {"label": "Chili Flare", "blurb": "bright chili-red with a flare of yellow at the snout"},
        "soot_sock": {"label": "Soot Sock", "blurb": "warm brown with soot-darkened feet"},
        "amber_eye": {"label": "Amber Eye", "blurb": "deep amber scales and brighter amber eyes"},
        "clinker_back": {"label": "Clinker Back", "blurb": "clinker-dark back fading to warm flanks"},
        "matchstick": {"label": "Matchstick", "blurb": "slim pale body with a red-tipped tail like a match"},
        "brazier_band": {"label": "Brazier Band", "blurb": "bronze bands across a coal-dark body"},
        "willow_ash": {"label": "Willow Ash", "blurb": "soft willow-grey with pale ash freckles"},
        "sparkler": {"label": "Sparkler", "blurb": "dark base that seems to shed tiny spark motes"},
        "terracotta": {"label": "Terracotta", "blurb": "earthy terracotta clay-orange, matte and warm"},
        "night_coal": {"label": "Night Coal", "blurb": "near-black night body with a single blue-hot ember spot"},
        "honey_smoke": {"label": "Honey Smoke", "blurb": "honey-gold washed with soft grey smoke"},
    },
    "raven": {
        "ink_quill": {"label": "Ink Quill", "blurb": "classic deep black with a glossy ink sheen"},
        "midnight_blue": {"label": "Midnight Blue", "blurb": "black that flashes deep midnight blue in light"},
        "storm_grey": {"label": "Storm Grey", "blurb": "charcoal-grey storm feathers, softer than pure black"},
        "silver_ruff": {"label": "Silver Ruff", "blurb": "black body with a silvered neck ruff"},
        "coal_wing": {"label": "Coal Wing", "blurb": "matte coal wings, slightly rough-edged"},
        "obsidian": {"label": "Obsidian", "blurb": "mirror-black like polished volcanic glass"},
        "frost_tip": {"label": "Frost Tip", "blurb": "black feathers tipped as if brushed with frost"},
        "ember_eye": {"label": "Ember Eye", "blurb": "dark raven with warm ember-orange eyes"},
        "moon_gleam": {"label": "Moon Gleam", "blurb": "black with a pale moonlit sheen on the primaries"},
        "slate_breast": {"label": "Slate Breast", "blurb": "slate-grey breast against darker wings"},
        "shadow_hood": {"label": "Shadow Hood", "blurb": "deeper black hood fading into softer body feathers"},
        "pewter_tail": {"label": "Pewter Tail", "blurb": "black body, pewter-grey tail feathers"},
        "violet_sheen": {"label": "Violet Sheen", "blurb": "black with a violet iridescent sheen"},
        "iron_beak": {"label": "Iron Beak", "blurb": "standard dark plumage, iron-grey beak accent"},
        "dusk_plume": {"label": "Dusk Plume", "blurb": "warm dusk-brown undertones in otherwise dark plumage"},
        "night_messenger": {"label": "Night Messenger", "blurb": "lean black courier look, sleek and spare"},
        "rookish": {"label": "Rookish", "blurb": "slightly looser, rook-like fluff around the face"},
        "gloss_cape": {"label": "Gloss Cape", "blurb": "high-gloss cape of feathers over matte underparts"},
        "ash_primary": {"label": "Ash Primary", "blurb": "black with ash-grey primary flight feathers"},
        "star_fleck": {"label": "Star Fleck", "blurb": "tiny pale flecks like distant stars in the wings"},
        "copper_gloss": {"label": "Copper Gloss", "blurb": "black with a copper flash when the light hits"},
        "quill_scratch": {"label": "Quill Scratch", "blurb": "slightly uneven quills, scholar's-bird look"},
        "harbor_black": {"label": "Harbor Black", "blurb": "wet-looking harbor black, salt-sheen suggestion"},
        "library_dust": {"label": "Library Dust", "blurb": "soft dusty black as if it lives among old books"},
        "chimney_soot": {"label": "Chimney Soot", "blurb": "sooty soft black, a little disreputable"},
        "winter_ruff": {"label": "Winter Ruff", "blurb": "fuller winter ruff around the neck"},
        "inkspot": {"label": "Inkspot", "blurb": "mostly black with one pale ink-spot on the chest"},
        "twilight": {"label": "Twilight", "blurb": "blue-black twilight blend across the wings"},
        "corvid_crown": {"label": "Corvid Crown", "blurb": "subtle crest of raised crown feathers"},
        "quiet_night": {"label": "Quiet Night", "blurb": "matte, sound-absorbing black — the silent one"},
    },
    "fox": {
        "rust_tail": {"label": "Rust Tail", "blurb": "classic rust-orange with a bright white-tipped tail"},
        "ember_fox": {"label": "Ember Fox", "blurb": "deeper ember-orange, almost firelit"},
        "snow_tip": {"label": "Snow Tip", "blurb": "pale cream body with snowy paw tips"},
        "charcoal_mask": {"label": "Charcoal Mask", "blurb": "orange coat with a bold charcoal face mask"},
        "honey_coat": {"label": "Honey Coat", "blurb": "soft honey-blonde fur, gentle and warm"},
        "dusk_red": {"label": "Dusk Red", "blurb": "dusky red-brown for evening hunts"},
        "silver_fox": {"label": "Silver Fox", "blurb": "silver-grey coat with darker points"},
        "cross_mark": {"label": "Cross Mark", "blurb": "classic cross-fox dark shoulder cross"},
        "sand_ear": {"label": "Sand Ear", "blurb": "sandy pale coat, large attentive ears"},
        "ginger_puff": {"label": "Ginger Puff", "blurb": "fluffy ginger, a little rounder and softer"},
        "night_pad": {"label": "Night Pad", "blurb": "darker rust with near-black paw pads and legs"},
        "cream_belly": {"label": "Cream Belly", "blurb": "bright orange with a full cream belly"},
        "briar_brush": {"label": "Briar Brush", "blurb": "brambly mottled coat, made for hedgerows"},
        "autumn_leaf": {"label": "Autumn Leaf", "blurb": "leaf-pile oranges and browns mixed together"},
        "frost_guard": {"label": "Frost Guard", "blurb": "thicker winter coat with frost-pale guard hairs"},
        "cinder_cheek": {"label": "Cinder Cheek", "blurb": "warm coat with cinder-dark cheek patches"},
        "gold_whisker": {"label": "Gold Whisker", "blurb": "golden-orange with bright whisker accents"},
        "shadow_tip": {"label": "Shadow Tip", "blurb": "orange body, shadow-dark ear and tail tips"},
        "meadow_soft": {"label": "Meadow Soft", "blurb": "soft muted orange like dry meadow grass"},
        "ink_sock": {"label": "Ink Sock", "blurb": "rust coat with ink-dark socks on the feet"},
        "copper_flare": {"label": "Copper Flare", "blurb": "bright copper that catches every bit of light"},
        "mist_grey": {"label": "Mist Grey", "blurb": "cool misty grey-orange blend"},
        "hearthside": {"label": "Hearthside", "blurb": "warm deep orange that looks forever firelit"},
        "thistle_patch": {"label": "Thistle Patch", "blurb": "slightly scruffy thistle-toned coat"},
        "moon_cream": {"label": "Moon Cream", "blurb": "pale cream-moon coat, almost white"},
        "russet_band": {"label": "Russet Band", "blurb": "russet band across the shoulders"},
        "velvet_ear": {"label": "Velvet Ear", "blurb": "soft coat with notably velvet-dark inner ears"},
        "spice_dust": {"label": "Spice Dust", "blurb": "spice-brown dusting over orange fur"},
        "lantern_eye": {"label": "Lantern Eye", "blurb": "standard orange with especially bright lantern eyes"},
        "kit_fluff": {"label": "Kit Fluff", "blurb": "extra-fluffy forever-kit look, soft and bright"},
    },
}


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


def look_ids(species: str) -> list[str]:
    return list(LOOKS.get(species, {}))


def default_look(species: str) -> str:
    ids = look_ids(species)
    return ids[0] if ids else ""


def look_meta(species: str, look: str | None) -> dict | None:
    if not species or not look:
        return None
    return LOOKS.get(species, {}).get(look)


def art_path(species: str, look: str) -> Path | None:
    if not species or not look:
        return None
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        path = ASSETS_DIR / species / f"{look}{ext}"
        if path.is_file():
            return path
    return None


class Familiars(commands.Cog):
    group = app_commands.Group(
        name="familiar",
        description="Adopt, care for, and scout with your familiar.",
    )

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
        rec.setdefault("look", None)
        rec.setdefault("friendship", 0)
        rec.setdefault("day", {"date": ""})
        if rec["day"].get("date") != today():
            rec["day"] = {"date": today()}
        # Migrate: assign a default look if adopted without one.
        if rec.get("species") and not rec.get("look"):
            rec["look"] = default_look(rec["species"])
        elif rec.get("look") and rec.get("species"):
            if rec["look"] not in LOOKS.get(rec["species"], {}):
                rec["look"] = default_look(rec["species"])
        return rec

    def familiar_of(self, user_id: int) -> dict | None:
        """Public lookup for other cogs (e.g. /profile) - None if not adopted."""
        rec = self.state["members"].get(str(user_id))
        if not rec or not rec.get("species"):
            return None
        # Ensure look is populated for callers without going through record().
        if not rec.get("look"):
            rec["look"] = default_look(rec["species"])
        return rec

    def fam_word(self, rec: dict) -> str:
        meta = FAMILIARS[rec["species"]]
        if rec.get("name"):
            return f"**{rec['name']}** the {meta['label']}"
        return f"{meta['article']} {meta['label']}"

    def look_label(self, rec: dict) -> str:
        meta = look_meta(rec.get("species"), rec.get("look"))
        return meta["label"] if meta else "Unknown"

    def art_file(self, rec: dict) -> discord.File | None:
        path = art_path(rec.get("species") or "", rec.get("look") or "")
        if path is None:
            return None
        return discord.File(path, filename="familiar.png")

    def _attach_art(self, embed: discord.Embed, rec: dict) -> discord.File | None:
        art = self.art_file(rec)
        if art is not None:
            embed.set_image(url="attachment://familiar.png")
        return art

    # ------------------------------------------------------------ /familiar status

    @group.command(name="status", description="Check in on your familiar — friendship and today's care.")
    async def status(self, interaction: discord.Interaction):
        rec = self.record(interaction.user.id)
        if not rec["species"]:
            await interaction.response.send_message(
                "You haven't adopted a familiar yet — `/familiar adopt` to pick one.",
                ephemeral=True,
            )
            return

        meta = FAMILIARS[rec["species"]]
        _, tier_name, _, _ = _tier(rec["friendship"])
        day = rec["day"]
        checklist = "  ".join(
            f"{'✅' if day.get(k) else '⬜'} {k.capitalize()}"
            for k in ("fed", "pet", "played", "scouted")
        )
        look = look_meta(rec["species"], rec.get("look"))
        look_line = (
            f"Look: **{look['label']}** — {look['blurb']}\n\n"
            if look else ""
        )
        embed = discord.Embed(
            title=f"{meta['emoji']} {self.fam_word(rec)}",
            description=(
                f"{look_line}"
                f"Friendship: **{rec['friendship']}** ({tier_name})\n\n"
                f"Today: {checklist}\n\n"
                "`/familiar feed` · `/familiar pet` · `/familiar play` · `/familiar scout`\n"
                "`/familiar look` to change its appearance · `/familiar looks` to browse"
                + ("" if rec.get("name") else "\n\nGive it a name with `/familiar name`.")
            ),
            color=WARM,
        )
        art = self._attach_art(embed, rec)
        kwargs = {"embed": embed, "ephemeral": True}
        if art is not None:
            kwargs["file"] = art
        await interaction.response.send_message(**kwargs)

    # ------------------------------------------------------------ /familiar adopt

    @group.command(name="adopt", description="Adopt a familiar for good (once).")
    @app_commands.describe(
        choose="Which familiar bonds with you",
        name="Optional name to give it right away",
        look="Optional starting look (leave blank for a random one)",
    )
    @app_commands.choices(choose=[
        app_commands.Choice(name="🦎 Salamander", value="salamander"),
        app_commands.Choice(name="🐦 Raven", value="raven"),
        app_commands.Choice(name="🦊 Fox", value="fox"),
    ])
    async def adopt(
        self,
        interaction: discord.Interaction,
        choose: app_commands.Choice[str],
        name: str | None = None,
        look: str | None = None,
    ):
        rec = self.record(interaction.user.id)
        if rec["species"]:
            await interaction.response.send_message(
                f"You already have {self.fam_word(rec)} - familiars bond for good.",
                ephemeral=True,
            )
            return

        if name is not None:
            name = name.strip()
            if not (1 <= len(name) <= NAME_MAX_LEN):
                await interaction.response.send_message(
                    f"Names need to be 1-{NAME_MAX_LEN} characters.",
                    ephemeral=True,
                )
                return

        species = choose.value
        if look:
            look = look.strip().lower().replace(" ", "_").replace("-", "_")
            if look not in LOOKS[species]:
                # try match by label
                match = next(
                    (k for k, v in LOOKS[species].items()
                     if v["label"].lower() == look.replace("_", " ")),
                    None,
                )
                if not match:
                    await interaction.response.send_message(
                        f"That's not a known {FAMILIARS[species]['label'].lower()} look. "
                        "Browse with `/familiar looks` after adopting, or leave look blank.",
                        ephemeral=True,
                    )
                    return
                look = match
        else:
            look = self.rng_module.choice(look_ids(species))

        rec["species"] = species
        rec["look"] = look
        rec["friendship"] = 0
        if name:
            rec["name"] = name
        self.save()
        meta = FAMILIARS[species]
        lm = LOOKS[species][look]
        embed = discord.Embed(
            title=f"{meta['emoji']} A bond is formed",
            description=(
                f"{self.fam_word(rec)} has chosen to stay with "
                f"{interaction.user.mention}.\n\n"
                f"Look: **{lm['label']}** — {lm['blurb']}\n\n"
                "Feed it, pet it, and play with it - once a day each raises its "
                "friendship. The more it trusts you, the more it brings back "
                "when you send it out with `/familiar scout`.\n"
                "Change how it looks anytime with `/familiar look`."
                + ("" if name else " Give it a name any time with `/familiar name`.")
            ),
            color=WARM,
        )
        art = self._attach_art(embed, rec)
        kwargs = {"embed": embed}
        if art is not None:
            kwargs["file"] = art
        await interaction.response.send_message(**kwargs)

    @adopt.autocomplete("look")
    async def adopt_look_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        # Species may not be resolved yet; offer across all, preferring chosen if present.
        species = None
        for opt in interaction.namespace.__dict__.values() if hasattr(interaction, "namespace") else []:
            pass
        try:
            chosen = getattr(interaction.namespace, "choose", None)
            if isinstance(chosen, str) and chosen in LOOKS:
                species = chosen
            elif hasattr(chosen, "value") and chosen.value in LOOKS:
                species = chosen.value
        except Exception:
            species = None

        cur = (current or "").lower().strip()
        out: list[app_commands.Choice[str]] = []
        species_list = [species] if species else list(LOOKS)
        for sp in species_list:
            for kid, meta in LOOKS[sp].items():
                label = f"{meta['label']} ({FAMILIARS[sp]['label']})"
                hay = f"{kid} {meta['label']} {meta['blurb']}".lower()
                if cur and cur not in hay:
                    continue
                out.append(app_commands.Choice(name=label[:100], value=kid))
                if len(out) >= 25:
                    return out
        return out

    # ------------------------------------------------------------ /familiar name

    @group.command(name="name", description="Give your familiar a name (or rename it).")
    @app_commands.describe(name="What it answers to")
    async def rename(self, interaction: discord.Interaction, name: str):
        rec = self.record(interaction.user.id)
        if not rec["species"]:
            await interaction.response.send_message(
                "Adopt a familiar first with `/familiar adopt`, then name it.",
                ephemeral=True,
            )
            return
        name = name.strip()
        if not (1 <= len(name) <= NAME_MAX_LEN):
            await interaction.response.send_message(
                f"Names need to be 1-{NAME_MAX_LEN} characters.",
                ephemeral=True,
            )
            return
        rec["name"] = name
        self.save()
        await interaction.response.send_message(
            f"From now on, {FAMILIARS[rec['species']]['label'].lower()} answers to **{name}**."
        )

    # ------------------------------------------------------------ /familiar look

    @group.command(name="look", description="Change your familiar's appearance.")
    @app_commands.describe(look="Which look to wear")
    async def change_look(self, interaction: discord.Interaction, look: str):
        rec = self.record(interaction.user.id)
        if not rec["species"]:
            await interaction.response.send_message(
                "Adopt a familiar first with `/familiar adopt`.",
                ephemeral=True,
            )
            return
        species = rec["species"]
        key = look.strip().lower().replace(" ", "_").replace("-", "_")
        if key not in LOOKS[species]:
            match = next(
                (k for k, v in LOOKS[species].items()
                 if v["label"].lower() == look.strip().lower()),
                None,
            )
            if not match:
                await interaction.response.send_message(
                    f"That's not a {FAMILIARS[species]['label'].lower()} look. "
                    "Try `/familiar looks` to browse, or autocomplete `/familiar look`.",
                    ephemeral=True,
                )
                return
            key = match
        if rec.get("look") == key:
            lm = LOOKS[species][key]
            await interaction.response.send_message(
                f"{self.fam_word(rec)} is already wearing **{lm['label']}**.",
                ephemeral=True,
            )
            return
        rec["look"] = key
        self.save()
        lm = LOOKS[species][key]
        embed = discord.Embed(
            title=f"{FAMILIARS[species]['emoji']} A new look",
            description=(
                f"{self.fam_word(rec)} settles into **{lm['label']}**.\n"
                f"*{lm['blurb']}*"
            ),
            color=WARM,
        )
        art = self._attach_art(embed, rec)
        kwargs = {"embed": embed}
        if art is not None:
            kwargs["file"] = art
        await interaction.response.send_message(**kwargs)

    @change_look.autocomplete("look")
    async def change_look_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        rec = self.familiar_of(interaction.user.id)
        if not rec:
            return []
        species = rec["species"]
        cur = (current or "").lower().strip()
        out: list[app_commands.Choice[str]] = []
        for kid, meta in LOOKS[species].items():
            hay = f"{kid} {meta['label']} {meta['blurb']}".lower()
            if cur and cur not in hay:
                continue
            mark = " ✓" if kid == rec.get("look") else ""
            out.append(app_commands.Choice(
                name=f"{meta['label']}{mark}"[:100],
                value=kid,
            ))
            if len(out) >= 25:
                break
        return out

    @group.command(name="looks", description="Browse the appearance catalog for your familiar.")
    @app_commands.describe(page="Catalog page (1–2)")
    async def looks_catalog(self, interaction: discord.Interaction, page: int = 1):
        rec = self.record(interaction.user.id)
        if not rec["species"]:
            await interaction.response.send_message(
                "Adopt a familiar first with `/familiar adopt`.",
                ephemeral=True,
            )
            return
        species = rec["species"]
        ids = look_ids(species)
        per = 15
        pages = max(1, (len(ids) + per - 1) // per)
        page = max(1, min(pages, int(page or 1)))
        chunk = ids[(page - 1) * per: page * per]
        lines = []
        for kid in chunk:
            meta = LOOKS[species][kid]
            mark = " ← current" if kid == rec.get("look") else ""
            lines.append(f"• **{meta['label']}**{mark} — {meta['blurb']}")
        embed = discord.Embed(
            title=f"{FAMILIARS[species]['emoji']} {FAMILIARS[species]['label']} looks",
            description=(
                f"{self.fam_word(rec)} · page {page}/{pages}\n\n"
                + "\n".join(lines)
                + "\n\nPick one with `/familiar look`."
            ),
            color=WARM,
        )
        art = self._attach_art(embed, rec)
        kwargs = {"embed": embed, "ephemeral": True}
        if art is not None:
            kwargs["file"] = art
        await interaction.response.send_message(**kwargs)

    # ------------------------------------------------------------ feed/pet/play

    async def _care(self, interaction: discord.Interaction, action: str, lines: dict, already: str):
        rec = self.record(interaction.user.id)
        if not rec["species"]:
            await interaction.response.send_message(
                "You don't have a familiar yet - `/familiar adopt` to pick one.",
                ephemeral=True,
            )
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

    @group.command(name="feed", description="Feed your familiar. Once a day.")
    async def feed(self, interaction: discord.Interaction):
        await self._care(interaction, "fed", FEED_LINES, "Already fed today - its belly's full.")

    @group.command(name="pet", description="Pet your familiar. Once a day.")
    async def pet(self, interaction: discord.Interaction):
        await self._care(interaction, "pet", PET_LINES, "Already had its fill of pets today.")

    @group.command(name="play", description="Play with your familiar. Once a day.")
    async def play(self, interaction: discord.Interaction):
        await self._care(interaction, "played", PLAY_LINES, "It's already worn out from playing today.")

    # ------------------------------------------------------------ /familiar scout

    @group.command(name="scout", description="Send your familiar out to bring something back. Once a day.")
    async def scout(self, interaction: discord.Interaction):
        rec = self.record(interaction.user.id)
        if not rec["species"]:
            await interaction.response.send_message(
                "You don't have a familiar yet - `/familiar adopt` to pick one.",
                ephemeral=True,
            )
            return
        if rec["day"].get("scouted"):
            await interaction.response.send_message(
                "It's already out (or resting from its last trip) - try again tomorrow.",
                ephemeral=True,
            )
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
                    store.record(
                        house=house,
                        delta=bonus,
                        actor_id=self.bot.user.id if self.bot.user else 0,
                        target_id=interaction.user.id,
                        reason=f"{fam} scouted something rare",
                    )
                    awarded = bonus
        else:
            lines.append(SCOUT_EMPTY[species].format(fam=fam))

        embed = discord.Embed(
            title=f"{FAMILIARS[species]['emoji']} Scouting report",
            description="\n".join(lines),
            color=WARM,
        )
        art = self._attach_art(embed, rec)
        embed.set_footer(
            text=f"Friendship: {tier_name}"
            + (f"  ·  +{awarded} bonus points" if awarded else "")
        )
        kwargs = {"embed": embed}
        if art is not None:
            kwargs["file"] = art
        await interaction.response.send_message(**kwargs)


async def setup(bot: commands.Bot):
    await bot.add_cog(Familiars(bot))
