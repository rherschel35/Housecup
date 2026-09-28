"""
The Velmora rumor mill (moved here from the old whisper bot).

- /rumor           - anyone can conjure a rumor on demand.
- Every RUMOR_INTERVAL_HOURS (default 6) a rumor is posted in RUMOR_CHANNEL_ID.

Rules:
- Only students who belong to a house are ever tagged (the same house
  lookup the points system uses). No house, no rumors about you. Bots never.
- The same person isn't tagged again until several others have had a turn.
- Rumors come in four flavors:
    explore      - tips and secrets about exploring Velmora (untagged)
    funny        - ridiculous student gossip ({a} / {a}+{b})
    headmasters  - odd staff-corridor whispers (untagged)
    ghosts       - embarrassing house-ghost gossip (untagged)

The rumor bank lives in data/rumors.json - edit it freely: {a} is the first
person tagged, {b} the second.
"""

import json
import logging
import os
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("velmora.rumors")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BANK_PATH = DATA_DIR / "rumors.json"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "rumor_state.json"

HEADER = "📜 **A new rumor is spreading through Velmora Academy...**"

# Weighted mix for untagged world rumors vs tagged student comedy.
UNTAGGED_WEIGHTS = {
    "explore": 0.28,
    "headmasters": 0.14,
    "ghosts": 0.14,
}
# Remainder (~0.44) is tagged student funny.
TWO_PERSON_CHANCE = 0.4
RECENT_MEMORY = 8           # how many recent targets to skip over when possible


def _interval_hours() -> float:
    try:
        return max(0.05, float(os.getenv("RUMOR_INTERVAL_HOURS") or 6))
    except ValueError:
        return 6.0


def _channel_id():
    raw = os.getenv("RUMOR_CHANNEL_ID", "")
    return int(raw) if raw.isdigit() else None


class Rumors(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rng = random.Random()
        try:
            self.bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Couldn't read rumors.json")
            self.bank = {
                "explore": [], "one_person": [], "two_person": [],
                "headmasters": [], "ghosts": [], "lore_tagline": "",
            }
        try:
            self.state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.state = {}
        self.state.setdefault("last_post", 0)
        self.state.setdefault("recent", [])
        self.state.setdefault("recent_templates", [])

    async def cog_load(self):
        self.scheduler.start()

    async def cog_unload(self):
        self.scheduler.cancel()

    def save(self):
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state), encoding="utf-8")
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Couldn't save rumor state")

    # ------------------------------------------------------------ choosing people

    def housed_members(self, guild) -> list:
        """(member, house) for every human in a house."""
        store = self.bot.get_cog("Store")
        if not store:
            return []
        out = []
        for m in getattr(guild, "members", []):
            if getattr(m, "bot", False):
                continue
            try:
                house = store.member_house(m)
            except Exception:
                house = None
            if house:
                out.append((m, house))
        return out

    def pick_person(self, pool, exclude=()):
        """Someone who hasn't been the subject lately, if at all possible."""
        recent = set(self.state["recent"][-RECENT_MEMORY:])
        choices = [p for p in pool if p[0].id not in exclude and p[0].id not in recent]
        if not choices:
            choices = [p for p in pool if p[0].id not in exclude]
        if not choices:
            return None
        picked = self.rng.choice(choices)
        self.state["recent"] = (self.state["recent"] + [picked[0].id])[-50:]
        return picked

    def pick_template(self, templates):
        recent = set(self.state["recent_templates"])
        fresh = [t for t in templates if t not in recent] or templates
        t = self.rng.choice(fresh)
        self.state["recent_templates"] = (self.state["recent_templates"] + [t])[-30:]
        return t

    def _untagged(self, key: str) -> str | None:
        pool = self.bank.get(key) or []
        if not pool:
            return None
        text = self.pick_template(pool)
        tagline = self.bank.get("lore_tagline", "")
        if key == "explore" and tagline:
            return f"{text}\n\n{tagline}".strip()
        return text

    def _funny(self, guild) -> str | None:
        pool = self.housed_members(guild)
        if not pool:
            return None
        a, _house = self.pick_person(pool)
        if not a:
            return None
        if (len(pool) >= 2 and self.rng.random() < TWO_PERSON_CHANCE
                and self.bank.get("two_person")):
            second = self.pick_person(pool, exclude={a.id})
            if second:
                t = self.pick_template(self.bank["two_person"])
                return t.replace("{a}", a.mention).replace("{b}", second[0].mention)
        if not self.bank.get("one_person"):
            return None
        return self.pick_template(self.bank["one_person"]).replace("{a}", a.mention)

    # ------------------------------------------------------------ building a rumor

    def build(self, guild):
        """Returns the rumor text, or None if nothing can be said yet."""
        roll = self.rng.random()
        cursor = 0.0
        for key, weight in UNTAGGED_WEIGHTS.items():
            cursor += weight
            if roll < cursor:
                text = self._untagged(key)
                if text:
                    return text

        funny = self._funny(guild)
        if funny:
            return funny

        # Nobody housed yet (or empty funny pools) — fall back to any untagged bank.
        for key in ("explore", "ghosts", "headmasters"):
            text = self._untagged(key)
            if text:
                return text
        return None

    @staticmethod
    def message(rumor: str) -> str:
        return f"{HEADER}\n\n{rumor}"

    # ------------------------------------------------------------ /rumor

    @app_commands.command(name="rumor", description="Spread a spicy, funny, or silly rumor around Velmora Academy.")
    async def rumor(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This only works inside a server.", ephemeral=True)
            return
        rumor = self.build(interaction.guild)
        self.save()
        if not rumor:
            await interaction.response.send_message(
                "The rumor mill is empty right now. Try again later.", ephemeral=True)
            return
        await interaction.response.send_message(
            self.message(rumor), allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))

    # ------------------------------------------------------------ the schedule

    @tasks.loop(minutes=5)
    async def scheduler(self):
        cid = _channel_id()
        if not cid:
            return
        if time.time() - self.state.get("last_post", 0) < _interval_hours() * 3600:
            return
        channel = self.bot.get_channel(cid)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(cid)
            except discord.HTTPException:
                log.warning("Rumor channel %s not reachable", cid)
                return
        rumor = self.build(channel.guild)
        self.state["last_post"] = time.time()
        self.save()
        if not rumor:
            log.info("Nothing to rumor about right now - skipping.")
            return
        try:
            await channel.send(self.message(rumor),
                               allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            log.info("Posted a scheduled rumor")
        except discord.HTTPException:
            log.exception("Scheduled rumor failed")

    @scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()
        # First run after a restart shouldn't post straight away if one went out recently;
        # on the very first run ever, wait one interval like the old bot did.
        if not self.state.get("last_post"):
            self.state["last_post"] = time.time()
            self.save()
        log.info("Rumors every %s hour(s) in channel %s", _interval_hours(), _channel_id())


async def setup(bot: commands.Bot):
    await bot.add_cog(Rumors(bot))
