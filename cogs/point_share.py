"""Shared end-of-event pool split.

Attack and House Duel Night both pay the top of a tally with
floor((count / total) × pool). Attack leaves each share uncapped.
Duel Night passes ``max_each``.
"""


def kill_share_awards(
    by_kills: list[dict],
    *,
    pool: int,
    top_n: int,
    total_kills: int,
    max_each: int | None = None,
) -> dict[int, int]:
    """Top ``top_n`` by kills: floor((kills / total) × pool).

    Only the cut is paid; leftover from rounding or from kills outside the
    top N stays unspent. ``max_each`` caps one player's share. Omit it and
    shares are uncapped — that is what Attack uses.
    """
    if pool <= 0 or total_kills <= 0 or top_n <= 0:
        return {}
    cap = None if max_each is None else max(0, int(max_each))
    out: dict[int, int] = {}
    for r in by_kills[:top_n]:
        kills = int(r.get("kills") or 0)
        if kills <= 0:
            continue
        pts = int(pool * kills / total_kills)  # truncate like 17.5 → 17
        if cap is not None:
            pts = min(pts, cap)
        if pts > 0:
            out[int(r["uid"])] = pts
    return out
