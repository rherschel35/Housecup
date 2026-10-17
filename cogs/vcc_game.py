"""Velmora Curse & Crown — Discord challenge, private table links, rewards.

The duel itself is played on the web table. This cog deals the challenge,
hands each player a link that only they can use, and writes the result
back to the channel.

During a wager night the winner's house takes the stake from the loser's.
Any other time the winner draws a Chocolate Frog or a spell card, and the
Cup does not move.

Links and table tokens are never logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.store import HOUSES, house_display
from cogs.vcc.book import Book, public_base_url
from cogs.vcc.cards import CARDS
from cogs.vcc.channels import vcc_channel_allowed, vcc_channel_refusal
from cogs.vcc.scoring import (
    NIGHT_LENGTHS,
    WAGERS,
    collectible_kind,
    dropdown_amounts,
    pick_from,
    points_to_move,
    stake_decision,
)
from cogs.vcc.web import Hub, create_app, start_site

log = logging.getLogger("velmora.vcc")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
VCC_PATH = STATE_DIR / "vcc_state.json"

GOLD = 0xC6A15B


def _turn_timeout() -> float:
    raw = os.getenv("VCC_TURN_TIMEOUT_SECONDS", "240").strip()
    try:
        return max(30.0, float(raw))
    except ValueError:
        return 240.0


def _accept_timeout() -> float:
    raw = os.getenv("VCC_ACCEPT_TIMEOUT_SECONDS", "120").strip()
    try:
        return max(30.0, float(raw))
    except ValueError:
        return 120.0


def _house_name(key: str) -> str:
    meta = HOUSES.get(key)
    return meta["name"] if meta else key


_STAKE_LABEL = {0: "Free", 5: "5 pts", 10: "10 pts"}


def _stake_label(amount: int) -> str:
    return _STAKE_LABEL.get(int(amount), str(amount))


def _tally(wins: int, losses: int) -> str:
    win_word = "win" if wins == 1 else "wins"
    loss_word = "loss" if losses == 1 else "losses"
    return f"{wins} {win_word}, {losses} {loss_word}"


def _pair_balances(store, house_a: str, house_b: str) -> tuple[int, int] | None:
    """Season totals for both houses, or None when there is no Cup to stake."""
    if store is None or not store.season_active():
        return None
    totals = dict(store.house_totals("season"))
    return int(totals.get(house_a, 0)), int(totals.get(house_b, 0))


def _stake_sentence(mode: str, wager: int, house_a: str, house_b: str, balances) -> str:
    if mode != "wager":
        return (
            "No wager night is running. The winner draws a Chocolate Frog "
            "or a spell card. No house points move."
        )
    if int(wager) <= 0:
        return "Wager: **Free**. No house points move."
    covered = ""
    if balances is not None:
        covered = (
            f" {house_display(house_a)} has **{balances[0]}** this season, "
            f"{house_display(house_b)} has **{balances[1]}**."
        )
    return (
        f"Wager: **{int(wager)}** house points.{covered} "
        f"The winner's house takes {int(wager)} from the loser's house. "
        "Each house keeps the rest of what it earned."
    )


def _refusal(decision: str, wager: int, house_a: str, house_b: str, balances) -> str:
    if decision == "no-night":
        return (
            "Wager nights are closed. Pick **Free** — the winner draws "
            "a Chocolate Frog or a spell card, and no house points move."
        )
    if decision == "no-balance":
        return "No House Cup season is running, so there's nothing to stake. Pick **Free**."
    if decision == "short" and balances is not None:
        allowed = dropdown_amounts(night=True, balances=balances, balances_known=True)
        choices = ", ".join(_stake_label(amount) for amount in allowed)
        return (
            f"A **{int(wager)}**-point wager needs both houses to have at least "
            f"{int(wager)} this season. {house_display(house_a)} has **{balances[0]}**, "
            f"{house_display(house_b)} has **{balances[1]}**. Open stakes: {choices}."
        )
    return "Pick a stake from the list: Free, 5 pts, or 10 pts."


async def _complete_wager(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Free outside a wager night. During a night, only stakes both houses can cover."""
    cog = interaction.client.get_cog("Vcc")
    night = bool(cog and cog.book.wager_night_on())
    balances = None
    known = False
    if night and cog is not None:
        store = interaction.client.get_cog("Store")
        opponent = getattr(getattr(interaction, "namespace", None), "opponent", None)
        me = interaction.user
        if store is not None and not store.season_active():
            known = True
        elif (
            store is not None
            and isinstance(me, discord.Member)
            and isinstance(opponent, discord.Member)
        ):
            mine = store.member_house(me)
            theirs = store.member_house(opponent)
            if mine and theirs:
                balances = _pair_balances(store, mine, theirs)
                known = True
    amounts = dropdown_amounts(night=night, balances=balances, balances_known=known)
    needle = (current or "").strip().casefold()
    choices = []
    for amount in amounts:
        label = _stake_label(amount)
        if needle and needle not in label.casefold() and needle not in str(amount):
            continue
        choices.append(app_commands.Choice(name=label, value=amount))
    return choices


