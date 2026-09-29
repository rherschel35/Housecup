"""
Brooms. Purely cosmetic - chosen from the same three words that chose
your wand (and cast your patronus).

    /broom            - claim yours, or see it again
    /broom @member    - see someone else's

100 combos (10 shafts × 5 bristles × 2 bindings), each with a unique
portrait, Speed / Altitude (0–10), and a handful of ridiculous stats.
No flying, no races — looking cool is the whole point.
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

from cogs import broom_art

log = logging.getLogger("velmora.brooms")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
BROOMS_PATH = STATE_DIR / "brooms.json"

MODEL = os.getenv("BROOM_MODEL", "claude-haiku-4-5-20251001")
BROOM_COLOR = 0x6B4F3A

# 10 × 5 × 2 = 100 unique brooms
SHAFTS = {
    "Ashrail":      {"blurb": "steady oak-ash hybrid; hates drama mid-flight", "color": "#8B5A2B", "grain": "#C4A574"},
    "Cinderhaft":   {"blurb": "warm to the touch; smoulders a little on takeoff", "color": "#6B2E1F", "grain": "#C45C2A"},
    "Moonwillow":   {"blurb": "pale and quiet; prefers night air", "color": "#C9C4B0", "grain": "#E8E4D4"},
    "Thornspur":    {"blurb": "competitive grain; leans into sharp turns", "color": "#4A3A28", "grain": "#7A5A38"},
    "Glasspine":    {"blurb": "almost translucent; whispers when it banks", "color": "#7A9A9A", "grain": "#C0D8D8"},
    "Ironbark":     {"blurb": "heavy, stubborn, refuses to drop you", "color": "#3A322C", "grain": "#6A5A4A"},
    "Starlatch":    {"blurb": "restless; fidgets if left propped too long", "color": "#2A2848", "grain": "#7A78B0"},
    "Hearthbeam":   {"blurb": "homey and reliable; smells faintly of toast", "color": "#A06838", "grain": "#D4A060"},
    "Riverreed":    {"blurb": "flexible; forgives clumsy landings", "color": "#5A7A48", "grain": "#A0C080"},
    "Gloamwood":    {"blurb": "half-light specialist; sulks at noon", "color": "#3A3040", "grain": "#8A7090"},
}

BRISTLES = {
    "Storm-twig":   {"blurb": "crackles in humidity", "color": "#4A6080", "accent": "#A0C8F0", "style": "wild"},
    "Goldwhisk":    {"blurb": "showy; catches every sunset", "color": "#C9A84C", "accent": "#F2E0A0", "style": "fan"},
    "Softmoss":     {"blurb": "absurdly gentle landings", "color": "#6A8A58", "accent": "#B0D090", "style": "soft"},
    "Nightbristle": {"blurb": "drinks moonlight; slightly judgmental", "color": "#2A2438", "accent": "#8070A8", "style": "tight"},
    "Emberreed":    {"blurb": "tips glow when you show off", "color": "#A04020", "accent": "#F08040", "style": "wild"},
}

BINDINGS = {
    "Copper wire":  {"blurb": "warm wraps; good for gripping mid-boast", "color": "#B87333", "spark": "#E8A060"},
    "Silver twine": {"blurb": "polite and shiny; never frays on purpose", "color": "#C0C8D0", "spark": "#F0F4F8"},
}

assert len(SHAFTS) * len(BRISTLES) * len(BINDINGS) == 100

# Serious-looking stats + ridiculous ones. All 0–10, all cosmetic.
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

READING_PROMPT = """You are the broom-fitter of Velmora. Brooms are personal and cosmetic — no races, no real flying advantage. A student once gave the wandmaker three words. You read those SAME words for how they would look on a broom: style, daring, patience, and show.

Do NOT match their words literally. Choose exactly one of each:

SHAFTS:
{shafts}

BRISTLES:
{bristles}

BINDINGS:
{bindings}

Then write:
- "finish": one short line on how this broom looks/feels in the hand.
- "reading": two or three sentences to the student on why this combo chose them. Warm, a little uncanny. Mention that the stats are for bragging only.

The student's words: "{words}"

