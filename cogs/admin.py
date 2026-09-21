"""
Setup and season management. Everything here is staff-only.

    /pointsconfig              - what the bot currently thinks is true
    /setstaffrole <role>       - who may award points besides admins
    /sethouserole <house> <role>
    /setannounce <channel> [day] [hour]
    /sort <member> <house>     - pin someone to a house, ignoring roles
    /unsort <member>           - back to reading their roles
    /season rename|end|list
"""

import datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands

from cogs.store import HOUSES, house_display

log = logging.getLogger("velmora.admin")

HOUSE_CHOICES = [
    app_commands.Choice(name=f"House {meta['name']}", value=key)
    for key, meta in HOUSES.items()
]

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday")

WEEKDAY_CHOICES = [
    app_commands.Choice(name=day, value=i) for i, day in enumerate(WEEKDAYS)
]


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _store(self):
        return self.bot.get_cog("Store")

    async def _guard(self, interaction: discord.Interaction):
        """Returns the store if the caller is staff, otherwise None (and
        has already replied)."""
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return None
        if not store.is_staff(interaction.user):
            await interaction.response.send_message(
                "That one's for staff only.", ephemeral=True
            )
            return None
        return store

    @app_commands.command(name="pointsconfig", description="Show how house points are currently set up.")
    async def pointsconfig(self, interaction: discord.Interaction):
        store = await self._guard(interaction)
        if store is None:
            return

        s = store.settings
        staff = f"<@&{s['staff_role_id']}>" if s.get("staff_role_id") else "admins only"

        bound = s.get("house_roles", {})
        house_lines = []
        for key, meta in HOUSES.items():
            if key in bound:
                house_lines.append(f"{meta['emoji']} {meta['name']} — <@&{bound[key]}>")
            else:
                house_lines.append(f"{meta['emoji']} {meta['name']} — *matched by role name*")

        if s.get("announce_channel_id"):
            day = WEEKDAYS[s.get("announce_weekday", 6)]
            announce = f"<#{s['announce_channel_id']}> every {day} at {s.get('announce_hour', 18):02d}:00 UTC"
        else:
            announce = "off — set one with `/setannounce`"

        overrides = store.state.get("overrides", {})
        season = store.current_season()

        embed = discord.Embed(title="House points setup", color=discord.Color.blurple())
        embed.add_field(name="Who can award", value=f"Server admins + {staff}", inline=False)
        embed.add_field(name="House roles", value="\n".join(house_lines), inline=False)
        embed.add_field(name="Weekly standings", value=announce, inline=False)
        embed.add_field(name="Current season", value=f"{season['name']} (#{season['number']})")
        embed.add_field(name="Manual sorts", value=str(len(overrides)))
        embed.set_footer(text="Houses are read from member roles unless pinned with /sort.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="setstaffrole",
                          description="Let a role award points alongside admins.")
    @app_commands.describe(role="The role that may award and deduct points")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setstaffrole(self, interaction: discord.Interaction, role: discord.Role):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return
        store.set_setting("staff_role_id", role.id)
        await interaction.response.send_message(
            f"{role.mention} can now award and deduct house points.", ephemeral=True
        )

    @app_commands.command(name="sethouserole", description="Bind a house to a Discord role.")
    @app_commands.describe(house="Which house", role="The role its members hold")
    @app_commands.choices(house=HOUSE_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sethouserole(self, interaction: discord.Interaction,
                           house: app_commands.Choice[str], role: discord.Role):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return
        store.bind_house_role(house.value, role.id)
        await interaction.response.send_message(
            f"{house_display(house.value)} is now {role.mention}.", ephemeral=True
        )

    @app_commands.command(name="setannounce",
                          description="Post the standings automatically once a week.")
    @app_commands.describe(channel="Where to post", day="Which day (default Sunday)",
                           hour="Hour in UTC, 0-23 (default 18)")
    @app_commands.choices(day=WEEKDAY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setannounce(self, interaction: discord.Interaction,
                          channel: discord.TextChannel,
                          day: app_commands.Choice[int] = None,
                          hour: int = 18):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return
        if not 0 <= hour <= 23:
            await interaction.response.send_message("Hour has to be between 0 and 23.", ephemeral=True)
            return

        store.set_setting("announce_channel_id", channel.id)
        store.set_setting("announce_weekday", day.value if day else 6)
        store.set_setting("announce_hour", hour)
        store.set_setting("last_announced_week", None)

        weekday_name = WEEKDAYS[day.value if day else 6]
        await interaction.response.send_message(
            f"Standings will post in {channel.mention} every {weekday_name} at {hour:02d}:00 UTC.",
            ephemeral=True,
        )

    @app_commands.command(name="sort", description="Pin a member to a house, ignoring their roles.")
    @app_commands.describe(member="Who to sort", house="Which house")
    @app_commands.choices(house=HOUSE_CHOICES)
    async def sort(self, interaction: discord.Interaction, member: discord.Member,
                   house: app_commands.Choice[str]):
        store = await self._guard(interaction)
        if store is None:
            return
        store.set_override(member.id, house.value)
        await interaction.response.send_message(
            f"{member.display_name} is now counted under {house_display(house.value)}."
        )

    @app_commands.command(name="unsort", description="Stop pinning a member and read their roles again.")
    @app_commands.describe(member="Who to release")
    async def unsort(self, interaction: discord.Interaction, member: discord.Member):
        store = await self._guard(interaction)
        if store is None:
            return
        store.set_override(member.id, None)
        house = store.member_house(member)
        await interaction.response.send_message(
            f"{member.display_name} is back to their roles — "
            + (f"currently {house_display(house)}." if house else "currently no house."),
            ephemeral=True,
        )

    # -------------------------------------------------------------- seasons

    season = app_commands.Group(name="season", description="Manage House Cup seasons.")

    @season.command(name="rename", description="Rename the current season.")
    @app_commands.describe(name="What to call it, e.g. 'Autumn Term'")
    async def season_rename(self, interaction: discord.Interaction, name: str):
        store = await self._guard(interaction)
        if store is None:
            return
        store.rename_season(name)
        await interaction.response.send_message(
            f"This season is now **{store.current_season()['name']}**.", ephemeral=True
        )

    @season.command(name="end", description="Crown a champion and start a new season.")
    @app_commands.describe(confirm="This resets season scores. All-time totals are kept.")
    @app_commands.choices(confirm=[
        app_commands.Choice(name="Yes, end the season and crown a champion", value="yes"),
    ])
    async def season_end(self, interaction: discord.Interaction,
                         confirm: app_commands.Choice[str]):
        store = await self._guard(interaction)
        if store is None:
            return

        record = store.end_season()
        rows = record["standings"]

        if record.get("winner"):
            meta = HOUSES[record["winner"]]
            title = f"{meta['emoji']} House {meta['name']} wins {record['name']}"
            color = meta["color"]
            blurb = meta["motto"]
        elif record.get("tied"):
            title = f"{record['name']} ends in a tie"
            color = discord.Color.light_grey()
            blurb = " and ".join(HOUSES[k]["name"] for k in record["tied"]) + " finish level."
        else:
            title = f"{record['name']} ends with no points awarded"
            color = discord.Color.dark_grey()
            blurb = "Nothing was recorded this season."

        table = "\n".join(
            f"{HOUSES[k]['emoji']} **{HOUSES[k]['name']}** — `{p:,}`" for k, p in rows
        )
        embed = discord.Embed(title=title, description=f"*{blurb}*\n\n{table}", color=color)

        champions = record.get("champions", [])
        if champions:
            names = " & ".join(f"<@{c['id']}>" for c in champions)
            label = "House Cup Champion" if len(champions) == 1 else "House Cup Co-Champions"
            embed.add_field(
                name=f"\U0001F3C6 {label}",
                value=f"{names} \u2014 top earner for the winning house with "
                      f"**{champions[0]['points']:,}** points.",
                inline=False,
            )
        embed.set_footer(text=f"{store.current_season()['name']} begins now. All-time totals carry over.")
        await interaction.response.send_message(embed=embed)

    @season.command(name="list", description="Every season so far.")
    async def season_list(self, interaction: discord.Interaction):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return

        archive = store.state.get("archive", [])
        current = store.current_season()
        started = datetime.datetime.fromtimestamp(current["started_at"], datetime.timezone.utc)

        lines = []
        for record in archive:
            winner = (house_display(record["winner"]) if record.get("winner")
                      else ("tied" if record.get("tied") else "no champion"))
            lines.append(f"**{record['name']}** — {winner}")
        lines.append(f"**{current['name']}** — running since {started:%d %b %Y}")

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Seasons",
                description="\n".join(lines),
                color=discord.Color.dark_gold(),
            ),
            ephemeral=True,
        )

    # ---------------------------------------------------------- error guard

    async def cog_app_command_error(self, interaction: discord.Interaction,
                                    error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need Manage Server for that one."
        else:
            log.exception("Admin command failed", exc_info=error)
            message = "That didn't go through. Check the logs."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
