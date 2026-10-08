"""
Wizard's Checkers board renderer — Pillow PNG for Discord match messages.

Pure rendering helper: no Discord imports. Callers pass a piece dict keyed by
(file, rank) plus optional last-move / selection / destination highlights.
Red sits at the bottom (like White in Wizard's Chess).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageDraw, ImageFont

SQ = 96
# Discord mobile shrinks the whole PNG to ~380px wide. Glyph height is ~0.75×
# font size, so we can go to ~124 before stacked ranks overlap. Put spare
# pixels into the left/bottom strips only (no empty top/right).
MARGIN_LEFT = 130
MARGIN_RIGHT = 16
MARGIN_TOP = 16
MARGIN_BOTTOM = 130
# Back-compat alias used by older call sites / mental model.
MARGIN = MARGIN_LEFT
BOARD_PX = SQ * 8
IMG_W = BOARD_PX + MARGIN_LEFT + MARGIN_RIGHT
IMG_H = BOARD_PX + MARGIN_TOP + MARGIN_BOTTOM
IMG_SIZE = max(IMG_W, IMG_H)  # legacy name; render uses IMG_W / IMG_H

LIGHT = (240, 217, 181)
DARK = (181, 136, 99)
COORD = (255, 250, 235)
COORD_ON_LIGHT = (60, 40, 20)
COORD_ON_DARK = (255, 245, 225)
LAST_MOVE = (246, 246, 105)
SELECTED = (186, 202, 68)
DOT = (40, 40, 40, 120)
RING = (40, 40, 40, 170)

RED_FILL = (196, 48, 43)
RED_OUTLINE = (90, 18, 14)
RED_HIGHLIGHT = (255, 180, 160)
BLACK_FILL = (36, 32, 30)
BLACK_OUTLINE = (220, 215, 205)
BLACK_HIGHLIGHT = (90, 86, 82)
CROWN = (232, 196, 80)

# Prefer the TTF shipped in data/fonts — production hosts often lack DejaVu, and
# ImageFont.load_default() without a size is ~10px (looks like empty margins).
_FONT_DIR = Path(__file__).resolve().parent.parent / "data" / "fonts"
_LABEL_CANDIDATES = [
    _FONT_DIR / "Cinzel.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
]


def _load_font(paths: list[Path], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in paths:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


# ~0.75× size ≈ glyph height; 124 stays under one square so ranks don't overlap.
_LABEL_FONT = _load_font(_LABEL_CANDIDATES, 124)
_SQUARE_COORD_FONT = _load_font(_LABEL_CANDIDATES, 52)
_CROWN_FONT = _load_font(_LABEL_CANDIDATES, 28)


def square_name(sq: tuple[int, int]) -> str:
    file, rank = sq
    return f"{chr(ord('a') + file)}{rank + 1}"


def piece_label(piece: str) -> str:
    """Name for Discord select / slash labels (not board art)."""
    kind = "King" if piece.isupper() else "Man"
    color = "Red" if piece.lower() == "r" else "Black"
    return f"{color} {kind}"


def describe_move(piece: str, frm: tuple[int, int], to: tuple[int, int], *,
                  jumped: bool = False, crowned: bool = False) -> str:
    name = piece_label(piece)
    text = f"{name} {square_name(frm)} jumps to {square_name(to)}" if jumped else (
        f"{name} {square_name(frm)} to {square_name(to)}"
    )
    if crowned:
        text += ", crowned"
    return text


def _sq_xy(sq: tuple[int, int]) -> tuple[int, int]:
    file, rank = sq
    # Red at bottom: rank 0 on the bottom row.
    col, row = file, 7 - rank
    return MARGIN_LEFT + col * SQ, MARGIN_TOP + row * SQ


def _draw_piece(draw: ImageDraw.ImageDraw, cx: int, cy: int, piece: str) -> None:
    red = piece.lower() == "r"
    fill = RED_FILL if red else BLACK_FILL
    outline = RED_OUTLINE if red else BLACK_OUTLINE
    highlight = RED_HIGHLIGHT if red else BLACK_HIGHLIGHT
    r = int(SQ * 0.34)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=outline, width=4)
    # Inner ring for a wooden-disc look.
    inner = int(r * 0.62)
    draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], outline=highlight, width=3)
    if piece.isupper():
        draw.ellipse([cx - r + 8, cy - r + 8, cx + r - 8, cy + r - 8], outline=CROWN, width=3)
        draw.text((cx, cy), "K", font=_CROWN_FONT, fill=CROWN, anchor="mm")


def render_board(
    board: dict[tuple[int, int], str],
    *,
    last_from: Optional[tuple[int, int]] = None,
    last_to: Optional[tuple[int, int]] = None,
    selected: Optional[tuple[int, int]] = None,
    destinations: Optional[Iterable[tuple[int, int]]] = None,
) -> bytes:
    """Render a checkers board PNG. Returns PNG bytes."""
    dest_set = set(destinations or ())
    img = Image.new("RGB", (IMG_W, IMG_H), (48, 42, 36))
    draw = ImageDraw.Draw(img, "RGBA")

    files = "abcdefgh"
    ranks = "12345678"

    for rank in range(8):
        for file in range(8):
            sq = (file, rank)
            x, y = _sq_xy(sq)
            # Dark playable squares match is_dark: (file + rank) % 2 == 0
            base = DARK if (file + rank) % 2 == 0 else LIGHT
            color = base
            if last_from == sq or last_to == sq:
                color = tuple(round((base[i] + LAST_MOVE[i]) / 2) for i in range(3))
            if selected is not None and sq == selected:
                color = tuple(round((base[i] + SELECTED[i]) / 2) for i in range(3))
            draw.rectangle([x, y, x + SQ - 1, y + SQ - 1], fill=color)

            col, row = file, 7 - rank
            ink = COORD_ON_DARK if (file + rank) % 2 == 0 else COORD_ON_LIGHT
            if col == 0:
                draw.text((x + 5, y + 3), ranks[rank], font=_SQUARE_COORD_FONT, fill=ink,
                          anchor="lt", stroke_width=1, stroke_fill=ink)
            if row == 7:
                draw.text((x + SQ - 5, y + SQ - 3), files[file], font=_SQUARE_COORD_FONT,
                          fill=ink, anchor="rb", stroke_width=1, stroke_fill=ink)

    for i in range(8):
        fx = MARGIN_LEFT + i * SQ + SQ // 2
        fy = MARGIN_TOP + i * SQ + SQ // 2
        # Stroke keeps glyphs crisp after Discord recompresses / downscales.
        draw.text((fx, IMG_H - MARGIN_BOTTOM // 2), files[i], font=_LABEL_FONT,
                  fill=COORD, anchor="mm", stroke_width=5, stroke_fill=(20, 16, 12))
        draw.text((MARGIN_LEFT // 2, fy), ranks[7 - i], font=_LABEL_FONT,
                  fill=COORD, anchor="mm", stroke_width=5, stroke_fill=(20, 16, 12))

    for sq in dest_set:
        x, y = _sq_xy(sq)
        cx, cy = x + SQ // 2, y + SQ // 2
        if sq in board:
            r = SQ // 2 - 5
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=RING, width=5)
        else:
            r = 12
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=DOT)

    for sq, piece in board.items():
        x, y = _sq_xy(sq)
        _draw_piece(draw, x + SQ // 2, y + SQ // 2, piece)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def render_board_file(
    board: dict[tuple[int, int], str],
    path: str | Path,
    **kwargs,
) -> Path:
    """Write a board PNG to disk (smoke tests / debugging)."""
    path = Path(path)
    path.write_bytes(render_board(board, **kwargs))
    return path
