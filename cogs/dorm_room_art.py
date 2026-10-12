"""Compose a dorm-room portrait from placed decor + frog shelf cards."""

from __future__ import annotations

import colorsys
import hashlib
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ART_DIR = Path(__file__).resolve().parent.parent / "dorm_art_assets"
BASE_PATH = ART_DIR / "room_base.jpg"
FROG_ART_DIR = Path(__file__).resolve().parent.parent / "frog_art_assets"

# Normalized boxes on the 16:9 base (x0, y0, x1, y1) in 0..1.
SLOT_BOX = {
    "bed": (0.04, 0.42, 0.36, 0.88),
    "wall": (0.08, 0.08, 0.34, 0.38),
    "desk": (0.38, 0.48, 0.62, 0.82),
    "shelf": (0.38, 0.10, 0.62, 0.42),
    "window": (0.66, 0.08, 0.96, 0.72),
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


def _emoji_font(size: int) -> ImageFont.ImageFont | None:
    path = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
    if Path(path).is_file():
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            return None
    return None


def _color_for(key: str) -> tuple[int, int, int]:
    dig = hashlib.sha256(key.encode()).digest()
    h = dig[0] / 255.0
    s = 0.35 + (dig[1] / 255.0) * 0.35
    v = 0.45 + (dig[2] / 255.0) * 0.35
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def _box_px(size: tuple[int, int], slot: str) -> tuple[int, int, int, int]:
    w, h = size
    x0, y0, x1, y1 = SLOT_BOX[slot]
    return (
        int(x0 * w),
        int(y0 * h),
        int(x1 * w),
        int(y1 * h),
    )


def _rounded(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=None, width: int = 1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _paste_cover(base: Image.Image, overlay: Image.Image, box: tuple[int, int, int, int], *, alpha: int = 220):
    x0, y0, x1, y1 = box
    tw, th = max(1, x1 - x0), max(1, y1 - y0)
    fitted = overlay.convert("RGBA").resize((tw, th), Image.Resampling.LANCZOS)
    if fitted.mode != "RGBA":
        fitted = fitted.convert("RGBA")
    # Soft edge so props sit in the room.
    mask = Image.new("L", (tw, th), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((2, 2, tw - 3, th - 3), radius=max(8, min(tw, th) // 10), fill=alpha)
    mask = mask.filter(ImageFilter.GaussianBlur(1.2))
    base.paste(fitted, (x0, y0), mask)


def _draw_plaque(
    img: Image.Image,
    box: tuple[int, int, int, int],
    *,
    title: str,
    subtitle: str,
    accent: tuple[int, int, int],
    emoji: str = "",
):
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # Bottom-anchored name plate inside the slot.
    pw = max(90, int((x1 - x0) * 0.92))
    ph = 44
    px0 = x0 + ((x1 - x0) - pw) // 2
    py1 = y1 - 8
    py0 = py1 - ph
    _rounded(d, (px0, py0, px0 + pw, py1), 10, (*accent, 200), outline=(245, 230, 200, 220), width=2)
    # Soft fill strip above plaque tinting the furniture zone.
    tint = Image.new("RGBA", (x1 - x0, y1 - y0), (*accent, 55))
    img.paste(tint, (x0, y0), tint)

    font = _font(16)
    small = _font(12, bold=False)
    label = title if len(title) <= 22 else title[:21] + "…"
    d.text((px0 + 10, py0 + 6), label, font=font, fill=(250, 244, 230, 255))
    if subtitle:
        d.text((px0 + 10, py0 + 24), subtitle[:28], font=small, fill=(230, 220, 200, 230))

    ef = _emoji_font(28)
    if emoji and ef is not None:
        try:
            d.text((px0 + pw - 36, py0 + 6), emoji, font=ef, embedded_color=True)
        except Exception:
            pass

    img.alpha_composite(layer)


def _frog_thumb(card_id: str) -> Image.Image | None:
    # Prefer composed card art filenames used by frog_card_art.
    try:
        from cogs.frog_card_art import ART_FILES
    except Exception:
        ART_FILES = {}
    name = ART_FILES.get(card_id)
    if name:
        path = FROG_ART_DIR / name
        if path.is_file():
            return Image.open(path).convert("RGB")
    for cand in FROG_ART_DIR.glob(f"*{card_id}*"):
        if cand.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            return Image.open(cand).convert("RGB")
    return None


def _paint_bed(img: Image.Image, box, accent, name: str, emoji: str):
    x0, y0, x1, y1 = box
    quilt = Image.new("RGBA", (x1 - x0, max(1, int((y1 - y0) * 0.45))), (*accent, 180))
    qd = ImageDraw.Draw(quilt)
    # Patchwork tiles
    tw, th = quilt.size
    cols, rows = 4, 3
    cw, rh = tw // cols, th // rows
    for r in range(rows):
        for c in range(cols):
            shade = 30 * ((r + c) % 3 - 1)
            col = tuple(max(0, min(255, accent[i] + shade)) for i in range(3))
            qd.rectangle((c * cw, r * rh, (c + 1) * cw - 1, (r + 1) * rh - 1), fill=(*col, 200))
    img.paste(quilt, (x0, y0 + int((y1 - y0) * 0.35)), quilt)
    _draw_plaque(img, box, title=name, subtitle="Bed", accent=accent, emoji=emoji)


def _paint_wall(img: Image.Image, box, accent, name: str, emoji: str):
    x0, y0, x1, y1 = box
    banner = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    d = ImageDraw.Draw(banner)
    d.rounded_rectangle((8, 4, x1 - x0 - 8, y1 - y0 - 4), radius=8, fill=(*accent, 200))
    d.rectangle((int((x1 - x0) * 0.45), 0, int((x1 - x0) * 0.55), 10), fill=(200, 180, 120, 230))
    img.paste(banner, (x0, y0), banner)
    _draw_plaque(img, box, title=name, subtitle="Wall", accent=accent, emoji=emoji)


def _paint_desk(img: Image.Image, box, accent, name: str, emoji: str):
    x0, y0, x1, y1 = box
    tray = Image.new("RGBA", (x1 - x0, max(1, int((y1 - y0) * 0.35))), (*accent, 170))
    td = ImageDraw.Draw(tray)
    td.ellipse((10, 6, 40, 34), fill=(245, 240, 220, 220))
    td.rectangle((48, 12, tray.size[0] - 12, 28), fill=(40, 28, 20, 200))
    img.paste(tray, (x0, y0 + int((y1 - y0) * 0.2)), tray)
    _draw_plaque(img, box, title=name, subtitle="Desk", accent=accent, emoji=emoji)


def _paint_shelf(img: Image.Image, box, accent, name: str, emoji: str, frog_art: Image.Image | None = None):
    x0, y0, x1, y1 = box
    if frog_art is not None:
        # Mini card on the shelf.
        tw = max(40, int((x1 - x0) * 0.35))
        th = int(tw * 1.35)
        card = frog_art.resize((tw, th), Image.Resampling.LANCZOS).convert("RGBA")
        cx = x0 + (x1 - x0 - tw) // 2
        cy = y0 + 8
        img.paste(card, (cx, cy), card)
    else:
        jar = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        d = ImageDraw.Draw(jar)
        d.rounded_rectangle((20, 10, x1 - x0 - 20, y1 - y0 - 18), radius=10, fill=(*accent, 160))
        img.paste(jar, (x0, y0), jar)
    _draw_plaque(img, box, title=name, subtitle="Shelf", accent=accent, emoji=emoji)


def _paint_window(img: Image.Image, box, accent, name: str, emoji: str):
    x0, y0, x1, y1 = box
    pane = Image.new("RGBA", (x1 - x0, y1 - y0), (*accent, 70))
    # Soft rain streaks / glow.
    d = ImageDraw.Draw(pane)
    for i in range(12):
        xx = 8 + i * max(1, (x1 - x0) // 12)
        d.line((xx, 10, xx + 4, y1 - y0 - 10), fill=(200, 220, 255, 50), width=1)
    img.paste(pane, (x0, y0), pane)
    _draw_plaque(img, box, title=name, subtitle="Window", accent=accent, emoji=emoji)


_PAINTERS = {
    "bed": _paint_bed,
    "wall": _paint_wall,
    "desk": _paint_desk,
    "shelf": _paint_shelf,
    "window": _paint_window,
}


def render_room_png(
    slots: dict,
    *,
    decor_lookup: dict,
    frog_meta_lookup=None,
) -> bytes | None:
    """
    slots: slot -> {"kind": "decor"|"frog", "id": ...}
    decor_lookup: decor_id -> decor meta dict (name/emoji/slot)
    frog_meta_lookup: optional callable(card_id) -> meta or None
    """
    if not BASE_PATH.is_file():
        return None
    base = Image.open(BASE_PATH).convert("RGBA")

    for slot in ("bed", "wall", "desk", "shelf", "window"):
        placed = (slots or {}).get(slot)
        box = _box_px(base.size, slot)
        if not placed:
            # Dim empty slot hint.
            empty = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 35))
            base.paste(empty, (box[0], box[1]), empty)
            continue

        kind = placed.get("kind")
        pid = placed.get("id")
        if kind == "decor" and pid in decor_lookup:
            meta = decor_lookup[pid]
            accent = _color_for(pid)
            name = meta.get("name", pid)
            emoji = meta.get("emoji", "")
            painter = _PAINTERS.get(slot, _paint_desk)
            if slot == "shelf":
                painter(base, box, accent, name, emoji, frog_art=None)
            else:
                painter(base, box, accent, name, emoji)
        elif kind == "frog":
            meta = frog_meta_lookup(pid) if callable(frog_meta_lookup) else None
            name = (meta or {}).get("name", "Frog card")
            emoji = (meta or {}).get("emoji", "🃏")
            accent = _color_for(f"frog:{pid}")
            thumb = _frog_thumb(pid)
            _paint_shelf(base, box, accent, name, emoji, frog_art=thumb)
        else:
            accent = (80, 70, 60)
            _draw_plaque(base, box, title="Oddity", subtitle=slot.title(), accent=accent)

    # Warm vignette so the collage reads as one photo.
    vignette = Image.new("RGBA", base.size, (0, 0, 0, 0))
    vd = ImageDraw.Draw(vignette)
    w, h = base.size
    vd.rectangle((0, 0, w, h), fill=(20, 12, 8, 40))
    hole = Image.new("L", base.size, 0)
    hd = ImageDraw.Draw(hole)
    hd.ellipse((int(w * 0.08), int(h * 0.05), int(w * 0.92), int(h * 0.95)), fill=255)
    hole = hole.filter(ImageFilter.GaussianBlur(40))
    clear = Image.new("RGBA", base.size, (0, 0, 0, 0))
    vignette = Image.composite(clear, vignette, hole)
    base = Image.alpha_composite(base, vignette)

    out = io.BytesIO()
    base.convert("RGB").save(out, format="JPEG", quality=86, optimize=True)
    return out.getvalue()
