"""
/staff — every staff tool under one root.

Player-facing verbs stay short top-level names (`/wand`, `/duel`, `/broom`).
Staff work lives here so we stay under Discord's 100 top-level command cap.

Thin wrappers call into the owning cogs; auth stays in those methods.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cogs import staff_groups as sg
from cogs.admin import HOUSE_CHOICES, WEEKDAY_CHOICES
from cogs.hexes import EFFECTS
from cogs.store import HOUSES

log = logging.getLogger("velmora.staff")

HEX_CHOICES = [
    app_commands.Choice(name=v["name"], value=k) for k, v in EFFECTS.items()
]


class Staff(commands.Cog):
    """Owns the `/staff` tree (subgroups are shared Group objects)."""

    staff = sg.staff
    points = sg.points
    staff_setup = sg.setup
    houses = sg.houses
    identity = sg.identity
    descent = sg.descent
    raid = sg.raid
    duels = sg.duels
    challenge = sg.challenge
    hex_g = sg.hexes
    market = sg.market

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _cog(self, name: str):
        return self.bot.get_cog(name)

    # -------------------------------------------------------------- points

    @points.command(name="award", description="Award house points to a member.")
    @app_commands.describe(member="Who earned them", points="How many", reason="What for (optional)")
    async def points_award(self, interaction: discord.Interaction, member: discord.Member,
                           points: int, reason: str = ""):
        cog = self._cog("Points")
        if not cog:
            await interaction.response.send_message("Points isn't loaded.", ephemeral=True)
            return
        await cog.award(interaction, member, points, reason)

    @points.command(name="take", description="Deduct house points from a member.")
    @app_commands.describe(member="Who loses them", points="How many", reason="What for (optional)")
    async def points_take(self, interaction: discord.Interaction, member: discord.Member,
                          points: int, reason: str = ""):
        cog = self._cog("Points")
        if not cog:
            await interaction.response.send_message("Points isn't loaded.", ephemeral=True)
            return
        await cog.take(interaction, member, points, reason)

    @points.command(name="awardhouse", description="Award or deduct points for a whole house at once.")
    @app_commands.describe(house="Which house", points="Use a negative number to deduct",
                           reason="What for (optional)")
    @app_commands.choices(house=HOUSE_CHOICES)
    async def points_awardhouse(self, interaction: discord.Interaction,
                                house: app_commands.Choice[str], points: int, reason: str = ""):
        cog = self._cog("Points")
        if not cog:
            await interaction.response.send_message("Points isn't loaded.", ephemeral=True)
            return
        await cog.awardhouse(interaction, house, points, reason)

    @points.command(name="undo", description="Reverse the most recent points entry.")
    async def points_undo(self, interaction: discord.Interaction):
        cog = self._cog("Points")
        if not cog:
            await interaction.response.send_message("Points isn't loaded.", ephemeral=True)
            return
        await cog.undo(interaction)

    # --------------------------------------------------------------- setup

    @staff_setup.command(name="pointsconfig", description="Show how house points are currently set up.")
    async def setup_pointsconfig(self, interaction: discord.Interaction):
        cog = self._cog("Admin")
        if not cog:
            await interaction.response.send_message("Admin isn't loaded.", ephemeral=True)
            return
        await cog.pointsconfig(interaction)

    @staff_setup.command(name="setstaffrole", description="Let a role award points alongside admins.")
    @app_commands.describe(role="The role that may award and deduct points")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_setstaffrole(self, interaction: discord.Interaction, role: discord.Role):
        cog = self._cog("Admin")
        if not cog:
            await interaction.response.send_message("Admin isn't loaded.", ephemeral=True)
            return
        await cog.setstaffrole(interaction, role)

    @staff_setup.command(name="sethouserole", description="Bind a house to a Discord role.")
    @app_commands.describe(house="Which house", role="The role its members hold")
    @app_commands.choices(house=HOUSE_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_sethouserole(self, interaction: discord.Interaction,
                                 house: app_commands.Choice[str], role: discord.Role):
        cog = self._cog("Admin")
        if not cog:
            await interaction.response.send_message("Admin isn't loaded.", ephemeral=True)
            return
        await cog.sethouserole(interaction, house, role)

    @staff_setup.command(name="setannounce", description="Post the standings automatically once a week.")
    @app_commands.describe(channel="Where to post", day="Which day (default Sunday)",
                           hour="Hour in UTC, 0-23 (default 18)")
    @app_commands.choices(day=WEEKDAY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_setannounce(self, interaction: discord.Interaction,
                                channel: discord.TextChannel,
                                day: app_commands.Choice[int] = None,
                                hour: int = 18):
        cog = self._cog("Admin")
        if not cog:
            await interaction.response.send_message("Admin isn't loaded.", ephemeral=True)
            return
        await cog.setannounce(interaction, channel, day, hour)

    # --------------------------------------------------------------- houses

    @houses.command(name="sort", description="Pin a member to a house, ignoring their roles.")
    @app_commands.describe(member="Who to sort", house="Which house")
    @app_commands.choices(house=HOUSE_CHOICES)
    async def houses_sort(self, interaction: discord.Interaction, member: discord.Member,
                          house: app_commands.Choice[str]):
        cog = self._cog("Admin")
        if not cog:
            await interaction.response.send_message("Admin isn't loaded.", ephemeral=True)
            return
        await cog.sort(interaction, member, house)

    @houses.command(name="unsort", description="Stop pinning a member and read their roles again.")
    @app_commands.describe(member="Who to release")
    async def houses_unsort(self, interaction: discord.Interaction, member: discord.Member):
        cog = self._cog("Admin")
        if not cog:
            await interaction.response.send_message("Admin isn't loaded.", ephemeral=True)
            return
        await cog.unsort(interaction, member)

    # ------------------------------------------------------------- identity

    @identity.command(
        name="wandreset",
        description="Let a wand choose someone again (patronus too; broom stays).",
    )
    @app_commands.describe(member="Whose wand to release")
    async def identity_wandreset(self, interaction: discord.Interaction, member: discord.Member):
        cog = self._cog("Wands")
        if not cog:
            await interaction.response.send_message("Wands isn't loaded.", ephemeral=True)
            return
        await cog.wandreset(interaction, member)

    @identity.command(
        name="broomreset",
        description="Free someone's broom claim (wand and patronus stay).",
    )
    @app_commands.describe(member="Whose broom to release")
    async def identity_broomreset(self, interaction: discord.Interaction, member: discord.Member):
        cog = self._cog("Brooms")
        if not cog:
            await interaction.response.send_message("Brooms isn't loaded.", ephemeral=True)
            return
        await cog.broomreset(interaction, member)

    @identity.command(
        name="upgradebroom",
        description="Freely raise a member's broom Speed or Altitude / control.",
    )
    @app_commands.describe(
        member="Whose broom to upgrade",
        stat="Speed or Altitude (control)",
        amount="How many points to add (default 1)",
    )
    @app_commands.choices(
        stat=[
            app_commands.Choice(name="Speed", value="speed"),
            app_commands.Choice(name="Altitude / control", value="altitude"),
        ],
    )
    async def identity_upgradebroom(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        stat: app_commands.Choice[str],
        amount: app_commands.Range[int, 1, 10] = 1,
    ):
        cog = self._cog("Brooms")
        if not cog:
            await interaction.response.send_message("Brooms isn't loaded.", ephemeral=True)
            return
        await cog.upgrade_broom(interaction, member, stat, amount)

    # -------------------------------------------------------------- descent

    @descent.command(name="reset", description="Wipe someone's Descent progress back to floor 1.")
    @app_commands.describe(member="Whose progress to reset (leave blank for your own)")
    async def descent_reset(self, interaction: discord.Interaction, member: discord.Member = None):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentreset(interaction, member)

    @descent.command(name="unlock", description="Clear the 3-loss lockout without wiping progress.")
    @app_commands.describe(member="Whose lockout to clear (leave blank for your own)")
    async def descent_unlock(self, interaction: discord.Interaction, member: discord.Member = None):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentunlock(interaction, member)

    @descent.command(name="boost", description="Add Descent HP / Attack / Defense points to a player.")
    @app_commands.describe(
        member="Who to boost",
        hp="Stat points to add to Max HP (12 HP each). Can be negative.",
        attack="Stat points to add to Attack (2 ATK each). Can be negative.",
        defense="Stat points to add to Defense (1 DEF each). Can be negative.",
    )
    async def descent_boost(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        hp: int = 0,
        attack: int = 0,
        defense: int = 0,
    ):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentboost(interaction, member, hp, attack, defense)

    # ----------------------------------------------------------------- raid

    @raid.command(name="reset", description="Clear weekly lockout or end the active raid.")
    @app_commands.describe(member="Clear this player's weekly lockout", end_run="Force-end the active raid")
    async def raid_reset(self, interaction: discord.Interaction,
                         member: discord.Member = None, end_run: bool = False):
        cog = self._cog("ThreeRaid")
        if not cog:
            await interaction.response.send_message("3Raid isn't loaded.", ephemeral=True)
            return
        await cog.raidreset(interaction, member, end_run)

    # ---------------------------------------------------------------- duels

    @duels.command(name="night", description="Start or end a House Duel Night — duel wins count double.")
    @app_commands.describe(action="Start or end it")
    @app_commands.choices(action=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end", value="end"),
    ])
    async def duels_night(self, interaction: discord.Interaction, action: app_commands.Choice[str]):
        cog = self._cog("Duels")
        if not cog:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        await cog.duelnight(interaction, action)

    # ------------------------------------------------------------ challenge

    @challenge.command(name="post", description="Post a challenge to the channel right now.")
    @app_commands.describe(tier="Which challenge to post")
    @app_commands.choices(tier=[
        app_commands.Choice(name="Daily (1 point)", value="daily"),
        app_commands.Choice(name="Trial (3 points)", value="trial"),
        app_commands.Choice(name="Weekly Rite (5 points)", value="rite"),
    ])
    async def challenge_post(self, interaction: discord.Interaction, tier: app_commands.Choice[str]):
        cog = self._cog("Quests")
        if not cog:
            await interaction.response.send_message("Quests isn't loaded.", ephemeral=True)
            return
        await cog.challenge(interaction, tier)

    @challenge.command(name="config", description="Set where and when challenges post automatically.")
    @app_commands.describe(channel="Where challenges should appear",
                           hour="Hour in UTC, 0-23", weekday="Which day the weekly Rite lands on")
    @app_commands.choices(weekday=[
        app_commands.Choice(name=d, value=i) for i, d in enumerate(
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def challenge_config(self, interaction: discord.Interaction,
                               channel: discord.TextChannel, hour: int = 18,
                               weekday: app_commands.Choice[int] = None):
        cog = self._cog("Quests")
        if not cog:
            await interaction.response.send_message("Quests isn't loaded.", ephemeral=True)
            return
        await cog.challengeconfig(interaction, channel, hour, weekday)

    # ------------------------------------------------------------------ hex

    @hex_g.command(name="cast", description="(Headmaster) Curse a student with a prank hex.")
    @app_commands.describe(member="Who to hex", effect="Which curse to cast",
                           duration="How many minutes it lasts (0 = until lifted; Limp Wand is always 60)")
    @app_commands.choices(effect=HEX_CHOICES)
    async def hex_cast(self, interaction: discord.Interaction, member: discord.Member,
                       effect: app_commands.Choice[str], duration: app_commands.Range[int, 0, 10080]):
        cog = self._cog("Hexes")
        if not cog:
            await interaction.response.send_message("Hexes isn't loaded.", ephemeral=True)
            return
        await cog.hex(interaction, member, effect, duration)

    @hex_g.command(name="lift", description="(Headmaster) Lift a hex early.")
    @app_commands.describe(member="Whose hex to lift")
    async def hex_lift(self, interaction: discord.Interaction, member: discord.Member):
        cog = self._cog("Hexes")
        if not cog:
            await interaction.response.send_message("Hexes isn't loaded.", ephemeral=True)
            return
        await cog.unhex(interaction, member)

    @hex_g.command(name="list", description="(Headmaster) Show who's currently hexed.")
    async def hex_list(self, interaction: discord.Interaction):
        cog = self._cog("Hexes")
        if not cog:
            await interaction.response.send_message("Hexes isn't loaded.", ephemeral=True)
            return
        await cog.hexlist(interaction)

    # --------------------------------------------------------------- market

    @market.command(name="sellreset", description="Clear someone's daily Marketplace sell-points cap.")
    @app_commands.describe(member="Whose sell attempts to reset")
    async def market_sellreset(self, interaction: discord.Interaction, member: discord.Member):
        cog = self._cog("Marketplace")
        if not cog:
            await interaction.response.send_message("Marketplace isn't loaded.", ephemeral=True)
            return
        await cog.sellreset(interaction, member)


async def setup(bot: commands.Bot):
    await bot.add_cog(Staff(bot))
    log.info("Staff command tree loaded (%s houses available)", len(HOUSES))
