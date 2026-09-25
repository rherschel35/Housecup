"""
Wizard duels. Best of three, spells chosen in secret.

    /duel @member        - challenge someone
    /duelrecord [member] - rank, wins, streak, rivals, and today's duel points
    /houseduels          - each house's overall win/loss duelling record
    /duelnight start|end - (staff) House Duel Night: duel wins count double

Rewards: ranks by total wins, hidden Dueling Circle reputation (flourish,
wand glow, one-time gift), win streaks with bounties, rivals, signature
spells with titles, and a weekly Duelist of the Week (role + house points).
Only wins, ranks, streaks and titles are ever shown - never a win rate.

Five spells. Each beats exactly two others and loses to the other two, so
there is no safe pick - only reading your opponent. Both duelists choose
privately, then both spells are revealed at once.

The winner's house earns 1 point, up to 3 duel points per person per day.
Duels past that still count toward the win/loss record. Duels between two
members of the SAME house never award points - otherwise two housemates
could simply trade wins and mint points for their house.

Wands are cosmetic: they're shown in the duel, but they don't affect it.
"""

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("velmora.duels")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
DUELS_PATH = STATE_DIR / "duels.json"

ROUNDS_TO_WIN = 2          # best of three
MAX_ROUNDS = 9             # ties replay; this stops an endless run of them
ACCEPT_TIMEOUT = 120
ROUND_TIMEOUT = 60
REWARD_POINTS = 1
REWARDED_PER_DAY = 3
WINDOW = 24 * 3600

# Optional, off by default: a single Discord user ID who quietly wins duels
# more often than the spells alone would give them. Set DUEL_FAVORED_USER_ID
# in the environment to turn it on for that account - no code change needed
# to flip it on/off or retarget it. When unset, duels are decided purely by
# resolve() below, exactly as documented.
FAVORED_USER_ID = os.getenv("DUEL_FAVORED_USER_ID")
FAVORED_USER_ID = int(FAVORED_USER_ID) if FAVORED_USER_ID and FAVORED_USER_ID.isdigit() else None
# Chance, PER ROUND, that the favored user's round is decided in their favor
# regardless of what either side cast. Rounds that don't trigger this fall
# through to the real rock-paper-scissors-of-five above, which is roughly a
# coin flip against an unpredictable opponent. There's no clean closed form
# from a per-round bias to a match-level win rate (best of 3, ties replay),
# so this default was tuned by simulation against a random opponent:
# 0.455 per round -> ~85% of matches won overall, 0.342 -> ~78% (the
# current setting; tuned with /tmp/test_duel_bias.py). A more adversarial opponent who could
# somehow read your picks would knock this down toward the raw per-round
# number, never above it - the bias never makes you invincible, only likely.
FAVORED_ROUND_BIAS = float(os.getenv("DUEL_FAVORED_BIAS", "0.342"))

SPELLS = {
    "hex":    {"name": "Hex",    "emoji": "⚡"},
    "ward":   {"name": "Ward",   "emoji": "\U0001F6E1️"},
    "disarm": {"name": "Disarm", "emoji": "\U0001FA84"},
    "bind":   {"name": "Bind",   "emoji": "⛓️"},
    "mirror": {"name": "Mirror", "emoji": "\U0001FA9E"},
}

# (winner, loser) -> what happened. Every pair appears exactly once, and each
# spell wins twice and loses twice.
BEATS = {
    ("hex", "disarm"):   "The hex lands before they can reach for a wand.",
    ("hex", "bind"):     "The hex tears straight through the binding.",
    ("ward", "hex"):     "The ward swallows the hex whole.",
    ("ward", "mirror"):  "A ward gives a mirror nothing to throw back.",
    ("disarm", "ward"):  "Their wand is gone before the ward can form.",
    ("disarm", "mirror"): "A mirror can't reflect a wand that's already flying.",
    ("bind", "ward"):    "The binding coils round the ward and squeezes it shut.",
    ("bind", "disarm"):  "Bound hands can't disarm anyone.",
    ("mirror", "hex"):   "The hex rebounds off the mirror into its own caster.",
    ("mirror", "bind"):  "The binding rebounds and ties up the one who cast it.",
}


def resolve(a: str, b: str) -> tuple[int, str]:
    """0 = tie, 1 = first spell wins, 2 = second spell wins; plus the line."""
    if a == b:
        return 0, f"Both cast {SPELLS[a]['name']}. The spells cancel out."
    if (a, b) in BEATS:
        return 1, BEATS[(a, b)]
    return 2, BEATS[(b, a)]


# ------------------------------------------------------------ duel rewards
#
# Ranks come from all-time wins. Everything shown publicly is wins, rank,
# streaks and titles - never a win rate or a loss count.

