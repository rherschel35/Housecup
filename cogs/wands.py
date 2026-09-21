"""
Wands. The wand chooses the wizard - from three words.

    /wand              - receive your wand, or see it again
    /wand member:@x    - see someone else's
    /wandreset @x      - staff, let someone be chosen again

A member gives any three words - in any order, in a sentence or not - and
the wand is read from the temperament behind them. Claude does the reading
when it's available, so the wand is a genuine interpretation with a few
lines on why it chose you. If the API is unreachable the wand is still
chosen, deterministically, from the words themselves.

One wand per person, for good. Purely cosmetic.
"""

import hashlib
import json
import logging
import os
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.wands")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
WANDS_PATH = STATE_DIR / "wands.json"

MODEL = os.getenv("WAND_MODEL", "claude-haiku-4-5-20251001")

# Each entry carries the temperament it suits, which both the reader and
# the fallback use, so a wand always comes with a reason.
WOODS = {
    "Ash":        "steady and stubborn; loyal to one owner, sulks with anyone else",
    "Blackthorn": "suited to those who have been through something hard and come out sharper",
    "Cedar":      "discerning and hard to fool; favours strength of character",
    "Cherry":     "rare and striking; beautiful, with a temper under the bloom",
    "Elm":        "dignified and precise; dislikes carelessness",
    "Hawthorn":   "contradictory - healing and cursing come equally easily to it",
    "Hazel":      "sensitive to its owner's moods; wilts under neglect",
    "Holly":      "protective; drawn to those fighting their own worst impulses",
    "Larch":      "quietly courageous; brings out confidence its owner didn't know they had",
    "Maple":      "restless; happiest with a traveller who never stays still",
    "Oak":        "stout and reliable; a friend in hard times",
    "Pine":       "independent and original; drawn to the solitary and the curious",
    "Rowan":      "clear-headed and warm-hearted; wards off the darker arts",
    "Walnut":     "clever and inventive; follows its owner's intellect anywhere",
    "Willow":     "for those who doubt themselves more than they should",
    "Yew":        "fierce and enduring; chooses those who will not back down",
}

CORES = {
    "Moonstone thread":    "for the curious and the unpredictable - it loves an experiment",
    "Wolf's whisker":      "for the watchful and the loyal; slow to trust, impossible to turn",
    "Stag's velvet":       "for the gentle-hearted; its strength is in its steadiness",
    "Starglass shard":     "for inventors and dreamers; it wants to make something new",
    "Old elephant's hair": "for those who remember - it never forgets a single spell",
    "Hearthcoal ember":    "for the warm and the fierce; it runs hot",
    "Storm-feather":       "for the bold and the quick; powerful, and a little wild",
    "Riverpearl":          "for the patient; calm on the surface, deep underneath",
    "Nightbloom root":     "for those at home in the dark and the quiet",
    "Bell-bronze filing":  "for those who speak up; it answers clearly and loudly",
}

FLEXIBILITIES = ["unyielding", "rigid", "firm", "sturdy", "supple", "pliant", "whippy"]

MIN_LENGTH, MAX_LENGTH = 9.0, 14.0  # inches, in quarter steps


def _fmt_length(inches: float) -> str:
    whole = int(inches)
    frac = {0.0: "", 0.25: "¼", 0.5: "½", 0.75: "¾"}[round(inches - whole, 2)]
    return f"{whole}{frac} inches"


def _words_of(text: str) -> list[str]:
    # Letters in any language, and digits - "any three words" means anything.
    return re.findall(r"[\w']+", text or "")


def fallback_wand(words: str) -> dict:
    """Choose a wand from the words alone, with no API. The same words, in
    any order, always give the same wand."""
    key = " ".join(sorted(w.lower() for w in _words_of(words))) or "silence"
    digest = hashlib.sha256(key.encode()).digest()

    wood = sorted(WOODS)[digest[0] % len(WOODS)]
    core = sorted(CORES)[digest[1] % len(CORES)]
    steps = int((MAX_LENGTH - MIN_LENGTH) * 4)
    length = MIN_LENGTH + (digest[2] % (steps + 1)) / 4
    flex = FLEXIBILITIES[digest[3] % len(FLEXIBILITIES)]

    return {
        "wood": wood,
        "core": core,
        "length": length,
        "flexibility": flex,
        "reading": (f"{wood} is {WOODS[wood]}. The {core.lower()} at its heart is "
                    f"{CORES[core]}. It took one look at your words and decided."),
    }


def _clean_ai_wand(raw: dict) -> dict | None:
    """Accept the model's answer only if every part is a real catalog value."""
    try:
        wood, core = raw["wood"], raw["core"]
        flex = raw["flexibility"].lower()
        length = round(float(raw["length"]) * 4) / 4
        reading = str(raw["reading"]).strip()
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if wood not in WOODS or core not in CORES or flex not in FLEXIBILITIES:
        return None
    if not MIN_LENGTH <= length <= MAX_LENGTH or not reading:
        return None
    return {"wood": wood, "core": core, "length": length,
            "flexibility": flex, "reading": reading[:600]}


READING_PROMPT = """You are the wandmaker of Velmora, a school of magic. A student has given you three words - any words, in any order. From them, read their temperament and choose the wand that would choose them.

Do NOT match their words literally. If they write "fire" do not simply pick the warmest core; if they name a wood or a creature, do not just hand it back. Read the character behind the words - what someone who reaches for those words is like - and choose from that. The wand chooses the wizard, not the other way round.

Choose exactly one of each:

WOODS:
{woods}

CORES:
{cores}

FLEXIBILITY: {flex}
LENGTH: between 9 and 14 inches, in quarter inches.

Then write a short reading - two or three sentences, in the wandmaker's voice, warm and a little uncanny - explaining why this wand chose them. Speak to the student directly. Refer to what their words revealed, not to the words themselves.

The student's words: "{words}"

Reply with ONLY a JSON object, no other text:
{{"wood": "...", "core": "...", "flexibility": "...", "length": 11.25, "reading": "..."}}"""


