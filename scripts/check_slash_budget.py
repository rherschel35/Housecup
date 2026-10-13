#!/usr/bin/env python3
"""Fail if top-level slash commands exceed Discord's global cap (100).

Usage (from repo root):
  python scripts/check_slash_budget.py
  python scripts/check_slash_budget.py --warn-at 90
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Match bot.py defaults: board games on unless explicitly disabled.
# Keep cog imports off the repo data/ tree.
os.environ.setdefault("ENABLE_BOARD_GAMES", "1")
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-slash-"))

DISCORD_CAP = 100


def _initial_cogs() -> tuple[str, ...]:
    # Keep in sync with bot.py INITIAL_COGS / ENABLE_BOARD_GAMES.
    from bot import INITIAL_COGS

    return tuple(INITIAL_COGS)


async def _cancel_background() -> None:
    pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def count_top_level() -> tuple[int, list[str], list[tuple[str, str]]]:
    import discord
    from discord.ext import commands

    cogs = _initial_cogs()
    # bot.py configures INFO logging on import. Quiet it for this check.
    logging.getLogger().setLevel(logging.ERROR)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    failed: list[tuple[str, str]] = []
    for cog in cogs:
        try:
            await bot.load_extension(cog)
        except Exception as exc:  # noqa: BLE001 — report all load failures
            failed.append((cog, f"{type(exc).__name__}: {exc}"))
    from cogs.staff_groups import nest_pure_staff_groups

    nest_pure_staff_groups(bot)
    names = sorted(c.name for c in bot.tree.get_commands())
    await _cancel_background()
    return len(names), names, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--warn-at",
        type=int,
        default=90,
        help="Print a warning when top-level count is at or above this (default 90).",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=DISCORD_CAP,
        help=f"Hard fail at this count (default {DISCORD_CAP}).",
    )
    args = parser.parse_args()

    count, names, failed = asyncio.run(count_top_level())
    print(f"Top-level slash commands: {count}/{args.cap}")
    for name in names:
        print(f"  /{name}")
    if failed:
        print("Cog load failures:")
        for cog, err in failed:
            print(f"  - {cog}: {err}")
        return 1
    if count >= args.warn_at:
        print(
            f"WARNING: at or above soft budget ({args.warn_at}). "
            "Nest new staff tools under /staff."
        )
    if count > args.cap:
        print("ERROR: over Discord's global top-level slash command cap.")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