RANKS = [            # (minimum wins, title) - highest first
    (100, "Legend"),
    (60, "Master of the Circle"),
    (30, "Spellblade"),
    (15, "Duelist"),
    (5, "Apprentice"),
    (1, "Novice"),
    (0, "Untested"),
]

SIGNATURE_AT = 10            # round wins with one spell to make it your signature
SIGNATURE_TITLES = {
    "hex": "the Hexer",
    "ward": "the Wall",
    "disarm": "the Quickdraw",
    "bind": "the Binder",
    "mirror": "the Trickster",
}

STREAK_ANNOUNCE = 3          # wins in a row before the channel hears about it
STREAK_EVERY = 5             # past the bounty, only announce every 5th win in a row
ANNOUNCE_RANKS_FROM = 5      # Novice (first win) isn't worth a post; Apprentice is
BOUNTY_AT = 5                # wins in a row before a bounty goes up
BOUNTY_POINTS = 2            # paid to whoever breaks it (outside the daily cap)

RIVAL_AFTER = 5              # duels between the same two people
RIVAL_BONUS = 1              # extra point for beating your rival (uses a cap slot)

# Hidden Dueling Circle reputation.
REP_WIN, REP_LOSS = 2, 1
REP_FLOURISH = 10            # your wins get a personal flourish
REP_GLOW = 20                # wand glow on your profile
REP_GIFT = 30                # one-time gift from the Circle
GIFT_POINTS = 3

WEEKLY_POINTS = 5            # Duelist of the Week's house bonus
CHAMPION_ROLE_NAME = os.getenv("DUEL_CHAMPION_ROLE_NAME", "Champion of the Circle")
DUEL_NIGHT_MULTIPLIER = 2
DUEL_NIGHT_MAX_HOURS = 6     # a forgotten duel night switches itself off

FLOURISHES = [
    "The Circle hums as {name} lowers their wand.",
    "Sparks drift up from {name}'s wand like it's showing off.",
    "{name} bows to the Circle. The Circle, somehow, bows back.",
    "The torches around the Circle flare for {name}.",
    "A ripple of silver runs through the floor under {name}'s feet.",
]

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    import datetime as _dt
    TZ = _dt.timezone.utc


def rank_for(wins: int) -> str:
    for threshold, title in RANKS:
        if wins >= threshold:
            return title
    return "Untested"


def rank_index(wins: int) -> int:
    """0 = lowest rank. Used to spot a rank-up."""
    idx = 0
    for i, (threshold, _) in enumerate(sorted(RANKS)):
        if wins >= threshold:
            idx = i
    return idx


def week_key(now: float = None) -> str:
    import datetime as dt
    d = dt.datetime.fromtimestamp(now if now is not None else time.time(), TZ)
    y, w, _ = d.isocalendar()   # ISO weeks start Monday
    return f"{y}-W{w:02d}"


def pair_key(a: int, b: int) -> str:
    return f"{min(a, b)}-{max(a, b)}"


