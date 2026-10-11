#!/usr/bin/env python3
"""Install wand_* AI images from artifacts into wand_art_assets/."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = Path("/opt/cursor/artifacts/assets")
DST = ROOT / "wand_art_assets"

sys.path.insert(0, str(ROOT))
from cogs.wands import CORES, WOODS, image_name  # noqa: E402


def main() -> None:
    # Expected: wand_{Wood}_{CoreSlug}.jpg/png  (spaces -> underscores, apostrophes dropped)
    wood_map = {w.replace(" ", "_").lower(): w for w in WOODS}
    core_map = {
        c.replace(" ", "_").replace("'", "").lower(): c for c in CORES
    }
    # also allow hyphen cores as-is
    installed = 0
    for p in sorted(SRC.glob("wand_*")):
        stem = p.stem  # wand_Oak_Storm-feather
        if not stem.startswith("wand_"):
            continue
        rest = stem[5:]
        # split wood (first token matching a wood) from core
        matched = None
        for wslug, wood in sorted(wood_map.items(), key=lambda x: -len(x[0])):
            prefix = wslug + "_"
            if rest.lower().startswith(prefix):
                cslug = rest[len(prefix):]
                core = core_map.get(cslug.lower())
                if core:
                    matched = (wood, core)
                    break
        if not matched:
            print("unmapped", p.name)
            continue
        wood, core = matched
        out = DST / image_name(wood, core)
        Image.open(p).convert("RGB").save(out, "PNG", optimize=True)
        installed += 1
        print(out.name, out.stat().st_size)

    need = []
    for wood in WOODS:
        for core in CORES:
            path = DST / image_name(wood, core)
            if not path.is_file() or path.stat().st_size < 400_000:
                need.append(f"{wood}|{core}")
    Path("/tmp/wand_still_need.txt").write_text("\n".join(need) + "\n")
    print(f"installed={installed} still_need_ai={len(need)}")


if __name__ == "__main__":
    main()
