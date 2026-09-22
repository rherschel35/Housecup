"""
The Dementor Alarm. Something cold slips into one of the social channels
on its own, roughly twice a day - and a headmaster can always summon one
on the spot.

    /cast spell:Patronus       - anyone, banishes the dementor in this channel
    /dementor channels         - staff, set the 4 channels it can appear in
    /dementor summon [channel] - staff, make one appear right now
    /dementor status           - staff, what's configured and what's active

Only someone who has already cast a patronus (/patronus) can banish one -
that's the whole point of the game. Whoever banishes it first wins; the
flavour text uses their own patronus's shape. No consequences yet for
leaving one be; it just sits there, cold, until somebody deals with it.
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

from cogs.world_engine import today

log = logging.getLogger("velmora.dementors")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "dementors.json"

SPAWNS_PER_DAY = 2
TICK_MINUTES = 5
TICKS_PER_DAY = (24 * 60) // TICK_MINUTES
SPAWN_CHANCE = SPAWNS_PER_DAY / TICKS_PER_DAY   # per tick, while under the daily cap

REWARD_POINTS = 3
DARK = 0x0B0B12

ARRIVALS = [
    "The room goes cold. Every candle nearby gutters at once, and something that isn't "
    "quite shadow settles into the corner.",
    "Somewhere in the room, joy just left. It's hard to say exactly where. It doesn't matter - "
    "it's spreading.",
    "The temperature drops so fast someone's drink frosts over. A Dementor has found its way in.",
    "You didn't hear it arrive. You never do. But the laughter in the room just stopped, all at once.",
]

BANISHED = [
    "{patronus} bursts from {member}'s wand in a wash of silver light, and the Dementor flees "
    "before it, shrieking, back into whatever brought it here.",
    "A silver {animal} charges out of nowhere at {member}'s command. The Dementor doesn't even "
    "try to fight it - it's already gone.",
    "{member} raises their wand, and {patronus} drives the cold out of the room like it was "
    "never there at all.",
]


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


class Dementors(commands.Cog):
    group = app_commands.Group(name="dementor", description="(staff) Run the Dementor Alarm.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rng = random.Random()
        self.lock = None  # set on cog_load, needs a running loop
        self.state = self._load()
        self.state.setdefault("channel_ids", [])
        self.state.setdefault("active", None)
        self.state.setdefault("day_key", "")
        self.state.setdefault("spawns_today", 0)

    async def cog_load(self):
        import asyncio
        self.lock = asyncio.Lock()
        self.tick.start()

    async def cog_unload(self):
        self.tick.cancel()

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.exception("Dementor state unreadable - starting empty.")
            return {}

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Couldn't save dementor state.")

    def _roll_day(self, now: float = None) -> None:
        key = today(now)
        if self.state.get("day_key") != key:
            self.state["day_key"] = key
            self.state["spawns_today"] = 0

    # ------------------------------------------------------------ spawning

    def embed_arrival(self) -> discord.Embed:
        return discord.Embed(
            title="🖤 A Dementor has appeared",
            description=self.rng.choice(ARRIVALS) + "\n\nOnly a cast patronus can drive it out - "
                        "`/cast spell:Patronus`.",
            color=DARK,
        )

    async def spawn(self, channel_id: int = None) -> discord.TextChannel | None:
        """Make a dementor appear. Picks a random configured channel if none
        is given. Returns the channel it landed in, or None if it couldn't."""
        pool = self.state.get("channel_ids", [])
        if channel_id is None:
            if not pool:
                return None
            channel_id = self.rng.choice(pool)
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                log.warning("Dementor channel %s not reachable", channel_id)
                return None
        try:
            msg = await channel.send(embed=self.embed_arrival())
        except discord.HTTPException:
            log.exception("Couldn't post the dementor in %s", channel_id)
            return None
        self.state["active"] = {"channel_id": channel_id, "message_id": msg.id, "spawned_at": time.time()}
        self.state["spawns_today"] = self.state.get("spawns_today", 0) + 1
        self.save()
        log.info("A dementor appeared in channel %s", channel_id)
        return channel

    @tasks.loop(minutes=TICK_MINUTES)
    async def tick(self):
        async with self.lock:
            self._roll_day()
            if self.state.get("active"):
                return
            if self.state.get("spawns_today", 0) >= SPAWNS_PER_DAY:
                return
            if not self.state.get("channel_ids"):
                return
            if self.rng.random() >= SPAWN_CHANCE:
                return
            await self.spawn()

    @tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ /cast

    @app_commands.command(name="cast", description="Cast a spell at whatever's in this channel.")
    @app_commands.describe(spell="Which spell to cast")
    @app_commands.choices(spell=[app_commands.Choice(name="Patronus", value="patronus")])
    async def cast(self, interaction: discord.Interaction, spell: app_commands.Choice[str]):
        if spell.value != "patronus":
            await interaction.response.send_message("That spell isn't taught here yet.", ephemeral=True)
            return

        patronus_cog = self.bot.get_cog("Patronus")
        mine = patronus_cog.patronus_of(interaction.user.id) if patronus_cog else None
        if not mine:
            await interaction.response.send_message(
                "You have no patronus to send against it yet - cast one with `/patronus` first.",
                ephemeral=True,
            )
            return

        async with self.lock:
            active = self.state.get("active")
            if not active or active["channel_id"] != interaction.channel_id:
                await interaction.response.send_message(
                    "The air here feels perfectly normal. Nothing to banish.", ephemeral=True
                )
                return
            self.state["active"] = None
            self.save()

        store = self.bot.get_cog("Store")
        house = store.member_house(interaction.user) if store else None
        awarded = 0
        if store and house:
            store.record(
                house=house,
                delta=REWARD_POINTS,
                actor_id=self.bot.user.id if self.bot.user else 0,
                target_id=interaction.user.id,
                reason="Banished a Dementor",
            )
            awarded = REWARD_POINTS

        animal = mine["animal"].lower()
        line = self.rng.choice(BANISHED).format(
            patronus=f"{_article(animal)} silver {animal}",
            animal=animal,
            member=interaction.user.mention,
        )
        embed = discord.Embed(title="✨ The Dementor is banished", description=line, color=0xC4CCD6)
        if awarded:
            from cogs.store import HOUSES
            embed.set_footer(text=f"+{awarded} points for House {HOUSES[house]['name']}")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------ staff

    async def _staff(self, interaction) -> bool:
        store = self.bot.get_cog("Store")
        if store and store.is_staff(interaction.user):
            return True
        await interaction.response.send_message("That's for staff.", ephemeral=True)
        return False

    @group.command(name="channels", description="(staff) Set the 4 channels a dementor can appear in.")
    @app_commands.describe(a="First channel", b="Second channel", c="Third channel", d="Fourth channel")
    async def channels(self, interaction: discord.Interaction, a: discord.TextChannel,
                        b: discord.TextChannel, c: discord.TextChannel, d: discord.TextChannel):
        if not await self._staff(interaction):
            return
        self.state["channel_ids"] = [a.id, b.id, c.id, d.id]
        self.save()
        await interaction.response.send_message(
            f"Dementors may now appear in {a.mention}, {b.mention}, {c.mention}, {d.mention}.",
            ephemeral=True,
        )

    @group.command(name="summon", description="(staff) Make a dementor appear right now.")
    @app_commands.describe(channel="Where (leave blank for a random configured channel)")
    async def summon(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not await self._staff(interaction):
            return
        async with self.lock:
            if self.state.get("active"):
                await interaction.response.send_message(
                    "One's already loose somewhere. Let it get banished first.", ephemeral=True)
                return
            if channel and channel.id not in self.state.get("channel_ids", []):
                await interaction.response.send_message(
                    "That channel isn't one of the four configured with `/dementor channels`.",
                    ephemeral=True)
                return
            self._roll_day()
            landed = await self.spawn(channel.id if channel else None)
        if not landed:
            await interaction.response.send_message(
                "No channels are configured yet - run `/dementor channels` first.", ephemeral=True)
            return
        await interaction.response.send_message(f"Summoned in {landed.mention}.", ephemeral=True)

    @group.command(name="status", description="(staff) What's configured and what's active.")
    async def status(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        self._roll_day()
        pool = self.state.get("channel_ids", [])
        lines = [f"Channels: {', '.join(f'<#{c}>' for c in pool) if pool else 'none set'}",
                 f"Spawned today: {self.state.get('spawns_today', 0)}/{SPAWNS_PER_DAY}"]
        active = self.state.get("active")
        if active:
            age = int((time.time() - active["spawned_at"]) / 60)
            lines.append(f"Active now in <#{active['channel_id']}>, {age} minute(s) ago.")
        else:
            lines.append("Nothing active right now.")
        embed = discord.Embed(title="Dementor Alarm", description="\n".join(lines), color=DARK)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Dementors(bot))
