#!/usr/bin/env python3
"""Render silver-mist fallback portraits for every pool patronus animal.

AI portraits (when present) win; this fills any missing
patronus_art_assets/<Animal>.png so every form has an image.
"""

from __future__ import annotations

import hashlib
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cogs.patronus import ANIMALS, image_name  # noqa: E402

OUT = ROOT / "patronus_art_assets"
SIZE = 768


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _digest(animal: str) -> bytes:
    return hashlib.sha256(f"patronus-art:{animal}".encode()).digest()


def render(animal: str) -> Image.Image:
    dig = _digest(animal)
    img = Image.new("RGB", (SIZE, SIZE), (8, 10, 16))
    px = img.load()

    # Deep night gradient + animal-tinted mist
    r0, g0, b0 = 6 + dig[0] % 10, 8 + dig[1] % 12, 18 + dig[2] % 20
    r1, g1, b1 = 20 + dig[3] % 18, 28 + dig[4] % 22, 48 + dig[5] % 30
    cx = SIZE * (0.42 + (dig[6] % 40) / 200)
    cy = SIZE * (0.48 + (dig[7] % 30) / 200)
    for y in range(SIZE):
        for x in range(SIZE):
            t = y / SIZE
            dx = (x - cx) / SIZE
            dy = (y - cy) / SIZE
            d = math.sqrt(dx * dx + dy * dy)
            mist = max(0.0, 1.0 - d * 1.55)
            mist = mist ** 1.35
            rr = int(r0 * (1 - t) + r1 * t + mist * (140 + dig[8] % 40))
            gg = int(g0 * (1 - t) + g1 * t + mist * (155 + dig[9] % 35))
            bb = int(b0 * (1 - t) + b1 * t + mist * (175 + dig[10] % 40))
            # faint star speckles
            h = (x * 73856093 ^ y * 19349663 ^ dig[11]) & 0xFFFF
            if h < 40:
                boost = 80 + (h % 100)
                rr = min(255, rr + boost)
                gg = min(255, gg + boost)
                bb = min(255, bb + boost)
            px[x, y] = (min(255, rr), min(255, gg), min(255, bb))

    # Soft luminous orb / silhouette stand-in
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i, rad in enumerate((280, 220, 160, 110, 70)):
        alpha = 18 + i * 14
        color = (210, 225, 240, alpha)
        gd.ellipse(
            (cx - rad, cy - rad * 0.85, cx + rad, cy + rad * 0.85),
            fill=color,
        )
    glow = glow.filter(ImageFilter.GaussianBlur(28))
    img = Image.alpha_composite(img.convert("RGBA"), glow)

    # Animal name as silver sigil (readable identity when no AI art)
    draw = ImageDraw.Draw(img)
    label = animal
    font = _font(54 if len(label) < 14 else 42 if len(label) < 20 else 34)
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx, ty = (SIZE - tw) // 2, int(SIZE * 0.72) - th // 2
    # soft glow behind text
    for ox, oy in ((0, 0), (2, 0), (-2, 0), (0, 2), (0, -2)):
        draw.text((tx + ox, ty + oy), label, font=font, fill=(180, 200, 220, 90))
    draw.text((tx, ty), label, font=font, fill=(230, 238, 245, 230))

    sub = _font(22)
    tag = "silver patronus"
    sb = draw.textbbox((0, 0), tag, font=sub)
    sw = sb[2] - sb[0]
    draw.text(((SIZE - sw) // 2, ty + th + 10), tag, font=sub, fill=(160, 175, 195, 180))

    # vignette
    vig = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vig)
    vd.rectangle((0, 0, SIZE, SIZE), fill=(0, 0, 0, 0))
    for i in range(90):
        a = int(i * 1.8)
        vd.rectangle((i, i, SIZE - 1 - i, SIZE - 1 - i), outline=(0, 0, 0, a))
    vig = vig.filter(ImageFilter.GaussianBlur(6))
    img = Image.alpha_composite(img, vig)
    return img.convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    force = "--force" in sys.argv
    made = skipped = 0
    for animal in sorted(ANIMALS):
        path = OUT / image_name(animal)
        if path.exists() and not force:
            skipped += 1
            continue
        render(animal).save(path, "PNG", optimize=True)
        made += 1
        print(path.name)
    print(f"done: made={made} skipped={skipped} total={len(ANIMALS)}")


if __name__ == "__main__":
    main()
