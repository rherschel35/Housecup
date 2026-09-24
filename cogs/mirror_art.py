"""
The Mirror - draws a player's wizard as a collectible trading card.

Everything is drawn in code (no image files) in a flat storybook style:
the wizard in robes of their house colours, whatever jewellery they're
wearing and their wand, on a foil-edged card with their name, house badge,
title, a few real stats, their house motto and a star rating. Drawn at 2x
and scaled down, so the edges come out smooth.

render(...) -> PNG bytes. Pure Pillow, no Discord - so it can be tested
on its own.
"""

from __future__ import annotations

import hashlib
import io
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path(__file__).resolve().parent.parent / "data" / "fonts"

W, H = 800, 1060          # final picture size
S = 2                     # drawn at 2x, scaled down at the end
CX = 400                  # the wizard stands in the middle

# ---------------------------------------------------------------- look options
# (key, label, value) - label is what players see in /wizard.

BODIES = {"masculine": "Masculine build", "neutral": "Neutral build", "feminine": "Feminine build"}

SKINS = {
    "porcelain": ("Porcelain", "#F7DECF"),
    "fair":      ("Fair",      "#EFC8AC"),
    "light":     ("Light",     "#E2AE89"),
    "olive":     ("Olive",     "#C8956A"),
    "tan":       ("Tan",       "#B07650"),
    "brown":     ("Brown",     "#8C593A"),
    "deep":      ("Deep",      "#6A412B"),
    "ebony":     ("Ebony",     "#4B2E20"),
}

HAIR_STYLES = {
    "crop": "Short crop", "sidepart": "Side part", "messy": "Messy", "spiky": "Spiky",
    "buzz": "Buzzed", "long": "Long & straight", "wavy": "Long & wavy", "curls": "Big curls",
    "bun": "Top bun", "ponytail": "Ponytail", "braid": "Side braid", "twin": "Twin braids",
}

HAIR_COLORS = {
    "black":    ("Black",         "#1F1B1D"),
    "darkbrown": ("Dark brown",   "#3A2518"),
    "brown":    ("Brown",         "#6A4128"),
    "auburn":   ("Auburn",        "#8B3A22"),
    "ginger":   ("Ginger",        "#C4622B"),
    "blonde":   ("Blonde",        "#D8B46A"),
    "platinum": ("Platinum",      "#EEE4CB"),
    "silver":   ("Silver",        "#B8BFCB"),
    "midnight": ("Midnight blue", "#2A3A72"),
    "plum":     ("Plum",          "#5E2C61"),
}

EYE_SHAPES = {"round": "Round eyes", "almond": "Almond eyes", "narrow": "Narrow eyes"}

EYE_COLORS = {
    "brown": ("Brown", "#5B3920"), "hazel": ("Hazel", "#7E6A2C"), "green": ("Green", "#3E7A4A"),
    "blue": ("Blue", "#3D6EA8"), "grey": ("Grey", "#77838F"), "amber": ("Amber", "#B87918"),
}

BROWS = {"soft": "Soft brows", "straight": "Straight brows", "arched": "Arched brows", "bold": "Bold brows"}

EXPRESSIONS = {"smile": "Smiling", "grin": "Grinning", "smirk": "Smirking",
               "serious": "Serious", "sleepy": "Sleepy"}

EXTRAS = {"none": "Nothing extra", "freckles": "Freckles", "glasses": "Round glasses",
          "freckles_glasses": "Freckles + glasses"}

FACIAL_HAIR = {"none": "No facial hair", "stubble": "Stubble", "mustache": "Mustache", "beard": "Beard"}

LOOK_FIELDS = {
    "body": BODIES, "skin": SKINS, "hair": HAIR_STYLES, "hair_color": HAIR_COLORS,
    "eyes": EYE_SHAPES, "eye_color": EYE_COLORS, "brows": BROWS, "expression": EXPRESSIONS,
    "extras": EXTRAS, "facial_hair": FACIAL_HAIR,
}


def option_label(field: str, key: str) -> str:
    v = LOOK_FIELDS[field][key]
    return v[0] if isinstance(v, tuple) else v


def default_look(user_id: int) -> dict:
    """A stable starting look for someone who hasn't used /wizard yet."""
    rng = random.Random(int(hashlib.sha256(str(user_id).encode()).hexdigest()[:12], 16))
    look = {f: rng.choice(sorted(opts)) for f, opts in LOOK_FIELDS.items()}
    look["facial_hair"] = "none" if rng.random() < 0.75 else look["facial_hair"]
    look["extras"] = "none" if rng.random() < 0.5 else look["extras"]
    return look


def clean_look(look: dict | None, user_id: int) -> dict:
    base = default_look(user_id)
    for f, opts in LOOK_FIELDS.items():
        if look and look.get(f) in opts:
            base[f] = look[f]
    return base


# ---------------------------------------------------------------- colour helpers

def rgb(c) -> tuple:
    if isinstance(c, tuple):
        return c[:3]
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t: float) -> tuple:
    a, b = rgb(a), rgb(b)
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def dark(c, t=0.35):
    return mix(c, (18, 12, 16), t)


def light(c, t=0.35):
    return mix(c, (255, 250, 240), t)


def rgba(c, a=255):
    return rgb(c) + (a,)


def hexint(n: int) -> str:
    return f"#{n:06X}"


MATERIALS = {
    "gold": "#DDAE3C", "silver": "#CDD3DC", "bronze": "#B37A3C", "iron": "#6F6F75",
    "brass": "#C49C4C", "cord": "#6E4B2E", "vine": "#4E8B3B", "pearl": "#F1EADC",
    "bone": "#E8DDC2", "black": "#2A2530", "copper": "#C0703C", "leather": "#7A4E2E",
}


def mat(name_or_hex):
    return MATERIALS.get(name_or_hex, name_or_hex)


# ---------------------------------------------------------------- drawing helpers

def P(pts):
    return [(x * S, y * S) for x, y in pts]


def B(box):
    return [v * S for v in box]


def bez(p0, p1, p2, p3, n=18):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def smooth(pts, closed=True, n=10):
    """Catmull-Rom through the points - organic shapes from a few dots."""
    out = []
    m = len(pts)
    rng_ = range(m) if closed else range(m - 1)
    for i in rng_:
        p0 = pts[(i - 1) % m] if closed or i > 0 else pts[i]
        p1 = pts[i]
        p2 = pts[(i + 1) % m]
        p3 = pts[(i + 2) % m] if closed or i + 2 < m else pts[(i + 1) % m]
        for k in range(n):
            t = k / n
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * t
                                    + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3) for j in range(2)))
    if not closed:
        out.append(pts[-1])
    return out


class Pen:
    """ImageDraw in picture coordinates, with a storybook outline."""

    def __init__(self, img):
        self.img = img
        self.d = ImageDraw.Draw(img)

    def poly(self, pts, fill, outline=None, w=3):
        self.d.polygon(P(pts), fill=rgba(fill) if fill is not None else None)
        if outline is not None and w:
            self.line(pts + [pts[0]], outline, w)

    def line(self, pts, color, w=3, alpha=255):
        pts = P(pts)
        self.d.line(pts, fill=rgba(color, alpha), width=int(w * S), joint="curve")
        r = w * S / 2
        for x, y in (pts[0], pts[-1]):
            self.d.ellipse([x - r, y - r, x + r, y + r], fill=rgba(color, alpha))

    def ell(self, cx, cy, rx, ry, fill, outline=None, w=3, alpha=255):
        box = B([cx - rx, cy - ry, cx + rx, cy + ry])
        self.d.ellipse(box, fill=rgba(fill, alpha) if fill is not None else None,
                       outline=rgba(outline) if outline is not None else None,
                       width=int(w * S) if outline is not None else 0)

    def arc(self, cx, cy, rx, ry, start, end, color, w=3):
        self.d.arc(B([cx - rx, cy - ry, cx + rx, cy + ry]), start, end, fill=rgba(color), width=int(w * S))

    def rect(self, box, fill, radius=0, outline=None, w=3):
        self.d.rounded_rectangle(B(box), radius=radius * S, fill=rgba(fill) if fill is not None else None,
                                 outline=rgba(outline) if outline is not None else None,
                                 width=int(w * S) if outline is not None else 0)


