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

_WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)
# Same labels and values as the old dropdowns. Autocomplete, not choices:
# the choice arrays alone keep /staffgame over Discord's 8000-character cap.
_ACTION_CHOICES = (
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="end", value="end"),
)
_LENGTH_CHOICES = (
    app_commands.Choice(name="30 minutes", value=30),
    app_commands.Choice(name="1 hour", value=60),
)
_WEEKDAY_CHOICES = tuple(
    app_commands.Choice(name=name, value=index)
    for index, name in enumerate(_WEEKDAY_NAMES)
)
_TIER_CHOICES = (
    app_commands.Choice(name="Daily (1 point)", value="daily"),
    app_commands.Choice(name="Trial (3 points)", value="trial"),
    app_commands.Choice(name="Weekly Rite (5 points)", value="rite"),
)
_BINGO_MODE_CHOICES = (
    app_commands.Choice(name="Traditional — any line", value="traditional"),
    app_commands.Choice(name="4 Corners", value="corners"),
    app_commands.Choice(name="Diagonals only", value="diagonals"),
    app_commands.Choice(name="Random pattern", value="random_pattern"),
)
_PRICE_CHOICES = (
    app_commands.Choice(name="Buy common ingredient", value="buy_common"),
    app_commands.Choice(name="Buy uncommon ingredient", value="buy_uncommon"),
    app_commands.Choice(name="Sell payout per batch", value="sell_payout"),
    app_commands.Choice(name="Daily sell-points cap", value="sell_daily_cap"),
    app_commands.Choice(name="Hex Scroll", value="scroll"),
    app_commands.Choice(name="Shop title", value="title"),
    app_commands.Choice(name="Room of Requirement", value="room"),
    app_commands.Choice(name="Broom upgrade token", value="broom_token"),
)


def _choice(value, name=None):
    """Rebuild the Choice object cogs already read `.value` from."""
    return app_commands.Choice(
        name=str(value) if name is None else name,
        value=value,
    )


def _weekday_choice(day: int | None):
    if day is None:
        return None
    return _choice(day, _WEEKDAY_NAMES[day])


def _match_choices(choices, current: str):
    """Suggestions for one option. Empty input returns the whole list."""
    needle = (current or "").strip().casefold()
    if not needle:
        return list(choices[:25])
    return [
        choice
        for choice in choices
        if needle in choice.name.casefold() or needle in str(choice.value).casefold()
    ][:25]


async def _complete_action(interaction: discord.Interaction, current: str):
    return _match_choices(_ACTION_CHOICES, current)


async def _complete_length(interaction: discord.Interaction, current: str):
    return _match_choices(_LENGTH_CHOICES, current)


async def _complete_weekday(interaction: discord.Interaction, current: str):
    return _match_choices(_WEEKDAY_CHOICES, current)


async def _complete_tier(interaction: discord.Interaction, current: str):
    return _match_choices(_TIER_CHOICES, current)


async def _complete_effect(interaction: discord.Interaction, current: str):
    return _match_choices(HEX_CHOICES, current)


async def _complete_bingo_mode(interaction: discord.Interaction, current: str):
    return _match_choices(_BINGO_MODE_CHOICES, current)


