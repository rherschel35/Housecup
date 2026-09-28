"""
Wizard's Chess board renderer — Pillow PNG for Discord match messages.

Pure rendering helper: no Discord imports. Callers pass a python-chess Board
plus optional last-move / selection / destination highlights.

Pieces are drawn as vector silhouettes (not Unicode glyphs) so the board
stays readable even when the host has no chess-capable font installed.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable, Optional

import chess
from PIL import Image, ImageDraw, ImageFont

SQ = 80
MARGIN = 34
BOARD_PX = SQ * 8
IMG_SIZE = BOARD_PX + MARGIN * 2

LIGHT = (240, 217, 181)
DARK = (181, 136, 99)
COORD = (220, 205, 185)
LAST_MOVE = (246, 246, 105)
SELECTED = (186, 202, 68)
DOT = (40, 40, 40, 120)
RING = (40, 40, 40, 170)
WHITE_FILL = (250, 248, 240)
WHITE_OUTLINE = (30, 26, 22)
BLACK_FILL = (28, 24, 22)
BLACK_OUTLINE = (235, 230, 220)

# Text labels as a mobile-friendly backup cue under the silhouette.
PIECE_LETTER = {
    chess.PAWN: "P",
    chess.KNIGHT: "N",
    chess.BISHOP: "B",
    chess.ROOK: "R",
    chess.QUEEN: "Q",
    chess.KING: "K",
}

_LABEL_CANDIDATES = [
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
    return ImageFont.load_default()


_LABEL_FONT = _load_font(_LABEL_CANDIDATES, 16)


def piece_glyph(piece: chess.Piece) -> str:
    """Short letter used in slash-command choice labels (not board art)."""
    return PIECE_LETTER[piece.piece_type]


def _sq_xy(square: int, flip: bool) -> tuple[int, int]:
    file = chess.square_file(square)
    rank = chess.square_rank(square)
    if flip:
        file = 7 - file
        rank = 7 - rank
    # rank 7 at top when not flipped (white at bottom)
    col, row = file, 7 - rank
    return MARGIN + col * SQ, MARGIN + row * SQ


def _poly(cx: int, cy: int, scale: float, points: list[tuple[float, float]]) -> list[tuple[int, int]]:
    return [(int(cx + x * scale), int(cy + y * scale)) for x, y in points]


def _draw_piece(draw: ImageDraw.ImageDraw, cx: int, cy: int, piece: chess.Piece) -> None:
    fill = WHITE_FILL if piece.color == chess.WHITE else BLACK_FILL
    outline = WHITE_OUTLINE if piece.color == chess.WHITE else BLACK_OUTLINE
    scale = SQ * 0.46
    # Local coords: (0,0) center; y positive down.
    cy = cy + int(SQ * 0.02)

    def shape(points: list[tuple[float, float]], width: int = 3) -> None:
        pts = _poly(cx, cy, scale, points)
        draw.polygon(pts, fill=fill, outline=outline)
        draw.line(pts + [pts[0]], fill=outline, width=width, joint="curve")

    def ellipse(x0, y0, x1, y1, width: int = 3) -> None:
        box = [
            int(cx + x0 * scale), int(cy + y0 * scale),
            int(cx + x1 * scale), int(cy + y1 * scale),
        ]
        draw.ellipse(box, fill=fill, outline=outline, width=width)

    def rect(x0, y0, x1, y1, width: int = 3) -> None:
        box = [
            int(cx + x0 * scale), int(cy + y0 * scale),
            int(cx + x1 * scale), int(cy + y1 * scale),
        ]
        draw.rectangle(box, fill=fill, outline=outline, width=width)

    t = piece.piece_type
    if t == chess.PAWN:
        ellipse(-0.30, -0.72, 0.30, -0.12)
        shape([(-0.40, -0.08), (0.40, -0.08), (0.50, 0.52), (-0.50, 0.52)])
        rect(-0.58, 0.52, 0.58, 0.78)
    elif t == chess.ROOK:
        shape([
            (-0.55, -0.70), (-0.28, -0.70), (-0.28, -0.42), (-0.08, -0.42),
            (-0.08, -0.70), (0.08, -0.70), (0.08, -0.42), (0.28, -0.42),
            (0.28, -0.70), (0.55, -0.70), (0.55, -0.18), (0.42, -0.02),
            (0.42, 0.52), (-0.42, 0.52), (-0.42, -0.02), (-0.55, -0.18),
        ])
        rect(-0.60, 0.52, 0.60, 0.78)
    elif t == chess.KNIGHT:
        shape([
            (-0.48, 0.52), (-0.42, 0.02), (-0.58, -0.18), (-0.38, -0.55),
            (-0.05, -0.78), (0.28, -0.55), (0.50, -0.22), (0.58, 0.08),
            (0.35, 0.18), (0.18, -0.02), (0.02, 0.22), (0.38, 0.52),
        ])
        draw.ellipse([
            int(cx - 0.14 * scale), int(cy - 0.45 * scale),
            int(cx + 0.02 * scale), int(cy - 0.28 * scale),
        ], fill=outline)
        rect(-0.58, 0.52, 0.58, 0.78)
    elif t == chess.BISHOP:
        ellipse(-0.10, -0.90, 0.10, -0.68)
        ellipse(-0.34, -0.70, 0.34, -0.02)
        draw.line([
            (int(cx - 0.10 * scale), int(cy - 0.55 * scale)),
            (int(cx + 0.14 * scale), int(cy - 0.22 * scale)),
        ], fill=outline, width=3)
        shape([(-0.45, 0.00), (0.45, 0.00), (0.52, 0.52), (-0.52, 0.52)])
        rect(-0.58, 0.52, 0.58, 0.78)
    elif t == chess.QUEEN:
        shape([
            (-0.55, -0.12), (-0.62, -0.68), (-0.32, -0.28), (-0.18, -0.78),
            (0.00, -0.28), (0.18, -0.78), (0.32, -0.28), (0.62, -0.68),
            (0.55, -0.12), (0.50, 0.52), (-0.50, 0.52),
        ])
        for px in (-0.62, -0.18, 0.18, 0.62):
            ellipse(px - 0.11, -0.88, px + 0.11, -0.66)
        ellipse(-0.12, -0.40, 0.12, -0.16)
        rect(-0.58, 0.52, 0.58, 0.78)
    elif t == chess.KING:
        shape([
            (-0.52, -0.08), (-0.58, -0.48), (-0.28, -0.25), (-0.22, -0.58),
            (0.22, -0.58), (0.28, -0.25), (0.58, -0.48), (0.52, -0.08),
            (0.48, 0.52), (-0.48, 0.52),
        ])
        draw.line([
            (cx, int(cy - 0.92 * scale)),
            (cx, int(cy - 0.48 * scale)),
        ], fill=outline, width=5)
        draw.line([
            (int(cx - 0.20 * scale), int(cy - 0.72 * scale)),
            (int(cx + 0.20 * scale), int(cy - 0.72 * scale)),
        ], fill=outline, width=5)
        rect(-0.58, 0.52, 0.58, 0.78)


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
            r = SQ // 2 - 5
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=RING, width=5)
        else:
            r = 12
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=DOT)

    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if not piece:
            continue
        x, y = _sq_xy(square, flip)
        _draw_piece(draw, x + SQ // 2, y + SQ // 2, piece)

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
