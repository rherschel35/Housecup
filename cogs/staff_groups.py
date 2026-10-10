"""Shared staff slash-command trees.

Player-facing verbs stay short top-level names. Staff tools split across
several roots because Discord caps each command group at 8000 characters —
one giant `/staff` tree exceeded that once world, dementor, and reaction
roles nested under it.

Import `staff`, subgroups, and extra roots from here.

Pure-staff groups owned by other cogs load top-level first; then
`nest_pure_staff_groups` moves them under the right staff root after all
cogs load.
"""

from __future__ import annotations

import logging

from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.staff_groups")

staff = app_commands.Group(
    name="staff",
    description="Core staff: points, setup, houses, usage.",
)
staffworld = app_commands.Group(
    name="staffworld",
    description="Staff: world events and dementors.",
)
staffgame = app_commands.Group(
    name="staffgame",
    description="Staff: descent, castles, duels, hex, market.",
)
staffops = app_commands.Group(
    name="staffops",
    description="Staff: beasts, gear admin, reaction roles, antispam.",
)

points = app_commands.Group(
    name="points", parent=staff, description="Award and manage house points."
)
setup = app_commands.Group(
    name="setup", parent=staff, description="Bot and house configuration."
)
houses = app_commands.Group(
    name="houses", parent=staff, description="Pin members to houses."
)
identity = app_commands.Group(
    name="identity", parent=staff, description="Wand, broom, and Animagus staff tools."
)
descent = app_commands.Group(
    name="descent", parent=staffgame, description="Descent staff tools."
)
castles = app_commands.Group(
    name="castles", parent=staffgame, description="Castle / army PvP staff tools."
)
raid = app_commands.Group(
    name="raid", parent=staffgame, description="3Raid staff tools."
)
duels = app_commands.Group(
    name="duels", parent=staffgame, description="Duel staff tools."
)
challenge = app_commands.Group(
    name="challenge", parent=staffgame, description="Challenge staff tools."
)
hexes = app_commands.Group(
    name="hex", parent=staffgame, description="Headmaster / President hexes."
)
bingo = app_commands.Group(
    name="bingo", parent=staffgame, description="Wizard Bingo staff tools."
)
market = app_commands.Group(
    name="market", parent=staffgame, description="Marketplace staff tools."
)
usage = app_commands.Group(
    name="usage", parent=staff, description="Slash-command usage stats."
)

# Top-level names of pure-staff groups owned by other cogs → staff root to nest under.
NEST_UNDER_STAFFWORLD = ("world", "dementor")
NEST_UNDER_STAFFOPS = ("beastadmin", "adornadmin", "reactionroles", "antispam")


def _nest_under(bot: commands.Bot, root_name: str, group_names: tuple[str, ...]) -> list[str]:
    root = bot.tree.get_command(root_name)
    if root is None or not isinstance(root, app_commands.Group):
        log.warning("No /%s group on the tree — skipping nest.", root_name)
        return []

    nested: list[str] = []
    for name in group_names:
        cmd = bot.tree.get_command(name)
        if cmd is None:
            continue
        if not isinstance(cmd, app_commands.Group):
            log.warning("/%s is not a group — leaving it top-level.", name)
            continue
        bot.tree.remove_command(name)
        cmd.parent = root
        root.add_command(cmd)
        nested.append(name)
    return nested


def nest_pure_staff_groups(bot: commands.Bot) -> list[str]:
    """Move pure-staff top-level groups under the right staff root."""
    nested: list[str] = []
    nested.extend(_nest_under(bot, "staffworld", NEST_UNDER_STAFFWORLD))
    nested.extend(_nest_under(bot, "staffops", NEST_UNDER_STAFFOPS))
    if nested:
        log.info("Nested staff groups: %s", ", ".join(nested))
    return nested
