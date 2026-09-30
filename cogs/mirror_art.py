"""
The Mirror - player's wizard as a collectible trading card.

Portrait is composited from layered sprite packs under
wizard_assets/{male,female,female_full} (STATE_DIR on Railway, or data/ locally).
  - female:      Tainara-P style (Body/Head/Hair folders)
  - female_full: Girl Sprites Premium (full figure on the card)
  - male:        MALE1 muscular pack (flat body/hair_top/… folders) when present
House identity is the card colour (not robes on the figure). Worn gear
is shown as icons beside the portrait, not drawn on the body.

    render(...) -> PNG bytes
"""

from __future__ import annotations

import hashlib
import io
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from cogs.wizard_assets_bootstrap import assets_root, crests_root, female_full_ready
from cogs import mirror_male1 as male1
from cogs import mirror_girl_premium as girl_full

FONT_DIR = Path(__file__).resolve().parent.parent / "data" / "fonts"
ASSETS = assets_root()
CRESTS = crests_root()

# Allow hot-reload of paths after bootstrap copies into STATE_DIR.
def refresh_asset_paths() -> None:
    global ASSETS, CRESTS
    ASSETS = assets_root()
    CRESTS = crests_root()

# Final card size (Discord-friendly)
CW, CH = 720, 1120  # taller so earned titles fit under the motto
S = 2  # draw at 2x then scale

# Source sprites are 1400x1200
SRC_W, SRC_H = 1400, 1200

# ---------------------------------------------------------------- look options
# Keys are what we store on the player. Labels are what /wizard shows.
# Discord selects max out at 25 choices, so lists stay under that.

GENDERS = {
    "male": "Masculine",
    "female": "Feminine",
    "female_full": "Feminine (full body)",
}


def available_genders() -> dict:
    """Presentations offered in /wizard."""
    out = {"male": GENDERS["male"], "female": GENDERS["female"]}
    if female_full_ready(ASSETS):
        out["female_full"] = GENDERS["female_full"]
    return out


def _is_feminine(gender: str) -> bool:
    return gender in ("female", "female_full")

SKINS = {str(i): f"Skin tone {i}" for i in range(1, 10)}

# Hair Back = the actual cut. (Legacy Tainara male menus — used only if the
# old male pack is still on disk; MALE1 overrides via options_for.)
HAIR_BACK_MALE = {
    "1": "Soft bowl",
    "4": "Messy tufts",
    "5": "Short spikes",
    "6": "Textured short",
    "7": "Smooth round",
    "9": "Wild spikes",
    "15": "Tall spikes",
    "16": "Messy crop",
    "17": "Short crop",
    "18": "Spiky volume",
}

HAIR_BACK_FEMALE = {
    "1": "Straight long",
    "2": "Soft waves",
    "3": "Layered",
    "4": "Full volume",
    "5": "Sleek",
    "6": "Wavy mid",
    "7": "Long cascade",
    "8": "Soft bob",
    "9": "Fluffy",
    "10": "Long layers",
    "11": "Silky",
    "12": "Thick waves",
    "13": "Gentle curl",
    "14": "Long smooth",
    "15": "Voluminous",
    "16": "Flowing",
    "17": "Soft length",
    "18": "Classic long",
}

# Bangs = fringe overlay. Masculine menu is curated short-only so it doesn't
# read as long hair; feminine keeps a fuller set (Discord max 25 options).
HAIR_BANGS_MALE = {
    "none": "No fringe (short hairline)",
    "1": "Short clean fringe",
    "6": "Short spikes",
    "7": "Jagged short",
    "9": "Choppy crop fringe",
    "10": "Pushed-back hairline",
}

HAIR_BANGS_FEMALE = {
    "none": "No fringe (short hairline)",
    "1": "Short fringe",
    "2": "Side-swept",
    "3": "Straight mid",
    "4": "Full blunt",
    "5": "Choppy side",
    "6": "Spiky fringe",
    "7": "Even spikes",
    "8": "Center part long",
    "9": "Short choppy",
    "10": "Open forehead",
    "11": "Mid with gap",
    "12": "Long rounded",
    "13": "Long flat",
    "14": "Heavy side sweep",
    "15": "Long face frame",
    "16": "Bangs 16",
    "17": "Bangs 17",
    "18": "Bangs 18",
    "19": "Bangs 19",
    "20": "Bangs 20",
}

