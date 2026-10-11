#!/usr/bin/env python3
"""Copy patronus_* images from artifacts into patronus_art_assets/ as RGB PNGs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = Path("/opt/cursor/artifacts/assets")
DST = ROOT / "patronus_art_assets"


def animal_slugs() -> dict[str, str]:
    text = (ROOT / "cogs" / "patronus.py").read_text()
    start = text.index("ANIMALS = {")
    end = text.index("\n# Patronuses that share")
    animals = re.findall(r'^\s+"([^"]+)":', text[start:end], re.M)
    return {a.replace(" ", "_"): a for a in animals}


def main() -> None:
    slugs = animal_slugs()
    by_lower = {k.lower(): k for k in slugs}
    installed = 0
    for p in sorted(SRC.glob("patronus_*")):
        rest = p.stem[len("patronus_") :]
        if rest.endswith("_test"):
            rest = rest[: -len("_test")]
        key = by_lower.get(rest.lower())
        if not key:
            print("unmapped", p.name)
            continue
        out = DST / f"{key}.png"
        Image.open(p).convert("RGB").save(out, "PNG", optimize=True)
        installed += 1
        print(out.name, out.stat().st_size)

    need = []
    for slug, animal in sorted(slugs.items()):
        path = DST / f"{slug}.png"
        if not path.is_file() or path.stat().st_size < 400_000:
            need.append(animal)
    (Path("/tmp/patronus_still_need.txt")).write_text("\n".join(need) + "\n")
    print(f"installed={installed} still_need_ai={len(need)}")


if __name__ == "__main__":
    main()