def new_layer():
    return Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))


def glow_layer(layer, radius, strength=1.0):
    """Blur a layer into a soft glow. Only the part with something in it is
    blurred (much faster than blurring the whole picture)."""
    box = layer.getbbox()
    if not box:
        return layer
    pad = int(radius * S * 3) + 2
    x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
    x1, y1 = min(layer.width, box[2] + pad), min(layer.height, box[3] + pad)
    part = layer.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(radius * S))
    if strength != 1.0:
        part.putalpha(part.getchannel("A").point(lambda v: min(255, int(v * strength))))
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.paste(part, (x0, y0))
    return out


def star(pen, x, y, r, color, alpha=255):
    pts = []
    for i in range(8):
        ang = math.pi / 4 * i - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.28
        pts.append((x + rr * math.cos(ang), y + rr * math.sin(ang)))
    pen.d.polygon(P(pts), fill=rgba(color, alpha))


# ---------------------------------------------------------------- fonts

_font_cache: dict = {}


def font(name: str, size: int, weight: int | None = None):
    key = (name, size, weight)
    if key not in _font_cache:
        path = FONT_DIR / name
        try:
            f = ImageFont.truetype(str(path), size * S)
            if weight:
                try:
                    f.set_variation_by_axes([weight])
                except Exception:
                    pass
        except OSError:
            f = ImageFont.load_default(size * S)
        _font_cache[key] = f
    return _font_cache[key]


def emoji_image(ch: str, size: int) -> Image.Image | None:
    """A colour emoji as an image, size px wide (picture units)."""
    f = _font_cache.get("emoji")
    if f is None:
        try:
            f = ImageFont.truetype(str(FONT_DIR / "NotoColorEmoji.ttf"), 109)
        except OSError:
            return None
        _font_cache["emoji"] = f
    im = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((8, 8), ch, font=f, embedded_color=True)
    box = im.getbbox()
    if not box:
        return None
    im = im.crop(box)
    scale = size * S / max(im.size)
    return im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)


def paste_center(base, im, x, y):
    base.alpha_composite(im, (int(x * S - im.width / 2), int(y * S - im.height / 2)))


# ---------------------------------------------------------------- the body

BUILD = {  # shoulder, waist, hem half-widths
    "masculine": (126, 118, 150),
    "neutral":   (112, 104, 162),
    "feminine":  (100, 88, 186),
}

NECK_Y, SHOULDER_Y, WAIST_Y, HEM_Y = 440, 486, 646, 884
HEAD_CY, HEAD_RX, HEAD_RY = 330, 86, 96


def robe_outline(body):
    sh, wa, he = BUILD[body]
    left = [(CX - 30, NECK_Y + 4)] + bez((CX - 30, NECK_Y + 4), (CX - sh + 20, NECK_Y + 10),
                                          (CX - sh - 4, SHOULDER_Y - 18), (CX - sh, SHOULDER_Y + 12))[1:]
    left += bez((CX - sh, SHOULDER_Y + 12), (CX - sh + 4, 560), (CX - wa, 600), (CX - wa, WAIST_Y))[1:]
    left += bez((CX - wa, WAIST_Y), (CX - wa - 10, 740), (CX - he + 10, 830), (CX - he, HEM_Y))[1:]
    hem = bez((CX - he, HEM_Y), (CX - he / 2, HEM_Y + 12), (CX + he / 2, HEM_Y + 12), (CX + he, HEM_Y))[1:]
    right = [(2 * CX - x, y) for x, y in reversed(left)]
    return left + hem + right[1:]


def draw_feet(pen, skin):
    shoe = "#2B2128"
    for dx in (-44, 44):
        pen.ell(CX + dx, HEM_Y + 14, 34, 16, shoe, outline=dark(shoe, 0.5), w=3)
        pen.ell(CX + dx - 8, HEM_Y + 9, 12, 5, light(shoe, 0.25))


def draw_robe(img, body, house_color, skin):
    pen = Pen(img)
    hc = rgb(house_color)
    robe = mix(hc, "#17121C", 0.45)
    lining = light(hc, 0.1)
    trim = light(hc, 0.25)
    sh, wa, he = BUILD[body]

    # hood folded behind the shoulders
    hood = smooth([(CX - 78, NECK_Y + 6), (CX - 40, NECK_Y - 16), (CX + 40, NECK_Y - 16),
                   (CX + 78, NECK_Y + 6), (CX + 56, NECK_Y + 44), (CX, NECK_Y + 60), (CX - 56, NECK_Y + 44)])
    pen.poly(hood, lining, outline=dark(lining, 0.55), w=3)

    outline = robe_outline(body)
    pen.poly(outline, robe, outline=dark(robe, 0.55), w=4)

    # soft fold shading down the robe
    shade = new_layer()
    sp = Pen(shade)
    for dx in (-0.55, 0.55):
        sp.poly(smooth([(CX + dx * wa, WAIST_Y + 10), (CX + dx * (he - 20), HEM_Y - 6),
                        (CX + dx * (he - 50), HEM_Y - 6)], closed=True, n=6), dark(robe, 0.5))
    shade = glow_layer(shade, 10, 0.5)
    img.alpha_composite(shade)
    pen = Pen(img)

    # shirt collar + house tie in the V of the robe
    v = [(CX - 30, NECK_Y + 4), (CX, NECK_Y + 86), (CX + 30, NECK_Y + 4)]
    pen.poly(v, "#F2EEE6", outline=dark("#F2EEE6", 0.4), w=2)
    tie = [(CX - 9, NECK_Y + 12), (CX + 9, NECK_Y + 12), (CX + 6, NECK_Y + 70), (CX, NECK_Y + 84), (CX - 6, NECK_Y + 70)]
    pen.poly(tie, trim, outline=dark(trim, 0.5), w=2)
    for k in range(3):
        y = NECK_Y + 26 + k * 18
        pen.line([(CX - 7, y), (CX + 7, y + 6)], dark(trim, 0.45), 3)

    # open front of the robe, with trim either side
    for side in (-1, 1):
        pts = [(CX + side * 2, NECK_Y + 86)] + bez((CX + side * 2, NECK_Y + 86), (CX + side * 6, 640),
                                                   (CX + side * 4, 780), (CX + side * 6, HEM_Y + 10))[1:]
        pen.line(pts, trim, 7)
    lapel_l = [(CX - 30, NECK_Y + 4), (CX - 44, NECK_Y + 30), (CX - 4, NECK_Y + 96), (CX, NECK_Y + 86)]
    pen.poly(lapel_l, trim, outline=dark(trim, 0.5), w=2)
    pen.poly([(2 * CX - x, y) for x, y in lapel_l], trim, outline=dark(trim, 0.5), w=2)

    # hem band
    hem = bez((CX - he, HEM_Y), (CX - he / 2, HEM_Y + 12), (CX + he / 2, HEM_Y + 12), (CX + he, HEM_Y))
    pen.line(hem, trim, 8)
    # sash
    sash = smooth([(CX - wa - 2, WAIST_Y - 12), (CX, WAIST_Y - 6), (CX + wa + 2, WAIST_Y - 12),
                   (CX + wa + 3, WAIST_Y + 10), (CX, WAIST_Y + 16), (CX - wa - 3, WAIST_Y + 10)], n=6)
    sash_c = "#4A3526"
    pen.poly(sash, sash_c, outline=dark(sash_c, 0.5), w=3)
    pen.rect([CX - 12, WAIST_Y - 10, CX + 12, WAIST_Y + 12], "#C9A24A", radius=3, outline="#6E5220", w=2)