class Duels(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.busy: set[int] = set()   # members currently in a duel

    async def cog_load(self):
        self.weekly.start()

    async def cog_unload(self):
        self.weekly.cancel()

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(DUELS_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        except (OSError, json.JSONDecodeError):
            log.exception("Duel records unreadable - starting fresh.")
            state = {}
        state.setdefault("records", {})
        state.setdefault("rewarded", {})
        for key in ("rep", "streaks", "bounties", "pairs", "spell_wins", "signature",
                    "rank_seen", "gifted", "rivals_announced"):
            state.setdefault(key, {})
        state.setdefault("week", {"key": week_key(), "wins": {}})
        state.setdefault("champion", None)      # {"user_id", "week"}
        state.setdefault("duel_night", None)    # {"by", "at"}
        if not state.get("rewards_backfilled"):
            self._backfill(state)
        return state

    @staticmethod
    def _backfill(state: dict) -> None:
        """First run of the reward system: everyone starts at the rank and
        Circle reputation their existing record has already earned. Ranks
        are set quietly (no flood of announcements); anyone already past
        the gift threshold receives it on their next duel."""
        for uid, rec in state["records"].items():
            w, l = rec.get("w", 0), rec.get("l", 0)
            state["rep"][uid] = w * REP_WIN + l * REP_LOSS
            state["rank_seen"][uid] = rank_index(w)
        state["rewards_backfilled"] = True
        log.info("Duel rewards backfilled for %d duellists", len(state["records"]))

    def save(self) -> None:
        try:
            DUELS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = DUELS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, DUELS_PATH)
        except OSError:
            log.exception("Could not save duel records.")

    def record_of(self, user_id: int) -> dict:
        return self.state["records"].setdefault(str(user_id), {"w": 0, "l": 0})

    def rewarded_today(self, user_id: int, now: float = None) -> int:
        now = now if now is not None else time.time()
        stamps = [t for t in self.state["rewarded"].get(str(user_id), []) if now - t < WINDOW]
        self.state["rewarded"][str(user_id)] = stamps
        return len(stamps)

    # ------------------------------------------------------- public helpers

    def wins_of(self, user_id: int) -> int:
        return self.state["records"].get(str(user_id), {}).get("w", 0)

    def rep_of(self, user_id: int) -> int:
        return self.state["rep"].get(str(user_id), 0)

    def streak_of(self, user_id: int) -> int:
        return self.state["streaks"].get(str(user_id), 0)

    def has_bounty(self, user_id: int) -> bool:
        return str(user_id) in self.state["bounties"]

    def signature_of(self, user_id: int):
        """(spell key, title) once they've earned a signature, else None."""
        key = self.state["signature"].get(str(user_id))
        return (key, SIGNATURE_TITLES[key]) if key in SIGNATURE_TITLES else None

    def rivals_of(self, user_id: int) -> list[int]:
        """Everyone they've duelled RIVAL_AFTER+ times, most duelled first."""
        me = str(user_id)
        rows = []
        for key, n in self.state["pairs"].items():
            a, b = key.split("-")
            if n >= RIVAL_AFTER and me in (a, b):
                rows.append((n, int(b if a == me else a)))
        rows.sort(reverse=True)
        return [uid for _, uid in rows]

    def is_champion(self, user_id: int) -> bool:
        c = self.state.get("champion")
        return bool(c and c.get("user_id") == user_id)

    def duel_night_on(self, now: float = None) -> bool:
        night = self.state.get("duel_night")
        if not night:
            return False
        now = now if now is not None else time.time()
        if now - night.get("at", 0) > DUEL_NIGHT_MAX_HOURS * 3600:
            self.state["duel_night"] = None
            self.save()
            return False
        return True

    def note_round_win(self, user_id: int, spell: str) -> None:
        """A round won with this spell. Called as each round resolves."""
        tally = self.state["spell_wins"].setdefault(str(user_id), {})
        tally[spell] = tally.get(spell, 0) + 1

    # -------------------------------------------------------------- settle

    def _roll_week(self, now: float) -> None:
        key = week_key(now)
        if self.state["week"].get("key") != key:
            # The loop normally crowns the champion first; if it missed the
            # boundary (bot was down), the old week's tally is kept aside so
            # it can still be crowned.
            if self.state["week"].get("wins"):
                self.state["pending_week"] = self.state["week"]
            self.state["week"] = {"key": key, "wins": {}}

    def _award(self, store, house, delta, target_id, reason):
        store.record(house=house, delta=delta,
                     actor_id=self.bot.user.id if self.bot.user else 0,
                     target_id=target_id, reason=reason)

    def settle(self, winner, loser, now: float = None) -> dict:
        """Record the result, award points, and work out every reward it
        triggers. Returns what happened so the announcement can say so."""
        now = now if now is not None else time.time()
        wid, lid = str(winner.id), str(loser.id)
        self._roll_week(now)

        self.record_of(winner.id)["w"] += 1
        self.record_of(loser.id)["l"] += 1
        notes: list[str] = []

        store = self.bot.get_cog("Store")
        w_house = store.member_house(winner) if store else None
        l_house = store.member_house(loser) if store else None
        night = self.duel_night_on(now)

        # rivalry (counted before scoring so the 5th duel already pays)
        pk = pair_key(winner.id, loser.id)
        self.state["pairs"][pk] = self.state["pairs"].get(pk, 0) + 1
        rivals = self.state["pairs"][pk] >= RIVAL_AFTER
        if rivals and not self.state["rivals_announced"].get(pk):
            self.state["rivals_announced"][pk] = True
            notes.append(f"⚔️ **{winner.display_name}** and **{loser.display_name}** have met "
                         f"{self.state['pairs'][pk]} times. They are now **Rivals**.")

        # ------------------------------------------------ house points
        outcome = {"awarded": 0, "reason": None, "house": w_house, "notes": notes,
                   "night": night, "rival": rivals}
        if not store or not w_house:
            outcome["reason"] = "no-house"
        elif w_house == l_house:
            outcome["reason"] = "same-house"
        else:
            slots = REWARDED_PER_DAY - self.rewarded_today(winner.id, now)
            if slots <= 0:
                outcome["reason"] = "daily-cap"
            else:
                wins_paid = 1 + (1 if rivals and slots >= 2 else 0)
                base = wins_paid * REWARD_POINTS
                pts = base * (DUEL_NIGHT_MULTIPLIER if night else 1)
                why = f"Duel win over {loser.display_name}"
                if wins_paid > 1:
                    why += " (rival)"
                if night:
                    why += " (Duel Night)"
                self._award(store, w_house, pts, winner.id, why)
                self.state["rewarded"].setdefault(wid, []).extend([now] * wins_paid)
                outcome["awarded"] = pts

        # ------------------------------------------------ bounty on the loser
        bounty = self.state["bounties"].pop(lid, None)
        if bounty:
            paid = ""
            if store and w_house and w_house != l_house:
                self._award(store, w_house, BOUNTY_POINTS, winner.id,
                            f"Broke {loser.display_name}'s {bounty.get('streak', BOUNTY_AT)}-win streak")
                from cogs.store import HOUSES
                h = HOUSES[w_house]
                paid = f" +{BOUNTY_POINTS} to {h['emoji']} {h['name']}."
            notes.append(f"💰 **{winner.display_name}** broke **{loser.display_name}**'s "
                         f"{self.state['streaks'].get(lid, 0)}-win streak and claimed the bounty!{paid}")

        # ------------------------------------------------ streaks
        self.state["streaks"][lid] = 0
        streak = self.state["streaks"].get(wid, 0) + 1
        self.state["streaks"][wid] = streak
        if streak >= BOUNTY_AT and wid not in self.state["bounties"]:
            self.state["bounties"][wid] = {"since": now, "streak": streak}
            notes.append(f"🎯 **{winner.display_name}** has won {streak} in a row. "
                         f"**A bounty is on their head** - beat them for +{BOUNTY_POINTS} points.")
        elif wid in self.state["bounties"]:
            self.state["bounties"][wid]["streak"] = streak
            if streak % STREAK_EVERY == 0:
                notes.append(f"🔥 **{winner.display_name}** is on a {streak}-win streak. "
                             "The bounty still stands.")
        elif streak == STREAK_ANNOUNCE:
            notes.append(f"🔥 **{winner.display_name}** is on a {streak}-win streak.")

        # ------------------------------------------------ Circle reputation
        self.state["rep"][wid] = self.state["rep"].get(wid, 0) + REP_WIN
        self.state["rep"][lid] = self.state["rep"].get(lid, 0) + REP_LOSS
        for member, uid in ((winner, wid), (loser, lid)):
            if self.state["rep"][uid] >= REP_GIFT and not self.state["gifted"].get(uid):
                self.state["gifted"][uid] = True
                house = store.member_house(member) if store else None
                if store and house:
                    self._award(store, house, GIFT_POINTS, member.id, "A gift from the Dueling Circle")
                    notes.append(f"🎁 The Dueling Circle has been watching **{member.display_name}**. "
                                 f"It sends a gift: +{GIFT_POINTS} to their house.")
                else:
                    notes.append(f"🎁 The Dueling Circle has been watching **{member.display_name}**, "
                                 "and it approves.")
        outcome["flourish"] = None
        if self.state["rep"][wid] >= REP_FLOURISH:
            outcome["flourish"] = FLOURISHES[winner.id % len(FLOURISHES)].format(name=winner.display_name)

        # ------------------------------------------------ ranks
        wins = self.record_of(winner.id)["w"]
        idx = rank_index(wins)
        if idx > self.state["rank_seen"].get(wid, 0):
            self.state["rank_seen"][wid] = idx
            if wins >= ANNOUNCE_RANKS_FROM:
                notes.append(f"⬆️ **{winner.display_name}** has risen to **{rank_for(wins)}** "
                             f"({wins} wins).")

        # ------------------------------------------------ signature spells
        for member in (winner, loser):
            uid = str(member.id)
            tally = self.state["spell_wins"].get(uid, {})
            if not tally:
                continue
            best = max(tally, key=lambda k: (tally[k], k))
            if tally[best] >= SIGNATURE_AT and self.state["signature"].get(uid) != best:
                self.state["signature"][uid] = best
                notes.append(f"✨ **{member.display_name}** has made {SPELLS[best]['name']} their "
                             f"signature spell. They are now **{member.display_name} "
                             f"{SIGNATURE_TITLES[best]}**.")

        # ------------------------------------------------ this week's tally
        week = self.state["week"]["wins"]
        entry = week.get(wid, [0, now])
        week[wid] = [entry[0] + 1, now]   # [wins, when they reached that count]

        self.save()
        return outcome

    def _duelist_label(self, member) -> str:
        wands = self.bot.get_cog("Wands")
        wand = wands.short_name(member.id) if wands else None
        label = f"**{member.display_name}**"
        sig = self.signature_of(member.id)
        if sig:
            label += f" {sig[1]}"
        if self.has_bounty(member.id):
            label += " 🎯"
        return label + (f" *({wand})*" if wand else "")

    # ------------------------------------------------- Duelist of the Week

    @tasks.loop(minutes=5)
    async def weekly(self):
        """Crowns last week's Duelist of the Week once the week turns over
        (Monday, midnight Central)."""
        try:
            self._roll_week(time.time())
            pending = self.state.pop("pending_week", None)
            if pending:
                self.save()
                await self._crown(pending)
            # a forgotten duel night switches itself off
            self.duel_night_on()
        except Exception:
            log.exception("Weekly duel check failed")

    @weekly.before_loop
    async def _before_weekly(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def pick_champion(wins: dict):
        """Most wins; a tie goes to whoever reached that count first."""
        if not wins:
            return None
        return min(wins.items(), key=lambda kv: (-kv[1][0], kv[1][1]))

    async def _crown(self, week: dict):
        best = self.pick_champion(week.get("wins", {}))
        if not best:
            return
        uid, (count, _) = int(best[0]), best[1]
        arena = os.getenv("DUEL_CHANNEL_ID", "")
        channel = self.bot.get_channel(int(arena)) if arena.isdigit() else None
        guild = channel.guild if channel else (self.bot.guilds[0] if self.bot.guilds else None)
        member = None
        if guild:
            member = guild.get_member(uid)
            if member is None:
                try:
                    member = await guild.fetch_member(uid)
                except discord.DiscordException:
                    member = None

        previous = self.state.get("champion")
        self.state["champion"] = {"user_id": uid, "week": week.get("key")}
        self.save()

        store = self.bot.get_cog("Store")
        house = store.member_house(member) if (store and member) else None
        paid = ""
        if store and house:
            self._award(store, house, WEEKLY_POINTS, uid, f"Duelist of the Week ({week.get('key')})")
            from cogs.store import HOUSES
            h = HOUSES[house]
            paid = f" +{WEEKLY_POINTS} to {h['emoji']} {h['name']}."

        # the role: taken from last week's champion, given to this one
        role_note = ""
        role = discord.utils.get(guild.roles, name=CHAMPION_ROLE_NAME) if guild else None
        if role:
            try:
                for holder in list(role.members):
                    if holder.id != uid:
                        await holder.remove_roles(role, reason="New Duelist of the Week")
                if member and role not in member.roles:
                    await member.add_roles(role, reason="Duelist of the Week")
            except discord.DiscordException:
                log.exception("Could not hand over the %s role", CHAMPION_ROLE_NAME)
                role_note = ""
        elif guild:
            log.warning("No '%s' role found - champion announced without a role", CHAMPION_ROLE_NAME)

        name = member.display_name if member else f"<@{uid}>"
        if channel:
            try:
                await channel.send(embed=discord.Embed(
                    title="🏆 Duelist of the Week",
                    description=(f"**{name}** won the most duels last week - **{count}** of them - "
                                 f"and is **{CHAMPION_ROLE_NAME}** for the week ahead.{paid}{role_note}\n\n"
                                 "The tally starts fresh now. Go take it from them."),
                    color=0xD4A017,
                ))
            except discord.DiscordException:
                log.exception("Could not announce the Duelist of the Week")

    # ------------------------------------------------------------ commands

    @app_commands.command(name="duel", description="Challenge someone to a wizard's duel.")
    @app_commands.describe(opponent="Who you're challenging")
    async def duel(self, interaction: discord.Interaction, opponent: discord.Member):
        me = interaction.user
        arena = os.getenv("DUEL_CHANNEL_ID", "")
        if arena.isdigit() and interaction.channel_id != int(arena):
            await interaction.response.send_message(
                f"⚔️ Duels are fought in <#{arena}>.", ephemeral=True
            )
            return
        if opponent.id == me.id:
            await interaction.response.send_message("You can't duel yourself.", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message(
                "The ghosts don't duel. They've seen enough of that.", ephemeral=True
            )
            return
        if me.id in self.busy:
            await interaction.response.send_message("You're already in a duel.", ephemeral=True)
            return
        if opponent.id in self.busy:
            await interaction.response.send_message(
                f"{opponent.display_name} is already in a duel.", ephemeral=True
            )
            return

        duel = Duel(self, interaction.channel, me, opponent)
        self.busy.update({me.id, opponent.id})
        await interaction.response.send_message(
            content=opponent.mention,
            embed=duel.challenge_embed(),
            view=AcceptView(duel),
        )
        duel.message = await interaction.original_response()
        duel.start_accept_timer()

    @app_commands.command(name="duelrecord", description="Duel wins, rank, streak, and today's duel points.")
    @app_commands.describe(member="Whose record (leave blank for your own)")
    async def duelrecord(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        wins = self.wins_of(member.id)
        left = max(0, REWARDED_PER_DAY - self.rewarded_today(member.id))

        title = member.display_name
        sig = self.signature_of(member.id)
        if sig:
            title += f" {sig[1]}"
        embed = discord.Embed(title=f"{title} — duelling record", color=0x6C5CE7)
        losses = self.record_of(member.id).get("l", 0)
        embed.add_field(name="Rank", value=rank_for(wins))
        embed.add_field(name="Wins", value=str(wins))
        embed.add_field(name="Losses", value=str(losses))
        streak = self.streak_of(member.id)
        embed.add_field(name="Streak", value=(f"🔥 {streak}" if streak >= 2 else str(streak))
                        + (" 🎯 bounty" if self.has_bounty(member.id) else ""))
        this_week = self.state["week"]["wins"].get(str(member.id), [0])[0] \
            if self.state["week"].get("key") == week_key() else 0
        embed.add_field(name="This week", value=f"{this_week} wins")
        rivals = self.rivals_of(member.id)
        if rivals:
            names = []
            for rid in rivals[:3]:
                m = interaction.guild.get_member(rid) if interaction.guild else None
                names.append(m.display_name if m else f"<@{rid}>")
            embed.add_field(name="Rivals", value=", ".join(names))
        if sig:
            embed.add_field(name="Signature spell",
                            value=f"{SPELLS[sig[0]]['emoji']} {SPELLS[sig[0]]['name']}")
        if self.is_champion(member.id):
            embed.add_field(name="Honours", value=f"🏆 {CHAMPION_ROLE_NAME}", inline=False)
        wands = self.bot.get_cog("Wands")
        wand = wands.wand_of(member.id) if wands else None
        if wand:
            glow = " ✨ *(it glows)*" if self.rep_of(member.id) >= REP_GLOW else ""
            embed.add_field(name="Wand", value=f"{wand['wood']}, {wand['core'].lower()}{glow}",
                            inline=False)
        footer = f"{left} of {REWARDED_PER_DAY} duel points still available today"
        if self.duel_night_on():
            footer += " • ⚔️ Duel Night: wins count double"
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="houseduels", description="Each house's overall duelling record.")
    async def houseduels(self, interaction: discord.Interaction):
        from cogs.store import HOUSES
        store = self.bot.get_cog("Store")
        guild = interaction.guild

        totals = {key: {"w": 0, "l": 0} for key in HOUSES}
        for uid, rec in self.state.get("records", {}).items():
            member = guild.get_member(int(uid)) if guild else None
            if member is None:
                continue
            house = store.member_house(member) if store else None
            if house not in totals:
                continue
            totals[house]["w"] += rec.get("w", 0)
            totals[house]["l"] += rec.get("l", 0)

        ranked = sorted(totals.items(), key=lambda kv: (-kv[1]["w"], kv[1]["l"]))

        lines = []
        for i, (house_key, rec) in enumerate(ranked, start=1):
            meta = HOUSES[house_key]
            w, l = rec["w"], rec["l"]
            lines.append(f"**{i}. {meta['emoji']} {meta['name']}** — {w}-{l}")

        embed = discord.Embed(
            title="⚔️ House Duelling Record",
            description="\n".join(lines) if lines else "No duels have been fought yet.",
            color=HOUSES[ranked[0][0]]["color"] if ranked and (ranked[0][1]["w"] or ranked[0][1]["l"]) else 0x6C5CE7,
        )
        embed.set_footer(text="Ranked by total wins • same-house duels count too")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="duelnight", description="(staff) Start or end a House Duel Night - duel wins count double.")
    @app_commands.describe(action="Start or end it")
    @app_commands.choices(action=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="end", value="end"),
    ])
    async def duelnight(self, interaction: discord.Interaction, action: app_commands.Choice[str]):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if action.value == "start":
            self.state["duel_night"] = {"by": interaction.user.id, "at": time.time()}
            self.save()
            await interaction.response.send_message(embed=discord.Embed(
                title="⚔️ House Duel Night",
                description=(f"Every duel win counts **double** for your house tonight. "
                             f"The daily limit of {REWARDED_PER_DAY} paid wins still stands. "
                             f"Ends when staff call it, or after {DUEL_NIGHT_MAX_HOURS} hours."),
                color=0xB8434F))
        else:
            was_on = self.duel_night_on()
            self.state["duel_night"] = None
            self.save()
            await interaction.response.send_message(
                "⚔️ Duel Night is over. Wins are back to normal." if was_on
                else "There's no Duel Night running.", ephemeral=not was_on)


