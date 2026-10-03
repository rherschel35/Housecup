"""
Brooms. Chosen from the same three words that chose your wand
(and cast your patronus). Portraits stay unique; flight stats can be upgraded.

    /broom                 - claim yours, or see it again
    /broom @member         - see someone else's
    /broomupgrade          - raise Speed or Altitude (yours or @member; you pay)
    /staff identity upgradebroom  - free Speed or Altitude bump on anyone
    /broomreset @x         - staff, free someone's broom claim (wand stays)

100 fixed painted portraits (like Descent monsters). Each broom can be
claimed by only ONE player; if your words point at a taken broom, you get
the next-closest available one. Base Speed/Altitude are fixed per model so
Discord matches the Compendium; upgrades add bonuses toward 10 via Pitch
Resin (forage), Descent materials, or Marketplace broom tokens.
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

# Hard-assign specific brooms to Discord user IDs. Those models stay out of
# everyone else's pool. Extra pairs via BROOM_RESERVED="id:Model,id2:Model2".
RESERVED_BROOMS: dict[int, str] = {
    555141900802457630: "Moonflare",  # Headmaster Gon Vale
    206828442065305600: "Rosewake",
}


def _load_reserved() -> dict[int, str]:
    out = dict(RESERVED_BROOMS)
    raw = os.getenv("BROOM_RESERVED", "").strip()
    if not raw:
        return out
    for part in raw.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        uid_s, model = part.split(":", 1)
        uid_s, model = uid_s.strip(), model.strip()
        if uid_s.isdigit() and model:
            out[int(uid_s)] = model
    return out


# 100 named brooms — each image is broom_art_assets/<Name>.png
BROOMS = {
    "Cinderbolt":   "built for speed and show; loves a dramatic takeoff",
    "Moonrake":     "quiet night flyer; drawn to loners and lookouts",
    "Stormneedle":  "cuts through weather; a little wild",
    "Glassgale":    "light and almost silent; favours the precise",
    "Ironwhisk":    "sturdy workhorse; never drops you mid-air",
    "Thornspur":    "sharp handling; for the competitive",
    "Hearthsweep":  "warm and reliable; a first broom that never leaves the heart",
    "Silverhaft":   "elegant and rare; wants someone who won't waste it",
    "Oakrail":      "steady climber; patient turns, solid landings",
    "Wispwing":     "barely there; for the quick and the clever",
    "Vaultbreaker": "heavy and powerful; built for force, not finesse",
    "Riverreed":    "flexible and forgiving; teaches as it flies",
    "Nightcompass": "always finds its way home",
    "Bellringer":   "loud and proud; arrives before you do",
    "Ashfeather":   "light wood, soft landing; gentle hands",
    "Emberhaft":    "runs warm under the grip; for the fierce and the fond",
    "Frostquill":   "cool and exact; never oversteers",
    "Starlatch":    "restless traveller's broom; hates sitting still",
    "Kindling":     "simple, honest ash and twig; nothing to prove",
    "Gloamrunner":  "at home in the half-light between day and night",
    "Hollowreed":   "hollow and haunting; hums in empty corridors",
    "Sunlatch":     "bright and bold; refuses cloudy excuses",
    "Mirebrush":    "cunning fen flyer; tracks mud into every hall",
    "Cloudknit":    "soft sky-dweller; altitude is a lifestyle",
    "Ravenquill":   "sleek messenger; keeps secrets in the grain",
    "Rosebriar":    "beautiful and sharp; never apologize for the thorns",
    "Cobaltspur":   "sporty and loud; painted for the finish line",
    "Gravemoss":    "ancient and solemn; older than the pitch",
    "Pixiedrift":   "mischief on a stick; glitter is not optional",
    "Dreadkeel":    "prestige and menace; arrives like a storm front",
    "Mirthspire":   "cheer carved into the grain; laughs on takeoff",
    "Gallopwick":   "horsehair speed; born for open sky",
    "Spindlehaze":  "spins fog behind it; hard to follow",
    "Quartzflare":  "crystal shaft; catches every scrap of light",
    "Bramblehook":  "hooks the wind and won't let go",
    "Tidewhisper":  "sea-salt soft; talks like the tide",
    "Emberlace":    "filigree fire; delicate and dangerous",
    "Nightskein":   "unspools starlight as it flies",
    "Copperfinch":  "bright metal song; never quiet on approach",
    "Velvetreach":  "plush and far-reaching; theatrical landings",
    "Ashmantle":    "cloaked in ash; understated power",
    "Stormpetal":   "petals in a tempest; pretty until it isn't",
    "Glimmershank": "shimmers at the edge of vision",
    "Foxfire":      "trickster green flame; hard to catch",
    "Driftwillow":  "lazy curves; somehow still on time",
    "Ironpetal":    "steel bloom; beauty with weight",
    "Moonspindle":  "winds moonlight into the grip",
    "Thistlewake":  "leaves a prickly trail of sparks",
    "Crystalspur":  "sharp glass speed; no second chances",
    "Hearthfang":   "home's bite; protective and hot",
    "Wavecrest":    "rides invisible surf",
    "Shadowloom":   "weaves shade under its path",
    "Brightkeel":   "keeps a level sunny course",
    "Duskrake":     "scrapes the last light from the day",
    "Sparrowhaft":  "small, quick, endlessly brave",
    "Goldenthorn":  "gilded and pointed; vanity with teeth",
    "Mistrail":     "lays a misty railway through the air",
    "Frostbark":    "winter wood; breath fogs on contact",
    "Sunbriar":     "sunny thorns; cheerful aggression",
    "Nightforge":   "hammered in darkness; holds a spark",
    "Mapleflare":   "autumn fire along the shaft",
    "Grimquill":    "writes grim stories in the wind",
    "Silkreed":     "impossibly smooth; whispers apologies",
    "Thunderlatch": "clicks once, then the sky answers",
    "Palehook":     "ghost-white catch; reels you home",
    "Coralwhisk":   "reef colours; salt and sparkle",
    "Starfen":      "marsh and constellation; odd but true",
    "Dawnspindle":  "spins the morning into being",
    "Witchbroom":   "classic silhouette; owns the stereotype",
    "Hollowspark":  "empty core, bright spit of light",
    "Jadekeel":     "jade-green balance; calm and costly",
    "Rumblerush":   "loud low flyer; rattles windows",
    "Pearlwisp":    "pearl sheen and soft wisp bristles",
    "Cinderfen":    "smoulders over wet ground without dying",
    "Ghostrail":    "leaves a pale track nobody else can see",
    "Brightmoss":   "living green glow; soft landings",
    "Stormlace":    "lightning in lacework; formal chaos",
    "Emberquill":   "writes in heat; warm to the tip",
    "Silverfen":    "misty silver wetlands energy",
    "Oakwhisper":   "old oak secrets; slow advice",
    "Glintspur":    "catches light like a dare",
    "Voidreed":     "drinks colour; silhouette of absence",
    "Firemantle":   "cloak of flame when showing off",
    "Snowlatch":    "locks onto cold air; crisp turns",
    "Briarfinch":   "songbird thorns; cheerful menace",
    "Deepsky":      "altitude snob; hates low ceilings",
    "Hearthquill":  "writes home; smells like toast",
    "Mistforge":    "hammered fog; soft but solid",
    "Thornlace":    "pretty pattern, real spikes",
    "Cloudspindle": "spins cotton-cloud wake",
    "Ravenfen":     "black feathers over black water",
    "Glowbark":     "bioluminescent wood grain",
    "Ironlace":     "metal filigree; heavy elegance",
    "Moonflare":    "lunar flash on banked turns",
    "Wavecutter":   "splits the air like a bow wave",
    "Pixiekeel":    "tiny chaos with a keel of glitter",
    "Dreadmoss":    "velvet dread; soft and awful",
    "Cobaltquill":  "ink-blue speed lines",
    "Rosewake":     "rose-petal trail; romantic flex",
    "Glassfinch":   "fragile look, fierce flight",
}

assert len(BROOMS) == 100

RESERVED_BROOMS = {
    uid: model for uid, model in _load_reserved().items() if model in BROOMS
}
RESERVED_MODELS = set(RESERVED_BROOMS.values())

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

MAX_STAT = 10
PITCH_RESIN_ID = "pitch_resin"
RESIN_PER_UPGRADE = 5
DESCENT_MATS_PER_UPGRADE = 100
DESCENT_MAT_IDS = (
    "descent_poison_ichor",
    "descent_ember_shard",
    "descent_frost_core",
    "descent_storm_relic",
    "descent_light_dust",
    "descent_sigil",
)


def bonus_field(stat: str) -> str:
    return f"{stat}_bonus"


BROOM_TOKEN_PRICE = 50


def token_point_cost(current: int | None = None) -> int:
    """Marketplace token price (flat) for a Speed or Altitude bump."""
    return BROOM_TOKEN_PRICE


def effective_stats(broom: dict) -> dict:
    """Base model sheet + upgrade bonuses, capped at MAX_STAT."""
    model = broom.get("model")
    base = stats_for_model(model) if model in BROOMS else dict(broom.get("stats") or {})
    out = dict(base)
    for k in SERIOUS_STATS:
        bonus = max(0, int(broom.get(bonus_field(k), 0) or 0))
        out[k] = min(MAX_STAT, int(base.get(k, 0)) + bonus)
    return out

READING_PROMPT = """You are the broom-fitter of Velmora. Brooms are personal and cosmetic. A student once gave the wandmaker three words. Read those SAME words and choose which broom would choose them.

