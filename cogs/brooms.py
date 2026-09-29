"""
Brooms. Purely cosmetic - chosen from the same three words that chose
your wand (and cast your patronus).

    /broom            - claim yours, or see it again
    /broom @member    - see someone else's

Fixed portraits (like Descent monsters) — players don't customize.
Each broom has Speed, Altitude, and ridiculous 0–10 stats for bragging.
/wandreset releases the broom with the wand and patronus.
"""

from __future__ import annotations

import hashlib
import io
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
ASSETS_DIR = Path(__file__).resolve().parent.parent / "broom_art_assets"

MODEL = os.getenv("BROOM_MODEL", "claude-haiku-4-5-20251001")
BROOM_COLOR = 0x6B4F3A

# Named brooms with fixed art — same idea as monster_art_assets.
BROOMS = {
    "Cinderbolt":   {"blurb": "built for speed and show; loves a dramatic takeoff", "image": "Cinderbolt.png"},
    "Moonrake":     {"blurb": "quiet night flyer; drawn to loners and lookouts", "image": "Moonrake.png"},
    "Stormneedle":  {"blurb": "cuts through weather; a little wild", "image": "Stormneedle.png"},
    "Glassgale":    {"blurb": "light and almost silent; favours the precise", "image": "Glassgale.png"},
    "Ironwhisk":    {"blurb": "sturdy workhorse; never drops you mid-air", "image": "Ironwhisk.png"},
    "Thornspur":    {"blurb": "sharp handling; for the competitive", "image": "Thornspur.png"},
    "Hearthsweep":  {"blurb": "warm and reliable; a first broom that never leaves the heart", "image": "Hearthsweep.png"},
    "Silverhaft":   {"blurb": "elegant and rare; wants someone who won't waste it", "image": "Silverhaft.png"},
    "Oakrail":      {"blurb": "steady climber; patient turns, solid landings", "image": "Oakrail.png"},
    "Wispwing":     {"blurb": "barely there; for the quick and the clever", "image": "Wispwing.png"},
    "Vaultbreaker": {"blurb": "heavy and powerful; built for force, not finesse", "image": "Vaultbreaker.png"},
    "Riverreed":    {"blurb": "flexible and forgiving; teaches as it flies", "image": "Riverreed.png"},
    "Nightcompass": {"blurb": "always finds its way home", "image": "Nightcompass.png"},
    "Bellringer":   {"blurb": "loud and proud; arrives before you do", "image": "Bellringer.png"},
    "Ashfeather":   {"blurb": "light wood, soft landing; gentle hands", "image": "Ashfeather.png"},
    "Emberhaft":    {"blurb": "runs warm under the grip; for the fierce and the fond", "image": "Emberhaft.png"},
    "Frostquill":   {"blurb": "cool and exact; never oversteers", "image": "Frostquill.png"},
    "Starlatch":    {"blurb": "restless traveller's broom; hates sitting still", "image": "Starlatch.png"},
    "Kindling":     {"blurb": "simple, honest ash and twig; nothing to prove", "image": "Kindling.png"},
    "Gloamrunner":  {"blurb": "at home in the half-light between day and night", "image": "Gloamrunner.png"},
    "Hollowreed":   {"blurb": "hollow and haunting; hums in empty corridors", "image": "Hollowreed.png"},
    "Sunlatch":     {"blurb": "bright and bold; refuses cloudy excuses", "image": "Sunlatch.png"},
    "Mirebrush":    {"blurb": "cunning fen flyer; tracks mud into every hall", "image": "Mirebrush.png"},
    "Cloudknit":    {"blurb": "soft sky-dweller; altitude is a lifestyle", "image": "Cloudknit.png"},
    "Ravenquill":   {"blurb": "sleek messenger; keeps secrets in the grain", "image": "Ravenquill.png"},
    "Rosebriar":    {"blurb": "beautiful and sharp; never apologize for the thorns", "image": "Rosebriar.png"},
    "Cobaltspur":   {"blurb": "sporty and loud; painted for the finish line", "image": "Cobaltspur.png"},
    "Gravemoss":    {"blurb": "ancient and solemn; older than the pitch", "image": "Gravemoss.png"},
    "Pixiedrift":   {"blurb": "mischief on a stick; glitter is not optional", "image": "Pixiedrift.png"},
    "Dreadkeel":    {"blurb": "prestige and menace; arrives like a storm front", "image": "Dreadkeel.png"},
}

SERIOUS_STATS = ("speed", "altitude")
SILLY_STATS = (
    "drama",
    "snack_pouch",
    "squirrel_spite",
    "midair_humming",
    "gossip_range",
    "how_much_it_judges_you",
)
STAT_LABELS = {
    "speed": "Speed",
    "altitude": "Altitude",
    "drama": "Drama",
    "snack_pouch": "Snack Pouch",
    "squirrel_spite": "Squirrel Spite",
    "midair_humming": "Mid-air Humming",
    "gossip_range": "Gossip Range",
    "how_much_it_judges_you": "How Much It Judges You",
}

READING_PROMPT = """You are the broom-fitter of Velmora. Brooms are personal and cosmetic — no races, no real flying advantage. A student once gave the wandmaker three words. You read those SAME words for how they would look on a broom.

Do NOT match their words literally. Choose exactly one broom from this list:
{brooms}

Then write:
- "finish": one short line on how this broom looks/feels in the hand.
- "reading": two or three sentences to the student on why this broom chose them. Warm, a little uncanny. Mention the stats are for bragging only.

The student's words: "{words}"

Reply with ONLY JSON:
{{"model": "...", "finish": "...", "reading": "..."}}"""


