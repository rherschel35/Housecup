"""
Patronuses, cast from the same three words that chose your wand.

    /patronus            - cast yours, or see it again
    /patronus @member    - see someone else's

The wand read the words for temperament. The patronus reads them for what
you'd protect - the thing that keeps you steady. No new words are asked
for; the ones given to the wandmaker are reused, so the two always belong
to the same person.

One per person, for good. /wandreset releases both, since the patronus
comes from the wand's words. Purely cosmetic.
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

log = logging.getLogger("velmora.patronus")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
PATRONUS_PATH = STATE_DIR / "patronus.json"

MODEL = os.getenv("PATRONUS_MODEL", "claude-haiku-4-5-20251001")
SILVER = 0xC4CCD6

# Real animals only, each with what it guards. The reader must choose from
# these, the same way the wandmaker chooses from real woods and cores.
ANIMALS = {
    "Hare":           "quick and watchful; guards the people who can't run",
    "Fox":            "clever and self-reliant; protects by outthinking trouble",
    "Otter":          "playful and devoted; keeps its family close",
    "Stag":           "steady and proud; stands between danger and the herd",
    "Doe":            "gentle and alert; its strength is quiet vigilance",
    "Wolf":           "loyal to its pack above everything",
    "Elephant":       "remembers everyone; never abandons the ones who fall behind",
    "Owl":            "patient and wise; sees what others miss in the dark",
    "Raven":          "curious and sharp; carries secrets safely",
    "Swan":           "graceful, fiercely protective of what it loves",
    "Heron":          "still and patient; waits for exactly the right moment",
    "Hawk":           "focused and fast; never loses sight of its own",
    "Falcon":         "fearless in a dive; strikes first to keep others safe",
    "Eagle":          "sees the whole of things from far above",
    "Horse":          "strong and free; carries others through hard ground",
    "Badger":         "stubborn and fierce; defends its home to the last",
    "Hedgehog":       "small, soft inside, impossible to hurt",
    "Bear":           "enormous warmth and enormous strength in the same body",
    "Lynx":           "solitary and perceptive; knows what's coming before it arrives",
    "Snow leopard":   "rare and quiet; at home where it's hardest to live",
    "Tiger":          "courage that doesn't need an audience",
    "Lion":           "leads from the front and takes the blows first",
    "Whale":          "vast and calm; its song carries across whole oceans",
    "Dolphin":        "joyful and social; never leaves one of its own behind",
    "Seal":           "at ease in two worlds at once",
    "Tortoise":       "slow, certain, and older than any trouble",
    "Cat":            "independent; chooses its people and keeps them",
    "Hound":          "faithful beyond reason; follows its person anywhere",
    "Mouse":          "brave in small ways, every single day",
    "Squirrel":       "always preparing, so no one goes without",
    "Bat":            "finds its way where there is no light at all",
    "Crane":          "devoted for life; a symbol of long faithfulness",
    "Magpie":         "clever and bright; collects what others overlook",
    "Robin":          "cheerful in the coldest season",
    "Hummingbird":    "tiny, tireless, and full of more life than seems possible",
    "Octopus":        "endlessly inventive; solves what can't be solved",
    "Manta ray":      "moves through the deep with calm, gliding certainty",
    "Ram":            "headstrong; meets every obstacle straight on",
    "Moose":          "gentle until something it loves is threatened",
    "Salmon":         "swims home against every current",
}

# Patronuses that share a shape with a house emblem.
EMBLEM_MATCH = {"Elephant": "vashara", "Stag": "veyren", "Doe": "veyren", "Wolf": "thornmere"}

READING_PROMPT = """You are reading a student's patronus at Velmora, a school of magic. A patronus is a guardian made of silver light, and it takes the shape of what a person would protect - the thing that keeps them steady when everything else goes dark.

The student once gave the wandmaker three words. The wand read them for temperament. You are reading the SAME words for something different: what this person holds dear, and how they guard it.

Do NOT match their words literally. If they wrote an animal's name, do not simply hand that animal back. Read the heart behind the words.

Choose exactly one animal from this list:
{animals}

Then write:
- "form": one sentence describing how the patronus appears or moves when it first takes shape. Silver, specific, and a little strange.
- "reading": two or three sentences, speaking to the student directly, on why this is the shape their protection takes. Warm, a little uncanny. Refer to what the words revealed, not to the words themselves.

The student's words: "{words}"

