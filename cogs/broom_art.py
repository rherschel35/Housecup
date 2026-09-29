"""
Procedural broom portraits. Every shaft × bristles × binding combo gets a
unique look from its parts' colours and shapes — no image files required.
"""

from __future__ import annotations

import hashlib
import io
import math
import random

from PIL import Image, ImageDraw, ImageFilter

W, H = 640, 640
S = 2  # draw at 2x, then scale down


def _hx(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _rgba(rgb, a=255):
    return (*rgb, a)


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _bg(rng: random.Random) -> Image.Image:
    top = _hx(rng.choice(["#1A1410", "#121820", "#1C1218", "#101818"]))
    bot = _hx(rng.choice(["#2A2018", "#1C2830", "#2A1820", "#182828"]))
    img = Image.new("RGB", (W * S, H * S))
    px = img.load()
    for y in range(H * S):
        t = y / (H * S)
        col = _lerp(top, bot, t)
        for x in range(W * S):
            px[x, y] = col
    return img.convert("RGBA")


def _shaft_poly(cx, cy, length, thick, angle, tip_taper=0.55):
    """Rectangle-ish shaft along an angle, tapered at the tip (handle end)."""
    ca, sa = math.cos(angle), math.sin(angle)
    # bristle end is +length/2, handle is -length/2
    pts = []
    for along, half in ((-0.5, thick * tip_taper / 2), (0.5, thick / 2)):
        px = cx + ca * along * length
        py = cy + sa * along * length
        # perpendicular
        nx, ny = -sa, ca
        pts.append((px + nx * half, py + ny * half))
        pts.append((px - nx * half, py - ny * half))
    # order as quad: tip-left, tip-right, butt-right, butt-left → reorder
    return [pts[0], pts[1], pts[3], pts[2]]


def _bristle_bundle(draw, ox, oy, angle, count, length, color, accent, style, rng):
    ca, sa = math.cos(angle), math.sin(angle)
    nx, ny = -sa, ca
    for i in range(count):
        t = (i / max(1, count - 1)) - 0.5
        spread = 22 * S + rng.uniform(-4, 4) * S
        # flare by style
        if style == "tight":
            spread *= 0.65
            length_i = length * rng.uniform(0.85, 1.0)
        elif style == "wild":
            spread *= 1.35
            length_i = length * rng.uniform(0.7, 1.15)
        elif style == "fan":
            spread *= 1.5
            length_i = length * (0.75 + 0.25 * (1 - abs(t) * 2))
        else:  # soft
            spread *= 1.0
            length_i = length * rng.uniform(0.9, 1.05)
        bx = ox + nx * t * spread * 2
        by = oy + ny * t * spread * 2
        tip_x = bx + ca * length_i + nx * t * spread * 0.4 + rng.uniform(-2, 2) * S
        tip_y = by + sa * length_i + ny * t * spread * 0.4 + rng.uniform(-2, 2) * S
        col = color if rng.random() > 0.25 else accent
        width = max(2, int((3.2 - abs(t) * 1.5) * S))
        draw.line([(bx, by), (tip_x, tip_y)], fill=_rgba(col, 230), width=width)


def render(shaft: dict, bristles: dict, binding: dict, seed: str) -> bytes:
    """Render a broom portrait for this combo. Deterministic for the same seed."""
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))
    base = _bg(rng)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    angle = math.radians(-55 + rng.uniform(-6, 6))
    cx, cy = W * S // 2 + 10 * S, H * S // 2 + 20 * S
    shaft_len = 340 * S
    shaft_thick = 18 * S

    shaft_col = _hx(shaft["color"])
    grain = _hx(shaft.get("grain", shaft["color"]))
    bristle_col = _hx(bristles["color"])
    bristle_acc = _hx(bristles.get("accent", bristles["color"]))
    bind_col = _hx(binding["color"])

    # soft ground glow
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([cx - 180 * S, cy + 40 * S, cx + 200 * S, cy + 160 * S],
               fill=_rgba(bristle_col, 40))
    glow = glow.filter(ImageFilter.GaussianBlur(30 * S // 2))
    base.alpha_composite(glow)

    # shaft body
    poly = _shaft_poly(cx, cy, shaft_len, shaft_thick, angle)
    d.polygon(poly, fill=_rgba(shaft_col))
    # grain line
    ca, sa = math.cos(angle), math.sin(angle)
    for k in range(3):
        off = (k - 1) * 3 * S
        nx, ny = -sa * off, ca * off
        d.line([
            (cx - ca * shaft_len * 0.42 + nx, cy - sa * shaft_len * 0.42 + ny),
            (cx + ca * shaft_len * 0.35 + nx, cy + sa * shaft_len * 0.35 + ny),
        ], fill=_rgba(grain, 90), width=max(1, S))

    # handle knob
    hx = cx - ca * shaft_len * 0.48
    hy = cy - sa * shaft_len * 0.48
    r = int(11 * S)
    d.ellipse([hx - r, hy - r, hx + r, hy + r], fill=_rgba(grain))

    # binding wraps near bristle end
    bx = cx + ca * shaft_len * 0.28
    by = cy + sa * shaft_len * 0.28
    for i in range(5):
        t = i / 4
        px = bx - ca * (8 * S) * t
        py = by - sa * (8 * S) * t
        half = shaft_thick * 0.7
        nx, ny = -sa, ca
        d.line([
            (px + nx * half, py + ny * half),
            (px - nx * half, py - ny * half),
        ], fill=_rgba(bind_col, 240), width=max(2, int(2.5 * S)))

    # bristles fan from butt end
    ox = cx + ca * shaft_len * 0.42
    oy = cy + sa * shaft_len * 0.42
    style = bristles.get("style", "soft")
    count = {"tight": 28, "soft": 36, "fan": 42, "wild": 48}.get(style, 36)
    _bristle_bundle(d, ox, oy, angle, count, 150 * S, bristle_col, bristle_acc, style, rng)

    # little sparkles for flair
    spark = _hx(binding.get("spark", binding["color"]))
    for _ in range(8):
        sx = rng.randint(80 * S, (W - 80) * S)
        sy = rng.randint(80 * S, (H - 80) * S)
        rr = rng.randint(2 * S, 4 * S)
        d.ellipse([sx - rr, sy - rr, sx + rr, sy + rr], fill=_rgba(spark, 120))

    base.alpha_composite(layer)
    out = base.resize((W, H), Image.LANCZOS).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
