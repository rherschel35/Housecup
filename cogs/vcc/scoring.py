"""House-point decision for a Curse & Crown win.

The ledger itself lives in the Store cog. This only decides whether a
finished duel should move the Cup, matching the other games: same-house
duels pay nothing, and a player can only be paid for a few wins each
Central day.
"""

from __future__ import annotations

WIN_POINTS = 3
REWARDED_PER_DAY = 3


def points_for_win(
    *,
    same_house: bool,
    rewarded_today: int,
    amount: int = WIN_POINTS,
    cap: int = REWARDED_PER_DAY,
) -> tuple[int, str]:
    """Return ``(points, note)``.

    ``note`` is ``awarded``, ``same-house``, or ``daily-cap``.
    """
    if same_house:
        return 0, "same-house"
    if rewarded_today >= cap:
        return 0, "daily-cap"
    return amount, "awarded"
