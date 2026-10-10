"""
Wizard Bingo win modes and pattern shapes (5×5, 0-indexed).

Modes:
  traditional  — any full row, column, or diagonal
  corners      — four corners only
  diagonals    — either main diagonal
  pattern      — exact shape from the pattern library (random_pattern picks one)
"""

from __future__ import annotations

import random
from typing import Iterable

ROWS = COLS = 5
CENTER = (2, 2)

# Stable staff Choice values
MODE_TRADITIONAL = "traditional"
MODE_CORNERS = "corners"
MODE_DIAGONALS = "diagonals"
MODE_RANDOM_PATTERN = "random_pattern"

MODE_CHOICES = (
    (MODE_TRADITIONAL, "Traditional — any line"),
    (MODE_CORNERS, "4 Corners"),
    (MODE_DIAGONALS, "Diagonals only"),
    (MODE_RANDOM_PATTERN, "Random pattern"),
)

MODE_LABELS = {
    MODE_TRADITIONAL: "Traditional",
    MODE_CORNERS: "4 Corners",
    MODE_DIAGONALS: "Diagonals only",
    MODE_RANDOM_PATTERN: "Random pattern",
}


def _line_rows() -> list[list[tuple[int, int]]]:
    return [[(r, c) for c in range(COLS)] for r in range(ROWS)]


def _line_cols() -> list[list[tuple[int, int]]]:
    return [[(r, c) for r in range(ROWS)] for c in range(COLS)]


def _diags() -> list[list[tuple[int, int]]]:
    return [
        [(i, i) for i in range(ROWS)],
        [(i, COLS - 1 - i) for i in range(ROWS)],
    ]


TRADITIONAL_LINES = _line_rows() + _line_cols() + _diags()
DIAGONAL_LINES = _diags()
CORNERS = [(0, 0), (0, 4), (4, 0), (4, 4)]


def _cells(*pairs: tuple[int, int]) -> frozenset[tuple[int, int]]:
    return frozenset(pairs)


def _full_col(c: int) -> frozenset[tuple[int, int]]:
    return frozenset((r, c) for r in range(ROWS))


def _full_row(r: int) -> frozenset[tuple[int, int]]:
    return frozenset((r, c) for c in range(COLS))


def _block(r0: int, c0: int, h: int, w: int) -> frozenset[tuple[int, int]]:
    return frozenset(
        (r, c) for r in range(r0, r0 + h) for c in range(c0, c0 + w)
    )