Reply with ONLY a JSON object:
{{"animal": "...", "form": "...", "reading": "..."}}"""


def fallback_patronus(words: str) -> dict:
    """Cast without the API. Same words, same patronus - and salted so it
    doesn't simply shadow whatever the wand fallback picked."""
    toks = sorted(w.lower() for w in re.findall(r"[\w']+", words or ""))
    digest = hashlib.sha256(("patronus:" + " ".join(toks)).encode()).digest()
    animal = sorted(ANIMALS)[digest[0] % len(ANIMALS)]
    return {
        "animal": animal,
        "form": f"The {animal.lower()} gathers itself out of the silver light and stays close.",
        "reading": f"The {animal.lower()} is {ANIMALS[animal]}. That is what your words were guarding.",
    }


def _clean(raw: dict) -> dict | None:
    """Accept the model's answer only if the animal is real and on the list."""
    try:
        animal = str(raw["animal"]).strip()
        form = str(raw["form"]).strip()
        reading = str(raw["reading"]).strip()
    except (KeyError, TypeError):
        return None
    match = next((a for a in ANIMALS if a.lower() == animal.lower()), None)
    if not match or not form or not reading:
        return None
    return {"animal": match, "form": form[:300], "reading": reading[:600]}


class Patronus(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.patronuses = self._load()

    def _load(self) -> dict:
        try:
            with open(PATRONUS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.exception("Patronus records unreadable - starting empty.")
            return {}

    def save(self) -> None:
        try:
            PATRONUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = PATRONUS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.patronuses, f, indent=2)
            os.replace(tmp, PATRONUS_PATH)
        except OSError:
            log.exception("Could not save patronus records.")

    def patronus_of(self, user_id: int) -> dict | None:
        return self.patronuses.get(str(user_id))

    def release(self, user_id: int) -> bool:
        gone = self.patronuses.pop(str(user_id), None) is not None
        if gone:
            self.save()
        return gone

    async def cast(self, words: str) -> dict:
        wands = self.bot.get_cog("Wands")
        client = getattr(wands, "client", None)
        if client is not None:
            prompt = READING_PROMPT.format(
                animals="\n".join(f"- {k}: {v}" for k, v in ANIMALS.items()),
                words=words.replace('"', "'")[:200],
            )
            try:
                response = await client.messages.create(
                    model=MODEL, max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(getattr(b, "text", "") for b in response.content)
                match = re.search(r"\{.*\}", text, re.S)
                if match:
                    result = _clean(json.loads(match.group(0)))
                    if result:
                        return result
                log.warning("Patronus reading came back unusable; using the fallback.")
            except Exception:
                log.exception("Patronus reading failed; using the fallback.")
        return fallback_patronus(words)

    def embed_for(self, member, patronus: dict, fresh: bool = False) -> discord.Embed:
        embed = discord.Embed(
            title=("Silver light spills from the wand…" if fresh
                   else f"{member.display_name}'s patronus"),
            description=(f"**A silver {patronus['animal'].lower()}**\n"
                         f"*{patronus['form']}*\n\n{patronus['reading']}"),
            color=SILVER,
        )
        house = EMBLEM_MATCH.get(patronus["animal"])
        if house:
            from cogs.store import HOUSES
            embed.add_field(name="​",
                            value=f"It shares its shape with {HOUSES[house]['emoji']} "
                                  f"House {HOUSES[house]['name']}'s emblem.", inline=False)
        if fresh:
            embed.set_footer(text=f"{member.display_name}'s patronus • cast from the words "
                                  "that chose their wand")
        return embed

    @app_commands.command(name="patronus",
                          description="Cast your patronus from the words that chose your wand.")
    @app_commands.describe(member="Whose patronus to see (leave blank for your own)")
    async def patronus(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user

        existing = self.patronus_of(target.id)
        if existing:
            await interaction.response.send_message(embed=self.embed_for(target, existing))
            return

        if member and member.id != interaction.user.id:
            await interaction.response.send_message(
                f"{member.display_name} hasn't cast a patronus yet.", ephemeral=True
            )
            return

        wands = self.bot.get_cog("Wands")
        wand = wands.wand_of(target.id) if wands else None
        if not wand or not wand.get("words"):
            await interaction.response.send_message(
                "Your patronus answers to the same words that chose your wand — "
                "and no wand has chosen you yet. Start with `/wand`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        result = await self.cast(wand["words"])
        self.patronuses[str(target.id)] = result
        self.save()
        await interaction.followup.send(embed=self.embed_for(target, result, fresh=True))


async def setup(bot: commands.Bot):
    await bot.add_cog(Patronus(bot))