def draw_neck_and_ears(pen, skin):
    pen.rect([CX - 22, HEAD_CY + 60, CX + 22, NECK_Y + 16], skin, radius=6, outline=dark(skin, 0.45), w=3)
    pen.ell(CX, NECK_Y - 6, 22, 8, dark(skin, 0.18))
    for side in (-1, 1):
        pen.ell(CX + side * (HEAD_RX - 2), HEAD_CY + 16, 14, 20, skin, outline=dark(skin, 0.45), w=3)
        pen.ell(CX + side * (HEAD_RX - 2), HEAD_CY + 16, 6, 10, dark(skin, 0.15))


def head_shape():
    top = HEAD_CY - HEAD_RY
    pts = [(CX, top), (CX + 62, top + 14), (CX + HEAD_RX, HEAD_CY - 18), (CX + HEAD_RX - 4, HEAD_CY + 34),
           (CX + 56, HEAD_CY + 78), (CX, HEAD_CY + HEAD_RY), (CX - 56, HEAD_CY + 78),
           (CX - HEAD_RX + 4, HEAD_CY + 34), (CX - HEAD_RX, HEAD_CY - 18), (CX - 62, top + 14)]
    return smooth(pts, n=10)


def draw_head(pen, skin):
    pen.poly(head_shape(), skin, outline=dark(skin, 0.45), w=4)


# ---------------------------------------------------------------- arms

def arm_geometry(body):
    sh, _, _ = BUILD[body]
    # down arm on the viewer's left, wand arm on the viewer's right
    down_hand = (CX - sh - 6, 716)
    wand_hand = (CX + sh + 58, 612)
    return down_hand, wand_hand


def draw_arms(img, body, house_color, skin):
    pen = Pen(img)
    hc = rgb(house_color)
    robe = mix(hc, "#17121C", 0.45)
    trim = light(hc, 0.25)
    sh, _, _ = BUILD[body]
    (dx, dy), (wx, wy) = arm_geometry(body)

    # left sleeve, hanging
    sl = smooth([(CX - sh + 14, SHOULDER_Y - 16), (CX - sh - 16, SHOULDER_Y + 4), (CX - sh - 34, 600),
                 (CX - sh - 46, dy - 20), (CX - sh + 34, dy - 22), (CX - sh + 26, 590), (CX - sh + 26, SHOULDER_Y + 30)],
                n=8)
    pen.poly(sl, robe, outline=dark(robe, 0.55), w=4)
    pen.line([(CX - sh - 45, dy - 21), (CX - sh + 34, dy - 22)], trim, 7)
    pen.ell(dx, dy, 21, 19, skin, outline=dark(skin, 0.45), w=3)
    for k in range(3):   # fingers
        pen.arc(dx - 8 + k * 8, dy + 12, 5, 6, 0, 180, dark(skin, 0.4), 2)

    # right sleeve, raised to hold the wand
    sr = smooth([(CX + sh - 14, SHOULDER_Y - 16), (CX + sh + 18, SHOULDER_Y + 2), (CX + sh + 46, 548),
                 (CX + sh + 82, 588), (CX + sh + 44, 612), (CX + sh + 8, 590), (CX + sh - 22, SHOULDER_Y + 40)], n=8)
    pen.poly(sr, robe, outline=dark(robe, 0.55), w=4)
    pen.line([(CX + sh + 82, 588), (CX + sh + 44, 612)], trim, 7)
    return (dx, dy), (wx, wy)


def draw_wand_and_hand(img, body, skin, wand, sparks=False):
    pen = Pen(img)
    _, (wx, wy) = arm_geometry(body)
    wood = WOOD_COLORS.get((wand or {}).get("wood"), "#6B4A2E")
    tip = (wx + 96, wy - 150)
    base = (wx - 14, wy + 22)
    pen.line([base, tip], dark(wood, 0.5), 11)
    pen.line([base, tip], wood, 7)
    pen.line([(base[0] + 2, base[1] - 4), ((base[0] + tip[0]) / 2, (base[1] + tip[1]) / 2)], light(wood, 0.3), 2)
    # handle knots
    for t in (0.12, 0.2):
        x, y = base[0] + (tip[0] - base[0]) * t, base[1] + (tip[1] - base[1]) * t
        pen.ell(x, y, 7, 7, dark(wood, 0.15), outline=dark(wood, 0.5), w=2)
    # tip light
    glow = new_layer()
    gp = Pen(glow)
    gp.ell(tip[0], tip[1], 16 if not sparks else 26, 16 if not sparks else 26, (255, 246, 205), alpha=220)
    img.alpha_composite(glow_layer(glow, 8 if not sparks else 12, 1.4))
    pen = Pen(img)
    star(pen, tip[0], tip[1], 11 if not sparks else 18, (255, 255, 240))
    if sparks:
        rng = random.Random(7)
        for _ in range(9):
            a = rng.uniform(0, 2 * math.pi)
            d = rng.uniform(26, 58)
            star(pen, tip[0] + d * math.cos(a), tip[1] + d * math.sin(a), rng.uniform(3, 7), (255, 236, 160))
    # hand wrapped round it
    pen.ell(wx, wy, 21, 19, skin, outline=dark(skin, 0.45), w=3)
    for k in range(3):
        pen.arc(wx - 10 + k * 8, wy - 4, 5, 6, 90, 270, dark(skin, 0.4), 2)


WOOD_COLORS = {
    "Ash": "#C9B08A", "Blackthorn": "#3A2B2A", "Cedar": "#A35E3A", "Cherry": "#8E3B2E",
    "Elm": "#8A6A48", "Hawthorn": "#9C7B5B", "Hazel": "#9B6B3F", "Holly": "#E2D6BC",
    "Larch": "#B48454", "Maple": "#D1A56B", "Oak": "#7A5634", "Pine": "#C99E62",
    "Rowan": "#9A4E36", "Walnut": "#553722", "Willow": "#B7A77E", "Yew": "#6D3A2A",
}


# ---------------------------------------------------------------- hair

