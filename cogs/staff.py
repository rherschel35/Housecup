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
    """Owns the staff slash trees (subgroups are shared Group objects)."""

    staff = sg.staff
    staffworld = sg.staffworld
    staffgame = sg.staffgame
    staffops = sg.staffops
    points = sg.points
    staff_setup = sg.setup
    houses = sg.houses
    identity = sg.identity
    descent = sg.descent
    castles = sg.castles
    raid = sg.raid
    duels = sg.duels
    challenge = sg.challenge
    hex_g = sg.hexes
    market = sg.market
    usage = sg.usage

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

    @staff_setup.command(
        name="eventboard",
        description="Post/keep a living weekly Attack · Duel Night · House Cup board in a channel.",
    )
    @app_commands.describe(
        channel="Where the board lives (default: this channel, or the official schedule channel)",
    )
    async def setup_eventboard(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
    ):
        cog = self._cog("EventBoard")
        if not cog:
            await interaction.response.send_message("Event board isn't loaded.", ephemeral=True)
            return
        await cog.eventboard(interaction, channel)

    @staff_setup.command(
        name="eventboardoff",
        description="Stop updating the living weekly event schedule board.",
    )
    async def setup_eventboardoff(self, interaction: discord.Interaction):
        cog = self._cog("EventBoard")
        if not cog:
            await interaction.response.send_message("Event board isn't loaded.", ephemeral=True)
            return
        await cog.eventboardoff(interaction)

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
        name="animagusreset",
        description="Release someone's Animagus form so they can be found again.",
    )
    @app_commands.describe(member="Whose Animagus form to release")
    async def identity_animagusreset(self, interaction: discord.Interaction, member: discord.Member):
        cog = self._cog("Animagus")
        if not cog:
            await interaction.response.send_message("Animagus isn't loaded.", ephemeral=True)
            return
        await cog.animagusreset(interaction, member)

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

    @descent.command(
        name="backfill",
        description="Add missing army + boss trophies from cleared depth (never replaces army).",
    )
    @app_commands.describe(
        member="Who to backfill (pre-tracking Descent progress → army/bosses)",
    )
    async def descent_backfill(self, interaction: discord.Interaction, member: discord.Member):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentbackfill(interaction, member)

    # -------------------------------------------------------------- castles

    @castles.command(
        name="unlock",
        description="Clear all castle lock timers and allow sieges any day (skip Wed/Sat).",
    )
    async def castles_unlock(self, interaction: discord.Interaction):
        cog = self._cog("Castles")
        if not cog:
            await interaction.response.send_message("Castles isn't loaded.", ephemeral=True)
            return
        await cog.staff_unlock_all(interaction)

    @castles.command(
        name="schedule",
        description="Restore normal Wed/Sat siege days (turn off staff unlock).",
    )
    async def castles_schedule(self, interaction: discord.Interaction):
        cog = self._cog("Castles")
        if not cog:
            await interaction.response.send_message("Castles isn't loaded.", ephemeral=True)
            return
        await cog.staff_restore_schedule(interaction)

    @castles.command(
        name="pvplive",
        description="Turn the 200/day army bind cap on (season live) or off (pre-season uncapped).",
    )
    @app_commands.describe(live="True = PvP season live (200 binds/day). False = uncapped binds.")
    async def castles_pvplive(self, interaction: discord.Interaction, live: bool):
        cog = self._cog("Castles")
        if not cog:
            await interaction.response.send_message("Castles isn't loaded.", ephemeral=True)
            return
        await cog.staff_set_pvp_live(interaction, live)

    @castles.command(
        name="seedarmy",
        description="Staff test: add fake Descent army troops to a player.",
    )
    @app_commands.describe(
        member="Who gets the troops",
        count="How many to add (default 350, max 2000)",
        floor="Descent floor stats to use (default 69)",
        clear="Wipe their home army first, then seed",
    )
    async def castles_seedarmy(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        count: app_commands.Range[int, 1, 2000] = 350,
        floor: app_commands.Range[int, 1, 100] = 69,
        clear: bool = False,
    ):
        cog = self._cog("Castles")
        if not cog:
            await interaction.response.send_message("Castles isn't loaded.", ephemeral=True)
            return
        await cog.staff_seed_army(interaction, member, count=count, floor=floor, clear=clear)

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

    @duels.command(name="night", description="Start or end House Duel Night — 30 minutes or 1 hour.")
    @app_commands.describe(
        action="Start or end it",
        length="Required to start: 30 minutes or 1 hour",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end", value="end"),
    ])
    @app_commands.choices(length=[
        app_commands.Choice(name="30 minutes", value=30),
        app_commands.Choice(name="1 hour", value=60),
    ])
    async def duels_night(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        length: app_commands.Choice[int] = None,
    ):
        cog = self._cog("Duels")
        if not cog:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        await cog.duelnight(interaction, action, length)

    @duels.command(
        name="nightschedule",
        description="Schedule 1–3 weekly Duel Nights — each with its own Chicago time.",
    )
    @app_commands.describe(
        day_a="First night weekday (required)",
        hour_a="Hour for first night (America/Chicago, 0-23)",
        length="30 minutes or 1 hour for every scheduled night",
        minute_a="Minute for first night (0-59)",
        day_b="Optional second night weekday",
        hour_b="Hour for second night (required if day_b is set)",
        minute_b="Minute for second night (0-59)",
        day_c="Optional third night weekday",
        hour_c="Hour for third night (required if day_c is set)",
        minute_c="Minute for third night (0-59)",
        channel="Ignored — warn / start / results always post to the event announce channel",
    )
    @app_commands.choices(
        day_a=[
            app_commands.Choice(name="Monday", value=0),
            app_commands.Choice(name="Tuesday", value=1),
            app_commands.Choice(name="Wednesday", value=2),
            app_commands.Choice(name="Thursday", value=3),
            app_commands.Choice(name="Friday", value=4),
            app_commands.Choice(name="Saturday", value=5),
            app_commands.Choice(name="Sunday", value=6),
        ],
        day_b=[
            app_commands.Choice(name="Monday", value=0),
            app_commands.Choice(name="Tuesday", value=1),
            app_commands.Choice(name="Wednesday", value=2),
            app_commands.Choice(name="Thursday", value=3),
            app_commands.Choice(name="Friday", value=4),
            app_commands.Choice(name="Saturday", value=5),
            app_commands.Choice(name="Sunday", value=6),
        ],
        day_c=[
            app_commands.Choice(name="Monday", value=0),
            app_commands.Choice(name="Tuesday", value=1),
            app_commands.Choice(name="Wednesday", value=2),
            app_commands.Choice(name="Thursday", value=3),
            app_commands.Choice(name="Friday", value=4),
            app_commands.Choice(name="Saturday", value=5),
            app_commands.Choice(name="Sunday", value=6),
        ],
        length=[
            app_commands.Choice(name="30 minutes", value=30),
            app_commands.Choice(name="1 hour", value=60),
        ],
    )
    async def duels_nightschedule(
        self,
        interaction: discord.Interaction,
        day_a: app_commands.Choice[int],
        hour_a: app_commands.Range[int, 0, 23],
        length: app_commands.Choice[int],
        minute_a: app_commands.Range[int, 0, 59] = 0,
        day_b: app_commands.Choice[int] = None,
        hour_b: app_commands.Range[int, 0, 23] = None,
        minute_b: app_commands.Range[int, 0, 59] = 0,
        day_c: app_commands.Choice[int] = None,
        hour_c: app_commands.Range[int, 0, 23] = None,
        minute_c: app_commands.Range[int, 0, 59] = 0,
        channel: discord.TextChannel = None,
    ):
        cog = self._cog("Duels")
        if not cog:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        await cog.nightschedule(
            interaction,
            day_a, hour_a, minute_a,
            day_b, hour_b, minute_b,
            day_c, hour_c, minute_c,
            length, channel,
        )

    @duels.command(name="nightscheduleoff", description="Turn off the weekly Duel Night schedule.")
    async def duels_nightscheduleoff(self, interaction: discord.Interaction):
        cog = self._cog("Duels")
        if not cog:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        await cog.nightscheduleoff(interaction)

    @duels.command(
        name="nightscheduleroles",
        description="Roles pinged 5 min before and at Duel Night start.",
    )
    @app_commands.describe(
        champions="Champions role",
        wizards_and_witches="WIZARDS AND WITCHES role (combined community ping)",
    )
    async def duels_nightscheduleroles(
        self,
        interaction: discord.Interaction,
        champions: discord.Role,
        wizards_and_witches: discord.Role,
    ):
        cog = self._cog("Duels")
        if not cog:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        await cog.nightscheduleroles(interaction, champions, wizards_and_witches)

    @duels.command(name="nightschedulestatus", description="Show the weekly Duel Night schedule.")
    async def duels_nightschedulestatus(self, interaction: discord.Interaction):
        cog = self._cog("Duels")
        if not cog:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        await cog.nightschedulestatus(interaction)

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

    @market.command(name="setprice", description="Set the point price for a Marketplace item.")
    @app_commands.describe(
        item="What to reprice",
        points="New price in house points",
    )
    @app_commands.choices(item=[
        app_commands.Choice(name="Buy common ingredient", value="buy_common"),
        app_commands.Choice(name="Buy uncommon ingredient", value="buy_uncommon"),
        app_commands.Choice(name="Sell payout per batch", value="sell_payout"),
        app_commands.Choice(name="Daily sell-points cap", value="sell_daily_cap"),
        app_commands.Choice(name="Hex Scroll", value="scroll"),
        app_commands.Choice(name="Shop title", value="title"),
        app_commands.Choice(name="Room of Requirement", value="room"),
        app_commands.Choice(name="Broom upgrade token", value="broom_token"),
    ])
    async def market_setprice(
        self,
        interaction: discord.Interaction,
        item: app_commands.Choice[str],
        points: app_commands.Range[int, 0, 100_000],
    ):
        cog = self._cog("Marketplace")
        if not cog:
            await interaction.response.send_message("Marketplace isn't loaded.", ephemeral=True)
            return
        await cog.staff_setprice(interaction, item.value, int(points))

    @market.command(name="prices", description="Show current Marketplace prices (including staff overrides).")
    async def market_prices(self, interaction: discord.Interaction):
        cog = self._cog("Marketplace")
        if not cog:
            await interaction.response.send_message("Marketplace isn't loaded.", ephemeral=True)
            return
        await cog.staff_prices(interaction)

    # ---------------------------------------------------------------- usage

    @usage.command(name="top", description="Most-used slash commands since tracking started.")
    @app_commands.describe(limit="How many to show (default 25, max 50)")
    async def usage_top(
        self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 50] = 25
    ):
        cog = self._cog("Usage")
        if not cog:
            await interaction.response.send_message("Usage isn't loaded.", ephemeral=True)
            return
        await cog.show_top(interaction, limit)

    @usage.command(name="unused", description="Slash commands that have never been used.")
    async def usage_unused(self, interaction: discord.Interaction):
        cog = self._cog("Usage")
        if not cog:
            await interaction.response.send_message("Usage isn't loaded.", ephemeral=True)
            return
        await cog.show_unused(interaction)

    @usage.command(name="reset", description="Clear usage counters and start fresh.")
    async def usage_reset(self, interaction: discord.Interaction):
        cog = self._cog("Usage")
        if not cog:
            await interaction.response.send_message("Usage isn't loaded.", ephemeral=True)
            return
        await cog.do_reset(interaction)

    async def _run_command_sync(self, interaction: discord.Interaction) -> None:
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            import bot as bot_module

            ok = await bot_module.sync_commands(force=True)
        except Exception:
            log.exception("Staff-forced command sync failed")
            await interaction.followup.send(
                "Command sync crashed — check the bot logs.", ephemeral=True
            )
            return
        if ok:
            import bot as bot_module

            if bot_module.sync_outcome_is_wipe_only():
                lead = (
                    "Guild slash lists wiped clean (global tree unchanged — no full re-upload). "
                )
            else:
                lead = "Global commands pushed and guild slash lists wiped clean. "
            await interaction.followup.send(
                f"{lead}"
                "Fully quit Discord and reopen `/`.\n"
                "• `/familiar` → status / adopt / name / feed / pet / play / scout\n"
                "• Top-level `/play` · `/feed` · `/pet` · `/scout` should be gone",
                ephemeral=True,
            )
        else:
            import bot as bot_module

            await interaction.followup.send(
                "Sync didn't finish cleanly (rate limit, timeout, or guild wipe failed). "
                "Try again in a few minutes, or set FORCE_COMMAND_SYNC=1 and redeploy."
                f"{bot_module.sync_failure_hint()}",
                ephemeral=True,
            )

    @staff.command(
        name="sync",
        description="Force-push slash commands (clears stale /feed · /familiar).",
    )
    async def staff_sync(self, interaction: discord.Interaction):
        await self._run_command_sync(interaction)

    @app_commands.command(
        name="synccmds",
        description="Staff: wipe stale guild slash commands and refresh Discord's command list.",
    )
    async def synccmds(self, interaction: discord.Interaction):
        """Top-level alias — easier to find on mobile than /staff → sync."""
        await self._run_command_sync(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(Staff(bot))
    log.info("Staff command tree loaded (%s houses available)", len(HOUSES))
