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
            ("patronus", "Your patronus, cast from the same three words as your wand."),
            ("rumor", "Start a rumor about someone in Velmora. Believe nothing."),
            ("cast", "Banish whatever's in this channel. Needs a patronus already cast."),
            ("familiar", "Adopt a familiar (once, for good), name it, or see the one you have."),
            ("feed", "Feed your familiar. Once a day."),
            ("pet", "Pet your familiar. Once a day."),
            ("play", "Play with your familiar. Once a day."),
            ("scout", "Send your familiar out to bring something back. Once a day."),
        ],
    },
    "explore": {
        "title": "Explore Velmora",
        "staff": False,
        "note": "Places remember how you treat them. Not everything you can do is listed here.",
        "entries": [
            ("places", "Where you can go, and what's happening there."),
            ("explore", "Go somewhere and see what you find."),
            ("forage", "Search for ingredients and strange things."),
            ("satchel", "What you're carrying. Only you can see it."),
            ("use", "Try something from your satchel where you are."),
            ("offer", "Leave an offering where you are."),
        ],
    },
    "beasts": {
        "title": "Beasts",
        "staff": False,
        "note": ("A few times a day a beast wanders into the explore channel and says what it wants. "
                 "First to bring it befriends it. 70 to find - some only come out after dark."),
        "entries": [
            ("approach", "Befriend the beast that's here, if you have what it wants."),
            ("bestiary", "Every beast you've befriended - and the ones still out there."),
            ("summon", "Call one of your beasts to show off. Just for fun."),
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
            ("profile", "Your wand, patronus, points, duel rank, beasts and honours - or anyone's."),
            ("points", "Your points (or anyone's), this season and all time."),
            ("standings", "The House Cup table."),
            ("leaderboard", "The top earners."),
            ("duelrecord", "Your duel rank, wins, streak, rivals, and today's duel points left."),
            ("history", "Recent points activity."),
            ("housecup", "Past seasons and their champions."),
            ("triwizard history", "Every Tri-Wizard champion so far."),
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
            ("triwizard crown", "Record a Tri-Wizard Tournament winner."),
            ("triwizard remove", "Undo a Tri-Wizard entry made by mistake."),
        ],
    },
    "staff_world": {
        "title": "Staff — places",
        "staff": True,
        "entries": [
            ("world eventstart", "Start an event in a place (the Garden...)."),
            ("world eventend", "End a place's event."),
            ("world eventstatus", "What's happening around Velmora."),
            ("world academy", "Switch an academy event on or off, e.g. tournament."),
            ("world rep", "How a place feels about someone."),
            ("world give", "Put an item in someone's satchel."),
            ("world channel", "Set the channel a place lives in."),
            ("world reload", "Reload the places' writing without restarting."),
        ],
    },
    "staff_threats": {
        "title": "Staff — wild threats",
        "staff": True,
        "entries": [
            ("dementor channels", "Set the 4 channels a wild threat can appear in."),
            ("dementor summon", "Make one appear right now (any creature, or a dementor)."),
            ("duelnight", "Start or end a House Duel Night - duel wins count double."),
            ("beastadmin spawn", "Make a beast appear right now."),
            ("beastadmin channel", "Set which channel beasts appear in."),
            ("beastadmin status", "What's out there, and when the next beast comes."),
            ("dementor status", "What's configured and what's active."),
            ("dementor eventstart", "Start 'Attack on Velmora' - monsters flood every channel."),
            ("dementor eventend", "End the running event early and tally it up."),
            ("dementor eventstatus", "How the current event is going."),
        ],
    },
    "staff_reactionroles": {
        "title": "Staff — reaction roles",
        "staff": True,
        "note": "Reacting to the sign-up post hands out a role. One pick per row.",
        "entries": [
            ("reactionroles addstatus", "Add a Champion/Alumni-style reaction role."),
            ("reactionroles addhouse", "Add a house to the sign-up (reuses /sethouserole)."),
            ("reactionroles remove", "Drop an emoji from the sign-up."),
            ("reactionroles post", "Publish the sign-up message and react to it."),
            ("reactionroles config", "What's configured for the sign-up."),
            ("reactionroles setmember", "Set someone's role by hand."),
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
            ("wandreset", "Let a wand choose someone again (their patronus too)."),
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
        self._ids: dict = {}   # server id (or None for global) -> {name: id}

    async def _command_ids(self, guild=None) -> dict[str, int]:
        """Top-level command name -> Discord ID, needed for clickable
        mentions. Commands are registered to the server, so look there
        first; fall back to the global list. Cached per server; if it
        fails, help still works with plain /names."""
        key = getattr(guild, "id", None)
        if key in self._ids:
            return self._ids[key]
        try:
            fetched = await self.bot.tree.fetch_commands(guild=guild) if guild else []
            if not fetched:
                fetched = await self.bot.tree.fetch_commands()
            ids = {c.name: c.id for c in fetched}
        except Exception:
            log.exception("Couldn't fetch command IDs - showing plain names.")
            return {}
        if ids:
            self._ids[key] = ids
        return ids

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
        ids = await self._command_ids(interaction.guild)
        await interaction.response.send_message(embed=self.build(ids, is_staff), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