def hair_back(pen, style, color):
    c, o = color, dark(color, 0.5)
    top = HEAD_CY - HEAD_RY
    if style in ("long", "braid", "twin"):
        pts = [(CX - 96, HEAD_CY - 40), (CX - 70, top - 6), (CX + 70, top - 6), (CX + 96, HEAD_CY - 40),
               (CX + 102, HEAD_CY + 100), (CX + 96, 560), (CX - 96, 560), (CX - 102, HEAD_CY + 100)]
        if style != "long":
            pts = [(CX - 96, HEAD_CY - 40), (CX - 70, top - 6), (CX + 70, top - 6), (CX + 96, HEAD_CY - 40),
                   (CX + 98, HEAD_CY + 60), (CX + 80, HEAD_CY + 100), (CX - 80, HEAD_CY + 100), (CX - 98, HEAD_CY + 60)]
        pen.poly(smooth(pts, n=8), c, outline=o, w=4)
    elif style == "wavy":
        pts = [(CX - 98, HEAD_CY - 40), (CX - 70, top - 8), (CX + 70, top - 8), (CX + 98, HEAD_CY - 40)]
        for k in range(6):
            y = HEAD_CY + 10 + k * 45
            pts.append((CX + 104 + (14 if k % 2 else -4), y))
        pts += [(CX + 80, 600), (CX - 80, 600)]
        for k in reversed(range(6)):
            y = HEAD_CY + 10 + k * 45
            pts.append((CX - 104 - (14 if k % 2 else -4), y))
        pen.poly(smooth(pts, n=8), c, outline=o, w=4)
    elif style == "curls":
        rng = random.Random(3)
        blobs = []
        for k in range(22):
            a = math.pi * (0.95 + 1.1 * k / 21)
            blobs.append((CX + 116 * math.cos(a), HEAD_CY - 10 + 112 * math.sin(a) * 0.95))
        for k in range(8):
            blobs.append((CX + rng.uniform(-110, 110), HEAD_CY + rng.uniform(20, 90)))
        for x, y in blobs:
            pen.ell(x, y, 36, 34, o)
        for x, y in blobs:
            pen.ell(x, y, 32, 30, c)
    elif style == "ponytail":
        pen.ell(CX, HEAD_CY - 20, HEAD_RX + 6, HEAD_RY - 8, c, outline=o, w=4)
        pts = [(CX + 62, top + 22), (CX + 118, top + 18), (CX + 150, HEAD_CY + 30), (CX + 136, HEAD_CY + 150),
               (CX + 112, HEAD_CY + 70), (CX + 84, top + 60)]
        pen.poly(smooth(pts, n=8), c, outline=o, w=4)
        for k in range(3):
            pen.line(smooth([(CX + 96 + k * 10, top + 34), (CX + 124 + k * 6, HEAD_CY + 20),
                             (CX + 124 + k * 4, HEAD_CY + 110)], closed=False, n=6), dark(c, 0.25), 2)
    elif style == "bun":
        pen.ell(CX, HEAD_CY - 20, HEAD_RX + 6, HEAD_RY - 8, c, outline=o, w=4)
    else:
        # a little volume behind the head for short styles
        pen.ell(CX, HEAD_CY - 26, HEAD_RX + 8, HEAD_RY - 4, c, outline=o, w=4)


def hair_front(img, style, color):
    pen = Pen(img)
    c, o, hi = color, dark(color, 0.5), light(color, 0.25)
    top = HEAD_CY - HEAD_RY
    L, R = CX - HEAD_RX - 8, CX + HEAD_RX + 8

    rise = {"bun": 46, "ponytail": 46, "buzz": 30, "crop": 64, "sidepart": 70}.get(style, 78)

    def cap(fringe, sides=(HEAD_CY - 6, HEAD_CY - 6)):
        pts = [(L, sides[0])] + bez((L, sides[0]), (L - 10, top - rise), (R + 10, top - rise), (R, sides[1]))[1:]
        pts += list(reversed(fringe))
        return pts

    if style == "buzz":
        fringe = [(CX - HEAD_RX + 2, HEAD_CY - 20)] + bez((CX - HEAD_RX + 2, HEAD_CY - 20), (CX - 40, top + 26),
                                               (CX + 40, top + 26), (CX + HEAD_RX - 2, HEAD_CY - 20))[1:]
        buzz = new_layer()
        bp = Pen(buzz)
        L2, R2 = CX - HEAD_RX + 2, CX + HEAD_RX - 2
        shell = [(L2, HEAD_CY - 20)] + bez((L2, HEAD_CY - 20), (L2 - 4, top - 26), (R2 + 4, top - 26), (R2, HEAD_CY - 20))[1:]
        bp.poly(shell + list(reversed(fringe)), c)
        a = buzz.getchannel("A").point(lambda v: int(v * 0.72))
        buzz.putalpha(a)
        img.alpha_composite(buzz)
        return
    if style == "crop":
        fringe = [(L, HEAD_CY - 6), (L + 20, HEAD_CY - 38)]
        for k in range(7):
            x = CX - 62 + k * 21
            fringe.append((x, HEAD_CY - 52 + (8 if k % 2 else 0)))
        fringe += [(R - 20, HEAD_CY - 38), (R, HEAD_CY - 6)]
    elif style == "sidepart":
        fringe = [(L, HEAD_CY - 4)] + bez((L, HEAD_CY - 4), (CX - 70, HEAD_CY - 60),
                                          (CX - 10, HEAD_CY - 44), (CX + 30, HEAD_CY - 74))[1:]
        fringe += bez((CX + 30, HEAD_CY - 74), (CX + 60, HEAD_CY - 70), (R - 8, HEAD_CY - 50), (R, HEAD_CY - 4))[1:]
    elif style in ("messy", "spiky"):
        fringe = [(L, HEAD_CY - 2)]
        n = 7
        for k in range(n + 1):
            x = L + 14 + k * (R - L - 28) / n
            fringe.append((x, HEAD_CY - (30 if k % 2 else 58) + (8 if style == "messy" and k % 3 == 0 else 0)))
        fringe.append((R, HEAD_CY - 2))
    elif style in ("long", "wavy", "braid", "twin"):
        fringe = [(L, HEAD_CY + 40)] + bez((L, HEAD_CY + 40), (L + 4, HEAD_CY - 30),
                                           (CX - 30, HEAD_CY - 60), (CX, HEAD_CY - 76))[1:]
        fringe += bez((CX, HEAD_CY - 76), (CX + 30, HEAD_CY - 60), (R - 4, HEAD_CY - 30), (R, HEAD_CY + 40))[1:]
        pen.poly(cap(fringe, (HEAD_CY + 40, HEAD_CY + 40)), c, outline=o, w=4)
        pen.line([(CX, top - 2), (CX, HEAD_CY - 72)], o, 3)
        pen.arc(CX - 40, HEAD_CY - 60, 30, 26, 210, 290, hi, 3)
        pen.arc(CX + 40, HEAD_CY - 60, 30, 26, 250, 330, hi, 3)
        if style == "braid":
            draw_braid(pen, [(CX + 78, HEAD_CY + 40), (CX + 92, HEAD_CY + 110), (CX + 96, 520), (CX + 88, 590)], c)
        if style == "twin":
            draw_braid(pen, [(CX + 80, HEAD_CY + 40), (CX + 94, HEAD_CY + 110), (CX + 98, 520), (CX + 92, 580)], c)
            draw_braid(pen, [(CX - 80, HEAD_CY + 40), (CX - 94, HEAD_CY + 110), (CX - 98, 520), (CX - 92, 580)], c)
        return
    elif style == "curls":
        rng = random.Random(9)
        for k in range(9):
            x = CX - 74 + k * 18.5
            y = HEAD_CY - 60 + rng.uniform(-6, 6) + (abs(k - 4) * 4)
            pen.ell(x, y, 22, 20, c, outline=o, w=3)
        for k in range(9):
            x = CX - 74 + k * 18.5
            pen.arc(x, HEAD_CY - 62 + abs(k - 4) * 4, 10, 9, 200, 340, hi, 2)
        return
    elif style in ("bun", "ponytail"):
        fringe = [(L + 2, HEAD_CY - 10)] + bez((L + 2, HEAD_CY - 10), (CX - 50, HEAD_CY - 70),
                                               (CX + 50, HEAD_CY - 70), (R - 2, HEAD_CY - 10))[1:]
        pen.poly(cap(fringe, (HEAD_CY - 10, HEAD_CY - 10)), c, outline=o, w=4)
        for k in range(4):
            x = CX - 45 + k * 30
            pen.line([(x, top + 2), (x + (k - 1.5) * 6, HEAD_CY - 62)], dark(c, 0.25), 2)
        if style == "bun":
            pen.ell(CX, top - 26, 42, 34, c, outline=o, w=4)
            pen.arc(CX, top - 26, 26, 18, 200, 340, dark(c, 0.3), 3)
            pen.arc(CX, top - 30, 30, 20, 20, 160, light(c, 0.2), 2)
            pen.rect([CX - 26, top - 2, CX + 26, top + 8], "#C9A24A", radius=4, outline="#6E5220", w=2)
        else:
            pen.ell(CX + 70, top + 28, 11, 11, "#C9A24A", outline="#6E5220", w=2)
        return
    else:
        fringe = [(L, HEAD_CY), (R, HEAD_CY)]

    pts = cap(fringe, (fringe[0][1], fringe[-1][1]))
    pen.poly(pts, c, outline=o, w=4)
    if style == "spiky":
        for k in range(5):
            x = CX - 60 + k * 30
            pen.poly([(x - 16, top + 2), (x - 4 + (k - 2) * 4, top - 22), (x + 14, top + 2)], c, outline=o, w=3)
            pen.poly([(x - 14, top + 16), (x - 4 + (k - 2) * 4, top - 16), (x + 12, top + 14)], c)
    pen.arc(CX - 30, top + 40, 40, 30, 200, 280, hi, 3)


