#!/usr/bin/env python3
"""Install familiar_* AI images from artifacts into familiar_art_assets/."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = Path("/opt/cursor/artifacts/assets")
DST = ROOT / "familiar_art_assets"

sys.path.insert(0, str(ROOT))
from cogs.familiars import LOOKS  # noqa: E402


def main() -> None:
    installed = 0
    # Expected: familiar_{species}_{look_id}.jpg/png
    for p in sorted(SRC.glob("familiar_*")):
        m = re.match(r"familiar_(salamander|raven|fox)_(.+)\.(png|jpg|jpeg|webp)$", p.name, re.I)
        if not m:
            print("unmapped", p.name)
            continue
        species = m.group(1).lower()
        look = m.group(2)
        if look not in LOOKS.get(species, {}):
            # try lowercase match
            by_lower = {k.lower(): k for k in LOOKS[species]}
            look = by_lower.get(look.lower())
            if not look:
                print("unknown look", p.name)
                continue
        out = DST / species / f"{look}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.open(p).convert("RGB").save(out, "PNG", optimize=True)
        installed += 1
        print(out.relative_to(DST), out.stat().st_size)

    need = []
    for species, looks in LOOKS.items():
        for look_id in looks:
            path = DST / species / f"{look_id}.png"
            if not path.is_file() or path.stat().st_size < 400_000:
                need.append(f"{species}/{look_id}")
    Path("/tmp/familiar_still_need.txt").write_text("\n".join(need) + "\n")
    print(f"installed={installed} still_need_ai={len(need)}")


if __name__ == "__main__":
    main()
