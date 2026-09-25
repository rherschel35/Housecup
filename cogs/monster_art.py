"""
Descent monster portraits - drawn in code (no image files), same approach
as mirror_art.py for the Mirror trading cards. Each monster is a simple
silhouette (one of a handful of "kinds": blob, serpent, humanoid,
quadruped, flier, orb) tinted to its element's palette, on a gradient
backdrop. Bosses get a bigger silhouette, a richer glow, and a small
crown accent.

render(name, element, kind, is_boss) -> PNG bytes. Pure Pillow, no
Discord - deterministic per (name, element, kind, is_boss), so the same
monster always renders the same portrait.
"""

from __future__ import annotations

import hashlib
import io
import math
import random

from PIL import Image, ImageDraw, ImageFilter

W, H = 640, 640
S = 2  # drawn at 2x, scaled down for smooth edges
CX, CY = W // 2, H // 2 + 20

# ------------------------------------------------------------- palettes

PALETTES = {
    "fire":      {"bg": ("#2B0E08", "#5A1B0B"), "body": "#D94E23", "accent": "#F2A65A", "glow": "#FF7A3D"},
    "ice":       {"bg": ("#081722", "#123049"), "body": "#7FC4D9", "accent": "#E8F6FA", "glow": "#9FE0F0"},
    "lightning": {"bg": ("#140F26", "#2A1E4D"), "body": "#C9A6E8", "accent": "#F2D64B", "glow": "#E8D64B"},
    "poison":    {"bg": ("#0E1A0C", "#1E3418"), "body": "#7FA65A", "accent": "#C7E86B", "glow": "#9BE04B"},
    "light":     {"bg": ("#241C0C", "#4A3A16"), "body": "#F2E8C9", "accent": "#F2D68A", "glow": "#FFF3C4"},
}


