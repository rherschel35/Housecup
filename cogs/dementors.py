"""
Wild Threats. Something slips into one of the social channels on its own,
a few times a day - and a headmaster can always summon one on the spot.

    /cast
        - anyone, opens spell buttons against whatever's here
        - an explore monster that already shows spell buttons refuses this
          and says to use the buttons on the post
        - CAST_AUTO_USER_ID auto-picks the right spell (no buttons)
    /staff dementor channels          - set the channels they can appear in
    /staff dementor summon [channel] [creature] - make one appear right now
        (study hall + shared games lounge + swarm practice room are
         practice summons; Attack waves never go there)
    /staff dementor status            - what's configured and what's active

Every creature has exactly one spell that actually works on it, and the
post never says which - that's the whole game. The spell that works is
CREATURES[id]["weak"] (patronus, hex, ward, disarm, bind, or mirror).
A Dementor is still only driven out by an already-cast Patronus, same
as always.

A monster stirred by /explore puts those spells on the public arrival
post as green buttons, the way a Quidditch sign-up sits on its message.
Everyone in the channel can see and press them. They are shuffled every
time they are drawn, so the working spell is not always in the same
spot. Discord allows 5 buttons per row and 25 total; the six spells use
two rows. A wrong button locks that player out of this monster and says so out
loud: a random teasing line names them, the spell, and the creature.
Someone else can still try. During the finder's 25 seconds, anyone
else's click gets a private note and is not a lockout. A player who is
already locked out gets a private note too, not another public miss.
The buttons disable when the monster is defeated or slips away. /cast
does not fight these. It replies in private: use the spell buttons on
the monster post. /cast is unchanged for forage, staff summons, ambient
sightings, and Attack waves, including its own spell menu. A wrong
/cast there still does nothing and does not lock anyone out.

Most creatures fall to a single clean hit, and that result posts right
away. A couple don't: they take two hits from two DIFFERENT people before
they're actually gone, so a lone wizard can weaken one but can't finish it
alone. Everyone who lands the right hit shares in the reward once it's down.

A Storm Sprite is rarer and skittish - leave it too long and it bolts to
another configured channel rather than wait to be caught.

When /explore or /forage in a dangerous place stirs one up, that student
gets 25 seconds alone to /cast at it. The arrival post counts that window
down. On a two-person creature, the first clean hit removes that countdown
and opens the fight — the second hit resolves it immediately (rewards and
the result post) instead of waiting out the clock. If that second hit never
comes, the creature stays for 2 minutes counted from that first cast, then
slips away with no reward. The finish clock does not start at spawn, so a
finder who casts near the end of their 25 seconds still leaves everyone the
full two minutes. A hit that lands only after the exclusive window does not
start this clock; that creature stays until someone beats it. A one-person
creature is unchanged: the winning cast resolves it immediately, and the
countdown comes off the arrival post. With nobody having landed a hit, the
25 seconds still end the finder's exclusive turn and then anyone can try;
that creature stays until it's beaten (or a Storm Sprite wanders). Scheduled
sightings and staff summons stay open to everyone from the start and have
no countdown.

A monster stirred by /explore still pays its usual house points in full,
every time — the daily explore cap does not reduce that payout. Those
same points also count toward the checklist line "Explore points" (3,
resets midnight America/Chicago). Progress on that line stops at 3, so
once it shows complete it stays complete while later kills keep paying
in full. The checklist credit is not a second payout. Forage, scheduled
sightings, staff summons, and Attack swarms do not touch that line.

    /staff dementor eventstart [minutes] [name]  - start "Attack on Velmora"
    /staff dementor practice [channel] [minutes] - house practice swarm
        (one channel — house rooms, study hall, or swarm practice room;
         minutes 1–240, not locked to Attack's 5/10; no points/rewards;
         staff or Presidents)
    /staff dementor eventend                     - end it early
    /staff dementor eventstatus                  - how it's going

For a limited time, a fresh wave of monsters floods ALL configured threat
channels every 23 seconds - whatever was still standing gets swept aside
for the new wave. Staff pick a **5-minute** or **10-minute** swarm:

  - 5 minutes: top **7** by kills share a **100**-point pool
  - 10 minutes: top **10** by kills share a **300**-point pool

Each scorer gets floor((their kills ÷ total swarm kills) × pool). The end
board lists **everyone** who scored a kill (share only pays the top cut).
The house with the most fighters who registered a kill gets a turnout
bonus. First place is most kills, then most rep; that MVP gets +5. If
kills and rep are both tied at the top, each of those wizards gets +2.
One big scoreboard reveal at the end in every channel that fought.

Staff can schedule **1–3** Attacks a week, each with its own Chicago day,
time, and **5- or 10-minute** length. Five minutes before each, the bot
warns in the shared event announce channel with Champions / WIZARDS AND
WITCHES role pings; it pings again there when the swarm starts. Monster
waves still flood the threat channels. When the swarm ends, the full
scoreboard posts on the announce board and each wave channel just notes
that the event is over.

House practice floods a single house channel the same way, but awards
nothing at the end — just drills for the real Attack.

Event casts and wave posts release the shared lock before talking to
Discord, so a busy Attack doesn't freeze every other /cast behind one
slow image upload.
"""

import asyncio
import datetime as dt
import json
import logging
import os
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from zoneinfo import ZoneInfo
    CHICAGO = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover - Windows without tzdata
    CHICAGO = dt.timezone.utc

from cogs.velmora_channels import (
    EVENT_ANNOUNCE_CHANNEL_ID,
    EVERYTHING_LOUNGE_CHANNEL_IDS,
    RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS,
    SHARED_GAMES_CHANNEL_ID,
    STUDY_HALL_CHANNEL_ID,
    SWARM_PRACTICE_CHANNEL_IDS,
)
from cogs.point_share import kill_share_awards
from cogs.world_engine import today

# Summon/practice rooms — Attack waves never flood these (except everything
# lounges, which opt into full threat traffic below).
PRACTICE_SUMMON_CHANNEL_IDS = frozenset({
    STUDY_HALL_CHANNEL_ID,
    SHARED_GAMES_CHANNEL_ID,
    *SWARM_PRACTICE_CHANNEL_IDS,
    *EVERYTHING_LOUNGE_CHANNEL_IDS,
})

# Practice rooms + restricted open lounges: no ambient / Attack floods.
# Everything lounges stay open to threats on purpose.
NO_THREAT_SPAWN_CHANNEL_IDS = frozenset({
    *(PRACTICE_SUMMON_CHANNEL_IDS - EVERYTHING_LOUNGE_CHANNEL_IDS),
    *RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS,
})

# Always included in ambient spawns + Attack waves (alongside staff channels).
EXTRA_THREAT_CHANNEL_IDS = frozenset({
    1552403823769952266,
})

# Restricted open lounges stay spawn-free even for house practice.
# Study hall / games lounge / swarm practice / everything lounges may host
# practice swarms on purpose.
PRACTICE_BLOCKED_CHANNEL_IDS = frozenset({
    *RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS,
})

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
# Two-person explore threats: after the first clean hit, this is how long
# the fight stays open. Counted from that cast, not from the spawn.
PARTIAL_FINISH_SECONDS = 120

REWARD_POINTS = 3   # kept for backward compatibility; see CREATURES["dementor"]["points"]
DARK = 0x0B0B12
THREAT_ALERT_COLOR = 0xC0392B  # every "X has appeared" alert, regardless of creature, so it never blends in

EVENT_WAVE_SECONDS = 23
EVENT_DEFAULT_MINUTES = 5
# House practice can override length (not locked to Attack's 5/10 presets).
# Cap high enough for multi-hour drills; Attack still uses EVENT_PRESETS only.
EVENT_MAX_MINUTES = 240
EVENT_MVP_BONUS = 5
# When two+ wizards share both the top kill count and the same top rep.
EVENT_KILL_REP_TIE_BONUS = 2
EVENT_KILL_POOL = 100  # legacy base; presets set kill_pool explicitly
EVENT_HOUSE_TURNOUT_BONUS = 10  # house with the most fighters who registered a kill
# length minutes → kill-share cut size and absolute pool
EVENT_PRESETS = {
    5: {"reward_top": 7, "kill_pool": 100, "label": "5-minute"},
    10: {"reward_top": 10, "kill_pool": 300, "label": "10-minute"},
}
EVENT_COLOR = 0x8A2F2F
EVENT_WARN_MINUTES = 5
ATTACK_SCHEDULE_MAX_DAYS = 3
# Role names pinged on warn + start (case-insensitive). Override with env:
# ATTACK_PING_ROLE_NAMES=Champions,WIZARDS AND WITCHES
_DEFAULT_PING_NAMES = ("Champions", "WIZARDS AND WITCHES")
WEEKDAY_CHOICES = [
    app_commands.Choice(name="Monday", value=0),
    app_commands.Choice(name="Tuesday", value=1),
    app_commands.Choice(name="Wednesday", value=2),
    app_commands.Choice(name="Thursday", value=3),
    app_commands.Choice(name="Friday", value=4),
    app_commands.Choice(name="Saturday", value=5),
    app_commands.Choice(name="Sunday", value=6),
]
WEEKDAY_LABELS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)
ATTACK_LENGTH_CHOICES = [
    app_commands.Choice(name="5 min", value=5),
    app_commands.Choice(name="10 min", value=10),
]


def _preset_kill_pool(preset: dict) -> int:
    if "kill_pool" in preset:
        return int(preset["kill_pool"])
    # Older in-memory shape used point_mult × EVENT_KILL_POOL.
    return int(EVENT_KILL_POOL * int(preset.get("point_mult") or 1))


def _event_kill_pool(event: dict) -> int:
    if event.get("kill_pool") is not None:
        return int(event["kill_pool"])
    return int(EVENT_KILL_POOL * int(event.get("point_mult") or 1))


def _chicago_now(ts: float | None = None) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts if ts is not None else time.time(), CHICAGO)


