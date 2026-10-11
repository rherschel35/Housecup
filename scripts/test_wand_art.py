#!/usr/bin/env python3
"""Smoke test: every wood×core has a portrait; image_name is unique."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    from cogs.wands import ASSETS_DIR, CORES, WOODS, image_name

    names = [image_name(w, c) for w in WOODS for c in CORES]
    assert len(names) == 160
    assert len(set(names)) == 160
    missing = [
        image_name(w, c)
        for w in WOODS
        for c in CORES
        if not (ASSETS_DIR / image_name(w, c)).is_file()
    ]
    assert not missing, missing[:10]
    print("wand art OK", len(names), "portraits")


if __name__ == "__main__":
    main()
