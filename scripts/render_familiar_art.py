#!/usr/bin/env python3
"""Procedural placeholders for familiar looks (AI art overwrites these)."""

from __future__ import annotations

import hashlib
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cogs.familiars import LOOKS  # noqa: E402

OUT = ROOT / "familiar_art_assets"
SIZE = 768

PALETTE = {
    "salamander": ((180, 70, 40), (60, 28, 22), (255, 160, 60)),
    "raven": ((40, 44, 58), (18, 20, 28), (160, 170, 200)),
    "fox": ((200, 110, 50), (70, 40, 28), (255, 210, 140)),
}


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def render(species: str, look_id: str, label: str) -> Image.Image:
    dig = hashlib.sha256(f"{species}:{look_id}".encode()).digest()
    accent, bg, light = PALETTE[species]
    img = Image.new("RGB", (SIZE, SIZE), bg)
    px = img.load()
    cx = SIZE * (0.45 + (dig[0] % 20) / 200)
    cy = SIZE * (0.48 + (dig[1] % 20) / 200)
    for y in range(SIZE):
        for x in range(SIZE):
            t = y / SIZE
            dx = (x - cx) / SIZE
            dy = (y - cy) / SIZE
            d = math.sqrt(dx * dx + dy * dy)
            mist = max(0.0, 1.0 - d * 1.6) ** 1.3
            rr = int(bg[0] * (1 - t * 0.3) + accent[0] * mist * 0.55)
            gg = int(bg[1] * (1 - t * 0.3) + accent[1] * mist * 0.55)
            bb = int(bg[2] * (1 - t * 0.2) + accent[2] * mist * 0.55)
            h = (x * 73856093 ^ y * 19349663 ^ dig[2]) & 0xFFFF
            if h < 35:
                rr = min(255, rr + 40)
                gg = min(255, gg + 40)
                bb = min(255, bb + 40)
            px[x, y] = (min(255, rr), min(255, gg), min(255, bb))

    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i, rad in enumerate((260, 200, 140, 90)):
        a = 20 + i * 16
        gd.ellipse(
            (cx - rad, cy - rad * 0.8, cx + rad, cy + rad * 0.8),
            fill=(*light, a),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(26))
    img = Image.alpha_composite(img.convert("RGBA"), glow)

    draw = ImageDraw.Draw(img)
    title = label
    font = _font(48 if len(title) < 14 else 36)
    bbox = draw.textbbox((0, 0), title, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx, ty = (SIZE - tw) // 2, int(SIZE * 0.70) - th // 2
    draw.text((tx, ty), title, font=font, fill=(*light, 230))
    sub = _font(22)
    tag = f"{species} familiar"
    sb = draw.textbbox((0, 0), tag, font=sub)
    sw = sb[2] - sb[0]
    draw.text(((SIZE - sw) // 2, ty + th + 8), tag, font=sub, fill=(220, 210, 200, 180))
    return img.convert("RGB")


def main() -> None:
    force = "--force" in sys.argv
    made = skipped = 0
    for species, looks in LOOKS.items():
        (OUT / species).mkdir(parents=True, exist_ok=True)
        for look_id, meta in looks.items():
            path = OUT / species / f"{look_id}.png"
            if path.exists() and not force and path.stat().st_size > 400_000:
                skipped += 1
                continue
            if path.exists() and not force and path.stat().st_size > 50_000:
                # keep existing procedural unless forcing
                skipped += 1
                continue
            render(species, look_id, meta["label"]).save(path, "PNG", optimize=True)
            made += 1
            print(path.relative_to(OUT))
    print(f"done made={made} skipped={skipped}")


if __name__ == "__main__":
    main()
