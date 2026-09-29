"""
Brooms. Purely cosmetic - chosen from the same three words that chose
your wand (and cast your patronus).

    /broom            - claim yours, or see it again
    /broom @member    - see someone else's

No flying, no races, no stats. Just a broom that feels like yours.
/wandreset releases the broom with the wand and patronus, since all
three come from the same words.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.brooms")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
BROOMS_PATH = STATE_DIR / "brooms.json"

MODEL = os.getenv("BROOM_MODEL", "claude-haiku-4-5-20251001")
BROOM_COLOR = 0x6B4F3A

# Each broom carries the temperament it suits. The reader (and the
# fallback) choose from these - never invent a new model.
BROOMS = {
    "Cinderbolt":     "built for speed and show; loves a dramatic takeoff",
    "Glassgale":      "light and almost silent; favours the precise",
    "Ironwhisk":      "sturdy workhorse; never drops you mid-air",
    "Moonrake":       "quiet night flyer; drawn to loners and lookouts",
    "Thornspur":      "sharp handling; for the competitive",
    "Hearthsweep":    "warm and reliable; a first broom that never leaves the heart",
    "Stormneedle":    "cuts through weather; a little wild",
    "Silverhaft":     "elegant and rare; wants someone who won't waste it",
    "Oakrail":        "steady climber; patient turns, solid landings",
    "Wispwing":       "barely there; for the quick and the clever",
    "Vaultbreaker":   "heavy and powerful; built for force, not finesse",
    "Riverreed":      "flexible and forgiving; teaches as it flies",
    "Nightcompass":   "always finds its way home",
    "Bellringer":     "loud and proud; arrives before you do",
    "Ashfeather":     "light wood, soft landing; gentle hands",
    "Emberhaft":      "runs warm under the grip; for the fierce and the fond",
    "Frostquill":     "cool and exact; never oversteers",
    "Starlatch":      "restless traveller's broom; hates sitting still",
    "Kindling":       "simple, honest ash and twig; nothing to prove",
    "Gloamrunner":    "at home in the half-light between day and night",
}

READING_PROMPT = """You are the broom-fitter of Velmora, a school of magic. Brooms here are not tools of Quidditch rankings — they are personal, like a wand. A student once gave the wandmaker three words. The wand read them for temperament. The patronus read them for what they protect. You are reading the SAME words for how they move through the air — their style of flight, daring, patience, and show.

Do NOT match their words literally. If they wrote "fire" or "storm", do not simply hand back the flashiest broom. Read the flyer behind the words.

Choose exactly one broom from this list:
{brooms}

Then write:
- "finish": one short line describing how the broom looks or feels in the hand (wood grain, bristles, bindings — specific and a little strange).
- "reading": two or three sentences, speaking to the student directly, on why this broom chose them. Warm, a little uncanny. Refer to what the words revealed, not to the words themselves. Remind them it serves no purpose but looking cool — and that is enough.

The student's words: "{words}"

Reply with ONLY a JSON object:
{{"model": "...", "finish": "...", "reading": "..."}}"""


def fallback_broom(words: str) -> dict:
    """Fit without the API. Same words, same broom — salted so it doesn't
    simply shadow the wand or patronus fallback."""
    toks = sorted(w.lower() for w in re.findall(r"[\w']+", words or ""))
    digest = hashlib.sha256(("broom:" + " ".join(toks)).encode()).digest()
    model = sorted(BROOMS)[digest[0] % len(BROOMS)]
    return {
        "model": model,
        "finish": f"The {model} settles into your hand like it had been waiting.",
        "reading": (f"The {model} is {BROOMS[model]}. That is how your words want to fly — "
                    "and it does not need to do anything else to be yours."),
    }


def _clean(raw: dict) -> dict | None:
    try:
        model = str(raw["model"]).strip()
        finish = str(raw["finish"]).strip()
        reading = str(raw["reading"]).strip()
    except (KeyError, TypeError):
        return None
    match = next((b for b in BROOMS if b.lower() == model.lower()), None)
    if not match or not finish or not reading:
        return None
    return {"model": match, "finish": finish[:300], "reading": reading[:600]}


class Brooms(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.brooms = self._load()

    def _load(self) -> dict:
        try:
            with open(BROOMS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.exception("Broom records unreadable - starting empty.")
            return {}

    def save(self) -> None:
        try:
            BROOMS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = BROOMS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.brooms, f, indent=2)
            os.replace(tmp, BROOMS_PATH)
        except OSError:
            log.exception("Could not save broom records.")

    def broom_of(self, user_id: int) -> dict | None:
        return self.brooms.get(str(user_id))

    def release(self, user_id: int) -> bool:
        gone = self.brooms.pop(str(user_id), None) is not None
        if gone:
            self.save()
        return gone

    async def fit(self, words: str) -> dict:
        wands = self.bot.get_cog("Wands")
        client = getattr(wands, "client", None)
        if client is not None:
            prompt = READING_PROMPT.format(
                brooms="\n".join(f"- {k}: {v}" for k, v in BROOMS.items()),
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
                log.warning("Broom reading came back unusable; using the fallback.")
            except Exception:
                log.exception("Broom reading failed; using the fallback.")
        return fallback_broom(words)

    def embed_for(self, member, broom: dict, fresh: bool = False) -> discord.Embed:
        embed = discord.Embed(
            title=("A broom answers your grip…" if fresh
                   else f"{member.display_name}'s broom"),
            description=(f"**The {broom['model']}**\n"
                         f"*{broom['finish']}*\n\n{broom['reading']}"),
            color=BROOM_COLOR,
        )
        if fresh:
            embed.set_footer(text=f"{member.display_name}'s broom • from the words that chose "
                                  "their wand • purely for looking cool")
        else:
            embed.set_footer(text="Purely cosmetic. Looking cool is the whole point.")
        return embed

    @app_commands.command(name="broom",
                          description="Claim your broom from the words that chose your wand. Purely cosmetic.")
    @app_commands.describe(member="Whose broom to see (leave blank for your own)")
    async def broom(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user

        existing = self.broom_of(target.id)
        if existing:
            await interaction.response.send_message(embed=self.embed_for(target, existing))
            return

        if member and member.id != interaction.user.id:
            await interaction.response.send_message(
                f"{member.display_name} hasn't claimed a broom yet.", ephemeral=True
            )
            return

        wands = self.bot.get_cog("Wands")
        wand = wands.wand_of(target.id) if wands else None
        if not wand or not wand.get("words"):
            await interaction.response.send_message(
                "Your broom answers to the same words that chose your wand — "
                "and no wand has chosen you yet. Start with `/wand`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        result = await self.fit(wand["words"])
        self.brooms[str(target.id)] = result
        self.save()
        await interaction.followup.send(embed=self.embed_for(target, result, fresh=True))


async def setup(bot: commands.Bot):
    await bot.add_cog(Brooms(bot))