# In these packs the crown/front hair lives in Bangs/. Skipping that layer
# leaves a bald scalp. "none" still draws a short hairline fill:
BANGS_NONE_LAYER = {"male": "6", "female": "1", "female_full": "hair_1"}

# Unions used for validation / storage (either presentation may have saved a key).
HAIR_BACK = {**HAIR_BACK_FEMALE, **HAIR_BACK_MALE, **male1.HAIR_BACK}
HAIR_BANGS = {**HAIR_BANGS_FEMALE, **HAIR_BANGS_MALE, **male1.HAIR_TOP}

HAIR_COLORS = {
    "1": "Black",
    "2": "Dark brown",
    "3": "Auburn",
    "4": "Light brown",
    "5": "Wine red",
    "6": "Pink",
    "7": "Purple",
    "8": "Blue",
    "9": "Green",
    "10": "Silver",
}

EYE_TYPES = {
    "1": "Round",
    "2": "Almond",
    "3": "Soft lidded",
    "4": "Sharp",
    "5": "Wide",
    "6": "Tired",
}

MOUTHS = {
    "Smile": "Smile", "Smiling": "Soft smile", "Grin": "Grin", "Big Smile": "Big smile",
    "Neutro": "Neutral", "Serious": "Serious", "Smirk": "Smirk", "Laugh": "Laugh",
    "Embarrassed": "Embarrassed", "Sulking": "Sulking", "Surprised": "Surprised",
    "Angry": "Angry", "Sad": "Sad", "Cheeky": "Cheeky",
    **male1.MOUTHS,
}

BROWS = {
    "Neutro": "Neutral brows", "Serious": "Serious brows", "Curved": "Curved brows",
    "Up": "Raised brows", "Sad": "Sad brows", "Angry": "Angry brows",
    "Confused": "Confused brows",
    **male1.BROWS,
}

# Feminine pack outfits (Discord max 25).
CLOTHES_FEMALE = {str(i): f"Outfit {i}" for i in range(1, 25)}

# Masculine pack: named cuts that read male (uniforms, shirts, jackets).
CLOTHES_MALE = {
    "1": "Gakuran closed",
    "2": "Gakuran open collar",
    "3": "Gakuran over shirt",
    "4": "White dress shirt",
    "5": "Open dress shirt",
    "6": "Sport tee",
    "7": "Track jacket open",
    "8": "Track jacket half",
    "9": "Track jacket zipped",
    "10": "Blue button-down",
    "11": "Plain tee",
    "15": "Basketball jersey",
    "16": "Blue vest",
    "17": "Denim jacket",
    "18": "Hoodie",
    "20": "Beige sweater",
    "21": "Suit vest + tie",
    "22": "Blazer",
    "28": "Grey polo",
    "29": "Black button shirt",
    "30": "Khaki polo",
    "34": "Logo tee",
}

CLOTHES = {**CLOTHES_FEMALE, **CLOTHES_MALE, **male1.CLOTHES}

CLOTHES_COLORS = {
    "1": "Black",
    "2": "White",
    "3": "Blue",
    "4": "Green",
    "5": "Red",
    "6": "Lavender",
    "7": "Cream",
    "8": "Leaf green",
    **male1.CLOTHES_COLORS,
}

# Natural-looking defaults for legacy masculine randomize.
MALE_HAIR_COLORS = ("1", "2", "3", "4", "5", "10")
MALE_BROWS = ("Serious", "Neutro", "Angry")
MALE_EYES = ("4", "2", "1")  # Sharp, Almond, Round

GLASSES = {
    "none": "No glasses",
    "1": "Glasses 1", "2": "Glasses 2", "3": "Glasses 3", "4": "Glasses 4", "5": "Glasses 5",
    **male1.BEARDS,
}

IRIS_TYPES = {
    "1": "Classic",
    "2": "Soft glow",
    "3": "Ringed",
    "4": "Bright",
    **male1.NOSES,
}

