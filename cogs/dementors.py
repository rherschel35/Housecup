"""
Wild Threats. Something slips into one of the social channels on its own,
a few times a day - and a headmaster can always summon one on the spot.

    /cast
        - anyone, opens spell buttons against whatever's here
        - CAST_AUTO_USER_ID auto-picks the right spell (no buttons)
    /staff dementor channels          - set the 4 channels they can appear in
    /staff dementor summon [channel] [creature] - make one appear right now
        (study hall is allowed for practice; Attack waves never go there)
    /staff dementor status            - what's configured and what's active

Every creature has exactly one spell that actually works on it, and the
post never says which - that's the whole game. Cast the wrong one and
nothing happens; no penalty, just try something else (or ask someone who
knows). A Dementor is still only driven out by an already-cast Patronus,
same as always.

Most creatures fall to a single clean hit. A couple don't: they take two
hits from two DIFFERENT people before they're actually gone, so a lone
wizard can weaken one but can't finish it alone. Everyone who lands the
right hit shares in the reward once it's down.

A Storm Sprite is rarer and skittish - leave it too long and it bolts to
another configured channel rather than wait to be caught.

When /explore or /forage in a dangerous place stirs one up, that student
gets 25 seconds alone to /cast at it. After that window, anyone can try.
Scheduled sightings and staff summons stay open to everyone from the start.

    /staff dementor eventstart [minutes] [name]  - start "Attack on Velmora"
    /staff dementor eventend                     - end it early
    /staff dementor eventstatus                  - how it's going

For a limited time (5 minutes by default), a fresh wave of monsters floods
ALL FOUR configured channels every 23 seconds - whatever was still standing
gets swept aside for the new wave. Kills earn personal "rep" during the
event instead of house points right away; when the clock runs out (or
staff end it early), every contributor's rep is converted into points for
their house all at once, and whoever racked up the most rep gets a bonus
on top. One big scoreboard reveal at the end, in every channel that fought.

Event casts and wave posts release the shared lock before talking to
Discord, so a busy Attack doesn't freeze every other /cast behind one
slow image upload.
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

from cogs.velmora_channels import STUDY_HALL_CHANNEL_ID
from cogs.world_engine import today

log = logging.getLogger("velmora.dementors")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "dementors.json"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "wild_threat_art_assets"

SPAWNS_PER_DAY = 2
TICK_MINUTES = 5
TICKS_PER_DAY = (24 * 60) // TICK_MINUTES
SPAWN_CHANCE = SPAWNS_PER_DAY / TICKS_PER_DAY   # per tick, while under the daily cap

WANDER_MINUTES = 10   # how long a creature that can wander waits before it bolts
FINDER_EXCLUSIVE_SECONDS = 25  # explorer who stirred one up gets first crack

REWARD_POINTS = 3   # kept for backward compatibility; see CREATURES["dementor"]["points"]
DARK = 0x0B0B12
THREAT_ALERT_COLOR = 0xC0392B  # every "X has appeared" alert, regardless of creature, so it never blends in

EVENT_WAVE_SECONDS = 23
EVENT_DEFAULT_MINUTES = 5
EVENT_MAX_MINUTES = 60
EVENT_MVP_BONUS = 5
EVENT_COLOR = 0x8A2F2F

ATTACK_INTRO = [
    "Something has broken through the wards. Fight back - every monster you put down counts "
    "toward the tally. Nobody's told which spell beats which. Work it out, or find someone who knows.",
    "The castle's defenses are down, all at once, everywhere. Hold the line - Velmora will "
    "remember who answered the call, and by how much.",
]

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

# ---------------------------------------------------------------- creatures
#
# "weak": the /cast spell value that actually works. "patronus" is handled
#   by the original dementor path below (it needs an already-cast patronus,
#   not just the right pick). Everything else uses the five duel spells
#   from cogs/duels.py: hex, ward, disarm, bind, mirror.
# "pack": how many DIFFERENT people need to land that spell before it's
#   actually gone. 1 means one clean hit does it.
# "points": awarded to the house of EVERY contributor once it's defeated
#   (not split - a pack of 2 means two people each get the full amount).
#   During an event, this same number is what's earned as personal rep
#   instead, and only gets converted to house points at the very end.
# "wander": if true, an uncaught one relocates to another configured
#   channel after WANDER_MINUTES instead of waiting to be found. (Outside
#   an event only - during an event, waves already replace it every 15s.)

CREATURES = {
    "dementor": {
        "name": "Dementor", "emoji": "🖤", "color": DARK, "weak": "patronus",
        "pack": 1, "points": 3, "wander": False,
        "image": "Dementor.png",
        "defeat_image": "Dementor_Defeat.png",
        "arrivals": ARRIVALS,
    },
    "shadow_wolf": {
        "name": "Shadow Wolf", "emoji": "🐺", "color": 0x2B2B33, "weak": "hex",
        "pack": 1, "points": 3, "wander": False,
        "image": "Shadow_Wolf.png",
        "defeat_image": "Shadow_Wolf_Defeat.png",
        "arrivals": [
            "Something low and fast slips between people's feet, all teeth and no sound.",
            "A shadow peels off the wall and starts circling. It hasn't decided who yet.",
            "You hear it before you see it - claws on stone, moving quick.",
        ],
        "victory": [
            "{member}'s spell catches it mid-lunge. It yelps once and is gone.",
            "{member} doesn't wait for it to strike first. One clean hit and it scatters into smoke.",
        ],
        "progress": [],  # pack 1: never shown, resolves in one hit
    },
    "wisp_swarm": {
        "name": "Wisp Swarm", "emoji": "🌫️", "color": 0x9AA7B0, "weak": "mirror",
        "pack": 1, "points": 3, "wander": False,
        "image": "Wisp_Swarm.png",
        "defeat_image": "Wisp_Swarm_Defeat.png",
        "arrivals": [
            "A cluster of pale lights drifts in through the window, humming faintly.",
            "The air fills with soft floating wisps. They don't seem hostile. They also don't leave.",
            "Little cold lights blink into being, one by one, until there are far too many.",
        ],
        "victory": [
            "{member}'s spell throws every wisp's own glow straight back at it, and the whole swarm winks out at once.",
            "{member} turns the swarm's light against itself. It scatters like blown-out candles.",
        ],
        "progress": [],
    },
    "storm_sprite": {
        "name": "Storm Sprite", "emoji": "⚡", "color": 0x4B6EF5, "weak": "ward",
        "pack": 1, "points": 5, "wander": True,
        "image": "Storm_Sprite.png",
        "defeat_image": "Storm_Sprite_Defeat.png",
        "arrivals": [
            "A crackle of static runs along the ceiling and drops, grinning, into the room.",
            "The lights flicker once. Something small, bright, and extremely pleased with itself has arrived.",
        ],
        "victory": [
            "{member}'s ward holds against it, and for once it can't wriggle free. Caught.",
            "{member} boxes it in before it can bolt again. It sparks once, sulking, and fades.",
        ],
        "wander_lines": [
            "It's gone before anyone gets close - and turned up somewhere else entirely.",
            "It bolts through the wall the second it feels a spell coming. It'll be back. Elsewhere.",
        ],
        "progress": [],
    },
    "stone_golem": {
        "name": "Stone Golem", "emoji": "🪨", "color": 0x6E6455, "weak": "bind",
        "pack": 2, "points": 4, "wander": False,
        "image": "Stone_Golem.png",
        "defeat_image": "Stone_Golem_Defeat.png",
        "arrivals": [
            "Something heavy grinds to a halt in the doorway. It's not going anywhere on its own.",
            "A shape built out of loose stone hauls itself upright and just... stands there. Ominously.",
        ],
        "victory": [
            "{member} finishes what {other} started, and the golem folds back into a pile of ordinary rock.",
        ],
        "progress": [
            "{member}'s binding holds - the golem staggers, but it's still standing. It'll take someone else, too.",
        ],
    },
    "nightweaver": {
        "name": "Nightweaver", "emoji": "🕷️", "color": 0x2E1A33, "weak": "disarm",
        "pack": 2, "points": 4, "wander": False,
        "image": "Nightweaver.png",
        "defeat_image": "Nightweaver_Defeat.png",
        "arrivals": [
            "Thread-thin shadows stitch themselves across the ceiling, and something with too many legs waits at the center.",
            "A web that wasn't there a moment ago now stretches corner to corner. Something's home.",
        ],
        "victory": [
            "{member} strips away the last of its silk after {other} tore the first of it loose. It drops, and vanishes.",
        ],
        "progress": [
            "{member} tears its silk loose, but it spins more before anyone else can finish it.",
        ],
    },
}

SPAWN_WEIGHTS = {
    "dementor": 4, "shadow_wolf": 3, "wisp_swarm": 3,
    "stone_golem": 2, "nightweaver": 2, "storm_sprite": 1,
}

_DUEL_SPELL_NAMES = {"hex": "Hex", "ward": "Ward", "disarm": "Disarm", "bind": "Bind", "mirror": "Mirror"}
SPELL_ORDER = ("patronus", "hex", "ward", "disarm", "bind", "mirror")
SPELL_LABEL = {"patronus": "Patronus", **_DUEL_SPELL_NAMES}
CREATURE_CHOICES = [app_commands.Choice(name=c["name"], value=key) for key, c in CREATURES.items()]

# Headmaster convenience: /cast auto-picks the right weakness (no buttons).
# Everyone else gets ephemeral spell buttons. Override with CAST_AUTO_USER_ID;
# unset/0 disables.
_raw_cast_auto = os.getenv("CAST_AUTO_USER_ID", "555141900802457630")
CAST_AUTO_USER_ID = (
    int(_raw_cast_auto) if _raw_cast_auto and str(_raw_cast_auto).isdigit() else None
)


class SpellPick:
    """Tiny stand-in for app_commands.Choice so button casts share resolve code."""

    __slots__ = ("name", "value")

    def __init__(self, value: str):
        self.value = value
        self.name = SPELL_LABEL.get(value, value.title())


class CastSpellButton(discord.ui.Button):
    def __init__(self, spell_value: str):
        label = SPELL_LABEL.get(spell_value, spell_value.title())
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=f"cast:{spell_value}")
        self.spell_value = spell_value

    async def callback(self, interaction: discord.Interaction):
        view: CastSpellView = self.view  # type: ignore[assignment]
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("Those aren't your spells.", ephemeral=True)
            return
        view.stop()
        for item in view.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=view)
        except discord.DiscordException:
            pass
        await view.cog.resolve_cast(interaction, SpellPick(self.spell_value))


class CastSpellView(discord.ui.View):
    def __init__(self, cog: "Dementors", owner_id: int):
        super().__init__(timeout=45)
        self.cog = cog
        self.owner_id = owner_id
        for value in SPELL_ORDER:
            self.add_item(CastSpellButton(value))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


class Dementors(commands.Cog):
    # Nested under /staff after load (see staff_groups.nest_pure_staff_groups).
    group = app_commands.Group(
        name="dementor",
        description="Run the Wild Threats system.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rng = random.Random()
        self.lock = None  # set on cog_load, needs a running loop
        self.state = self._load()
        self.state.setdefault("channel_ids", [])
        self.state.setdefault("active", None)
        self.state.setdefault("day_key", "")
        self.state.setdefault("spawns_today", 0)
        self.state.setdefault("event", None)

    async def cog_load(self):
        import asyncio
        self.lock = asyncio.Lock()
        self.tick.start()
        self.event_tick.start()

    async def cog_unload(self):
        self.tick.cancel()
        self.event_tick.cancel()

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

    async def _get_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                log.warning("Threat channel %s not reachable", channel_id)
                return None
        return channel

    # ------------------------------------------------------------ spawning

    def _creature_file(self, creature_id: str, *, field: str = "image") -> discord.File | None:
        """Attachable portrait (arrival or defeat), or None if missing."""
        image = CREATURES.get(creature_id, {}).get(field)
        if not image:
            return None
        path = ASSETS_DIR / image
        if not path.is_file():
            log.warning("Missing wild-threat art for %s (%s)", creature_id, path)
            return None
        return discord.File(path, filename="monster.png")

    def _attach_art(self, embed: discord.Embed, creature_id: str, *, field: str = "image") -> discord.File | None:
        """Set embed image + return the File to send with it, if art exists."""
        file = self._creature_file(creature_id, field=field)
        if file:
            embed.set_image(url="attachment://monster.png")
        return file

    def _dementor_defeat_art(
        self, embed: discord.Embed, user_id: int
    ) -> discord.File | None:
        """Sealed caster's defeat portrait (or regular patronus art); else default."""
        patronus_cog = self.bot.get_cog("Patronus")
        if patronus_cog is not None:
            art = patronus_cog.art_file_for(user_id, field="defeat_art")
            if art is not None:
                embed.set_image(url="attachment://patronus.png")
                return art
        return self._attach_art(embed, "dementor", field="defeat_image")

    def embed_arrival(self, creature_id: str, finder_id: int | None = None,
                       exclusive_until: float | None = None) -> discord.Embed:
        c = CREATURES[creature_id]
        desc = self.rng.choice(c["arrivals"])
        if creature_id == "dementor":
            desc += "\n\nOnly a cast patronus can drive it out — `/cast`, then tap **Patronus**."
        if finder_id and exclusive_until and time.time() < exclusive_until:
            desc += (f"\n\n⏳ <@{finder_id}> stirred it up — they have until "
                     f"<t:{int(exclusive_until)}:R> to `/cast` first. "
                     f"Then anyone can try.")
        return discord.Embed(
            title=f"{c['emoji']} A {c['name']} has appeared",
            description=desc,
            color=THREAT_ALERT_COLOR,
        )

    def _finder_blocks(self, active: dict, user_id: int) -> str | None:
        """Ephemeral refusal while the explorer who stirred it up still has
        exclusive cast rights; None once the window is open or there was no finder."""
        until = active.get("exclusive_until")
        finder = active.get("finder_id")
        if not until or not finder:
            return None
        if time.time() >= until or user_id == finder:
            return None
        return (f"<@{finder}> stirred this one up — they have until "
                f"<t:{int(until)}:R> to cast at it. After that, anyone can try.")

    async def spawn(self, channel_id: int = None, creature_id: str = None,
                    finder_id: int | None = None) -> discord.TextChannel | None:
        """Make a creature appear. Picks a random configured channel if none
        is given, and a random creature (weighted) if none is given - except
        a manual, unspecified /staff dementor summon still defaults to a Dementor,
        same as it always has. When finder_id is set (explore/forage stir),
        that student gets FINDER_EXCLUSIVE_SECONDS alone to /cast. Returns
        the channel it landed in, or None."""
        pool = self.state.get("channel_ids", [])
        if channel_id is None:
            if not pool:
                return None
            channel_id = self.rng.choice(pool)
        channel = await self._get_channel(channel_id)
        if channel is None:
            return None
        if creature_id is None:
            creature_id = "dementor"
        now = time.time()
        exclusive_until = (now + FINDER_EXCLUSIVE_SECONDS) if finder_id else None
        embed = self.embed_arrival(creature_id, finder_id=finder_id,
                                   exclusive_until=exclusive_until)
        file = self._attach_art(embed, creature_id)
        try:
            msg = await channel.send(embed=embed, file=file) if file else await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Couldn't post the %s in %s", creature_id, channel_id)
            return None
        active = {
            "creature": creature_id, "channel_id": channel_id, "message_id": msg.id,
            "spawned_at": now, "hits": [],
        }
        if finder_id:
            active["finder_id"] = finder_id
            active["exclusive_until"] = exclusive_until
        self.state["active"] = active
        self.state["spawns_today"] = self.state.get("spawns_today", 0) + 1
        self.save()
        log.info("A %s appeared in channel %s (finder=%s)", creature_id, channel_id, finder_id)
        return channel

    async def try_ambient_spawn(self, channel_id: int, creature_id: str = None,
                                finder_id: int | None = None) -> bool:
        """Let another cog (right now: searching the Dungeons or the
        Forbidden Woods) try to drop a Wild Threat into a specific channel
        on the spot. Refuses quietly - no error, just nothing happens - if
        something's already loose or an event is running, so it never
        steals the encounter out from under another channel. finder_id is
        the explorer who stirred it — they get a short exclusive cast window."""
        async with self.lock:
            if self.state.get("active") or self.state.get("event"):
                return False
            # Unlike a manual /staff dementor summon, an ambient trigger like this
            # should draw from the whole weighted roster, not default to a
            # plain Dementor.
            landed = await self.spawn(channel_id, creature_id or self._roll_creature(),
                                      finder_id=finder_id)
        return bool(landed)

    def _roll_creature(self) -> str:
        keys = list(SPAWN_WEIGHTS.keys())
        weights = [SPAWN_WEIGHTS[k] for k in keys]
        return self.rng.choices(keys, weights=weights, k=1)[0]

    async def _wander(self, active: dict) -> None:
        """Relocate a wandering creature to a different configured channel."""
        c = CREATURES[active["creature"]]
        pool = [cid for cid in self.state.get("channel_ids", []) if cid != active["channel_id"]]
        if not pool:
            return
        new_id = self.rng.choice(pool)
        old_channel = self.bot.get_channel(active["channel_id"])
        if old_channel is not None and c.get("wander_lines"):
            try:
                await old_channel.send(self.rng.choice(c["wander_lines"]))
            except discord.HTTPException:
                pass
        new_channel = await self._get_channel(new_id)
        if new_channel is None:
            return
        creature_id = active["creature"]
        embed = self.embed_arrival(creature_id)
        file = self._attach_art(embed, creature_id)
        try:
            msg = (await new_channel.send(embed=embed, file=file) if file
                   else await new_channel.send(embed=embed))
        except discord.HTTPException:
            return
        active["channel_id"] = new_id
        active["message_id"] = msg.id
        active["spawned_at"] = time.time()
        active["hits"] = []
        # New channel, open season — the explorer's exclusive window doesn't travel.
        active.pop("finder_id", None)
        active.pop("exclusive_until", None)
        self.save()

    @tasks.loop(minutes=TICK_MINUTES)
    async def tick(self):
        async with self.lock:
            self._roll_day()
            if self.state.get("event"):
                return  # an event is running - it owns spawning right now
            active = self.state.get("active")
            if active:
                c = CREATURES.get(active["creature"], {})
                if c.get("wander") and time.time() - active["spawned_at"] >= WANDER_MINUTES * 60:
                    await self._wander(active)
                return
            if self.state.get("spawns_today", 0) >= SPAWNS_PER_DAY:
                return
            if not self.state.get("channel_ids"):
                return
            if self.rng.random() >= SPAWN_CHANCE:
                return
            await self.spawn(creature_id=self._roll_creature())

    @tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ /cast

    @staticmethod
    def _as_channel_id(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _creature_in_channel(self, channel_id: int) -> dict | None:
        """Active wild threat in this channel (event wave or solo spawn)."""
        channel_id = self._as_channel_id(channel_id)
        if channel_id is None:
            return None
        event = self.state.get("event")
        key = str(channel_id)
        if event and key in event.get("channels", {}):
            cid = event["channels"][key].get("creature")
            return CREATURES.get(cid) if cid else None
        active = self.state.get("active")
        if active and self._as_channel_id(active.get("channel_id")) == channel_id:
            return CREATURES.get(active.get("creature"))
        return None

    def _auto_spell_for(self, channel_id: int) -> SpellPick | None:
        creature = self._creature_in_channel(channel_id)
        if not creature:
            return None
        return SpellPick(creature["weak"])

    def _can_auto_cast(self, user_id: int) -> bool:
        return CAST_AUTO_USER_ID is not None and user_id == CAST_AUTO_USER_ID

    def _nothing_here_message(self, channel_id: int) -> str:
        """Clearer refusal when a wave is mid-landing vs truly empty air."""
        event = self.state.get("event")
        if event:
            key = str(self._as_channel_id(channel_id) or channel_id)
            if key not in event.get("channels", {}):
                return (
                    f"**{event.get('name', 'The attack')}** is still landing in this room — "
                    "try `/cast` again in a second."
                )
        return "The air here feels perfectly normal. Nothing to banish."

    @app_commands.command(name="cast", description="Cast a spell at whatever's in this channel.")
    async def cast(self, interaction: discord.Interaction):
        """Spell is chosen via ephemeral buttons."""
        hexes = self.bot.get_cog("Hexes")
        if hexes and await hexes.deny_if_limp_wand(interaction):
            return

        channel_id = interaction.channel_id
        if self._can_auto_cast(interaction.user.id):
            spell = self._auto_spell_for(channel_id)
            if spell is None:
                await interaction.response.send_message(
                    self._nothing_here_message(channel_id),
                    ephemeral=True,
                )
                return
            await self.resolve_cast(interaction, spell)
            return

        if self._creature_in_channel(channel_id) is None:
            await interaction.response.send_message(
                self._nothing_here_message(channel_id),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Which spell?",
            view=CastSpellView(self, interaction.user.id),
            ephemeral=True,
        )

    async def resolve_cast(self, interaction: discord.Interaction, spell: SpellPick):
        """Resolve a cast once the spell is known (auto-pick or button)."""
        event_reply = None
        async with self.lock:
            channel_id = interaction.channel_id
            event = self.state.get("event")
            key = str(self._as_channel_id(channel_id) or channel_id)
            if event and key in event.get("channels", {}):
                # Build the reply under the lock; Discord I/O happens after release
                # so a slow image upload can't freeze every other /cast.
                event_reply = self._cast_event(interaction, spell, event, key)
            else:
                active = self.state.get("active")
                active_cid = self._as_channel_id(active.get("channel_id")) if active else None
                if not active or active_cid != self._as_channel_id(channel_id):
                    await interaction.response.send_message(
                        self._nothing_here_message(channel_id), ephemeral=True
                    )
                    return
                blocked = self._finder_blocks(active, interaction.user.id)
                if blocked:
                    await interaction.response.send_message(blocked, ephemeral=True)
                    return
                creature_id = active["creature"]
                creature = CREATURES[creature_id]

                if creature_id == "dementor":
                    await self._cast_dementor(interaction, spell, active)
                    return

                if spell.value != creature["weak"]:
                    await interaction.response.send_message("Nothing happens.", ephemeral=True)
                    return

                if interaction.user.id in active.get("hits", []):
                    await interaction.response.send_message(
                        "You've already struck this one - it'll take someone else to finish it.",
                        ephemeral=True,
                    )
                    return

                active.setdefault("hits", []).append(interaction.user.id)

                if len(active["hits"]) < creature["pack"]:
                    self.save()
                    line = self.rng.choice(creature["progress"]).format(member=interaction.user.mention)
                    await interaction.response.send_message(
                        embed=discord.Embed(description=line, color=creature["color"])
                    )
                    return

                # Defeated - award every contributor and clear the encounter.
                self.state["active"] = None
                self.save()

        if event_reply is not None:
            await self._deliver_cast_reply(interaction, event_reply)
            return

        contributors = active["hits"]
        store = self.bot.get_cog("Store")
        credited = []
        for uid in contributors:
            member = interaction.guild.get_member(uid) if interaction.guild else None
            house = store.member_house(member) if (store and member) else None
            if store and house:
                store.record(
                    house=house,
                    delta=creature["points"],
                    actor_id=self.bot.user.id if self.bot.user else 0,
                    target_id=uid,
                    reason=f"Defeated a {creature['name']}",
                )
            credited.append((uid, house))

        if creature["pack"] > 1:
            others = ", ".join(f"<@{uid}>" for uid in contributors[:-1]) or "someone else"
            line = self.rng.choice(creature["victory"]).format(member=interaction.user.mention, other=others)
        else:
            line = self.rng.choice(creature["victory"]).format(member=interaction.user.mention)

        embed = discord.Embed(title=f"✨ The {creature['name']} is defeated", description=line, color=creature["color"])
        from cogs.store import HOUSES
        footer_parts = [
            f"+{creature['points']} for House {HOUSES[house]['name']}" for uid, house in credited if house
        ]
        if footer_parts:
            embed.set_footer(text=" • ".join(footer_parts))
        file = self._attach_art(embed, creature_id, field="defeat_image")
        if file:
            await interaction.response.send_message(embed=embed, file=file)
        else:
            await interaction.response.send_message(embed=embed)

    async def _cast_dementor(self, interaction, spell, active):
        """The original Dementor flow, unchanged: needs an already-cast
        patronus, resolves in a single hit, uses its own flavour text."""
        if spell.value != "patronus":
            await interaction.response.send_message("Nothing happens.", ephemeral=True)
            return

        patronus_cog = self.bot.get_cog("Patronus")
        mine = patronus_cog.patronus_of(interaction.user.id) if patronus_cog else None
        if not mine:
            await interaction.response.send_message(
                "You have no patronus to send against it yet - cast one with `/patronus` first.",
                ephemeral=True,
            )
            return

        self.state["active"] = None
        self.save()

        store = self.bot.get_cog("Store")
        house = store.member_house(interaction.user) if store else None
        awarded = 0
        points = CREATURES["dementor"]["points"]
        if store and house:
            store.record(
                house=house,
                delta=points,
                actor_id=self.bot.user.id if self.bot.user else 0,
                target_id=interaction.user.id,
                reason="Banished a Dementor",
            )
            awarded = points

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
        file = self._dementor_defeat_art(embed, interaction.user.id)
        if file:
            await interaction.response.send_message(embed=embed, file=file)
        else:
            await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------- event: /cast

    def _credit_event(self, event: dict, uids: list, creature: dict) -> None:
        tally = event.setdefault("tally", {})
        for uid in uids:
            entry = tally.setdefault(str(uid), {"rep": 0, "kills": 0})
            entry["rep"] += creature["points"]
            entry["kills"] += 1

    def _cast_event(self, interaction: discord.Interaction, spell, event: dict, key: str) -> dict:
        """Resolve a hit during a Wild Threats event while holding the lock.
        Mutates event state and returns a reply dict for delivery *after*
        the lock is released — never talks to Discord itself."""
        wave = event["channels"][key]
        creature_id = wave["creature"]
        creature = CREATURES[creature_id]

        if creature_id == "dementor":
            if spell.value != "patronus":
                return {"content": "Nothing happens.", "ephemeral": True}
            patronus_cog = self.bot.get_cog("Patronus")
            mine = patronus_cog.patronus_of(interaction.user.id) if patronus_cog else None
            if not mine:
                return {
                    "content": (
                        "You have no patronus to send against it yet - "
                        "cast one with `/patronus` first."
                    ),
                    "ephemeral": True,
                }
            del event["channels"][key]
            self._credit_event(event, [interaction.user.id], creature)
            self.save()
            animal = mine["animal"].lower()
            line = self.rng.choice(BANISHED).format(
                patronus=f"{_article(animal)} silver {animal}", animal=animal, member=interaction.user.mention,
            )
            embed = discord.Embed(title="✨ The Dementor is banished", description=line, color=0xC4CCD6)
            embed.set_footer(text=f"+{creature['points']} rep - ⚔️ {event['name']}")
            file = self._dementor_defeat_art(embed, interaction.user.id)
            return {"embed": embed, "file": file}

        if spell.value != creature["weak"]:
            return {"content": "Nothing happens.", "ephemeral": True}

        if interaction.user.id in wave.get("hits", []):
            return {
                "content": "You've already struck this one - it'll take someone else to finish it.",
                "ephemeral": True,
            }

        wave.setdefault("hits", []).append(interaction.user.id)

        if len(wave["hits"]) < creature["pack"]:
            self.save()
            line = self.rng.choice(creature["progress"]).format(member=interaction.user.mention)
            return {"embed": discord.Embed(description=line, color=creature["color"])}

        contributors = wave["hits"]
        del event["channels"][key]
        self._credit_event(event, contributors, creature)
        self.save()

        if creature["pack"] > 1:
            others = ", ".join(f"<@{uid}>" for uid in contributors[:-1]) or "someone else"
            line = self.rng.choice(creature["victory"]).format(member=interaction.user.mention, other=others)
        else:
            line = self.rng.choice(creature["victory"]).format(member=interaction.user.mention)

        embed = discord.Embed(title=f"✨ The {creature['name']} is defeated", description=line, color=creature["color"])
        embed.set_footer(text=f"+{creature['points']} rep each - ⚔️ {event['name']}")
        file = self._attach_art(embed, creature_id, field="defeat_image")
        return {"embed": embed, "file": file}

    async def _deliver_cast_reply(self, interaction: discord.Interaction, reply: dict) -> None:
        """Send a /cast reply prepared under the lock (no lock held here)."""
        kwargs = {}
        if "content" in reply:
            kwargs["content"] = reply["content"]
        if "embed" in reply:
            kwargs["embed"] = reply["embed"]
        if reply.get("file"):
            kwargs["file"] = reply["file"]
        if reply.get("ephemeral"):
            kwargs["ephemeral"] = True
        await interaction.response.send_message(**kwargs)

    # ------------------------------------------------------- event: spawns

    def _event_channel_ids(self) -> list[int]:
        """Channels Attack waves may flood. Study hall is summon/practice only."""
        out = []
        for raw in self.state.get("channel_ids", []):
            cid = self._as_channel_id(raw)
            if cid is None or cid == STUDY_HALL_CHANNEL_ID:
                continue
            out.append(cid)
        return out

    def _prepare_wave(self, event: dict) -> list[dict]:
        """Roll the next wave under the lock.

        Standing creatures stay castable until each room's new post is
        registered in `_deliver_wave` (avoids `/cast` seeing empty air
        while Discord is still posting the wave).
        """
        payloads = []
        for cid in self._event_channel_ids():
            creature_id = self._roll_creature()
            embed = self.embed_arrival(creature_id)
            embed.set_footer(text=f"⚔️ {event['name']}")
            file = self._attach_art(embed, creature_id)
            payloads.append({
                "channel_id": cid,
                "creature_id": creature_id,
                "embed": embed,
                "file": file,
            })
        return payloads

    async def _deliver_wave(self, started_at: float, payloads: list[dict]) -> None:
        """Post wave messages; register each channel as soon as it lands.

        Writing state only after every channel finished left a race where a
        Dementor was visible in Discord but `/cast` (and auto-pick) said
        nothing was there.
        """
        # Drop the previous wave once delivery starts, then fill rooms in.
        async with self.lock:
            event = self.state.get("event")
            if not event or event.get("started_at") != started_at:
                return
            event["channels"] = {}
            self.save()

        for p in payloads:
            channel = await self._get_channel(p["channel_id"])
            if channel is None:
                continue
            try:
                msg = (await channel.send(embed=p["embed"], file=p["file"]) if p["file"]
                       else await channel.send(embed=p["embed"]))
            except discord.HTTPException:
                continue
            async with self.lock:
                event = self.state.get("event")
                if not event or event.get("started_at") != started_at:
                    return
                event.setdefault("channels", {})[str(p["channel_id"])] = {
                    "creature": p["creature_id"],
                    "message_id": msg.id,
                    "spawned_at": time.time(),
                    "hits": [],
                }
                self.save()

    def _clear_event(self) -> dict | None:
        """Must be called while holding self.lock. Pops the running event
        (if any) out of state and returns it, so the caller can finalize it
        - awarding points, posting the recap - without holding the lock for
        all of that."""
        event = self.state.get("event")
        self.state["event"] = None
        if event:
            self.save()
        return event

    async def _finalize_event(self, event: dict) -> None:
        tally = event.get("tally", {})
        store = self.bot.get_cog("Store")
        guild = self.bot.get_guild(event["guild_id"]) if event.get("guild_id") else None
        from cogs.store import HOUSES

        rows = []
        for uid_str, entry in tally.items():
            uid = int(uid_str)
            member = guild.get_member(uid) if guild else None
            house = store.member_house(member) if (store and member) else None
            rows.append([uid, entry.get("rep", 0), entry.get("kills", 0), house])
        rows.sort(key=lambda r: r[1], reverse=True)

        top_rep = rows[0][1] if rows else 0
        mvp_uids = {uid for uid, rep, kills, house in rows if rep == top_rep and rep > 0}

        house_totals: dict[str, int] = {}
        for uid, rep, kills, house in rows:
            if not (store and house):
                continue
            bonus = EVENT_MVP_BONUS if uid in mvp_uids else 0
            store.record(
                house=house, delta=rep + bonus,
                actor_id=self.bot.user.id if self.bot.user else 0,
                target_id=uid, reason=f"{event['name']} - final tally",
            )
            house_totals[house] = house_totals.get(house, 0) + rep + bonus

        total_kills = sum(r[2] for r in rows)
        lines = [f"**{total_kills}** monster(s) put down by **{len(rows)}** wizard(s)."]
        if rows:
            lines.append("")
            for uid, rep, kills, house in rows[:10]:
                crown = "👑 " if uid in mvp_uids else ""
                bonus_note = f" (+{EVENT_MVP_BONUS} MVP bonus)" if uid in mvp_uids else ""
                house_note = f" - House {HOUSES[house]['name']}" if house else " - no house, no points"
                lines.append(f"{crown}<@{uid}>: **{rep}** rep, {kills} kill(s){house_note}{bonus_note}")
        if house_totals:
            lines.append("")
            lines.append(" • ".join(
                f"House {HOUSES[h]['name']}: +{p}"
                for h, p in sorted(house_totals.items(), key=lambda kv: -kv[1])
            ))

        embed = discord.Embed(
            title=f"🏳️ {event['name']} has ended",
            description="\n".join(lines) if rows else "Nobody landed a hit. The grounds are quiet again.",
            color=EVENT_COLOR,
        )
        for cid in self._event_channel_ids():
            channel = self.bot.get_channel(cid)
            if channel is None:
                continue
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                continue

    @tasks.loop(seconds=EVENT_WAVE_SECONDS)
    async def event_tick(self):
        finished = None
        wave = None  # (started_at, payloads)
        async with self.lock:
            event = self.state.get("event")
            if not event:
                return
            if time.time() >= event["ends_at"]:
                finished = self._clear_event()
            else:
                started_at = event["started_at"]
                payloads = self._prepare_wave(event)
                wave = (started_at, payloads)
        if finished:
            await self._finalize_event(finished)
        elif wave:
            await self._deliver_wave(wave[0], wave[1])

    @event_tick.before_loop
    async def before_event_tick(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ staff

    async def _staff(self, interaction) -> bool:
        store = self.bot.get_cog("Store")
        if store and store.is_staff(interaction.user):
            return True
        await interaction.response.send_message("That's for staff.", ephemeral=True)
        return False

    @group.command(name="channels", description="(staff) Set the 4 channels a threat can appear in.")
    @app_commands.describe(a="First channel", b="Second channel", c="Third channel", d="Fourth channel")
    async def channels(self, interaction: discord.Interaction, a: discord.TextChannel,
                        b: discord.TextChannel, c: discord.TextChannel, d: discord.TextChannel):
        if not await self._staff(interaction):
            return
        ids = [a.id, b.id, c.id, d.id]
        if STUDY_HALL_CHANNEL_ID in ids:
            await interaction.response.send_message(
                f"Study hall (<#{STUDY_HALL_CHANNEL_ID}>) is practice-only — use "
                "`/staff dementor summon` there. Pick four other channels for ambient spawns "
                "and Attack waves.",
                ephemeral=True,
            )
            return
        self.state["channel_ids"] = ids
        self.save()
        await interaction.response.send_message(
            f"Wild Threats may now appear in {a.mention}, {b.mention}, {c.mention}, {d.mention}. "
            f"Study hall (<#{STUDY_HALL_CHANNEL_ID}>) stays summon-only.",
            ephemeral=True,
        )

    def _summon_channel_ok(self, channel_id: int) -> bool:
        """Configured event channels, or study hall for practice summons."""
        if channel_id == STUDY_HALL_CHANNEL_ID:
            return True
        return channel_id in self.state.get("channel_ids", [])

    @group.command(name="summon", description="(staff) Make one appear right now.")
    @app_commands.describe(
        channel="Where (blank = random event channel; study hall is practice-only)",
        creature="Which one (leave blank for a Dementor)",
    )
    @app_commands.choices(creature=CREATURE_CHOICES)
    async def summon(self, interaction: discord.Interaction, channel: discord.TextChannel = None,
                      creature: app_commands.Choice[str] = None):
        if not await self._staff(interaction):
            return
        async with self.lock:
            event = self.state.get("event")
            # Study hall stays open for practice summons during an Attack;
            # event waves never target it.
            if event and (not channel or channel.id != STUDY_HALL_CHANNEL_ID):
                await interaction.response.send_message(
                    f"**{event['name']}** is running right now - summon in the study hall "
                    f"(<#{STUDY_HALL_CHANNEL_ID}>) for practice, or `/staff dementor eventend` first.",
                    ephemeral=True,
                )
                return
            if self.state.get("active"):
                await interaction.response.send_message(
                    "One's already loose somewhere. Let it get dealt with first.", ephemeral=True)
                return
            if channel and not self._summon_channel_ok(channel.id):
                await interaction.response.send_message(
                    "Pick one of the four `/staff dementor channels`, or the study hall "
                    f"(<#{STUDY_HALL_CHANNEL_ID}>) for practice.",
                    ephemeral=True,
                )
                return
            self._roll_day()
            landed = await self.spawn(channel.id if channel else None,
                                       creature.value if creature else None)
        if not landed:
            await interaction.response.send_message(
                "No channels are configured yet - run `/staff dementor channels` first.", ephemeral=True)
            return
        name = CREATURES[creature.value]["name"] if creature else "Dementor"
        await interaction.response.send_message(f"Summoned a {name} in {landed.mention}.", ephemeral=True)

    @group.command(name="status", description="(staff) What's configured and what's active.")
    async def status(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        self._roll_day()
        pool = self.state.get("channel_ids", [])
        lines = [
            f"Event channels: {', '.join(f'<#{c}>' for c in pool) if pool else 'none set'}",
            f"Practice summon: <#{STUDY_HALL_CHANNEL_ID}> (never used by Attack waves)",
            f"Spawned today: {self.state.get('spawns_today', 0)}/{SPAWNS_PER_DAY}",
        ]
        active = self.state.get("active")
        if active:
            age = int((time.time() - active["spawned_at"]) / 60)
            c = CREATURES.get(active["creature"], {"name": active["creature"]})
            hits = len(active.get("hits", []))
            need = CREATURES.get(active["creature"], {}).get("pack", 1)
            progress = f", {hits}/{need} hit(s) landed" if need > 1 else ""
            lines.append(f"Active now: {c['name']} in <#{active['channel_id']}>, {age} minute(s) ago{progress}.")
            until = active.get("exclusive_until")
            finder = active.get("finder_id")
            if finder and until and time.time() < until:
                lines.append(f"Exclusive cast: <@{finder}> until <t:{int(until)}:R>.")
        else:
            lines.append("Nothing active right now.")
        if self.state.get("event"):
            lines.append(f"⚔️ An event is running - see `/staff dementor eventstatus`.")
        embed = discord.Embed(title="Wild Threats", description="\n".join(lines), color=DARK)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------------- staff: event

    @group.command(name="eventstart", description="(staff) Start 'Attack on Velmora' - monsters flood every channel.")
    @app_commands.describe(minutes=f"How long it runs, in minutes (default {EVENT_DEFAULT_MINUTES})",
                            name="What to call it")
    async def eventstart(self, interaction: discord.Interaction,
                          minutes: int = EVENT_DEFAULT_MINUTES, name: str = "Attack on Velmora"):
        if not await self._staff(interaction):
            return
        err = None
        start = None  # (started_at, payloads, clean_name, minutes, intro, pool)
        async with self.lock:
            if self.state.get("event"):
                err = f"**{self.state['event']['name']}** is already running."
            else:
                pool = self._event_channel_ids()
                if not pool:
                    err = "No channels configured yet - run `/staff dementor channels` first."
                elif not (1 <= minutes <= EVENT_MAX_MINUTES):
                    err = f"Pick a length between 1 and {EVENT_MAX_MINUTES} minutes."
                else:
                    clean_name = name.strip() or "Attack on Velmora"
                    now = time.time()
                    # Clear encounter only if it's in an event channel; leave
                    # a study-hall practice summon alone.
                    active = self.state.get("active")
                    if not (active and active.get("channel_id") == STUDY_HALL_CHANNEL_ID):
                        self.state["active"] = None
                    event = {
                        "name": clean_name, "started_at": now, "ends_at": now + minutes * 60,
                        "guild_id": interaction.guild_id, "channels": {}, "tally": {},
                    }
                    self.state["event"] = event
                    self.save()
                    intro = discord.Embed(
                        title=f"⚔️ {clean_name}",
                        description=self.rng.choice(ATTACK_INTRO) +
                                    f"\n\nWaves keep coming for the next {minutes} minute(s).",
                        color=EVENT_COLOR,
                    )
                    payloads = self._prepare_wave(event)
                    start = (event["started_at"], payloads, clean_name, minutes, intro, pool)

        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        started_at, payloads, clean_name, minutes, intro, pool = start
        await interaction.response.send_message(
            f"**{clean_name}** begins now, for {minutes} minute(s).", ephemeral=True)

        for cid in pool:
            channel = await self._get_channel(cid)
            if channel is None:
                continue
            try:
                await channel.send(embed=intro)
            except discord.HTTPException:
                continue

        await self._deliver_wave(started_at, payloads)

    @group.command(name="eventend", description="(staff) End the running event early and tally it up.")
    async def eventend(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        async with self.lock:
            event = self._clear_event()
        if not event:
            await interaction.response.send_message("Nothing is running right now.", ephemeral=True)
            return
        await interaction.response.send_message(f"**{event['name']}** ended early.", ephemeral=True)
        await self._finalize_event(event)

    @group.command(name="eventstatus", description="(staff) How the current event is going.")
    async def eventstatus(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        event = self.state.get("event")
        if not event:
            await interaction.response.send_message("Nothing running right now.", ephemeral=True)
            return
        remaining = max(0, int(event["ends_at"] - time.time()))
        mins, secs = divmod(remaining, 60)
        tally = sorted(event.get("tally", {}).items(), key=lambda kv: -kv[1].get("rep", 0))
        lines = [
            f"**{event['name']}** - {mins}m {secs}s left.",
            f"{len(event.get('channels', {}))} monster(s) out right now.",
        ]
        if tally:
            lines.append("")
            lines += [f"<@{uid}>: **{e['rep']}** rep, {e['kills']} kill(s)" for uid, e in tally[:10]]
        else:
            lines.append("Nobody's landed a hit yet.")
        embed = discord.Embed(title="⚔️ Event status", description="\n".join(lines), color=EVENT_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Dementors(bot))
