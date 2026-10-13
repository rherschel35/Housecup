#!/usr/bin/env python3
"""Fail if any top-level slash command exceeds Discord's 8000-character limit.

The size is the JSON Discord receives, same measurement as
bot._command_group_json_size:

  len(json.dumps(command.to_dict(tree), separators=(",", ":")))

Usage (from repo root):
  python scripts/check_command_payload.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ENABLE_BOARD_GAMES", "1")
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-payload-"))
os.environ.pop("DISCORD_TOKEN", None)


async def _cancel_background() -> None:
    pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def measure() -> tuple[list[tuple[int, str]], list[tuple[str, str]], int]:
    import discord
    from discord.ext import commands

    from bot import DISCORD_COMMAND_GROUP_MAX_CHARS, INITIAL_COGS

    logging.getLogger().setLevel(logging.ERROR)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    failed: list[tuple[str, str]] = []
    for cog in INITIAL_COGS:
        try:
            await bot.load_extension(cog)
        except Exception as exc:  # noqa: BLE001 — report all load failures
            failed.append((cog, f"{type(exc).__name__}: {exc}"))
    from cogs.staff_groups import nest_pure_staff_groups

    nest_pure_staff_groups(bot)
    rows: list[tuple[int, str]] = []
    for command in bot.tree.get_commands():
        payload = json.dumps(command.to_dict(bot.tree), separators=(",", ":"))
        rows.append((len(payload), command.name))
    rows.sort(reverse=True)
    await _cancel_background()
    return rows, failed, DISCORD_COMMAND_GROUP_MAX_CHARS


def main() -> int:
    rows, failed, limit = asyncio.run(measure())
    print(f"Top-level slash payload sizes (limit {limit}):")
    over: list[tuple[str, int]] = []
    for size, name in rows:
        flag = " OVER" if size > limit else ""
        print(f"  {size:5d}  /{name}{flag}")
        if size > limit:
            over.append((name, size))
    if failed:
        print("Cog load failures:")
        for cog, err in failed:
            print(f"  - {cog}: {err}")
        return 1
    if over:
        print("ERROR: command payload exceeds Discord's limit.")
        for name, size in over:
            print(f"  /{name} is {size} (max {limit})")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
