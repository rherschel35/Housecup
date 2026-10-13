"""House Cup configuration: channel IDs, role names, and point caps.

Values here are the live game settings. Cogs re-export the names players
and other modules already import, so a move into this file does not change
behavior. Secrets stay in the environment; this module only holds defaults.
"""

import os
from pathlib import Path

# Project root. Nested cog packages must use this instead of __file__
# parents so state files stay at the same paths on the Railway volume.
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))


# ---------------------------------------------------------------- channels
#
# Moved verbatim from cogs/velmora_channels.py. Helper functions that build
# on these sets still live there.

STUDY_HALL_CHANNEL_ID = 1555035151208153209  # new-student study hall

# Shared Potions/Duels room — also one of the multi-game open lounges.
DUELS_POTIONS_CHANNEL_ID = 1555554825616236726

# Full multi-game lounge: everything except castle PvP and 3Raid.
EVERYTHING_LOUNGE_CHANNEL_ID = 1558611215054282822
EVERYTHING_LOUNGE_CHANNEL_IDS = frozenset({EVERYTHING_LOUNGE_CHANNEL_ID})

# Multi-game lounges — duels, potions, Descent, Quidditch/broom, chess/checkers.
# Not: Wild Threat spawns, exploring, castle PvP, or 3Raid
# (except channels also listed in EVERYTHING_LOUNGE_CHANNEL_IDS).
OPEN_LOUNGE_EXTRA_CHANNEL_ID = 1557137090058395768
OPEN_LOUNGE_CHANNEL_IDS = frozenset({
    OPEN_LOUNGE_EXTRA_CHANNEL_ID,
    DUELS_POTIONS_CHANNEL_ID,
    *EVERYTHING_LOUNGE_CHANNEL_IDS,
})

# Open lounges that keep explore / threats / practice swarms out.
RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS = frozenset(
    OPEN_LOUNGE_CHANNEL_IDS - EVERYTHING_LOUNGE_CHANNEL_IDS
)

# Lounge where duels, potions, Quidditch, broom races, and practice
# Wild Threat summons all run (Attack waves never flood this room).
SHARED_GAMES_CHANNEL_ID = 1556100254082924595
SHARED_GAMES_CHANNEL_IDS = frozenset({SHARED_GAMES_CHANNEL_ID})

# Dedicated house practice swarm rooms (Attack waves never flood these).
SWARM_PRACTICE_CHANNEL_ID = 1558467851587817472
SWARM_PRACTICE_EXTRA_CHANNEL_IDS = (
    1558543500742623375,
    1558543550247993374,
    1558543590819631227,
)
SWARM_PRACTICE_CHANNEL_IDS = frozenset({
    SWARM_PRACTICE_CHANNEL_ID,
    *SWARM_PRACTICE_EXTRA_CHANNEL_IDS,
})

# Castle / army PvP — sieges, map board, battle reports.
CASTLES_CHANNEL_ID = 1556398769275404441
CASTLES_CHANNEL_IDS = frozenset({CASTLES_CHANNEL_ID})

# Wizard Bingo — primary room, plus everything lounges.
BINGO_CHANNEL_ID = 1558313269800734740
BINGO_CHANNEL_IDS = frozenset({
    BINGO_CHANNEL_ID,
    *EVERYTHING_LOUNGE_CHANNEL_IDS,
})

# Extra rooms where /duel is allowed (in addition to DUEL_CHANNEL_ID,
# study hall, the shared Potions/Duels room, and SHARED_GAMES_CHANNEL_ID).
EXTRA_DUEL_CHANNEL_IDS = frozenset({
    1556111133314916476,
    1556111252713902172,
})

# Warn / start / results board for scheduled Attack swarms and Duel Night.
# Override with env EVENT_ANNOUNCE_CHANNEL_ID if needed.
EVENT_ANNOUNCE_CHANNEL_ID = 1542575004313722921

# Living weekly Attack / Duel Night / Challenge schedule board.
# Override with env EVENT_BOARD_CHANNEL_ID if needed.
EVENT_BOARD_CHANNEL_ID = 1551945597362241658
# Scratch room for trying the board before the official post.
EVENT_BOARD_TEST_CHANNEL_ID = 1552401261184155648

# Quidditch pitch. Broom races use the same room.
QUIDDITCH_CHANNEL_ID = 1553089438933065913

# 3Raid home channel. Lounges are not added.
THREERAID_CHANNEL_ID = 1553925340043157504

# Potions classroom. The scratch room is the event-board test channel.
POTIONS_CHANNEL_ID = 1553253009398693958

# Wizard's Chess and Checkers share one board-games channel.
CHESS_CHANNEL_ID = 1553832675993985024
CHECKERS_CHANNEL_ID = 1553832675993985024

# Descent home channels. Study hall and open lounges are added at the call site.
DESCENT_HOME_CHANNEL_IDS = (
    1553089612849877053,  # original Descent channel
    1553861330988044308,
    1553861650732421202,
    1552401261184155648,
    1554579738943815712,
    1554579829452701786,
    1554579862176665630,
)

