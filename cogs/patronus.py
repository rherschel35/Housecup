"""
Patronuses, cast from the same three words that chose your wand.

    /patronus            - cast yours, or see it again
    /patronus @member    - see someone else's

The wand read the words for temperament. The patronus reads them for what
you'd protect - the thing that keeps you steady. No new words are asked
for; the ones given to the wandmaker are reused, so the two always belong
to the same person.

One per person, for good. /staff identity wandreset releases both, since the patronus
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
ASSETS_DIR = Path(__file__).resolve().parent.parent / "patronus_art_assets"

MODEL = os.getenv("PATRONUS_MODEL", "claude-haiku-4-5-20251001")
SILVER = 0xC4CCD6

# Headmaster-assigned patronuses (animal need not be on ANIMALS).
# Optional "art" filename under patronus_art_assets/. Overrides stored casts.
CUSTOM_PATRONUSES: dict[str, dict] = {
    "929771936408547328": {  # Rowie / Class Pres. Rowwie
        "animal": "Siren",
        "form": (
            "Silver scales shimmer beneath dark, restless waves as the siren "
            "rises from the sea, her voice carrying with the sharp clarity of "
            "a bell across open water. She moves with deliberate confidence, "
            "never hesitating, never asking permission from the tide."
        ),
        "reading": (
            "You possess a nature that refuses to be softened for anyone else. "
            "You say what you mean, pursue what you want, and meet competition "
            "with a smile that suggests you already intend to win. Your strength "
            "is your directness. You do not circle the truth when you can simply "
            "reach for it.\n\n"
            "**Your guard is this:** you are the current beneath the surface. "
            "You may be calm, but you are never passive. You protect what matters "
            "by confronting what threatens it, by refusing to back down when "
            "challenged, and by making yourself impossible to overlook.\n\n"
            "Like the sea, you do not need to raise your voice to prove your "
            "power. You simply keep moving forward until everything in your path "
            "learns to move with you."
        ),
        "art": "Siren_Rowie.png",
        "defeat_art": "Siren_Rowie_Defeat.png",
    },
    "206828442065305600": {  # Pod / Vish's Head Boy Pod
        "animal": "Griffin",
        "form": (
            "A powerful griffin emerges from the light, its wings spreading "
            "wide as its lion-like paws settle firmly against the ground. There "
            "is something regal in its presence, but not distant. Its gaze is "
            "sharp, curious, and almost mischievous, as though it has already "
            "figured out the room before anyone else has."
        ),
        "reading": (
            "You carry an unusual combination of confidence and curiosity. You "
            "want to experience everything, meet people, build connections, and "
            "leave something behind that matters. You can be loud, ridiculous, "
            "flirtatious, and completely unserious one moment, then surprisingly "
            "thoughtful and fiercely determined the next.\n\n"
            "**Your guard is this:** you don't give up on people easily. You may "
            "become frustrated, hurt, or disappointed, but the people you truly "
            "claim as your own tend to stay yours. You have a stubborn instinct "
            "to protect, encourage, and fight for the things you believe deserve "
            "a chance.\n\n"
            "The griffin represents the part of you that refuses to be reduced "
            "to one thing. **You can be both tenderness and force, humor and "
            "seriousness, ambition and loyalty.** You adapt without losing "
            "yourself.\n\n"
            "And beneath everything is a desire to live a life that actually "
            "feels *alive*. To make memories. To love loudly. To build something "
            "you're proud of. To be surrounded by people who matter and to know "
            "that, when the moment comes, you had the courage to choose your "
            "own path.\n\n"
            "**The griffin does not simply guard what it loves. It rises above "
            "it, sees where it is going, and then refuses to turn back.**"
        ),
        "art": "Griffin_Pod.jpg",
        "defeat_art": "Griffin_Pod_Defeat.jpg",
    },
    "534422209968734209": {  # Mara
        "animal": "Margarita",
        "form": (
            "Golden-green light gathers in a shimmering swirl, forming the "
            "unmistakable shape of a margarita glass. Its silvery magic glows "
            "around the rim like frost, while a tiny lime wedge rests against "
            "the edge, bright and mischievous. The Patronus sparkles with the "
            "kind of energy that makes it impossible to tell whether it just "
            "arrived to protect you or convince you to stay out another three "
            "hours."
        ),
        "reading": (
            "You carry a warmth that draws people in without trying. You're "
            "playful, silly, and effortlessly flirty, with a talent for turning "
            "ordinary moments into something worth remembering. You love people, "
            "conversation, laughter, and the simple joy of being surrounded by "
            "good company.\n\n"
            "Your greatest strength is your generosity. You're the person who "
            "notices when someone needs a hand, pulls them into the fun, and "
            "somehow manages to make them feel like they belong. You give freely, "
            "whether it's your time, your attention, your humor, or the last "
            "drink at the table.\n\n"
            "**Your guard is this:** you refuse to let life become too serious "
            "for too long. You protect joy. You remind people to laugh when "
            "they've forgotten how, to loosen their grip on the things weighing "
            "them down, and to enjoy the moment while it's still happening.\n\n"
            "Like a margarita, you're bright, refreshing, a little dangerous "
            "when underestimated, and considerably more fun when shared."
        ),
        "art": "Margarita_Mara.png",
        "defeat_art": "Margarita_Mara_Defeat.png",
    },
    "793895185896439838": {  # Saro
        "animal": "Chimera",
        "form": (
            "Silver mist gathers and coils through the air, shimmering brighter "
            "as it takes shape. First come the eyes of a lion, fierce and "
            "unwavering, followed by the long, powerful head of a dragon. Great "
            "dragon wings unfold from its back, scattering ribbons of luminous "
            "silver through the darkness. It does not emerge gently. It arrives "
            "like something ancient remembering exactly what it was made to protect."
        ),
        "reading": (
            "You carry both the quiet independence of the lion and the untamed "
            "power of the dragon. You know your own strength, and you have no "
            "need to constantly prove it. You can stand alone when you have to, "
            "but your strength becomes something far greater when you choose to "
            "stand beside someone you love.\n\n"
            "Your chimera knows that loyalty is not given freely. It is earned. "
            "You are selective about who gets close enough to see the softer "
            "parts of you, but once someone has earned their place, you protect "
            "them with a ferocity that can surprise anyone who mistook your "
            "independence for indifference.\n\n"
            "The lion in your Patronus represents your courage, pride, and "
            "unwavering sense of self. The dragon represents the part of you "
            "that refuses to be diminished, controlled, or made smaller for "
            "someone else's comfort. Its silver form reflects something deeper: "
            "you have learned that your strength does not have to be loud to be "
            "powerful.\n\n"
            "When danger comes, the chimera doesn't simply defend you.\n\n"
            "It rises.\n\n"
            "Its wings spread wide, its lion's roar becomes a thunderous "
            "dragon's cry, and the silver mist around it burns brighter until "
            "there is nowhere left for darkness to hide.\n\n"
            "It is not a creature that asks permission to exist.\n\n"
            "It knows exactly what it is."
        ),
        "art": "Chimera_Saro.jpg",
        "defeat_art": "Chimera_Saro_Defeat.jpg",
    },
}

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
EMBLEM_MATCH = {"Elephant": "vashara", "Bear": "vashara", "Grizzly": "vashara", "Stag": "veyren", "Doe": "veyren", "Wolf": "thornmere"}

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

    def custom_of(self, user_id: int) -> dict | None:
        return CUSTOM_PATRONUSES.get(str(user_id))

    def patronus_of(self, user_id: int) -> dict | None:
        custom = self.custom_of(user_id)
        if custom:
            return {
                "animal": custom["animal"],
                "form": custom["form"],
                "reading": custom["reading"],
            }
        return self.patronuses.get(str(user_id))

    def art_path_for(self, user_id: int, *, field: str = "art") -> Path | None:
        custom = self.custom_of(user_id)
        if not custom:
            return None
        name = custom.get(field) or (custom.get("art") if field == "defeat_art" else None)
        if not name:
            return None
        path = ASSETS_DIR / name
        return path if path.is_file() else None

    def art_file_for(self, user_id: int, *, field: str = "art") -> discord.File | None:
        path = self.art_path_for(user_id, field=field)
        if path is None:
            return None
        return discord.File(path, filename="patronus.png")

    def release(self, user_id: int) -> bool:
        # Custom assignments are code-level — wandreset can't erase them.
        if self.custom_of(user_id):
            return False
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

    def embed_for(
        self,
        member,
        patronus: dict,
        fresh: bool = False,
        *,
        with_art: bool = False,
    ) -> discord.Embed:
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
        if with_art:
            embed.set_image(url="attachment://patronus.png")
        if fresh:
            embed.set_footer(text=f"{member.display_name}'s patronus • cast from the words "
                                  "that chose their wand")
        elif self.custom_of(member.id):
            embed.set_footer(text=f"{member.display_name}'s patronus • headmaster-sealed")
        return embed

    async def _send_patronus(
        self,
        interaction: discord.Interaction,
        member,
        patronus: dict,
        *,
        fresh: bool = False,
        followup: bool = False,
    ) -> None:
        art = self.art_file_for(member.id)
        embed = self.embed_for(member, patronus, fresh=fresh, with_art=art is not None)
        kwargs = {"embed": embed}
        if art is not None:
            kwargs["file"] = art
        if followup:
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)

    @app_commands.command(name="patronus",
                          description="Cast your patronus from the words that chose your wand.")
    @app_commands.describe(member="Whose patronus to see (leave blank for your own)")
    async def patronus(self, interaction: discord.Interaction, member: discord.Member = None):
        hexes = self.bot.get_cog("Hexes")
        if hexes and await hexes.deny_if_limp_wand(interaction):
            return

        target = member or interaction.user

        existing = self.patronus_of(target.id)
        if existing:
            await self._send_patronus(interaction, target, existing)
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
        await self._send_patronus(
            interaction, target, result, fresh=True, followup=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Patronus(bot))
