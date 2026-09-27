"""
Wizard's Chess board renderer — Pillow PNG for Discord match messages.

Pure rendering helper: no Discord imports. Callers pass a python-chess Board
plus optional last-move / selection / destination highlights.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable, Optional

import chess
from PIL import Image, ImageDraw, ImageFont

SQ = 64
MARGIN = 28
BOARD_PX = SQ * 8
IMG_SIZE = BOARD_PX + MARGIN * 2

LIGHT = (240, 217, 181)
DARK = (181, 136, 99)
COORD = (210, 195, 175)
LAST_MOVE = (246, 246, 105)
SELECTED = (186, 202, 68)
DOT = (40, 40, 40, 110)
RING = (40, 40, 40, 160)
WHITE_FILL = (250, 248, 240)
WHITE_OUTLINE = (40, 35, 30)
BLACK_FILL = (35, 30, 28)
BLACK_OUTLINE = (220, 215, 205)

UNICODE_PIECE = {
    (chess.PAWN, True): "♙", (chess.KNIGHT, True): "♘", (chess.BISHOP, True): "♗",
    (chess.ROOK, True): "♖", (chess.QUEEN, True): "♕", (chess.KING, True): "♔",
    (chess.PAWN, False): "♟", (chess.KNIGHT, False): "♞", (chess.BISHOP, False): "♝",
    (chess.ROOK, False): "♜", (chess.QUEEN, False): "♛", (chess.KING, False): "♚",
}

_FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]
_LABEL_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
]


def _load_font(paths: list[Path], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in paths:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


_PIECE_FONT = _load_font(_FONT_CANDIDATES, 46)
_LABEL_FONT = _load_font(_LABEL_CANDIDATES, 14)


def piece_glyph(piece: chess.Piece) -> str:
    return UNICODE_PIECE[(piece.piece_type, piece.color)]


def _sq_xy(square: int, flip: bool) -> tuple[int, int]:
    file = chess.square_file(square)
    rank = chess.square_rank(square)
    if flip:
        file = 7 - file
        rank = 7 - rank
    # rank 7 at top when not flipped (white at bottom)
    col, row = file, 7 - rank
    return MARGIN + col * SQ, MARGIN + row * SQ


def _draw_piece(draw: ImageDraw.ImageDraw, cx: int, cy: int, piece: chess.Piece) -> None:
    glyph = piece_glyph(piece)
    fill = WHITE_FILL if piece.color == chess.WHITE else BLACK_FILL
    outline = WHITE_OUTLINE if piece.color == chess.WHITE else BLACK_OUTLINE
    # rough outline for contrast on both square colors
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
        draw.text((cx + dx, cy + dy), glyph, font=_PIECE_FONT, fill=outline, anchor="mm")
    draw.text((cx, cy), glyph, font=_PIECE_FONT, fill=fill, anchor="mm")


def render_board(
    board: chess.Board,
    *,
    last_move: Optional[chess.Move] = None,
    selected: Optional[int] = None,
    destinations: Optional[Iterable[int]] = None,
    flip: bool = False,
) -> bytes:
    """Render a board PNG. Returns PNG bytes.

    flip=True puts Black's side at the bottom (use when Black is to move).
    """
    dest_set = set(destinations or ())
    img = Image.new("RGB", (IMG_SIZE, IMG_SIZE), (48, 42, 36))
    draw = ImageDraw.Draw(img, "RGBA")

    for rank in range(8):
        for file in range(8):
            square = chess.square(file, rank)
            x, y = _sq_xy(square, flip)
            base = LIGHT if (file + rank) % 2 == 0 else DARK
            color = base
            if last_move and square in (last_move.from_square, last_move.to_square):
                color = tuple(round((base[i] + LAST_MOVE[i]) / 2) for i in range(3))
            if selected is not None and square == selected:
                color = tuple(round((base[i] + SELECTED[i]) / 2) for i in range(3))
            draw.rectangle([x, y, x + SQ - 1, y + SQ - 1], fill=color)

    # coordinates
    files = "abcdefgh"
    ranks = "12345678"
    for i in range(8):
        file_idx = 7 - i if flip else i
        rank_idx = i if flip else 7 - i
        fx = MARGIN + i * SQ + SQ // 2
        fy = MARGIN + i * SQ + SQ // 2
        draw.text((fx, IMG_SIZE - MARGIN // 2), files[file_idx], font=_LABEL_FONT,
                  fill=COORD, anchor="mm")
        draw.text((MARGIN // 2, fy), ranks[rank_idx], font=_LABEL_FONT,
                  fill=COORD, anchor="mm")

    # destination markers under pieces
    for square in dest_set:
        x, y = _sq_xy(square, flip)
        cx, cy = x + SQ // 2, y + SQ // 2
        if board.piece_at(square):
            r = SQ // 2 - 4
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=RING, width=4)
        else:
            r = 10
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=DOT)

    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if not piece:
            continue
        x, y = _sq_xy(square, flip)
        _draw_piece(draw, x + SQ // 2, y + SQ // 2 + 2, piece)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def render_board_file(
    board: chess.Board,
    path: str | Path,
    **kwargs,
) -> Path:
    """Write a board PNG to disk (smoke tests / debugging)."""
    path = Path(path)
    path.write_bytes(render_board(board, **kwargs))
    return path
