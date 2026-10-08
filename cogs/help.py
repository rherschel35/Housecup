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
            ("duel", "Challenge someone. Best of 3 = 1 win; best of 5/7/9 = series of first-to-2 sets (set wins and losses both count on your record)."),
            ("trio scramble", "Open a casual 3v3 trio duel — any houses, join either side."),
            ("trio housematch", "Open a house-vs-house 3v3 trio duel (house role gated)."),
            ("grand", "Challenge someone to a Grand Duel (both need 50+ 1v1 wins)."),
            ("duelend", "Clear a stuck duel lock so you can fight again (staff can clear others)."),
            ("houseduels", "Each house's overall duelling win/loss record."),
            ("bean", "Spend 1 point on a mystery bean. Might pay 5. Might be Mordy's socks."),
            ("wand", "Give any three words and a wand chooses you. Yours for good."),
            ("patronus", "Your patronus, cast from the same three words as your wand."),
            ("animagus", "Your Animagus form from three words of your own — you don't pick it. Run again anytime for 1 min of animal sounds on your messages."),
            ("broom", "Claim your unique broom (see Brooms section for upgrades, races, and notes)."),
            ("rumor", "Explore secrets, student gossip, odd headmaster whispers, embarrassing ghosts."),
            ("cast", "Banish a wild threat — tap a spell button. 25s alone if you stirred it up."),
            ("familiar status", "Check in on your familiar — friendship and today's care checklist."),
            ("familiar adopt", "Adopt a salamander, raven, or fox for good (once)."),
            ("familiar name", "Give your familiar a name, or rename it."),
            ("familiar feed", "Feed your familiar. Once a day."),
            ("familiar pet", "Pet your familiar. Once a day."),
            ("familiar play", "Play with your familiar. Once a day."),
            ("familiar scout", "Send your familiar out to bring something back. Once a day."),
            ("raid", "Open a 3Raid lobby — Attacker, Specialty, Tank, 10 encounters."),
            ("raidstatus", "Your 3Raid weekly clear status."),
            ("piphowdoi", "Ask Pip Wick how to do something (public hearthling answer from help + Compendium)."),
            ("gonsomethingbroke", "Tell Gon something's broken — staff get a jump link to your ticket."),
        ],
    },
    "market": {
        "title": "Marketplace",
        "staff": False,
        "note": "Spend house points on ingredients, scrolls, titles, and the Room. Selling pays points back (daily cap).",
        "entries": [
            ("market browse", "The Marketplace catalogue — ingredients, scrolls, titles, Room."),
            ("market buy", "Buy a common or uncommon ingredient (prices set by staff)."),
            ("market sell", "Sell ingredients or Descent mats in batches (payout/cap set by staff)."),
            ("market scroll", "Buy a Hex Scroll."),
            ("market broomtoken", "Buy a Speed or Altitude broom upgrade token."),
            ("market title", "Buy an exclusive Marketplace title (search the full catalogue)."),
            ("market room", "Buy the Room of Requirement. Pings Headmasters."),
            ("hexscroll", "Cast a Hex Scroll on someone (30 min, random curse)."),
        ],
    },
    "descent": {
        "title": "The Descent",
        "staff": False,
        "note": ("Solo 100-floor dungeon — only in Descent channels. "
                 "Practice cleared floors with `/descend floor:n` for loot; nearby floors can sharpen a free stat pick. "
                 "`/descend auto:True` posts the next monster after each win. "
                 "Idle 3 minutes with no button press counts as a loss. "
                 "Wins bind monsters into your army for castle sieges."),
        "entries": [
            ("descend", "Fight the next monster (or resume). Use auto:True to chain wins without retyping."),
            ("descendend", "Forfeit an open fight (counts as a loss). Idle 3 min with no button also loses. Staff can forfeit others."),
            ("descentstatus", "Your floor, monster progress, stats, Max AP, and lockout."),
            ("army", "Full army roster (paged) + sacrifice 500 → ATK/DEF — also works here in Descent channels."),
        ],
    },
    "castles": {
        "title": "Castles & Army",
        "staff": False,
        "note": ("Army PvP in the castles channel. Bind Descent monsters, hold one of six castles, "
                 "siege on Wed & Sat (Chicago). Reinforce anytime except mid-assault. "
                 "One castle per player — abandon before attacking another."),
        "entries": [
            ("castles", "Map board: owners, perks, reinforce anytime (not mid-assault), siege, abandon."),
            ("army", "Full paged roster + sacrifice 500 → ATK/DEF (castles + Descent channels). Size (5000) and daily bind caps start when PvP goes live."),
        ],
    },
    "potions": {
        "title": "Potions",
        "staff": False,
        "note": "Brew from satchel ingredients. Some potions help in the Descent; others call private beast encounters.",
        "entries": [
            ("brew", "Spend two ingredients and try to brew a potion (🟢 = you have them)."),
            ("drink", "Drink a brewed potion from your satchel to activate it."),
            ("potions", "Potion Rep, recipes with effect text, and 🟢/🔴 ingredients you have."),
        ],
    },
    "quidditch": {
        "title": "Quidditch",
        "staff": False,
        "note": "Team pickup matches live here. Solo and challenge broom races (`/broomrace`) also run in the Quidditch channel — see Brooms.",
        "entries": [
            ("quidditch scramble", "Start a casual pickup match — any houses, either side."),
            ("quidditch housematch", "Start a house-vs-house Quidditch match."),
            ("quidditch stats", "Your Quidditch record and title."),
            ("broomrace", "Private broom races / challenges on this pitch (see Brooms for daily caps)."),
        ],
    },
    "board_games": {
        "title": "Chess & Checkers",
        "staff": False,
        "note": (
            "Chess and Checkers share the board-games channel. Challenge → Accept → "
            "Make move (piece, then square). Captures are mandatory in checkers; "
            "multi-jumps stay on the same piece."
        ),
        "entries": [
            ("chess challenge", "Challenge someone to Wizard's Chess (chess channel)."),
            ("chess move", "Make a chess move by squares (or use Make move on the board)."),
            ("chess resign", "Concede a chess match."),
            ("chess stats", "Chess wins, losses, draws, and active games."),
            ("checkers challenge", "Challenge someone to Wizard's Checkers (checkers channel)."),
            ("checkers move", "Make a checkers move by squares (or use Make move on the board)."),
            ("checkers resign", "Concede a checkers match."),
            ("checkers stats", "Checkers wins, losses, and active games."),
            ("chess reset", "(staff) Wipe someone's chess record."),
            ("checkers reset", "(staff) Wipe someone's checkers record."),
        ],
    },
    "brooms": {
        "title": "Brooms",
        "staff": False,
        "note": (
            "Claim one unique broom from your wand words (100 portraits). "
            "Upgrade Speed/Altitude toward 10, then race on the Quidditch pitch. "
            "Every stage shows the full set of lines — read them; stats only decide "
            "whether you can hold a clean line. Traps double time lost. "
            "Solo: 5 learning races/day (UTC). Challenges: always open; wins pay 3 pts "
            "(5 paid wins/day), then free play."
        ),
        "entries": [
            ("broom", "Claim your unique broom from the same three words — or view yours / @member's."),
            ("broomupgrade", "Raise Speed or Altitude on you or @member. Pay with Pitch Resin ×5, Descent mat ×100, or a market token."),
            ("market broomtoken", "Buy a Speed or Altitude upgrade token (50 pts). Spend with /broomupgrade."),
            ("broomrace", "Solo 6-stage race (Quidditch channel), or pass opponent: to challenge on the same track."),
            ("broomraceend", "Clear a stuck broom race — yours or anyone else's."),
            ("broomnotes", "Permanent study notes from courses you've finished (⚠ marks known traps when studied)."),
            ("checklist", "Daily points board — broom races, potions, and the rest of today's caps."),
        ],
    },
    "explore": {
        "title": "Explore Velmora",
        "staff": False,
        "note": ("Places remember how you treat them. Not everything you can do is listed here. "
                 "Exploring or foraging in the Dungeons or Forbidden Woods can stir up a wild threat — "
                 "you get 25 seconds alone to `/cast` before anyone else can try."),
        "entries": [
            ("places", "Where you can go, and what's happening there."),
            ("explore", "Go somewhere and see what you find."),
            ("forage", "Search for ingredients and strange things (Pitch Resin for broom upgrades is forage-only)."),
            ("satchel", "What you're carrying. Only you can see it."),
            ("use", "Try something from your satchel where you are."),
            ("offer", "Leave an offering where you are."),
        ],
    },
    "beasts": {
        "title": "Beasts",
        "staff": False,
        "note": ("A few times a day a beast wanders into the explore channel and says what it wants. "
                 "First to `/approach` gets 60 seconds alone; then anyone can try. "
                 "70 to find — including Nifflers — some only after dark."),
        "entries": [
            ("approach", "Befriend the beast that's here (first approach gets 60s exclusive)."),
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
                 "Gear to craft or earn; earned pieces have perks while worn (never for duels or points)."),
        "entries": [
            ("wizard", "Design how your wizard looks (including Feminine full-body)."),
            ("mirror", "Your wizard trading card (or anyone's): look, gear, title and stats."),
            ("jewelbox", "What you own (with perk / looks-only), what you're wearing, what you can craft."),
            ("crafts", "Craft book — recipes with ingredient dots, plus earned gear goals (Magpie's Eye, etc.)."),
            ("craft", "Make a piece from materials in your satchel."),
            ("wear", "Put on a piece you own — shows its perk if it has one."),
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
            ("profile", "Your Mirror, wand, patronus, Animagus, broom, points, duel rank, beasts and honours - or anyone's."),
            ("checklist", "Your daily points board (public) — duels, Quidditch, broom, beasts, potions, market, and more."),
            ("points", "Your points (or anyone's), this season and all time."),
            ("standings", "The House Cup table."),
            ("leaderboard", "The top earners."),
            ("ranks", "Everyone with points, ranked from first to last."),
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
            ("staff points award", "Give a member points for their house."),
            ("staff points take", "Take points from a member."),
            ("staff points awardhouse", "Give or take points for a whole house."),
            ("staff points undo", "Reverse the last points entry."),
            ("staffgame challenge post", "Post a challenge right now."),
            ("countdown set", "Start a countdown."),
            ("countdown cancel", "Call off the countdown."),
            ("season end", "Crown a champion and start a new season."),
            ("season rename", "Rename the current season."),
            ("triwizard crown", "Record a Tri-Wizard Tournament winner."),
            ("triwizard remove", "Undo a Tri-Wizard entry made by mistake."),
            ("staffgame descent boost", "Add Descent HP / Attack / Defense points to a player."),
            ("staffgame descent unlock", "Clear the 3-loss Descent lockout without wiping progress."),
            ("staffgame descent reset", "Wipe someone's Descent progress back to floor 1."),
            ("staffgame descent backfill", "Add missing army + boss trophies from cleared depth (keeps existing army)."),
            ("staffgame castles unlock", "Clear all castle lock timers and allow sieges any day."),
            ("staffgame castles schedule", "Restore normal Wed/Sat siege days after a staff unlock."),
            ("staffgame castles pvplive", "Turn 200/day army binds on (season live) or off (pre-season uncapped)."),
            ("staffgame castles seedarmy", "Test helper: add fake army troops (default 350 at floor 69)."),
            ("staff sync", "Force-push slash commands (wipes stale guild /feed · /play · flat /familiar)."),
            ("synccmds", "Same as /staff sync — top-level, easier on mobile."),
            ("staffgame raid reset", "Clear a player's 3Raid weekly lockout, or end the active raid."),
            ("staffgame market sellreset", "Clear someone's daily Marketplace sell-points cap."),
            ("staffgame market setprice", "Set the point price for scrolls, titles, Room, ingredients, etc."),
            ("staffgame market prices", "Show current Marketplace prices (including overrides)."),
            ("staffgame hex cast", "Curse a student — chat mangles, or Limp Wand (blocks /wand, /patronus, /broom, /cast for 1 hour)."),
            ("staffgame hex lift", "Lift a hex early."),
            ("staffgame hex list", "Show who's currently hexed."),
            ("staffgame duels night", "Start or end House Duel Night — pick 30 minutes or 1 hour; double points, no daily cap, end-night house/MVP bonuses."),
        ],
    },
    "staff_world": {
        "title": "Staff — places",
        "staff": True,
        "entries": [
            ("staffworld world eventstart", "Start an event in a place (the Garden...)."),
            ("staffworld world eventend", "End a place's event."),
            ("staffworld world eventstatus", "What's happening around Velmora."),
            ("staffworld world academy", "Switch an academy event on or off, e.g. tournament."),
            ("staffworld world rep", "How a place feels about someone."),
            ("staffworld world give", "Put an item in someone's satchel."),
            ("staffworld world channel", "Set the channel a place lives in."),
            ("staffworld world reload", "Reload the places' writing without restarting."),
        ],
    },
    "staff_threats": {
        "title": "Staff — wild threats",
        "staff": True,
        "entries": [
            ("staffworld dementor channels", "Set the channels a wild threat can appear in (optional 5th)."),
            ("staffworld dementor summon", "Make one appear right now (study hall OK for practice; Attack waves skip it)."),
            ("staffops beastadmin spawn", "Make a beast appear right now."),
            ("staffops beastadmin channel", "Set which channel beasts appear in."),
            ("staffops beastadmin status", "What's out there, and when the next beast comes."),
            ("staffops adornadmin give", "Give someone a piece of gear."),
            ("staffops adornadmin take", "Take a piece of gear back."),
            ("staffops adornadmin channel", "Where earned gear is announced."),
            ("staffops adornadmin status", "How gear is spread around the server."),
            ("staffworld dementor status", "What's configured and what's active."),
            ("staffworld dementor eventstart", "Start Attack on Velmora — 5-min (top 7 share 100) or 10-min (top 5 share 200)."),
            ("staffworld dementor practice", "House practice swarm in one channel — no points or rewards (staff or Presidents)."),
            ("staffworld dementor eventend", "End the running event early and tally it up."),
            ("staffworld dementor eventstatus", "How the current event is going."),
        ],
    },
    "staff_reactionroles": {
        "title": "Staff — reaction roles",
        "staff": True,
        "note": "Reacting to the sign-up post hands out a role. One pick per row.",
        "entries": [
            ("staffops reactionroles addstatus", "Add a Champion/Alumni-style reaction role."),
            ("staffops reactionroles addhouse", "Add a house to the sign-up (reuses /staff setup sethouserole)."),
            ("staffops reactionroles remove", "Drop an emoji from the sign-up."),
            ("staffops reactionroles setmessage", "Set the sign-up post title and intro text."),
            ("staffops reactionroles post", "Publish the sign-up message and react to it."),
            ("staffops reactionroles config", "What's configured for the sign-up."),
            ("staffops reactionroles setmember", "Set someone's role by hand."),
        ],
    },
    "staff_setup": {
        "title": "Staff — setup",
        "staff": True,
        "entries": [
            ("staffgame challenge config", "Where and when challenges post."),
            ("staff setup setstaffrole", "Who counts as staff."),
            ("staff setup sethouserole", "Link a house to a role."),
            ("staff houses sort", "Pin a member to a house."),
            ("staff houses unsort", "Go back to reading their roles."),
            ("staff identity wandreset", "Let a wand choose someone again (their patronus too; broom stays)."),
            ("staff identity broomreset", "Free someone's broom claim (wand and patronus stay)."),
            ("staff identity animagusreset", "Release someone's Animagus form so they can be found again."),
            ("staff identity upgradebroom", "Freely raise anyone's broom Speed or Altitude / control (pick the member)."),
            ("staff setup setannounce", "Post the standings weekly."),
            ("staff setup pointsconfig", "See how everything's set up."),
            ("staffops antispam on", "Turn on the personal cool-down anti-spam guard."),
            ("staffops antispam off", "Turn the anti-spam guard off."),
            ("staffops antispam status", "Whether anti-spam is on, and the cool-down ladder."),
            ("staff usage top", "Most-used slash commands since tracking started."),
            ("staff usage unused", "Slash commands that have never been used."),
            ("staff usage reset", "Clear usage counters and start fresh."),
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
