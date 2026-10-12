"""Dorm-room portrait for /room view.

Uses the painted showcase room as the image. Current placements are listed in
a slim caption strip *under* the art — never as boxes over the painting.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ART_DIR = Path(__file__).resolve().parent.parent / "dorm_art_assets"
BASE_PATH = ART_DIR / "room_base.jpg"

SLOT_ORDER = ("bed", "wall", "desk", "shelf", "window")
SLOT_LABEL = {
    "bed": "Bed",
    "wall": "Wall",
    "desk": "Desk",
    "shelf": "Shelf",
    "window": "Window",
}


def _font(size: int, *, bold: bool = True) -> ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates += [
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    candidates += [
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for name in candidates:
        if Path(name).is_file():
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                pass
    return ImageFont.load_default()


def _slot_label(
    slot: str,
    placed: dict | None,
    *,
    decor_lookup: dict,
    frog_meta_lookup,
) -> str:
    title = SLOT_LABEL.get(slot, slot.title())
    if not placed:
        return f"{title}: empty"
    kind = placed.get("kind")
    pid = placed.get("id")
    if kind == "decor" and pid in decor_lookup:
        name = decor_lookup[pid].get("name", pid)
        return f"{title}: {name}"
    if kind == "frog":
        meta = frog_meta_lookup(pid) if callable(frog_meta_lookup) else None
        name = (meta or {}).get("name", "Frog card")
        return f"{title}: {name}"
    return f"{title}: …"


def render_room_png(
    slots: dict,
    *,
    decor_lookup: dict,
    frog_meta_lookup=None,
) -> bytes | None:
    """Return the showcase dorm portrait + caption strip of current placements."""
    if not BASE_PATH.is_file():
        return None

    art = Image.open(BASE_PATH).convert("RGB")
    w, h = art.size

    lines = [
        _slot_label(
            slot,
            (slots or {}).get(slot),
            decor_lookup=decor_lookup,
            frog_meta_lookup=frog_meta_lookup,
        )
        for slot in SLOT_ORDER
    ]
    caption = "  ·  ".join(lines)

    font = _font(18)
    # Measure / wrap caption to two lines max.
    draw_probe = ImageDraw.Draw(art)
    max_w = w - 48
    if draw_probe.textlength(caption, font=font) <= max_w:
        caption_lines = [caption]
    else:
        # Split near the middle separator.
        parts = caption.split("  ·  ")
        mid = (len(parts) + 1) // 2
        caption_lines = [
            "  ·  ".join(parts[:mid]),
            "  ·  ".join(parts[mid:]),
        ]

    strip_h = 28 + 22 * len(caption_lines)
    out = Image.new("RGB", (w, h + strip_h), (28, 20, 14))
    out.paste(art, (0, 0))
    d = ImageDraw.Draw(out)
    # Gold hairline under the painting.
    d.line((24, h + 1, w - 24, h + 1), fill=(196, 168, 110), width=1)
    y = h + 10
    for line in caption_lines:
        tw = d.textlength(line, font=font)
        d.text(((w - tw) / 2, y), line, font=font, fill=(236, 224, 200))
        y += 22

    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()