Do NOT match words literally. Prefer this ranked shortlist (best match first) — pick the first name on the list:
{ranked}

Blurbs:
{blurbs}

Write:
- "finish": one short line on how it looks/feels.
- "reading": two or three sentences to the student. Mention stats are for bragging only.

Words: "{words}"

ONLY JSON:
{{"model": "...", "finish": "...", "reading": "..."}}"""


def _words_key(words: str) -> str:
    toks = sorted(w.lower() for w in re.findall(r"[\w']+", words or ""))
    return " ".join(toks) or "silence"


def _digest(words: str) -> bytes:
    return hashlib.sha256(("broom:" + _words_key(words)).encode()).digest()


def _stat_block(digest: bytes) -> dict:
    return {k: digest[4 + i] % 11 for i, k in enumerate(SERIOUS_STATS + SILLY_STATS)}


def stats_for_model(model: str) -> dict:
    """Cosmetic stats are fixed per broom model (Discord and Compendium match)."""
    return _stat_block(_digest(model))


def _bar(n: int, width: int = 10) -> str:
    n = max(0, min(10, int(n)))
    return "█" * n + "░" * (width - n) + f" {n}/10"


def image_name(model: str) -> str:
    return f"{model}.png"


def preference_rank(words: str) -> list[str]:
    """All brooms ordered from closest to furthest match for these words."""
    digest = _digest(words)
    key = _words_key(words)

    def score(model: str) -> tuple:
        h = hashlib.sha256(f"{key}|{model}".encode()).digest()
        dist = sum(abs(h[i] - digest[i]) for i in range(12))
        # Stable tie-break by name
        return (dist, model)

    return sorted(BROOMS, key=score)


def fallback_broom(words: str, model: str) -> dict:
    return {
        "model": model,
        "finish": f"The {model} settles into your hand like it had been waiting.",
        "reading": (
            f"The {model} is {BROOMS[model]}. "
            "It will not make you faster. It will make you look like yourself."
        ),
        "stats": stats_for_model(model),
    }


def _clean(raw: dict, words: str, allowed: set[str]) -> dict | None:
    try:
        model = str(raw["model"]).strip()
        finish = str(raw["finish"]).strip()
        reading = str(raw["reading"]).strip()
    except (KeyError, TypeError):
        return None
    match = next((m for m in allowed if m.lower() == model.lower()), None)
    if not match or not finish or not reading:
        return None
    return {
        "model": match,
        "finish": finish[:300],
        "reading": reading[:600],
        "stats": stats_for_model(match),
    }


def _ensure(broom: dict, words: str | None = None) -> dict:
    out = dict(broom)
    if out.get("model") not in BROOMS:
        seed = words or out.get("finish") or out.get("shaft") or "legacy"
        model = preference_rank(str(seed))[0]
        fb = fallback_broom(str(seed), model)
        out["model"] = fb["model"]
        out.setdefault("finish", fb["finish"])
        out.setdefault("reading", out.get("reading") or fb["reading"])
    # Always re-derive so Discord stays in lockstep with the Compendium sheet.
    out["stats"] = stats_for_model(out["model"])
    for k in SERIOUS_STATS:
        field = bonus_field(k)
        base = int(out["stats"].get(k, 0))
        raw = max(0, int(out.get(field, 0) or 0))
        out[field] = min(raw, max(0, MAX_STAT - base))
    return out


class Brooms(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.brooms = self._load()

    def _load(self) -> dict:
        try:
            with open(BROOMS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"owners": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Broom records unreadable - starting empty.")
            return {"owners": {}}
        # Migrate flat user-id map → {"owners": {...}}
        if "owners" not in data:
            owners = {uid: rec for uid, rec in data.items() if isinstance(rec, dict)}
            return {"owners": owners}
        data.setdefault("owners", {})
        return data

    def save(self) -> None:
        try:
            BROOMS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = BROOMS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.brooms, f, indent=2)
            os.replace(tmp, BROOMS_PATH)
        except OSError:
            log.exception("Could not save broom records.")

    @property
    def owners(self) -> dict:
        return self.brooms.setdefault("owners", {})

    def claimed_models(self) -> dict[str, str]:
        """model -> user_id for every currently owned broom."""
        out: dict[str, str] = {}
        for uid, rec in self.owners.items():
            if not isinstance(rec, dict):
                continue
            model = rec.get("model")
            if model in BROOMS and model not in out:
                out[model] = uid
        return out

    def available_models(self) -> list[str]:
        taken = set(self.claimed_models())
        return [m for m in BROOMS if m not in taken]

    def broom_of(self, user_id: int) -> dict | None:
        raw = self.owners.get(str(user_id))
        if not raw:
            return None
        fixed = _ensure(raw)
        # Migrate stored word-based stats → fixed model sheet.
        if fixed.get("stats") != raw.get("stats"):
            self.owners[str(user_id)] = fixed
            self.save()
        return fixed

    def release(self, user_id: int) -> bool:
        gone = self.owners.pop(str(user_id), None) is not None
        if gone:
            self.save()
        return gone

    def reserved_held(self) -> set[str]:
        """Models reserved for someone else (held out of the open pool)."""
        claimed = self.claimed_models()
        held: set[str] = set()
        for uid, model in RESERVED_BROOMS.items():
            owner = claimed.get(model)
            if owner is None or owner == str(uid):
                held.add(model)
        return held

    def assign_model(self, words: str, user_id: int | None = None) -> str | None:
        """Closest preferred broom that nobody else owns yet."""
        if user_id is not None and user_id in RESERVED_BROOMS:
            model = RESERVED_BROOMS[user_id]
            owner = self.claimed_models().get(model)
            if owner is None or owner == str(user_id):
                return model

        taken = set(self.claimed_models())
        # Keep other players' reserved brooms out of the open pool.
        for uid, model in RESERVED_BROOMS.items():
            if user_id is None or uid != user_id:
                taken.add(model)

        for model in preference_rank(words):
            if model not in taken:
                return model
        return None

    async def fit(self, words: str, user_id: int | None = None) -> dict | None:
        # Reserved assignment skips the AI shortlist — the broom is fixed.
        if user_id is not None and user_id in RESERVED_BROOMS:
            model = self.assign_model(words, user_id=user_id)
            if model == RESERVED_BROOMS[user_id]:
                return fallback_broom(words, model)

        model = self.assign_model(words, user_id=user_id)
        if model is None:
            return None

        # Offer the fitter a short ranked list of still-available brooms
        # (closest first) so Claude stays near the word-match without
        # naming a claimed one.
        open_models = set(self.available_models()) - (
            self.reserved_held() - ({RESERVED_BROOMS[user_id]} if user_id in RESERVED_BROOMS else set())
        )
        ranked = [m for m in preference_rank(words) if m in open_models][:12]
        if model not in ranked:
            ranked = [model] + ranked

        wands = self.bot.get_cog("Wands")
        client = getattr(wands, "client", None)
        if client is not None:
            prompt = READING_PROMPT.format(
                ranked=", ".join(ranked),
                blurbs="\n".join(f"- {m}: {BROOMS[m]}" for m in ranked),
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
                    result = _clean(json.loads(match.group(0)), words, set(ranked))
                    if result:
                        # Re-check claim in case of race; fall back to assign_model
                        claimed = self.claimed_models()
                        if result["model"] not in claimed:
                            return result
                        alt = self.assign_model(words, user_id=user_id)
                        if alt:
                            return fallback_broom(words, alt)
                log.warning("Broom reading came back unusable; using the fallback.")
            except Exception:
                log.exception("Broom reading failed; using the fallback.")

        # Fallback always uses the unique closest available model
        pick = self.assign_model(words, user_id=user_id)
        return fallback_broom(words, pick) if pick else None

    def portrait_file(self, broom: dict) -> discord.File | None:
        broom = _ensure(broom)
        path = ASSETS_DIR / image_name(broom["model"])
        if not path.exists():
            log.warning("Missing broom art %s", path)
            return None
        return discord.File(io.BytesIO(path.read_bytes()), filename="broom.png")

    def embed_for(self, member, broom: dict, fresh: bool = False,
                  redirected_from: str | None = None) -> discord.Embed:
        broom = _ensure(broom)
        model = broom["model"]
        base = broom["stats"]
        eff = effective_stats(broom)
        serious_lines = []
        for k in SERIOUS_STATS:
            bonus = int(broom.get(bonus_field(k), 0) or 0)
            line = f"**{STAT_LABELS[k]}** {_bar(eff.get(k, 0))}"
            if bonus:
                line += f"  *(base {base.get(k, 0)} +{bonus})*"
            serious_lines.append(line)
        serious = "\n".join(serious_lines)
        silly = "\n".join(
            f"**{STAT_LABELS[k]}** {_bar(base.get(k, 0))}" for k in SILLY_STATS
        )
        note = ""
        if redirected_from and redirected_from != model:
            note = (f"\n\n-# {redirected_from} was already claimed — "
                    f"the next-closest broom found you instead.")
        embed = discord.Embed(
            title=("A broom answers your grip…" if fresh
                   else f"{member.display_name}'s broom"),
            description=(f"**The {model}**\n*{broom['finish']}*\n\n"
                         f"{broom['reading']}{note}"),
            color=BROOM_COLOR,
        )
        embed.add_field(name="Flight", value=serious, inline=False)
        embed.add_field(name="Also (deeply scientific)", value=silly, inline=False)
        if (ASSETS_DIR / image_name(model)).exists():
            embed.set_image(url="attachment://broom.png")
        left = len(self.available_models())
        if fresh:
            embed.set_footer(
                text=f"Yours alone • {left} brooms still unclaimed • /broomupgrade toward 10"
            )
        else:
            embed.set_footer(
                text=f"Unique claim • {left} brooms still unclaimed • /broomupgrade toward 10"
            )
        return embed

    def can_upgrade(self, broom: dict, stat: str) -> tuple[bool, str]:
        if stat not in SERIOUS_STATS:
            return False, "Pick **Speed** or **Altitude**."
        broom = _ensure(broom)
        if effective_stats(broom).get(stat, 0) >= MAX_STAT:
            return False, f"**{STAT_LABELS[stat]}** is already maxed at **{MAX_STAT}/10**."
        return True, ""

    def apply_upgrade(self, user_id: int, stat: str, amount: int = 1) -> dict | None:
        """Add upgrade bonus(es). Returns updated broom or None if missing/capped."""
        raw = self.owners.get(str(user_id))
        if not raw:
            return None
        broom = _ensure(raw)
        ok, _ = self.can_upgrade(broom, stat)
        if not ok:
            return None
        field = bonus_field(stat)
        base = int(broom["stats"].get(stat, 0))
        have = int(broom.get(field, 0) or 0)
        room = MAX_STAT - (base + have)
        gain = max(0, min(int(amount), room))
        if gain <= 0:
            return None
        broom[field] = have + gain
        broom = _ensure(broom)
        self.owners[str(user_id)] = broom
        self.save()
        return broom

    @app_commands.command(
        name="broom",
        description="Claim your unique broom from your wand words — fixed portrait, silly stats, one owner each.",
    )
    @app_commands.describe(member="Whose broom to see (leave blank for your own)")
    async def broom(self, interaction: discord.Interaction, member: discord.Member = None):
        hexes = self.bot.get_cog("Hexes")
        if hexes and await hexes.deny_if_limp_wand(interaction):
            return

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

        reserved = RESERVED_BROOMS.get(target.id)
        open_left = [
            m for m in self.available_models()
            if m not in self.reserved_held() or m == reserved
        ]
        if not open_left and not reserved:
            await interaction.response.send_message(
                "Every broom in Velmora has already been claimed. "
                "A staff `/broomreset` frees a claim without touching their wand.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        preferred = (
            reserved if reserved
            else preference_rank(wand["words"])[0]
        )
        result = await self.fit(wand["words"], user_id=target.id)
        if not result:
            await interaction.followup.send(
                "Every broom in Velmora has already been claimed.", ephemeral=True
            )
            return

        # Final claim guard (concurrent /broom)
        claimed = self.claimed_models()
        if result["model"] in claimed and claimed[result["model"]] != str(target.id):
            alt = self.assign_model(wand["words"], user_id=target.id)
            if not alt:
                await interaction.followup.send(
                    "Every broom in Velmora has already been claimed.", ephemeral=True
                )
                return
            result = fallback_broom(wand["words"], alt)

        self.owners[str(target.id)] = result
        self.save()
        redirected = preferred if preferred != result["model"] else None
        embed = self.embed_for(target, result, fresh=True, redirected_from=redirected)
        file = self.portrait_file(result)
        kwargs = {"embed": embed}
        if file:
            kwargs["file"] = file
        await interaction.followup.send(**kwargs)

    @app_commands.command(
        name="broomupgrade",
        description="Raise a broom's Speed or Altitude toward 10 (yours or @member — you pay).",
    )
    @app_commands.describe(
        stat="Speed or Altitude (control)",
        pay_with="How you'll pay for this bump",
        member="Whose broom to upgrade (leave blank for your own)",
        descent_mat="Required when paying with Descent materials (100 of one kind)",
    )
    @app_commands.choices(
        stat=[
            app_commands.Choice(name="Speed", value="speed"),
            app_commands.Choice(name="Altitude / control", value="altitude"),
        ],
        pay_with=[
            app_commands.Choice(
                name=f"Pitch Resin ×{RESIN_PER_UPGRADE} (forage)",
                value="resin",
            ),
            app_commands.Choice(
                name=f"Descent material ×{DESCENT_MATS_PER_UPGRADE}",
                value="descent",
            ),
            app_commands.Choice(name="Marketplace broom token ×1", value="token"),
        ],
    )
    async def broomupgrade(
        self,
        interaction: discord.Interaction,
        stat: app_commands.Choice[str],
        pay_with: app_commands.Choice[str],
        member: discord.Member | None = None,
        descent_mat: str | None = None,
    ):
        hexes = self.bot.get_cog("Hexes")
        if hexes and await hexes.deny_if_limp_wand(interaction):
            return

        target = member or interaction.user
        payer = interaction.user
        stat_key = stat.value
        method = pay_with.value
        broom = self.broom_of(target.id)
        if not broom:
            if target.id == payer.id:
                await interaction.response.send_message(
                    "You need a broom first — `/broom`.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"{target.display_name} hasn't claimed a broom yet.",
                    ephemeral=True,
                )
            return
        ok, why = self.can_upgrade(broom, stat_key)
        if not ok:
            msg = why if target.id == payer.id else f"{target.display_name}'s broom: {why}"
            await interaction.response.send_message(msg, ephemeral=True)
            return

        world = self.bot.get_cog("World")
        market = self.bot.get_cog("Marketplace")
        label = STAT_LABELS[stat_key]
        before = effective_stats(broom)[stat_key]

        if method == "resin":
            if not world:
                await interaction.response.send_message(
                    "Satchels are out of reach right now.", ephemeral=True
                )
                return
            async with world.lock:
                student = world.student(payer)
                have = student.get("items", {}).get(PITCH_RESIN_ID, 0)
                if have < RESIN_PER_UPGRADE:
                    await interaction.response.send_message(
                        f"You need **{RESIN_PER_UPGRADE}** × Pitch Resin "
                        f"(forage in the Garden or Forbidden Woods). "
                        f"You have **{have}**.",
                        ephemeral=True,
                    )
                    return
                if not world.world.take(student, PITCH_RESIN_ID, RESIN_PER_UPGRADE):
                    await interaction.response.send_message(
                        "Something stuck in the resin — try again.", ephemeral=True
                    )
                    return
                world.save()
            paid = f"**{RESIN_PER_UPGRADE}** × Pitch Resin"

        elif method == "descent":
            if not world:
                await interaction.response.send_message(
                    "Satchels are out of reach right now.", ephemeral=True
                )
                return
            if descent_mat not in DESCENT_MAT_IDS:
                await interaction.response.send_message(
                    f"Pick a Descent material and bring "
                    f"**{DESCENT_MATS_PER_UPGRADE}** of it.",
                    ephemeral=True,
                )
                return
            meta = world.world.items.get(descent_mat) if world else None
            if not meta:
                await interaction.response.send_message(
                    "That material isn't recognized.", ephemeral=True
                )
                return
            async with world.lock:
                student = world.student(payer)
                have = student.get("items", {}).get(descent_mat, 0)
                if have < DESCENT_MATS_PER_UPGRADE:
                    await interaction.response.send_message(
                        f"You need **{DESCENT_MATS_PER_UPGRADE}** × "
                        f"{meta['emoji']} **{meta['name']}** (you have **{have}**).",
                        ephemeral=True,
                    )
                    return
                if not world.world.take(student, descent_mat, DESCENT_MATS_PER_UPGRADE):
                    await interaction.response.send_message(
                        "Something moved in your satchel — try again.", ephemeral=True
                    )
                    return
                world.save()
            paid = (
                f"**{DESCENT_MATS_PER_UPGRADE}** × {meta['emoji']} **{meta['name']}**"
            )

        elif method == "token":
            if not market:
                await interaction.response.send_message(
                    "The Marketplace isn't loaded.", ephemeral=True
                )
                return
            if market.broom_token_count(payer.id, stat_key) < 1:
                whose = "your" if target.id == payer.id else f"{target.display_name}'s"
                await interaction.response.send_message(
                    f"You don't have a **{label}** broom token. "
                    f"Buy one with `/market broomtoken` "
                    f"(**{BROOM_TOKEN_PRICE}** pts) for {whose} next bump.",
                    ephemeral=True,
                )
                return
            if not market.spend_broom_token(payer.id, stat_key):
                await interaction.response.send_message(
                    "That token slipped away — try again.", ephemeral=True
                )
                return
            paid = f"1 × Marketplace **{label}** token"

        else:
            await interaction.response.send_message(
                "Unknown payment method.", ephemeral=True
            )
            return

        updated = self.apply_upgrade(target.id, stat_key, 1)
        if not updated:
            # Refund best-effort if the upgrade somehow failed after payment.
            if method == "resin" and world:
                async with world.lock:
                    world.world.give(
                        world.student(payer), PITCH_RESIN_ID, RESIN_PER_UPGRADE
                    )
                    world.save()
            elif method == "descent" and world and descent_mat:
                async with world.lock:
                    world.world.give(
                        world.student(payer),
                        descent_mat,
                        DESCENT_MATS_PER_UPGRADE,
                    )
                    world.save()
            elif method == "token" and market:
                market.grant_broom_token(payer.id, stat_key)
            await interaction.response.send_message(
                f"**{label}** couldn't be raised further — payment returned.",
                ephemeral=True,
            )
            return

        after = effective_stats(updated)[stat_key]
        if target.id == payer.id:
            who_line = (
                f"{payer.display_name} tunes **{label}**: "
                f"**{before}/10** → **{after}/10**."
            )
        else:
            who_line = (
                f"{payer.display_name} tunes **{target.display_name}**'s **{label}**: "
                f"**{before}/10** → **{after}/10**."
            )
        embed = discord.Embed(
            title=f"🧹 {updated['model']} upgraded",
            description=f"{who_line}\n\nPaid with {paid}.",
            color=BROOM_COLOR,
        )
        embed.set_footer(text="Base model sheet unchanged • upgrades stack toward 10")
        await interaction.response.send_message(embed=embed)

    @broomupgrade.autocomplete("descent_mat")
    async def _broomupgrade_descent_ac(
        self, interaction: discord.Interaction, current: str
    ):
        world = self.bot.get_cog("World")
        q = (current or "").lower()
        have = {}
        items = {}
        if world:
            have = world.student(interaction.user).get("items", {})
            items = world.world.items
        out = []
        for iid in DESCENT_MAT_IDS:
            meta = items.get(iid)
            if not meta:
                continue
            n = int(have.get(iid, 0))
            label = f"{meta['emoji']} {meta['name']} ×{n}"[:100]
            if q and q not in label.lower() and q not in iid:
                continue
            out.append(app_commands.Choice(name=label, value=iid))
        return out[:25]

    async def upgrade_broom(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        stat: app_commands.Choice[str],
        amount: app_commands.Range[int, 1, 10] = 1,
    ):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That one's for staff.", ephemeral=True)
            return
        broom = self.broom_of(member.id)
        if not broom:
            await interaction.response.send_message(
                f"{member.display_name} hasn't claimed a broom yet.", ephemeral=True
            )
            return
        stat_key = stat.value
        ok, why = self.can_upgrade(broom, stat_key)
        if not ok:
            await interaction.response.send_message(why, ephemeral=True)
            return
        before = effective_stats(broom)[stat_key]
        updated = self.apply_upgrade(member.id, stat_key, int(amount))
        if not updated:
            await interaction.response.send_message(
                f"Couldn't raise **{STAT_LABELS[stat_key]}** further.", ephemeral=True
            )
            return
        after = effective_stats(updated)[stat_key]
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"🧹 Staff upgrade — {updated['model']}",
                description=(
                    f"**{STAT_LABELS[stat_key]}** for {member.display_name}: "
                    f"**{before}/10** → **{after}/10**."
                ),
                color=BROOM_COLOR,
            ),
            ephemeral=True,
        )

    async def broomreset(self, interaction: discord.Interaction, member: discord.Member):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That one's for staff.", ephemeral=True)
            return
        if not self.release(member.id):
            await interaction.response.send_message(
                f"{member.display_name} doesn't have a broom to release.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"{member.display_name}'s broom has been released. "
            "Their wand and patronus are untouched — they can `/broom` again.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Brooms(bot))
