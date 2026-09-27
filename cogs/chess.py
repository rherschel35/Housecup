"""
Wizard's Chess - real chess, played out over Discord messages.

    /chess challenge member:<@user>              - challenge someone to a match
    /chess move opponent:<name> from:<sq> to:<sq> - make a move (or pick a piece, then
                                                     a square, on the board message)
    /chess resign opponent:<name>                 - concede a match in progress
    /chessstats [member]                          - wins, losses, title, and active games
    /chessreset member:<@user>                    - (staff) wipe someone's chess record

Rules are real chess, enforced by the `chess` library - legal moves only,
proper checkmate/stalemate detection, castling, en passant, promotion.
Only one active match is allowed between any two given players at a time,
but everyone can be in as many simultaneous matches (against different
people) as they like.

Rewards: +3 House Cup points per win, capped at 15 points (5 point-earning
wins) per player per day - beyond that, wins still count for your record
and title, just without points. Beating someone from your own house never
pays points either way. A resignation counts exactly like a loss/win. A
match with no move in 24 hours auto-forfeits: whoever's turn it was takes
a loss, and the other player does NOT get a win for it.
"""

from __future__ import annotations

import datetime
import io
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import chess
import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.chess_board import piece_glyph, render_board

log = logging.getLogger("velmora.chess")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "chess_state.json"

CHESS_CHANNEL_ID = 1553366177584124014

POINTS_PER_WIN = 3
DAILY_WIN_CAP = 5   # point-earning wins per day (5 x 3 = 15 points/day)
TIMEOUT_SECONDS = 24 * 3600

# (min wins, [titles unlocked at this tier])
TITLE_TIERS = [
    (10, ["Pawn Star", "Knight Shift Supervisor", "Certified Board Menace"]),
    (25, ["The Forklift Certified Bishop", "Assistant Manager of Checkmate"]),
    (50, ["Board Certified War Criminal", "The Unnecessarily Sweaty Grandmaster",
         "Department Head of Psychological Warfare"]),
    (100, ["God-Emperor of the 64 Squares", "International Menace to Casual Gaming"]),
]
RANKUP_LINES = {
    10: ["Somewhere, a pawn just filed a complaint.", "You've been noticed. The bishops are nervous."],
    25: ["Game night will never forgive you.", "People are starting to make excuses not to play you."],
    50: ["The board itself seems to sweat when you sit down.", "Friendships have been lost. You did that."],
    100: ["The 64 squares now answer to you.", "Casual chess night is no longer casual, and it's your fault."],
}

FLAVOR_CAPTURE = [
    "A piece topples with a satisfying clack.",
    "Captured — the board remembers.",
    "One fewer piece standing.",
]
FLAVOR_CHECK = [
    "The king feels the pressure.",
    "Check — the wizards lean in.",
]
FLAVOR_MOVE = [
    "The pieces rearrange themselves.",
    "A quiet shift across the board.",
]


def today_str() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def blank_player() -> dict:
    return {"wins": 0, "losses": 0, "draws": 0, "daily": {"date": today_str(), "count": 0},
            "titles_seen": []}


def title_for(wins: int) -> list[str]:
    out = []
    for threshold, names in TITLE_TIERS:
        if wins >= threshold:
            out += names
    return out


class Match:
    def __init__(self, white_id: int, black_id: int):
        self.board = chess.Board()
        self.white_id = white_id
        self.black_id = black_id
        self.last_move_at = time.time()
        self.log: list[str] = []
        self.message_id: Optional[int] = None
        self.channel_id: Optional[int] = None
        self.last_san: Optional[str] = None
        self.last_flavor: Optional[str] = None

    def player_ids(self) -> set[int]:
        return {self.white_id, self.black_id}

    def turn_id(self) -> int:
        return self.white_id if self.board.turn == chess.WHITE else self.black_id

    def color_of(self, user_id: int) -> Optional[bool]:
        if user_id == self.white_id:
            return chess.WHITE
        if user_id == self.black_id:
            return chess.BLACK
        return None

    def last_move(self) -> Optional[chess.Move]:
        return self.board.peek() if self.board.move_stack else None


class ChallengeView(discord.ui.View):
    def __init__(self, cog: "Chess", challenger_id: int, opponent_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="♟️")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("This challenge isn't addressed to you.", ephemeral=True)
            return
        await self.cog.start_match(interaction, self.challenger_id, self.opponent_id)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("This challenge isn't addressed to you.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Challenge declined.", embed=None, view=None,
                                                attachments=[])
        self.stop()


