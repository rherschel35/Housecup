#!/usr/bin/env python3
"""Resize/compress Discord portrait packs toward broom-sized (~512px) assets."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DIRS = [
    ROOT / "patronus_art_assets",
    ROOT / "wand_art_assets",
    ROOT / "familiar_art_assets",
    ROOT / "frog_art_assets",
    ROOT / "beast_art_assets",
    ROOT / "animagus_art_assets",
]
MAX_EDGE = 512
JPEG_QUALITY = 85


def fit(im: Image.Image, max_edge: int = MAX_EDGE) -> Image.Image:
    w, h = im.size
    if max(w, h) <= max_edge:
        return im
    scale = max_edge / float(max(w, h))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    return im.resize((nw, nh), Image.Resampling.LANCZOS)


def compress_one(path: Path) -> tuple[int, int]:
    before = path.stat().st_size
    with Image.open(path) as im:
        im.load()
        fmt = (im.format or path.suffix.lstrip(".")).upper()
        rgb = im.convert("RGB")
        rgb = fit(rgb)

    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        rgb.save(path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    else:
        # Palette PNG matches broom-scale sizes for photo-like AI art.
        pal = rgb.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
        pal.save(path, format="PNG", optimize=True)

    after = path.stat().st_size
    return before, after


def main() -> int:
    targets: list[Path] = []
    for d in DIRS:
        if not d.is_dir():
            continue
        targets.extend(
            p
            for p in sorted(d.rglob("*"))
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}
        )

    if not targets:
        print("No images found.", file=sys.stderr)
        return 1

    total_before = total_after = 0
    for i, path in enumerate(targets, 1):
        before, after = compress_one(path)
        total_before += before
        total_after += after
        if i % 50 == 0 or i == len(targets):
            print(
                f"[{i}/{len(targets)}] {path.relative_to(ROOT)} "
                f"{before // 1024}KB -> {after // 1024}KB"
            )

    saved = total_before - total_after
    print(
        f"Done: {len(targets)} files, "
        f"{total_before / 1024 / 1024:.1f}MB -> {total_after / 1024 / 1024:.1f}MB "
        f"(saved {saved / 1024 / 1024:.1f}MB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