def _rng_for(name: str) -> random.Random:
    seed = int(hashlib.sha256(name.encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def _lerp(c1, c2, t):
    def hx(c):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    a, b = hx(c1), hx(c2)
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _background(pal: dict) -> Image.Image:
    top, bottom = pal["bg"]
    img = Image.new("RGB", (W * S, H * S))
    px = img.load()
    for y in range(H * S):
        t = y / (H * S)
        r, g, b = _lerp(top, bottom, t)
        for x in range(0, W * S, 4):  # coarse fill, fast enough for a flat gradient
            for dx in range(4):
                if x + dx < W * S:
                    px[x + dx, y] = (r, g, b)
    return img


def _glow(base: Image.Image, cx, cy, radius, color) -> Image.Image:
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    hexcol = color.lstrip("#")
    rgb = tuple(int(hexcol[i:i + 2], 16) for i in (0, 2, 4))
    gd.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=rgb + (140,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius // 3))
    out = base.convert("RGBA")
    out.alpha_composite(glow)
    return out.convert("RGB")


# ------------------------------------------------------------ silhouettes
# Each draws into (draw, cx, cy, scale) using pal["body"]/["accent"], with
# a bit of rng-driven jitter so same-kind monsters aren't identical.

def _blob(draw, cx, cy, scale, pal, rng):
    r = 120 * scale
    draw.ellipse([cx - r, cy - r * 0.8, cx + r, cy + r * 0.9], fill=pal["body"])
    for i in range(3):
        lx = cx - r * 0.5 + i * r * 0.5
        draw.ellipse([lx - 10 * scale, cy + r * 0.6, lx + 10 * scale, cy + r * 0.95], fill=pal["accent"])
    eye_y = cy - r * 0.15
    for ex in (cx - 35 * scale, cx + 35 * scale):
        draw.ellipse([ex - 12 * scale, eye_y - 12 * scale, ex + 12 * scale, eye_y + 12 * scale], fill="#1A1A1A")


def _serpent(draw, cx, cy, scale, pal, rng):
    pts = []
    for i in range(24):
        t = i / 23
        x = cx + math.sin(t * 3.4 + rng.random()) * 90 * scale
        y = cy - 150 * scale + t * 260 * scale
        pts.append((x, y))
    width = 34 * scale
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        w = width * (1 - i / len(pts) * 0.6)
        draw.line([x0, y0, x1, y1], fill=pal["body"], width=int(w))
    hx, hy = pts[0]
    draw.ellipse([hx - 30 * scale, hy - 26 * scale, hx + 30 * scale, hy + 26 * scale], fill=pal["body"])
    for ex in (hx - 10 * scale, hx + 10 * scale):
        draw.ellipse([ex - 6 * scale, hy - 6 * scale, ex + 6 * scale, hy + 6 * scale], fill=pal["accent"])


def _humanoid(draw, cx, cy, scale, pal, rng):
    draw.polygon([
        (cx - 70 * scale, cy + 160 * scale), (cx - 90 * scale, cy - 20 * scale),
        (cx - 40 * scale, cy - 140 * scale), (cx + 40 * scale, cy - 140 * scale),
        (cx + 90 * scale, cy - 20 * scale), (cx + 70 * scale, cy + 160 * scale),
    ], fill=pal["body"])
    draw.ellipse([cx - 45 * scale, cy - 210 * scale, cx + 45 * scale, cy - 125 * scale], fill=pal["body"])
    for ex in (cx - 18 * scale, cx + 18 * scale):
        draw.ellipse([ex - 8 * scale, cy - 175 * scale, ex + 8 * scale, cy - 160 * scale], fill=pal["accent"])


def _quadruped(draw, cx, cy, scale, pal, rng):
    draw.ellipse([cx - 110 * scale, cy - 20 * scale, cx + 110 * scale, cy + 70 * scale], fill=pal["body"])
    for lx in (-80, -30, 30, 80):
        x = cx + lx * scale
        draw.rectangle([x - 12 * scale, cy + 40 * scale, x + 12 * scale, cy + 150 * scale], fill=pal["body"])
    draw.polygon([(cx - 120 * scale, cy + 10 * scale), (cx - 150 * scale, cy - 30 * scale),
                 (cx - 100 * scale, cy - 10 * scale)], fill=pal["body"])  # tail
    hx = cx + 120 * scale
    draw.ellipse([hx - 40 * scale, cy - 60 * scale, hx + 40 * scale, cy + 20 * scale], fill=pal["body"])
    for tx in (hx - 15, hx + 20):
        draw.polygon([(tx * scale / scale, cy - 55 * scale), (tx * scale / scale - 8 * scale, cy - 30 * scale),
                     (tx * scale / scale + 8 * scale, cy - 30 * scale)], fill=pal["body"])  # ears
    for ex in (hx, hx + 22 * scale):
        draw.ellipse([ex - 6 * scale, cy - 25 * scale, ex + 6 * scale, cy - 13 * scale], fill=pal["accent"])


def _flier(draw, cx, cy, scale, pal, rng):
    draw.polygon([(cx - 160 * scale, cy - 10 * scale), (cx - 10 * scale, cy - 60 * scale),
                 (cx - 10 * scale, cy + 30 * scale)], fill=pal["body"])
    draw.polygon([(cx + 160 * scale, cy - 10 * scale), (cx + 10 * scale, cy - 60 * scale),
                 (cx + 10 * scale, cy + 30 * scale)], fill=pal["body"])
    draw.ellipse([cx - 35 * scale, cy - 40 * scale, cx + 35 * scale, cy + 60 * scale], fill=pal["body"])
    draw.ellipse([cx - 15 * scale, cy - 55 * scale, cx + 15 * scale, cy - 25 * scale], fill=pal["accent"])


def _orb(draw, cx, cy, scale, pal, rng):
    r = 110 * scale
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=pal["body"])
    for _ in range(10):
        ang = rng.uniform(0, math.tau)
        rr = rng.uniform(0.3, 0.9) * r
        x, y = cx + math.cos(ang) * rr, cy + math.sin(ang) * rr
        draw.ellipse([x - 8 * scale, y - 8 * scale, x + 8 * scale, y + 8 * scale], fill=pal["accent"])


KINDS = {"blob": _blob, "serpent": _serpent, "humanoid": _humanoid,
         "quadruped": _quadruped, "flier": _flier, "orb": _orb}


def _crown(draw, cx, cy, scale, pal):
    pts = [(cx - 60 * scale, cy), (cx - 40 * scale, cy - 50 * scale), (cx - 15 * scale, cy - 15 * scale),
           (cx, cy - 65 * scale), (cx + 15 * scale, cy - 15 * scale), (cx + 40 * scale, cy - 50 * scale),
           (cx + 60 * scale, cy)]
    draw.polygon(pts, fill=pal["accent"])


def render(name: str, element: str, kind: str, is_boss: bool = False) -> bytes:
    pal = PALETTES.get(element, PALETTES["light"])
    rng = _rng_for(name)
    base = _background(pal)
    draw_fn = KINDS.get(kind, _blob)

    scale = S * (1.35 if is_boss else 1.0)
    draw = ImageDraw.Draw(base)
    draw_fn(draw, CX * S, (CY + (30 if is_boss else 0)) * S, scale, pal, rng)
    if is_boss:
        _crown(draw, CX * S, (CY - (200 if kind == "humanoid" else 140)) * S, scale, pal)

    glow_r = int(220 * S * (1.2 if is_boss else 1.0))
    out = _glow(base, CX * S, CY * S, glow_r, pal["glow"])

    out = out.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
