#!/usr/bin/env python3
"""Compile and import bot.py and every cog module. No Discord token required.

Usage (from repo root):
  python scripts/check_imports.py
"""

from __future__ import annotations

import compileall
import importlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Importing cogs reads STATE_DIR at import time. Keep that off the repo tree.
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-import-"))
os.environ.pop("DISCORD_TOKEN", None)


def _modules() -> list[str]:
    names = ["bot"]
    names.extend(
        f"cogs.{path.stem}" for path in sorted((ROOT / "cogs").glob("*.py"))
    )
    return names


def main() -> int:
    compiled = compileall.compile_file(str(ROOT / "bot.py"), quiet=1)
    compiled = compileall.compile_dir(str(ROOT / "cogs"), quiet=1) and compiled
    compiled = compileall.compile_dir(str(ROOT / "scripts"), quiet=1) and compiled
    if not compiled:
        print("compileall failed")
        return 1

    failed: list[tuple[str, str]] = []
    for name in _modules():
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — report every module
            failed.append((name, f"{type(exc).__name__}: {exc}"))

    total = len(_modules())
    print(f"Imported {total - len(failed)}/{total} modules (no Discord token)")
    if failed:
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