class PieceSelect(discord.ui.Select):
    """Step 1: pick one of your pieces that has a legal move."""

    def __init__(self, cog: "Chess", match_key: tuple[int, int], player_id: int,
                 choices: list[tuple[str, str]], page: int = 0):
        self.cog = cog
        self.match_key = match_key
        self.player_id = player_id
        self.page = page
        options = [discord.SelectOption(label=label, value=value) for label, value in choices]
        super().__init__(placeholder="Step 1 — pick a piece...", options=options,
                         min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("It's not your move.", ephemeral=True)
            return
        m = self.cog.matches.get(self.match_key)
        if not m:
            await interaction.response.send_message("This match has ended.", ephemeral=True)
            return
        if m.turn_id() != self.player_id:
            await interaction.response.send_message("It's not your move.", ephemeral=True)
            return
        try:
            selected = chess.parse_square(self.values[0])
        except ValueError:
            await interaction.response.send_message("That square isn't valid anymore.", ephemeral=True)
            return
        await self.cog.refresh_match_message(interaction, m, selected=selected, respond=True)


class DestSelect(discord.ui.Select):
    """Step 2: pick a legal destination for the selected piece."""

    def __init__(self, cog: "Chess", match_key: tuple[int, int], player_id: int,
                 selected: int, choices: list[tuple[str, str]]):
        self.cog = cog
        self.match_key = match_key
        self.player_id = player_id
        self.selected = selected
        options = [discord.SelectOption(label=label, value=value) for label, value in choices]
        super().__init__(placeholder="Step 2 — pick a square...", options=options,
                         min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("It's not your move.", ephemeral=True)
            return
        m = self.cog.matches.get(self.match_key)
        if not m:
            await interaction.response.send_message("This match has ended.", ephemeral=True)
            return
        if m.turn_id() != self.player_id:
            await interaction.response.send_message("It's not your move.", ephemeral=True)
            return
        mv = chess.Move.from_uci(self.values[0])
        if mv not in m.board.legal_moves:
            await interaction.response.send_message(
                "That move isn't legal anymore - someone else's move landed first. Refresh by "
                "checking the latest board message.", ephemeral=True)
            return
        await self.cog.play_move(interaction, m, mv, interaction.user)


class MatchView(discord.ui.View):
    """Persistent match controls: piece → destination menus, optional page/change, resign."""

    def __init__(self, cog: "Chess", match_key: tuple[int, int], player_id: int,
                 *, selected: Optional[int] = None, page: int = 0,
                 piece_pages: Optional[list[list[tuple[str, str]]]] = None,
                 dest_choices: Optional[list[tuple[str, str]]] = None):
        super().__init__(timeout=None)
        self.cog = cog
        self.match_key = match_key
        self.player_id = player_id
        self.selected = selected
        self.page = page
        self.piece_pages = piece_pages or []

        if selected is None:
            if piece_pages:
                page = max(0, min(page, len(piece_pages) - 1))
                self.page = page
                self.add_item(PieceSelect(cog, match_key, player_id, piece_pages[page], page=page))
                if len(piece_pages) > 1:
                    prev_btn = discord.ui.Button(label="◀ Pieces", style=discord.ButtonStyle.secondary,
                                                 disabled=(page <= 0), row=1)
                    next_btn = discord.ui.Button(label="Pieces ▶", style=discord.ButtonStyle.secondary,
                                                 disabled=(page >= len(piece_pages) - 1), row=1)

                    async def _prev(interaction: discord.Interaction):
                        await self._page(interaction, -1)

                    async def _next(interaction: discord.Interaction):
                        await self._page(interaction, 1)

                    prev_btn.callback = _prev
                    next_btn.callback = _next
                    self.add_item(prev_btn)
                    self.add_item(next_btn)
        else:
            if dest_choices:
                self.add_item(DestSelect(cog, match_key, player_id, selected, dest_choices))
            change = discord.ui.Button(label="Change piece", style=discord.ButtonStyle.secondary, row=1)

            async def _change(interaction: discord.Interaction):
                if interaction.user.id != self.player_id:
                    await interaction.response.send_message("It's not your move.", ephemeral=True)
                    return
                m = self.cog.matches.get(self.match_key)
                if not m:
                    await interaction.response.send_message("This match has ended.", ephemeral=True)
                    return
                await self.cog.refresh_match_message(interaction, m, selected=None, respond=True)

            change.callback = _change
            self.add_item(change)

        resign = discord.ui.Button(label="Resign", style=discord.ButtonStyle.danger, row=2)

        async def _resign(interaction: discord.Interaction):
            await self.cog.resign_from_view(interaction, self.match_key)

        resign.callback = _resign
        self.add_item(resign)

    async def _page(self, interaction: discord.Interaction, delta: int):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("It's not your move.", ephemeral=True)
            return
        m = self.cog.matches.get(self.match_key)
        if not m:
            await interaction.response.send_message("This match has ended.", ephemeral=True)
            return
        await self.cog.refresh_match_message(
            interaction, m, selected=None, page=self.page + delta, respond=True)


class Chess(commands.Cog):
    group = app_commands.Group(name="chess", description="Wizard's Chess.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self.matches: dict[tuple[int, int], Match] = {}  # key: frozenset-sorted tuple(low, high)

    async def cog_load(self):
        self.timeout_check.start()

    async def cog_unload(self):
        self.timeout_check.cancel()

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read %s", STATE_PATH)
            state = {}
        state.setdefault("players", {})
        return state

    def save(self):
        tmp = STATE_PATH.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save %s", STATE_PATH)

    def record(self, user_id: int) -> dict:
        rec = self.state["players"].setdefault(str(user_id), blank_player())
        if rec["daily"]["date"] != today_str():
            rec["daily"] = {"date": today_str(), "count": 0}
        rec.setdefault("titles_seen", [])
        return rec

    def wins_of(self, user_id: int) -> int:
        return self.record(user_id)["wins"]

    def titles_of(self, user_id: int) -> list[str]:
        return title_for(self.wins_of(user_id))

    @staticmethod
    def _key(a: int, b: int) -> tuple[int, int]:
        return (a, b) if a < b else (b, a)

    def match_between(self, a: int, b: int) -> Optional[Match]:
        return self.matches.get(self._key(a, b))

    def matches_of(self, user_id: int) -> list[Match]:
        return [m for m in self.matches.values() if user_id in m.player_ids()]

    def _in_channel(self, interaction: discord.Interaction) -> bool:
        return interaction.channel_id == CHESS_CHANNEL_ID

    # -------------------------------------------------------- move menus

    def _queen_legal_moves(self, board: chess.Board) -> list[chess.Move]:
        """Legal moves, queen-only promotions (same policy as the old single dropdown)."""
        out = []
        for mv in board.legal_moves:
            if mv.promotion and mv.promotion != chess.QUEEN:
                continue
            out.append(mv)
        return out

    def _piece_pages(self, board: chess.Board) -> list[list[tuple[str, str]]]:
        """Step 1 options: current side's pieces with ≥1 legal move. Paginated at 25."""
        by_sq: dict[int, chess.Piece] = {}
        for mv in self._queen_legal_moves(board):
            piece = board.piece_at(mv.from_square)
            if piece:
                by_sq[mv.from_square] = piece
        choices = []
        for sq in sorted(by_sq, key=lambda s: (chess.square_rank(s), chess.square_file(s))):
            piece = by_sq[sq]
            label = f"{piece_glyph(piece)} {chess.square_name(sq)}"
            choices.append((label, chess.square_name(sq)))
        if not choices:
            return []
        pages = [choices[i:i + 25] for i in range(0, len(choices), 25)]
        return pages

    def _dest_choices(self, board: chess.Board, from_sq: int) -> list[tuple[str, str]]:
        """Step 2 options for a selected piece. Castling → O-O / O-O-O; promo → e8=Q."""
        out = []
        for mv in self._queen_legal_moves(board):
            if mv.from_square != from_sq:
                continue
            if board.is_kingside_castling(mv):
                label = "O-O"
            elif board.is_queenside_castling(mv):
                label = "O-O-O"
            elif mv.promotion == chess.QUEEN:
                label = f"{chess.square_name(mv.to_square)}=Q"
            else:
                label = chess.square_name(mv.to_square)
            out.append((label, mv.uci()))
        # Discord select labels must be unique; rare duplicate square names shouldn't happen
        seen = set()
        unique = []
        for label, uci in out:
            if label in seen:
                label = f"{label} ({uci})"
            seen.add(label)
            unique.append((label, uci))
        return unique[:25]

    def _board_file(self, m: Match, selected: Optional[int] = None) -> discord.File:
        dests = None
        if selected is not None:
            dests = [mv.to_square for mv in self._queen_legal_moves(m.board)
                     if mv.from_square == selected]
        png = render_board(
            m.board,
            last_move=m.last_move(),
            selected=selected,
            destinations=dests,
            flip=(m.board.turn == chess.BLACK),
        )
        return discord.File(io.BytesIO(png), filename="chess_board.png")

    def _match_embed(self, interaction: discord.Interaction, m: Match, *,
                     ended: Optional[str] = None) -> discord.Embed:
        white = interaction.guild.get_member(m.white_id) if interaction.guild else None
        black = interaction.guild.get_member(m.black_id) if interaction.guild else None
        w_name = white.display_name if white else str(m.white_id)
        b_name = black.display_name if black else str(m.black_id)
        lines = [f"♙ **{w_name}** (White)  ⚔️  ♟ **{b_name}** (Black)"]
        if m.last_san:
            lines.append(f"Last move: **{m.last_san}**")
            if m.last_flavor:
                lines.append(f"*{m.last_flavor}*")
        if ended:
            lines.append(ended)
        else:
            turn = white if m.turn_id() == m.white_id else black
            turn_name = turn.display_name if turn else str(m.turn_id())
            check = " **Check!**" if m.board.is_check() else ""
            lines.append(f"{turn_name} to move — pick a piece, then a square"
                         f"{check}. Or `/chess move`.")
        color = 0x2ECC71 if ended and "wins" in ended.lower() else (
            0x7A7A7A if ended else 0x8B5FBF)
        embed = discord.Embed(
            title=f"♟️ Wizard's Chess — {w_name} ⚔️ {b_name}",
            description="\n".join(lines),
            color=color,
        )
        embed.set_image(url="attachment://chess_board.png")
        return embed

    def _match_view(self, m: Match, *, selected: Optional[int] = None,
                    page: int = 0) -> Optional[MatchView]:
        key = self._key(m.white_id, m.black_id)
        player_id = m.turn_id()
        if selected is None:
            pages = self._piece_pages(m.board)
            if not pages:
                return MatchView(self, key, player_id, selected=None, page=0, piece_pages=[])
            return MatchView(self, key, player_id, selected=None, page=page, piece_pages=pages)
        dests = self._dest_choices(m.board, selected)
        return MatchView(self, key, player_id, selected=selected, dest_choices=dests)

    async def refresh_match_message(self, interaction: discord.Interaction, m: Match, *,
                                    selected: Optional[int] = None, page: int = 0,
                                    respond: bool = True, ended: Optional[str] = None):
        """Redraw the match board message (edit in place)."""
        file = self._board_file(m, selected=selected if not ended else None)
        embed = self._match_embed(interaction, m, ended=ended)
        view = None if ended else self._match_view(m, selected=selected, page=page)
        if respond and not interaction.response.is_done():
            await interaction.response.edit_message(
                content=None, embed=embed, attachments=[file], view=view)
            try:
                msg = await interaction.original_response()
                m.message_id = msg.id
                m.channel_id = msg.channel.id
            except discord.DiscordException:
                pass
            return
        # Slash-command path or already-responded: edit the stored board message.
        channel = interaction.channel
        if m.channel_id:
            channel = self.bot.get_channel(m.channel_id) or channel
        if channel and m.message_id:
            try:
                msg = await channel.fetch_message(m.message_id)
                await msg.edit(content=None, embed=embed, attachments=[file], view=view)
                return
            except discord.DiscordException:
                log.exception("Could not edit chess board message")
        # Fallback: post a fresh board message.
        if channel:
            msg = await channel.send(embed=embed, file=file, view=view)
            m.message_id = msg.id
            m.channel_id = msg.channel.id

    # ------------------------------------------------------------- rewards

    async def _award_and_record(self, channel, winner_id: Optional[int], loser_id: int, reason: str):
        """winner_id is None for a timeout forfeit - the loser just takes the
        loss, nobody gets a win or points."""
        loser_rec = self.record(loser_id)
        loser_rec["losses"] += 1
        lines = []

        if winner_id is not None:
            winner_rec = self.record(winner_id)
            winner_rec["wins"] += 1
            store = self.bot.get_cog("Store")
            winner_member = channel.guild.get_member(winner_id) if channel.guild else None
            loser_member = channel.guild.get_member(loser_id) if channel.guild else None
            paid = False
            if store and winner_member and loser_member:
                wh, lh = store.member_house(winner_member), store.member_house(loser_member)
                if wh and wh != lh and winner_rec["daily"]["count"] < DAILY_WIN_CAP:
                    store.record(house=wh, delta=POINTS_PER_WIN,
                                actor_id=self.bot.user.id if self.bot.user else 0,
                                target_id=winner_id, reason=f"Wizard's Chess: won by {reason}")
                    winner_rec["daily"]["count"] += 1
                    paid = True
            self.save()

            for threshold, names in TITLE_TIERS:
                if winner_rec["wins"] >= threshold and threshold not in winner_rec["titles_seen"]:
                    winner_rec["titles_seen"].append(threshold)
                    line = random.choice(RANKUP_LINES[threshold])
                    lines.append(f"🏆 **{winner_member.display_name if winner_member else winner_id}** just "
                                f"unlocked a new tier of Chess titles ({', '.join(names)})! {line}")
            self.save()
            if paid and winner_member:
                lines.insert(0, f"+{POINTS_PER_WIN} House Cup points to {winner_member.display_name}'s house.")
        else:
            self.save()

        if lines:
            await channel.send("\n".join(lines))

    # -------------------------------------------------------- match upkeep

    @tasks.loop(minutes=15)
    async def timeout_check(self):
        now = time.time()
        stale = [key for key, m in self.matches.items() if now - m.last_move_at > TIMEOUT_SECONDS]
        for key in stale:
            m = self.matches.pop(key, None)
            if not m:
                continue
            channel = self.bot.get_channel(CHESS_CHANNEL_ID)
            if not channel:
                continue
            loser_id = m.turn_id()
            try:
                await channel.send(embed=discord.Embed(
                    title="⏳ Match forfeited",
                    description=f"<@{loser_id}> didn't move within 24 hours. The match is over - no win for "
                               f"the other side, but the loss stands.",
                    color=0x7A7A7A))
                await self._award_and_record(channel, None, loser_id, "timeout")
            except discord.DiscordException:
                log.exception("Could not post a chess timeout forfeit")

    @timeout_check.before_loop
    async def _before_timeout(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ starting

    async def start_match(self, interaction: discord.Interaction, challenger_id: int, opponent_id: int):
        key = self._key(challenger_id, opponent_id)
        if key in self.matches:
            await interaction.response.edit_message(
                content="You two already have a match in progress.", embed=None, view=None,
                attachments=[])
            return
        white_id, black_id = (challenger_id, opponent_id) if random.random() < 0.5 else (opponent_id, challenger_id)
        m = Match(white_id, black_id)
        self.matches[key] = m
        await self.refresh_match_message(interaction, m, selected=None, respond=True)

    @group.command(name="challenge", description="Challenge someone to a game of Wizard's Chess.")
    @app_commands.describe(member="Who to challenge")
    async def challenge(self, interaction: discord.Interaction, member: discord.Member):
        if not self._in_channel(interaction):
            await interaction.response.send_message(f"Chess only runs in <#{CHESS_CHANNEL_ID}>.", ephemeral=True)
            return
        if member.bot or member.id == interaction.user.id:
            await interaction.response.send_message("Pick a real opponent.", ephemeral=True)
            return
        if self.match_between(interaction.user.id, member.id):
            await interaction.response.send_message("You two already have a match in progress.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"♟️ {member.mention}, {interaction.user.display_name} challenges you to Wizard's Chess!",
            view=ChallengeView(self, interaction.user.id, member.id))

    # ------------------------------------------------------------- moving

    def _flavor_for(self, board_before: chess.Board, mv: chess.Move) -> str:
        if board_before.is_capture(mv):
            return random.choice(FLAVOR_CAPTURE)
        board_before.push(mv)
        try:
            if board_before.is_check():
                return random.choice(FLAVOR_CHECK)
        finally:
            board_before.pop()
        if random.random() < 0.35:
            return random.choice(FLAVOR_MOVE)
        return ""

    async def play_move(self, interaction: discord.Interaction, m: Match, mv: chess.Move, mover):
        """Applies a legal move (menu or typed command) and refreshes the board message."""
        san = m.board.san(mv)
        flavor = self._flavor_for(m.board, mv)
        m.board.push(mv)
        m.last_move_at = time.time()
        m.last_san = san
        m.last_flavor = flavor or None
        m.log.append(san)

        from_component = bool(interaction.message and m.message_id
                              and interaction.message.id == m.message_id)

        outcome = m.board.outcome(claim_draw=True)
        if outcome:
            self.matches.pop(self._key(m.white_id, m.black_id), None)
            if outcome.winner is None:
                self.record(m.white_id)["draws"] += 1
                self.record(m.black_id)["draws"] += 1
                self.save()
                ended = "🤝 **Draw.** Nobody's chess pieces get smashed today."
                if from_component:
                    await self.refresh_match_message(interaction, m, ended=ended, respond=True)
                else:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            f"Played **{san}**. Draw.", ephemeral=True)
                    await self.refresh_match_message(interaction, m, ended=ended, respond=False)
                return
            winner_id = m.white_id if outcome.winner == chess.WHITE else m.black_id
            loser_id = m.black_id if outcome.winner == chess.WHITE else m.white_id
            winner = interaction.guild.get_member(winner_id) if interaction.guild else None
            w_name = winner.display_name if winner else str(winner_id)
            ended = f"🏆 **Checkmate.** {w_name} wins."
            if from_component:
                await self.refresh_match_message(interaction, m, ended=ended, respond=True)
            else:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"Played **{san}**. Checkmate — {w_name} wins.", ephemeral=True)
                await self.refresh_match_message(interaction, m, ended=ended, respond=False)
            await self._award_and_record(interaction.channel, winner_id, loser_id, "checkmate")
            return

        if from_component:
            await self.refresh_match_message(interaction, m, selected=None, respond=True)
        else:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Played **{san}**.", ephemeral=True)
            await self.refresh_match_message(interaction, m, selected=None, respond=False)

    @group.command(name="move",
                   description="Make a move by typing squares (or pick piece → square on the board).")
    @app_commands.describe(opponent="Who you're playing", from_square="Square to move from (e.g. e2)",
                           to_square="Square to move to (e.g. e4)")
    async def move(self, interaction: discord.Interaction, opponent: discord.Member,
                   from_square: str, to_square: str):
        if not self._in_channel(interaction):
            await interaction.response.send_message(f"Chess only runs in <#{CHESS_CHANNEL_ID}>.", ephemeral=True)
            return
        m = self.match_between(interaction.user.id, opponent.id)
        if not m:
            await interaction.response.send_message("You don't have a match against them right now.",
                                                     ephemeral=True)
            return
        my_color = m.color_of(interaction.user.id)
        if m.board.turn != my_color:
            await interaction.response.send_message("It's not your move.", ephemeral=True)
            return
        try:
            fsq, tsq = chess.parse_square(from_square.lower()), chess.parse_square(to_square.lower())
        except ValueError:
            await interaction.response.send_message("That's not a real square (use e.g. `e2`).", ephemeral=True)
            return

        candidates = [mv for mv in m.board.legal_moves if mv.from_square == fsq and mv.to_square == tsq]
        if not candidates:
            await interaction.response.send_message("That's not a legal move right now.", ephemeral=True)
            return
        mv = candidates[0]
        if len(candidates) > 1:  # promotion choices - default to queen
            mv = chess.Move(fsq, tsq, promotion=chess.QUEEN)

        await self.play_move(interaction, m, mv, interaction.user)

    @move.autocomplete("from_square")
    async def _from_autocomplete(self, interaction: discord.Interaction, current: str):
        opponent = interaction.namespace.opponent
        if not opponent:
            return []
        m = self.match_between(interaction.user.id, opponent.id)
        if not m or m.color_of(interaction.user.id) != m.board.turn:
            return []
        out = []
        for sq in sorted({mv.from_square for mv in m.board.legal_moves}):
            name = chess.square_name(sq)
            if current.lower() in name:
                piece = m.board.piece_at(sq)
                out.append(app_commands.Choice(name=f"{name} ({chess.piece_name(piece.piece_type)})", value=name))
        return out[:25]

    @move.autocomplete("to_square")
    async def _to_autocomplete(self, interaction: discord.Interaction, current: str):
        opponent = interaction.namespace.opponent
        from_square = getattr(interaction.namespace, "from_square", None)
        if not opponent or not from_square:
            return []
        m = self.match_between(interaction.user.id, opponent.id)
        if not m:
            return []
        try:
            fsq = chess.parse_square(from_square.lower())
        except ValueError:
            return []
        out = []
        for mv in [mv for mv in m.board.legal_moves if mv.from_square == fsq]:
            name = chess.square_name(mv.to_square)
            if current.lower() in name and name not in [o.value for o in out]:
                out.append(app_commands.Choice(name=name, value=name))
        return out[:25]

    # ----------------------------------------------------------- resigning

    async def resign_from_view(self, interaction: discord.Interaction, match_key: tuple[int, int]):
        m = self.matches.get(match_key)
        if not m:
            await interaction.response.send_message("This match has ended.", ephemeral=True)
            return
        if interaction.user.id not in m.player_ids():
            await interaction.response.send_message("You're not in this match.", ephemeral=True)
            return
        winner_id = m.black_id if interaction.user.id == m.white_id else m.white_id
        self.matches.pop(match_key, None)
        winner = interaction.guild.get_member(winner_id) if interaction.guild else None
        w_name = winner.display_name if winner else str(winner_id)
        ended = f"🏳️ {interaction.user.display_name} resigns. {w_name} wins."
        await self.refresh_match_message(interaction, m, ended=ended, respond=True)
        await self._award_and_record(interaction.channel, winner_id, interaction.user.id, "resignation")

    @group.command(name="resign", description="Concede a match in progress.")
    @app_commands.describe(opponent="Who you're conceding to")
    async def resign(self, interaction: discord.Interaction, opponent: discord.Member):
        if not self._in_channel(interaction):
            await interaction.response.send_message(f"Chess only runs in <#{CHESS_CHANNEL_ID}>.", ephemeral=True)
            return
        m = self.match_between(interaction.user.id, opponent.id)
        if not m:
            await interaction.response.send_message("You don't have a match against them right now.",
                                                     ephemeral=True)
            return
        self.matches.pop(self._key(m.white_id, m.black_id), None)
        ended = f"🏳️ {interaction.user.display_name} resigns. {opponent.display_name} wins."
        await interaction.response.send_message(embed=discord.Embed(description=ended, color=0xC0392B))
        await self.refresh_match_message(interaction, m, ended=ended, respond=False)
        await self._award_and_record(interaction.channel, opponent.id, interaction.user.id, "resignation")

    # -------------------------------------------------------------- stats

    @app_commands.command(name="chessstats", description="Wins, losses, title, and active games.")
    @app_commands.describe(member="Whose stats to show (leave blank for your own)")
    async def chessstats(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        if not self._in_channel(interaction):
            await interaction.response.send_message(f"Chess only runs in <#{CHESS_CHANNEL_ID}>.", ephemeral=True)
            return
        target = member or interaction.user
        rec = self.record(target.id)
        adorn = self.bot.get_cog("Adornments")
        title = adorn.title_of(target) if adorn else None

        active = self.matches_of(target.id)
        active_lines = []
        for m in active:
            opp_id = m.black_id if target.id == m.white_id else m.white_id
            opp = interaction.guild.get_member(opp_id)
            turn = "your move" if m.turn_id() == target.id else "their move"
            active_lines.append(f"vs {opp.display_name if opp else opp_id} ({turn})")

        desc = [f"**{rec['wins']}W - {rec['losses']}L - {rec['draws']}D**"]
        if title:
            desc.append(f"Title: **{title}**")
        desc.append(f"Point-earning wins today: {rec['daily']['count']}/{DAILY_WIN_CAP}")
        embed = discord.Embed(title=f"{target.display_name}'s Chess record", description="\n".join(desc),
                              color=0x8B5FBF)
        embed.add_field(name="Active games", value="\n".join(active_lines) if active_lines else "*None right now.*",
                        inline=False)
        await interaction.response.send_message(embed=embed)

    # --------------------------------------------------------------- staff

    @group.command(name="reset", description="(staff) Wipe someone's chess record.")
    @app_commands.describe(member="Whose record to reset")
    async def reset(self, interaction: discord.Interaction, member: discord.Member):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        self.state["players"][str(member.id)] = blank_player()
        for key in [k for k in self.matches if member.id in k]:
            self.matches.pop(key, None)
        self.save()
        await interaction.response.send_message(f"{member.display_name}'s chess record has been wiped, and any "
                                                 f"of their active matches were dropped.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Chess(bot))
