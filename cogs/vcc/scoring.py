"""Rewards for a Curse & Crown duel.

During a wager night the table is a stake: Free, 5, or 10 house points.
The winner's house takes that stake from the loser's house, and never more
than the losing house still has. Both houses have to be able to cover the
stake before the duel starts. Free moves nothing.

Any other time the duel pays no house points. The winner draws a Chocolate
Frog or one of the 35 spell cards instead.
"""

from __future__ import annotations

from typing import Sequence

# Dropdown values. 0 is a free table.
WAGERS = (0, 5, 10)
MAX_WAGER = 10
NIGHT_LENGTHS = (30, 60)


def dropdown_amounts(
    *,
    night: bool,
    balances: tuple[int, int] | None,
    balances_known: bool,
) -> tuple[int, ...]:
    """Stakes to show in the challenge dropdown.

    Outside a wager night the only row is Free. During a night, 5 and 10
    appear when both houses can cover them. If the opponent isn't chosen
    yet, the night menu lists every stake and the command checks again.
    """
    if not night:
        return (0,)
    if not balances_known:
        return WAGERS
    if balances is None:
        return (0,)
    covered = min(int(balances[0]), int(balances[1]))
    return tuple(amount for amount in WAGERS if amount == 0 or covered >= amount)


def stake_decision(
    *,
    night: bool,
    wager: int,
    balances: tuple[int, int] | None,
) -> str:
    """Whether this stake may be offered.

    ``ok``, ``invalid``, ``no-night``, ``no-balance``, or ``short``.
    ``balances`` is ``None`` when there is no House Cup to stake against.
    """
    if int(wager) not in WAGERS:
        return "invalid"
    if not night:
        return "ok" if int(wager) == 0 else "no-night"
    if int(wager) == 0:
        return "ok"
    if balances is None:
        return "no-balance"
    if min(int(balances[0]), int(balances[1])) < int(wager):
        return "short"
    return "ok"


def points_to_move(wager: int, loser_balance: int) -> tuple[int, str]:
    """House points that actually change hands.

    The loser keeps everything their house earned beyond this stake: the
    move is the agreed wager, or less when the house can no longer cover
    it. ``(amount, note)`` where note is ``free``, ``paid``, ``partial``,
    ``uncovered``, or ``invalid``.
    """
    stake = int(wager)
    if stake == 0:
        return 0, "free"
    if stake not in WAGERS:
        return 0, "invalid"
    have = int(loser_balance)
    if have <= 0:
        return 0, "uncovered"
    if have < stake:
        return have, "partial"
    return stake, "paid"


def collectible_kind(roll: int) -> str:
    """Even rolls draw a spell card. Odd rolls draw a Chocolate Frog."""
    return "frog" if int(roll) % 2 else "spell"


def pick_from(ids: Sequence[str], roll: int) -> str:
    """Stable pick from the 35-card catalog (or any non-empty list)."""
    catalog = tuple(ids)
    if not catalog:
        raise ValueError("empty catalog")
    return catalog[int(roll) % len(catalog)]
