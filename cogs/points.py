"""
Awarding and taking points.

    /award <member> <points> [reason]   - staff
    /take  <member> <points> [reason]   - staff
    /awardhouse <house> <points> [...]  - staff, straight to a house
    /points [member]                    - anyone
    /history [member]                   - anyone
    /undo                               - staff, reverses the last entry
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cogs.store import HOUSES, house_display

log = logging.getLogger("velmora.points")

# The most a single command can move, so a slip of the keyboard can't hand
# one house ten thousand points.
MAX_PER_AWARD = 500

HOUSE_CHOICES = [
    app_commands.Choice(name=f"House {meta['name']}", value=key)
    for key, meta in HOUSES.items()
]


class Points(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _store(self):
        return self.bot.get_cog("Store")

    async def _guard(self, interaction: discord.Interaction, store) -> bool:
        """Staff-only gate. Returns True if the caller may proceed."""
        if store is None:
            await interaction.response.send_message(
                "The ledger isn't loaded right now. Try again in a moment.", ephemeral=True
            )
            return False
        if not store.is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff can move house points.", ephemeral=True
            )
            return False
        return True

    async def _change(self, interaction: discord.Interaction, member: discord.Member,
                      points: int, reason: str, *, taking: bool):
        store = self._store()
        if not await self._guard(interaction, store):
            return

        if points <= 0:
            await interaction.response.send_message(
                "Give me a positive number - use `/take` to deduct.", ephemeral=True
            )
            return
        if points > MAX_PER_AWARD:
            await interaction.response.send_message(
                f"{points} is more than the {MAX_PER_AWARD} cap for a single command.",
                ephemeral=True,
            )
            return
        if member.bot:
            await interaction.response.send_message(
                "The ghosts don't compete for the House Cup.", ephemeral=True
            )
            return

        house = store.member_house(member)
        if not house:
            await interaction.response.send_message(
                f"{member.display_name} isn't in a house yet. Give them their house role, "
                "or set it directly with `/sort`.",
                ephemeral=True,
            )
            return

        delta = -points if taking else points
        store.record(
            house=house,
            delta=delta,
            actor_id=interaction.user.id,
            target_id=member.id,
            reason=reason,
        )

        meta = HOUSES[house]
        season_total = store.state["totals"]["season"]["houses"].get(house, 0)
        verb = "loses" if taking else "earns"
        embed = discord.Embed(
            title=f"House {meta['name']} {verb} {points} point{'s' if points != 1 else ''}",
            description=(f"**{member.display_name}** — {reason}" if reason
                         else f"**{member.display_name}**"),
            color=0x9E4A4A if taking else meta["color"],
        )
        embed.add_field(name="House total (season)", value=f"{season_total:,}")
        embed.add_field(name=f"{member.display_name}'s points",
                        value=f"{store.member_points(member.id):,}")
        embed.set_footer(text=f"by {interaction.user.display_name} • {store.current_season()['name']}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="award", description="Award house points to a member.")
    @app_commands.describe(member="Who earned them", points="How many", reason="What for (optional)")
    async def award(self, interaction: discord.Interaction, member: discord.Member,
                    points: int, reason: str = ""):
        await self._change(interaction, member, points, reason, taking=False)

    @app_commands.command(name="take", description="Deduct house points from a member.")
    @app_commands.describe(member="Who loses them", points="How many", reason="What for (optional)")
    async def take(self, interaction: discord.Interaction, member: discord.Member,
                   points: int, reason: str = ""):
        await self._change(interaction, member, points, reason, taking=True)

    @app_commands.command(name="awardhouse",
                          description="Award or deduct points for a whole house at once.")
    @app_commands.describe(house="Which house", points="Use a negative number to deduct",
                           reason="What for (optional)")
    @app_commands.choices(house=HOUSE_CHOICES)
    async def awardhouse(self, interaction: discord.Interaction,
                         house: app_commands.Choice[str], points: int, reason: str = ""):
        store = self._store()
        if not await self._guard(interaction, store):
            return

        if points == 0:
            await interaction.response.send_message("Zero points is no change at all.", ephemeral=True)
            return
        if abs(points) > MAX_PER_AWARD:
            await interaction.response.send_message(
                f"That's past the {MAX_PER_AWARD} cap for a single command.", ephemeral=True
            )
            return

        store.record(house=house.value, delta=points, actor_id=interaction.user.id, reason=reason)
        meta = HOUSES[house.value]
        total = store.state["totals"]["season"]["houses"].get(house.value, 0)

        embed = discord.Embed(
            title=f"House {meta['name']} {'gains' if points > 0 else 'loses'} {abs(points)} points",
            description=reason or meta["motto"],
            color=meta["color"] if points > 0 else 0x9E4A4A,
        )
        embed.add_field(name="House total (season)", value=f"{total:,}")
        embed.set_footer(text=f"by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="points", description="See a member's house points.")
    @app_commands.describe(member="Whose points (leave blank for your own)")
    async def points_cmd(self, interaction: discord.Interaction, member: discord.Member = None):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return

        member = member or interaction.user
        house = store.member_house(member)
        season = store.member_points(member.id, "season")
        alltime = store.member_points(member.id, "alltime")
        rank = store.member_rank(member.id, "season")

        meta = HOUSES.get(house)
        embed = discord.Embed(
            title=member.display_name,
            description=house_display(house) if house else "Not sorted into a house yet.",
            color=meta["color"] if meta else discord.Color.dark_grey(),
        )
        embed.add_field(name="This season", value=f"{season:,}")
        embed.add_field(name="All time", value=f"{alltime:,}")
        if rank:
            embed.add_field(name="Rank", value=f"#{rank}")
        if house:
            embed.set_footer(text=f"House total this season: "
                                  f"{store.state['totals']['season']['houses'].get(house, 0):,}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="history", description="Recent points activity.")
    @app_commands.describe(member="Limit to one member (optional)")
    async def history(self, interaction: discord.Interaction, member: discord.Member = None):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return

        entries = store.history(user_id=member.id if member else None, limit=10)
        if not entries:
            await interaction.response.send_message(
                "Nothing on the books yet." if not member
                else f"Nothing recorded for {member.display_name} yet.",
                ephemeral=True,
            )
            return

        lines = []
        for e in entries:
            who = f"<@{e['target_id']}>" if e.get("target_id") else "the house"
            sign = "+" if e["delta"] > 0 else ""
            tail = f" — {e['reason']}" if e.get("reason") else ""
            lines.append(
                f"`{sign}{e['delta']}` {HOUSES[e['house']]['emoji']} {who}{tail}"
            )

        embed = discord.Embed(
            title="Recent entries" + (f" — {member.display_name}" if member else ""),
            description="\n".join(lines),
            color=discord.Color.dark_teal(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="undo", description="Reverse the most recent points entry.")
    async def undo(self, interaction: discord.Interaction):
        store = self._store()
        if not await self._guard(interaction, store):
            return

        entry = store.undo_last(actor_id=interaction.user.id)
        if entry is None:
            await interaction.response.send_message("There's nothing left to undo.", ephemeral=True)
            return

        who = f"<@{entry['target_id']}>" if entry.get("target_id") else "the house"
        sign = "+" if entry["delta"] > 0 else ""
        await interaction.response.send_message(
            f"Reversed `{sign}{entry['delta']}` for {who} "
            f"({house_display(entry['house'])}). The ledger is corrected."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Points(bot))