def draw_braid(pen, path, color):
    pts = smooth(path, closed=False, n=12)
    o = dark(color, 0.5)
    step = max(1, len(pts) // 9)
    seg = pts[::step]
    for i, (x, y) in enumerate(seg):
        r = 18 - i * 0.6
        pen.ell(x, y, r, r * 0.85, color, outline=o, w=3)
        pen.arc(x, y, r * 0.6, r * 0.5, 200, 340, dark(color, 0.25), 2)
    x, y = seg[-1]
    pen.ell(x, y + 14, 8, 6, "#C9A24A", outline="#6E5220", w=2)
    pen.poly([(x - 10, y + 18), (x + 10, y + 18), (x + 6, y + 40), (x - 6, y + 40)], color, outline=o, w=2)


# ---------------------------------------------------------------- the face

def draw_eye(img, x, y, shape, iris, expression, flip=False):
    ry = {"round": 15, "almond": 12, "narrow": 8}[shape]
    rx = 17 if shape != "narrow" else 16
    pad = 30
    size = (int(2 * pad * S), int(2 * pad * S))
    patch = Image.new("RGBA", size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(patch)
    c = (pad * S, pad * S)

    def e(cx, cy, a, b, fill):
        pd.ellipse([c[0] + (cx - a) * S, c[1] + (cy - b) * S, c[0] + (cx + a) * S, c[1] + (cy + b) * S], fill=fill)

    if shape == "almond":
        mask = Image.new("L", size, 0)
        md = ImageDraw.Draw(mask)
        pts = smooth([(-rx, 2), (-rx * 0.4, -ry), (rx * 0.5, -ry * 1.05), (rx, -1), (rx * 0.3, ry * 0.9), (-rx * 0.5, ry * 0.8)], n=8)
        if flip:
            pts = [(-px, py) for px, py in pts]
        md.polygon([(c[0] + px * S, c[1] + py * S) for px, py in pts], fill=255)
    else:
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).ellipse([c[0] - rx * S, c[1] - ry * S, c[0] + rx * S, c[1] + ry * S], fill=255)
    e(0, 0, rx + 2, ry + 2, (255, 255, 255, 255))
    ir = 11 if shape != "narrow" else 8
    e(0, 1, ir, ir, rgba(iris))
    e(0, 1, ir * 0.45, ir * 0.45, (25, 18, 24, 255))
    e(ir * 0.35, -ir * 0.35, 3.2, 3.2, (255, 255, 255, 255))
    patch.putalpha(ImageChops.multiply(patch.getchannel("A"), mask))
    img.alpha_composite(patch, (int((x - pad) * S), int((y - pad) * S)))
    pen = Pen(img)
    lash = (40, 26, 30)
    if expression == "sleepy":
        # heavy lids: the top half of the eye is covered
        lid = bez((x - rx - 3, y - 1), (x - rx / 2, y + 4), (x + rx / 2, y + 4), (x + rx + 3, y - 1))
        return lid
    pen.arc(x, y, rx + 1, ry + 1, 190, 350, lash, 4)
    if shape == "round":
        pen.arc(x, y, rx + 1, ry + 1, 20, 160, dark(lash, 0.0), 1)
    return None


def draw_face(img, look, skin, hair_color):
    pen = Pen(img)
    ey = HEAD_CY + 12
    iris = EYE_COLORS[look["eye_color"]][1]
    expr = look["expression"]
    lids = []
    for side in (-1, 1):
        lid = draw_eye(img, CX + side * 34, ey, look["eyes"], iris, expr, flip=side > 0)
        if lid:
            lids.append(lid)
    pen = Pen(img)
    for lid in lids:
        x0, x1 = lid[0][0], lid[-1][0]
        pen.poly(lid + [(x1 + 2, lid[-1][1] - 24), (x0 - 2, lid[0][1] - 24)], skin)
        pen.line(lid, (40, 26, 30), 4)
        pen.line(bez((x0 + 4, lid[0][1] - 8), (x0 + 10, lid[0][1] - 13), (x1 - 10, lid[-1][1] - 13), (x1 - 4, lid[-1][1] - 8)),
                 dark(skin, 0.25), 2)

    # brows
    bc = dark(hair_color, 0.35) if look["hair_color"] not in ("platinum", "silver", "blonde") else dark(hair_color, 0.45)
    by = HEAD_CY - 16
    for side in (-1, 1):
        x = CX + side * 34
        style = look["brows"]
        if style == "straight":
            pts = [(x - 16 * side, by), (x + 16 * side, by - 2)]
            pen.line(pts, bc, 5)
        elif style == "arched":
            pen.line(bez((x - 18 * side, by + 4), (x - 6 * side, by - 10), (x + 8 * side, by - 10), (x + 18 * side, by)), bc, 4)
        elif style == "bold":
            pen.line([(x - 18 * side, by + 2), (x - 2 * side, by - 4), (x + 18 * side, by - 1)], bc, 8)
        else:
            pen.line(bez((x - 16 * side, by + 2), (x - 6 * side, by - 5), (x + 6 * side, by - 5), (x + 16 * side, by)), bc, 3)
        if expr == "serious":
            pen.line([(x - 16 * side, by - 2), (x + 16 * side, by + 4)], bc, 4)

    # cheeks
    blush = new_layer()
    bp = Pen(blush)
    for side in (-1, 1):
        bp.ell(CX + side * 50, HEAD_CY + 44, 16, 9, mix(skin, "#E0555A", 0.55), alpha=110)
    img.alpha_composite(glow_layer(blush, 4))
    pen = Pen(img)

    # nose
    pen.arc(CX, HEAD_CY + 38, 7, 5, 20, 160, dark(skin, 0.4), 3)

    # facial hair (under the mouth)
    fh = look["facial_hair"]
    hc = hair_color
    if fh == "stubble":
        st = new_layer()
        sp = Pen(st)
        sp.poly(smooth([(CX - 80, HEAD_CY + 24), (CX - 56, HEAD_CY + 80), (CX, HEAD_CY + 98),
                        (CX + 56, HEAD_CY + 80), (CX + 80, HEAD_CY + 24), (CX + 40, HEAD_CY + 66),
                        (CX, HEAD_CY + 74), (CX - 40, HEAD_CY + 66)], n=8), hc)
        a = st.getchannel("A").point(lambda v: int(v * 0.28))
        st.putalpha(a)
        img.alpha_composite(st)
        pen = Pen(img)
    elif fh == "beard":
        pen.poly(smooth([(CX - 84, HEAD_CY + 10), (CX - 70, HEAD_CY + 86), (CX - 30, HEAD_CY + 118), (CX, HEAD_CY + 124),
                         (CX + 30, HEAD_CY + 118), (CX + 70, HEAD_CY + 86), (CX + 84, HEAD_CY + 10),
                         (CX + 50, HEAD_CY + 56), (CX, HEAD_CY + 70), (CX - 50, HEAD_CY + 56)], n=8),
                 hc, outline=dark(hc, 0.5), w=3)

    # mouth
    my = HEAD_CY + 60
    mc = dark(skin, 0.55)
    if expr == "smile":
        pen.line(bez((CX - 18, my - 2), (CX - 8, my + 10), (CX + 8, my + 10), (CX + 18, my - 2)), mc, 4)
    elif expr == "grin":
        m = [(CX - 22, my - 4)] + bez((CX - 22, my - 4), (CX - 14, my + 20), (CX + 14, my + 20), (CX + 22, my - 4))[1:]
        pen.poly(m, "#6B2E33", outline=mc, w=3)
        pen.poly([(CX - 18, my - 2), (CX + 18, my - 2), (CX + 14, my + 5), (CX - 14, my + 5)], "#FFFFFF")
    elif expr == "smirk":
        pen.line(bez((CX - 16, my + 2), (CX - 4, my + 6), (CX + 8, my + 4), (CX + 20, my - 6)), mc, 4)
    elif expr == "serious":
        pen.line([(CX - 14, my + 2), (CX + 14, my + 2)], mc, 4)
    else:
        pen.ell(CX, my + 2, 7, 5, mix(mc, skin, 0.2))

    if fh == "mustache" or fh == "beard":
        pen.poly(smooth([(CX - 30, my - 4), (CX - 12, my - 16), (CX, my - 12), (CX + 12, my - 16),
                         (CX + 30, my - 4), (CX + 14, my - 6), (CX, my - 4), (CX - 14, my - 6)], n=6),
                 hc, outline=dark(hc, 0.5), w=2)

    if look["extras"] in ("freckles", "freckles_glasses"):
        rng = random.Random(5)
        for side in (-1, 1):
            for _ in range(6):
                pen.ell(CX + side * rng.uniform(38, 62), HEAD_CY + rng.uniform(28, 46), 2.2, 2.2, dark(skin, 0.3))
    if look["extras"] in ("glasses", "freckles_glasses"):
        gc = "#8A6A2A"
        for side in (-1, 1):
            pen.ell(CX + side * 34, ey, 25, 23, None, outline=gc, w=4)
        pen.arc(CX, ey - 2, 10, 8, 200, 340, gc, 4)
        for side in (-1, 1):
            pen.line([(CX + side * 59, ey - 4), (CX + side * 84, ey - 10)], gc, 4)


# ---------------------------------------------------------------- jewellery

def draw_necklace(img, g, glow=False):
    pen = Pen(img)
    chain = mat(g.get("chain", "gold"))
    col = mat(g.get("color", "#9B59B6"))
    px, py = CX, NECK_Y + 70
    left = bez((CX - 34, NECK_Y + 2), (CX - 36, NECK_Y + 44), (CX - 18, py - 8), (px, py - 12))
    right = [(2 * CX - x, y) for x, y in reversed(left)]
    kind = g.get("chain", "gold")
    if kind in ("cord", "leather"):
        pen.line(left + right[1:], dark(chain, 0.2), 4)
    elif kind == "vine":
        pen.line(left + right[1:], chain, 4)
        for x, y in (left[4], left[10], right[6], right[12]):
            pen.poly([(x, y), (x + 8, y - 6), (x + 12, y + 2)], light(chain, 0.2))
    elif kind == "pearl":
        for x, y in (left + right)[::2]:
            pen.ell(x, y, 4, 4, chain, outline=dark(chain, 0.35), w=1)
    else:
        pen.line(left + right[1:], dark(chain, 0.35), 4)
        for x, y in (left + right)[::2]:
            pen.ell(x, y, 2.4, 2.4, light(chain, 0.4))
    if glow:
        gl = new_layer()
        Pen(gl).ell(px, py + 10, 26, 26, light(col, 0.4), alpha=200)
        img.alpha_composite(glow_layer(gl, 10, 1.5))
        pen = Pen(img)
    draw_pendant(pen, g.get("pendant", "circle"), px, py + 8, col, chain)


def draw_pendant(pen, kind, x, y, col, metal):
    o = dark(col, 0.55)
    m = mat(metal) if metal not in ("cord", "leather", "vine", "pearl") else MATERIALS["gold"]
    if kind == "locket":
        pen.ell(x, y + 4, 15, 17, m, outline=dark(m, 0.5), w=3)
        pen.ell(x, y + 4, 8, 9, col, outline=o, w=2)
    elif kind == "drop":
        pen.poly(smooth([(x, y - 14), (x + 12, y + 8), (x, y + 20), (x - 12, y + 8)], n=8), col, outline=o, w=3)
        pen.ell(x - 4, y + 2, 3, 4, light(col, 0.7))
    elif kind == "coin":
        pen.ell(x, y + 4, 16, 16, col, outline=o, w=3)
        pen.ell(x, y + 4, 10, 10, None, outline=dark(col, 0.3), w=2)
    elif kind == "fang":
        pen.poly(smooth([(x - 9, y - 8), (x + 9, y - 8), (x + 5, y + 10), (x, y + 26), (x - 4, y + 10)], n=6), col, outline=o, w=3)
    elif kind == "tooth":
        pen.poly(smooth([(x - 12, y - 8), (x + 12, y - 8), (x + 8, y + 12), (x, y + 32), (x - 8, y + 12)], n=6), col, outline=o, w=3)
        pen.line([(x - 12, y - 6), (x + 12, y - 6)], m, 5)
    elif kind == "star":
        star(pen, x, y + 6, 20, dark(col, 0.4))
        star(pen, x, y + 6, 16, col)
    elif kind == "crescent":
        outer = [(x + 16 * math.cos(a), y + 6 + 16 * math.sin(a)) for a in [math.radians(d) for d in range(40, 330, 10)]]
        inner = [(x + 7 + 12 * math.cos(a), y + 3 + 12 * math.sin(a)) for a in [math.radians(d) for d in range(310, 50, -10)]]
        pen.poly(outer + inner, col, outline=o, w=3)
    elif kind == "eye":
        pen.poly(smooth([(x - 18, y + 6), (x, y - 6), (x + 18, y + 6), (x, y + 18)], n=6), m, outline=dark(m, 0.5), w=3)
        pen.ell(x, y + 6, 7, 7, col, outline=o, w=2)
        pen.ell(x, y + 6, 3, 3, (20, 16, 20))
    elif kind == "gem":
        pen.poly([(x - 12, y), (x, y - 8), (x + 12, y), (x, y + 20)], col, outline=o, w=3)
        pen.line([(x - 12, y), (x + 12, y)], light(col, 0.4), 2)
    elif kind == "leaf":
        pen.poly(smooth([(x, y - 12), (x + 12, y + 6), (x, y + 24), (x - 12, y + 6)], n=6), col, outline=o, w=3)
        pen.line([(x, y - 10), (x, y + 22)], dark(col, 0.3), 2)
    else:
        pen.ell(x, y + 4, 14, 14, col, outline=o, w=3)
        pen.ell(x - 4, y, 4, 4, light(col, 0.6))


def draw_bracelet(img, body, g):
    pen = Pen(img)
    sh, _, _ = BUILD[body]
    # sits on the wand-arm wrist, just below the cuff
    x, y = CX + sh + 50, 598
    band = mat(g.get("band", "gold"))
    acc = mat(g.get("color", "#C0392B"))
    style = g.get("style", "band")
    ang = -0.6
    def at(t):
        return (x + 22 * math.cos(ang) * t, y + 22 * math.sin(ang) * t)
    a, b = at(-1), at(1)
    if style == "beads":
        for t in (-1, -0.5, 0, 0.5, 1):
            px, py = at(t)
            pen.ell(px, py, 6, 6, acc if t != 0 else band, outline=dark(acc, 0.5), w=2)
    elif style == "cuff":
        pen.line([a, b], dark(band, 0.4), 14)
        pen.line([a, b], band, 10)
        mx, my = at(0)
        pen.ell(mx, my, 5, 5, acc, outline=dark(acc, 0.5), w=2)
    elif style == "vine":
        pen.line([a, b], band, 7)
        for t in (-0.6, 0.1, 0.8):
            px, py = at(t)
            pen.poly([(px, py), (px + 7, py - 8), (px + 11, py + 1)], acc)
    elif style == "chain":
        pen.line([a, b], dark(band, 0.35), 6)
        for t in (-0.8, -0.4, 0, 0.4, 0.8):
            px, py = at(t)
            pen.ell(px, py, 3.2, 3.2, light(band, 0.35))
    elif style == "laurel":
        pen.line([a, b], band, 7)
        for t in (-0.8, -0.4, 0, 0.4, 0.8):
            px, py = at(t)
            pen.ell(px + 3, py - 5, 5, 3, light(band, 0.2), outline=dark(band, 0.4), w=1)
    else:
        pen.line([a, b], dark(band, 0.4), 10)
        pen.line([a, b], band, 6)
    if g.get("charm"):
        mx, my = at(0.3)
        pen.line([(mx, my), (mx + 2, my + 14)], dark(band, 0.3), 2)
        draw_pendant(pen, g["charm"], mx + 2, my + 20, acc, g.get("band", "gold"))


def draw_ring(img, body, g):
    pen = Pen(img)
    (dx, dy), _ = arm_geometry(body), None
    dx, dy = dx
    band = mat(g.get("band", "gold"))
    x, y = dx + 4, dy + 6
    pen.arc(x, y, 9, 5, 0, 180, dark(band, 0.4), 6)
    pen.arc(x, y, 9, 5, 0, 180, band, 3)
    if g.get("color"):
        col = mat(g["color"])
        gl = new_layer()
        Pen(gl).ell(x, y + 5, 12, 12, light(col, 0.5), alpha=210)
        img.alpha_composite(glow_layer(gl, 5, 1.3))
        pen = Pen(img)
        pen.poly([(x - 6, y + 5), (x, y), (x + 6, y + 5), (x, y + 11)], col, outline=dark(col, 0.5), w=2)
    if g.get("charm") == "bell":
        pen.line([(x, y + 6), (x, y + 12)], band, 2)
        pen.poly(smooth([(x - 7, y + 22), (x - 5, y + 12), (x, y + 9), (x + 5, y + 12), (x + 7, y + 22)], closed=False, n=5)
                 + [(x - 7, y + 22)], MATERIALS["gold"], outline="#6E5220", w=2)


def draw_talisman(img, g):
    pen = Pen(img)
    x, y = CX - 58, WAIST_Y + 10
    col = mat(g.get("color", "#8E6B3E"))
    acc = mat(g.get("accent", "gold"))
    o = dark(col, 0.55)
    kind = g.get("shape", "disc")
    cord_end = (x - 4, y + 34)
    pen.line([(x, y), cord_end], dark(acc, 0.2), 3)
    tx, ty = x - 4, y + 56
    if kind == "feather":
        pen.poly(smooth([(tx, ty - 24), (tx + 10, ty), (tx + 4, ty + 34), (tx - 6, ty + 30), (tx - 10, ty)], n=6), col, outline=o, w=3)
        pen.line([(tx, ty - 20), (tx - 1, ty + 34)], light(col, 0.4), 2)
    elif kind == "pouch":
        pen.poly(smooth([(tx - 16, ty - 16), (tx + 16, ty - 16), (tx + 20, ty + 14), (tx, ty + 26), (tx - 20, ty + 14)], n=6), col, outline=o, w=3)
        pen.line([(tx - 16, ty - 12), (tx + 16, ty - 12)], acc, 3)
        pen.ell(tx, ty + 6, 5, 5, light(acc, 0.3))
    elif kind == "tassel":
        pen.ell(tx, ty - 14, 8, 8, acc, outline=dark(acc, 0.5), w=2)
        for k in range(7):
            pen.line([(tx - 9 + k * 3, ty - 8), (tx - 12 + k * 4, ty + 26)], col, 3)
    elif kind == "sigil":
        pen.ell(tx, ty, 20, 20, col, outline=o, w=3)
        star(pen, tx, ty, 12, acc)
        pen.ell(tx, ty, 14, 14, None, outline=acc, w=2)
    elif kind == "knot":
        for a in range(3):
            ang = a * 2.094
            pen.ell(tx + 9 * math.cos(ang), ty + 9 * math.sin(ang), 11, 11, None, outline=col, w=5)
    elif kind == "compass":
        pen.ell(tx, ty, 20, 20, acc, outline=dark(acc, 0.5), w=3)
        pen.ell(tx, ty, 15, 15, "#F4EEDC")
        pen.poly([(tx, ty - 12), (tx + 4, ty), (tx, ty + 12), (tx - 4, ty)], col)
    elif kind == "whistle":
        pen.rect([tx - 8, ty - 22, tx + 8, ty + 22], acc, radius=6, outline=dark(acc, 0.5), w=3)
        pen.ell(tx, ty - 8, 4, 4, dark(acc, 0.5))
        pen.ell(tx, ty + 18, 10, 6, col, outline=o, w=2)
    elif kind == "totem":
        pen.rect([tx - 12, ty - 24, tx + 12, ty + 28], col, radius=4, outline=o, w=3)
        for k in range(3):
            yy = ty - 14 + k * 16
            pen.ell(tx - 5, yy, 2.5, 2.5, acc)
            pen.ell(tx + 5, yy, 2.5, 2.5, acc)
            pen.line([(tx - 6, yy + 6), (tx + 6, yy + 6)], acc, 2)
    elif kind == "key":
        pen.ell(tx, ty - 14, 10, 10, None, outline=col, w=5)
        pen.line([(tx, ty - 4), (tx, ty + 28)], col, 5)
        pen.line([(tx, ty + 18), (tx + 10, ty + 18)], col, 4)
        pen.line([(tx, ty + 26), (tx + 8, ty + 26)], col, 4)
    elif kind == "disc":
        pen.ell(tx, ty, 18, 18, col, outline=o, w=3)
        pen.ell(tx, ty, 9, 9, acc)
    elif kind == "eye":
        draw_pendant(pen, "eye", tx, ty - 6, col, g.get("accent", "gold"))
    else:
        draw_pendant(pen, kind, tx, ty - 8, col, g.get("accent", "gold"))


# ================================================================ the wizard

def figure_layer(look: dict, house_color: str, gear: dict, wand: dict | None, sparks: bool = False):
    """Just the wizard, on a transparent 2x layer, full body."""
    skin = SKINS[look["skin"]][1]
    hair_c = HAIR_COLORS[look["hair_color"]][1]
    fig = new_layer()
    pen = Pen(fig)
    hair_back(pen, look["hair"], hair_c)
    draw_feet(pen, skin)
    draw_robe(fig, look["body"], house_color, skin)
    pen = Pen(fig)
    draw_neck_and_ears(pen, skin)
    draw_head(pen, skin)
    draw_face(fig, look, skin, hair_c)
    hair_front(fig, look["hair"], hair_c)
    draw_arms(fig, look["body"], house_color, skin)
    if gear.get("necklace"):
        draw_necklace(fig, gear["necklace"], glow=gear["necklace"].get("glow", False))
    if gear.get("talisman"):
        draw_talisman(fig, gear["talisman"])
    draw_wand_and_hand(fig, look["body"], skin, wand, sparks=sparks)
    if gear.get("bracelet"):
        draw_bracelet(fig, look["body"], gear["bracelet"])
    if gear.get("ring"):
        draw_ring(fig, look["body"], gear["ring"])
    return fig


# ================================================================ the card

CW, CH = 680, 1000
FIG_CROP = (110, 180, 690, 780)      # head to knees, in picture units - every gear slot shows
WIN = (48, 120, CW - 48, 600)         # the picture window on the card


def _fit(d, text, name, size, weight, maxw):
    f = font(name, size, weight)
    while d.textlength(text, font=f) > maxw * S and size > 12:
        size -= 1
        f = font(name, size, weight)
    return f


def card_number(user_id: int) -> int:
    return int(hashlib.sha256(f"card:{user_id}".encode()).hexdigest()[:8], 16) % 999 + 1


def render(*, look: dict, user_id: int, name: str, title: str | None = None,
           house_color: int | str = 0x6C5CE7, house_emoji: str | None = None,
           gear: dict | None = None, wand: dict | None = None, beast_emoji: str | None = None,
           stats: list | None = None, motto: str | None = None, stars: int = 1,
           aura: bool = False, gold_trim: bool = False, wand_sparks: bool = False) -> bytes:
    """The whole trading card as PNG bytes. gear maps slot -> visual dict
    (see gear_data). stats is up to three (label, value) pairs."""
    look = clean_look(look, user_id)
    gear = gear or {}
    hcol = hexint(house_color) if isinstance(house_color, int) else house_color
    hc = rgb(hcol)
    fig = figure_layer(look, hcol, gear, wand, sparks=wand_sparks)

    card = Image.new("RGBA", (CW * S, CH * S), (0, 0, 0, 0))
    # ---- foil edge: a diagonal sheen in the house colours (gold for House Cup winners)
    foil = Image.new("RGBA", card.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(foil)
    base = (222, 178, 74) if gold_trim else light(hc, 0.3)
    for i in range(-CH, CW + CH, 6):
        t = (i % 180) / 180
        c = mix(base, (255, 244, 205), 0.5 + 0.5 * math.sin(t * 2 * math.pi))
        c = mix(c, (255, 226, 140) if gold_trim else (185, 222, 255), 0.25 + 0.25 * math.cos(t * 4 * math.pi))
        fd.line([(i * S, 0), ((i + CH) * S, CH * S)], fill=rgba(c), width=7 * S)
    m = Image.new("L", card.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, CW * S, CH * S], radius=34 * S, fill=255)
    foil.putalpha(m)
    card.alpha_composite(foil)
    pen = Pen(card)
    pen.rect([0, 0, CW, CH], None, radius=34, outline=dark(hc, 0.6), w=4)
    inner = dark(hc, 0.55)
    pen.rect([22, 22, CW - 22, CH - 22], inner, radius=22)

    # ---- header: name + house badge
    pen.rect([40, 40, CW - 40, 104], light(hc, 0.75), radius=14, outline=dark(hc, 0.5), w=3)
    fname = _fit(pen.d, name, "Cinzel.ttf", 36, 800, CW - 200)
    pen.d.text((64 * S, 72 * S), name, font=fname, fill=rgba(dark(hc, 0.7)), anchor="lm")
    pen.ell(CW - 76, 72, 26, 26, "#F2E6C4", outline=dark(hc, 0.5), w=3)
    em = emoji_image(house_emoji, 30) if house_emoji else None
    if em:
        paste_center(card, em, CW - 76, 72)

    # ---- the picture window
    wx0, wy0, wx1, wy1 = WIN
    win = Image.new("RGBA", card.size, (0, 0, 0, 0))
    wp = Pen(win)
    for y in range(wy0, wy1):
        t = (y - wy0) / (wy1 - wy0)
        wp.d.line([(wx0 * S, y * S), (wx1 * S, y * S)], fill=rgba(mix(light(hc, 0.6), hc, t)), width=S)
    rng = random.Random(user_id)
    for _ in range(18):
        star(wp, rng.uniform(wx0, wx1), rng.uniform(wy0, wy1), rng.uniform(3, 8), (255, 255, 240),
             alpha=rng.randint(120, 230))
    halo = new_layer_card()
    Pen(halo).ell(CW / 2, 300, 170, 170, (255, 246, 220), alpha=170)
    win.alpha_composite(glow_layer(halo, 30, 1.2))
    crop = fig.crop(tuple(v * S for v in FIG_CROP))
    k = (wy1 - wy0 + 10) * S / crop.height
    crop = crop.resize((int(crop.width * k), int(crop.height * k)), Image.LANCZOS)
    ox, oy = int(CW / 2 * S - crop.width / 2), int((wy0 + 4) * S)
    if aura:
        a = Image.new("RGBA", crop.size, (255, 214, 110, 0))
        a.putalpha(crop.getchannel("A"))
        glow = new_layer_card()
        glow.alpha_composite(a, (ox, oy))
        win.alpha_composite(glow_layer(glow, 16, 1.8))
    win.alpha_composite(crop, (ox, oy))
    if beast_emoji:
        be = emoji_image(beast_emoji, 96)
        if be:
            win.alpha_composite(be, (int((wx1 - 14) * S - be.width), int((wy1 - 56) * S - be.height)))
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(B([wx0, wy0, wx1, wy1]), radius=10 * S, fill=255)
    win.putalpha(ImageChops.multiply(win.getchannel("A"), mask))
    card.alpha_composite(win)
    pen = Pen(card)
    pen.rect([wx0, wy0, wx1, wy1], None, radius=10, outline="#F2E6C4", w=5)

    # ---- title ribbon
    ttext = title or "Student of Velmora"
    pen.rect([80, 580, CW - 80, 630], "#F2E6C4", radius=10, outline=dark(hc, 0.5), w=3)
    ft = _fit(pen.d, ttext, "Alegreya-Italic.ttf", 28, 600, CW - 200)
    pen.d.text((CW / 2 * S, 605 * S), ttext, font=ft, fill=rgba(dark(hc, 0.65)), anchor="mm")

    # ---- stats + motto
    stats = (stats or [])[:3]
    pen.rect([48, 648, CW - 48, 830], "#F7EFD9", radius=12, outline=dark(hc, 0.4), w=3)
    if stats:
        fs, colw = font("Cinzel.ttf", 22, 700), (CW - 96) / len(stats)
        for i, (label, value) in enumerate(stats):
            x = 48 + colw * (i + 0.5)
            fv = _fit(pen.d, str(value), "Cinzel.ttf", 30, 800, colw - 16)
            pen.d.text((x * S, 712 * S), str(value), font=fv, fill=rgba(dark(hc, 0.6)), anchor="mm")
            pen.d.text((x * S, 752 * S), label, font=fs, fill=rgba("#6B5A48"), anchor="mm")
            if i:
                pen.line([(48 + colw * i, 680), (48 + colw * i, 780)], light(dark(hc, 0.4), 0.5), 2)
    if motto:
        quote = f"\u201c{motto}\u201d"
        fm = _fit(pen.d, quote, "Alegreya-Italic.ttf", 21, 500, CW - 130)
        pen.d.text((CW / 2 * S, 802 * S), quote, font=fm, fill=rgba("#5A4A3A"), anchor="mm")

    # ---- rarity stars + card number
    n = max(1, min(4, stars))
    for i in range(4):
        star(pen, CW / 2 - 66 + i * 44, 880, 16, "#F2C94C" if i < n else light(inner, 0.25))
    fn = font("Cinzel.ttf", 18, 600)
    pen.d.text((CW / 2 * S, 930 * S), f"VELMORA  \u00b7  No. {card_number(user_id):03d}", font=fn,
               fill=rgba(light(hc, 0.6)), anchor="mm")

    out = card.resize((CW, CH), Image.LANCZOS)
    bg = Image.new("RGB", out.size, (20, 16, 22))
    bg.paste(out, mask=out.getchannel("A"))
    buf = io.BytesIO()
    bg.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def new_layer_card():
    return Image.new("RGBA", (CW * S, CH * S), (0, 0, 0, 0))
