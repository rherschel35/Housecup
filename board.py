"""
Standings and leaderboards.

    /standings [scope]     - the House Cup table
    /leaderboard [scope]   - top individual members
    /housecup              - past seasons and their winners

Plus an optional weekly standings post, off until an announce channel is set.
"""

import datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.store import HOUSES, house_display

log = logging.getLogger("velmora.board")

BAR_WIDTH = 12
MEDALS = ("\U0001F947", "\U0001F948", "\U0001F949")  # 1st, 2nd, 3rd

SCOPE_CHOICES = [
    app_commands.Choice(name="This season", value="season"),
    app_commands.Choice(name="All time", value="alltime"),
]


def _bar(points: int, top: int) -> str:
    """A proportional bar so the table reads at a glance, not as a column
    of numbers. Negative totals render empty rather than inverted."""
    if top <= 0 or points <= 0:
        return "·" * BAR_WIDTH
    filled = max(1, round(BAR_WIDTH * points / top))
    return "█" * filled + "·" * (BAR_WIDTH - filled)


def build_standings_embed(store, scope: str = "season") -> discord.Embed:
    rows = store.house_totals(scope)
    top = max((p for _, p in rows), default=0)
    season = store.current_season()

    lines = []
    for i, (key, points) in enumerate(rows):
        meta = HOUSES[key]
        lead = MEDALS[i] if i < 3 and points > 0 else " "
        lines.append(f"{lead} {meta['emoji']} **{meta['name']}** — `{points:,}`\n`{_bar(points, top)}`")

    leader = rows[0] if rows else (None, 0)
    tied = [k for k, p in rows if p == leader[1] and p > 0]

    if not top:
        subtitle = "No points awarded yet. The board is wide open."
    elif len(tied) > 1:
        subtitle = "Dead level at the top — " + " and ".join(HOUSES[k]["name"] for k in tied) + "."
    else:
        margin = leader[1] - (rows[1][1] if len(rows) > 1 else 0)
        subtitle = (f"**House {HOUSES[leader[0]]['name']}** leads by {margin:,}."
                    if margin else f"**House {HOUSES[leader[0]]['name']}** leads.")

    embed = discord.Embed(
        title="The House Cup" if scope == "season" else "All-Time Standings",
        description=subtitle + "\n\n" + "\n".join(lines),
        color=HOUSES[leader[0]]["color"] if leader[0] and top else discord.Color.dark_grey(),
    )
    if scope == "season":
        started = datetime.datetime.fromtimestamp(season["started_at"], datetime.timezone.utc)
        embed.set_footer(text=f"{season['name']} • since {started:%d %b %Y}")
    else:
        embed.set_footer(text="Every point ever awarded, across all seasons.")
    return embed


class Board(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.weekly_post.start()

    def cog_unload(self):
        self.weekly_post.cancel()

    def _store(self):
        return self.bot.get_cog("Store")

    @app_commands.command(name="standings", description="The House Cup standings.")
    @app_commands.describe(scope="This season (default) or all time")
    @app_commands.choices(scope=SCOPE_CHOICES)
    async def standings(self, interaction: discord.Interaction,
                        scope: app_commands.Choice[str] = None):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_standings_embed(store, scope.value if scope else "season")
        )

    @app_commands.command(name="leaderboard", description="Top members by points earned.")
    @app_commands.describe(scope="This season (default) or all time")
    @app_commands.choices(scope=SCOPE_CHOICES)
    async def leaderboard(self, interaction: discord.Interaction,
                          scope: app_commands.Choice[str] = None):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return

        which = scope.value if scope else "season"
        rows = store.member_totals(which, limit=10)
        if not rows:
            await interaction.response.send_message(
                "No one has earned anything yet.", ephemeral=True
            )
            return

        lines = []
        for i, (uid, points) in enumerate(rows):
            lead = MEDALS[i] if i < 3 else f"`#{i + 1}`"
            member = interaction.guild.get_member(uid) if interaction.guild else None
            house = store.member_house(member) if member else None
            tag = f" {HOUSES[house]['emoji']}" if house else ""
            lines.append(f"{lead} <@{uid}>{tag} — `{points:,}`")

        embed = discord.Embed(
            title="Top of the school" if which == "season" else "All-Time Greats",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(
            text=store.current_season()["name"] if which == "season" else "All seasons combined"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="housecup", description="Past seasons and their champions.")
    async def housecup(self, interaction: discord.Interaction):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return

        archive = store.state.get("archive", [])
        if not archive:
            await interaction.response.send_message(
                "No season has finished yet — the first cup is still up for grabs.",
                embed=build_standings_embed(store, "season"),
            )
            return

        lines = []
        for record in archive[-10:]:
            ended = datetime.datetime.fromtimestamp(record["ended_at"], datetime.timezone.utc)
            if record.get("winner"):
                result = f"{house_display(record['winner'])}"
            elif record.get("tied"):
                result = "shared by " + " & ".join(HOUSES[k]["name"] for k in record["tied"])
            else:
                result = "no champion"
            lines.append(f"**{record['name']}** ({ended:%b %Y}) — {result}")

        embed = discord.Embed(
            title="The House Cup — past champions",
            description="\n".join(reversed(lines)),
            color=discord.Color.dark_gold(),
        )
        await interaction.response.send_message(embed=embed)

    # ----------------------------------------------------------- weekly post

    @tasks.loop(minutes=30)
    async def weekly_post(self):
        """Posts the standings once a week, only if a channel is configured.
        Checks twice an hour rather than sleeping a week, so a redeploy
        doesn't silently skip it."""
        store = self._store()
        if store is None:
            return
        channel_id = store.settings.get("announce_channel_id")
        if not channel_id:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        if now.weekday() != store.settings.get("announce_weekday", 6):
            return
        if now.hour != store.settings.get("announce_hour", 18):
            return

        stamp = f"{now.isocalendar().year}-W{now.isocalendar().week}"
        if store.settings.get("last_announced_week") == stamp:
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            log.warning("Announce channel %s is not visible to the bot.", channel_id)
            return

        try:
            await channel.send(
                content="**The week's standings.**",
                embed=build_standings_embed(store, "season"),
            )
            store.set_setting("last_announced_week", stamp)
        except discord.DiscordException:
            log.exception("Weekly standings post failed.")

    @weekly_post.before_loop
    async def before_weekly(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Board(bot))