def _link(raw: str | None) -> str | None:
    base = public_base_url()
    if not base or not raw:
        return None
    return f"{base}/play/{raw}"


def _practice_link_embed(url: str | None) -> discord.Embed:
    embed = _link_embed(url, fresh=False)
    embed.title = "Practice table — Velmora Curse & Crown"
    if url:
        embed.description = (
            "Practice. Only you can see this hand. The Tutor plays an easy game. "
            "No house points and no cards.\n"
            f"{url}"
        )
        embed.set_footer(text="Practice. This link is yours, and it closes when the duel ends.")
    return embed


def _link_embed(url: str | None, *, fresh: bool) -> discord.Embed:
    if url:
        note = "Only you can see this hand."
        if fresh:
            note = "This replaces your previous link. The old one has stopped working."
        embed = discord.Embed(
            title="Your table — Velmora Curse & Crown",
            description=f"{note}\n{url}",
            color=GOLD,
        )
        embed.set_footer(text="The link is yours. It closes when the duel ends.")
        return embed
    return discord.Embed(
        title="Your table isn't published yet",
        description=(
            "The duel is ready, but this bot has no public address. "
            "Staff need to generate a Railway domain and set `VCC_PUBLIC_URL`. "
            "Press **My table** again once that's done."
        ),
        color=GOLD,
    )