class Duel:
    """One duel, held in memory. Duels last a couple of minutes, so they
    aren't saved - a redeploy mid-duel simply ends it."""

    def __init__(self, cog: Duels, channel, challenger, opponent):
        self.cog = cog
        self.channel = channel
        self.a = challenger
        self.b = opponent
        self.message = None
        self.score = {challenger.id: 0, opponent.id: 0}
        self.picks: dict[int, str] = {}
        self.round = 0
        self.history: list[str] = []
        self.state = "pending"     # pending -> active -> done
        self.lock = asyncio.Lock()
        self._timer = None

    # -------------------------------------------------------------- display

    def challenge_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="A duel is called",
            description=f"{self.cog._duelist_label(self.a)} challenges "
                        f"{self.cog._duelist_label(self.b)}.\n\n"
                        "Best of three. Spells are chosen in secret and revealed together.",
            color=0xB8434F,
        )
        embed.set_footer(text=f"{self.b.display_name} has {ACCEPT_TIMEOUT // 60} minutes to answer")
        return embed

    def board_embed(self, footer: str = None) -> discord.Embed:
        a_s, b_s = self.score[self.a.id], self.score[self.b.id]
        lines = [f"{self.cog._duelist_label(self.a)} **{a_s}** — "
                 f"**{b_s}** {self.cog._duelist_label(self.b)}"]
        if self.history:
            lines.append("")
            lines.extend(self.history[-4:])
        if self.state == "active":
            waiting = [m.display_name for m in (self.a, self.b) if m.id not in self.picks]
            lines.append("")
            lines.append(f"**Round {self.round}** — "
                         + ("waiting on " + " and ".join(waiting) if waiting else "revealing…"))
        embed = discord.Embed(title="The duel", description="\n".join(lines), color=0xB8434F)
        embed.set_footer(text=footer or f"Press Cast to choose your spell • "
                                        f"{ROUND_TIMEOUT}s per round")
        return embed

    async def _update(self, view=None, footer=None):
        if self.message is None:
            return
        try:
            await self.message.edit(content=None, embed=self.board_embed(footer), view=view)
        except discord.DiscordException:
            log.exception("Could not update the duel message.")

    # --------------------------------------------------------------- timing

    def _arm(self, coro):
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.create_task(coro)

    def start_accept_timer(self):
        self._arm(self._accept_timeout())

    async def _accept_timeout(self):
        await asyncio.sleep(ACCEPT_TIMEOUT)
        async with self.lock:
            if self.state != "pending":
                return
            self.state = "done"
            self.cog.busy.difference_update({self.a.id, self.b.id})
        try:
            await self.message.edit(
                content=None,
                embed=discord.Embed(description=f"{self.b.display_name} never answered. "
                                                "The duel is off.", color=0x7A7A7A),
                view=None,
            )
        except discord.DiscordException:
            pass

    async def _round_timeout(self, round_no: int):
        await asyncio.sleep(ROUND_TIMEOUT)
        async with self.lock:
            if self.state != "active" or self.round != round_no:
                return
            cast = [m for m in (self.a, self.b) if m.id in self.picks]
            if len(cast) == 1:
                winner = cast[0]
                loser = self.b if winner is self.a else self.a
                self.history.append(f"*{loser.display_name} froze and never cast.*")
                await self._finish(winner, loser, forfeit=True)
            else:
                self.state = "done"
                self.cog.busy.difference_update({self.a.id, self.b.id})
                await self._update(view=None, footer="Neither duelist cast. The duel fizzles out.")

    # ------------------------------------------------------------ the rounds

    async def _next_round(self):
        self.round += 1
        self.picks = {}
        if self.round > MAX_ROUNDS:
            self.state = "done"
            self.cog.busy.difference_update({self.a.id, self.b.id})
            await self._update(view=None, footer="Too evenly matched — declared a draw.")
            return
        await self._update(view=CastView(self))
        self._arm(self._round_timeout(self.round))

    async def cast(self, member, spell: str, round_no: int = None) -> str:
        """Lock in a spell. Returns a message for the caster."""
        async with self.lock:
            if self.state != "active":
                return "This duel is over."
            if round_no is not None and round_no != self.round:
                return "That round is already over - press Cast again for this one."
            if member.id not in self.score:
                return "You're not in this duel."
            if member.id in self.picks:
                return f"You've already cast {SPELLS[self.picks[member.id]]['name']} this round."
            self.picks[member.id] = spell

            if len(self.picks) < 2:
                await self._update(view=CastView(self))
                return f"You cast **{SPELLS[spell]['name']}**. Waiting on your opponent…"

            a_spell, b_spell = self.picks[self.a.id], self.picks[self.b.id]
            disp_a, disp_b = a_spell, b_spell
            favored = None
            if FAVORED_USER_ID in (self.a.id, self.b.id):
                favored = self.a if FAVORED_USER_ID == self.a.id else self.b
            if favored and random.random() < FAVORED_ROUND_BIAS:
                # Don't just declare a win with a line that doesn't match any
                # real matchup - that's the kind of thing an attentive player
                # notices after enough duels. Instead, quietly credit the
                # favored side with whichever of the two spells that legitimately
                # beats the opponent's actual cast they'd need to have thrown.
                # resolve() then produces a completely ordinary, real matchup
                # line. Only the favored player could ever notice their shown
                # spell doesn't match what they clicked (their own private
                # "You cast X" confirmation still reflects the real pick) -
                # nobody else sees anything but a normal result.
                opp_spell = b_spell if favored is self.a else a_spell
                counters = [w for (w, l) in BEATS if l == opp_spell]
                winning_spell = random.choice(counters)
                if favored is self.a:
                    disp_a = winning_spell
                else:
                    disp_b = winning_spell
            result, line = resolve(disp_a, disp_b)
            reveal = (f"R{self.round}: {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                      f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {line}")
            if result == 1:
                self.score[self.a.id] += 1
                self.cog.note_round_win(self.a.id, disp_a)
            elif result == 2:
                self.score[self.b.id] += 1
                self.cog.note_round_win(self.b.id, disp_b)
            self.history.append(reveal)

            if self.score[self.a.id] >= ROUNDS_TO_WIN:
                await self._finish(self.a, self.b)
            elif self.score[self.b.id] >= ROUNDS_TO_WIN:
                await self._finish(self.b, self.a)
            else:
                await self._next_round()
            return f"You cast **{SPELLS[spell]['name']}**."

    async def _finish(self, winner, loser, forfeit: bool = False):
        self.state = "done"
        if self._timer:
            self._timer.cancel()
        self.cog.busy.difference_update({self.a.id, self.b.id})
        outcome = self.cog.settle(winner, loser)

        from cogs.store import HOUSES
        if outcome.get("flourish"):
            self.history.append(f"*{outcome['flourish']}*")
        if outcome["awarded"]:
            h = HOUSES[outcome["house"]]
            tail = f"+{outcome['awarded']} to {h['emoji']} {h['name']}"
            if outcome.get("night"):
                tail += " (Duel Night!)"
        elif outcome["reason"] == "same-house":
            tail = "Housemates — no points change hands"
        elif outcome["reason"] == "daily-cap":
            tail = f"{winner.display_name} has taken today's {REWARDED_PER_DAY} duel points already"
        else:
            tail = "No house to credit"
        verb = "wins by forfeit" if forfeit else "wins the duel"
        await self._update(view=None, footer=f"{winner.display_name} {verb} • {tail}")
        notes = outcome.get("notes") or []
        if notes and self.channel is not None:
            try:
                await self.channel.send("\n".join(notes))
            except discord.DiscordException:
                log.exception("Could not post duel announcements.")


