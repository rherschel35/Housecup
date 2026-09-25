"""
Quidditch - team pickup matches in their own channel.

    /quidditch scramble size:<2|4>                         - casual 2v2/4v4, any houses, join either side
    /quidditch housematch house1:<H> house2:<H> size:<2|4>  - house-vs-house, only members of that house
                                                              can join that side
    /quidditchstats                                         - your record and title

A match is 5 rounds. Each round every joined player privately picks one
action via button: Attack, Block, or Chase the Snitch. Attacks beat blocks
one-for-one to score a 10-point goal for that side; anyone chasing the
Snitch has a small chance each round to catch it, which ends the match
immediately and adds a bonus to their side's score. After 5 rounds (or an
early Snitch catch) whichever side has more points wins.

Rewards:
    - Scramble win: updates your personal Quidditch record only. No
      house points, no loot - mixed-house teams have no single house to
      credit.
    - House match win: updates your personal record AND gives your house
      3 points per winning player - but only your first 3 point-earning
      house-match wins each day count. You can keep playing past that,
      it just stops adding house points for the day (your win record and
      title still climb).
    - Titles (shown via /quidditchstats): a ladder based on total house
      match wins - 5 -> Rising Chaser, 15 -> Storm Breaker, 30 -> Pitch
      Veteran, 50 -> Legend of the Pitch.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import random
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.quidditch")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "quidditch_state.json"

# Quidditch only runs in this one channel.
QUIDDITCH_CHANNEL_ID = 1553071961641328720

HOUSES = ["Caldrin", "Thornmere", "Veyren", "Vashara", "Moonveil"]

ROUNDS = 5
GOAL_POINTS = 10
SNITCH_CHANCE_PER_CHASER = 0.12   # per chaser, per round
SNITCH_BONUS = 30                 # added to the catcher's side's score

HOUSE_POINTS_PER_WIN = 3
DAILY_HOUSE_WIN_CAP = 3   # a player's own first N point-earning house-match wins each day

# (min house-match wins, title)
TITLE_THRESHOLDS = [
    (50, "Legend of the Pitch"),
    (30, "Pitch Veteran"),
    (15, "Storm Breaker"),
    (5, "Rising Chaser"),
]


def today_str() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def blank_player() -> dict:
    return {
        "scramble_w": 0, "scramble_l": 0,
        "house_w": 0, "house_l": 0,
        "daily": {"date": today_str(), "count": 0},
    }


def title_for(house_wins: int) -> Optional[str]:
    for threshold, name in TITLE_THRESHOLDS:
        if house_wins >= threshold:
            return name
    return None


class Signup:
    """Waiting-for-players state before a match actually starts."""

    def __init__(self, is_house: bool, size: int, house_a: Optional[str] = None, house_b: Optional[str] = None):
        self.is_house = is_house
        self.size = size
        self.house_a = house_a
        self.house_b = house_b
        self.team_a: list[int] = []
        self.team_b: list[int] = []

    def label_a(self) -> str:
        return f"Join {self.house_a}" if self.is_house else "Join Team A"

    def label_b(self) -> str:
        return f"Join {self.house_b}" if self.is_house else "Join Team B"

    def embed(self) -> discord.Embed:
        title = f"🏟️ {self.house_a} vs {self.house_b}" if self.is_house else f"🏟️ Quidditch Scramble ({self.size}v{self.size})"
        e = discord.Embed(title=title, description="Waiting for players to join both sides.", color=0x6C5CE7)
        side_a_name = self.house_a if self.is_house else "Team A"
        side_b_name = self.house_b if self.is_house else "Team B"
        e.add_field(name=f"{side_a_name} ({len(self.team_a)}/{self.size})",
                    value="\n".join(f"<@{u}>" for u in self.team_a) or "—", inline=True)
        e.add_field(name=f"{side_b_name} ({len(self.team_b)}/{self.size})",
                    value="\n".join(f"<@{u}>" for u in self.team_b) or "—", inline=True)
        return e


class Match:
    """An in-progress match. Purely in-memory, like duels/Descent fights."""

    def __init__(self, signup: Signup):
        self.is_house = signup.is_house
        self.size = signup.size
        self.house_a = signup.house_a
        self.house_b = signup.house_b
        self.team_a = list(signup.team_a)
        self.team_b = list(signup.team_b)
        self.round = 0
        self.score_a = 0
        self.score_b = 0
        self.actions: dict[int, str] = {}
        self.log: list[str] = []
        self.finished = False
        self.winner: Optional[str] = None  # "a", "b", or None for a tie

    def all_players(self) -> list[int]:
        return self.team_a + self.team_b

    def side_name(self, side: str) -> str:
        if side == "a":
            return self.house_a if self.is_house else "Team A"
        return self.house_b if self.is_house else "Team B"

    def embed(self) -> discord.Embed:
        title = f"🏟️ {self.side_name('a')} vs {self.side_name('b')}"
        waiting = [u for u in self.all_players() if u not in self.actions]
        if self.finished:
            desc = "**Match over.**"
        else:
            desc = f"Round {self.round + 1}/{ROUNDS}"
            if waiting:
                desc += "\nWaiting on: " + ", ".join(f"<@{u}>" for u in waiting)
        e = discord.Embed(title=title, description=desc,
                          color=0x2ECC71 if self.finished else 0x6C5CE7)
        e.add_field(name=f"{self.side_name('a')}", value=f"**{self.score_a}** pts", inline=True)
        e.add_field(name=f"{self.side_name('b')}", value=f"**{self.score_b}** pts", inline=True)
        if self.log:
            e.add_field(name="Last round", value="\n".join(self.log[-4:]), inline=False)
        return e


class JoinButton(discord.ui.Button):
    def __init__(self, side: str, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.success if side == "a" else discord.ButtonStyle.primary)
        self.side = side

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.join_signup(interaction, self.view, self.side)


class SignupView(discord.ui.View):
    def __init__(self, cog: "Quidditch", signup: Signup):
        super().__init__(timeout=1800)
        self.cog = cog
        self.signup = signup
        self.add_item(JoinButton("a", signup.label_a()))
        self.add_item(JoinButton("b", signup.label_b()))


class ActionButton(discord.ui.Button):
    def __init__(self, action: str, label: str, emoji: str, style: discord.ButtonStyle):
        super().__init__(label=label, emoji=emoji, style=style)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.submit_action(interaction, self.view, self.action)


class MatchView(discord.ui.View):
    def __init__(self, cog: "Quidditch", match: Match):
        super().__init__(timeout=1800)
        self.cog = cog
        self.match = match
        self.add_item(ActionButton("attack", "Attack", "🧹", discord.ButtonStyle.danger))
        self.add_item(ActionButton("block", "Block", "🛡️", discord.ButtonStyle.primary))
        self.add_item(ActionButton("chase", "Chase Snitch", "✨", discord.ButtonStyle.success))


class Quidditch(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self.group = app_commands.Group(name="quidditch", description="Play a Quidditch match.")
        self._register_commands()
        bot.tree.add_command(self.group)

    def cog_unload(self):
        self.bot.tree.remove_command(self.group.name, type=self.group.type)

    def _load(self) -> dict:
        if STATE_PATH.exists():
            try:
                return json.loads(STATE_PATH.read_text())
            except Exception:
                log.exception("Failed to load quidditch state")
        return {"players": {}}

    def save(self):
        STATE_PATH.write_text(json.dumps(self.state, indent=2))

    def record(self, user_id: int) -> dict:
        rec = self.state["players"].setdefault(str(user_id), blank_player())
        if rec["daily"]["date"] != today_str():
            rec["daily"] = {"date": today_str(), "count": 0}
        return rec

    def _in_channel(self, interaction: discord.Interaction) -> bool:
        return interaction.channel_id == QUIDDITCH_CHANNEL_ID

    # -------------------------------------------------------------- setup

    def _register_commands(self):
        size_choices = [app_commands.Choice(name="2v2", value=2), app_commands.Choice(name="4v4", value=4)]
        house_choices = [app_commands.Choice(name=h, value=h) for h in HOUSES]

        @self.group.command(name="scramble", description="Start a casual pickup match - any houses, either side.")
        @app_commands.describe(size="Players per side")
        @app_commands.choices(size=size_choices)
        async def scramble(interaction: discord.Interaction, size: Optional[app_commands.Choice[int]] = None):
            await self.start_signup(interaction, is_house=False, size=size.value if size else 2)

        @self.group.command(name="housematch", description="Start a house-vs-house match.")
        @app_commands.describe(house1="First house", house2="Second house", size="Players per side")
        @app_commands.choices(house1=house_choices, house2=house_choices, size=size_choices)
        async def housematch(interaction: discord.Interaction, house1: app_commands.Choice[str],
                             house2: app_commands.Choice[str], size: Optional[app_commands.Choice[int]] = None):
            if house1.value == house2.value:
                await interaction.response.send_message("Pick two different houses.", ephemeral=True)
                return
            await self.start_signup(interaction, is_house=True, size=size.value if size else 2,
                                    house_a=house1.value, house_b=house2.value)

    # ------------------------------------------------------------ signup

    async def start_signup(self, interaction: discord.Interaction, is_house: bool, size: int,
                           house_a: Optional[str] = None, house_b: Optional[str] = None):
        if not self._in_channel(interaction):
            await interaction.response.send_message(
                f"Quidditch can only be played in <#{QUIDDITCH_CHANNEL_ID}>.", ephemeral=True)
            return
        signup = Signup(is_house, size, house_a, house_b)
        await interaction.response.send_message(embed=signup.embed(), view=SignupView(self, signup))

    async def join_signup(self, interaction: discord.Interaction, view: SignupView, side: str):
        signup = view.signup
        uid = interaction.user.id
        if uid in signup.team_a or uid in signup.team_b:
            await interaction.response.send_message("You're already signed up for this match.", ephemeral=True)
            return
        target = signup.team_a if side == "a" else signup.team_b
        if len(target) >= signup.size:
            await interaction.response.send_message("That side is already full.", ephemeral=True)
            return
        if signup.is_house:
            store = self.bot.get_cog("Store")
            required_house = signup.house_a if side == "a" else signup.house_b
            member_house = store.member_house(interaction.user) if store else None
            if member_house != required_house:
                await interaction.response.send_message(
                    f"Only members of House {required_house} can join this side.", ephemeral=True)
                return
        target.append(uid)

        if len(signup.team_a) >= signup.size and len(signup.team_b) >= signup.size:
            match = Match(signup)
            await interaction.response.edit_message(embed=match.embed(), view=MatchView(self, match))
        else:
            await interaction.response.edit_message(embed=signup.embed(), view=view)

    # ------------------------------------------------------------ combat

    async def submit_action(self, interaction: discord.Interaction, view: MatchView, action: str):
        match = view.match
        uid = interaction.user.id
        if uid not in match.all_players():
            await interaction.response.send_message("You're not in this match.", ephemeral=True)
            return
        if uid in match.actions:
            await interaction.response.send_message("You've already chosen this round.", ephemeral=True)
            return
        match.actions[uid] = action

        if len(match.actions) < len(match.all_players()):
            await interaction.response.edit_message(embed=match.embed(), view=view)
            return

        self._resolve_round(match)

        if match.finished:
            await self.finish_match(interaction, match)
        else:
            match.actions = {}
            await interaction.response.edit_message(embed=match.embed(), view=view)

    def _resolve_round(self, match: Match):
        attackers_a = [u for u in match.team_a if match.actions.get(u) == "attack"]
        attackers_b = [u for u in match.team_b if match.actions.get(u) == "attack"]
        blockers_a = [u for u in match.team_a if match.actions.get(u) == "block"]
        blockers_b = [u for u in match.team_b if match.actions.get(u) == "block"]
        chasers = [u for u in match.all_players() if match.actions.get(u) == "chase"]

        goals_a = max(0, len(attackers_a) - len(blockers_b)) * GOAL_POINTS
        goals_b = max(0, len(attackers_b) - len(blockers_a)) * GOAL_POINTS
        match.score_a += goals_a
        match.score_b += goals_b

        line = f"**Round {match.round + 1}:** {match.side_name('a')} +{goals_a} · {match.side_name('b')} +{goals_b}"
        match.log.append(line)

        catcher_side = None
        for uid in chasers:
            if random.random() < SNITCH_CHANCE_PER_CHASER:
                catcher_side = "a" if uid in match.team_a else "b"
                break

        match.round += 1

        if catcher_side:
            if catcher_side == "a":
                match.score_a += SNITCH_BONUS
            else:
                match.score_b += SNITCH_BONUS
            match.log.append(f"✨ {match.side_name(catcher_side)} caught the Snitch! +{SNITCH_BONUS} bonus")
            match.finished = True
        elif match.round >= ROUNDS:
            match.finished = True

        if match.finished:
            if match.score_a > match.score_b:
                match.winner = "a"
            elif match.score_b > match.score_a:
                match.winner = "b"
            else:
                match.winner = None

    async def finish_match(self, interaction: discord.Interaction, match: Match):
        winning_team = None
        losing_team = None
        if match.winner == "a":
            winning_team, losing_team = match.team_a, match.team_b
        elif match.winner == "b":
            winning_team, losing_team = match.team_b, match.team_a

        awarded: list[int] = []
        capped: list[int] = []

        if not match.is_house:
            for uid in winning_team or []:
                rec = self.record(uid)
                rec["scramble_w"] += 1
            for uid in losing_team or []:
                rec = self.record(uid)
                rec["scramble_l"] += 1
        else:
            store = self.bot.get_cog("Store")
            winning_house = match.house_a if match.winner == "a" else (match.house_b if match.winner == "b" else None)
            for uid in winning_team or []:
                rec = self.record(uid)
                rec["house_w"] += 1
                if winning_house and rec["daily"]["count"] < DAILY_HOUSE_WIN_CAP:
                    rec["daily"]["count"] += 1
                    awarded.append(uid)
                    if store:
                        store.record(house=winning_house, delta=HOUSE_POINTS_PER_WIN,
                                     actor_id=self.bot.user.id if self.bot.user else 0,
                                     target_id=uid, reason="Quidditch house match win")
                else:
                    capped.append(uid)
            for uid in losing_team or []:
                rec = self.record(uid)
                rec["house_l"] += 1
        self.save()

        if match.winner is None:
            desc = f"**It's a tie!** {match.score_a} — {match.score_b}. No wins or points recorded."
        else:
            desc = f"🏆 **{match.side_name(match.winner)} wins!** {match.score_a} — {match.score_b}"
            if match.is_house:
                if awarded:
                    desc += "\n+3 house points: " + ", ".join(f"<@{u}>" for u in awarded)
                if capped:
                    desc += "\nDaily cap reached (no points, win still recorded): " + ", ".join(f"<@{u}>" for u in capped)

        embed = discord.Embed(title="Match Result", description=desc, color=0x2ECC71)
        await interaction.response.edit_message(embed=embed, view=None)

    # ----------------------------------------------------------- commands

    @app_commands.command(name="quidditchstats", description="Your Quidditch record and title.")
    async def quidditchstats(self, interaction: discord.Interaction):
        if not self._in_channel(interaction):
            await interaction.response.send_message(
                f"Quidditch can only be played in <#{QUIDDITCH_CHANNEL_ID}>.", ephemeral=True)
            return
        rec = self.record(interaction.user.id)
        title = title_for(rec["house_w"])
        desc = [f"Scramble: **{rec['scramble_w']}W - {rec['scramble_l']}L**",
                f"House matches: **{rec['house_w']}W - {rec['house_l']}L**",
                f"Point-earning wins today: {rec['daily']['count']}/{DAILY_HOUSE_WIN_CAP}"]
        if title:
            desc.append(f"🏅 Title: **{title}**")
        embed = discord.Embed(title=f"{interaction.user.display_name}'s Quidditch Record",
                              description="\n".join(desc), color=0x6C5CE7)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Quidditch(bot))