IRIS_COLORS = {
    "1": "Dark brown",
    "2": "Plum",
    "3": "Crimson",
    "4": "Deep red",
    "5": "Rose red",
    "6": "Copper",
    "7": "Amber",
    "8": "Forest green",
    "9": "Bright green",
    "10": "Teal",
    "11": "Steel blue",
    "12": "Sea green",
    "13": "Sky blue",
    "14": "Royal blue",
    "15": "Purple",
    "16": "Midnight",
    "17": "Violet",
    "18": "Rose",
    "19": "Grey",
    **male1.PUPILS,
}

LOOK_FIELDS = {
    "gender": GENDERS,
    "skin": SKINS,
    "hair_back": HAIR_BACK,
    "hair_bangs": HAIR_BANGS,
    "hair_color": HAIR_COLORS,
    "eyes": EYE_TYPES,
    "iris_type": IRIS_TYPES,
    "iris_color": IRIS_COLORS,
    "brows": BROWS,
    "mouth": MOUTHS,
    "clothes": CLOTHES,
    "clothes_color": CLOTHES_COLORS,
    "glasses": GLASSES,
}


def _use_male1() -> bool:
    return male1.is_male1_root(ASSETS / "male")


def options_for(field: str, look: dict | None = None) -> dict:
    """Menu options for a field, filtered by presentation when it matters."""
    look = look or {}
    gender = look.get("gender", "female")
    if field == "gender":
        return available_genders()
    if gender == "female_full" and girl_full.is_girl_premium_root(ASSETS / "female_full"):
        g = girl_full.options(field)
        if g:
            return g
    if gender == "male" and _use_male1():
        m1 = male1.options(field)
        if m1:
            return m1
        if field == "skin":
            return male1.SKINS
    if field == "hair_back":
        return HAIR_BACK_MALE if gender == "male" else HAIR_BACK_FEMALE
    if field == "hair_bangs":
        return HAIR_BANGS_MALE if gender == "male" else HAIR_BANGS_FEMALE
    if field == "clothes":
        return CLOTHES_MALE if gender == "male" else CLOTHES_FEMALE
    return LOOK_FIELDS[field]


def option_label(field: str, key: str, look: dict | None = None) -> str:
    opts = options_for(field, look) if look is not None else LOOK_FIELDS.get(field, {})
    v = opts.get(key, LOOK_FIELDS.get(field, {}).get(key, key))
    return v[0] if isinstance(v, tuple) else v


DEFAULT_FIELD_LABEL = {
    "gender": "Presentation", "skin": "Skin tone", "clothes": "Outfit", "clothes_color": "Outfit colour",
    "hair_back": "Hair style", "hair_bangs": "Bangs", "hair_color": "Hair colour",
    "eyes": "Eye shape", "iris_type": "Iris style", "iris_color": "Eye colour",
    "brows": "Brows", "mouth": "Expression", "glasses": "Glasses",
}


def field_label(field: str, look: dict | None = None) -> str:
    """Human placeholder for a /wizard select."""
    look = look or {}
    if look.get("gender") == "female_full":
        override = girl_full.field_label(field)
        if override:
            return override
    if look.get("gender") == "male" and _use_male1():
        override = male1.field_label(field)
        if override:
            return override
    return DEFAULT_FIELD_LABEL.get(field, field)


def clamp_hair_to_gender(look: dict) -> dict:
    """Drop styles/outfits that aren't in the current presentation's menu."""
    if look.get("gender") == "female_full":
        return girl_full.clamp_look(look)
    if look.get("gender") == "male" and _use_male1():
        return male1.clamp_look(look)
    male = look.get("gender") == "male"
    fallbacks = {
        "hair_back": "17" if male else "1",
        "hair_bangs": "1" if male else "none",
        "clothes": "1",
    }
    for field, fallback in fallbacks.items():
        opts = options_for(field, look)
        if look.get(field) not in opts:
            look[field] = fallback if fallback in opts else next(iter(opts))
    return look