# Named shapes for "Random pattern" — lots of classic bingo boards.
PATTERNS: dict[str, frozenset[tuple[int, int]]] = {
    "heart": _cells(
        (0, 0), (0, 1), (0, 3), (0, 4),
        (1, 0), (1, 2), (1, 4),
        (2, 0), (2, 2), (2, 4),
        (3, 1), (3, 3),
        (4, 2),
    ),
    "double_x": _cells(
        (0, 0), (0, 2), (1, 1), (2, 0), (2, 2),
        (2, 4), (3, 3), (4, 2), (4, 4),
    ),
    "arrow_br": _cells(
        (0, 0), (1, 0), (2, 0),
        (0, 1), (1, 1),
        (0, 2), (2, 2),
        (3, 3), (4, 4),
    ),
    "arrow_bl": _cells(
        (0, 4), (1, 4), (2, 4),
        (0, 3), (1, 3),
        (0, 2), (2, 2),
        (3, 1), (4, 0),
    ),
    "bno": _full_col(0) | _full_col(2) | _full_col(4),
    "letter_x": _cells(*[(i, i) for i in range(5)], *[(i, 4 - i) for i in range(5)]),
    "letter_t": _full_row(0) | frozenset((r, 2) for r in range(ROWS)),
    "letter_u": (
        _full_col(0) | _full_col(4) | _full_row(4)
    ),
    "letter_h": (
        _full_col(0) | _full_col(4) | frozenset((2, c) for c in range(COLS))
    ),
    "letter_l": _full_col(0) | _full_row(4),
    "plus": (
        frozenset((2, c) for c in range(COLS))
        | frozenset((r, 2) for r in range(ROWS))
    ),
    "frame": (
        _full_row(0) | _full_row(4) | _full_col(0) | _full_col(4)
    ),
    "postage_stamp": _block(0, 0, 2, 2),
    "postage_br": _block(3, 3, 2, 2),
    "small_diamond": _cells((1, 2), (2, 1), (2, 2), (2, 3), (3, 2)),
    "large_diamond": _cells(
        (0, 2),
        (1, 1), (1, 3),
        (2, 0), (2, 2), (2, 4),
        (3, 1), (3, 3),
        (4, 2),
    ),
    "pyramid": _cells(
        (0, 2),
        (1, 1), (1, 2), (1, 3),
        (2, 0), (2, 1), (2, 2), (2, 3), (2, 4),
    ),
    "pyramid_up": _cells(
        (2, 0), (2, 1), (2, 2), (2, 3), (2, 4),
        (3, 1), (3, 2), (3, 3),
        (4, 2),
    ),
    "checker_dark": _cells(
        *[(r, c) for r in range(ROWS) for c in range(COLS) if (r + c) % 2 == 0]
    ),
    "checker_light": _cells(
        *[(r, c) for r in range(ROWS) for c in range(COLS) if (r + c) % 2 == 1]
    ),
    "railroad": _full_row(1) | _full_row(3),
    "ladder": _full_col(1) | _full_col(3),
    "smile": _cells(
        (1, 1), (1, 3),
        (2, 2),
        (3, 0), (3, 4),
        (4, 1), (4, 2), (4, 3),
    ),
    "airplane": _cells(
        (0, 2),
        (1, 0), (1, 1), (1, 2), (1, 3), (1, 4),
        (2, 2),
        (3, 1), (3, 2), (3, 3),
        (4, 2),
    ),
    "bowtie": _cells(
        (0, 0), (0, 4),
        (1, 1), (1, 3),
        (2, 2),
        (3, 1), (3, 3),
        (4, 0), (4, 4),
    ),
    "zigzag": _cells(
        (0, 0), (0, 1),
        (1, 1), (1, 2),
        (2, 2), (2, 3),
        (3, 3), (3, 4),
        (4, 4),
    ),
    "s_curve": _cells(
        (0, 1), (0, 2), (0, 3), (0, 4),
        (1, 4),
        (2, 1), (2, 2), (2, 3),
        (3, 0),
        (4, 0), (4, 1), (4, 2), (4, 3),
    ),
    "window": (
        _full_row(0) | _full_row(2) | _full_row(4)
        | _full_col(0) | _full_col(2) | _full_col(4)
    ),
    "cross_corners": _cells(
        (0, 0), (0, 4), (1, 1), (1, 3), (2, 2), (3, 1), (3, 3), (4, 0), (4, 4),
        (0, 2), (2, 0), (2, 4), (4, 2),
    ),
    "ring": _cells(
        (0, 1), (0, 2), (0, 3),
        (1, 0), (1, 4),
        (2, 0), (2, 4),
        (3, 0), (3, 4),
        (4, 1), (4, 2), (4, 3),
    ),
    "blackout": frozenset((r, c) for r in range(ROWS) for c in range(COLS)),
    "top_bottom": _full_row(0) | _full_row(4),
    "sides": _full_col(0) | _full_col(4),
    "center_cross_small": _cells(
        (1, 2), (2, 1), (2, 2), (2, 3), (3, 2),
    ),
    "hourglass": _cells(
        (0, 0), (0, 1), (0, 2), (0, 3), (0, 4),
        (1, 1), (1, 3),
        (2, 2),
        (3, 1), (3, 3),
        (4, 0), (4, 1), (4, 2), (4, 3), (4, 4),
    ),
    "lightning": _cells(
        (0, 2), (0, 3), (0, 4),
        (1, 2),
        (2, 1), (2, 2), (2, 3),
        (3, 2),
        (4, 0), (4, 1), (4, 2),
    ),
    "fish": _cells(
        (1, 1), (1, 2), (1, 3),
        (2, 0), (2, 2), (2, 4),
        (3, 1), (3, 2), (3, 3),
        (2, 1), (2, 3),
    ),
    "mountain": _cells(
        (2, 0), (2, 4),
        (3, 0), (3, 1), (3, 3), (3, 4),
        (4, 0), (4, 1), (4, 2), (4, 3), (4, 4),
        (1, 1), (1, 3),
        (0, 2),
    ),
}