def _attack_ping_role_names() -> list[str]:
    raw = os.getenv("ATTACK_PING_ROLE_NAMES", "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return list(_DEFAULT_PING_NAMES)


def format_kill_footer(
    results: list[tuple[int, str | None]],
    *,
    points_word: bool = False,
) -> str:
    """Defeat footer from (awarded, house_key) rows.

    ``points_word`` keeps the Dementor line ("+3 points for House …").
    Other creatures stay "+3 for House …".
    """
    from cogs.store import HOUSES
    label = "points for House" if points_word else "for House"
    parts: list[str] = []
    for awarded, house in results:
        if awarded and house in HOUSES:
            parts.append(f"+{awarded} {label} {HOUSES[house]['name']}")
    return " • ".join(parts)


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
#   When the creature was stirred by /explore, this same full amount also
#   counts toward the Explore points checklist, clamped at 3 for that line.
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

# Discord components: 5 buttons on a row, 25 components on a message.
BUTTONS_PER_ROW = 5
MAX_SPELL_BUTTONS = 25
THREAT_SPELL_PREFIX = "threatspell:"
LOCKED_OUT_MESSAGE = "You're already out for this one."
USE_SPELL_BUTTONS_MESSAGE = "Use the spell buttons on the monster post."

# Public misses. {member}, {spell}, and {creature} are filled in when one
# is picked. Generic lines always qualify; spell and creature lines are
# added when they match, then one is chosen at random.
WRONG_SPELL_LINES = [
    "{member} cast {spell} at the {creature}. The {creature} is now mildly offended and {member} is banned from this fight.",
    "{member}'s {spell} fizzles out like a wet sparkler. The {creature} laughs. Out you go.",
    "{member} offers the {creature} a {spell}. The {creature} declines, politely, and {member} is out for this one.",
    "A brave little {spell} from {member} bonks the {creature} and apologizes. {member} doesn't get another go.",
    "The {creature} watches {member}'s {spell} wander off to think about its life. That's a miss. {member} is benched.",
    "{member} casts {spell} with a flourish. The {creature} applauds the effort and shows {member} the door.",
    "{spell} pops, fizzes, and becomes a small embarrassed cloud. The {creature} is fine. {member} is done here.",
    "{member}'s {spell} trips over its own wording. The {creature} helps it up, then bans {member} from the rest of the fight.",
    "Not {spell}, {member}. The {creature} has standards, and also a very firm no. Out for this one.",
    "{member} and their {spell} have a moment. The {creature} does not join them. {member} is out.",
    "The {creature} files {member}'s {spell} under nice try and closes the folder. No second attempt.",
    "{member}'s {spell} takes a bow. The {creature} does not clap. {member} is out for this one.",
]
WRONG_SPELL_BY_SPELL = {
    "hex": [
        "{member}'s {spell} doodles a tiny mustache on the {creature}. The {creature} is not taking fashion notes, and {member} is out.",
        "{member} aims {spell} at the air beside the {creature}. The air is annoyed. The {creature} is fine. {member} is benched.",
    ],
    "ward": [
        "{member} puts up a {spell}. The {creature} walks around it, impressed by the carpentry, and {member} is still out.",
        "{member}'s {spell} is a lovely shield. The {creature} was not swinging just then. {member} doesn't get a redo.",
    ],
    "disarm": [
        "{member} tries to {spell} the {creature}. It wasn't holding anything. Awkward. {member} is out for this one.",
        "{member}'s {spell} tugs at a sleeve the {creature} does not have. {member} is shown the bench.",
    ],
    "bind": [
        "{member}'s {spell} ties a neat bow on the {creature}, and the bow falls off. {member} is out.",
        "The {creature} steps out of {member}'s {spell} like a pair of loose shoes. No second try for {member}.",
    ],
    "mirror": [
        "{member}'s {spell} shows the {creature} its good side. It already knew. {member} is out.",
        "The {creature} waves at the reflection from {member}'s {spell} and ignores {member}. Banned from this fight.",
    ],
    "patronus": [
        "{member}'s {spell} arrives as a silver puff and gets stage fright. The {creature} waits. {member} is out for this one.",
        "A very small {spell} from {member} takes one look at the {creature} and remembers an appointment. {member} is benched.",
    ],
}
WRONG_SPELL_BY_CREATURE = {
    "stone_golem": [
        "{member}'s {spell} bounces off the {creature} and goes to live in the countryside. {member} is out.",
        "The {creature} shrugs, and a pebble lands on {member}'s {spell}. That's a no. {member} is done here.",
    ],
    "nightweaver": [
        "{member}'s {spell} gets gift-wrapped in spare silk by the {creature}. {member} is out for this one.",
        "The {creature} claps several hands for {member}'s {spell}, then shows {member} the exit.",
    ],
    "shadow_wolf": [
        "The {creature} wags its tail at {member}'s {spell} and does not budge. {member} is benched.",
        "{member} casts {spell}. The {creature} sneezes a little shadow and looks pleased. {member} is out.",
    ],
    "wisp_swarm": [
        "The {creature} flickers in time with {member}'s {spell}, then keeps floating. {member} doesn't get another try.",
        "{member}'s {spell} blows through the {creature} like a polite breeze. {member} is out for this one.",
    ],
    "storm_sprite": [
        "The {creature} giggles, sparks once, and lets {member}'s {spell} miss. {member} is out.",
        "{member}'s {spell} tickles the {creature}. It is delighted and still very much here. {member} is benched.",
    ],
    "dementor": [
        "{member}'s {spell} makes the {creature}'s cloak flutter, which it rather enjoys. {member} is out for this one.",
        "The {creature} gives {member}'s {spell} a slow, unimpressed hood-tilt. {member} is benched.",
    ],
}

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


def _unknown_interaction(exc: BaseException) -> bool:
    """Discord 10062: the click arrived after the interaction token expired."""
    return isinstance(exc, discord.NotFound) and getattr(exc, "code", None) == 10062


class CastSpellButton(discord.ui.Button):
    def __init__(self, spell_value: str):
        label = SPELL_LABEL.get(spell_value, spell_value.title())
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=f"cast:{spell_value}")
        self.spell_value = spell_value

    async def callback(self, interaction: discord.Interaction):
        view: CastSpellView = self.view  # type: ignore[assignment]
        if interaction.user.id != view.owner_id:
            try:
                await interaction.response.send_message("Those aren't your spells.", ephemeral=True)
            except discord.NotFound as exc:
                if _unknown_interaction(exc):
                    log.debug("Spell button skipped; interaction expired.")
                    return
                raise
            return
        view.stop()
        for item in view.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=view)
        except discord.DiscordException:
            pass
        try:
            await view.cog.resolve_cast(interaction, SpellPick(self.spell_value))
        except discord.NotFound as exc:
            if _unknown_interaction(exc):
                # Token already dead (view timed out, or Discord gave up). The
                # cast path raises here only on the reply, so a hit that was
                # already saved stays saved.
                log.debug(
                    "Spell button skipped; interaction expired (spell=%s).",
                    self.spell_value,
                )
                return
            raise


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

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        if _unknown_interaction(error):
            log.debug(
                "Spell button skipped; interaction expired (spell=%s).",
                getattr(item, "spell_value", None),
            )
            return
        await super().on_error(interaction, error, item)


def shuffled_spell_order(rng) -> list[str]:
    """Copy of SPELL_ORDER in a new order. The working spell moves with it."""
    order = list(SPELL_ORDER)
    rng.shuffle(order)
    return order


def wrong_spell_choices(spell_value: str, creature_id: str) -> list[str]:
    """Generic misses, plus any lines written for this spell or creature."""
    lines = list(WRONG_SPELL_LINES)
    lines.extend(WRONG_SPELL_BY_SPELL.get(spell_value) or [])
    lines.extend(WRONG_SPELL_BY_CREATURE.get(creature_id) or [])
    return lines


def render_wrong_spell(line: str, member: str, spell: str, creature: str) -> str:
    return line.format(member=member, spell=spell, creature=creature)


def wrong_spell_announcement(rng, user, spell_value: str, creature_id: str) -> str:
    """One public, good-natured miss. Names the player, the spell, and the creature."""
    creature = CREATURES.get(creature_id) or {}
    member = getattr(user, "mention", None) or getattr(user, "display_name", None) or "Someone"
    spell = SPELL_LABEL.get(spell_value, str(spell_value).replace("_", " ").title())
    name = creature.get("name") or "creature"
    line = rng.choice(wrong_spell_choices(spell_value, creature_id))
    return render_wrong_spell(line, member, spell, name)