# Always included in ambient Wild Threat spawns and Attack waves.
EXTRA_THREAT_CHANNEL_ID = 1552403823769952266

# Env-var defaults. The environment still overrides these.
BEAST_CHANNEL_ID_DEFAULT = "1552403888890576916"  # #explore-velmora
BROKE_REPORT_CHANNEL_ID_DEFAULT = "1555383179802579005"
HEADMASTER_USER_ID_DEFAULT = "555141900802457630"


# ---------------------------------------------------------------- roles

HEADMASTER_ROLE_NAME = "headmasters"  # matched case-insensitively
# Attack warn/start and Duel Night, unless an env list overrides them.
COMMUNITY_PING_ROLE_NAMES = ("Champions", "WIZARDS AND WITCHES")
DUEL_CHAMPION_ROLE_NAME_DEFAULT = "Champion of the Circle"


# ---------------------------------------------------------------- points
#
# House-point awards and the caps that gate them. Names are prefixed when
# two games used the same constant name for different values.

# Duels
DUEL_REWARD_POINTS = 1
DUEL_REWARDED_PER_DAY = 3
DUEL_BOUNTY_POINTS = 2
DUEL_RIVAL_BONUS = 1
DUEL_TRIO_HOUSE_WIN_GOAL = 5
DUEL_TRIO_HOUSE_COMPLETION_POINTS = 5
DUEL_TRIO_REWARD_POINTS = 1
DUEL_GRAND_REWARD_POINTS = 2
DUEL_GRAND_REWARDED_PER_DAY = 3
DUEL_GIFT_POINTS = 3
DUEL_WEEKLY_POINTS = 5
DUEL_NIGHT_MULTIPLIER = 2
DUEL_NIGHT_MVP_BONUS = 5
DUEL_NIGHT_HOUSE_BONUS = 10

# Wild Threats / Attack
THREAT_REWARD_POINTS = 3
THREAT_EVENT_MVP_BONUS = 5
THREAT_EVENT_KILL_REP_TIE_BONUS = 2
THREAT_EVENT_KILL_POOL = 100
THREAT_EVENT_HOUSE_TURNOUT_BONUS = 10
THREAT_EVENT_PRESETS = {
    5: {"reward_top": 7, "kill_pool": 100, "label": "5-minute"},
    10: {"reward_top": 10, "kill_pool": 300, "label": "10-minute"},
}

# Quidditch house set: 5 house-match wins → +5 once.
QUIDDITCH_HOUSE_WIN_GOAL = 5
QUIDDITCH_HOUSE_COMPLETION_POINTS = 5

# Broom races
BROOM_SOLO_DAILY_CAP = 5
BROOM_SOLO_COMPLETION_POINTS = 3
BROOM_CHALLENGE_POINT_CAP = 3
BROOM_CHALLENGE_POINTS = 3

# Potions: 5 brews → +3 once.
BREW_GOAL = 5
BREW_COMPLETION_POINTS = 3

# 3Raid
THREERAID_POINTS_ON_CLEAR = 25

# Bingo
BINGO_POINTS = 5
BINGO_MAX_WINNERS = 5

# Checklist full-clear streak
CHECKLIST_FULL_CLEAR_BONUS_EVERY = 10
CHECKLIST_FULL_CLEAR_BONUS_POINTS = 5

# Chocolate frogs
FROG_DECADE_PTS = 10
FROG_MYTHIC_PTS = 25

# Mystery beans. COST is house points spent per bean.
BEAN_COST = 1
BEANS_PER_DAY = 3

# /staff points award ceiling
MAX_PER_AWARD = 500

# Beasts
BEAST_BEFRIEND_GOAL = 5
BEAST_BEFRIEND_COMPLETION_POINTS = 3
BEAST_JOURNAL_COMPLETE_POINTS = 5
BEAST_RARITY_POINTS = {"common": 1, "uncommon": 2, "rare": 3, "legendary": 5}

# Castles
SUNDAY_CASTLE_POINTS = 50
BANNERHALL_PER_CASTLE = 30
BIND_BONUS_GOAL = 200
BIND_BONUS_POINTS = 10
BIND_BONUS_MIN_FLOOR = 40

# Exploring
EXPLORE_POINTS_PER_DAY = 3
EXPLORE_CHOICE_REP_PER_DAY = 3
EXPLORE_OFFER_REP_PER_DAY = 4

# Chess and checkers award nothing.
BOARD_GAME_POINTS_PER_WIN = 0
BOARD_GAME_WEEKLY_WIN_CAP = 0

# Familiars
FAMILIAR_FRIENDSHIP_CAP = 100

# Marketplace defaults. Live prices still come from marketplace.json.
DEFAULT_PRICES = {
    "buy_common": 3,
    "buy_uncommon": 6,
    "sell_payout": 3,
    "sell_daily_cap": 6,
    "scroll": 30,
    "title": 50,
    "room": 500,
    "broom_token": 50,
}
