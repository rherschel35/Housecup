"""
/help - everything the bot does, grouped, and only what applies to you.

Players see the games and their own standing. Staff see an extra section
for running events and setup. Commands are shown as clickable mentions,
so tapping one fills it into the message box ready to run.

Keep HELP in step with the commands. The test suite checks that every
slash command the bot defines appears here, so a new command can't be
added and quietly forgotten.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.help")

# (command path, what it does). A path with a space is a subcommand.
HELP = {
    "play": {
        "title": "Play",
        "staff": False,
        "entries": [
            ("duel", "Challenge someone. Best of three, spells chosen in secret."),
            ("bean", "Spend 1 point on a mystery bean. Might pay 5. Might be Mordy's socks."),
            ("wand", "Give any three words and a wand chooses you. Yours for good."),
        ],
    },
    "challenges": {
        "title": "Challenges",
        "staff": False,
        "note": ("Challenges are posted in the channel for everyone at once. "
                 "The **first three** to answer correctly win points - one attempt each."),
        "entries": [
            ("challengestatus", "What's open right now, and when the next ones come."),
        ],
    },
    "standing": {
        "title": "Your standing",
        "staff": False,
        "entries": [
            ("points", "Your points (or anyone's), this season and all time."),
            ("standings", "The House Cup table."),
            ("leaderboard", "The top earners."),
            ("duelrecord", "Wins, losses, and today's duel points left."),
            ("history", "Recent points activity."),
            ("housecup", "Past seasons and their champions."),
            ("season list", "Every season so far."),
            ("countdown status", "How long until the next big thing."),
        ],
    },
    "staff_events": {
        "title": "Staff — running things",
        "staff": True,
        "entries": [
            ("award", "Give a member points for their house."),
            ("take", "Take points from a member."),
            ("awardhouse", "Give or take points for a whole house."),
            ("undo", "Reverse the last points entry."),
            ("challenge", "Post a challenge right now."),
            ("countdown set", "Start a countdown."),
            ("countdown cancel", "Call off the countdown."),
            ("season end", "Crown a champion and start a new season."),
            ("season rename", "Rename the current season."),
        ],
    },
    "staff_setup": {
        "title": "Staff — setup",
        "staff": True,
        "entries": [
            ("challengeconfig", "Where and when challenges post."),
            ("setstaffrole", "Who counts as staff."),
            ("sethouserole", "Link a house to a role."),
            ("sort", "Pin a member to a house."),
            ("unsort", "Go back to reading their roles."),
            ("wandreset", "Let a wand choose someone again."),
            ("setannounce", "Post the standings weekly."),
            ("pointsconfig", "See how everything's set up."),
        ],
    },
}


def all_help_commands() -> set[str]:
    """Every command path the help text mentions."""
    return {path for section in HELP.values() for path, _ in section["entries"]}


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ids: dict[str, int] | None = None

    async def _command_ids(self) -> dict[str, int]:
        """Top-level command name -> Discord ID, needed for clickable
        mentions. Fetched once and cached; if it fails, help still works
        with plain /names."""
        if self._ids is None:
            try:
                fetched = await self.bot.tree.fetch_commands()
                self._ids = {c.name: c.id for c in fetched}
            except Exception:
                log.exception("Couldn't fetch command IDs - showing plain names.")
                return {}
        return self._ids

    @staticmethod
    def mention(path: str, ids: dict[str, int]) -> str:
        root = path.split(" ")[0]
        if root in ids:
            return f"</{path}:{ids[root]}>"
        return f"`/{path}`"

    def build(self, ids: dict[str, int], is_staff: bool) -> discord.Embed:
        embed = discord.Embed(
            title="Velmora",
            description=("Everything you do here earns points for your house. "
                         "The house with the most points when the season ends takes the House Cup."),
            color=0x6C5CE7,
        )
        for section in HELP.values():
            if section["staff"] and not is_staff:
                continue
            lines = []
            if section.get("note"):
                lines.append(section["note"])
            lines += [f"{self.mention(p, ids)} — {d}" for p, d in section["entries"]]
            embed.add_field(name=section["title"], value="\n".join(lines), inline=False)

        embed.set_footer(text="Tap a command to use it. Only you can see this.")
        return embed

    @app_commands.command(name="help", description="Everything this bot can do.")
    async def help(self, interaction: discord.Interaction):
        store = self.bot.get_cog("Store")
        is_staff = bool(store and store.is_staff(interaction.user))
        ids = await self._command_ids()
        await interaction.response.send_message(embed=self.build(ids, is_staff), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
