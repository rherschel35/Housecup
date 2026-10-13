"""Shared Velmora channel IDs used across games.

Study hall is a new-student practice room where several games are also
allowed alongside their main homes.

Open lounges are multi-game rooms: most games run there, but Wild Threat
spawns and /explore|/forage stay out on purpose.

Everything lounges are open lounges that also allow explore, Wild Threats,
practice swarms, and Bingo. Still not castle PvP or 3Raid.
"""

from cogs.config import (
    BINGO_CHANNEL_ID,
    BINGO_CHANNEL_IDS,
    CASTLES_CHANNEL_ID,
    CASTLES_CHANNEL_IDS,
    DUELS_POTIONS_CHANNEL_ID,
    EVENT_ANNOUNCE_CHANNEL_ID,
    EVENT_BOARD_CHANNEL_ID,
    EVENT_BOARD_TEST_CHANNEL_ID,
    EVERYTHING_LOUNGE_CHANNEL_ID,
    EVERYTHING_LOUNGE_CHANNEL_IDS,
    EXTRA_DUEL_CHANNEL_IDS,
    OPEN_LOUNGE_CHANNEL_IDS,
    RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS,
    SHARED_GAMES_CHANNEL_ID,
    SHARED_GAMES_CHANNEL_IDS,
    STUDY_HALL_CHANNEL_ID,
    SWARM_PRACTICE_CHANNEL_ID,
    SWARM_PRACTICE_CHANNEL_IDS,
)


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
