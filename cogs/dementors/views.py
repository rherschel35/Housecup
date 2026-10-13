"""Spell buttons for /cast."""

import discord

from cogs.dementors.catalog import (
    SPELL_LABEL,
    SPELL_ORDER,
)

class SpellPick:
    """Tiny stand-in for app_commands.Choice so button casts share resolve code."""

    __slots__ = ("name", "value")

    def __init__(self, value: str):
        self.value = value
        self.name = SPELL_LABEL.get(value, value.title())


class CastSpellButton(discord.ui.Button):
    def __init__(self, spell_value: str):
        label = SPELL_LABEL.get(spell_value, spell_value.title())
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=f"cast:{spell_value}")
        self.spell_value = spell_value

    async def callback(self, interaction: discord.Interaction):
        view: CastSpellView = self.view  # type: ignore[assignment]
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("Those aren't your spells.", ephemeral=True)
            return
        view.stop()
        for item in view.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=view)
        except discord.DiscordException:
            pass
        await view.cog.resolve_cast(interaction, SpellPick(self.spell_value))


class CastSpellView(discord.ui.View):
    def __init__(self, cog: "Dementors", owner_id: int):
        super().__init__(timeout=45)
        self.cog = cog
        self.owner_id = owner_id
        for value in SPELL_ORDER:
            self.add_item(CastSpellButton(value))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"