def spell_button_slots(order: list[str]) -> list[tuple[str, int]]:
    """(spell, row) capped at 5 buttons per row and 25 total."""
    slots = []
    for index, spell in enumerate(order[:MAX_SPELL_BUTTONS]):
        slots.append((spell, index // BUTTONS_PER_ROW))
    return slots


def _spell_order_from_message(msg) -> list[str]:
    """Spell order already drawn on a message, so a disable keeps that layout."""
    order: list[str] = []
    for row in getattr(msg, "components", None) or []:
        for child in getattr(row, "children", None) or []:
            custom_id = getattr(child, "custom_id", None) or ""
            if custom_id.startswith(THREAT_SPELL_PREFIX):
                order.append(custom_id[len(THREAT_SPELL_PREFIX):])
    if order:
        return order
    view = getattr(msg, "view", None)
    if view is not None:
        for child in getattr(view, "children", []):
            spell = getattr(child, "spell_value", None)
            if spell:
                order.append(spell)
    return order


class ThreatSpellButton(discord.ui.Button):
    """Green spell on the public monster post. Anyone in the channel can press it."""

    def __init__(self, spell_value: str, *, row: int, disabled: bool = False):
        label = SPELL_LABEL.get(spell_value, spell_value.title())
        super().__init__(
            label=label,
            style=discord.ButtonStyle.success,
            custom_id=f"{THREAT_SPELL_PREFIX}{spell_value}",
            row=row,
            disabled=disabled,
        )
        self.spell_value = spell_value

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Dementors")
        if cog is None:
            try:
                await interaction.response.send_message(
                    "Wild Threats isn't available right now.",
                    ephemeral=True,
                )
            except discord.NotFound as exc:
                if _unknown_interaction(exc):
                    return
                raise
            return
        try:
            await cog.on_public_spell_click(interaction, self.spell_value)
        except discord.NotFound as exc:
            if _unknown_interaction(exc):
                log.debug(
                    "Threat spell button skipped; interaction expired (spell=%s).",
                    self.spell_value,
                )
                return
            raise


class ThreatSpellView(discord.ui.View):
    """Public spell row under an explore monster. No timeout: the fight outlives 3 minutes."""

    def __init__(self, order: list[str], *, disabled: bool = False):
        super().__init__(timeout=None)
        for spell, row in spell_button_slots(order):
            self.add_item(ThreatSpellButton(spell, row=row, disabled=disabled))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        if _unknown_interaction(error):
            log.debug(
                "Threat spell button skipped; interaction expired (spell=%s).",
                getattr(item, "spell_value", None),
            )
            return
        await super().on_error(interaction, error, item)


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def exclusive_countdown_note(finder_id: int, exclusive_until: float) -> str:
    """Visible exclusive-window line on an explore/forage arrival post."""
    return (
        f"\n\n⏳ <@{finder_id}> stirred it up — they have until "
        f"<t:{int(exclusive_until)}:R> to `/cast` first. "
        f"Then anyone can try."
    )


def strip_exclusive_countdown(description: str | None) -> str:
    """Drop the exclusive-window countdown, leaving the arrival text."""
    if not description:
        return ""
    for marker in ("\n\n⏳ ", "\n⏳ "):
        idx = description.find(marker)
        if idx != -1:
            return description[:idx].rstrip()
    return description


def partial_pack_should_expire(active: dict | None, now: float) -> bool:
    """A multi-person fight with a hit landed, still short of a kill, whose
    finish deadline has passed.

    That deadline is PARTIAL_FINISH_SECONDS after the first hit, and only
    when that hit landed inside the finder's exclusive window. Zero hits do
    not expire: the window only ends the finder's turn, and the creature
    stays until someone beats it. A first hit that lands after the window
    never sets partial_expires_at, so those stay too.
    """
    if not active:
        return False
    try:
        deadline = float(active.get("partial_expires_at") or 0)
    except (TypeError, ValueError):
        return False
    if deadline <= 0 or now < deadline:
        return False
    hits = active.get("hits") or []
    if not hits:
        return False
    creature = CREATURES.get(active.get("creature") or "")
    if not creature:
        return False
    try:
        pack = int(creature.get("pack") or 1)
    except (TypeError, ValueError):
        pack = 1
    return len(hits) < pack


class Dementors(commands.Cog):
    # Nested under /staff after load (see staff_groups.nest_pure_staff_groups).
    group = app_commands.Group(
        name="dementor",
        description="Wild Threats.",
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
        self.state.setdefault("attack_schedule", self._blank_attack_schedule())
        self._partial_expiry_task: asyncio.Task | None = None

    @staticmethod
    def _blank_attack_schedule() -> dict:
        return {
            "enabled": False,
            # Each slot: {weekday, hour, minute, minutes} — minutes is 5 or 10
            "slots": [],
            "minutes": 10,  # legacy fallback when a slot omits minutes
            "role_ids": [],
            "last_warn_key": None,
            "last_start_key": None,
        }

    def _attack_schedule(self) -> dict:
        sched = self.state.setdefault("attack_schedule", self._blank_attack_schedule())
        for key, value in self._blank_attack_schedule().items():
            sched.setdefault(key, value)
        # Migrate legacy shared-time shape (weekdays + one hour/minute).
        if not sched.get("slots") and sched.get("weekdays"):
            hour = int(sched.get("hour", 20))
            minute = int(sched.get("minute", 0))
            legacy_mins = int(sched.get("minutes") or 10)
            if legacy_mins not in EVENT_PRESETS:
                legacy_mins = 10
            sched["slots"] = [
                {
                    "weekday": int(d),
                    "hour": hour,
                    "minute": minute,
                    "minutes": legacy_mins,
                }
                for d in sched.get("weekdays") or []
                if 0 <= int(d) <= 6
            ]
            self.save()
        return sched

    @staticmethod
    def _normalize_schedule_slots(
        raw_slots: list,
        default_minutes: int = 10,
    ) -> list[dict]:
        """Deduplicate by weekday (first wins); keep up to 3. Skips empty slots."""
        fallback = default_minutes if default_minutes in EVENT_PRESETS else 10
        out: list[dict] = []
        seen: set[int] = set()
        for raw in raw_slots:
            if not raw:
                continue
            try:
                day = int(raw["weekday"])
                hour = int(raw["hour"])
                minute = int(raw.get("minute", 0))
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= day <= 6 and 0 <= hour <= 23 and 0 <= minute <= 59):
                continue
            if day in seen:
                continue
            seen.add(day)
            try:
                mins = int(raw.get("minutes", fallback))
            except (TypeError, ValueError):
                mins = fallback
            if mins not in EVENT_PRESETS:
                mins = fallback
            out.append({
                "weekday": day,
                "hour": hour,
                "minute": minute,
                "minutes": mins,
            })
            if len(out) >= ATTACK_SCHEDULE_MAX_DAYS:
                break
        return out

    def _slot_attack_minutes(self, slot: dict | None = None) -> int:
        if slot is not None:
            try:
                minutes = int(slot.get("minutes"))
            except (TypeError, ValueError):
                minutes = 0
            if minutes in EVENT_PRESETS:
                return minutes
        minutes = int(self._attack_schedule().get("minutes") or 10)
        return minutes if minutes in EVENT_PRESETS else 10

    def _scheduled_attack_minutes(self) -> int:
        """Legacy helper — prefers the next slot's length, else schedule default."""
        nxt = self._next_attack_slot()
        if nxt is not None:
            return self._slot_attack_minutes(nxt[2])
        return self._slot_attack_minutes(None)

    async def cog_load(self):
        self.lock = asyncio.Lock()
        self.tick.start()
        self.event_tick.start()
        self.attack_schedule_tick.start()
        # A restart mid-fight should still close a half-finished two-person
        # creature when its finish deadline has passed.
        self._arm_partial_expiry()
        # Public explore-monster buttons. The message itself carries the
        # shuffled layout; this listener matches the custom ids after a restart.
        self.bot.add_view(ThreatSpellView(list(SPELL_ORDER)))

    async def cog_unload(self):
        self._cancel_partial_expiry()
        self.tick.cancel()
        self.event_tick.cancel()
        self.attack_schedule_tick.cancel()

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
            desc += exclusive_countdown_note(finder_id, exclusive_until)
        return discord.Embed(
            title=f"{c['emoji']} A {c['name']} has appeared",
            description=desc,
            color=THREAT_ALERT_COLOR,
        )

    def _finder_blocks(self, active: dict, user_id: int) -> str | None:
        """Ephemeral refusal while the explorer who stirred it up still has
        exclusive cast rights; None once the window is open or there was no finder.

        A landed hit opens a two-person fight immediately. The exclusive
        timestamp stays on the encounter; the finish window is separate."""
        if active.get("hits"):
            return None
        until = active.get("exclusive_until")
        finder = active.get("finder_id")
        if not until or not finder:
            return None
        if time.time() >= until or user_id == finder:
            return None
        return (f"<@{finder}> stirred this one up — they have until "
                f"<t:{int(until)}:R> to cast at it. After that, anyone can try.")

    async def spawn(self, channel_id: int = None, creature_id: str = None,
                    finder_id: int | None = None, *,
                    pay_explore_points: bool = False) -> discord.TextChannel | None:
        """Make a creature appear. Picks a random configured channel if none
        is given, and a random creature (weighted) if none is given - except
        a manual, unspecified /staff dementor summon still defaults to a Dementor,
        same as it always has. When finder_id is set (explore/forage stir),
        that student gets FINDER_EXCLUSIVE_SECONDS alone to /cast.
        pay_explore_points is only for a threat stirred by /explore: the
        defeat still pays full house points, and those points also count
        on the Explore points checklist. Returns the channel it landed in,
        or None."""
        pool = self._configured_channel_ids()
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
        # Only /explore. Forage, ambient sightings, staff summons, and
        # Attack waves keep the /cast menu and do not get this public row.
        spell_buttons = bool(pay_explore_points)
        view = ThreatSpellView(shuffled_spell_order(self.rng)) if spell_buttons else None
        try:
            send_kwargs: dict = {"embed": embed}
            if file:
                send_kwargs["file"] = file
            if view is not None:
                send_kwargs["view"] = view
            msg = await channel.send(**send_kwargs)
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
        if pay_explore_points:
            # Stays set if the creature wanders, so the checklist credit
            # still applies after a Storm Sprite bolts.
            active["pay_explore_points"] = True
        if spell_buttons:
            active["spell_buttons"] = True
            active["locked_out"] = []
        self._cancel_partial_expiry()
        self.state["active"] = active
        self.state["spawns_today"] = self.state.get("spawns_today", 0) + 1
        self.save()
        log.info("A %s appeared in channel %s (finder=%s)", creature_id, channel_id, finder_id)
        return channel

    async def try_ambient_spawn(self, channel_id: int, creature_id: str = None,
                                finder_id: int | None = None, *,
                                pay_explore_points: bool = False) -> bool:
        """Let another cog (right now: searching the Dungeons or the
        Forbidden Woods) try to drop a Wild Threat into a specific channel
        on the spot. Refuses quietly - no error, just nothing happens - if
        something's already loose or an event is running, so it never
        steals the encounter out from under another channel. finder_id is
        the explorer who stirred it — they get a short exclusive cast window.
        pay_explore_points counts the eventual kill on the Explore points
        checklist. /explore sets it; /forage does not. The house-point
        payout is the full creature amount either way."""
        async with self.lock:
            if self.state.get("active") or self.state.get("event"):
                return False
            # Unlike a manual /staff dementor summon, an ambient trigger like this
            # should draw from the whole weighted roster, not default to a
            # plain Dementor.
            landed = await self.spawn(channel_id, creature_id or self._roll_creature(),
                                      finder_id=finder_id,
                                      pay_explore_points=pay_explore_points)
        return bool(landed)

    def _roll_creature(self) -> str:
        keys = list(SPAWN_WEIGHTS.keys())
        weights = [SPAWN_WEIGHTS[k] for k in keys]
        return self.rng.choices(keys, weights=weights, k=1)[0]

    async def _wander(self, active: dict) -> None:
        """Relocate a wandering creature to a different configured channel."""
        c = CREATURES[active["creature"]]
        pool = [cid for cid in self._configured_channel_ids() if cid != active["channel_id"]]
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
        spell_buttons = bool(active.get("spell_buttons"))
        view = ThreatSpellView(shuffled_spell_order(self.rng)) if spell_buttons else None
        old_channel_id = active["channel_id"]
        old_message_id = active.get("message_id")
        try:
            send_kwargs: dict = {"embed": embed}
            if file:
                send_kwargs["file"] = file
            if view is not None:
                send_kwargs["view"] = view
            msg = await new_channel.send(**send_kwargs)
        except discord.HTTPException:
            return
        active["channel_id"] = new_id
        active["message_id"] = msg.id
        active["spawned_at"] = time.time()
        active["hits"] = []
        # New channel, open season — the explorer's exclusive window doesn't travel.
        # pay_explore_points stays with the creature so the checklist still counts it.
        # A wrong-spell lockout stays too: it is the same monster.
        active.pop("finder_id", None)
        active.pop("exclusive_until", None)
        active.pop("partial_expires_at", None)
        self._cancel_partial_expiry()
        self.save()
        if spell_buttons and old_message_id:
            await self._disable_threat_buttons(old_channel_id, old_message_id)

    @tasks.loop(minutes=TICK_MINUTES)
    async def tick(self):
        async with self.lock:
            self._roll_day()
            if self.state.get("event"):
                return  # an event is running - it owns spawning right now
            active = self.state.get("active")
            if active:
                # Restart safety: the sleep task is gone, so the 5-minute tick
                # still closes a half-finished two-person fight past its deadline.
                if partial_pack_should_expire(active, time.time()):
                    creature_id = active["creature"]
                    channel_id = active["channel_id"]
                    button_message = self._button_message(active)
                    self._cancel_partial_expiry()
                    self.state["active"] = None
                    self.save()
                    await self._announce_partial_slip(channel_id, creature_id, button_message)
                    return
                c = CREATURES.get(active["creature"], {})
                if c.get("wander") and time.time() - active["spawned_at"] >= WANDER_MINUTES * 60:
                    await self._wander(active)
                return
            if self.state.get("spawns_today", 0) >= SPAWNS_PER_DAY:
                return
            if not self._configured_channel_ids():
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
        if CAST_AUTO_USER_ID is not None and user_id == CAST_AUTO_USER_ID:
            return True
        potions = self.bot.get_cog("Potions")
        if potions and potions.has_liquid_luck(user_id):
            return True
        castles = self.bot.get_cog("Castles")
        return bool(castles and castles.surestroke_auto_cast(user_id))

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

    def _button_message(self, active: dict | None) -> tuple[int, int] | None:
        """Arrival post that is showing public spell buttons, if any."""
        if not active or not active.get("spell_buttons"):
            return None
        channel_id = active.get("channel_id")
        message_id = active.get("message_id")
        if not channel_id or not message_id:
            return None
        return channel_id, message_id

    def _buttons_match(self, active: dict | None, channel_id, message_id) -> bool:
        if not active or not active.get("spell_buttons"):
            return False
        if active.get("message_id") != message_id:
            return False
        return self._as_channel_id(active.get("channel_id")) == self._as_channel_id(channel_id)

    def _cast_locked_out(self, channel_id, user_id: int) -> bool:
        """True when this player already hit the wrong public spell here.

        An Attack wave in this channel is a different fight, so the solo
        lockout does not follow them into it.
        """
        event = self.state.get("event")
        key = str(self._as_channel_id(channel_id) or channel_id)
        if event and key in (event.get("channels") or {}):
            return False
        active = self.state.get("active")
        if not self._buttons_match(active, channel_id, active.get("message_id") if active else None):
            return False
        return user_id in (active.get("locked_out") or [])

    def _cast_blocked_by_buttons(self, channel_id) -> bool:
        """Explore monsters with public spell buttons are not fought with /cast.

        An Attack wave in this channel is a different fight, so /cast stays
        available for that wave.
        """
        event = self.state.get("event")
        key = str(self._as_channel_id(channel_id) or channel_id)
        if event and key in (event.get("channels") or {}):
            return False
        active = self.state.get("active")
        if not active or not active.get("spell_buttons"):
            return False
        return self._as_channel_id(active.get("channel_id")) == self._as_channel_id(channel_id)

    async def _disable_threat_buttons(self, channel_id, message_id) -> None:
        """Grey out the public spell row. The monster is gone from this post."""
        if not channel_id or not message_id:
            return
        channel = self.bot.get_channel(channel_id) if self.bot else None
        if channel is None and self.bot is not None:
            fetch_channel = getattr(self.bot, "fetch_channel", None)
            if fetch_channel is None:
                return
            try:
                channel = await fetch_channel(channel_id)
            except discord.HTTPException:
                return
        if channel is None:
            return
        fetch_message = getattr(channel, "fetch_message", None)
        if fetch_message is None:
            return
        try:
            msg = await fetch_message(message_id)
        except discord.HTTPException:
            return
        except AttributeError:
            return
        order = _spell_order_from_message(msg) or list(SPELL_ORDER)
        view = ThreatSpellView(order, disabled=True)
        edit = getattr(msg, "edit", None)
        if edit is None:
            return
        try:
            await edit(view=view)
        except discord.HTTPException:
            log.info("Could not disable threat spell buttons on message %s", message_id)
        except AttributeError:
            return

    async def on_public_spell_click(self, interaction: discord.Interaction, spell_value: str) -> None:
        """Resolve one press of a green spell on the monster post.

        A wrong spell is announced in the channel and locks that player out.
        "Not yet" and "already out" stay ephemeral. A clean hit uses /cast's
        public result.
        """
        if self.lock is None:
            await interaction.response.send_message(
                "Wild Threats is still starting up — try again in a second.",
                ephemeral=True,
            )
            return
        message = getattr(interaction, "message", None)
        message_id = getattr(message, "id", None)
        stale = False
        private = None
        public_miss = None
        cast = False
        async with self.lock:
            active = self.state.get("active")
            if not self._buttons_match(active, interaction.channel_id, message_id):
                stale = True
            else:
                blocked = self._finder_blocks(active, interaction.user.id)
                if blocked:
                    private = blocked
                elif interaction.user.id in (active.get("locked_out") or []):
                    private = LOCKED_OUT_MESSAGE
                elif spell_value != CREATURES[active["creature"]]["weak"]:
                    locked = active.setdefault("locked_out", [])
                    if interaction.user.id not in locked:
                        locked.append(interaction.user.id)
                        self.save()
                    public_miss = wrong_spell_announcement(
                        self.rng, interaction.user, spell_value, active["creature"],
                    )
                else:
                    cast = True
        if stale:
            await self._disable_threat_buttons(interaction.channel_id, message_id)
            await interaction.response.send_message(
                self._nothing_here_message(interaction.channel_id),
                ephemeral=True,
            )
            return
        if private is not None:
            await interaction.response.send_message(private, ephemeral=True)
            return
        if public_miss is not None:
            await interaction.response.send_message(public_miss)
            return
        if cast:
            await self.resolve_cast(interaction, SpellPick(spell_value))

    @app_commands.command(name="cast", description="Cast a spell at whatever's in this channel.")
    async def cast(self, interaction: discord.Interaction):
        """Spell menu for fights that do not already show public spell buttons."""
        hexes = self.bot.get_cog("Hexes")
        if hexes and await hexes.deny_if_limp_wand(interaction):
            return

        channel_id = interaction.channel_id
        if self._cast_blocked_by_buttons(channel_id):
            await interaction.response.send_message(USE_SPELL_BUTTONS_MESSAGE, ephemeral=True)
            return
        if self._cast_locked_out(channel_id, interaction.user.id):
            await interaction.response.send_message(LOCKED_OUT_MESSAGE, ephemeral=True)
            return
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

    async def _award_kill_points(
        self, member, amount: int, reason: str, *, explore: bool,
    ) -> tuple[int, str | None]:
        """Pay a wild-threat kill its full house points. Returns (awarded, house).

        A creature stirred by /explore also adds those points to the Explore
        points checklist, clamped at the daily 3. That credit is not a second
        house-point payout, and a finished checklist does not reduce the kill.
        """
        amount = int(amount)
        if member is None or amount <= 0:
            return 0, None
        store = self.bot.get_cog("Store")
        house = store.member_house(member) if store else None
        if not (store and house):
            return 0, None
        store.record(
            house=house,
            delta=amount,
            actor_id=self.bot.user.id if self.bot.user else 0,
            target_id=member.id,
            reason=reason,
        )
        if explore:
            await self._count_explore_kill(member, amount)
        return amount, house

    async def _count_explore_kill(self, member, amount: int) -> None:
        """Add an explore-stirred kill to the checklist counter. No payout."""
        world_cog = self.bot.get_cog("World")
        if world_cog is None:
            log.warning("Explore kill checklist not updated; World cog is not loaded")
            return
        async with world_cog.lock:
            student = world_cog.student(member)
            world_cog.world.note_explore_kill(student, amount)
            world_cog.save()

    def _note_partial_deadline(self, active: dict, now: float | None = None) -> None:
        """Open a finish window once a multi-person fight has a hit but isn't
        finished. The window is PARTIAL_FINISH_SECONDS from this first hit, so
        players get the full two minutes even when the exclusive window is
        almost over. Later hits do not move it. A hit that lands after the
        exclusive window does not start this clock — that creature stays."""
        if active.get("partial_expires_at"):
            return
        until = active.get("exclusive_until")
        if not until:
            return
        now = time.time() if now is None else now
        try:
            until_f = float(until)
        except (TypeError, ValueError):
            return
        # The exclusive timestamp only decides whether this hit still counts
        # as inside the finder's turn. It is not the slip-away deadline.
        if now >= until_f:
            return
        creature = CREATURES.get(active.get("creature") or "")
        try:
            pack = int(creature.get("pack") or 1) if creature else 1
        except (TypeError, ValueError):
            pack = 1
        hits = active.get("hits") or []
        if pack <= 1 or not hits or len(hits) >= pack:
            return
        active["partial_expires_at"] = now + PARTIAL_FINISH_SECONDS

    def _cancel_partial_expiry(self) -> None:
        task = getattr(self, "_partial_expiry_task", None)
        self._partial_expiry_task = None
        if task is not None and not task.done():
            task.cancel()

    def _arm_partial_expiry(self) -> None:
        """Sleep until the hidden deadline, then close a still-unfinished fight."""
        self._cancel_partial_expiry()
        active = self.state.get("active") or {}
        try:
            deadline = float(active.get("partial_expires_at") or 0)
        except (TypeError, ValueError):
            return
        if deadline <= 0:
            return
        hits = active.get("hits") or []
        creature = CREATURES.get(active.get("creature") or "")
        try:
            pack = int(creature.get("pack") or 1) if creature else 1
        except (TypeError, ValueError):
            pack = 1
        if not hits or len(hits) >= pack:
            return
        message_id = active.get("message_id")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._partial_expiry_task = loop.create_task(
            self._partial_expiry_wait(message_id, deadline)
        )

    async def _partial_expiry_wait(self, message_id, deadline: float) -> None:
        delay = deadline - time.time()
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        wait_ready = getattr(self.bot, "wait_until_ready", None)
        if wait_ready is not None:
            try:
                await wait_ready()
            except Exception:
                return
        if self.lock is None:
            return
        async with self.lock:
            active = self.state.get("active")
            if not active or active.get("message_id") != message_id:
                return
            # Sleep can wake a hair early; the deadline itself still counts.
            if not partial_pack_should_expire(active, max(time.time(), deadline)):
                return
            creature_id = active["creature"]
            channel_id = active["channel_id"]
            button_message = self._button_message(active)
            self.state["active"] = None
            self.save()
        await self._announce_partial_slip(channel_id, creature_id, button_message)

    async def _announce_partial_slip(
        self, channel_id: int, creature_id: str, button_message: tuple | None = None,
    ) -> None:
        """No rewards. The finish window ran out with the fight unfinished."""
        if button_message:
            await self._disable_threat_buttons(*button_message)
        c = CREATURES.get(creature_id) or {}
        name = c.get("name") or "creature"
        emoji = c.get("emoji") or ""
        channel = await self._get_channel(channel_id)
        if channel is None:
            return
        text = f"{emoji} The {name} slips away. One spell wasn't enough, and nobody finished it in time."
        embed = discord.Embed(description=text.strip(), color=0x7A7A7A)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.info("Could not post the slip-away for %s in %s", creature_id, channel_id)

    async def _clear_visible_countdown(self, active: dict) -> None:
        """Edit the arrival post so the exclusive-window countdown is gone."""
        channel_id = active.get("channel_id")
        message_id = active.get("message_id")
        if not channel_id or not message_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return
        if channel is None or not hasattr(channel, "fetch_message"):
            return
        try:
            msg = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return
        embeds = getattr(msg, "embeds", None)
        if not embeds:
            return
        embed = embeds[0]
        desc = embed.description or ""
        stripped = strip_exclusive_countdown(desc)
        if stripped == desc:
            return
        new_embed = embed.copy()
        new_embed.description = stripped
        # Keep the creature portrait. Passing [] would strip attachments.
        edit_kwargs: dict = {"embed": new_embed}
        existing = getattr(msg, "attachments", None)
        if existing:
            edit_kwargs["attachments"] = list(existing)
        try:
            await msg.edit(**edit_kwargs)
        except discord.HTTPException:
            log.info("Could not clear the threat countdown on message %s", message_id)

    async def resolve_cast(self, interaction: discord.Interaction, spell: SpellPick):
        """Resolve a cast once the spell is known (auto-pick or button)."""
        event_reply = None
        explore_pay = False
        spawn_ref = None
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
                if interaction.user.id in (active.get("locked_out") or []):
                    await interaction.response.send_message(LOCKED_OUT_MESSAGE, ephemeral=True)
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
                    # First hit on a two-person explore creature: hide the
                    # countdown and open a 2-minute finish window from this
                    # cast. Anyone else can finish it now; if they don't, it
                    # slips away when that window ends.
                    self._note_partial_deadline(active)
                    self.save()
                    self._arm_partial_expiry()
                    await self._clear_visible_countdown(active)
                    line = self.rng.choice(creature["progress"]).format(member=interaction.user.mention)
                    await interaction.response.send_message(
                        embed=discord.Embed(description=line, color=creature["color"])
                    )
                    return

                # Defeated - award every contributor and clear the encounter.
                # One-person creatures land here on their only hit, so they
                # still resolve immediately instead of waiting out the clock.
                explore_pay = bool(active.get("pay_explore_points"))
                spawn_ref = {
                    "channel_id": active.get("channel_id"),
                    "message_id": active.get("message_id"),
                    "spell_buttons": bool(active.get("spell_buttons")),
                }
                self._cancel_partial_expiry()
                self.state["active"] = None
                self.save()

        if event_reply is not None:
            await self._deliver_cast_reply(interaction, event_reply)
            return

        if spawn_ref:
            await self._clear_visible_countdown(spawn_ref)
            if spawn_ref.get("spell_buttons"):
                await self._disable_threat_buttons(
                    spawn_ref.get("channel_id"), spawn_ref.get("message_id"),
                )

        contributors = active["hits"]
        credited = []
        for uid in contributors:
            member = interaction.guild.get_member(uid) if interaction.guild else None
            awarded, house = await self._award_kill_points(
                member, creature["points"], f"Defeated a {creature['name']}", explore=explore_pay,
            )
            credited.append((awarded, house))

        if creature["pack"] > 1:
            others = ", ".join(f"<@{uid}>" for uid in contributors[:-1]) or "someone else"
            line = self.rng.choice(creature["victory"]).format(member=interaction.user.mention, other=others)
        else:
            line = self.rng.choice(creature["victory"]).format(member=interaction.user.mention)

        embed = discord.Embed(title=f"✨ The {creature['name']} is defeated", description=line, color=creature["color"])
        footer = format_kill_footer(credited)
        if footer:
            embed.set_footer(text=footer)
        file = self._attach_art(embed, creature_id, field="defeat_image")
        if file:
            await interaction.response.send_message(embed=embed, file=file)
        else:
            await interaction.response.send_message(embed=embed)

    async def _cast_dementor(self, interaction, spell, active):
        """Dementor flow: needs an already-cast patronus, resolves in a single
        hit, uses its own flavour text. An explore-stirred one also counts
        toward the Explore points checklist."""
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

        explore_pay = bool(active.get("pay_explore_points"))
        spawn_ref = {
            "channel_id": active.get("channel_id"),
            "message_id": active.get("message_id"),
            "spell_buttons": bool(active.get("spell_buttons")),
        }
        self._cancel_partial_expiry()
        self.state["active"] = None
        self.save()
        await self._clear_visible_countdown(spawn_ref)
        if spawn_ref.get("spell_buttons"):
            await self._disable_threat_buttons(
                spawn_ref.get("channel_id"), spawn_ref.get("message_id"),
            )

        points = CREATURES["dementor"]["points"]
        awarded, house = await self._award_kill_points(
            interaction.user, points, "Banished a Dementor", explore=explore_pay,
        )

        animal = mine["animal"].lower()
        line = self.rng.choice(BANISHED).format(
            patronus=f"{_article(animal)} silver {animal}",
            animal=animal,
            member=interaction.user.mention,
        )
        embed = discord.Embed(title="✨ The Dementor is banished", description=line, color=0xC4CCD6)
        footer = format_kill_footer([(awarded, house)], points_word=True)
        if footer:
            embed.set_footer(text=footer)
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
            if event.get("practice"):
                embed.set_footer(text=f"Practice · no points · ⚔️ {event['name']}")
            else:
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
        if event.get("practice"):
            embed.set_footer(text=f"Practice · no points · ⚔️ {event['name']}")
        else:
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

    def _configured_channel_ids(self) -> list[int]:
        """Staff-set channels plus hard-coded extras. No-spawn rooms stay out."""
        seen: set[int] = set()
        out: list[int] = []
        for raw in list(self.state.get("channel_ids", [])) + list(EXTRA_THREAT_CHANNEL_IDS):
            cid = self._as_channel_id(raw)
            if cid is None or cid in NO_THREAT_SPAWN_CHANNEL_IDS or cid in seen:
                continue
            seen.add(cid)
            out.append(cid)
        return out

    def _event_channel_ids(self, event: dict | None = None) -> list[int]:
        """Channels the current Attack (or practice) wave may flood."""
        event = event if event is not None else self.state.get("event")
        if event and event.get("channel_ids"):
            out: list[int] = []
            for raw in event["channel_ids"]:
                cid = self._as_channel_id(raw)
                if cid is not None:
                    out.append(cid)
            return out
        return self._configured_channel_ids()

    def _prepare_wave(self, event: dict) -> list[dict]:
        """Roll the next wave under the lock.

        Standing creatures stay castable until each room's new post is
        registered in `_deliver_wave` (avoids `/cast` seeing empty air
        while Discord is still posting the wave).
        """
        payloads = []
        practice = bool(event.get("practice"))
        for cid in self._event_channel_ids(event):
            creature_id = self._roll_creature()
            embed = self.embed_arrival(creature_id)
            footer = f"⚔️ {event['name']}"
            if practice:
                footer = f"Practice · no points · {footer}"
            embed.set_footer(text=footer)
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
        practice = bool(event.get("practice"))

        actor = self.bot.user.id if self.bot.user else 0
        reward_top = int(event.get("reward_top") or EVENT_PRESETS[EVENT_DEFAULT_MINUTES]["reward_top"])
        minutes = int(event.get("minutes") or EVENT_DEFAULT_MINUTES)

        rows = []
        for uid_str, entry in tally.items():
            uid = int(uid_str)
            member = guild.get_member(uid) if guild else None
            house = store.member_house(member) if (store and member) else None
            rows.append({
                "uid": uid,
                "rep": int(entry.get("rep", 0) or 0),
                "kills": int(entry.get("kills", 0) or 0),
                "house": house,
            })
        # Kill board: most kills first; kill ties broken by most rep.
        by_kills = sorted(
            [r for r in rows if r["kills"] > 0],
            key=lambda r: (-r["kills"], -r["rep"], r["uid"]),
        )
        # MVP / first-place bonus among the kill leaders.
        # Sole (or clear rep) leader → +5. Full kills+rep dead heat → +2 each.
        mvp_uids: set[int] = set()
        mvp_bonus = EVENT_MVP_BONUS
        if by_kills:
            max_kills = by_kills[0]["kills"]
            leaders = [r for r in by_kills if r["kills"] == max_kills]
            best_rep = max(r["rep"] for r in leaders)
            tied = [r for r in leaders if r["rep"] == best_rep]
            mvp_uids = {r["uid"] for r in tied}
            if len(tied) > 1:
                mvp_bonus = EVENT_KILL_REP_TIE_BONUS

        total_kills = sum(r["kills"] for r in rows)
        # Who cashes kill-share: top 7 on 5-min, top 10 on 10-min.
        kill_cut = reward_top
        kill_pool = _event_kill_pool(event)
        kill_share = kill_share_awards(
            by_kills,
            pool=kill_pool,
            top_n=kill_cut,
            total_kills=total_kills,
        )
        row_by_uid = {r["uid"]: r for r in rows}

        # House with the most distinct fighters who registered a kill.
        turnout: dict[str, set[int]] = {}
        for r in rows:
            if r["kills"] > 0 and r["house"]:
                turnout.setdefault(r["house"], set()).add(r["uid"])
        turnout_counts = {h: len(uids) for h, uids in turnout.items()}
        best_turnout = max(turnout_counts.values()) if turnout_counts else 0
        turnout_houses = {
            h for h, n in turnout_counts.items() if n == best_turnout and best_turnout > 0
        }

        house_totals: dict[str, int] = {}

        def _award(house: str, delta: int, target_id: int | None, reason: str) -> None:
            if not (store and house and delta):
                return
            store.record(
                house=house, delta=delta, actor_id=actor,
                target_id=target_id or 0, reason=reason,
            )
            house_totals[house] = house_totals.get(house, 0) + delta

        if not practice:
            # Kill share among the cut — main individual payout.
            for uid, pts in kill_share.items():
                row = row_by_uid[uid]
                if row["house"]:
                    _award(
                        row["house"], pts, uid,
                        f"{event['name']} - kill share (top {kill_cut})",
                    )

            # First place: most kills, then most rep (+5). Dead heat → +2 each.
            mvp_reason = (
                f"{event['name']} - kill/rep tie"
                if mvp_bonus == EVENT_KILL_REP_TIE_BONUS
                else f"{event['name']} - MVP"
            )
            for uid in mvp_uids:
                row = row_by_uid[uid]
                if row["house"] and mvp_bonus:
                    _award(
                        row["house"], mvp_bonus, uid,
                        mvp_reason,
                    )

            # Turnout bonus — most houses with a registered kill.
            for house in turnout_houses:
                _award(
                    house, EVENT_HOUSE_TURNOUT_BONUS, None,
                    f"{event['name']} - house turnout",
                )

        if practice:
            lines = [
                f"**Practice swarm** — **{total_kills}** monster(s) put down by "
                f"**{len(rows)}** wizard(s). **No points awarded.**",
            ]
            if by_kills:
                lines.append("")
                lines.append("Everyone with a kill:")
                for place, r in enumerate(by_kills, start=1):
                    crown = "👑 " if r["uid"] in mvp_uids else ""
                    house_note = (
                        f" - House {HOUSES[r['house']]['name']}" if r["house"] else ""
                    )
                    lines.append(
                        f"{crown}**#{place}** <@{r['uid']}>: {r['kills']} kill(s), "
                        f"{r['rep']} rep{house_note}"
                    )
        else:
            lines = [
                f"**{total_kills}** monster(s) put down by **{len(by_kills)}** wizard(s) "
                f"with a kill ({minutes}-minute swarm · **{kill_pool}**-pt pool)."
            ]

            board_lines = []
            # Dense rank: same kills+rep → same place number.
            place = 0
            prev_key = None
            for idx, r in enumerate(by_kills):
                key = (r["kills"], r["rep"])
                if key != prev_key:
                    place = idx + 1
                    prev_key = key
                pts = kill_share.get(r["uid"], 0)
                pct = (100.0 * r["kills"] / total_kills) if total_kills else 0.0
                bits = []
                if r["uid"] in mvp_uids:
                    label = "tie" if mvp_bonus == EVENT_KILL_REP_TIE_BONUS else "MVP"
                    bits.append(f"+{mvp_bonus} {label}")
                bonus_note = f" ({', '.join(bits)})" if bits else ""
                crown = "👑 " if r["uid"] in mvp_uids else ""
                house_note = (
                    f" - House {HOUSES[r['house']]['name']}" if r["house"]
                    else " - no house, no points"
                )
                if idx < kill_cut:
                    board_lines.append(
                        f"{crown}**#{place}** <@{r['uid']}>: {r['kills']} kill(s) "
                        f"({pct:.0f}% of swarm) → **+{pts}**{house_note}{bonus_note}"
                    )
                else:
                    board_lines.append(
                        f"**#{place}** <@{r['uid']}>: {r['kills']} kill(s) "
                        f"({pct:.0f}% of swarm){house_note}"
                    )
            if board_lines:
                lines.append("")
                lines.append(
                    f"Top **{kill_cut}** by kills share **{kill_pool}** pts "
                    f"(kills ÷ total swarm kills × {kill_pool}; kill ties → most rep). "
                    f"Full kill board:"
                )
                lines.extend(board_lines)
            elif rows:
                lines.append("Nobody scored a kill — no points awarded.")

            if turnout_houses and best_turnout:
                names = ", ".join(HOUSES[h]["name"] for h in sorted(turnout_houses))
                lines.append("")
                lines.append(
                    f"🏠 Turnout: House {names} "
                    f"({best_turnout} fighter(s) with a kill) +{EVENT_HOUSE_TURNOUT_BONUS}"
                )

        if house_totals:
            lines.append("")
            lines.append(" • ".join(
                f"House {HOUSES[h]['name']}: +{p}"
                for h, p in sorted(house_totals.items(), key=lambda kv: -kv[1])
            ))

        title = (
            f"🏳️ Practice ended — {event['name']}"
            if practice else f"🏳️ {event['name']} has ended"
        )
        empty_note = (
            "Nobody landed a hit. Good drill anyway — no points either way."
            if practice else
            "Nobody landed a hit. The grounds are quiet again."
        )
        chunks = self._embed_desc_chunks(lines) if rows else [empty_note]

        async def _send_scoreboard(channel_ids: list[int]) -> None:
            for cid in channel_ids:
                channel = self.bot.get_channel(cid)
                if channel is None:
                    try:
                        channel = await self._get_channel(cid)
                    except Exception:
                        channel = None
                if channel is None:
                    continue
                for i, chunk in enumerate(chunks):
                    embed = discord.Embed(
                        title=title if i == 0 else f"{title} (cont.)",
                        description=chunk,
                        color=EVENT_COLOR,
                    )
                    try:
                        await channel.send(embed=embed)
                    except discord.HTTPException:
                        break

        if practice:
            # House practice stays local — full board in the practice room only.
            await _send_scoreboard(self._event_channel_ids(event))
            return

        # Live Attack: full results on the shared announce board; wave rooms
        # only get a short "ended" ping so the fight channels stay clean.
        announce_id = self._attack_announce_channel_id()
        await _send_scoreboard([announce_id])
        ended = discord.Embed(
            title=title,
            description=(
                f"The swarm is over. Full results are in <#{announce_id}>."
            ),
            color=EVENT_COLOR,
        )
        for cid in self._event_channel_ids(event):
            if cid == announce_id:
                continue
            channel = self.bot.get_channel(cid)
            if channel is None:
                continue
            try:
                await channel.send(embed=ended)
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

    @staticmethod
    def _embed_desc_chunks(lines: list[str], *, limit: int = 3900) -> list[str]:
        """Split scoreboard lines so each Discord embed description fits."""
        if not lines:
            return [""]
        chunks: list[str] = []
        cur: list[str] = []
        size = 0
        for line in lines:
            add = len(line) + (1 if cur else 0)
            if cur and size + add > limit:
                chunks.append("\n".join(cur))
                cur = [line]
                size = len(line)
            else:
                cur.append(line)
                size += add
        if cur:
            chunks.append("\n".join(cur))
        return chunks

    def _resolve_attack_ping_roles(self, guild: discord.Guild | None) -> list[discord.Role]:
        if guild is None:
            return []
        sched = self._attack_schedule()
        roles: list[discord.Role] = []
        seen: set[int] = set()
        for rid in sched.get("role_ids") or []:
            try:
                role = guild.get_role(int(rid))
            except (TypeError, ValueError):
                continue
            if role and role.id not in seen:
                roles.append(role)
                seen.add(role.id)
        # Always also name-match defaults (Champions + WIZARDS AND WITCHES) so a
        # partial role_ids list still picks up the combined community role.
        wanted = {n.lower() for n in _attack_ping_role_names()}
        for role in guild.roles:
            if role.name.lower() in wanted and role.id not in seen:
                roles.append(role)
                seen.add(role.id)
        return roles

    def _attack_ping_content(self, guild: discord.Guild | None) -> str:
        roles = self._resolve_attack_ping_roles(guild)
        if not roles:
            return ""
        return " ".join(r.mention for r in roles)

    def _attack_announce_channel_id(self) -> int:
        raw = os.getenv("EVENT_ANNOUNCE_CHANNEL_ID", "").strip()
        if raw.isdigit():
            return int(raw)
        return EVENT_ANNOUNCE_CHANNEL_ID

    async def _broadcast_attack_notice(
        self,
        *,
        guild: discord.Guild | None,
        embed: discord.Embed,
        ping: bool,
    ) -> None:
        """Post warn/start board copy in the shared event announce channel."""
        content = self._attack_ping_content(guild) if ping else None
        allowed = discord.AllowedMentions(roles=True, users=False, everyone=False)
        channel = await self._get_channel(self._attack_announce_channel_id())
        if channel is None:
            log.warning(
                "Attack announce channel %s not reachable",
                self._attack_announce_channel_id(),
            )
            return
        try:
            await channel.send(
                content=content or None,
                embed=embed,
                allowed_mentions=allowed if content else discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.exception(
                "Could not post Attack notice in %s",
                self._attack_announce_channel_id(),
            )

    async def _begin_attack(
        self,
        *,
        minutes: int,
        name: str,
        guild_id: int | None,
    ) -> tuple[bool, str, tuple | None]:
        """Start a live Attack. Returns (ok, staff_message, start_tuple_or_None)."""
        preset = EVENT_PRESETS.get(minutes)
        if not preset:
            return False, "Pick a 5- or 10-minute Attack.", None
        if self.lock is None:
            return False, "Wild Threats is still starting up — try again in a few seconds.", None

        async with self.lock:
            if self.state.get("event"):
                running = self.state["event"]
                kind = "practice swarm" if running.get("practice") else "Attack"
                return (
                    False,
                    f"**{running['name']}** ({kind}) is already running — "
                    "`/staff dementor eventend` first.",
                    None,
                )
            pool = self._event_channel_ids()
            if not pool:
                return False, "No channels configured yet - run `/staff dementor channels` first.", None

            clean_name = (name or "").strip() or "Attack on Velmora"
            now = time.time()
            active = self.state.get("active")
            if not (
                active
                and active.get("channel_id") in PRACTICE_SUMMON_CHANNEL_IDS
            ):
                button_message = self._button_message(active)
                self._cancel_partial_expiry()
                self.state["active"] = None
                if button_message:
                    await self._disable_threat_buttons(*button_message)
            pool_pts = _preset_kill_pool(preset)
            mult_line = (
                f"Top **{preset['reward_top']}** by kills share **{pool_pts}** pts "
                f"(kills ÷ total swarm kills × {pool_pts})."
            )
            event = {
                "name": clean_name,
                "started_at": now,
                "ends_at": now + minutes * 60,
                "minutes": minutes,
                "reward_top": preset["reward_top"],
                "kill_pool": pool_pts,
                "guild_id": guild_id,
                "channels": {},
                "tally": {},
            }
            self.state["event"] = event
            self.save()
            intro = discord.Embed(
                title=f"⚔️ {clean_name}",
                description=(
                    self.rng.choice(ATTACK_INTRO)
                    + f"\n\n**{preset['label']}** swarm — waves for the next "
                    f"**{minutes}** minutes. {mult_line} "
                    f"House turnout: **+{EVENT_HOUSE_TURNOUT_BONUS}** · "
                    f"MVP (most kills, then most rep): **+{EVENT_MVP_BONUS}** "
                    f"(kill+rep dead heat: **+{EVENT_KILL_REP_TIE_BONUS}** each)."
                ),
                color=EVENT_COLOR,
            )
            payloads = self._prepare_wave(event)
            start = (event["started_at"], payloads, clean_name, minutes, intro, pool, preset)

        return (
            True,
            f"**{clean_name}** begins now — **{preset['label']}** "
            f"(top {preset['reward_top']} share {pool_pts} kill pts).",
            start,
        )

    async def _announce_attack_wave(self, start: tuple, *, ping: bool = False) -> None:
        started_at, payloads, _clean_name, _minutes, intro, pool, _preset = start
        guild = None
        if pool:
            ch0 = self.bot.get_channel(pool[0])
            guild = getattr(ch0, "guild", None) if ch0 else None
            if guild is None and self.bot.guilds:
                guild = self.bot.guilds[0]
        # Role pings + board intro go to the shared announce channel; fight
        # rooms only get the intro (no @mentions) before waves land.
        if ping:
            await self._broadcast_attack_notice(guild=guild, embed=intro, ping=True)
        none_mentions = discord.AllowedMentions.none()
        for cid in pool:
            channel = await self._get_channel(cid)
            if channel is None:
                continue
            try:
                await channel.send(embed=intro, allowed_mentions=none_mentions)
            except discord.HTTPException:
                continue
        await self._deliver_wave(started_at, payloads)

    def _next_attack_slot(
        self, now: dt.datetime | None = None,
    ) -> tuple[dt.datetime, str, dict] | None:
        """Next Chicago slot (warn/start target) from the saved weekly schedule."""
        sched = self._attack_schedule()
        if not sched.get("enabled"):
            return None
        default_mins = self._slot_attack_minutes(None)
        slots = self._normalize_schedule_slots(
            sched.get("slots") or [], default_minutes=default_mins,
        )
        if not slots:
            return None
        by_day = {int(s["weekday"]): s for s in slots}
        now = now or _chicago_now()
        best: dt.datetime | None = None
        best_spec: dict | None = None
        for add in range(0, 8):
            day = (now + dt.timedelta(days=add)).date()
            spec = by_day.get(day.weekday())
            if not spec:
                continue
            candidate = dt.datetime(
                day.year, day.month, day.day,
                int(spec["hour"]), int(spec["minute"]),
                tzinfo=CHICAGO,
            )
            if add == 0 and candidate + dt.timedelta(seconds=120) < now:
                continue
            if best is None or candidate < best:
                best = candidate
                best_spec = spec
        if best is None or best_spec is None:
            return None
        key = best.strftime("%Y-%m-%dT%H:%M")
        return best, key, best_spec

    @tasks.loop(seconds=30)
    async def attack_schedule_tick(self):
        sched = self._attack_schedule()
        if not sched.get("enabled"):
            return
        slot = self._next_attack_slot()
        if slot is None:
            return
        target, key, spec = slot
        now = _chicago_now()
        warn_at = target - dt.timedelta(minutes=EVENT_WARN_MINUTES)

        minutes = self._slot_attack_minutes(spec)
        preset = EVENT_PRESETS[minutes]
        pool_pts = _preset_kill_pool(preset)

        # Five-minute warning.
        if warn_at <= now < target and sched.get("last_warn_key") != key:
            guild = self.bot.guilds[0] if self.bot.guilds else None
            embed = discord.Embed(
                title="⚔️ Attack on Velmora — 5 minutes",
                description=(
                    f"A **{preset['label']}** swarm hits <t:{int(target.timestamp())}:t> "
                    f"(<t:{int(target.timestamp())}:R>). "
                    f"Top **{preset['reward_top']}** by kills share **{pool_pts}** pts. "
                    f"Get ready."
                ),
                color=EVENT_COLOR,
            )
            await self._broadcast_attack_notice(guild=guild, embed=embed, ping=True)
            sched["last_warn_key"] = key
            self.save()
            log.info("Posted Attack warn for slot %s (%s)", key, preset["label"])

        # Start window: on time through +2 minutes (bot was briefly down).
        if target <= now < target + dt.timedelta(seconds=120) and sched.get("last_start_key") != key:
            guild = self.bot.guilds[0] if self.bot.guilds else None
            guild_id = guild.id if guild else None
            ok, msg, start = await self._begin_attack(
                minutes=minutes,
                name="Attack on Velmora",
                guild_id=guild_id,
            )
            sched["last_start_key"] = key
            self.save()
            if ok and start:
                await self._announce_attack_wave(start, ping=True)
                log.info("Scheduled Attack started for slot %s (%s)", key, preset["label"])
            else:
                log.warning("Scheduled Attack did not start for %s: %s", key, msg)

    @attack_schedule_tick.before_loop
    async def before_attack_schedule_tick(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ staff

    async def _staff(self, interaction) -> bool:
        store = self.bot.get_cog("Store")
        if store and store.is_staff(interaction.user):
            return True
        await interaction.response.send_message("That's for staff.", ephemeral=True)
        return False

    @group.command(name="channels", description="Set channels.")
    @app_commands.describe(
        a="Channel", b="Channel", c="Channel", d="Channel",
        e="Optional",
    )
    async def channels(self, interaction: discord.Interaction, a: discord.TextChannel,
                        b: discord.TextChannel, c: discord.TextChannel, d: discord.TextChannel,
                        e: discord.TextChannel = None):
        if not await self._staff(interaction):
            return
        ids = [a.id, b.id, c.id, d.id]
        if e is not None:
            ids.append(e.id)
        # Keep uniqueness while preserving order.
        seen: set[int] = set()
        unique: list[int] = []
        for cid in ids:
            if cid not in seen:
                seen.add(cid)
                unique.append(cid)
        ids = unique
        blocked = [cid for cid in ids if cid in NO_THREAT_SPAWN_CHANNEL_IDS]
        if blocked:
            mentions = ", ".join(f"<#{cid}>" for cid in blocked)
            await interaction.response.send_message(
                f"{mentions} "
                f"{'is' if len(blocked) == 1 else 'are'} off-limits for Wild Threat "
                "spawns (study hall / games lounge / open lounges). Pick other channels.",
                ephemeral=True,
            )
            return
        self.state["channel_ids"] = ids
        self.save()
        mentions = ", ".join(f"<#{cid}>" for cid in ids)
        extras = [cid for cid in sorted(EXTRA_THREAT_CHANNEL_IDS) if cid not in ids]
        extra_note = (
            f" Always-on: {', '.join(f'<#{cid}>' for cid in extras)}."
            if extras else ""
        )
        practice_mentions = ", ".join(
            f"<#{cid}>" for cid in sorted(PRACTICE_SUMMON_CHANNEL_IDS)
        )
        await interaction.response.send_message(
            f"Wild Threats may now appear in {mentions}.{extra_note} "
            f"{practice_mentions} stay summon-only; open lounges stay spawn-free.",
            ephemeral=True,
        )

    def _summon_channel_ok(self, channel_id: int) -> bool:
        """Configured event channels, extras, or practice summon rooms."""
        if channel_id in EVERYTHING_LOUNGE_CHANNEL_IDS:
            return True
        if channel_id in RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS:
            return False
        if channel_id in PRACTICE_SUMMON_CHANNEL_IDS:
            return True
        return channel_id in self._configured_channel_ids()

    @group.command(name="summon", description="Summon now.")
    @app_commands.describe(
        channel="Channel",
        creature="Creature",
    )
    @app_commands.choices(creature=CREATURE_CHOICES)
    async def summon(self, interaction: discord.Interaction, channel: discord.TextChannel = None,
                      creature: app_commands.Choice[str] = None):
        if not await self._staff(interaction):
            return
        async with self.lock:
            event = self.state.get("event")
            # Practice rooms stay open for summons during an Attack;
            # event waves never target them.
            if event and (not channel or channel.id not in PRACTICE_SUMMON_CHANNEL_IDS):
                practice = ", ".join(
                    f"<#{cid}>" for cid in sorted(PRACTICE_SUMMON_CHANNEL_IDS)
                )
                await interaction.response.send_message(
                    f"**{event['name']}** is running right now - summon in {practice} "
                    f"for practice, or `/staff dementor eventend` first.",
                    ephemeral=True,
                )
                return
            if self.state.get("active"):
                await interaction.response.send_message(
                    "One's already loose somewhere. Let it get dealt with first.", ephemeral=True)
                return
            if channel and not self._summon_channel_ok(channel.id):
                practice = ", ".join(
                    f"<#{cid}>" for cid in sorted(PRACTICE_SUMMON_CHANNEL_IDS)
                )
                await interaction.response.send_message(
                    "Pick one of the `/staff dementor channels` (or the always-on Attack rooms), or "
                    f"{practice} for practice.",
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

    @group.command(name="status", description="Status.")
    async def status(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        self._roll_day()
        pool = self._configured_channel_ids()
        staff_set = self.state.get("channel_ids", [])
        practice = ", ".join(
            f"<#{cid}>" for cid in sorted(PRACTICE_SUMMON_CHANNEL_IDS)
        )
        lines = [
            f"Event channels: {', '.join(f'<#{c}>' for c in pool) if pool else 'none set'}"
            + (f" (staff set {len(staff_set)}; +always-on extras)" if pool else ""),
            f"Practice summon: {practice} (never used by Attack waves)",
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
            partial_at = active.get("partial_expires_at")
            if hits and partial_at and time.time() < float(partial_at):
                lines.append(
                    f"First hit landed — countdown hidden. "
                    f"Slips away <t:{int(float(partial_at))}:R> if it isn't finished."
                )
            elif finder and until and time.time() < until:
                lines.append(f"Exclusive cast: <@{finder}> until <t:{int(until)}:R>.")
        else:
            lines.append("Nothing active right now.")
        if self.state.get("event"):
            lines.append(f"⚔️ An event is running - see `/staff dementor eventstatus`.")
        embed = discord.Embed(title="Wild Threats", description="\n".join(lines), color=DARK)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------------------------------------- staff: event

    @group.command(name="eventstart", description="Start Attack.")
    @app_commands.describe(
        length="5 or 10",
        name="Name",
    )
    @app_commands.choices(length=ATTACK_LENGTH_CHOICES)
    async def eventstart(
        self,
        interaction: discord.Interaction,
        length: app_commands.Choice[int],
        name: str = "Attack on Velmora",
    ):
        if not await self._staff(interaction):
            return
        minutes = int(length.value)

        # Ack Discord before waiting on the shared lock / posting waves.
        await interaction.response.defer(ephemeral=True)

        try:
            ok, msg, start = await self._begin_attack(
                minutes=minutes,
                name=name,
                guild_id=interaction.guild_id,
            )
            await interaction.followup.send(msg, ephemeral=True)
            if ok and start:
                await self._announce_attack_wave(start, ping=False)
        except Exception:
            log.exception("Attack on Velmora failed to start")
            try:
                await interaction.followup.send(
                    "Attack failed to start — check the bot logs.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

    @group.command(
        name="eventschedule",
        description="Weekly Attacks.",
    )
    @app_commands.describe(
        day_a="0 Mon-6 Sun",
        hour_a="Hour 0-23",
        length_a="5 or 10",
        minute_a="Minute",
        day_b="2nd day",
        hour_b="2nd hour",
        length_b="2nd length",
        minute_b="2nd min",
        day_c="3rd day",
        hour_c="3rd hour",
        length_c="3rd length",
        minute_c="3rd min",
    )
    @app_commands.choices(
        day_a=WEEKDAY_CHOICES,
        day_b=WEEKDAY_CHOICES,
        day_c=WEEKDAY_CHOICES,
        length_a=ATTACK_LENGTH_CHOICES,
        length_b=ATTACK_LENGTH_CHOICES,
        length_c=ATTACK_LENGTH_CHOICES,
    )
    async def eventschedule(
        self,
        interaction: discord.Interaction,
        day_a: app_commands.Choice[int],
        hour_a: app_commands.Range[int, 0, 23],
        length_a: app_commands.Choice[int],
        minute_a: app_commands.Range[int, 0, 59] = 0,
        day_b: app_commands.Choice[int] | None = None,
        hour_b: app_commands.Range[int, 0, 23] | None = None,
        length_b: app_commands.Choice[int] | None = None,
        minute_b: app_commands.Range[int, 0, 59] = 0,
        day_c: app_commands.Choice[int] | None = None,
        hour_c: app_commands.Range[int, 0, 23] | None = None,
        length_c: app_commands.Choice[int] | None = None,
        minute_c: app_commands.Range[int, 0, 59] = 0,
    ):
        if not await self._staff(interaction):
            return
        mins_a = int(length_a.value)
        if mins_a not in EVENT_PRESETS:
            await interaction.response.send_message(
                "Pick a 5- or 10-minute Attack for night 1.", ephemeral=True,
            )
            return
        raw_slots: list[dict | None] = [
            {
                "weekday": day_a.value,
                "hour": hour_a,
                "minute": minute_a,
                "minutes": mins_a,
            },
        ]
        if day_b is not None:
            if hour_b is None or length_b is None:
                await interaction.response.send_message(
                    "Set **hour_b** and **length_b** when you pick a second day.",
                    ephemeral=True,
                )
                return
            mins_b = int(length_b.value)
            if mins_b not in EVENT_PRESETS:
                await interaction.response.send_message(
                    "Pick a 5- or 10-minute Attack for night 2.", ephemeral=True,
                )
                return
            raw_slots.append(
                {
                    "weekday": day_b.value,
                    "hour": hour_b,
                    "minute": minute_b,
                    "minutes": mins_b,
                },
            )
        if day_c is not None:
            if hour_c is None or length_c is None:
                await interaction.response.send_message(
                    "Set **hour_c** and **length_c** when you pick a third day.",
                    ephemeral=True,
                )
                return
            mins_c = int(length_c.value)
            if mins_c not in EVENT_PRESETS:
                await interaction.response.send_message(
                    "Pick a 5- or 10-minute Attack for night 3.", ephemeral=True,
                )
                return
            raw_slots.append(
                {
                    "weekday": day_c.value,
                    "hour": hour_c,
                    "minute": minute_c,
                    "minutes": mins_c,
                },
            )
        slots = self._normalize_schedule_slots(raw_slots, default_minutes=mins_a)
        if not slots:
            await interaction.response.send_message(
                "Need at least one valid weekday + time.", ephemeral=True,
            )
            return
        sched = self._attack_schedule()
        sched["enabled"] = True
        sched["slots"] = slots
        # Keep a schedule-level fallback for older readers / migrated slots.
        sched["minutes"] = mins_a
        # Drop legacy shared-time keys if present.
        sched.pop("weekdays", None)
        sched.pop("hour", None)
        sched.pop("minute", None)
        # Reset fire keys so the next matching slot can warn/start cleanly.
        sched["last_warn_key"] = None
        sched["last_start_key"] = None
        self.save()
        from cogs.event_board import bump_event_board
        bump_event_board(self.bot)
        slot_lines = ", ".join(
            f"**{WEEKDAY_LABELS[s['weekday']]}** {s['hour']:02d}:{s['minute']:02d} "
            f"({EVENT_PRESETS[s['minutes']]['label']})"
            for s in slots
        )
        next_slot = self._next_attack_slot()
        next_note = (
            f" Next: <t:{int(next_slot[0].timestamp())}:F>."
            if next_slot else ""
        )
        roles = self._resolve_attack_ping_roles(interaction.guild)
        role_note = (
            " Pings: " + ", ".join(r.mention for r in roles) + "."
            if roles else
            " (No Champions / WIZARDS AND WITCHES roles found — "
            "set them with `/staff dementor eventscheduleroles`.)"
        )
        await interaction.response.send_message(
            f"Attack schedule on (America/Chicago): {slot_lines}. "
            f"**{len(slots)}**/week · 5-minute warn before each."
            f"{next_note}{role_note}",
            ephemeral=True,
        )

    @group.command(
        name="eventscheduleoff",
        description="Schedule off.",
    )
    async def eventscheduleoff(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        sched = self._attack_schedule()
        sched["enabled"] = False
        self.save()
        from cogs.event_board import bump_event_board
        bump_event_board(self.bot)
        await interaction.response.send_message(
            "Weekly Attack schedule is off. Manual `/staff dementor eventstart` still works.",
            ephemeral=True,
        )

    @group.command(
        name="eventscheduleroles",
        description="Ping roles.",
    )
    @app_commands.describe(
        champions="Champions",
        wizards_and_witches="Witches role",
    )
    async def eventscheduleroles(
        self,
        interaction: discord.Interaction,
        champions: discord.Role,
        wizards_and_witches: discord.Role,
    ):
        if not await self._staff(interaction):
            return
        sched = self._attack_schedule()
        # Preserve order; drop duplicates.
        ids: list[int] = []
        for role in (champions, wizards_and_witches):
            if role.id not in ids:
                ids.append(role.id)
        sched["role_ids"] = ids
        self.save()
        await interaction.response.send_message(
            "Attack pings will mention "
            + ", ".join(r.mention for r in (champions, wizards_and_witches))
            + " on the 5-minute warning and again when the swarm starts.",
            ephemeral=True,
        )

    @group.command(
        name="eventschedulestatus",
        description="Show schedule.",
    )
    async def eventschedulestatus(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        sched = self._attack_schedule()
        if not sched.get("enabled"):
            await interaction.response.send_message(
                "Weekly Attack schedule is **off**.", ephemeral=True,
            )
            return
        default_mins = self._slot_attack_minutes(None)
        slots = self._normalize_schedule_slots(
            sched.get("slots") or [], default_minutes=default_mins,
        )
        if slots:
            lines = []
            for s in slots:
                mins = self._slot_attack_minutes(s)
                preset = EVENT_PRESETS[mins]
                pool_pts = _preset_kill_pool(preset)
                lines.append(
                    f"· **{WEEKDAY_LABELS[s['weekday']]}** at "
                    f"**{s['hour']:02d}:{s['minute']:02d}** · "
                    f"**{preset['label']}** · top {preset['reward_top']} share {pool_pts}"
                )
            slot_block = "\n".join(lines)
        else:
            slot_block = "· (no slots)"
        next_slot = self._next_attack_slot()
        next_note = (
            f"\nNext: <t:{int(next_slot[0].timestamp())}:F> "
            f"(<t:{int(next_slot[0].timestamp())}:R>)."
            if next_slot else "\nNext: not scheduled."
        )
        roles = self._resolve_attack_ping_roles(interaction.guild)
        role_note = (
            "\nPings: " + ", ".join(r.mention for r in roles)
            if roles else
            "\nPings: (no roles set — name-match Champions / WIZARDS AND WITCHES, "
            "or `/staff dementor eventscheduleroles`)"
        )
        await interaction.response.send_message(
            f"**On** — America/Chicago · **{len(slots)}**/week:\n{slot_block}\n"
            f"Warn {EVENT_WARN_MINUTES} min early."
            f"{next_note}{role_note}",
            ephemeral=True,
        )

    @group.command(
        name="practice",
        description="Practice swarm.",
    )
    @app_commands.describe(
        channel="Channel",
        minutes="Minutes",
    )
    async def practice(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        minutes: app_commands.Range[int, 1, EVENT_MAX_MINUTES] = EVENT_DEFAULT_MINUTES,
    ):
        # Ack Discord first — anything before this can cause "did not respond".
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.HTTPException:
            log.exception("practice: could not defer interaction")
            return

        try:
            store = self.bot.get_cog("Store")
            if not (store and store.can_run_house_practice(interaction.user)):
                await interaction.followup.send(
                    "That's for staff or Presidents.", ephemeral=True,
                )
                return

            target = channel or interaction.channel
            target_id = getattr(target, "id", None) or interaction.channel_id
            if not target_id:
                await interaction.followup.send(
                    "Pick a text channel for the practice swarm.", ephemeral=True,
                )
                return

            ok, msg, start = await self._begin_practice(
                channel_id=int(target_id),
                guild_id=interaction.guild_id,
                minutes=minutes,
            )
            await interaction.followup.send(msg, ephemeral=True)
            if ok and start:
                await self._announce_practice_wave(start)
        except Exception as exc:
            log.exception("House practice swarm failed to start")
            try:
                await interaction.followup.send(
                    f"Practice swarm failed to start (`{type(exc).__name__}`). "
                    "Check the bot logs.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass

    async def _begin_practice(
        self,
        *,
        channel_id: int,
        guild_id: int | None,
        minutes: int,
    ) -> tuple[bool, str, tuple | None]:
        """Start a practice swarm. Returns (ok, user_message, start_tuple_or_None)."""
        if channel_id in PRACTICE_BLOCKED_CHANNEL_IDS:
            return (
                False,
                "Open lounges stay spawn-free. Run house practice in a house "
                "channel or study hall instead.",
                None,
            )
        if not (1 <= minutes <= EVENT_MAX_MINUTES):
            return (
                False,
                f"Pick a length between 1 and {EVENT_MAX_MINUTES} minutes.",
                None,
            )

        if self.lock is None:
            return False, "Wild Threats is still starting up — try again in a few seconds.", None

        async with self.lock:
            if self.state.get("event"):
                running = self.state["event"]
                kind = "practice swarm" if running.get("practice") else "Attack"
                return (
                    False,
                    f"**{running['name']}** ({kind}) is already running — "
                    "`/staff dementor eventend` first.",
                    None,
                )

            clean_name = "House Practice Swarm"
            now = time.time()
            active = self.state.get("active")
            if active and active.get("channel_id") == channel_id:
                button_message = self._button_message(active)
                self._cancel_partial_expiry()
                self.state["active"] = None
                if button_message:
                    await self._disable_threat_buttons(*button_message)
            event = {
                "name": clean_name,
                "started_at": now,
                "ends_at": now + minutes * 60,
                "minutes": minutes,
                "guild_id": guild_id,
                "channels": {},
                "tally": {},
                "practice": True,
                "channel_ids": [channel_id],
            }
            self.state["event"] = event
            self.save()
            intro = discord.Embed(
                title=f"Practice — {clean_name}",
                description=(
                    "House drill — same waves as Attack on Velmora, but **nothing "
                    "awards points or rewards**. Learn the creatures, practice `/cast`, "
                    f"then clear out.\n\nWaves keep coming for the next {minutes} minute(s)."
                ),
                color=EVENT_COLOR,
            )
            payloads = self._prepare_wave(event)
            start = (event["started_at"], payloads, clean_name, minutes, intro, [channel_id])

        return (
            True,
            f"**{clean_name}** begins in <#{channel_id}> for {minutes} minute(s) — practice only.",
            start,
        )

    async def _announce_practice_wave(self, start: tuple) -> None:
        started_at, payloads, _clean_name, _minutes, intro, pool = start
        for cid in pool:
            ch = await self._get_channel(cid)
            if ch is None:
                continue
            try:
                await ch.send(embed=intro)
            except discord.HTTPException:
                continue
        await self._deliver_wave(started_at, payloads)

    @group.command(name="eventend", description="End event.")
    async def eventend(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        async with self.lock:
            event = self._clear_event()
        if not event:
            await interaction.response.send_message("Nothing is running right now.", ephemeral=True)
            return
        ended = "Practice ended." if event.get("practice") else f"**{event['name']}** ended early."
        await interaction.response.send_message(ended, ephemeral=True)
        await self._finalize_event(event)

    @group.command(name="eventstatus", description="Event status.")
    async def eventstatus(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        event = self.state.get("event")
        if not event:
            await interaction.response.send_message("Nothing running right now.", ephemeral=True)
            return
        remaining = max(0, int(event["ends_at"] - time.time()))
        mins, secs = divmod(remaining, 60)
        reward_top = int(event.get("reward_top") or EVENT_PRESETS[EVENT_DEFAULT_MINUTES]["reward_top"])
        kill_pool = _event_kill_pool(event)
        length = int(event.get("minutes") or EVENT_DEFAULT_MINUTES)
        tally = sorted(event.get("tally", {}).items(), key=lambda kv: -kv[1].get("rep", 0))
        practice = bool(event.get("practice"))
        where = self._event_channel_ids(event)
        lines = [
            f"**{event['name']}**"
            + (" (practice · no points)" if practice else "")
            + (
                f" — {mins}m {secs}s left "
                f"({length}-minute · top {reward_top} by kills share "
                f"{kill_pool} pts)."
                if not practice else
                f" - {mins}m {secs}s left."
            ),
            f"Channels: {', '.join(f'<#{c}>' for c in where) if where else 'none'}.",
            f"{len(event.get('channels', {}))} monster(s) out right now.",
        ]
        if tally:
            # Sort status by kills; show everyone with a kill.
            by_kills = sorted(
                [(uid, e) for uid, e in tally if int(e.get("kills", 0) or 0) > 0],
                key=lambda kv: -kv[1].get("kills", 0),
            )
            lines.append("")
            lines.append(
                f"Kill board (top {reward_top} share {kill_pool} at the end):"
                if not practice else "Kill board:"
            )
            lines += [
                f"<@{uid}>: **{e['kills']}** kill(s), {e['rep']} rep"
                for uid, e in by_kills
            ]
        else:
            lines.append("Nobody's landed a hit yet.")
        embed = discord.Embed(title="⚔️ Event status", description="\n".join(lines), color=EVENT_COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Dementors(bot))