def _words_key(words: str) -> str:
    toks = sorted(w.lower() for w in re.findall(r"[\w']+", words or ""))
    return " ".join(toks) or "silence"


def _digest(words: str) -> bytes:
    return hashlib.sha256(("broom:" + _words_key(words)).encode()).digest()


def _stat_block(digest: bytes) -> dict:
    stats = {}
    for i, key in enumerate(SERIOUS_STATS + SILLY_STATS):
        stats[key] = digest[4 + i] % 11
    return stats


def _bar(n: int, width: int = 10) -> str:
    n = max(0, min(10, int(n)))
    return "█" * n + "░" * (width - n) + f" {n}/10"


def fallback_broom(words: str) -> dict:
    digest = _digest(words)
    models = sorted(BROOMS)
    model = models[digest[0] % len(models)]
    return {
        "model": model,
        "finish": f"The {model} settles into your hand like it had been waiting.",
        "reading": (
            f"The {model} is {BROOMS[model]['blurb']}. "
            "It will not make you faster. It will make you look like yourself."
        ),
        "stats": _stat_block(digest),
    }


def _clean(raw: dict, words: str) -> dict | None:
    try:
        model = str(raw["model"]).strip()
        finish = str(raw["finish"]).strip()
        reading = str(raw["reading"]).strip()
    except (KeyError, TypeError):
        return None
    match = next((m for m in BROOMS if m.lower() == model.lower()), None)
    if not match or not finish or not reading:
        return None
    return {
        "model": match,
        "finish": finish[:300],
        "reading": reading[:600],
        "stats": _stat_block(_digest(words)),
    }


def _ensure(broom: dict, words: str | None = None) -> dict:
    """Normalize older combo-style records onto the fixed named catalog."""
    out = dict(broom)
    model = out.get("model")
    if model not in BROOMS:
        # Legacy shaft×bristle×binding → hash onto a named broom
        seed = words or out.get("finish") or out.get("shaft") or "legacy"
        fb = fallback_broom(str(seed))
        out["model"] = fb["model"]
        out.setdefault("finish", fb["finish"])
        out.setdefault("reading", out.get("reading") or fb["reading"])
        out["stats"] = fb["stats"]
    if "stats" not in out or not isinstance(out.get("stats"), dict):
        out["stats"] = _stat_block(_digest(words or out["model"]))
    return out


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
        raw = self.brooms.get(str(user_id))
        if not raw:
            return None
        return _ensure(raw)

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
                brooms="\n".join(f"- {k}: {v['blurb']}" for k, v in BROOMS.items()),
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
                    result = _clean(json.loads(match.group(0)), words)
                    if result:
                        return result
                log.warning("Broom reading came back unusable; using the fallback.")
            except Exception:
                log.exception("Broom reading failed; using the fallback.")
        return fallback_broom(words)

    def portrait_file(self, broom: dict) -> discord.File | None:
        broom = _ensure(broom)
        meta = BROOMS.get(broom["model"])
        if not meta:
            return None
        path = ASSETS_DIR / meta["image"]
        if not path.exists():
            log.warning("Missing broom art %s", path)
            return None
        data = path.read_bytes()
        return discord.File(io.BytesIO(data), filename="broom.png")

    def embed_for(self, member, broom: dict, fresh: bool = False) -> discord.Embed:
        broom = _ensure(broom)
        model = broom["model"]
        stats = broom["stats"]
        serious = "\n".join(
            f"**{STAT_LABELS[k]}** {_bar(stats.get(k, 0))}" for k in SERIOUS_STATS
        )
        silly = "\n".join(
            f"**{STAT_LABELS[k]}** {_bar(stats.get(k, 0))}" for k in SILLY_STATS
        )
        embed = discord.Embed(
            title=("A broom answers your grip…" if fresh
                   else f"{member.display_name}'s broom"),
            description=(f"**The {model}**\n*{broom['finish']}*\n\n{broom['reading']}"),
            color=BROOM_COLOR,
        )
        embed.add_field(name="Flight (for show)", value=serious, inline=False)
        embed.add_field(name="Also (deeply scientific)", value=silly, inline=False)
        if (ASSETS_DIR / BROOMS[model]["image"]).exists():
            embed.set_image(url="attachment://broom.png")
        if fresh:
            embed.set_footer(text=f"{member.display_name}'s broom • fixed portrait • "
                                  "stats are decorative")
        else:
            embed.set_footer(text="Purely cosmetic • looking cool is the point")
        return embed

    @app_commands.command(
        name="broom",
        description="Claim your broom from the words that chose your wand. Fixed portrait + silly stats — just for looking cool.",
    )
    @app_commands.describe(member="Whose broom to see (leave blank for your own)")
    async def broom(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user

        existing = self.broom_of(target.id)
        if existing:
            await interaction.response.defer()
            embed = self.embed_for(target, existing)
            file = self.portrait_file(existing)
            kwargs = {"embed": embed}
            if file:
                kwargs["file"] = file
            await interaction.followup.send(**kwargs)
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
        embed = self.embed_for(target, result, fresh=True)
        file = self.portrait_file(result)
        kwargs = {"embed": embed}
        if file:
            kwargs["file"] = file
        await interaction.followup.send(**kwargs)


async def setup(bot: commands.Bot):
    await bot.add_cog(Brooms(bot))
