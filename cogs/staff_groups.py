"""Shared `/staff` slash-command tree.

Every staff-only command nests under this root so player-facing verbs
(`/wand`, `/duel`, `/broom`, …) can stay short top-level names without
blowing Discord's 100 top-level command cap.

Import `staff` (and subgroups) from here — always the same Group objects
for groups owned by the Staff cog.

Staff-only groups that live on *other* cogs (world, dementor, …) stay
top-level at class definition time so discord.py binds callbacks to the
right cog, then `nest_pure_staff_groups` moves them under `/staff` after
all cogs have loaded.
"""

from __future__ import annotations

import logging

from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.staff_groups")

staff = app_commands.Group(
    name="staff",
    description="Staff tools for running Velmora.",
)

points = app_commands.Group(
    name="points", parent=staff, description="Award and manage house points.",
)
setup = app_commands.Group(
    name="setup", parent=staff, description="Bot and house configuration.",
)
houses = app_commands.Group(
    name="houses", parent=staff, description="Pin members to houses.",
)
identity = app_commands.Group(
    name="identity", parent=staff, description="Wand, broom, and Animagus staff tools.",
)
descent = app_commands.Group(
    name="descent", parent=staff, description="Descent staff tools.",
)
castles = app_commands.Group(
    name="castles", parent=staff, description="Castle / army PvP staff tools.",
)
raid = app_commands.Group(
    name="raid", parent=staff, description="3Raid staff tools.",
)
duels = app_commands.Group(
    name="duels", parent=staff, description="Duel staff tools.",
)
challenge = app_commands.Group(
    name="challenge", parent=staff, description="Challenge staff tools.",
)
hexes = app_commands.Group(
    name="hex", parent=staff, description="Headmaster hexes.",
)
market = app_commands.Group(
    name="market", parent=staff, description="Marketplace staff tools.",
)
usage = app_commands.Group(
    name="usage", parent=staff, description="Slash-command usage stats.",
)

# Top-level names of pure-staff groups owned by other cogs. After load,
# these are moved under `/staff` (bindings stay on the owning cog).
PURE_STAFF_GROUPS = (
    "world",
    "dementor",
    "beastadmin",
    "adornadmin",
    "reactionroles",
    "antispam",
)


def nest_pure_staff_groups(bot: commands.Bot) -> list[str]:
    """Move pure-staff top-level groups under the tree's `/staff` root.

    Returns the names that were nested.
    """
    staff_root = bot.tree.get_command("staff")
    if staff_root is None or not isinstance(staff_root, app_commands.Group):
        log.warning("No /staff group on the tree — skipping staff nesting.")
        return []

    nested: list[str] = []
    for name in PURE_STAFF_GROUPS:
        cmd = bot.tree.get_command(name)
        if cmd is None:
            continue
        if not isinstance(cmd, app_commands.Group):
            log.warning("/%s is not a group — leaving it top-level.", name)
            continue
        bot.tree.remove_command(name)
        cmd.parent = staff_root
        staff_root.add_command(cmd)
        nested.append(name)
    if nested:
        log.info("Nested under /staff: %s", ", ".join(nested))
    return nested
