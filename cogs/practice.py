"""Player practice drills.

`/practice` is its own command so `/brew` and the staff summon stay as they are.
Discord shows the two drills as a subcommand dropdown.

    /practice potion   - the cauldron minigame, no ingredients, no points
    /practice monster  - a creature from the wild-threat pool, no points

Anyone can start either one in any server channel. Neither one writes the
points ledger, a brew tally, a spawn counter, an event board, a daily
challenge, or a quest. `extras={"practice": True}` marks both subcommands
so the usage listener (and anything else that records a finished slash
command) can skip them.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


# Qualified names the usage hook must not record. Kept next to the commands
# so a rename cannot silently start counting again.
PRACTICE_COMMANDS = frozenset({"practice potion", "practice monster"})


def tracks_progress(command) -> bool:
    """False for the two practice drills.

    `/practice potion` and `/practice monster` are not daily challenges,
    quests, or any other progress. Callers that record a finished slash
    command should skip them.
    """
    extras = getattr(command, "extras", None) or {}
    if extras.get("practice"):
        return False
    name = (getattr(command, "qualified_name", None) or "").strip()
    return name not in PRACTICE_COMMANDS


class Practice(commands.Cog):
    group = app_commands.Group(
        name="practice",
        description="Practice a potion or a monster. No points or other progress.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @group.command(
        name="potion",
        description="Practice a potion brew. No points, challenges, or quests.",
        extras={"practice": True},
    )
    async def potion(self, interaction: discord.Interaction):
        potions = self.bot.get_cog("Potions")
        if potions is None:
            await interaction.response.send_message(
                "Potions isn't available right now.",
                ephemeral=True,
            )
            return
        await potions.start_practice_brew(interaction)

    @group.command(
        name="monster",
        description="Summon a practice monster. No points, challenges, or quests.",
        extras={"practice": True},
    )
    async def monster(self, interaction: discord.Interaction):
        threats = self.bot.get_cog("Dementors")
        if threats is None:
            await interaction.response.send_message(
                "Wild Threats isn't available right now.",
                ephemeral=True,
            )
            return
        await threats.start_practice_summon(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(Practice(bot))