PATTERN_LABELS = {
    "heart": "Heart",
    "double_x": "Double X",
    "arrow_br": "Arrow ↘",
    "arrow_bl": "Arrow ↙",
    "bno": "B-N-O columns",
    "letter_x": "Letter X",
    "letter_t": "Letter T",
    "letter_u": "Letter U",
    "letter_h": "Letter H",
    "letter_l": "Letter L",
    "plus": "Plus / Cross",
    "frame": "Frame / Outside",
    "postage_stamp": "Postage stamp (TL)",
    "postage_br": "Postage stamp (BR)",
    "small_diamond": "Small diamond",
    "large_diamond": "Large diamond",
    "pyramid": "Pyramid",
    "pyramid_up": "Pyramid (up)",
    "checker_dark": "Checker (dark)",
    "checker_light": "Checker (light)",
    "railroad": "Railroad",
    "ladder": "Ladder",
    "smile": "Smile",
    "airplane": "Airplane",
    "bowtie": "Bowtie",
    "zigzag": "Zigzag",
    "s_curve": "S-curve",
    "window": "Window",
    "cross_corners": "Star burst",
    "ring": "Ring",
    "blackout": "Blackout (cover-all)",
    "top_bottom": "Top & bottom",
    "sides": "Both sides",
    "center_cross_small": "Small plus",
    "hourglass": "Hourglass",
    "lightning": "Lightning",
    "fish": "Fish",
    "mountain": "Mountain",
}


def pick_random_pattern(rng: random.Random | None = None) -> str:
    rng = rng or random
    return rng.choice(sorted(PATTERNS.keys()))


def pattern_cells(pattern_id: str) -> frozenset[tuple[int, int]]:
    return PATTERNS[pattern_id]


def pattern_label(pattern_id: str | None) -> str:
    if not pattern_id:
        return "—"
    return PATTERN_LABELS.get(pattern_id, pattern_id.replace("_", " ").title())


def mode_label(mode: str, pattern_id: str | None = None) -> str:
    if mode == MODE_RANDOM_PATTERN or mode == "pattern":
        return f"Pattern — {pattern_label(pattern_id)}"
    return MODE_LABELS.get(mode, mode)


def ascii_pattern(cells: Iterable[tuple[int, int]]) -> str:
    """Compact emoji grid for Discord embeds."""
    need = set(cells)
    lines = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            if (r, c) == CENTER and (r, c) in need:
                row.append("⭐")
            elif (r, c) in need:
                row.append("⬛")
            else:
                row.append("⬜")
        lines.append("".join(row))
    return "\n".join(lines)


def preview_for_round(mode: str, pattern_id: str | None) -> str:
    if mode == MODE_TRADITIONAL:
        return (
            "Any full **row**, **column**, or **diagonal** wins.\n"
            + ascii_pattern([(2, c) for c in range(5)])  # sample: middle row
            + "\n*(example — middle row)*"
        )
    if mode == MODE_CORNERS:
        return "Mark all **four corners**.\n" + ascii_pattern(CORNERS)
    if mode == MODE_DIAGONALS:
        return (
            "Either main **diagonal** wins.\n"
            + ascii_pattern([(i, i) for i in range(5)])
            + "\n*(example — ↘ diagonal)*"
        )
    if pattern_id and pattern_id in PATTERNS:
        return (
            f"Cover this shape: **{pattern_label(pattern_id)}**\n"
            + ascii_pattern(PATTERNS[pattern_id])
        )
    return "Cover the announced pattern."


def cell_covered(
    grid: list[list[str]],
    marked: list[list[bool]],
    r: int,
    c: int,
) -> bool:
    if marked[r][c]:
        return True
    return grid[r][c].upper() == "FREE"


def has_bingo(
    grid: list[list[str]],
    marked: list[list[bool]],
    mode: str,
    pattern_id: str | None = None,
) -> bool:
    """True if the card satisfies the round's win condition."""
    if mode == MODE_TRADITIONAL:
        lines = TRADITIONAL_LINES
        return any(
            all(cell_covered(grid, marked, r, c) for r, c in line) for line in lines
        )
    if mode == MODE_DIAGONALS:
        return any(
            all(cell_covered(grid, marked, r, c) for r, c in line)
            for line in DIAGONAL_LINES
        )
    if mode == MODE_CORNERS:
        return all(cell_covered(grid, marked, r, c) for r, c in CORNERS)

    # Exact pattern (random_pattern resolves to a pattern_id at start)
    pid = pattern_id
    if not pid or pid not in PATTERNS:
        return False
    return all(cell_covered(grid, marked, r, c) for r, c in PATTERNS[pid])