Reply with ONLY JSON:
{{"shaft": "...", "bristles": "...", "binding": "...", "finish": "...", "reading": "..."}}"""


def _words_key(words: str) -> str:
    toks = sorted(w.lower() for w in re.findall(r"[\w']+", words or ""))
    return " ".join(toks) or "silence"


def _digest(words: str) -> bytes:
    return hashlib.sha256(("broom:" + _words_key(words)).encode()).digest()


def _stat_block(digest: bytes) -> dict:
    """Map digest bytes onto 0–10 stats. Deterministic for the same words."""
    stats = {}
    for i, key in enumerate(SERIOUS_STATS + SILLY_STATS):
        stats[key] = digest[4 + i] % 11  # 0..10 inclusive
    return stats


def _bar(n: int, width: int = 10) -> str:
    n = max(0, min(10, int(n)))
    return "█" * n + "░" * (width - n) + f" {n}/10"


def combo_name(shaft: str, bristles: str, binding: str) -> str:
    return f"{shaft} · {bristles} · {binding}"


def all_combos() -> list[tuple[str, str, str]]:
    return [(s, b, d) for s in sorted(SHAFTS) for b in sorted(BRISTLES) for d in sorted(BINDINGS)]


def fallback_broom(words: str) -> dict:
    digest = _digest(words)
    shafts = sorted(SHAFTS)
    bristles = sorted(BRISTLES)
    bindings = sorted(BINDINGS)
    shaft = shafts[digest[0] % len(shafts)]
    bristle = bristles[digest[1] % len(bristles)]
    binding = bindings[digest[2] % len(bindings)]
    stats = _stat_block(digest)
    return {
        "shaft": shaft,
        "bristles": bristle,
        "binding": binding,
        "finish": f"The {combo_name(shaft, bristle, binding)} settles into your hand like it had been waiting.",
        "reading": (
            f"{SHAFTS[shaft]['blurb'].capitalize()}. Bristles of {bristle.lower()} — "
            f"{BRISTLES[bristle]['blurb']}. Bound in {binding.lower()}. "
            "It will not make you faster. It will make you look like yourself."
        ),
        "stats": stats,
    }


def _clean(raw: dict, words: str) -> dict | None:
    try:
        shaft = str(raw["shaft"]).strip()
        bristles = str(raw["bristles"]).strip()
        binding = str(raw["binding"]).strip()
        finish = str(raw["finish"]).strip()
        reading = str(raw["reading"]).strip()
    except (KeyError, TypeError):
        return None
    shaft_m = next((s for s in SHAFTS if s.lower() == shaft.lower()), None)
    bristle_m = next((b for b in BRISTLES if b.lower() == bristles.lower()), None)
    bind_m = next((d for d in BINDINGS if d.lower() == binding.lower()), None)
    if not shaft_m or not bristle_m or not bind_m or not finish or not reading:
        return None
    return {
        "shaft": shaft_m,
        "bristles": bristle_m,
        "binding": bind_m,
        "finish": finish[:300],
        "reading": reading[:600],
        "stats": _stat_block(_digest(words)),
    }


def _ensure_stats(broom: dict, words: str | None = None) -> dict:
    """Back-fill stats/parts for any older broom records."""
    if "stats" not in broom or not isinstance(broom.get("stats"), dict):
        seed = words or broom.get("model") or broom.get("finish") or "broom"
        broom["stats"] = _stat_block(_digest(str(seed)))
    # Legacy single-model brooms → map onto a combo via hash
    if "shaft" not in broom or broom["shaft"] not in SHAFTS:
        fb = fallback_broom(words or broom.get("model") or "legacy")
        broom.setdefault("shaft", fb["shaft"])
        broom.setdefault("bristles", fb["bristles"])
        broom.setdefault("binding", fb["binding"])
        broom.setdefault("finish", fb["finish"])
        broom.setdefault("reading", broom.get("reading") or fb["reading"])
        broom["stats"] = fb["stats"]
    return broom


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
        return _ensure_stats(dict(raw))

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
                shafts="\n".join(f"- {k}: {v['blurb']}" for k, v in SHAFTS.items()),
                bristles="\n".join(f"- {k}: {v['blurb']}" for k, v in BRISTLES.items()),
                bindings="\n".join(f"- {k}: {v['blurb']}" for k, v in BINDINGS.items()),
                words=words.replace('"', "'")[:200],
            )
            try:
                response = await client.messages.create(
                    model=MODEL, max_tokens=450,
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

    def portrait_png(self, broom: dict) -> bytes:
        shaft = SHAFTS[broom["shaft"]]
        bristles = BRISTLES[broom["bristles"]]
        binding = BINDINGS[broom["binding"]]
        seed = combo_name(broom["shaft"], broom["bristles"], broom["binding"])
        return broom_art.render(shaft, bristles, binding, seed)

    def embed_for(self, member, broom: dict, fresh: bool = False) -> discord.Embed:
        broom = _ensure_stats(broom)
        name = combo_name(broom["shaft"], broom["bristles"], broom["binding"])
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
            description=(f"**{name}**\n*{broom['finish']}*\n\n{broom['reading']}"),
            color=BROOM_COLOR,
        )
        embed.add_field(name="Flight (for show)", value=serious, inline=False)
        embed.add_field(name="Also (deeply scientific)", value=silly, inline=False)
        embed.set_image(url="attachment://broom.png")
        if fresh:
            embed.set_footer(text=f"{member.display_name}'s broom • 1 of 100 combos • "
                                  "stats are decorative")
        else:
            embed.set_footer(text="1 of 100 combos • purely cosmetic • looking cool is the point")
        return embed

    def file_for(self, broom: dict) -> discord.File:
        png = self.portrait_png(broom)
        return discord.File(io.BytesIO(png), filename="broom.png")

    @app_commands.command(
        name="broom",
        description="Claim your broom from the words that chose your wand. 100 combos, silly stats, just for looking cool.",
    )
    @app_commands.describe(member="Whose broom to see (leave blank for your own)")
    async def broom(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user

        existing = self.broom_of(target.id)
        if existing:
            await interaction.response.defer()
            embed = self.embed_for(target, existing)
            await interaction.followup.send(embed=embed, file=self.file_for(existing))
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
        await interaction.followup.send(embed=embed, file=self.file_for(result))


async def setup(bot: commands.Bot):
    await bot.add_cog(Brooms(bot))
