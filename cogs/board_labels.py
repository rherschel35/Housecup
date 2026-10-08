"""
Bold a–h / 1–8 glyphs for board PNGs.

Production hosts often lack system fonts; Pillow's unsized default is ~10px.
These bitmaps scale to any pixel size so outer coords stay readable on Discord.
"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageDraw, ImageFont

# 5×7 on-bits (rows top→bottom). Enough for files a–h and ranks 1–8.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "..##.", ".#...", "#....", "#####"),
    "3": (".###.", "#...#", "....#", "..##.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": (".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "a": (".....", ".....", ".###.", "....#", ".####", "#...#", ".####"),
    "b": ("#....", "#....", "####.", "#...#", "#...#", "#...#", "####."),
    "c": (".....", ".....", ".###.", "#....", "#....", "#....", ".###."),
    "d": ("....#", "....#", ".####", "#...#", "#...#", "#...#", ".####"),
    "e": (".....", ".....", ".###.", "#...#", "#####", "#....", ".###."),
    "f": ("..##.", ".#..#", ".#...", "###..", ".#...", ".#...", ".#..."),
    "g": (".....", ".....", ".####", "#...#", ".####", "....#", ".###."),
    "h": ("#....", "#....", "####.", "#...#", "#...#", "#...#", "#...#"),
}

_FONT_DIR = Path(__file__).resolve().parent.parent / "data" / "fonts"
_COG_FONT_DIR = Path(__file__).resolve().parent / "fonts"
_FONT_CANDIDATES = [
    _FONT_DIR / "Cinzel.ttf",
    _COG_FONT_DIR / "Cinzel.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
]


def load_coord_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """TrueType for in-square coords; falls back to a sized default."""
    for path in _FONT_CANDIDATES:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def draw_coord(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    ch: str,
    *,
    pixel: int,
    fill: tuple[int, int, int],
    stroke_fill: tuple[int, int, int] = (20, 16, 12),
) -> None:
    """Draw one centered a–h / 1–8 glyph. `pixel` is the on-bit cell size."""
    pattern = _GLYPHS.get(ch)
    if pattern is None:
        return
    rows = len(pattern)
    cols = len(pattern[0])
    # Slight vertical trim — 7 rows of `pixel` should fit under one board square.
    w = cols * pixel
    h = rows * pixel
    cx, cy = xy
    x0 = int(cx - w / 2)
    y0 = int(cy - h / 2)
    # Fat outline then fill so Discord recompression doesn't eat strokes.
    for dy, row in enumerate(pattern):
        for dx, bit in enumerate(row):
            if bit != "#":
                continue
            x = x0 + dx * pixel
            y = y0 + dy * pixel
            draw.rectangle([x - 2, y - 2, x + pixel + 1, y + pixel + 1], fill=stroke_fill)
    for dy, row in enumerate(pattern):
        for dx, bit in enumerate(row):
            if bit != "#":
                continue
            x = x0 + dx * pixel
            y = y0 + dy * pixel
            draw.rectangle([x, y, x + pixel - 1, y + pixel - 1], fill=fill)
