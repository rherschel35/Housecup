"""
/gonsomethingbroke - tell Gon something's broken.

Players describe what's wrong. A ticket receipt is posted in the channel
where they ran the command, and a copy goes to the staff broke-report
channel with a button that jumps straight back to that ticket.
"""

from __future__ import annotations

import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.broke")

# Staff inbox for "something broke" tickets.
REPORT_CHANNEL_ID = int(
    os.getenv("BROKE_REPORT_CHANNEL_ID", "1555383179802579005") or 0
)

COOLDOWN_SECONDS = 120
MAX_REPORT_LEN = 1000
COLOR = 0xC0392B


class Broke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last: dict[int, float] = {}

    def _cooldown_left(self, user_id: int, now: float) -> int:
        last = self._last.get(user_id, 0)
        if not last or now >= last + COOLDOWN_SECONDS:
            return 0
        return max(1, int(last + COOLDOWN_SECONDS - now))

    @app_commands.command(
        name="gonsomethingbroke",
        description="Tell Gon something's broken — staff get a jump link to your ticket.",
    )
    @app_commands.describe(what="What's broken? Be as specific as you can.")
    async def gonsomethingbroke(self, interaction: discord.Interaction, what: str):
        what = (what or "").strip()
        if not what:
            await interaction.response.send_message(
                "Tell me what's broken — add a short description.", ephemeral=True
            )
            return
        if len(what) > MAX_REPORT_LEN:
            await interaction.response.send_message(
                f"Keep it under {MAX_REPORT_LEN} characters.", ephemeral=True
            )
            return

        now = time.time()
        left = self._cooldown_left(interaction.user.id, now)
        if left:
            await interaction.response.send_message(
                f"You just sent one — try again in {left}s.", ephemeral=True
            )
            return

        if interaction.channel is None or not isinstance(
            interaction.channel, (discord.TextChannel, discord.Thread)
        ):
            await interaction.response.send_message(
                "Run this in a text channel so staff can jump back to your ticket.",
                ephemeral=True,
            )
            return

        self._last[interaction.user.id] = now

        receipt = discord.Embed(
            title="🛠️ Something broke",
            description=what,
            color=COLOR,
            timestamp=discord.utils.utcnow(),
        )
        receipt.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )
        receipt.set_footer(text="Gon has been told. Staff can jump here from the report channel.")

        await interaction.response.send_message(embed=receipt)
        try:
            ticket_msg = await interaction.original_response()
        except discord.HTTPException:
            log.exception("Couldn't fetch broke-ticket receipt message")
            return

        report_channel = self.bot.get_channel(REPORT_CHANNEL_ID)
        if report_channel is None:
            try:
                report_channel = await self.bot.fetch_channel(REPORT_CHANNEL_ID)
            except discord.HTTPException:
                log.exception("Broke report channel %s not found", REPORT_CHANNEL_ID)
                await interaction.followup.send(
                    "Your ticket is posted here, but the staff inbox couldn't be reached. "
                    "Ping Gon if it's urgent.",
                    ephemeral=True,
                )
                return

        staff_embed = discord.Embed(
            title="🛠️ Broke report",
            description=what,
            color=COLOR,
            timestamp=discord.utils.utcnow(),
        )
        staff_embed.set_author(
            name=f"{interaction.user} ({interaction.user.id})",
            icon_url=interaction.user.display_avatar.url,
        )
        staff_embed.add_field(
            name="Submitted in",
            value=f"{interaction.channel.mention}\n[Jump to ticket]({ticket_msg.jump_url})",
            inline=False,
        )
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Go to ticket",
                style=discord.ButtonStyle.link,
                url=ticket_msg.jump_url,
            )
        )
        try:
            await report_channel.send(embed=staff_embed, view=view)
        except discord.HTTPException:
            log.exception("Couldn't post broke report to %s", REPORT_CHANNEL_ID)
            await interaction.followup.send(
                "Your ticket is posted here, but the staff inbox couldn't be reached. "
                "Ping Gon if it's urgent.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Broke(bot))
