"""Player practice drills.

`/practice` is its own command so `/brew` and the staff summon stay as they are.
Discord shows the two drills as a subcommand dropdown.

    /practice potion   - the cauldron minigame, no ingredients, no points
    /practice monster  - a creature from the wild-threat pool, no points

Anyone can start either one in any server channel. Neither one writes the
points ledger, a brew tally, a spawn counter, or an event board.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class Practice(commands.Cog):
    group = app_commands.Group(
        name="practice",
        description="Practice a potion or a monster. No house points.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @group.command(
        name="potion",
        description="Practice a potion brew. No ingredients and no points.",
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
        description="Summon a practice monster here. No house points.",
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
