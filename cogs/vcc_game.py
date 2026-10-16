"""Velmora Curse & Crown — Discord challenge, private table links, house points.

The duel itself is played on the web table. This cog deals the challenge,
hands each player a link that only they can use, and writes the result
back to the channel through the House Cup ledger.

Links and table tokens are never logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.store import HOUSES, house_display
from cogs.vcc.book import Book, public_base_url
from cogs.vcc.scoring import REWARDED_PER_DAY, WIN_POINTS, points_for_win
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


def _link(raw: str | None) -> str | None:
    base = public_base_url()
    if not base or not raw:
        return None
    return f"{base}/play/{raw}"


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


class Vcc(commands.Cog):
    """1v1 Curse & Crown. The cards are played on the web, not in chat."""

    vcc = app_commands.Group(
        name="vcc",
        description="Velmora Curse & Crown, a private spellcard duel.",
    )

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
        app = create_app(self.book, self.hub, self.lock, on_end=self.settle)
        self.runner = await start_site(app)
        self._watch = asyncio.create_task(self.watch(), name="vcc-watch")

    async def cog_unload(self) -> None:
        if self._watch:
            self._watch.cancel()
        if self.runner is not None:
            await self.runner.cleanup()

    # --------------------------------------------------------------- discord

    @vcc.command(name="challenge", description="Challenge someone to Velmora Curse & Crown.")
    @app_commands.describe(opponent="Who you're challenging")
    async def challenge(self, interaction: discord.Interaction, opponent: discord.Member):
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
            )

        embed = discord.Embed(
            title="Velmora Curse & Crown",
            description=(
                f"**{me.display_name}** of {_house_name(my_house)} challenges "
                f"**{opponent.display_name}** of {_house_name(their_house)}.\n\n"
                "Knock their Ward from 20 to nothing. Accept, and each of you "
                "gets a private link — only you can see your hand."
            ),
            color=GOLD,
        )
        embed.set_footer(
            text=f"Winner's house earns {WIN_POINTS} points, up to {REWARDED_PER_DAY} wins a day. "
            "Same house pays nothing."
        )
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

    async def accept(self, interaction: discord.Interaction, challenge_id: str) -> None:
        async with self.lock:
            challenge = self.book.challenges.get(challenge_id)
            if challenge is None:
                await interaction.response.send_message(
                    "That challenge has already been answered.", ephemeral=True
                )
                return
            if interaction.user.id != challenge.opponent_id:
                await interaction.response.send_message(
                    "This challenge isn't yours to answer.", ephemeral=True
                )
                return
            raws = self.book.start_game(challenge)

        started = discord.Embed(
            title="Velmora Curse & Crown",
            description=(
                f"**{challenge.challenger_name}** and **{challenge.opponent_name}** "
                "are at the table. Each of you has a private link — press **My table** "
                "if it didn't reach you. Only you can see your hand."
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
                points, note = self._award(table)
                table.points = points
                table.point_note = note
                table.points_applied = True
                if note == "awarded":
                    stamps = self.book.rewarded.setdefault(str(table.game.winner), [])
                    stamps.append(time.time())
                self.book.save()
            digests = list(table.tokens.keys())
            self.book.expire_tokens(table)
            embed = self._result_embed(table)
            channel_id = table.channel_id
            message_id = table.message_id
        try:
            await self._post_result(channel_id, message_id, embed)
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

    def _award(self, table) -> tuple[int, str]:
        game = table.game
        winner = game.players[game.winner]
        loser = game.other(game.winner)
        from cogs.world_engine import on_central_day

        already = self.book.rewarded_today(winner.id, lambda stamp: on_central_day(stamp))
        points, note = points_for_win(
            same_house=winner.house == loser.house,
            rewarded_today=already,
        )
        if points <= 0:
            return 0, note
        store = self.bot.get_cog("Store")
        if store is None or not store.season_active():
            return 0, "no-season"
        actor = self.bot.user.id if self.bot.user else 0
        entry = store.record(
            house=winner.house,
            delta=points,
            actor_id=actor,
            target_id=winner.id,
            reason=f"Curse & Crown win over {loser.name}",
        )
        if entry is None:
            return 0, "no-season"
        return points, "awarded"

    def _result_embed(self, table) -> discord.Embed:
        game = table.game
        winner = game.players[game.winner]
        loser = game.other(game.winner)
        summary = game.view_for(winner.id).get("summary") or f"{winner.name} wins."
        meta = HOUSES.get(winner.house)
        embed = discord.Embed(
            title="Velmora Curse & Crown",
            description=summary,
            color=meta["color"] if meta else GOLD,
        )
        embed.add_field(
            name="Ward",
            value=f"{winner.name} {winner.ward} · {loser.name} {loser.ward}",
            inline=False,
        )
        if table.point_note == "awarded" and table.points:
            embed.add_field(
                name="House Cup",
                value=f"+{table.points} to {house_display(winner.house)}",
                inline=False,
            )
        elif table.point_note == "same-house":
            embed.add_field(name="House Cup", value="Same house — the Cup doesn't move.", inline=False)
        elif table.point_note == "daily-cap":
            embed.add_field(
                name="House Cup",
                value=f"Daily Curse & Crown points already claimed ({REWARDED_PER_DAY} wins).",
                inline=False,
            )
        elif table.point_note == "no-season":
            embed.add_field(
                name="House Cup",
                value="No House Cup season is running, so the Cup doesn't move.",
                inline=False,
            )
        return embed

    async def _post_result(self, channel_id: int, message_id: int | None, embed: discord.Embed) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        await channel.send(embed=embed)
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
            expired = self.book.expire_challenges(now)
            timed = self.book.timeout_games(now)
            self.book.sweep_tombstones(now)
            retry = [table.id for table in self.book.unannounced() if not table.settling]
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