class Wands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.wands = self._load()
        self.client = None
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            try:
                from anthropic import AsyncAnthropic
                self.client = AsyncAnthropic(api_key=api_key)
            except Exception:
                log.exception("Could not start the Anthropic client - using the fallback reader.")
        else:
            log.info("No ANTHROPIC_API_KEY - wands will use the fallback reader.")

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(WANDS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.exception("Wand records unreadable - starting empty.")
            return {}

    def save(self) -> None:
        try:
            WANDS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = WANDS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.wands, f, indent=2)
            os.replace(tmp, WANDS_PATH)
        except OSError:
            log.exception("Could not save wand records.")

    def wand_of(self, user_id: int) -> dict | None:
        return self.wands.get(str(user_id))

    def short_name(self, user_id: int) -> str | None:
        """'holly & storm-feather' - for showing beside a name elsewhere."""
        w = self.wand_of(user_id)
        if not w:
            return None
        return f"{w['wood'].lower()} & {w['core'].lower()}"

    # ------------------------------------------------------------- reading

    async def read_wand(self, words: str) -> dict:
        if self.client is not None:
            prompt = READING_PROMPT.format(
                woods="\n".join(f"- {k}: {v}" for k, v in WOODS.items()),
                cores="\n".join(f"- {k}: {v}" for k, v in CORES.items()),
                flex=", ".join(FLEXIBILITIES),
                words=words.replace('"', "'")[:200],
            )
            try:
                response = await self.client.messages.create(
                    model=MODEL,
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(getattr(b, "text", "") for b in response.content)
                match = re.search(r"\{.*\}", text, re.S)
                if match:
                    wand = _clean_ai_wand(json.loads(match.group(0)))
                    if wand:
                        return wand
                log.warning("Wand reading came back unusable; using the fallback.")
            except Exception:
                log.exception("Wand reading failed; using the fallback.")
        return fallback_wand(words)

    def embed_for(self, member, wand: dict, fresh: bool = False) -> discord.Embed:
        from cogs.store import HOUSES
        store = self.bot.get_cog("Store")
        house = store.member_house(member) if store else None
        color = HOUSES[house]["color"] if house in HOUSES else 0x8B6F47

        embed = discord.Embed(
            title=("The wand has chosen." if fresh else f"{member.display_name}'s wand"),
            description=(
                f"**{wand['wood']}, {wand['core'].lower()} core**\n"
                f"{_fmt_length(wand['length'])}, {wand['flexibility']}\n\n"
                f"*{wand['reading']}*"
            ),
            color=color,
        )
        if fresh:
            embed.set_footer(text=f"{member.display_name}'s wand • it's yours for good")
        return embed

    # ------------------------------------------------------------ commands

    @app_commands.command(name="wand", description="Receive your wand, or look at someone's.")
    @app_commands.describe(member="Whose wand to look at (leave blank for your own)")
    async def wand(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user

        existing = self.wand_of(target.id)
        if existing:
            await interaction.response.send_message(embed=self.embed_for(target, existing))
            return

        if member and member.id != interaction.user.id:
            await interaction.response.send_message(
                f"{member.display_name} hasn't been chosen by a wand yet.", ephemeral=True
            )
            return

        await interaction.response.send_modal(WandModal(self))

    @app_commands.command(name="wandreset", description="Let a wand choose someone again (their patronus goes too).")
    @app_commands.describe(member="Whose wand to release")
    async def wandreset(self, interaction: discord.Interaction, member: discord.Member):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That one's for staff.", ephemeral=True)
            return
        if self.wands.pop(str(member.id), None) is None:
            await interaction.response.send_message(
                f"{member.display_name} doesn't have a wand to release.", ephemeral=True
            )
            return
        self.save()
        # The patronus is read from the wand's words, so it goes too.
        patronus = self.bot.get_cog("Patronus")
        released_patronus = patronus.release(member.id) if patronus else False
        await interaction.response.send_message(
            f"{member.display_name}'s wand has been released"
            + (" \u2014 and their patronus with it" if released_patronus else "")
            + ". They can be chosen again.",
            ephemeral=True,
        )


class WandModal(discord.ui.Modal, title="The wand chooses the wizard"):
    words = discord.ui.TextInput(
        label="Tell me any three words",
        placeholder="Any order. A sentence, or not. Whatever comes to you.",
        max_length=120,
    )

    def __init__(self, cog: Wands):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        text = str(self.words).strip()
        if not _words_of(text):
            await interaction.response.send_message(
                "The wands need words to listen to. Try again with `/wand`.", ephemeral=True
            )
            return

        # Someone may have double-submitted; the first wand stands.
        if self.cog.wand_of(interaction.user.id):
            await interaction.response.send_message(
                embed=self.cog.embed_for(interaction.user, self.cog.wand_of(interaction.user.id))
            )
            return

        await interaction.response.defer(thinking=True)
        wand = await self.cog.read_wand(text)
        wand["words"] = text
        self.cog.wands[str(interaction.user.id)] = wand
        self.cog.save()
        await interaction.followup.send(embed=self.cog.embed_for(interaction.user, wand, fresh=True))


async def setup(bot: commands.Bot):
    await bot.add_cog(Wands(bot))
