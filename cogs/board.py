"""
Standings and leaderboards.

    /standings [scope]     - the House Cup table
    /leaderboard [scope]   - top individual members
    /ranks [scope]         - everyone with points, ranked (paginated)
    /housecup              - past seasons and their winners

Plus an optional weekly standings post, off until an announce channel is set.
"""

import datetime
import logging
import math

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.store import HOUSES, house_display

log = logging.getLogger("velmora.board")

BAR_WIDTH = 12
MEDALS = ("\U0001F947", "\U0001F948", "\U0001F949")  # 1st, 2nd, 3rd
RANKS_PAGE_SIZE = 15

SCOPE_CHOICES = [
    app_commands.Choice(name="This season", value="season"),
    app_commands.Choice(name="All time", value="alltime"),
]


def _person_label(guild, uid: int) -> tuple[discord.Member | None, str]:
    """Prefer a real display name over <@id> mentions.

    Discord clients (especially mobile) often fail to resolve mentions inside
    embeds, so the raw user id shows up instead of a name. Writing the
    display name ourselves always reads correctly when the member is cached.
    """
    member = guild.get_member(uid) if guild else None
    if member is not None:
        return member, member.display_name
    # Not in the server (or not cached) — try the bot-wide user cache.
    user = None
    if guild is not None and getattr(guild, "_state", None) is not None:
        user = guild._state.get_user(uid)
    if user is not None:
        return None, user.global_name or user.name
    return None, f"User {uid}"


def _rank_lines(store, guild, rows: list[tuple[int, int]], start: int = 0) -> list[str]:
    """Format a slice of (user_id, points) as ranked lines."""
    lines = []
    for i, (uid, points) in enumerate(rows):
        rank = start + i + 1
        lead = MEDALS[rank - 1] if rank <= 3 else f"`#{rank}`"
        member, name = _person_label(guild, uid)
        house = store.member_house(member) if member else None
        tag = f" {HOUSES[house]['emoji']}" if house else ""
        lines.append(f"{lead} **{name}**{tag} — `{points:,}`")
    return lines


def build_ranks_embed(store, guild, scope: str, page: int, viewer_id: int | None = None) -> discord.Embed:
    rows = store.member_totals(scope, limit=None)
    total = len(rows)
    pages = max(1, math.ceil(total / RANKS_PAGE_SIZE)) if total else 1
    page = max(0, min(page, pages - 1))
    start = page * RANKS_PAGE_SIZE
    chunk = rows[start:start + RANKS_PAGE_SIZE]

    if not rows:
        body = "No one has earned anything yet."
    else:
        body = "\n".join(_rank_lines(store, guild, chunk, start=start))
        if viewer_id is not None:
            my_rank = store.member_rank(viewer_id, scope)
            my_pts = store.member_points(viewer_id, scope)
            if my_rank and my_pts > 0:
                body += f"\n\n*You are **#{my_rank}** with `{my_pts:,}`.*"
            elif my_pts <= 0:
                body += "\n\n*You don’t have points on this board yet.*"

    embed = discord.Embed(
        title="Point ranks" if scope == "season" else "Point ranks — all time",
        description=body,
        color=discord.Color.gold(),
    )
    footer = store.current_season()["name"] if scope == "season" else "All seasons combined"
    if total:
        footer += f" · {total} ranked · page {page + 1}/{pages}"
    embed.set_footer(text=footer)
    return embed


class RanksView(discord.ui.View):
    """Prev/next through the full ranked list."""

    def __init__(self, store, scope: str, page: int, pages: int, viewer_id: int):
        super().__init__(timeout=180)
        self.store = store
        self.scope = scope
        self.page = page
        self.pages = pages
        self.viewer_id = viewer_id
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.pages - 1

    async def _flip(self, interaction: discord.Interaction, delta: int):
        self.page = max(0, min(self.page + delta, self.pages - 1))
        self._sync_buttons()
        embed = build_ranks_embed(
            self.store, interaction.guild, self.scope, self.page, self.viewer_id
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._flip(interaction, -1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._flip(interaction, 1)


def _bar(points: int, top: int) -> str:
    """A proportional bar so the table reads at a glance, not as a column
    of numbers. Negative totals render empty rather than inverted."""
    if top <= 0 or points <= 0:
        return "·" * BAR_WIDTH
    filled = max(1, round(BAR_WIDTH * points / top))
    return "█" * filled + "·" * (BAR_WIDTH - filled)


def build_standings_embed(store, scope: str = "season") -> discord.Embed:
    season = store.current_season()
    if scope == "season" and not store.season_active():
        archive = store.state.get("archive") or []
        last = archive[-1] if archive else None
        parts = ["**No House Cup season is running.**"]
        if last:
            if last.get("winner"):
                parts.append(
                    f"Last cup: {house_display(last['winner'])} "
                    f"(**{last['name']}**)."
                )
            elif last.get("tied"):
                parts.append(
                    f"**{last['name']}** ended in a tie — "
                    + " & ".join(HOUSES[k]["name"] for k in last["tied"])
                    + "."
                )
            else:
                parts.append(f"**{last['name']}** has ended.")
        pending = season.get("name") or "the next season"
        parts.append(f"Staff open **{pending}** with `/season start`.")
        return discord.Embed(
            title="The House Cup",
            description="\n".join(parts),
            color=discord.Color.dark_grey(),
        )

    rows = store.house_totals(scope)
    top = max((p for _, p in rows), default=0)

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
        started_at = season.get("started_at")
        if started_at:
            started = datetime.datetime.fromtimestamp(started_at, datetime.timezone.utc)
            embed.set_footer(text=f"{season['name']} • since {started:%d %b %Y}")
        else:
            embed.set_footer(text=f"{season['name']}")
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
            member, name = _person_label(interaction.guild, uid)
            house = store.member_house(member) if member else None
            tag = f" {HOUSES[house]['emoji']}" if house else ""
            lines.append(f"{lead} **{name}**{tag} — `{points:,}`")

        embed = discord.Embed(
            title="Top of the school" if which == "season" else "All-Time Greats",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(
            text=store.current_season()["name"] if which == "season" else "All seasons combined"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="ranks",
        description="Everyone with points, ranked from first to last.",
    )
    @app_commands.describe(scope="This season (default) or all time")
    @app_commands.choices(scope=SCOPE_CHOICES)
    async def ranks(self, interaction: discord.Interaction,
                    scope: app_commands.Choice[str] = None):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return

        which = scope.value if scope else "season"
        rows = store.member_totals(which, limit=None)
        pages = max(1, math.ceil(len(rows) / RANKS_PAGE_SIZE)) if rows else 1
        embed = build_ranks_embed(
            store, interaction.guild, which, page=0, viewer_id=interaction.user.id
        )
        view = RanksView(store, which, page=0, pages=pages, viewer_id=interaction.user.id)
        # No pager chrome when everything fits on one page.
        if pages <= 1:
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(embed=embed, view=view)

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
            champs = record.get("champions", [])
            if champs:
                result += " \u2022 \U0001F3C6 " + " & ".join(f"<@{c['id']}>" for c in champs)
            lines.append(f"**{record['name']}** ({ended:%b %Y}) \u2014 {result}")

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