class VccCommands(app_commands.Group):
    """Every /vcc subcommand. The channel check runs before the command body."""

    def __init__(self) -> None:
        super().__init__(
            name="vcc",
            description="Velmora Curse & Crown, a private spellcard duel.",
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if vcc_channel_allowed(interaction.channel):
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(vcc_channel_refusal(), ephemeral=True)
        return False

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        # A refused channel already got its ephemeral reply. Don't log that.
        if isinstance(error, app_commands.CheckFailure):
            return
        log.exception("Curse & Crown command failed: %s", error)


class Vcc(commands.Cog):
    """1v1 Curse & Crown. The cards are played on the web, not in chat."""

    vcc = VccCommands()

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.hub = Hub()
        self.book = Book(
            VCC_PATH,
            turn_timeout=_turn_timeout(),
            accept_timeout=_accept_timeout(),
        )
        self.runner = None
        self._watch: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self.bot.add_view(TableView())
        for challenge in self.book.challenges.values():
            self.bot.add_view(ChallengeView(challenge.id))
        # Import-time checks and the slash-budget script load every cog
        # without a token. Don't bind a port in that case.
        if not os.getenv("DISCORD_TOKEN"):
            log.info("Curse & Crown web table not started (DISCORD_TOKEN unset).")
            return
        app = create_app(self.book, self.hub, self.lock, on_end=self.settle, bot=self.bot)
        self.runner = await start_site(app)
        self._watch = asyncio.create_task(self.watch(), name="vcc-watch")

    async def cog_unload(self) -> None:
        if self._watch:
            self._watch.cancel()
        if self.runner is not None:
            await self.runner.cleanup()

    # --------------------------------------------------------------- discord

    @vcc.command(name="challenge", description="Challenge someone to Velmora Curse & Crown.")
    @app_commands.describe(
        opponent="Who you're challenging",
        wager="Stake during a wager night: Free, 5 pts, or 10 pts.",
    )
    @app_commands.autocomplete(wager=_complete_wager)
    async def challenge(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        wager: int = 0,
    ):
        me = interaction.user
        if not isinstance(me, discord.Member) or interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Challenge someone in a server channel.", ephemeral=True
            )
            return
        if opponent.id == me.id:
            await interaction.response.send_message("You can't challenge yourself.", ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message(
                "The ghosts don't sit at this table.", ephemeral=True
            )
            return
        store = self.bot.get_cog("Store")
        if store is None:
            await interaction.response.send_message(
                "The house ledger isn't loaded right now.", ephemeral=True
            )
            return
        my_house = store.member_house(me)
        their_house = store.member_house(opponent)
        if not my_house or not their_house:
            await interaction.response.send_message(
                "You both need a house before the deck can include a house card. "
                "House roles (or `/staff houses sort`) decide that.",
                ephemeral=True,
            )
            return
        try:
            stake = int(wager)
        except (TypeError, ValueError):
            stake = -1
        if stake not in WAGERS:
            await interaction.response.send_message(
                _refusal("invalid", stake, my_house, their_house, None),
                ephemeral=True,
            )
            return

        night = self.book.wager_night_on()
        balances = _pair_balances(store, my_house, their_house)
        decision = stake_decision(night=night, wager=stake, balances=balances)
        if decision != "ok":
            await interaction.response.send_message(
                _refusal(decision, stake, my_house, their_house, balances),
                ephemeral=True,
            )
            return
        mode = "wager" if night else "card"

        async with self.lock:
            if self.book.in_match(me.id) or self.book.in_match(opponent.id):
                await interaction.response.send_message(
                    "One of you is already at a Curse & Crown table.", ephemeral=True
                )
                return
            opened = self.book.open_challenge(
                channel_id=interaction.channel.id,
                guild_id=interaction.guild.id,
                challenger_id=me.id,
                challenger_name=me.display_name,
                challenger_house=my_house,
                opponent_id=opponent.id,
                opponent_name=opponent.display_name,
                opponent_house=their_house,
                wager=stake,
                mode=mode,
            )

        embed = discord.Embed(
            title="Velmora Curse & Crown",
            description=(
                f"**{me.display_name}** of {_house_name(my_house)} challenges "
                f"**{opponent.display_name}** of {_house_name(their_house)}.\n\n"
                "Knock their Ward from 20 to nothing. Accept, and each of you "
                "gets a private link — only you can see your hand.\n\n"
                + _stake_sentence(mode, stake, my_house, their_house, balances)
            ),
            color=GOLD,
        )
        embed.set_footer(text="The stake is settled when the duel ends.")
        try:
            await interaction.response.send_message(
                content=opponent.mention,
                embed=embed,
                view=ChallengeView(opened.id),
            )
        except discord.HTTPException:
            async with self.lock:
                self.book.drop_challenge(opened.id)
            log.info("Could not post a Curse & Crown challenge.")
            return
        try:
            message = await interaction.original_response()
        except discord.HTTPException:
            log.info("Could not read the Curse & Crown challenge message back.")
            return
        async with self.lock:
            current = self.book.challenges.get(opened.id)
            if current:
                current.message_id = message.id
                self.book.save()

    @vcc.command(
        name="practice",
        description="Practice against an easy Tutor. No points, cards, or records.",
    )
    async def practice(self, interaction: discord.Interaction):
        me = interaction.user
        if interaction.channel is None:
            await interaction.response.send_message(
                "Open a practice table from a channel.", ephemeral=True
            )
            return
        store = self.bot.get_cog("Store")
        house = ""
        if store is not None and isinstance(me, discord.Member):
            house = store.member_house(me) or ""
        async with self.lock:
            if self.book.in_match(me.id):
                await interaction.response.send_message(
                    "You're already at a Curse & Crown table.", ephemeral=True
                )
                return
            raw = self.book.start_practice(
                channel_id=interaction.channel.id,
                guild_id=interaction.guild.id if interaction.guild else 0,
                player_id=me.id,
                player_name=me.display_name,
                player_house=house,
            )
        public = discord.Embed(
            title="Practice — Velmora Curse & Crown",
            description=(
                f"**{me.display_name}** is practicing against the **Tutor**.\n\n"
                "Practice. The Tutor plays an easy game. "
                "No house points, no Chocolate Frog, and no spell card. "
                "This table does not count toward a wager night, events, "
                "daily caps, or records.\n\n"
                "The table link is private."
            ),
            color=GOLD,
        )
        private = _practice_link_embed(_link(raw))
        try:
            if interaction.guild is None:
                await interaction.response.send_message(embed=private)
            else:
                await interaction.response.send_message(embed=public, view=TableView())
        except discord.HTTPException:
            async with self.lock:
                self.book.abandon_practice(me.id)
            log.info("Could not post a Curse & Crown practice table.")
            return
        if interaction.guild is not None:
            try:
                await interaction.followup.send(embed=private, ephemeral=True)
            except discord.HTTPException:
                log.info("Could not send a private practice link in channel %s.", interaction.channel.id)
            await self._dm(me.id, private)
            try:
                message = await interaction.original_response()
            except discord.HTTPException:
                log.info("Could not read the Curse & Crown practice message back.")
                return
            async with self.lock:
                table = self.book.active_game_for(me.id)
                if table and table.mode == "practice":
                    table.message_id = message.id
                    self.book.save()

    async def accept(self, interaction: discord.Interaction, challenge_id: str) -> None:
        async with self.lock:
            challenge = self.book.challenges.get(challenge_id)
            if challenge is None:
                outcome = "missing"
                raws = None
                refusal = None
            elif interaction.user.id != challenge.opponent_id:
                outcome = "stranger"
                raws = None
                refusal = None
            elif challenge.mode == "wager" and challenge.wager > 0:
                store = self.bot.get_cog("Store")
                balances = _pair_balances(store, challenge.challenger_house, challenge.opponent_house)
                decision = stake_decision(night=True, wager=challenge.wager, balances=balances)
                if decision != "ok":
                    self.book.drop_challenge(challenge_id)
                    outcome = "uncovered"
                    raws = None
                    refusal = _refusal(
                        decision,
                        challenge.wager,
                        challenge.challenger_house,
                        challenge.opponent_house,
                        balances,
                    )
                else:
                    outcome = "start"
                    raws = self.book.start_game(challenge)
                    refusal = None
            else:
                outcome = "start"
                raws = self.book.start_game(challenge)
                refusal = None
        if outcome == "missing":
            await interaction.response.send_message(
                "That challenge has already been answered.", ephemeral=True
            )
            return
        if outcome == "stranger":
            await interaction.response.send_message(
                "This challenge isn't yours to answer.", ephemeral=True
            )
            return
        if outcome == "uncovered":
            await interaction.response.edit_message(
                content=None,
                embed=discord.Embed(
                    description="The stake isn't covered anymore, so the challenge closes. " + (refusal or ""),
                    color=0x7A7A7A,
                ),
                view=None,
            )
            return

        started = discord.Embed(
            title="Velmora Curse & Crown",
            description=(
                f"**{challenge.challenger_name}** and **{challenge.opponent_name}** "
                "are at the table. Each of you has a private link — press **My table** "
                "if it didn't reach you. Only you can see your hand.\n\n"
                + _stake_sentence(
                    challenge.mode,
                    challenge.wager,
                    challenge.challenger_house,
                    challenge.opponent_house,
                    None,
                )
            ),
            color=GOLD,
        )
        await interaction.response.edit_message(content=None, embed=started, view=TableView())
        await interaction.followup.send(
            embed=_link_embed(_link(raws.get(interaction.user.id)), fresh=False),
            ephemeral=True,
        )
        await self._dm(challenge.challenger_id, _link_embed(_link(raws.get(challenge.challenger_id)), fresh=False))
        await self._dm(challenge.opponent_id, _link_embed(_link(raws.get(challenge.opponent_id)), fresh=False))

    async def decline(self, interaction: discord.Interaction, challenge_id: str) -> None:
        async with self.lock:
            challenge = self.book.challenges.get(challenge_id)
            if challenge is None:
                await interaction.response.send_message("That challenge is already closed.", ephemeral=True)
                return
            if interaction.user.id not in (challenge.challenger_id, challenge.opponent_id):
                await interaction.response.send_message("This isn't your challenge.", ephemeral=True)
                return
            self.book.drop_challenge(challenge_id)
        verb = "withdraws" if interaction.user.id == challenge.challenger_id else "declines"
        embed = discord.Embed(
            description=f"{interaction.user.display_name} {verb}. No duel today.",
            color=0x7A7A7A,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)

    async def send_table_link(self, interaction: discord.Interaction) -> None:
        user_id = interaction.user.id
        async with self.lock:
            table = self.book.active_game_for(user_id)
            if table is None:
                loc = self.book.by_user.get(user_id)
                waiting = bool(loc and loc[0] == "challenge")
                raw = None
                fresh = False
            else:
                waiting = False
                raw = self.book.remember_raw(table.id, user_id)
                fresh = False
                if not raw:
                    raw = self.book.rotate_link(user_id)
                    fresh = True
        if waiting:
            await interaction.response.send_message(
                "The table opens once the challenge is accepted.", ephemeral=True
            )
            return
        if not raw:
            await interaction.response.send_message(
                "You don't have a Curse & Crown table open.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=_link_embed(_link(raw), fresh=fresh),
            ephemeral=True,
        )

    async def _dm(self, user_id: int, embed: discord.Embed) -> None:
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(embed=embed)
        except discord.HTTPException as exc:
            log.info("Could not DM a Curse & Crown link to user %s (HTTP %s).", user_id, exc.status)

    # ---------------------------------------------------------------- settle

    async def settle(self, game_id: str) -> None:
        async with self.lock:
            table = self.book.games.get(game_id)
            if table is None or table.announced or table.settling:
                return
            if table.game.winner is None:
                return
            table.settling = True
            if not table.points_applied:
                self._apply_outcome(table)
            digests = list(table.tokens.keys())
            self.book.expire_tokens(table)
            embed = self._result_embed(table)
            prize = dict(table.prize or {})
            channel_id = table.channel_id
            message_id = table.message_id
        extra, attachment = self._frog_attachment(prize)
        try:
            await self._post_result(channel_id, message_id, embed, extra=extra, file=attachment)
        except Exception:
            log.exception("Could not post a Curse & Crown result in channel %s", channel_id)
            async with self.lock:
                current = self.book.games.get(game_id)
                if current:
                    current.settling = False
            return
        async with self.lock:
            current = self.book.games.get(game_id)
            if current:
                current.announced = True
                current.settling = False
                self.book.save()
        await self.hub.close(digests)

    def _apply_outcome(self, table) -> None:
        """House points, the prize card, and the win/loss record. Once per duel."""
        if table.points_applied:
            return
        points, note = self._award(table)
        table.points = points
        table.point_note = note
        game = table.game
        if note != "practice" and game.winner is not None and not game.practice:
            self.book.add_result(game.winner, game.other_id(game.winner))
        table.points_applied = True
        self.book.save()

    def _award(self, table) -> tuple[int, str]:
        game = table.game
        winner = game.players[game.winner]
        loser = game.other(game.winner)
        if table.mode == "practice" or game.practice:
            table.prize = None
            return 0, "practice"
        if table.mode != "wager":
            table.prize = self._give_collectible(winner.id)
            return 0, "card"
        if int(table.wager) <= 0:
            return 0, "free"
        store = self.bot.get_cog("Store")
        balances = _pair_balances(store, loser.house, loser.house)
        if balances is None:
            return 0, "no-season"
        moved, note = points_to_move(table.wager, balances[0])
        if moved <= 0:
            return 0, note
        actor = self.bot.user.id if self.bot.user else 0
        won = store.record(
            house=winner.house,
            delta=moved,
            actor_id=actor,
            target_id=winner.id,
            reason=f"Curse & Crown wager won from {loser.name}",
        )
        if won is None:
            return 0, "no-season"
        store.record(
            house=loser.house,
            delta=-moved,
            actor_id=actor,
            target_id=loser.id,
            reason=f"Curse & Crown wager lost to {winner.name}",
        )
        return moved, note

    def _give_collectible(self, user_id: int) -> dict:
        """A Chocolate Frog from the existing album, or one of the 35 spell cards."""
        frogs = self.bot.get_cog("Frogs")
        if frogs is not None and collectible_kind(random.randrange(2)) == "frog":
            try:
                from cogs.frogs import CARDS as FROG_CARDS

                card_id = frogs.roll()
                count, is_new = frogs.add_card(user_id, card_id)
                meta = FROG_CARDS[card_id]
                return {
                    "kind": "frog",
                    "card_id": card_id,
                    "name": meta["name"],
                    "emoji": meta.get("emoji") or "",
                    "text": meta.get("blurb") or "",
                    "count": count,
                    "new": is_new,
                }
            except Exception:
                log.exception("Chocolate Frog prize failed; dealing a spell card instead.")
        card_id = pick_from(tuple(CARDS), random.randrange(len(CARDS)))
        card = CARDS[card_id]
        count, is_new = self.book.add_spell(user_id, card_id)
        return {
            "kind": "spell",
            "card_id": card_id,
            "name": card.name,
            "text": card.text,
            "school": card.school_name,
            "tag": card.tag,
            "count": count,
            "new": is_new,
        }

    def _reward_lines(self, table) -> tuple[str, str]:
        game = table.game
        winner = game.players[game.winner]
        loser = game.other(game.winner)
        note = table.point_note
        if note == "practice" or table.mode == "practice":
            return (
                "Practice",
                "Practice against the Tutor. No house points, no Chocolate Frog, "
                "and no spell card. It does not count toward a wager night, events, "
                "daily caps, or records.",
            )
        if note == "card":
            prize = table.prize or {}
            fresh = "New." if prize.get("new") else f"Now ×{prize.get('count', 1)}."
            if prize.get("kind") == "frog":
                return (
                    "Prize",
                    f"{winner.name} draws a Chocolate Frog — {prize.get('emoji', '')} "
                    f"**{prize.get('name', 'a frog')}**.\n{prize.get('text', '')}\n"
                    f"{fresh} It's in their album (`/frog album`). No house points.",
                )
            return (
                "Prize",
                f"{winner.name} draws **{prize.get('name', 'a spell')}** "
                f"({prize.get('school', 'Spell')} · {prize.get('tag', 'Spell')}).\n"
                f"{prize.get('text', '')}\n"
                f"{fresh} It's in their spell album (`/vcc album`). No house points.",
            )
        if note == "free":
            return "Wager", f"Wager: **Free**. No house points move."
        if note == "no-season":
            return "Wager", "No House Cup season is running, so the stake can't be paid."
        if note == "uncovered":
            return (
                "Wager",
                f"The wager was **{table.wager}**, and {house_display(loser.house)} "
                "has nothing left to cover it. No points move.",
            )
        same = winner.house == loser.house
        if note == "partial":
            lead = (
                f"The wager was **{table.wager}**. {house_display(loser.house)} could cover "
                f"**{table.points}**, so those move"
            )
        elif note == "paid":
            lead = f"**{table.points}** house points move"
        else:
            return "Wager", "No house points move."
        if same:
            return (
                "Wager",
                f"{lead} from {loser.name} to {winner.name}. "
                f"{house_display(winner.house)} keeps the same Cup total. "
                "They keep everything earned beyond this stake.",
            )
        return (
            "Wager",
            f"{lead} from {house_display(loser.house)} to {house_display(winner.house)}. "
            "Each house keeps the rest of what it earned.",
        )

    def _result_embed(self, table) -> discord.Embed:
        game = table.game
        winner = game.players[game.winner]
        loser = game.other(game.winner)
        summary = game.view_for(winner.id).get("summary") or f"{winner.name} wins."
        meta = HOUSES.get(winner.house)
        practice = table.mode == "practice" or game.practice
        embed = discord.Embed(
            title="Practice — Velmora Curse & Crown" if practice else "Velmora Curse & Crown",
            description=summary,
            color=GOLD if practice else (meta["color"] if meta else GOLD),
        )
        embed.add_field(
            name="Ward",
            value=f"{winner.name} {winner.ward} · {loser.name} {loser.ward}",
            inline=False,
        )
        label, text = self._reward_lines(table)
        embed.add_field(name=label, value=text[:1024], inline=False)
        record = self._record_line(table)
        if record:
            embed.add_field(name="Record", value=record[:1024], inline=False)
        return embed

    def _record_line(self, table) -> str | None:
        """Win/loss totals after this duel. Practice is not a record."""
        game = table.game
        if table.mode == "practice" or game.practice or game.winner is None:
            return None
        winner = game.players[game.winner]
        loser = game.other(game.winner)
        return (
            f"{winner.name} — {_tally(*self.book.record_of(winner.id))}\n"
            f"{loser.name} — {_tally(*self.book.record_of(loser.id))}"
        )

    def _frog_attachment(self, prize: dict) -> tuple[discord.Embed | None, discord.File | None]:
        if prize.get("kind") != "frog" or not prize.get("card_id"):
            return None, None
        frogs = self.bot.get_cog("Frogs")
        if frogs is None:
            return None, None
        try:
            from cogs.frogs import _card_file

            flag = "**NEW!**" if prize.get("new") else f"Duplicate ×{prize.get('count', 1)}"
            embed = frogs.embed_card(
                prize["card_id"],
                title_prefix="🍫 Prize frog",
                flag=flag,
                footer="In their album · /frog album",
            )
            return embed, _card_file(prize["card_id"])
        except Exception:
            log.exception("Could not render a prize frog.")
            return None, None

    async def _post_result(
        self,
        channel_id: int,
        message_id: int | None,
        embed: discord.Embed,
        *,
        extra: discord.Embed | None = None,
        file: discord.File | None = None,
    ) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        kwargs = {"embeds": [embed] if extra is None else [embed, extra]}
        if file is not None:
            kwargs["file"] = file
        await channel.send(**kwargs)
        if message_id and hasattr(channel, "fetch_message"):
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(view=None)
            except discord.HTTPException as exc:
                log.info("Could not clear Curse & Crown buttons (%s).", exc.status)

    async def _close_challenge(self, challenge, text: str) -> None:
        if not challenge.message_id:
            return
        channel = self.bot.get_channel(challenge.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(challenge.channel_id)
            except discord.HTTPException:
                return
        try:
            message = await channel.fetch_message(challenge.message_id)
            await message.edit(
                content=None,
                embed=discord.Embed(description=text, color=0x7A7A7A),
                view=None,
            )
        except discord.HTTPException as exc:
            log.info("Could not close challenge %s (%s).", challenge.id, exc.status)

    async def watch(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Curse & Crown watch tick failed")
            await asyncio.sleep(2)

    async def _tick(self) -> None:
        now = time.time()
        async with self.lock:
            ended_night = self.book.pop_expired_wager_night(now)
            expired = self.book.expire_challenges(now)
            timed = self.book.timeout_games(now)
            self.book.sweep_tombstones(now)
            retry = [table.id for table in self.book.unannounced() if not table.settling]
        if ended_night:
            await self._announce_wager_night_over(ended_night)
        for challenge in expired:
            await self._close_challenge(challenge, "The challenge expired unanswered.")
        ended_now = set()
        for table, pushes in timed:
            ended_now.add(table.id)
            if pushes:
                await self.hub.push(pushes)
            await self.settle(table.id)
        for game_id in retry:
            if game_id not in ended_now:
                await self.settle(game_id)

    # ------------------------------------------------------------- album / night

    @vcc.command(name="record", description="Curse & Crown wins and losses.")
    @app_commands.describe(member="Whose record to view")
    async def record(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        async with self.lock:
            wins, losses = self.book.record_of(target.id)
        embed = discord.Embed(
            title=f"{target.display_name}'s Curse & Crown record",
            description=_tally(wins, losses),
            color=GOLD,
        )
        embed.set_footer(text="Finished duels only. Practice is not counted.")
        await interaction.response.send_message(embed=embed)

    @vcc.command(name="album", description="Your Curse & Crown spell cards.")
    @app_commands.describe(member="Whose spell album to view")
    async def album(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        async with self.lock:
            shelf = self.book.spells_of(target.id)
        unique = len(shelf)
        copies = sum(shelf.values())
        if not shelf:
            await interaction.response.send_message(
                f"{target.display_name} has no spell cards yet. "
                "Wins outside a wager night add one.",
                ephemeral=True,
            )
            return
        grouped: dict[str, list[str]] = {}
        for card_id, count in shelf.items():
            card = CARDS.get(card_id)
            if card is None:
                continue
            mark = f" ×{count}" if count > 1 else ""
            grouped.setdefault(card.school_name, []).append(f"{card.name}{mark}")
        order = (
            "Fire", "Frost", "Nature", "Shadow", "Star",
            "Asterwilde", "Oakmont", "Caldrin", "Veyren", "Thornmere",
        )
        lines = [f"**{unique}/35** unique · **{copies}** cards"]
        for school in order:
            names = grouped.get(school)
            if names:
                lines.append(f"**{school}** — " + ", ".join(sorted(names)))
        embed = discord.Embed(
            title=f"{target.display_name}'s spell album",
            description="\n".join(lines)[:4096],
            color=GOLD,
        )
        embed.set_footer(text="Won outside wager nights. Chocolate Frogs stay in /frog album.")
        await interaction.response.send_message(embed=embed)

    @vcc.command(name="night", description="Start or end a Curse & Crown wager night.")
    @app_commands.describe(
        action="Start or end the night",
        length="How long it runs",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="start", value="start"),
            app_commands.Choice(name="end", value="end"),
        ],
        length=[
            app_commands.Choice(name="30 minutes", value=30),
            app_commands.Choice(name="1 hour", value=60),
        ],
    )
    async def night(
        self,
        interaction: discord.Interaction,
        action: str,
        length: int | None = None,
    ):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if action not in {"start", "end"}:
            await interaction.response.send_message("Pick start or end.", ephemeral=True)
            return
        if action == "end":
            async with self.lock:
                finished = self.book.end_wager_night()
            if not finished:
                await interaction.response.send_message(
                    "There's no wager night running.", ephemeral=True
                )
                return
            await interaction.response.send_message(embed=_night_over_embed())
            return
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Start a wager night in a server channel.", ephemeral=True
            )
            return
        if length is not None and int(length) not in NIGHT_LENGTHS:
            await interaction.response.send_message(
                "Pick **30 minutes**, **1 hour**, or leave the length empty.",
                ephemeral=True,
            )
            return
        async with self.lock:
            ok, code, _night = self.book.begin_wager_night(
                by=interaction.user.id,
                channel_id=interaction.channel.id,
                guild_id=interaction.guild.id,
                minutes=None if length is None else int(length),
            )
        if not ok:
            text = (
                "A wager night is already running. End it first."
                if code == "already"
                else "Pick **30 minutes**, **1 hour**, or leave the length empty."
            )
            await interaction.response.send_message(text, ephemeral=True)
            return
        await interaction.response.send_message(embed=_night_open_embed(length))

    async def _announce_wager_night_over(self, night: dict) -> None:
        channel_id = night.get("channel_id")
        if not channel_id:
            return
        try:
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id))
            await channel.send(embed=_night_over_embed())
        except discord.HTTPException as exc:
            log.info("Could not post wager-night end in %s (HTTP %s).", channel_id, getattr(exc, "status", exc))


