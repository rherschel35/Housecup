"""Shared Velmora channel IDs used across games.

Study hall is a new-student practice room where several games are also
allowed alongside their main homes.
"""

STUDY_HALL_CHANNEL_ID = 1555035151208153209  # new-student study hall


def with_study_hall(*channel_ids: int) -> frozenset[int]:
    return frozenset({*channel_ids, STUDY_HALL_CHANNEL_ID})


def channel_mentions(channel_ids) -> str:
    return " · ".join(f"<#{cid}>" for cid in sorted(channel_ids))
