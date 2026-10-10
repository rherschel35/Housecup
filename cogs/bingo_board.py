"""
Wizard Bingo card renderer — Pillow PNG for Discord.

5×5 grid with short labels. Marked cells get a gold wash + check;
called-but-unmarked cells get a soft highlight so players know what to tap.
"""

from __future__ import annotations

import io
import textwrap
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from cogs.board_labels import load_coord_font

CELL = 118
PAD = 18
HEADER_H = 56
FOOTER_H = 36
COLS = 5
ROWS = 5

INK = (28, 24, 40)
INK_DIM = (90, 84, 110)
GOLD = (212, 168, 74)
GOLD_SOFT = (255, 214, 140)
MARKED_FILL = (72, 52, 18)
MARKED_BORDER = (240, 196, 110)
CALLED_FILL = (48, 42, 72)
CALLED_BORDER = (150, 140, 190)
EMPTY_FILL = (36, 32, 52)
EMPTY_BORDER = (70, 64, 92)
FREE_FILL = (54, 48, 28)
BG = (18, 16, 28)
TITLE = (248, 240, 220)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    return load_coord_font(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    # Hard-cap for tiny cells
    if len(lines) > 3:
        joined = " ".join(words)
        return textwrap.wrap(joined, width=max(6, len(joined) // 3 + 1))[:3]
    return lines


def render_card(
    *,
    grid: Sequence[Sequence[str]],
    marked: Sequence[Sequence[bool]],
    called: set[str] | None = None,
    player_name: str = "",
    round_label: str = "Wizard Bingo",
) -> bytes:
    """Return PNG bytes for one player's bingo card."""
    called = called or set()
    w = PAD * 2 + CELL * COLS
    h = PAD * 2 + HEADER_H + CELL * ROWS + FOOTER_H
    img = Image.new("RGBA", (w, h), BG + (255,))
    d = ImageDraw.Draw(img)

    title_font = _font(28)
    sub_font = _font(16)
    cell_font = _font(15)
    tiny_font = _font(13)

    d.text((PAD, 14), round_label, font=title_font, fill=TITLE)
    if player_name:
        d.text((PAD, 42), player_name[:40], font=sub_font, fill=INK_DIM)

    ox, oy = PAD, PAD + HEADER_H
    for r in range(ROWS):
        for c in range(COLS):
            x0 = ox + c * CELL
            y0 = oy + r * CELL
            x1, y1 = x0 + CELL - 4, y0 + CELL - 4
            label = grid[r][c]
            is_free = label.upper() == "FREE"
            is_marked = bool(marked[r][c]) or is_free
            is_called = label in called and not is_free

            if is_marked:
                fill, border = MARKED_FILL, MARKED_BORDER
            elif is_called:
                fill, border = CALLED_FILL, CALLED_BORDER
            elif is_free:
                fill, border = FREE_FILL, GOLD
            else:
                fill, border = EMPTY_FILL, EMPTY_BORDER

            d.rounded_rectangle([x0, y0, x1, y1], radius=12, fill=fill, outline=border, width=2)

            lines = _wrap(d, label, cell_font, CELL - 18)
            total_h = len(lines) * 16
            ty = y0 + (CELL - 4 - total_h) // 2 - 2
            for i, line in enumerate(lines):
                tw = d.textlength(line, font=cell_font)
                d.text(
                    (x0 + (CELL - 4 - tw) / 2, ty + i * 16),
                    line,
                    font=cell_font,
                    fill=GOLD_SOFT if is_marked else TITLE,
                )
            if is_marked and not is_free:
                # Hand-drawn tick — Cinzel has no ✓ glyph.
                cx, cy = x1 - 14, y0 + 14
                d.line([(cx - 6, cy), (cx - 2, cy + 5), (cx + 7, cy - 6)], fill=GOLD, width=3)

    marks = sum(1 for r in range(ROWS) for c in range(COLS) if marked[r][c] or grid[r][c].upper() == "FREE")
    footer = f"{marks}/25 · called squares glow · FREE center counts"
    d.text((PAD, h - FOOTER_H), footer, font=tiny_font, fill=INK_DIM)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
