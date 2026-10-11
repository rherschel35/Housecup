#!/usr/bin/env python3
"""Fast procedural placeholders for every wood×core wand portrait."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cogs.wands import CORES, WOODS, image_name  # noqa: E402

OUT = ROOT / "wand_art_assets"
SIZE = 512

WOOD_COLORS = {
    "Ash": (180, 160, 130),
    "Blackthorn": (55, 50, 45),
    "Cedar": (140, 90, 55),
    "Cherry": (160, 70, 70),
    "Elm": (150, 120, 80),
    "Hawthorn": (170, 140, 100),
    "Hazel": (190, 150, 100),
    "Holly": (100, 120, 70),
    "Larch": (200, 160, 90),
    "Maple": (210, 130, 70),
    "Oak": (120, 85, 50),
    "Pine": (150, 130, 80),
    "Rowan": (175, 100, 70),
    "Walnut": (90, 60, 40),
    "Willow": (170, 175, 130),
    "Yew": (80, 95, 60),
}

CORE_GLOW = {
    "Moonstone thread": (180, 200, 255),
    "Wolf's whisker": (200, 200, 210),
    "Stag's velvet": (180, 220, 160),
    "Starglass shard": (160, 180, 255),
    "Old elephant's hair": (210, 190, 150),
    "Hearthcoal ember": (255, 120, 40),
    "Storm-feather": (140, 160, 220),
    "Riverpearl": (160, 220, 230),
    "Nightbloom root": (140, 100, 180),
    "Bell-bronze filing": (220, 180, 80),
}


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def render(wood: str, core: str) -> Image.Image:
    dig = hashlib.sha256(f"{wood}|{core}".encode()).digest()
    wood_c = WOOD_COLORS.get(wood, (140, 100, 70))
    glow = CORE_GLOW.get(core, (200, 180, 140))
    bg = (28 + dig[0] % 12, 22 + dig[1] % 10, 18 + dig[2] % 10)
    img = Image.new("RGB", (SIZE, SIZE), bg)
    d = ImageDraw.Draw(img)

    # soft vignette glow
    overlay = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    cx, cy = SIZE // 2, SIZE // 2 + 20
    for i, rad in enumerate((260, 180, 110)):
        od.ellipse(
            (cx - rad, cy - rad, cx + rad, cy + rad),
            fill=(*glow, 28 + i * 18),
        )
    overlay = overlay.filter(ImageFilter.GaussianBlur(28))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)

    # wand shaft
    shaft = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shaft)
    # diagonal wand
    x0, y0 = 90, SIZE - 100
    x1, y1 = SIZE - 80, 110
    for w in range(10, 0, -1):
        shade = tuple(min(255, c + (10 - w) * 4) for c in wood_c)
        sd.line([(x0, y0), (x1, y1)], fill=(*shade, 255), width=w)
    # tip glow (core)
    sd.ellipse((x1 - 18, y1 - 18, x1 + 18, y1 + 18), fill=(*glow, 200))
    shaft = shaft.filter(ImageFilter.GaussianBlur(0.6))
    tip = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    td = ImageDraw.Draw(tip)
    td.ellipse((x1 - 40, y1 - 40, x1 + 40, y1 + 40), fill=(*glow, 70))
    tip = tip.filter(ImageFilter.GaussianBlur(12))
    img = Image.alpha_composite(img, tip)
    img = Image.alpha_composite(img, shaft)

    draw = ImageDraw.Draw(img)
    title = wood
    f = _font(36)
    bb = draw.textbbox((0, 0), title, font=f)
    tw = bb[2] - bb[0]
    draw.text(((SIZE - tw) // 2, SIZE - 78), title, font=f, fill=(230, 215, 190))
    sf = _font(16)
    sub = core
    sb = draw.textbbox((0, 0), sub, font=sf)
    sw = sb[2] - sb[0]
    draw.text(((SIZE - sw) // 2, SIZE - 40), sub, font=sf, fill=(*glow, 220))
    return img.convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    force = "--force" in sys.argv
    made = skipped = 0
    for wood in WOODS:
        for core in CORES:
            path = OUT / image_name(wood, core)
            if path.exists() and not force and path.stat().st_size > 40_000:
                skipped += 1
                continue
            render(wood, core).save(path, "PNG", optimize=True)
            made += 1
    print(f"done made={made} skipped={skipped} total={len(WOODS) * len(CORES)}")


if __name__ == "__main__":
    main()
