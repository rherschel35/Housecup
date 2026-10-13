"""
Wild Threats. Something slips into one of the social channels on its own,
a few times a day - and a headmaster can always summon one on the spot.

    /cast
        - anyone, opens spell buttons against whatever's here
        - CAST_AUTO_USER_ID auto-picks the right spell (no buttons)
    /staff dementor channels          - set the channels they can appear in
    /staff dementor summon [channel] [creature] - make one appear right now
        (study hall + shared games lounge + swarm practice room are
         practice summons; Attack waves never go there)
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

from discord.ext import commands

from cogs.dementors.catalog import (
    ARRIVALS,
    ASSETS_DIR,
    ATTACK_INTRO,
    ATTACK_LENGTH_CHOICES,
    ATTACK_SCHEDULE_MAX_DAYS,
    BANISHED,
    CAST_AUTO_USER_ID,
    CHICAGO,
    COMMUNITY_PING_ROLE_NAMES,
    CREATURES,
    CREATURE_CHOICES,
    DARK,
    DATA_DIR,
    EVENT_ANNOUNCE_CHANNEL_ID,
    EVENT_COLOR,
    EVENT_DEFAULT_MINUTES,
    EVENT_HOUSE_TURNOUT_BONUS,
    EVENT_KILL_POOL,
    EVENT_KILL_REP_TIE_BONUS,
    EVENT_MAX_MINUTES,
    EVENT_MVP_BONUS,
    EVENT_PRESETS,
    EVENT_WARN_MINUTES,
    EVENT_WAVE_SECONDS,
    EVERYTHING_LOUNGE_CHANNEL_IDS,
    EXTRA_THREAT_CHANNEL_ID,
    EXTRA_THREAT_CHANNEL_IDS,
    FINDER_EXCLUSIVE_SECONDS,
    HEADMASTER_USER_ID_DEFAULT,
    NO_THREAT_SPAWN_CHANNEL_IDS,
    PRACTICE_BLOCKED_CHANNEL_IDS,
    PRACTICE_SUMMON_CHANNEL_IDS,
    RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS,
    REWARD_POINTS,
    ROOT_DIR,
    SHARED_GAMES_CHANNEL_ID,
    SPAWNS_PER_DAY,
    SPAWN_CHANCE,
    SPAWN_WEIGHTS,
    SPELL_LABEL,
    SPELL_ORDER,
    STATE_DIR,
    STATE_PATH,
    STUDY_HALL_CHANNEL_ID,
    SWARM_PRACTICE_CHANNEL_IDS,
    THREAT_ALERT_COLOR,
    THREAT_EVENT_HOUSE_TURNOUT_BONUS,
    THREAT_EVENT_KILL_POOL,
    THREAT_EVENT_KILL_REP_TIE_BONUS,
    THREAT_EVENT_MVP_BONUS,
    THREAT_EVENT_PRESETS,
    THREAT_REWARD_POINTS,
    TICKS_PER_DAY,
    TICK_MINUTES,
    WANDER_MINUTES,
    WEEKDAY_CHOICES,
    WEEKDAY_LABELS,
    ZoneInfo,
    _DEFAULT_PING_NAMES,
    _DUEL_SPELL_NAMES,
    _attack_ping_role_names,
    _chicago_now,
    _event_kill_pool,
    _preset_kill_pool,
    _raw_cast_auto,
    kill_share_awards,
    log,
    today,
)

from cogs.dementors.views import (
    CastSpellButton,
    CastSpellView,
    SpellPick,
    _article,
)

from cogs.dementors.cog import Dementors


async def setup(bot: commands.Bot):
    await bot.add_cog(Dementors(bot))
