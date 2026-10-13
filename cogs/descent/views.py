"""Descent fight buttons and the stat-up picker."""

import discord

from cogs.descent.rules import (
    CAST_AP_COST,
    DEFEND_AP_COST,
    DESCENT_ONESHOT_USER_ID,
    ELEMENTS,
    ELEMENT_EMOJI,
    HEAL_AP_COST,
    IDLE_TIMEOUT,
    SPELL_NAME,
)

from cogs.descent.fight import Fight

class ElementButton(discord.ui.Button):
    def __init__(self, element: str, disabled: bool):
        super().__init__(label=SPELL_NAME[element], emoji=ELEMENT_EMOJI[element],
                          style=discord.ButtonStyle.secondary, disabled=disabled, row=0)
        self.element = element

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "cast", element=self.element)


class StrikeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Strike", emoji="🗡️", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "strike")


class HealButton(discord.ui.Button):
    def __init__(self, disabled: bool):
        super().__init__(label="Heal", emoji="💚", style=discord.ButtonStyle.success,
                          disabled=disabled, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "heal")


class DefendButton(discord.ui.Button):
    def __init__(self, disabled: bool):
        super().__init__(label="Defend", emoji="🛡️", style=discord.ButtonStyle.primary,
                          disabled=disabled, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "defend")


class RestButton(discord.ui.Button):
    def __init__(self, disabled: bool):
        super().__init__(label="Rest", emoji="😮‍💨", style=discord.ButtonStyle.danger,
                          disabled=disabled, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message("That's not your fight - use `/descend` to start your own.",
                                                     ephemeral=True)
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "rest")


class OneShotButton(discord.ui.Button):
    """Headmaster-only finisher — full rewards, no counterattack."""

    def __init__(self):
        super().__init__(
            label="One-shot",
            emoji="⚡",
            style=discord.ButtonStyle.danger,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message(
                "That's not your fight - use `/descend` to start your own.",
                ephemeral=True,
            )
            return
        if (
            DESCENT_ONESHOT_USER_ID is None
            or interaction.user.id != DESCENT_ONESHOT_USER_ID
        ):
            await interaction.response.send_message(
                "That button isn't for you.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await self.view.cog.take_action(interaction, "oneshot")


class OneShotView(discord.ui.View):
    """Ephemeral panel — only the sealed user receives this message."""

    def __init__(self, cog: "Descent", fight: Fight):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = fight.user_id
        self.add_item(OneShotButton())


class StopAutoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Stop Auto",
            emoji="⏹️",
            style=discord.ButtonStyle.danger,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.owner_id:
            await interaction.response.send_message(
                "That's not your fight - use `/descend` to start your own.",
                ephemeral=True,
            )
            return
        cog: Descent = self.view.cog
        cog.stop_auto(interaction.user.id)
        fight = cog.fights.get(interaction.user.id)
        if fight is not None:
            fight.auto = False
            await interaction.response.edit_message(
                embed=fight.embed(interaction.user),
                view=FightView(cog, fight),
            )
            await interaction.followup.send(
                "⏹️ Auto-play stopped. Finish this fight, then `/descend` for the next.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "⏹️ Auto-play stopped.",
            ephemeral=True,
        )


class FightView(discord.ui.View):
    def __init__(self, cog: "Descent", fight: Fight):
        # Slightly longer than IDLE_TIMEOUT so our asyncio timer fires first.
        super().__init__(timeout=IDLE_TIMEOUT + 30)
        self.cog = cog
        self.owner_id = fight.user_id
        for el in ELEMENTS:
            self.add_item(ElementButton(el, disabled=fight.ap < CAST_AP_COST))
        self.add_item(StrikeButton())
        self.add_item(HealButton(disabled=fight.ap < HEAL_AP_COST))
        self.add_item(DefendButton(disabled=fight.ap < DEFEND_AP_COST))
        self.add_item(RestButton(disabled=fight.ap >= fight.ap_max))
        if fight.auto:
            self.add_item(StopAutoButton())


class StatButton(discord.ui.Button):
    def __init__(self, stat: str, label: str, emoji: str):
        # Stable custom_ids so the picker survives bot restarts (registered
        # once in Descent.cog_load). Spending always applies to the clicker.
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.primary,
                         custom_id=f"descent:statup:{stat}")
        self.stat = stat

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.pick_stat(interaction, self.stat)


class StatUpView(discord.ui.View):
    """Persistent view — unspent Descent picks must still be clickable after
    a redeploy, otherwise players grind practice forever with a stuck
    pending_statup and never roll another +1."""

    def __init__(self, cog: "Descent"):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(StatButton("hp", "Max HP", "❤️"))
        self.add_item(StatButton("atk", "Attack", "⚔️"))
        self.add_item(StatButton("def", "Defense", "🛡️"))
