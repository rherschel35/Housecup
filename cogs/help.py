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
            ("trio scramble", "Open a casual 3v3 trio duel — any houses, join either side."),
            ("trio housematch", "Open a house-vs-house 3v3 trio duel (house role gated)."),
            ("grand", "Challenge someone to a Grand Duel (both need 50+ 1v1 wins)."),
            ("houseduels", "Each house's overall duelling win/loss record."),
            ("bean", "Spend 1 point on a mystery bean. Might pay 5. Might be Mordy's socks."),
            ("wand", "Give any three words and a wand chooses you. Yours for good."),
            ("patronus", "Your patronus, cast from the same three words as your wand."),
            ("rumor", "Explore secrets, student gossip, odd headmaster whispers, embarrassing ghosts."),
            ("cast", "Banish a wild threat in this channel. Needs a patronus already cast."),
            ("familiar", "Adopt a familiar (once, for good), name it, or see the one you have."),
            ("feed", "Feed your familiar. Once a day."),
            ("pet", "Pet your familiar. Once a day."),
            ("play", "Play with your familiar. Once a day."),
            ("scout", "Send your familiar out to bring something back. Once a day."),
            ("raid", "Open a 3Raid lobby — Attacker, Specialty, Tank, 10 encounters."),
            ("raidstatus", "Your 3Raid weekly clear status."),
            ("piphowdoi", "Ask Pip Wick how to do something (public hearthling answer from help + Compendium)."),
        ],
    },
    "market": {
        "title": "Marketplace",
        "staff": False,
        "note": "Spend house points on ingredients, scrolls, titles, and the Room. Selling pays points back (daily cap).",
        "entries": [
            ("market browse", "The Marketplace catalogue — ingredients, scrolls, titles, Room."),
            ("market buy", "Buy a common (3 pts) or uncommon (6 pts) ingredient."),
            ("market sell", "Sell 3 of the same common/uncommon for 3 pts (21/day cap)."),
            ("market scroll", "Buy a Hex Scroll (30 pts)."),
            ("market title", "Buy an exclusive Marketplace title (50 pts)."),
            ("market room", "Buy the Room of Requirement (100 pts). Pings Headmasters."),
            ("hexscroll", "Cast a Hex Scroll on someone (30 min, random curse)."),
        ],
    },
    "descent": {
        "title": "The Descent",
        "staff": False,
        "note": ("Solo 100-floor dungeon — only in Descent channels. "
                 "Practice cleared floors with `/descend floor:n` for loot; nearby floors can sharpen a free stat pick."),
        "entries": [
            ("descend", "Fight the next monster on your floor (or resume an open fight)."),
            ("descentstatus", "Your floor, monster progress, stats, Max AP, and lockout."),
        ],
    },
    "potions": {
        "title": "Potions",
        "staff": False,
        "note": "Brew from satchel ingredients. Some potions help in the Descent; others call private beast encounters.",
        "entries": [
            ("brew", "Spend two ingredients and try to brew a potion."),
            ("drink", "Drink a brewed potion from your satchel to activate it."),
            ("potions", "Your Potion Rep, discovered recipes, and brewed potions."),
        ],
    },
    "boards": {
        "title": "Chess & Checkers",
        "staff": False,
        "note": "Played in the chess channel. Wins can earn house points (daily cap). Beating your own house never pays.",
        "entries": [
            ("chess challenge", "Challenge someone to Wizard's Chess."),
            ("chess move", "Make a move (squares, or piece → square on the board)."),
            ("chess resign", "Concede a chess match in progress."),
            ("chessstats", "Chess wins, losses, title, and active games."),
            ("checkers challenge", "Challenge someone to Wizard's Checkers."),
            ("checkers move", "Make a checkers move."),
            ("checkers resign", "Concede a checkers match."),
            ("checkersstats", "Checkers wins, losses, title, and active games."),
        ],
    },
    "quidditch": {
        "title": "Quidditch",
        "staff": False,
        "entries": [
            ("quidditch scramble", "Start a casual pickup match — any houses, either side."),
            ("quidditch housematch", "Start a house-vs-house Quidditch match."),
            ("quidditchstats", "Your Quidditch record and title."),
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
                 "First to bring it befriends it. 70 to find — including Nifflers — some only after dark."),
        "entries": [
            ("approach", "Befriend the beast that's here, if you have what it wants."),
            ("bestiary", "Every beast you've befriended - and the ones still out there."),
            ("summon", "Call one of your beasts to show off. Just for fun."),
            ("study", "Study a befriended beast (once a day). Three pages = 5 house points once."),
            ("journal", "Open your Beast Journal — overview, or one beast's entry."),
        ],
    },
    "adornments": {
        "title": "Your wizard & gear",
        "staff": False,
        "note": ("Design your look in `/wizard` — Masculine, Feminine, or Feminine (full body). "
                 "40 gear pieces to craft or earn; earned pieces have perks while worn (never for duels or points)."),
        "entries": [
            ("wizard", "Design how your wizard looks (including Feminine full-body)."),
            ("mirror", "Your wizard trading card (or anyone's): look, gear, title and stats."),
            ("jewelbox", "What you own, what you're wearing, what you can craft."),
            ("craft", "Make a piece from materials in your satchel."),
            ("wear", "Put on a piece you own."),
            ("remove", "Take off whatever's in a slot."),
            ("title", "Choose which earned title shows under your name."),
            ("cheer", "House Cup Bracelet: a celebration for your house."),
            ("whistle", "Beastcaller's Whistle: call the next beast now. Once a week."),
            ("secrets", "Keeper's Talisman: how many secrets you haven't found."),
            ("nightwatch", "Nightwatch Pendant: night-beast heads-up on or off."),
        ],
    },
    "challenges": {
        "title": "Challenges",
        "staff": False,
        "note": ("Posted for everyone at once — first three correct answers win points. "
                 "Daily every 24 hours (pings @everyone); Trial and Rite on their schedule."),
        "entries": [
            ("challengestatus", "What's open right now, and when the next ones come."),
        ],
    },
    "standing": {
        "title": "Your standing",
        "staff": False,
        "entries": [
            ("profile", "Your Mirror, wand, patronus, points, duel rank, beasts and honours - or anyone's."),
            ("points", "Your points (or anyone's), this season and all time."),
            ("standings", "The House Cup table."),
            ("leaderboard", "The top earners."),
            ("duelrecord", "Your duel rank, wins, streak, rivals, trio/grand records, and today's duel points left."),
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
            ("descentboost", "Add Descent HP / Attack / Defense points to a player."),
            ("descentunlock", "Clear the 3-loss Descent lockout without wiping progress."),
            ("descentreset", "Wipe someone's Descent progress back to floor 1."),
            ("raidreset", "Clear a player's 3Raid weekly lockout, or end the active raid."),
            ("chess reset", "Wipe someone's chess record and drop their active matches."),
            ("checkers reset", "Wipe someone's checkers record and drop their active matches."),
            ("hex", "Curse a student's messages with a prank spell."),
            ("unhex", "Lift a hex early."),
            ("hexlist", "Show who's currently hexed."),
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
            ("adornadmin give", "Give someone a piece of gear."),
            ("adornadmin take", "Take a piece of gear back."),
            ("adornadmin channel", "Where earned gear is announced."),
            ("adornadmin status", "How gear is spread around the server."),
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
            ("antispam on", "Turn on the personal cool-down anti-spam guard."),
            ("antispam off", "Turn the anti-spam guard off."),
            ("antispam status", "Whether anti-spam is on, and the cool-down ladder."),
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

    def build(self, ids: dict[str, int], is_staff: bool, staff_part: bool = False) -> discord.Embed:
        """One embed: the player sections, or (staff_part=True) the staff
        sections. They're sent as two messages because Discord caps a
        message at 6000 characters of embeds, and with clickable command
        mentions everything together is longer than that."""
        if staff_part:
            embed = discord.Embed(title="Velmora — staff",
                                  description="Only staff can see and use these.", color=0x4B3F99)
        else:
            embed = discord.Embed(
                title="Velmora",
                description=("Everything you do here earns points for your house. "
                             "The house with the most points when the season ends takes the House Cup."),
                color=0x6C5CE7,
            )
        for section in HELP.values():
            if section["staff"] != staff_part:
                continue
            if section["staff"] and not is_staff:
                continue
            lines = []
            if section.get("note"):
                lines.append(section["note"])
            lines += [f"{self.mention(p, ids)} — {d}" for p, d in section["entries"]]
            # a field holds 1024 characters; long sections carry on in a second field
            chunk, name = [], section["title"]
            for line in lines:
                if chunk and len("\n".join(chunk + [line])) > 1024:
                    embed.add_field(name=name, value="\n".join(chunk), inline=False)
                    chunk, name = [], f"{section['title']} (cont.)"
                chunk.append(line)
            if chunk:
                embed.add_field(name=name, value="\n".join(chunk), inline=False)

        embed.set_footer(text="Tap a command to use it. Only you can see this.")
        return embed

    @app_commands.command(name="help", description="Everything this bot can do.")
    async def help(self, interaction: discord.Interaction):
        store = self.bot.get_cog("Store")
        is_staff = bool(store and store.is_staff(interaction.user))
        ids = await self._command_ids(interaction.guild)
        await interaction.response.send_message(embed=self.build(ids, is_staff), ephemeral=True)
        if is_staff:
            await interaction.followup.send(embed=self.build(ids, True, staff_part=True), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