def _roll_presentation(look: dict, rng: random.Random) -> dict:
    """Re-roll presentation-specific fields for the active pack."""
    if look.get("gender") == "female_full":
        for field in ("skin", "hair_back", "hair_bangs", "hair_color", "eyes",
                      "iris_type", "iris_color", "brows", "mouth",
                      "clothes", "clothes_color"):
            opts = options_for(field, look)
            if opts:
                look[field] = rng.choice(sorted(opts))
        look["glasses"] = "none" if rng.random() < 0.55 else rng.choice(
            [k for k in options_for("glasses", look) if k != "none"] or ["none"])
        return look
    if look.get("gender") == "male" and _use_male1():
        for field in ("skin", "hair_back", "hair_bangs", "hair_color", "eyes",
                      "iris_type", "iris_color", "brows", "mouth",
                      "clothes_color"):
            opts = options_for(field, look)
            look[field] = rng.choice(sorted(opts))
        tops = options_for("clothes", look)
        preferred = [k for k in (
            "basic_shirt", "shirt", "short_shirt", "sweatshirt", "jacket",
            "rustic_shirt", "open_shirt", "ample_sweater", "long_jacket",
        ) if k in tops]
        look["clothes"] = rng.choice(preferred or sorted(tops))
        look["glasses"] = "none" if rng.random() < 0.55 else rng.choice(
            [k for k in options_for("glasses", look) if k != "none"] or ["none"])
        return look
    look["hair_back"] = rng.choice(sorted(options_for("hair_back", look)))
    look["clothes"] = rng.choice(sorted(options_for("clothes", look)))
    bangs_opts = options_for("hair_bangs", look)
    if look.get("gender") == "male":
        short = [k for k in ("1", "6", "7", "9") if k in bangs_opts]
        if rng.random() < 0.2 or not short:
            look["hair_bangs"] = "none"
        else:
            look["hair_bangs"] = rng.choice(short)
        look["brows"] = rng.choice(MALE_BROWS)
        look["eyes"] = rng.choice(MALE_EYES)
        look["hair_color"] = rng.choice(MALE_HAIR_COLORS)
    else:
        look["hair_bangs"] = "none" if rng.random() < 0.25 else rng.choice(
            sorted(bangs_opts))
    return look


def default_look(user_id: int) -> dict:
    rng = random.Random(int(hashlib.sha256(str(user_id).encode()).hexdigest()[:12], 16))
    look = {f: rng.choice(sorted(opts)) for f, opts in LOOK_FIELDS.items()}
    _roll_presentation(look, rng)
    if not (look.get("gender") == "male" and _use_male1()):
        look["glasses"] = "none" if rng.random() < 0.7 else look["glasses"]
    return clamp_hair_to_gender(look)


def clean_look(look: dict | None, user_id: int) -> dict:
    """Accept current fields; ignore legacy Pillow look keys."""
    base = default_look(user_id)
    if not look:
        return clamp_hair_to_gender(base)
    genders = available_genders()
    if look.get("gender") in genders:
        base["gender"] = look["gender"]
    elif look.get("gender") == "female_full" and "female_full" not in genders:
        base["gender"] = "female"
    for f in LOOK_FIELDS:
        if f == "gender":
            continue
        opts = options_for(f, base)
        if look.get(f) in opts:
            base[f] = look[f]
    return clamp_hair_to_gender(base)


# ---------------------------------------------------------------- colour / fonts

def rgb(c) -> tuple:
    if isinstance(c, tuple):
        return c[:3]
    c = str(c).lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t: float) -> tuple:
    a, b = rgb(a), rgb(b)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def light(c, t=0.35):
    return mix(c, (255, 250, 240), t)


def dark(c, t=0.35):
    return mix(c, (20, 16, 24), t)


def rgba(c, a=255):
    return rgb(c) + (a,)


def hexint(n: int) -> str:
    return f"#{n:06X}"


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


# ---------------------------------------------------------------- asset paths

def _root(gender: str) -> Path:
    if gender == "female_full":
        return ASSETS / "female_full"
    if _is_feminine(gender):
        return ASSETS / "female"
    return ASSETS / "male"


def _open_layer(path: Path) -> Image.Image | None:
    if not path or not path.exists():
        return None
    try:
        return Image.open(path).convert("RGBA")
    except OSError:
        return None


def _first_existing(candidates: list[Path]) -> Path | None:
    for p in candidates:
        if p.exists():
            return p
    return None


