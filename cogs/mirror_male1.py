"""
Compose portraits from the MALE1 muscular sprite pack.

Layout (flat folders under wizard_assets/male/):
  body, body_head, body_ears, hair_back, hair_top, beard,
  eyes, pupils, eyebrows, nose, mouth, clothes_top, clothes_bottom, …

Look-field mapping (reuse /wizard keys):
  skin, hair_back, hair_bangs(=hair_top), hair_color,
  eyes, iris_type(=nose), iris_color(=pupils),
  brows, mouth, clothes(=top style), clothes_color, glasses(=beard)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

# Skin tones (file suffix hex, case-sensitive as on disk for the lightest).
SKIN_HEX = {
    "1": "f9E9E6",
    "2": "EFD0CC",
    "3": "E0AAA3",
    "4": "D2998C",
    "5": "AF7A6E",
    "6": "784D45",
    "7": "613C33",
    "8": "3C2823",
}
SKINS = {
    "1": "Fair",
    "2": "Light",
    "3": "Warm light",
    "4": "Medium",
    "5": "Tan",
    "6": "Brown",
    "7": "Deep brown",
    "8": "Deep",
}

HAIR_COLOR_HEX = {
    "1": "292934",
    "2": "533028",
    "3": "911714",
    "4": "969696",
    "5": "b86e40",
    "6": "db592e",
    "7": "ffd99d",
    "8": "ffffff",
}
HAIR_COLORS = {
    "1": "Near black",
    "2": "Dark brown",
    "3": "Auburn",
    "4": "Grey",
    "5": "Light brown",
    "6": "Ginger",
    "7": "Blond",
    "8": "White",
}

HAIR_BACK = {str(i): f"Hair back {i}" for i in range(1, 19)}
HAIR_TOP = {str(i): f"Hair {i}" for i in range(1, 20)}

EYES = {str(i): f"Eyes {i}" for i in range(1, 8)}
NOSES = {str(i): f"Nose {i}" for i in range(1, 11)}
BROWS = {str(i): f"Brows {i}" for i in range(1, 9)}
MOUTHS = {str(i): f"Mouth {i}" for i in range(1, 11)}

# pupil file stem → label (front.png has no suffix)
PUPIL_FILES = {
    "1": "front",
    "2": "front_2",
    "3": "front_3",
    "4": "front_4",
    "5": "front_5",
    "6": "front_6",
    "7": "front_7",
    "8": "front_8",
    "9": "front_9",
    "10": "front_10",
    "11": "front_11",
    "12": "front_12",
}
PUPILS = {
    "1": "Brown",
    "2": "Hazel",
    "3": "Amber",
    "4": "Green",
    "5": "Teal",
    "6": "Blue",
    "7": "Grey",
    "8": "Violet",
    "9": "Red",
    "10": "Gold",
    "11": "Silver",
    "12": "Dark",
}

CLOTHES = {
    "basic_shirt": "Basic shirt",
    "shirt": "Shirt",
    "short_shirt": "Short shirt",
    "open_shirt": "Open shirt",
    "rustic_shirt": "Rustic shirt",
    "sweatshirt": "Sweatshirt",
    "ample_sweater": "Ample sweater",
    "jacket": "Jacket",
    "long_jacket": "Long jacket",
    "costume_jacket": "Costume jacket",
    "leather_vest": "Leather vest",
    "lab_coat": "Lab coat",
    "lab_coat_short": "Short lab coat",
    "sleeveless": "Sleeveless",
    "tight_top": "Tight top",
    "small_top": "Small top",
    "open_top": "Open top",
    "long_medieval_top": "Medieval top",
}

# Shared palette across tops (hex suffixes).
CLOTH_COLOR_HEX = [
    "343434", "696969", "ffffff", "f7f4f0", "314d75", "82aab8", "bcd3e5",
    "566f40", "5a8677", "c1d6b6", "b33634", "9c3e52", "d18455", "ebb04f",
    "ede0ba", "6e4a42", "c0aba0", "eac8be", "6c5289", "c2add6", "ba79b2",
    "d7abbb", "bce5d9",
]
CLOTHES_COLORS = {str(i + 1): f"Colour {i + 1}" for i in range(len(CLOTH_COLOR_HEX))}
CLOTHES_COLORS.update({
    "1": "Charcoal",
    "2": "Grey",
    "3": "White",
    "4": "Ivory",
    "5": "Navy",
    "6": "Steel blue",
    "7": "Sky",
    "8": "Olive",
    "9": "Sea green",
    "10": "Sage",
    "11": "Crimson",
    "12": "Wine",
    "13": "Copper",
    "14": "Gold",
    "15": "Sand",
    "16": "Brown",
    "17": "Tan",
    "18": "Blush",
    "19": "Purple",
    "20": "Lilac",
    "21": "Pink",
    "22": "Rose",
    "23": "Mint",
})

BEARDS = {"none": "Clean shaven", **{str(i): f"Beard {i}" for i in range(1, 17)}}

# Bottoms auto-picked (not a wizard menu); colour index 1–23.
_BOTTOM_STYLE = "rugged_jeans"
_UNDERWEAR = "boxer"


def is_male1_root(root: Path) -> bool:
    """True only when the muscular pack actually has sprite PNGs.

    Empty hair_top/ + body/ directories used to count as ready, so compose
    silently returned a transparent canvas (house wash, no wizard).
    """
    body = root / "body"
    hair = root / "hair_top"
    if not body.is_dir() or not hair.is_dir():
        return False
    try:
        return any(body.glob("*.png")) and any(hair.glob("*.png"))
    except OSError:
        return False


def options(field: str) -> dict:
    return {
        "skin": SKINS,
        "hair_back": HAIR_BACK,
        "hair_bangs": HAIR_TOP,
        "hair_color": HAIR_COLORS,
        "eyes": EYES,
        "iris_type": NOSES,
        "iris_color": PUPILS,
        "brows": BROWS,
        "mouth": MOUTHS,
        "clothes": CLOTHES,
        "clothes_color": CLOTHES_COLORS,
        "glasses": BEARDS,
    }.get(field, {})


def field_label(field: str) -> str | None:
    """Override /wizard placeholders for male1-mapped fields."""
    return {
        "hair_back": "Hair back",
        "hair_bangs": "Hair front",
        "iris_type": "Nose",
        "iris_color": "Eye colour",
        "glasses": "Beard",
        "clothes": "Top",
    }.get(field)


def clamp_look(look: dict) -> dict:
    fallbacks = {
        "skin": "3",
        "hair_back": "5",
        "hair_bangs": "8",
        "hair_color": "2",
        "eyes": "3",
        "iris_type": "2",
        "iris_color": "4",
        "brows": "3",
        "mouth": "3",
        "clothes": "basic_shirt",
        "clothes_color": "1",
        "glasses": "none",
    }
    for field, fb in fallbacks.items():
        opts = options(field)
        if look.get(field) not in opts:
            look[field] = fb if fb in opts else next(iter(opts))
    return look


def _open(path: Path) -> Image.Image | None:
    if not path.exists():
        return None
    try:
        return Image.open(path).convert("RGBA")
    except OSError:
        return None


def _skin_hex(key: str) -> str:
    return SKIN_HEX.get(key, SKIN_HEX["3"])


def _hair_hex(key: str) -> str:
    return HAIR_COLOR_HEX.get(key, HAIR_COLOR_HEX["2"])


def _cloth_hex(key: str) -> str:
    try:
        idx = int(key) - 1
    except (TypeError, ValueError):
        idx = 0
    if idx < 0 or idx >= len(CLOTH_COLOR_HEX):
        idx = 0
    return CLOTH_COLOR_HEX[idx]


def compose(root: Path, look: dict, out_size: tuple[int, int]) -> Image.Image:
    """Full-body composite, then crop/scale to a bust matching the card window."""
    look = clamp_look(dict(look))
    skin = _skin_hex(look["skin"])
    hair = _hair_hex(look["hair_color"])
    cloth = _cloth_hex(look["clothes_color"])
    top = look["clothes"]
    hb = look["hair_back"]
    ht = look["hair_bangs"]
    eyes = look["eyes"]
    nose = look["iris_type"]
    pupil = PUPIL_FILES.get(look["iris_color"], "front")
    brows = look["brows"]
    mouth = look["mouth"]
    beard = look["glasses"]

    # Bottom colour files are numbered 1–23 (not hex).
    try:
        bottom_i = max(1, min(23, int(look["clothes_color"])))
    except (TypeError, ValueError):
        bottom_i = 5

    layers: list[Path | None] = [
        root / "hair_back" / f"{hb}_{hair}.png",
        root / "body" / f"1_{skin}.png",
        root / "body_head" / f"1_{skin}.png",
        root / "body_ears" / f"1_{skin}.png",
        root / "clothes_underwear" / f"{_UNDERWEAR}_{cloth}.png",
        root / "clothes_bottom" / f"{_BOTTOM_STYLE}_{bottom_i}.png",
        root / "clothes_top" / f"{top}_{cloth}.png",
        root / "eyes" / f"{eyes}_{skin}.png",
        root / "pupils" / f"{pupil}.png",
        root / "eyebrows" / f"{brows}.png",
        root / "nose" / f"{nose}.png",
        root / "mouth" / f"{mouth}.png",
    ]
    if beard != "none":
        layers.append(root / "beard" / f"{beard}_{hair}.png")
    layers.append(root / "hair_top" / f"{ht}_{hair}.png")

    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    for path in layers:
        if path is None:
            continue
        im = _open(path)
        if im is None:
            continue
        if im.size != (1024, 1024):
            im = im.resize((1024, 1024), Image.LANCZOS)
        canvas.alpha_composite(im)

    # Bust crop — head + upper torso for the Mirror card window.
    bust = canvas.crop((140, 10, 884, 700))
    out_w, out_h = out_size
    # Fit into target canvas, bottom-aligned so shoulders sit in frame.
    scale = min(out_w / bust.width, out_h / bust.height)
    nw, nh = max(1, round(bust.width * scale)), max(1, round(bust.height * scale))
    bust = bust.resize((nw, nh), Image.LANCZOS)
    out = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    out.alpha_composite(bust, ((out_w - nw) // 2, out_h - nh))
    return out
