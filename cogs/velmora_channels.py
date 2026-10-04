"""Shared Velmora channel IDs used across games.

Study hall is a new-student practice room where several games are also
allowed alongside their main homes.
"""

STUDY_HALL_CHANNEL_ID = 1555035151208153209  # new-student study hall

# Potions and Duels also run here (alongside their main channels).
# Descent stays out of this room on purpose.
DUELS_POTIONS_CHANNEL_ID = 1555554825616236726

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


def with_study_hall(*channel_ids: int) -> frozenset[int]:
    return frozenset({*channel_ids, STUDY_HALL_CHANNEL_ID})


def with_study_hall_and_duels_potions(*channel_ids: int) -> frozenset[int]:
    """Home channel(s) + study hall + shared Potions/Duels room + games lounge."""
    return frozenset({
        *channel_ids,
        STUDY_HALL_CHANNEL_ID,
        DUELS_POTIONS_CHANNEL_ID,
        *SHARED_GAMES_CHANNEL_IDS,
    })


def with_pitch_homes(*channel_ids: int) -> frozenset[int]:
    """Quidditch / broom-race homes: pitch channel(s) + study hall + games lounge."""
    return frozenset({
        *channel_ids,
        STUDY_HALL_CHANNEL_ID,
        *SHARED_GAMES_CHANNEL_IDS,
    })


def duel_home_channels(arena_id: int) -> frozenset[int]:
    """Every channel where duels may be started when an arena is configured."""
    return frozenset({
        arena_id,
        STUDY_HALL_CHANNEL_ID,
        DUELS_POTIONS_CHANNEL_ID,
        *SHARED_GAMES_CHANNEL_IDS,
        *EXTRA_DUEL_CHANNEL_IDS,
    })


def channel_mentions(channel_ids) -> str:
    return " · ".join(f"<#{cid}>" for cid in sorted(channel_ids))
