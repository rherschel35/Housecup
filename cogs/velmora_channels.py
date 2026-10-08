"""Shared Velmora channel IDs used across games.

Study hall is a new-student practice room where several games are also
allowed alongside their main homes.

Open lounges are multi-game rooms: most games run there, but Wild Threat
spawns and /explore|/forage stay out on purpose.
"""

STUDY_HALL_CHANNEL_ID = 1555035151208153209  # new-student study hall

# Shared Potions/Duels room — also one of the multi-game open lounges.
DUELS_POTIONS_CHANNEL_ID = 1555554825616236726

# Multi-game lounges — duels, potions, Descent, Quidditch/broom, chess/checkers.
# Not: Wild Threat spawns, exploring, castle PvP, 3Raid, or Forrest.
OPEN_LOUNGE_CHANNEL_IDS = frozenset({
    1557137090058395768,
    DUELS_POTIONS_CHANNEL_ID,
})

# Lounge where duels, potions, Quidditch, broom races, and practice
# Wild Threat summons all run (Attack waves never flood this room).
SHARED_GAMES_CHANNEL_ID = 1556100254082924595
SHARED_GAMES_CHANNEL_IDS = frozenset({SHARED_GAMES_CHANNEL_ID})

# Castle / army PvP — sieges, map board, battle reports.
CASTLES_CHANNEL_ID = 1556398769275404441
CASTLES_CHANNEL_IDS = frozenset({CASTLES_CHANNEL_ID})

# Extra rooms where /duel is allowed (in addition to DUEL_CHANNEL_ID,
# study hall, the shared Potions/Duels room, and SHARED_GAMES_CHANNEL_ID).
EXTRA_DUEL_CHANNEL_IDS = frozenset({
    1556111133314916476,
    1556111252713902172,
})

# Warn / start / results board for scheduled Attack swarms and Duel Night.
# Override with env EVENT_ANNOUNCE_CHANNEL_ID if needed.
EVENT_ANNOUNCE_CHANNEL_ID = 1542575004313722921


def with_open_lounges(*channel_ids: int) -> frozenset[int]:
    """Add the multi-game open lounges to a home-channel set."""
    return frozenset({*channel_ids, *OPEN_LOUNGE_CHANNEL_IDS})


def with_study_hall(*channel_ids: int) -> frozenset[int]:
    return frozenset({
        *channel_ids,
        STUDY_HALL_CHANNEL_ID,
        *OPEN_LOUNGE_CHANNEL_IDS,
    })


def with_study_hall_and_duels_potions(*channel_ids: int) -> frozenset[int]:
    """Home channel(s) + study hall + open lounges + games lounge."""
    return frozenset({
        *channel_ids,
        STUDY_HALL_CHANNEL_ID,
        *OPEN_LOUNGE_CHANNEL_IDS,
        *SHARED_GAMES_CHANNEL_IDS,
    })


def with_pitch_homes(*channel_ids: int) -> frozenset[int]:
    """Quidditch / broom-race homes: pitch + study hall + lounges + games lounge."""
    return frozenset({
        *channel_ids,
        STUDY_HALL_CHANNEL_ID,
        *OPEN_LOUNGE_CHANNEL_IDS,
        *SHARED_GAMES_CHANNEL_IDS,
    })


def duel_home_channels(arena_id: int) -> frozenset[int]:
    """Every channel where duels may be started when an arena is configured."""
    return frozenset({
        arena_id,
        STUDY_HALL_CHANNEL_ID,
        *OPEN_LOUNGE_CHANNEL_IDS,
        *SHARED_GAMES_CHANNEL_IDS,
        *EXTRA_DUEL_CHANNEL_IDS,
    })


def channel_mentions(channel_ids) -> str:
    return " · ".join(f"<#{cid}>" for cid in sorted(channel_ids))
