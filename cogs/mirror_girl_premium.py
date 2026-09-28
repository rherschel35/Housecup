"""
Girl Sprites Premium — second feminine presentation (full-body pack).

Source: Premium Girl Sprites.psd (Combined layers), exported to PNG parts under
wizard_assets/female_full/. Portrait is bust-cropped for the Mirror card, with
no white paper background.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

PACK_FORMAT = "girl_premium_v2"  # must match scripts/export_girl_premium.py
CANVAS = (1821, 2579)

# Bust window: head + torso for the Mirror card (same idea as MALE1).
BUST_TOP = 0.0
BUST_BOTTOM = 0.55

# ---------------------------------------------------------------- menus
# Keys are stored on the player look; labels show in /wizard.

SKINS = {
    "body": "Standard",
    "body_soft": "Softer",
}

HAIR_BACK = {
    "long_let_down": "Long let down",
    "ponytail_short": "Short ponytail",
    "short_twintails": "Short twintails",
    "long_pony": "Long ponytail",
    "short": "Short",
    "tied_back": "Tied back",
    "tied_up": "Tied up",
    "twintails": "Twintails",
    "dango": "Dango",
    "dango_covered": "Dango (covered)",
    "braids": "Braids",
    "none": "None",
}

HAIR_FRONT = {
    "hair_1": "Front 1",
    "hair_2": "Front 2",
    "bangs": "Straight bangs",
    "alt": "Alternative",
    "hime": "Hime",
    "short": "Short cut",
    "straight": "Straight",
    "shoulder": "Shoulder length",
    "eye_covered": "Eye covered",
    "none": "None",
}

CLOTHES = {
    "uniform2_brown": "School uniform (brown)",
    "uniform2_black": "School uniform (black)",
    "hoodie_long": "Long hoodie",
    "hoodie_short": "Short hoodie",
    "one_piece": "One-piece dress",
    "jeans": "Jeans",
    "shorts": "Shorts",
    "pe": "PE clothes",
}

SHOES = {
    "loafers": "Loafers",
    "sport": "Sport shoes",
    "sandals": "Sandals",
    "leggings": "Leggings",
    "long_socks": "Long socks",
    "tennis_socks": "Tennis socks",
    "none": "None",
}

MOUTHS = {
    "smile": "Smile",
    "flat": "Flat",
    "open": "Open",
    "grin": "Grin",
    "open_2": "Open 2",
    "open_big": "Open big",
    "sad": "Sad",
}

LASHES = {
    "normal": "Normal",
    "sharper": "Sharper",
    "sharp_2": "Sharp 2",
}

BROWS = {
    "thin_flat": "Flat",
    "thin_high": "High",
    "thin_surprised": "Surprised",
    "thin_sad": "Sad",
    "thin_antagonistic": "Antagonistic",
    "thin_angry": "Angry",
}

GLASSES = {
    "none": "None",
    "glasses_1": "Glasses 1",
    "glasses_2": "Glasses 2",
}

# Extra toggles packed into unused look slots where possible:
# iris_type → eye style, iris_color unused (fixed), clothes_color → shoes,
# hair_color unused for now (pack uses gradient maps in PSD).

EYES = {
    "standard": "Standard",
    "no_reflection": "No reflection",
}

EXTRAS = {
    "none": "None",
    "star": "Star clip",
    "choker": "Choker",
    "gloves": "Gloves",
    "heart": "Heart mark",
    "beauty_mark": "Beauty mark",
    "beauty_mark_2": "Beauty mark 2",
    "cat_ears": "Cat ears",
}


def is_girl_premium_root(root: Path) -> bool:
    """True only for the current pack format — older exports mis-placed layers."""
    marker = root / ".pack_format"
    try:
        if not marker.is_file():
            return False
        if marker.read_text(encoding="utf-8").strip() != PACK_FORMAT:
            return False
    except OSError:
        return False
    return (root / "body" / "body.png").is_file() and (root / "clothes").is_dir()


def options(field: str) -> dict:
    return {
        "skin": SKINS,
        "hair_back": HAIR_BACK,
        "hair_bangs": HAIR_FRONT,
        "hair_color": {"1": "Default"},  # pack ships baked colours
        "eyes": EYES,
        "iris_type": LASHES,  # lashes
        "iris_color": EXTRAS,  # clips / marks / cat ears
        "brows": BROWS,
        "mouth": MOUTHS,
        "clothes": CLOTHES,
        "clothes_color": SHOES,  # shoes / lower accent
        "glasses": GLASSES,
    }.get(field, {})


def field_label(field: str) -> str | None:
    return {
        "hair_back": "Hair back",
        "hair_bangs": "Hair front",
        "hair_color": "Hair colour",
        "clothes": "Outfit",
        "clothes_color": "Shoes / legs",
        "glasses": "Glasses",
        "eyes": "Eyes",
        "iris_type": "Lashes",
        "iris_color": "Extra",
    }.get(field)


def clamp_look(look: dict) -> dict:
    fallbacks = {
        "skin": "body",
        "hair_back": "long_let_down",
        "hair_bangs": "hair_1",
        "hair_color": "1",
        "eyes": "standard",
        "iris_type": "normal",
        "iris_color": "none",
        "brows": "thin_flat",
        "mouth": "smile",
        "clothes": "uniform2_brown",
        "clothes_color": "loafers",
        "glasses": "none",
    }
    for field, fallback in fallbacks.items():
        opts = options(field)
        if not opts:
            continue
        if look.get(field) not in opts:
            look[field] = fallback if fallback in opts else next(iter(opts))
    return look


def _open(path: Path) -> Image.Image | None:
    if not path.is_file():
        return None
    try:
        return Image.open(path).convert("RGBA")
    except OSError:
        return None


def _paste(canvas: Image.Image, path: Path) -> None:
    im = _open(path)
    if im is None:
        return
    if im.size != canvas.size:
        # Parts are exported full-canvas already; resize only if mismatched.
        im = im.resize(canvas.size, Image.Resampling.LANCZOS)
    canvas.alpha_composite(im)


def compose(root: Path, look: dict, target_size: tuple[int, int]) -> Image.Image:
    """Stack exported parts, bust-crop, fit into target_size (SRC_W×SRC_H)."""
    look = clamp_look(dict(look))
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))

    # 1. hair back
    hb = look["hair_back"]
    if hb != "none":
        _paste(canvas, root / "hair_back" / f"{hb}.png")

    # 2. body + blush + nose
    skin = look["skin"]
    _paste(canvas, root / "body" / f"{skin}.png")
    _paste(canvas, root / "body" / "blush.png")
    _paste(canvas, root / "body" / "nose_highlight.png")

    # 3. mid accessories (under clothes / over body)
    extra = look.get("iris_color", "none")
    if extra in ("choker", "gloves", "heart", "beauty_mark", "beauty_mark_2"):
        _paste(canvas, root / "accessories" / f"{extra}.png")

    # 4. clothes + shoes
    _paste(canvas, root / "clothes" / f"{look['clothes']}.png")
    shoes = look["clothes_color"]
    if shoes != "none":
        _paste(canvas, root / "shoes" / f"{shoes}.png")

    # 5. face
    _paste(canvas, root / "eyes" / "whites.png")
    _paste(canvas, root / "eyes" / f"{look['eyes']}.png")
    _paste(canvas, root / "lashes" / f"{look['iris_type']}.png")
    _paste(canvas, root / "mouth" / f"{look['mouth']}.png")
    _paste(canvas, root / "brows" / f"{look['brows']}.png")

    # 6. hair front (+ cat ears / star as extras)
    hf = look["hair_bangs"]
    if hf != "none":
        _paste(canvas, root / "hair_front" / f"{hf}.png")
    if extra == "cat_ears":
        _paste(canvas, root / "accessories" / "cat_ears.png")
    if extra == "star":
        _paste(canvas, root / "accessories" / "star.png")

    # 7. glasses
    if look["glasses"] != "none":
        _paste(canvas, root / "accessories" / f"{look['glasses']}.png")

    # Bust crop
    w, h = canvas.size
    top = int(h * BUST_TOP)
    bottom = int(h * BUST_BOTTOM)
    bust = canvas.crop((0, top, w, bottom))
    box = bust.getbbox()
    if box:
        bust = bust.crop(box)

    # Fit into target (bottom-aligned, centered) like MALE1
    tw, th = target_size
    scale = min(tw / bust.width, th / bust.height)
    nw, nh = max(1, int(bust.width * scale)), max(1, int(bust.height * scale))
    bust = bust.resize((nw, nh), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    out.alpha_composite(bust, ((tw - nw) // 2, th - nh))
    return out