def _clothes_color_path(root: Path, clothes: str, color: str) -> Path | None:
    folder = root / "Body" / "Clothes" / f"Clothes {clothes}"
    if not folder.is_dir():
        return None
    exact = folder / f"Color {color}.png"
    if exact.exists():
        return exact
    # fall back to first available colour
    colors = sorted(folder.glob("Color *.png"), key=lambda p: int(p.stem.split()[-1]))
    return colors[0] if colors else None


def _glasses_path(root: Path, glasses: str, color: str) -> Path | None:
    if glasses == "none":
        return None
    folder = root / "Accessory" / "Glasses" / f"Glasses {glasses}"
    if not folder.is_dir():
        return None
    exact = folder / f"Color {color}.png"
    if exact.exists():
        return exact
    colors = sorted(folder.glob("Color *.png"), key=lambda p: int(p.stem.split()[-1]))
    return colors[0] if colors else None


def compose_portrait(look: dict) -> Image.Image:
    """Stack sprite layers into a portrait (typically ~1400x1200)."""
    gender = look.get("gender", "male")
    root = _root(gender)
    if gender == "female_full" and girl_full.is_girl_premium_root(root):
        return girl_full.compose(root, look, (SRC_W, SRC_H))
    if gender == "male" and male1.is_male1_root(root):
        return male1.compose(root, look, (SRC_W, SRC_H))

    canvas = Image.new("RGBA", (SRC_W, SRC_H), (0, 0, 0, 0))

    skin = look.get("skin", "1")
    hair_c = look.get("hair_color", "1")
    hair_back = look.get("hair_back", "1")
    hair_bangs = look.get("hair_bangs", "none")
    eyes = look.get("eyes", "1")
    iris_t = look.get("iris_type", "1")
    iris_c = look.get("iris_color", "1")
    brows = look.get("brows", "Neutro")
    mouth = look.get("mouth", "Smile")
    clothes = look.get("clothes", "1")
    clothes_c = look.get("clothes_color", "1")
    glasses = look.get("glasses", "none")

    layers: list[Path | None] = []

    # 1. hair back
    layers.append(root / "Hair" / "Back" / f"Back {hair_back}" / f"Color {hair_c}.png")

    # 2. body (skin)
    layers.append(root / "Body" / "Body" / f"Body {skin}.png")

    # 3. clothes
    layers.append(_clothes_color_path(root, clothes, clothes_c))

    # 4. face (match skin tone index)
    face = root / "Head" / "Face" / f"Face {skin}.png"
    if not face.exists():
        face = root / "Head" / "Face" / "Face 1.png"
    layers.append(face)

    # 5. eyes: sclera → iris → shadow
    eye_dir = root / "Head" / "Eyes" / f"Eye Type {eyes}"
    layers.append(eye_dir / "Sclera" / "Normal" / "Sclera 1.png")
    layers.append(root / "Head" / "Eyes" / "Iris" / f"Type {iris_t}" / f"Iris {iris_c}.png")
    # shadow variants are numbered; prefer Shadow 1
    shadow = _first_existing([
        eye_dir / "Shadow" / "Normal" / "Shadow 1.png",
        eye_dir / "Shadow" / "Normal" / "Shadow 2.png",
    ])
    layers.append(shadow)

    # 6. brows (Type 1 = expression files, no colour)
    brow = root / "Head" / "Brows" / "Brow Type 1" / f"{brows}.png"
    if not brow.exists():
        brow = root / "Head" / "Brows" / "Brow Type 1" / "Neutro.png"
    layers.append(brow)

    # 7. mouth
    mouth_p = _first_existing([
        root / "Head" / "Mouth" / "Mouth Type 1" / f"{mouth}.png",
        root / "Head" / "Mouth" / "Mouth Type 2" / f"{mouth}.png",
        root / "Head" / "Mouth" / "Mouth Type 1" / "Smile.png",
    ])
    layers.append(mouth_p)

    # 8. bangs — crown/front hair lives here; "none" still draws a short
    # hairline fill so the scalp isn't bald.
    bangs_id = BANGS_NONE_LAYER.get(gender, "1") if hair_bangs == "none" else hair_bangs
    layers.append(root / "Hair" / "Bangs" / f"Bangs {bangs_id}" / f"Color {hair_c}.png")

    # 9. glasses (use outfit colour as a stand-in accent)
    layers.append(_glasses_path(root, glasses, clothes_c))

    for path in layers:
        if path is None:
            continue
        im = _open_layer(path)
        if im is None:
            continue
        if im.size != (SRC_W, SRC_H):
            im = im.resize((SRC_W, SRC_H), Image.LANCZOS)
        canvas.alpha_composite(im)

    return canvas