def _night_open_embed(minutes: int | None) -> discord.Embed:
    if minutes == 30:
        clock = "It runs for **30 minutes**."
    elif minutes == 60:
        clock = "It runs for **1 hour**."
    else:
        clock = "It runs until staff end it with `/vcc night`."
    return discord.Embed(
        title="Curse & Crown — wager night",
        description=(
            "Wager night is open. A challenger picks **Free**, **5 pts**, or **10 pts** "
            "from the stake list. The opponent sees that stake before they accept.\n\n"
            "The winner's house takes the stake from the loser's house. "
            "A house can't stake more than it has this season, and both houses "
            "keep the rest of what they earned. A free table moves no points.\n\n"
            f"{clock} Outside this night, wins draw a Chocolate Frog or a spell card instead."
        ),
        color=GOLD,
    )


def _night_over_embed() -> discord.Embed:
    return discord.Embed(
        title="Curse & Crown — wager night over",
        description=(
            "The night is closed. New challenges are free tables, "
            "and the winner draws a Chocolate Frog or a spell card. "
            "No house points move."
        ),
        color=GOLD,
    )


class TableView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="My table",
        style=discord.ButtonStyle.primary,
        custom_id="vcc:mytable",
    )
    async def my_table(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Vcc")
        if cog is None:
            await interaction.response.send_message(
                "Curse & Crown isn't loaded right now.", ephemeral=True
            )
            return
        await cog.send_table_link(interaction)


class ChallengeView(discord.ui.View):
    def __init__(self, challenge_id: str):
        super().__init__(timeout=None)
        self.add_item(AcceptButton(challenge_id))
        self.add_item(DeclineButton(challenge_id))


class AcceptButton(discord.ui.Button):
    def __init__(self, challenge_id: str):
        super().__init__(
            label="Accept",
            style=discord.ButtonStyle.success,
            custom_id=f"vcc:accept:{challenge_id}",
        )
        self.challenge_id = challenge_id

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Vcc")
        if cog is None:
            await interaction.response.send_message(
                "Curse & Crown isn't loaded right now.", ephemeral=True
            )
            return
        await cog.accept(interaction, self.challenge_id)


class DeclineButton(discord.ui.Button):
    def __init__(self, challenge_id: str):
        super().__init__(
            label="Decline",
            style=discord.ButtonStyle.secondary,
            custom_id=f"vcc:decline:{challenge_id}",
        )
        self.challenge_id = challenge_id

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Vcc")
        if cog is None:
            await interaction.response.send_message(
                "Curse & Crown isn't loaded right now.", ephemeral=True
            )
            return
        await cog.decline(interaction, self.challenge_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Vcc(bot))