# ------------------------------------------------------------------ the views

class AcceptView(discord.ui.View):
    def __init__(self, duel: Duel):
        super().__init__(timeout=ACCEPT_TIMEOUT + 5)
        self.duel = duel

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.duel
        if interaction.user.id != d.b.id:
            await interaction.response.send_message("This challenge isn't yours to answer.",
                                                    ephemeral=True)
            return
        async with d.lock:
            if d.state != "pending":
                await interaction.response.send_message("Too late for that.", ephemeral=True)
                return
            d.state = "active"
        await interaction.response.defer()
        d.round = 0
        await d._next_round()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.duel
        if interaction.user.id not in (d.a.id, d.b.id):
            await interaction.response.send_message("This isn't your duel.", ephemeral=True)
            return
        async with d.lock:
            if d.state != "pending":
                await interaction.response.send_message("Too late for that.", ephemeral=True)
                return
            d.state = "done"
            d.cog.busy.difference_update({d.a.id, d.b.id})
            if d._timer:
                d._timer.cancel()
        who = "withdraws" if interaction.user.id == d.a.id else "declines"
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(description=f"{interaction.user.display_name} {who}. "
                                            "No duel today.", color=0x7A7A7A),
            view=None,
        )


class CastView(discord.ui.View):
    def __init__(self, duel: Duel):
        super().__init__(timeout=ROUND_TIMEOUT + 5)
        self.duel = duel

    @discord.ui.button(label="Cast", style=discord.ButtonStyle.primary, emoji="\U0001FA84")
    async def cast(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.duel
        if interaction.user.id not in d.score:
            await interaction.response.send_message("You're watching, not duelling.",
                                                    ephemeral=True)
            return
        if interaction.user.id in d.picks:
            await interaction.response.send_message("You've already cast this round.",
                                                    ephemeral=True)
            return
        await interaction.response.send_message(
            f"Round {d.round} — choose your spell. Only you can see this.",
            view=SpellView(d),
            ephemeral=True,
        )


class SpellView(discord.ui.View):
    def __init__(self, duel: Duel):
        super().__init__(timeout=ROUND_TIMEOUT)
        self.duel = duel
        self.round_no = duel.round
        for key, spell in SPELLS.items():
            self.add_item(SpellButton(key, spell))


class SpellButton(discord.ui.Button):
    def __init__(self, key: str, spell: dict):
        super().__init__(label=spell["name"], emoji=spell["emoji"],
                         style=discord.ButtonStyle.secondary)
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        # Discord allows 3 seconds to answer a click; resolving a round also
        # edits the public message, so acknowledge first and report after.
        await interaction.response.defer()
        reply = await self.view.duel.cast(interaction.user, self.key, self.view.round_no)
        try:
            await interaction.edit_original_response(content=reply, view=None)
        except discord.DiscordException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Duels(bot))