# ---------------------------------------------------------------- card

def _fit(d, text, name, size, weight, maxw):
    f = font(name, size, weight)
    while d.textlength(text, font=f) > maxw * S and size > 12:
        size -= 1
        f = font(name, size, weight)
    return f


def load_crest(house_key: str | None, size: int, opacity: float = 1.0) -> Image.Image | None:
    """House crest PNG/JPG, black backdrop knocked out, sized to `size` px (picture units).

    opacity: 0..1 multiplier on the alpha channel (for watermark / background use).
    """
    if not house_key:
        return None
    path = CRESTS / f"{house_key}.jpg"
    if not path.exists():
        path = CRESTS / f"{house_key}.png"
    if not path.exists():
        return None
    try:
        im = Image.open(path).convert("RGBA")
    except OSError:
        return None
    # Knock out near-black studio background so the crest sits clean on the card.
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if r < 18 and g < 18 and b < 18:
                px[x, y] = (0, 0, 0, 0)
    # Crop to content
    box = im.getbbox()
    if box:
        im = im.crop(box)
    scale = size * S / max(im.size)
    im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    if opacity < 1.0:
        opacity = max(0.0, min(1.0, opacity))
        r, g, b, a = im.split()
        a = a.point(lambda p: int(p * opacity))
        im = Image.merge("RGBA", (r, g, b, a))
    return im


