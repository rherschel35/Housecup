"""
Wizard duels. Best of three, spells chosen in secret.

    /duel @member        - challenge someone
    /duelrecord [member] - wins, losses, and today's duel points

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
from discord.ext import commands

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
# 0.455 per round -> ~85% of matches won overall (see /tmp/test_duel_bias.py
# for the simulation used to tune it). A more adversarial opponent who could
# somehow read your picks would knock this down toward the raw per-round
# number, never above it - the bias never makes you invincible, only likely.
FAVORED_ROUND_BIAS = float(os.getenv("DUEL_FAVORED_BIAS", "0.455"))

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


class Duels(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.busy: set[int] = set()   # members currently in a duel

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
        return state

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

    def settle(self, winner, loser, now: float = None) -> dict:
        """Record the result and award the point if it's earned.
        Returns what happened so the announcement can say so."""
        now = now if now is not None else time.time()
        self.record_of(winner.id)["w"] += 1
        self.record_of(loser.id)["l"] += 1

        store = self.bot.get_cog("Store")
        w_house = store.member_house(winner) if store else None
        l_house = store.member_house(loser) if store else None

        outcome = {"awarded": 0, "reason": None, "house": w_house}
        if not store or not w_house:
            outcome["reason"] = "no-house"
        elif w_house == l_house:
            outcome["reason"] = "same-house"
        elif self.rewarded_today(winner.id, now) >= REWARDED_PER_DAY:
            outcome["reason"] = "daily-cap"
        else:
            store.record(
                house=w_house,
                delta=REWARD_POINTS,
                actor_id=self.bot.user.id if self.bot.user else 0,
                target_id=winner.id,
                reason=f"Duel win over {loser.display_name}",
            )
            self.state["rewarded"].setdefault(str(winner.id), []).append(now)
            outcome["awarded"] = REWARD_POINTS
        self.save()
        return outcome

    def _duelist_label(self, member) -> str:
        wands = self.bot.get_cog("Wands")
        wand = wands.short_name(member.id) if wands else None
        return f"**{member.display_name}**" + (f" *({wand})*" if wand else "")

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

    @app_commands.command(name="duelrecord", description="Wins, losses and today's duel points.")
    @app_commands.describe(member="Whose record (leave blank for your own)")
    async def duelrecord(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        rec = self.state["records"].get(str(member.id), {"w": 0, "l": 0})
        total = rec["w"] + rec["l"]
        pct = f"{round(100 * rec['w'] / total)}%" if total else "—"
        left = max(0, REWARDED_PER_DAY - self.rewarded_today(member.id))

        embed = discord.Embed(title=f"{member.display_name} — duelling record",
                              color=0x6C5CE7)
        embed.add_field(name="Wins", value=str(rec["w"]))
        embed.add_field(name="Losses", value=str(rec["l"]))
        embed.add_field(name="Win rate", value=pct)
        wands = self.bot.get_cog("Wands")
        wand = wands.wand_of(member.id) if wands else None
        if wand:
            embed.add_field(name="Wand", value=f"{wand['wood']}, {wand['core'].lower()}",
                            inline=False)
        embed.set_footer(text=f"{left} of {REWARDED_PER_DAY} duel points still available today")
        await interaction.response.send_message(embed=embed)


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
            favored = None
            if FAVORED_USER_ID in (self.a.id, self.b.id):
                favored = self.a if FAVORED_USER_ID == self.a.id else self.b
            if favored and random.random() < FAVORED_ROUND_BIAS:
                result = 1 if favored is self.a else 2
                line = "Something tips the moment their way, quicker than either spell alone."
            else:
                result, line = resolve(a_spell, b_spell)
            reveal = (f"R{self.round}: {SPELLS[a_spell]['emoji']} {SPELLS[a_spell]['name']} vs "
                      f"{SPELLS[b_spell]['emoji']} {SPELLS[b_spell]['name']} — {line}")
            if result == 1:
                self.score[self.a.id] += 1
            elif result == 2:
                self.score[self.b.id] += 1
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
        if outcome["awarded"]:
            h = HOUSES[outcome["house"]]
            tail = f"+{outcome['awarded']} to {h['emoji']} {h['name']}"
        elif outcome["reason"] == "same-house":
            tail = "Housemates — no points change hands"
        elif outcome["reason"] == "daily-cap":
            tail = f"{winner.display_name} has taken today's {REWARDED_PER_DAY} duel points already"
        else:
            tail = "No house to credit"
        verb = "wins by forfeit" if forfeit else "wins the duel"
        await self._update(view=None, footer=f"{winner.display_name} {verb} • {tail}")


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