async def _complete_price(interaction: discord.Interaction, current: str):
    return _match_choices(_PRICE_CHOICES, current)


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
    bingo = sg.bingo
    frog = sg.frog
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
        name="patronusreset",
        description="Give someone a new patronus from three words (wand stays).",
    )
    @app_commands.describe(member="Whose patronus to reset")
    async def identity_patronusreset(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        cog = self._cog("Patronus")
        if not cog:
            await interaction.response.send_message("Patronus isn't loaded.", ephemeral=True)
            return
        await cog.staff_patronusreset(interaction, member)

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

    @descent.command(name="reset", description="Reset.")
    @app_commands.describe(member="Who")
    async def descent_reset(self, interaction: discord.Interaction, member: discord.Member = None):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentreset(interaction, member)

    @descent.command(name="unlock", description="Unlock.")
    @app_commands.describe(member="Who")
    async def descent_unlock(self, interaction: discord.Interaction, member: discord.Member = None):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentunlock(interaction, member)

    @descent.command(name="boost", description="Boost.")
    @app_commands.describe(
        member="Who",
        hp="HP pts",
        attack="ATK pts",
        defense="DEF pts",
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
        name="set",
        description="Stats.",
    )
    @app_commands.describe(
        member="Who",
        hp="Max HP",
        attack="ATK",
        defense="DEF",
    )
    async def descent_set(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        hp: app_commands.Range[int, 55, 100000],
        attack: app_commands.Range[int, 8, 100000],
        defense: app_commands.Range[int, 3, 100000],
    ):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentset(interaction, member, hp, attack, defense)

    @descent.command(
        name="backfill",
        description="Army.",
    )
    @app_commands.describe(
        member="Who",
    )
    async def descent_backfill(self, interaction: discord.Interaction, member: discord.Member):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentbackfill(interaction, member)

    @descent.command(
        name="restore",
        description="Restore.",
    )
    @app_commands.describe(
        member="Who",
        floor="Floor",
        backfill_army="Army",
    )
    async def descent_restore(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        floor: app_commands.Range[int, 1, 100],
        backfill_army: bool = True,
    ):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentrestore(interaction, member, floor, backfill_army)

    @descent.command(
        name="recover",
        description="Backups.",
    )
    @app_commands.describe(
        apply="Apply",
    )
    async def descent_recover(self, interaction: discord.Interaction, apply: bool = True):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentrecover(interaction, apply)

    @descent.command(
        name="restorebulk",
        description="Bulk.",
    )
    @app_commands.describe(
        mapping="Lines",
        backfill_army="Army",
    )
    async def descent_restorebulk(
        self,
        interaction: discord.Interaction,
        mapping: str,
        backfill_army: bool = True,
    ):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentrestorebulk(interaction, mapping, backfill_army)

    @descent.command(
        name="claims",
        description="Claims.",
    )
    async def descent_claims(self, interaction: discord.Interaction):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentclaims(interaction)

    @descent.command(
        name="fixap",
        description="Fix AP.",
    )
    @app_commands.describe(
        only_wrong="Wrong",
    )
    async def descent_fixap(
        self,
        interaction: discord.Interaction,
        only_wrong: bool = True,
    ):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentfixap(interaction, only_wrong)

    @descent.command(
        name="approveclaims",
        description="Approve.",
    )
    @app_commands.describe(
        backfill_army="Army",
        clear="Clear",
    )
    async def descent_approveclaims(
        self,
        interaction: discord.Interaction,
        backfill_army: bool = True,
        clear: bool = True,
    ):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentapproveclaims(interaction, backfill_army, clear)

    @descent.command(
        name="restorespire",
        description="Spire.",
    )
    @app_commands.describe(
        floor="Floor",
        backfill_army="Army",
    )
    async def descent_restorespire(
        self,
        interaction: discord.Interaction,
        floor: app_commands.Range[int, 1, 100] = 90,
        backfill_army: bool = True,
    ):
        cog = self._cog("Descent")
        if not cog:
            await interaction.response.send_message("Descent isn't loaded.", ephemeral=True)
            return
        await cog.descentrestorespire(interaction, floor, backfill_army)

    # -------------------------------------------------------------- castles

    @castles.command(
        name="unlock",
        description="Unlock.",
    )
    async def castles_unlock(self, interaction: discord.Interaction):
        cog = self._cog("Castles")
        if not cog:
            await interaction.response.send_message("Castles isn't loaded.", ephemeral=True)
            return
        await cog.staff_unlock_all(interaction)

    @castles.command(
        name="schedule",
        description="Days.",
    )
    async def castles_schedule(self, interaction: discord.Interaction):
        cog = self._cog("Castles")
        if not cog:
            await interaction.response.send_message("Castles isn't loaded.", ephemeral=True)
            return
        await cog.staff_restore_schedule(interaction)

    @castles.command(
        name="pvplive",
        description="Cap.",
    )
    @app_commands.describe(live="Cap on")
    async def castles_pvplive(self, interaction: discord.Interaction, live: bool):
        cog = self._cog("Castles")
        if not cog:
            await interaction.response.send_message("Castles isn't loaded.", ephemeral=True)
            return
        await cog.staff_set_pvp_live(interaction, live)

    @castles.command(
        name="seedarmy",
        description="Seed.",
    )
    @app_commands.describe(
        member="Who",
        count="Count",
        floor="Floor",
        clear="Wipe",
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

    @raid.command(name="reset", description="Reset.")
    @app_commands.describe(member="Who", end_run="End")
    async def raid_reset(self, interaction: discord.Interaction,
                         member: discord.Member = None, end_run: bool = False):
        cog = self._cog("ThreeRaid")
        if not cog:
            await interaction.response.send_message("3Raid isn't loaded.", ephemeral=True)
            return
        await cog.raidreset(interaction, member, end_run)

    @raid.command(
        name="unstick",
        description="Unstick.",
    )
    async def raid_unstick(self, interaction: discord.Interaction):
        cog = self._cog("ThreeRaid")
        if not cog:
            await interaction.response.send_message("3Raid isn't loaded.", ephemeral=True)
            return
        await cog.raidunstick(interaction)

    # ---------------------------------------------------------------- duels

    @duels.command(name="night", description="Night.")
    @app_commands.describe(
        action="Action",
        length="Length",
    )
    async def duels_night(
        self,
        interaction: discord.Interaction,
        action: str,
        length: int = None,
    ):
        cog = self._cog("Duels")
        if not cog:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        if action not in {c.value for c in _ACTION_CHOICES}:
            await interaction.response.send_message(
                "Action must be start or end.", ephemeral=True,
            )
            return
        length_choice = None
        if length is not None:
            known_length = next((c for c in _LENGTH_CHOICES if c.value == length), None)
            if known_length is None:
                await interaction.response.send_message(
                    "Pick a length: **30 minutes** or **1 hour**.", ephemeral=True,
                )
                return
            length_choice = known_length
        await cog.duelnight(interaction, _choice(action), length_choice)

    duels_night.autocomplete("action")(_complete_action)
    duels_night.autocomplete("length")(_complete_length)

    @duels.command(
        name="nightschedule",
        description="Nights.",
    )
    @app_commands.describe(
        day_a="Weekday",
        hour_a="Hour",
        length="Length",
        minute_a="Minute",
        day_b="2nd day",
        hour_b="Hour",
        minute_b="2nd min",
        day_c="3rd day",
        hour_c="Hour",
        minute_c="3rd min",
        channel="Skip",
    )
    async def duels_nightschedule(
        self,
        interaction: discord.Interaction,
        day_a: int,
        hour_a: app_commands.Range[int, 0, 23],
        length: int,
        minute_a: app_commands.Range[int, 0, 59] = 0,
        day_b: int = None,
        hour_b: app_commands.Range[int, 0, 23] = None,
        minute_b: app_commands.Range[int, 0, 59] = 0,
        day_c: int = None,
        hour_c: app_commands.Range[int, 0, 23] = None,
        minute_c: app_commands.Range[int, 0, 59] = 0,
        channel: discord.TextChannel = None,
    ):
        cog = self._cog("Duels")
        if not cog:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        if any(day is not None and not 0 <= day <= 6 for day in (day_a, day_b, day_c)):
            await interaction.response.send_message(
                "Pick a weekday from the list.", ephemeral=True,
            )
            return
        known_length = next((c for c in _LENGTH_CHOICES if c.value == length), None)
        if known_length is None:
            await interaction.response.send_message(
                "Pick a length: **30 minutes** or **1 hour**.", ephemeral=True,
            )
            return
        await cog.nightschedule(
            interaction,
            _weekday_choice(day_a), hour_a, minute_a,
            _weekday_choice(day_b), hour_b, minute_b,
            _weekday_choice(day_c), hour_c, minute_c,
            known_length,
            channel,
        )

    duels_nightschedule.autocomplete("day_a")(_complete_weekday)
    duels_nightschedule.autocomplete("day_b")(_complete_weekday)
    duels_nightschedule.autocomplete("day_c")(_complete_weekday)
    duels_nightschedule.autocomplete("length")(_complete_length)

    @duels.command(name="nightscheduleoff", description="Off.")
    async def duels_nightscheduleoff(self, interaction: discord.Interaction):
        cog = self._cog("Duels")
        if not cog:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        await cog.nightscheduleoff(interaction)

    @duels.command(
        name="nightscheduleroles",
        description="Roles.",
    )
    @app_commands.describe(
        champions="Role",
        wizards_and_witches="Role",
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

    @duels.command(name="nightschedulestatus", description="Status.")
    async def duels_nightschedulestatus(self, interaction: discord.Interaction):
        cog = self._cog("Duels")
        if not cog:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        await cog.nightschedulestatus(interaction)

    @duels.command(
        name="housedaygrant",
        description="Dailies.",
    )
    async def duels_housedaygrant(self, interaction: discord.Interaction):
        store = self._cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        duels = self._cog("Duels")
        quidditch = self._cog("Quidditch")
        if not duels:
            await interaction.response.send_message("Duels isn't loaded.", ephemeral=True)
            return
        if not quidditch:
            await interaction.response.send_message("Quidditch isn't loaded.", ephemeral=True)
            return

        actor_id = interaction.user.id
        trio_by = {r["house"]: r for r in duels.grant_all_houses_trio_set_today(actor_id)}
        quid_by = {r["house"]: r for r in quidditch.grant_all_houses_set_today(actor_id)}

        from cogs.duels import TRIO_HOUSE_COMPLETION_POINTS, TRIO_HOUSE_WIN_GOAL
        from cogs.quidditch import HOUSE_COMPLETION_POINTS, HOUSE_WIN_GOAL
        from cogs.store import HOUSE_KEYS

        lines: list[str] = []
        trio_awarded = 0
        quid_awarded = 0
        for house in HOUSE_KEYS:
            t = trio_by.get(house) or {}
            q = quid_by.get(house) or {}
            name = t.get("name") or q.get("name") or house
            t_bits = (
                f"+{t['awarded']} trio"
                if t.get("awarded")
                else ("trio already claimed" if t.get("already") else "trio marked")
            )
            q_bits = (
                f"+{q['awarded']} Quidditch"
                if q.get("awarded")
                else ("Quidditch already claimed" if q.get("already") else "Quidditch marked")
            )
            trio_awarded += int(t.get("awarded") or 0)
            quid_awarded += int(q.get("awarded") or 0)
            lines.append(f"• **{name}** — {t_bits}, {q_bits}")

        embed = discord.Embed(
            title="House daily sets granted",
            description=(
                f"Every house is at **{TRIO_HOUSE_WIN_GOAL}/{TRIO_HOUSE_WIN_GOAL}** trio "
                f"and **{HOUSE_WIN_GOAL}/{HOUSE_WIN_GOAL}** Quidditch for **today (Central)**.\n"
                f"Awarded **+{trio_awarded}** trio / **+{quid_awarded}** Quidditch "
                f"(+{TRIO_HOUSE_COMPLETION_POINTS} / +{HOUSE_COMPLETION_POINTS} per unpaid house).\n"
                "Fresh counters start automatically at the next Central midnight."
            ),
            color=0xC9A227,
        )
        embed.add_field(name="Houses", value="\n".join(lines) or "—", inline=False)
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------ challenge

    @challenge.command(name="post", description="Post.")
    @app_commands.describe(tier="Tier")
    async def challenge_post(self, interaction: discord.Interaction, tier: str):
        cog = self._cog("Quests")
        if not cog:
            await interaction.response.send_message("Quests isn't loaded.", ephemeral=True)
            return
        known = next((c for c in _TIER_CHOICES if c.value == tier), None)
        if known is None:
            await interaction.response.send_message(
                "Tier must be daily, trial, or rite.", ephemeral=True,
            )
            return
        await cog.challenge(interaction, known)

    challenge_post.autocomplete("tier")(_complete_tier)

    @challenge.command(name="config", description="Auto.")
    @app_commands.describe(channel="Channel",
                           hour="Hour", weekday="Weekday")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def challenge_config(self, interaction: discord.Interaction,
                               channel: discord.TextChannel, hour: int = 18,
                               weekday: int = None):
        cog = self._cog("Quests")
        if not cog:
            await interaction.response.send_message("Quests isn't loaded.", ephemeral=True)
            return
        if weekday is not None and not 0 <= weekday <= 6:
            await interaction.response.send_message(
                "Pick a weekday from the list.", ephemeral=True,
            )
            return
        await cog.challengeconfig(interaction, channel, hour, _weekday_choice(weekday))

    challenge_config.autocomplete("weekday")(_complete_weekday)

    # ------------------------------------------------------------------ hex

    @hex_g.command(name="cast", description="Cast.")
    @app_commands.describe(member="Who", effect="Curse",
                           duration="Mins")
    async def hex_cast(self, interaction: discord.Interaction, member: discord.Member,
                       effect: str, duration: app_commands.Range[int, 0, 10080]):
        cog = self._cog("Hexes")
        if not cog:
            await interaction.response.send_message("Hexes isn't loaded.", ephemeral=True)
            return
        known = next((c for c in HEX_CHOICES if c.value == effect), None)
        await cog.hex(interaction, member, known or _choice(effect), duration)

    hex_cast.autocomplete("effect")(_complete_effect)

    @hex_g.command(name="lift", description="Lift.")
    @app_commands.describe(member="Who")
    async def hex_lift(self, interaction: discord.Interaction, member: discord.Member):
        cog = self._cog("Hexes")
        if not cog:
            await interaction.response.send_message("Hexes isn't loaded.", ephemeral=True)
            return
        await cog.unhex(interaction, member)

    @hex_g.command(name="list", description="List.")
    async def hex_list(self, interaction: discord.Interaction):
        cog = self._cog("Hexes")
        if not cog:
            await interaction.response.send_message("Hexes isn't loaded.", ephemeral=True)
            return
        await cog.hexlist(interaction)

    # --------------------------------------------------------------- bingo

    @bingo.command(name="start", description="Start.")
    @app_commands.describe(
        mode="Mode",
        name="Name",
        channel="Skip",
    )
    async def bingo_start(
        self,
        interaction: discord.Interaction,
        mode: str,
        name: str = None,
        channel: discord.TextChannel = None,
    ):
        cog = self._cog("Bingo")
        if not cog:
            await interaction.response.send_message("Bingo isn't loaded.", ephemeral=True)
            return
        await cog.staff_start(interaction, name=name, mode=mode, channel=channel)

    bingo_start.autocomplete("mode")(_complete_bingo_mode)

    @bingo.command(name="call", description="Call.")
    @app_commands.describe(square="Square")
    async def bingo_call(self, interaction: discord.Interaction, square: str):
        cog = self._cog("Bingo")
        if not cog:
            await interaction.response.send_message("Bingo isn't loaded.", ephemeral=True)
            return
        await cog.staff_call(interaction, square)

    @bingo_call.autocomplete("square")
    async def bingo_call_autocomplete(self, interaction: discord.Interaction, current: str):
        cog = self._cog("Bingo")
        if not cog:
            return []
        return await cog.call_autocomplete(interaction, current)

    @bingo.command(name="end", description="End.")
    async def bingo_end(self, interaction: discord.Interaction):
        cog = self._cog("Bingo")
        if not cog:
            await interaction.response.send_message("Bingo isn't loaded.", ephemeral=True)
            return
        await cog.staff_end(interaction)

    @bingo.command(name="winners", description="Winners.")
    async def bingo_winners(self, interaction: discord.Interaction):
        cog = self._cog("Bingo")
        if not cog:
            await interaction.response.send_message("Bingo isn't loaded.", ephemeral=True)
            return
        await cog.staff_winners(interaction)

    # --------------------------------------------------------------- frog prizes

    @frog.command(name="prize", description="Card.")
    @app_commands.describe(member="Who", card="Card")
    async def frog_prize(
        self, interaction: discord.Interaction, member: discord.Member, card: str
    ):
        cog = self._cog("Frogs")
        if not cog:
            await interaction.response.send_message("Frogs isn't loaded.", ephemeral=True)
            return
        await cog.staff_prize(interaction, member, card)

    @frog_prize.autocomplete("card")
    async def frog_prize_autocomplete(self, interaction: discord.Interaction, current: str):
        cog = self._cog("Frogs")
        if not cog:
            return []
        return await cog.staff_prize_autocomplete(interaction, current)

    # --------------------------------------------------------------- market

    @market.command(name="sellreset", description="Cap.")
    @app_commands.describe(member="Who")
    async def market_sellreset(self, interaction: discord.Interaction, member: discord.Member):
        cog = self._cog("Marketplace")
        if not cog:
            await interaction.response.send_message("Marketplace isn't loaded.", ephemeral=True)
            return
        await cog.sellreset(interaction, member)

    @market.command(name="setprice", description="Price.")
    @app_commands.describe(
        item="Item",
        points="Points",
    )
    async def market_setprice(
        self,
        interaction: discord.Interaction,
        item: str,
        points: app_commands.Range[int, 0, 100_000],
    ):
        cog = self._cog("Marketplace")
        if not cog:
            await interaction.response.send_message("Marketplace isn't loaded.", ephemeral=True)
            return
        await cog.staff_setprice(interaction, item, int(points))

    market_setprice.autocomplete("item")(_complete_price)

    @market.command(name="prices", description="Prices.")
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
                "• `/familiar` → status / adopt / name / look / looks / feed / pet / play / scout\n"
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