def render(*, look: dict, user_id: int, name: str, title: str | None = None,
           titles: list | None = None,
           house_key: str | None = None, house_color: int | str = 0x6C5CE7,
           house_emoji: str | None = None,
           gear: dict | None = None, gear_icons: list | None = None,
           wand: dict | None = None, beast_emoji: str | None = None,
           stats: list | None = None, motto: str | None = None, stars: int = 1,
           aura: bool = False, gold_trim: bool = False, wand_sparks: bool = False) -> bytes:
    """Full trading card as PNG bytes.

    gear_icons: optional list of {slot, emoji, name} for the side panel.
    gear: legacy on-body visuals — ignored (kept so older callers don't break).
    """
    look = clean_look(look, user_id)
    hcol = hexint(house_color) if isinstance(house_color, int) else house_color
    hc = rgb(hcol)

    portrait = compose_portrait(look)

    card = Image.new("RGBA", (CW * S, CH * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)

    # foil / house-coloured edge
    foil = Image.new("RGBA", card.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(foil)
    base = (222, 178, 74) if gold_trim else light(hc, 0.25)
    for i in range(-CH, CW + CH, 7):
        t = (i % 160) / 160
        c = mix(base, (255, 244, 205), 0.45 + 0.45 * math.sin(t * 2 * math.pi))
        fd.line([(i * S, 0), ((i + CH) * S, CH * S)], fill=rgba(c), width=8 * S)
    m = Image.new("L", card.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, CW * S, CH * S], radius=32 * S, fill=255)
    foil.putalpha(m)
    card.alpha_composite(foil)

    # inner panel
    d.rounded_rectangle([18 * S, 18 * S, (CW - 18) * S, (CH - 18) * S],
                        radius=24 * S, fill=rgba(dark(hc, 0.55)))

    # header bar
    d.rounded_rectangle([36 * S, 36 * S, (CW - 36) * S, 100 * S],
                        radius=14 * S, fill=rgba(light(hc, 0.72)),
                        outline=rgba(dark(hc, 0.45)), width=3 * S)
    fname = _fit(d, name, "Cinzel.ttf", 34, 800, CW - 200)
    d.text((56 * S, 68 * S), name, font=fname, fill=rgba(dark(hc, 0.75)), anchor="lm")
    # Small crest in the header (falls back to house emoji)
    header_crest = load_crest(house_key, 44)
    if header_crest:
        paste_center(card, header_crest, CW - 70, 68)
    elif house_emoji:
        em = emoji_image(house_emoji, 28)
        if em:
            paste_center(card, em, CW - 70, 68)

    # portrait window (left/main)
    wx0, wy0, wx1, wy1 = 40, 118, 500, 620
    # house-coloured wash behind portrait
    for y in range(wy0, wy1):
        t = (y - wy0) / max(1, wy1 - wy0)
        d.line([(wx0 * S, y * S), (wx1 * S, y * S)],
               fill=rgba(mix(light(hc, 0.55), hc, t * 0.85)))

    win_mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(win_mask).rounded_rectangle(
        [wx0 * S, wy0 * S, wx1 * S, wy1 * S], radius=12 * S, fill=255)

    # Large faded house crest as watermark BEHIND the character
    bg_crest = load_crest(house_key, size=int((wy1 - wy0) * 1.45), opacity=0.14)
    if bg_crest:
        crest_layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
        cx = (wx0 + wx1) / 2
        cy = (wy0 + wy1) / 2
        paste_center(crest_layer, bg_crest, cx, cy)
        crest_layer.putalpha(ImageChops.multiply(crest_layer.getchannel("A"), win_mask))
        card.alpha_composite(crest_layer)

    # scale portrait to fit window height
    target_h = (wy1 - wy0 - 8) * S
    scale = target_h / portrait.height
    pw = int(portrait.width * scale)
    ph = int(portrait.height * scale)
    port = portrait.resize((pw, ph), Image.LANCZOS)

    ox = int(((wx0 + wx1) / 2) * S - pw / 2)
    oy = int((wy0 + 4) * S)

    if aura:
        # Legend's Tooth — soft gold halo behind the portrait silhouette.
        # (Must use ImageFilter.GaussianBlur directly; a nested __import__
        # path raises AttributeError and fogs the whole Mirror.)
        glow = Image.new("RGBA", port.size, (255, 214, 110, 255))
        glow.putalpha(port.getchannel("A"))
        halo = glow.filter(ImageFilter.GaussianBlur(14))
        aura_layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
        aura_layer.paste(halo, (max(0, ox - 4), max(0, oy - 4)), halo)
        card.alpha_composite(aura_layer)

    layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
    layer.alpha_composite(port, (ox, oy))
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), win_mask))
    card.alpha_composite(layer)
    d.rounded_rectangle([wx0 * S, wy0 * S, wx1 * S, wy1 * S],
                        radius=12 * S, outline=rgba(dark(hc, 0.5)), width=3 * S)

    # side panel: worn gear icons
    icons = gear_icons or []
    sx0, sy0 = 520, 130
    panel_w = 160
    d.rounded_rectangle([sx0 * S, sy0 * S, (sx0 + panel_w) * S, 620 * S],
                        radius=12 * S, fill=rgba(light(hc, 0.12)),
                        outline=rgba(dark(hc, 0.4)), width=2 * S)
    d.text(((sx0 + panel_w / 2) * S, (sy0 + 18) * S), "Worn",
           font=font("Cinzel.ttf", 16, 700), fill=rgba(light(hc, 0.85)), anchor="mm")

    slots_order = ["necklace", "bracelet", "ring", "talisman"]
    slot_emoji = {"necklace": "📿", "bracelet": "⛓️", "ring": "💍", "talisman": "🧿"}
    by_slot = {g.get("slot"): g for g in icons}
    y = sy0 + 48
    for slot in slots_order:
        g = by_slot.get(slot)
        box = [sx0 + 12, y, sx0 + panel_w - 12, y + 88]
        d.rounded_rectangle([b * S for b in box], radius=10 * S,
                            fill=rgba(mix(hc, (30, 24, 36), 0.55) if g else dark(hc, 0.62)),
                            outline=rgba(light(hc, 0.4)), width=2 * S)
        em = emoji_image((g or {}).get("emoji") or slot_emoji[slot], 28)
        if em:
            paste_center(card, em, sx0 + panel_w / 2, y + 28)
        label = (g or {}).get("name") or "Empty"
        if len(label) > 16:
            label = label[:15] + "…"
        d.text(((sx0 + panel_w / 2) * S, (y + 58) * S), label,
               font=font("Alegreya-Italic.ttf", 13), fill=rgba(light(hc, 0.9)), anchor="mm")
        d.text(((sx0 + panel_w / 2) * S, (y + 74) * S), slot.title(),
               font=font("Cinzel.ttf", 10), fill=rgba(light(hc, 0.55)), anchor="mm")
        y += 100

    # title under portrait
    ty = 640
    if title:
        d.text((CW / 2 * S, ty * S), title, font=font("Alegreya-Italic.ttf", 22),
               fill=rgba(light(hc, 0.92)), anchor="mm")
        ty += 28

    # wand line
    if wand:
        wood = wand.get("wood", "Unknown")
        core = wand.get("core", "unknown core")
        wline = f"Wand — {wood}, {core}"
        if wand_sparks:
            wline += " ✨"
        d.text((CW / 2 * S, ty * S), wline, font=font("Cinzel.ttf", 14),
               fill=rgba(light(hc, 0.75)), anchor="mm")
        ty += 26

    # stats row
    stats = stats or []
    if stats:
        sw = (CW - 80) / max(1, len(stats))
        for i, (label, value) in enumerate(stats[:3]):
            cx = 40 + sw * i + sw / 2
            d.text((cx * S, (ty + 8) * S), str(value), font=font("Cinzel.ttf", 22, 700),
                   fill=rgba(light(hc, 0.95)), anchor="mm")
            d.text((cx * S, (ty + 30) * S), str(label), font=font("Alegreya-Italic.ttf", 13),
                   fill=rgba(light(hc, 0.65)), anchor="mm")
        ty += 56

    # Motto
    if motto:
        mf = _fit(d, f"“{motto}”", "Alegreya-Italic.ttf", 18, None, CW - 70)
        d.text((CW / 2 * S, (ty + 10) * S), f"“{motto}”", font=mf,
               fill=rgba(light(hc, 0.78)), anchor="mm")
        ty += 34

    # Earned titles listed under the motto (active title marked)
    title_list = [t for t in (titles or []) if t]
    if title_list:
        d.text((CW / 2 * S, (ty + 4) * S), "Titles",
               font=font("Cinzel.ttf", 13, 700), fill=rgba(light(hc, 0.6)), anchor="mm")
        ty += 22
        # Wrap into lines; put ★ before the equipped title
        parts = []
        for t in title_list:
            parts.append(f"★ {t}" if title and t == title else t)
        # Build wrapped lines by measuring
        f_title = font("Alegreya-Italic.ttf", 14)
        maxw = (CW - 70) * S
        lines, cur = [], ""
        for i, part in enumerate(parts):
            piece = part if not cur else f"{cur}  ·  {part}"
            if d.textlength(piece, font=f_title) <= maxw:
                cur = piece
            else:
                if cur:
                    lines.append(cur)
                cur = part
        if cur:
            lines.append(cur)
        # Cap how many lines we show so the card doesn't overflow forever
        max_lines = 5
        shown = lines[:max_lines]
        if len(lines) > max_lines:
            shown[-1] = shown[-1] + "  ·  …"
        for line in shown:
            d.text((CW / 2 * S, (ty + 2) * S), line, font=f_title,
                   fill=rgba(light(hc, 0.88)), anchor="mm")
            ty += 18
        ty += 8

    # stars
    star_y = min(CH - 36, max(ty + 8, CH - 48))
    for i in range(max(1, min(5, stars))):
        sx = CW / 2 + (i - (stars - 1) / 2) * 28
        pts = []
        for k in range(8):
            ang = math.pi / 4 * k - math.pi / 2
            rr = 10 if k % 2 == 0 else 3
            pts.append((sx * S + rr * S * 0.6 * math.cos(ang),
                        star_y * S + rr * S * 0.6 * math.sin(ang)))
        d.polygon(pts, fill=rgba((255, 230, 140) if gold_trim else light(hc, 0.85)))

    if beast_emoji:
        be = emoji_image(beast_emoji, 36)
        if be:
            paste_center(card, be, CW - 70, CH - 70)

    # downscale
    out = card.resize((CW, CH), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
