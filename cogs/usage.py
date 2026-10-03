"""
Slash-command usage tracking.

Counts every successful app command via on_app_command_completion.
Staff reads the tally with /staff usage top|unused|reset.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.usage")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "command_usage.json"

# Don't thrash disk on every slash — flush at most this often.
SAVE_INTERVAL = 30.0


def _blank() -> dict:
    return {"counts": {}, "since": time.time(), "total": 0}


class Usage(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self._dirty = False
        self._last_save = 0.0

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            return _blank()
        except (OSError, json.JSONDecodeError):
            log.exception("Command usage state unreadable — starting fresh.")
            return _blank()
        state.setdefault("counts", {})
        state.setdefault("since", time.time())
        state.setdefault("total", 0)
        return state

    def save(self, force: bool = False) -> None:
        now = time.time()
        if not force and (not self._dirty or now - self._last_save < SAVE_INTERVAL):
            return
        try:
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, STATE_PATH)
            self._dirty = False
            self._last_save = now
        except OSError:
            log.exception("Could not write command usage to %s", STATE_PATH)

    async def cog_unload(self):
        self.save(force=True)

    # -------------------------------------------------------------- record

    def record(self, qualified_name: str) -> None:
        key = (qualified_name or "").strip()
        if not key:
            return
        counts = self.state["counts"]
        counts[key] = int(counts.get(key, 0)) + 1
        self.state["total"] = int(self.state.get("total", 0)) + 1
        self._dirty = True
        self.save()

    @commands.Cog.listener()
    async def on_app_command_completion(
        self, interaction: discord.Interaction, command: app_commands.Command
    ):
        try:
            self.record(command.qualified_name)
        except Exception:
            log.exception("Failed to record usage for %s", getattr(command, "qualified_name", "?"))

    # --------------------------------------------------------------- query

    def _registered_paths(self) -> set[str]:
        """Every leaf command path currently on the tree."""
        out: set[str] = set()

        def walk(cmds, prefix: str = ""):
            for c in cmds:
                q = f"{prefix} {c.name}".strip() if prefix else c.name
                kids = list(getattr(c, "commands", None) or [])
                if kids:
                    walk(kids, q)
                else:
                    out.add(q)

        walk(self.bot.tree.get_commands())
        return out

    def top_rows(self, limit: int = 25) -> list[tuple[str, int]]:
        items = sorted(
            ((k, int(v)) for k, v in self.state["counts"].items()),
            key=lambda kv: (-kv[1], kv[0]),
        )
        return items[: max(1, min(limit, 50))]

    def unused_paths(self) -> list[str]:
        used = set(self.state["counts"])
        return sorted(p for p in self._registered_paths() if p not in used)

    def reset(self) -> None:
        self.state = _blank()
        self._dirty = True
        self.save(force=True)

    # ------------------------------------------------------------- staff UI

    def _guard(self, interaction: discord.Interaction) -> bool:
        store = self.bot.get_cog("Store")
        return bool(store and store.is_staff(interaction.user))

    async def show_top(self, interaction: discord.Interaction, limit: int = 25):
        if not self._guard(interaction):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        self.save(force=True)
        rows = self.top_rows(limit)
        since = self.state.get("since") or time.time()
        total = int(self.state.get("total", 0))
        if not rows:
            body = "*No slash commands recorded yet — usage starts counting after deploy.*"
        else:
            width = len(str(rows[0][1]))
            body = "\n".join(f"`{n:>{width}}`  `/{path}`" for path, n in rows)
        embed = discord.Embed(
            title="Slash command usage — top",
            description=body,
            color=0x4B3F99,
        )
        embed.set_footer(
            text=f"{total} uses since <t:{int(since)}:d> · /staff usage unused for never-used"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def show_unused(self, interaction: discord.Interaction):
        if not self._guard(interaction):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        unused = self.unused_paths()
        if not unused:
            body = "*Every registered command has been used at least once.*"
        else:
            # Cap the embed; Discord description max is 4096.
            lines = [f"`/{p}`" for p in unused]
            body = "\n".join(lines)
            if len(body) > 3900:
                # Keep as many full lines as fit.
                kept = []
                size = 0
                for line in lines:
                    if size + len(line) + 1 > 3800:
                        break
                    kept.append(line)
                    size += len(line) + 1
                rest = len(lines) - len(kept)
                body = "\n".join(kept) + f"\n…and **{rest}** more"
        embed = discord.Embed(
            title=f"Slash commands never used ({len(unused)})",
            description=body,
            color=0x4B3F99,
        )
        since = self.state.get("since") or time.time()
        embed.set_footer(text=f"Since <t:{int(since)}:d> · only counts successful runs")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def do_reset(self, interaction: discord.Interaction):
        if not self._guard(interaction):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        self.reset()
        await interaction.response.send_message(
            "Usage counters cleared. Counting starts fresh from now.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Usage(bot))
    log.info("Command usage tracking loaded")
