"""
Wizard's Checkers - standard American checkers rules, played over Discord.

    /checkers challenge member:<@user>              - challenge someone to a match
    /checkers move opponent:<name> from:<sq> to:<sq> - make a move (or pick a piece, then
                                                        a square, on the board message)
    /checkers resign opponent:<name>                 - concede a match in progress
    /checkersstats [member]                          - wins, losses, title, and active games
    /checkers reset member:<@user>                   - (staff) wipe someone's checkers record

Rules: 8x8 board, pieces only on dark squares, forward diagonal moves,
mandatory captures (if any capture is available anywhere on the board, only
captures are legal), multi-jump chains must be finished with the same piece
before the turn passes, and reaching the far row crowns a king (which can
move/capture in all four diagonal directions). A player with no legal move
on their turn loses immediately. Only one active match is allowed between
any two given players at a time, but everyone can be in as many
simultaneous matches (against different people) as they like.

UX mirrors Wizard's Chess: PNG board, public Make move / Resign buttons,
ephemeral piece → square picks, 60s challenge timeout, and a turn @mention
ping (edits don't notify).

Rewards: +1 House Cup point per win, capped at 5 points (5 point-earning
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

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.checkers_board import describe_move, piece_label, render_board, square_name as board_square_name

log = logging.getLogger("velmora.checkers")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "checkers_state.json"

# Same board-games channel as Wizard's Chess.
CHECKERS_CHANNEL_ID = 1553832675993985024

POINTS_PER_WIN = 1
DAILY_WIN_CAP = 5
TIMEOUT_SECONDS = 24 * 3600
CHALLENGE_TIMEOUT_SECONDS = 60

TITLE_TIERS = [
    (10, ["Checker Wrecker", "Minor Strategic Nuisance"]),
    (25, ["Regional Threat to Game Night", "Grandmaster of Bad Intentions", "Destroyer of Friendly Competition"]),
    (50, ["Supreme Chancellor of Tiny Wooden Violence", "King of Ruined Friendships"]),
    (100, ["The Final Boss of Cracker Barrel", "He Who Has Never Touched Grass",
          "Supreme Overlord of Checkers & Poor Sportsmanship"]),
]
RANKUP_LINES = {
    10: ["The checkerboard creaks warily when you sit down.", "Someone just lost a piece and their dignity."],
    25: ["Cracker Barrel waiting lists just got longer.", "You've made kinging a piece look threatening."],
    50: ["Tiny wooden discs fear you now.", "A grandparent somewhere just quit checkers forever."],
    100: ["The board is yours. It was never really anyone else's.",
         "Somewhere, a Cracker Barrel employee is drafting a ban."],
}

FLAVOR_JUMP = [
    "A piece is crushed to splinters!",
    "Captured — the board remembers.",
    "One fewer disc on the dark squares.",
]
FLAVOR_CROWN = [
    "Crowned — that piece answers to no one now.",
    "A king rises on the far row.",
]
FLAVOR_MOVE = [
    "A quiet slide across the board.",
    "The discs rearrange themselves.",
]


def today_str() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def blank_player() -> dict:
    return {"wins": 0, "losses": 0, "daily": {"date": today_str(), "count": 0}, "titles_seen": []}


def title_for(wins: int) -> list[str]:
    out = []
    for threshold, names in TITLE_TIERS:
        if wins >= threshold:
            out += names
    return out


def square_name(sq: tuple[int, int]) -> str:
    return board_square_name(sq)


def parse_square(name: str) -> Optional[tuple[int, int]]:
    name = name.strip().lower()
    if len(name) != 2 or name[0] not in "abcdefgh" or name[1] not in "12345678":
        return None
    return (ord(name[0]) - ord("a"), int(name[1]) - 1)


def is_dark(sq: tuple[int, int]) -> bool:
    return (sq[0] + sq[1]) % 2 == 0


def start_board() -> dict[tuple[int, int], str]:
    board = {}
    for rank in range(8):
        for file in range(8):
            sq = (file, rank)
            if not is_dark(sq):
                continue
            if rank <= 2:
                board[sq] = "r"
            elif rank >= 5:
                board[sq] = "b"
    return board


class Move:
    __slots__ = ("frm", "to", "captured")

    def __init__(self, frm, to, captured=None):
        self.frm, self.to, self.captured = frm, to, captured


def piece_moves(board: dict, sq: tuple[int, int], piece: str) -> tuple[list[Move], list[Move]]:
    file, rank = sq
    color = piece.lower()
    is_king = piece.isupper()
    if is_king:
        dirs = [(-1, 1), (1, 1), (-1, -1), (1, -1)]
    elif color == "r":
        dirs = [(-1, 1), (1, 1)]
    else:
        dirs = [(-1, -1), (1, -1)]
    simple, jumps = [], []
    for df, dr in dirs:
        f2, r2 = file + df, rank + dr
        if not (0 <= f2 < 8 and 0 <= r2 < 8):
            continue
        mid = (f2, r2)
        if mid not in board:
            simple.append(Move(sq, mid))
            continue
        if board[mid].lower() == color:
            continue
        f3, r3 = file + 2 * df, rank + 2 * dr
        if 0 <= f3 < 8 and 0 <= r3 < 8 and (f3, r3) not in board:
            jumps.append(Move(sq, (f3, r3), mid))
    return simple, jumps


def all_moves(board: dict, color: str) -> list[Move]:
    simples, jumps = [], []
    for sq, piece in board.items():
        if piece.lower() != color:
            continue
        s, j = piece_moves(board, sq, piece)
        simples += s
        jumps += j
    return jumps if jumps else simples


def apply_move(board: dict, move: Move) -> bool:
    """Applies the move in place. Returns True if the moved piece was just crowned."""
    piece = board.pop(move.frm)
    if move.captured:
        board.pop(move.captured, None)
    crowned = False
    if not piece.isupper():
        _, rank = move.to
        if (piece == "r" and rank == 7) or (piece == "b" and rank == 0):
            piece = piece.upper()
            crowned = True
    board[move.to] = piece
    return crowned


class Match:
    def __init__(self, red_id: int, black_id: int):
        self.board = start_board()
        self.red_id = red_id
        self.black_id = black_id
        self.turn = "r"
        self.chain_square: Optional[tuple[int, int]] = None
        self.last_move_at = time.time()
        self.message_id: Optional[int] = None
        self.channel_id: Optional[int] = None
        self.ping_message_id: Optional[int] = None
        self.last_from: Optional[tuple[int, int]] = None
        self.last_to: Optional[tuple[int, int]] = None
        self.last_spoken: Optional[str] = None
        self.last_flavor: Optional[str] = None

    def player_ids(self) -> set[int]:
        return {self.red_id, self.black_id}

    def turn_id(self) -> int:
        return self.red_id if self.turn == "r" else self.black_id

    def color_of(self, user_id: int) -> Optional[str]:
        if user_id == self.red_id:
            return "r"
        if user_id == self.black_id:
            return "b"
        return None


class ChallengeView(discord.ui.View):
    def __init__(self, cog: "Checkers", challenger_id: int, opponent_id: int,
                 message: discord.Message | None = None):
        super().__init__(timeout=CHALLENGE_TIMEOUT_SECONDS)
        self.cog = cog
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.message = message

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="🔴")
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

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(
                    content="🔴 Challenge expired (no response in 60 seconds).",
                    view=None,
                )
            except discord.HTTPException:
                pass


class EphemeralPieceSelect(discord.ui.Select):
    """Step 1 (ephemeral, turn-player only): pick a piece."""

    def __init__(self, cog: "Checkers", match_key: tuple[int, int], player_id: int,
                 choices: list[tuple[str, str]], page: int = 0):
        self.cog = cog
        self.match_key = match_key
        self.player_id = player_id
        self.page = page
        options = [discord.SelectOption(label=label, value=value) for label, value in choices]
        super().__init__(placeholder="Step 1 — pick a piece...", options=options,
                         min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        m = await self.cog._require_turn(interaction, self.match_key, self.player_id)
        if not m:
            return
        selected = parse_square(self.values[0])
        if selected is None:
            await interaction.response.send_message("That square isn't valid anymore.", ephemeral=True)
            return
        dests = self.cog._dest_choices(m, selected)
        await interaction.response.edit_message(
            content=(f"Moving from **{square_name(selected)}** — pick a square, "
                     f"or tap **Back** to choose a different piece."),
            view=EphemeralMoveView(self.cog, self.match_key, self.player_id,
                                   selected=selected, dest_choices=dests),
        )
        await self.cog.refresh_match_message(interaction, m, selected=selected,
                                             respond=False, notify=False)


class EphemeralDestSelect(discord.ui.Select):
    """Step 2 (ephemeral): pick a destination."""

    def __init__(self, cog: "Checkers", match_key: tuple[int, int], player_id: int,
                 selected: tuple[int, int], choices: list[tuple[str, str]]):
        self.cog = cog
        self.match_key = match_key
        self.player_id = player_id
        self.selected = selected
        options = [discord.SelectOption(label=label, value=value) for label, value in choices]
        super().__init__(placeholder="Step 2 — pick a square...", options=options,
                         min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        m = await self.cog._require_turn(interaction, self.match_key, self.player_id)
        if not m:
            return
        raw = self.values[0]
        fsq, tsq = parse_square(raw[:2]), parse_square(raw[2:4])
        match = next((mv for mv in self.cog._legal_moves(m)
                      if mv.frm == fsq and mv.to == tsq), None)
        if not match:
            await interaction.response.send_message(
                "That move isn't legal anymore — check the latest board.", ephemeral=True)
            return
        await self.cog.play_move(interaction, m, match, interaction.user, from_ephemeral=True)


class EphemeralMoveView(discord.ui.View):
    """Private move UI — only the player who opened it should use it."""

    def __init__(self, cog: "Checkers", match_key: tuple[int, int], player_id: int,
                 *, selected: Optional[tuple[int, int]] = None, page: int = 0,
                 piece_pages: Optional[list[list[tuple[str, str]]]] = None,
                 dest_choices: Optional[list[tuple[str, str]]] = None):
        super().__init__(timeout=300)
        self.cog = cog
        self.match_key = match_key
        self.player_id = player_id
        self.page = page
        self.piece_pages = piece_pages or []

        if selected is None:
            if piece_pages:
                page = max(0, min(page, len(piece_pages) - 1))
                self.page = page
                self.add_item(EphemeralPieceSelect(cog, match_key, player_id,
                                                   piece_pages[page], page=page))
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
                self.add_item(EphemeralDestSelect(cog, match_key, player_id, selected, dest_choices))
            # Mid-jump chains must stay on that piece — no Back.
            m = cog.matches.get(match_key)
            allow_back = not (m and m.chain_square is not None)
            if allow_back:
                back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary,
                                         emoji="↩️", row=1)

                async def _back(interaction: discord.Interaction):
                    cur = await self.cog._require_turn(interaction, self.match_key, self.player_id)
                    if not cur:
                        return
                    pages = self.cog._piece_pages(cur)
                    await interaction.response.edit_message(
                        content="Pick a piece to move:",
                        view=EphemeralMoveView(self.cog, self.match_key, self.player_id,
                                               selected=None, piece_pages=pages),
                    )
                    await self.cog.refresh_match_message(interaction, cur, selected=None,
                                                         respond=False, notify=False)

                back.callback = _back
                self.add_item(back)

    async def _page(self, interaction: discord.Interaction, delta: int):
        m = await self.cog._require_turn(interaction, self.match_key, self.player_id)
        if not m:
            return
        pages = self.piece_pages or self.cog._piece_pages(m)
        new_page = max(0, min(self.page + delta, len(pages) - 1))
        await interaction.response.edit_message(
            content="Pick a piece to move:",
            view=EphemeralMoveView(self.cog, self.match_key, self.player_id,
                                   selected=None, page=new_page, piece_pages=pages),
        )


class BoardView(discord.ui.View):
    """Public board controls. Move picking is ephemeral so off-turn players can't use it."""

    def __init__(self, cog: "Checkers", match_key: tuple[int, int]):
        super().__init__(timeout=None)
        self.cog = cog
        self.match_key = match_key

        move_btn = discord.ui.Button(label="Make move", style=discord.ButtonStyle.primary,
                                     emoji="🔴", row=0)
        resign_btn = discord.ui.Button(label="Resign", style=discord.ButtonStyle.danger, row=0)

        async def _make_move(interaction: discord.Interaction):
            m = self.cog.matches.get(self.match_key)
            if not m:
                await interaction.response.send_message("This match has ended.", ephemeral=True)
                return
            if interaction.user.id not in m.player_ids():
                await interaction.response.send_message("You're not in this match.", ephemeral=True)
                return
            if interaction.user.id != m.turn_id():
                await interaction.response.send_message(
                    "It's not your turn — you can't pick a piece right now.", ephemeral=True)
                return
            pages = self.cog._piece_pages(m)
            if not pages:
                await interaction.response.send_message("No legal moves.", ephemeral=True)
                return
            # Mid-jump: skip straight to destinations for the chained piece.
            if m.chain_square is not None:
                dests = self.cog._dest_choices(m, m.chain_square)
                await interaction.response.send_message(
                    content=(f"Keep jumping with **{square_name(m.chain_square)}** — "
                             f"pick the next square:"),
                    view=EphemeralMoveView(self.cog, self.match_key, interaction.user.id,
                                           selected=m.chain_square, dest_choices=dests),
                    ephemeral=True,
                )
                await self.cog.refresh_match_message(interaction, m, selected=m.chain_square,
                                                     respond=False, notify=False)
                return
            await interaction.response.send_message(
                content="Pick a piece to move:",
                view=EphemeralMoveView(self.cog, self.match_key, interaction.user.id,
                                       selected=None, piece_pages=pages),
                ephemeral=True,
            )

        async def _resign(interaction: discord.Interaction):
            await self.cog.resign_from_view(interaction, self.match_key)

        move_btn.callback = _make_move
        resign_btn.callback = _resign
        self.add_item(move_btn)
        self.add_item(resign_btn)


class Checkers(commands.Cog):
    group = app_commands.Group(name="checkers", description="Wizard's Checkers.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self.matches: dict[tuple[int, int], Match] = {}

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
        return interaction.channel_id == CHECKERS_CHANNEL_ID

    # -------------------------------------------------------- move menus

    def _legal_moves(self, m: Match) -> list[Move]:
        moves = all_moves(m.board, m.turn)
        if m.chain_square is not None:
            moves = [mv for mv in moves if mv.frm == m.chain_square]
        return moves

    def _piece_pages(self, m: Match) -> list[list[tuple[str, str]]]:
        by_sq: dict[tuple[int, int], str] = {}
        for mv in self._legal_moves(m):
            piece = m.board.get(mv.frm)
            if piece:
                by_sq[mv.frm] = piece
        choices = []
        for sq in sorted(by_sq, key=lambda s: (s[1], s[0])):
            label = f"{piece_label(by_sq[sq])} {square_name(sq)}"
            choices.append((label, square_name(sq)))
        if not choices:
            return []
        return [choices[i:i + 25] for i in range(0, len(choices), 25)]

    def _dest_choices(self, m: Match, from_sq: tuple[int, int]) -> list[tuple[str, str]]:
        out = []
        for mv in self._legal_moves(m):
            if mv.frm != from_sq:
                continue
            label = square_name(mv.to)
            if mv.captured:
                label += " (jump!)"
            out.append((label, f"{square_name(mv.frm)}{square_name(mv.to)}"))
        seen = set()
        unique = []
        for label, value in out:
            if label in seen:
                label = f"{label} ({value})"
            seen.add(label)
            unique.append((label, value))
        return unique[:25]

    def _board_file(self, m: Match, selected: Optional[tuple[int, int]] = None) -> discord.File:
        dests = None
        if selected is not None:
            dests = [mv.to for mv in self._legal_moves(m) if mv.frm == selected]
        png = render_board(
            m.board,
            last_from=m.last_from,
            last_to=m.last_to,
            selected=selected,
            destinations=dests,
        )
        return discord.File(io.BytesIO(png), filename="checkers_board.png")

    def _match_embed(self, interaction: discord.Interaction, m: Match, *,
                     ended: Optional[str] = None) -> discord.Embed:
        red = interaction.guild.get_member(m.red_id) if interaction.guild else None
        black = interaction.guild.get_member(m.black_id) if interaction.guild else None
        r_name = red.display_name if red else str(m.red_id)
        b_name = black.display_name if black else str(m.black_id)
        lines = [f"🔴 **{r_name}** (Red)  ⚔️  ⚫ **{b_name}** (Black)"]
        if m.last_spoken:
            lines.append(f"Last move: **{m.last_spoken}**")
            if m.last_flavor:
                lines.append(f"*{m.last_flavor}*")
        if ended:
            lines.append(ended)
        elif m.chain_square is not None:
            turn = red if m.turn_id() == m.red_id else black
            turn_name = turn.display_name if turn else str(m.turn_id())
            lines.append(
                f"{turn_name} must keep jumping with **{square_name(m.chain_square)}** — "
                f"pick the next square. Or `/checkers move`."
            )
        else:
            turn = red if m.turn_id() == m.red_id else black
            turn_name = turn.display_name if turn else str(m.turn_id())
            lines.append(f"{turn_name} to move — pick a piece, then a square. Or `/checkers move`.")
        color = 0x2ECC71 if ended and "wins" in ended.lower() else (
            0x7A7A7A if ended else 0xC0392B)
        embed = discord.Embed(
            title=f"🔴 Wizard's Checkers — {r_name} ⚔️ {b_name}",
            description="\n".join(lines),
            color=color,
        )
        embed.set_image(url="attachment://checkers_board.png")
        return embed

    def _match_view(self, m: Match) -> BoardView:
        return BoardView(self, self._key(m.red_id, m.black_id))

    async def _require_turn(self, interaction: discord.Interaction, match_key: tuple[int, int],
                            player_id: int) -> Optional[Match]:
        if interaction.user.id != player_id:
            await interaction.response.send_message("It's not your move.", ephemeral=True)
            return None
        m = self.matches.get(match_key)
        if not m:
            await interaction.response.send_message("This match has ended.", ephemeral=True)
            return None
        if m.turn_id() != player_id:
            await interaction.response.send_message(
                "It's not your turn — you can't pick a piece right now.", ephemeral=True)
            return None
        return m

    async def _notify_turn(self, channel, m: Match, *, ended: Optional[str] = None):
        """Post a fresh @mention message. Editing an existing message does not notify."""
        if channel is None:
            return
        if m.ping_message_id:
            try:
                old = await channel.fetch_message(m.ping_message_id)
                await old.delete()
            except discord.DiscordException:
                pass
            m.ping_message_id = None
        if ended:
            return
        try:
            msg = await channel.send(
                f"<@{m.turn_id()}> — your move.",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            m.ping_message_id = msg.id
        except discord.DiscordException:
            log.exception("Could not send checkers turn ping")

    async def refresh_match_message(self, interaction: discord.Interaction, m: Match, *,
                                    selected: Optional[tuple[int, int]] = None,
                                    respond: bool = True, ended: Optional[str] = None,
                                    notify: bool = False):
        """Redraw the match board message (edit in place)."""
        file = self._board_file(m, selected=selected if not ended else None)
        embed = self._match_embed(interaction, m, ended=ended)
        view = None if ended else self._match_view(m)
        channel = interaction.channel
        if m.channel_id:
            channel = self.bot.get_channel(m.channel_id) or channel

        if respond and not interaction.response.is_done():
            await interaction.response.edit_message(
                content=None, embed=embed, attachments=[file], view=view)
            try:
                msg = await interaction.original_response()
                m.message_id = msg.id
                m.channel_id = msg.channel.id
                channel = msg.channel
            except discord.DiscordException:
                pass
        elif channel and m.message_id:
            try:
                msg = await channel.fetch_message(m.message_id)
                await msg.edit(content=None, embed=embed, attachments=[file], view=view)
            except discord.DiscordException:
                log.exception("Could not edit checkers board message")
                if channel:
                    msg = await channel.send(embed=embed, file=file, view=view)
                    m.message_id = msg.id
                    m.channel_id = msg.channel.id
        elif channel:
            msg = await channel.send(embed=embed, file=file, view=view)
            m.message_id = msg.id
            m.channel_id = msg.channel.id

        if notify or ended:
            await self._notify_turn(channel, m, ended=ended)

    # ------------------------------------------------------------- rewards

    async def _award_and_record(self, channel, winner_id: Optional[int], loser_id: int, reason: str):
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
                                target_id=winner_id, reason=f"Wizard's Checkers: won by {reason}")
                    winner_rec["daily"]["count"] += 1
                    paid = True
            self.save()

            for threshold, names in TITLE_TIERS:
                if winner_rec["wins"] >= threshold and threshold not in winner_rec["titles_seen"]:
                    winner_rec["titles_seen"].append(threshold)
                    line = random.choice(RANKUP_LINES[threshold])
                    lines.append(f"🏆 **{winner_member.display_name if winner_member else winner_id}** just "
                                f"unlocked a new tier of Checkers titles ({', '.join(names)})! {line}")
            self.save()
            if paid and winner_member:
                lines.insert(0, f"+{POINTS_PER_WIN} House Cup point to {winner_member.display_name}'s house.")
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
            channel = self.bot.get_channel(CHECKERS_CHANNEL_ID)
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
                log.exception("Could not post a checkers timeout forfeit")

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
        red_id, black_id = (challenger_id, opponent_id) if random.random() < 0.5 else (opponent_id, challenger_id)
        m = Match(red_id, black_id)
        self.matches[key] = m
        await self.refresh_match_message(interaction, m, selected=None, respond=True, notify=True)

    @group.command(name="challenge", description="Challenge someone to a game of Wizard's Checkers.")
    @app_commands.describe(member="Who to challenge")
    async def challenge(self, interaction: discord.Interaction, member: discord.Member):
        if not self._in_channel(interaction):
            await interaction.response.send_message(f"Checkers only runs in <#{CHECKERS_CHANNEL_ID}>.",
                                                     ephemeral=True)
            return
        if member.bot or member.id == interaction.user.id:
            await interaction.response.send_message("Pick a real opponent.", ephemeral=True)
            return
        if self.match_between(interaction.user.id, member.id):
            await interaction.response.send_message("You two already have a match in progress.", ephemeral=True)
            return
        view = ChallengeView(self, interaction.user.id, member.id)
        await interaction.response.send_message(
            f"🔴 {member.mention}, {interaction.user.display_name} challenges you to Wizard's Checkers!\n"
            f"*(Expires in {CHALLENGE_TIMEOUT_SECONDS} seconds.)*",
            view=view)
        view.message = await interaction.original_response()

    # ------------------------------------------------------------- moving

    def _flavor_for(self, jumped: bool, crowned: bool) -> str:
        if crowned:
            return random.choice(FLAVOR_CROWN)
        if jumped:
            return random.choice(FLAVOR_JUMP)
        if random.random() < 0.35:
            return random.choice(FLAVOR_MOVE)
        return ""

    async def play_move(self, interaction: discord.Interaction, m: Match, match: Move, mover,
                        *, from_ephemeral: bool = False):
        """Applies a legal move/jump and refreshes the board message."""
        piece_before = m.board.get(match.frm, "?")
        my_color = m.turn
        crowned = apply_move(m.board, match)
        m.last_move_at = time.time()
        m.last_from, m.last_to = match.frm, match.to
        m.last_spoken = describe_move(
            piece_before, match.frm, match.to, jumped=bool(match.captured), crowned=crowned)
        m.last_flavor = self._flavor_for(bool(match.captured), crowned) or None

        async def _ack(text: str):
            if interaction.response.is_done():
                return
            if from_ephemeral:
                await interaction.response.edit_message(content=text, view=None)
            else:
                await interaction.response.send_message(text, ephemeral=True)

        continue_chain = False
        if match.captured and not crowned:
            _, further_jumps = piece_moves(m.board, match.to, m.board[match.to])
            if further_jumps:
                continue_chain = True

        if continue_chain:
            m.chain_square = match.to
            await _ack(f"Played **{m.last_spoken}**. Keep jumping!")
            await self.refresh_match_message(interaction, m, selected=m.chain_square,
                                             respond=False, notify=True)
            return

        m.chain_square = None
        m.turn = "b" if my_color == "r" else "r"

        if not all_moves(m.board, m.turn):
            self.matches.pop(self._key(m.red_id, m.black_id), None)
            loser_id = m.turn_id()
            winner_id = m.red_id if loser_id == m.black_id else m.black_id
            winner = interaction.guild.get_member(winner_id) if interaction.guild else None
            w_name = winner.display_name if winner else str(winner_id)
            ended = f"🏆 **{w_name} wins** — no legal moves left for the other side."
            await _ack(f"Played **{m.last_spoken}**. {w_name} wins.")
            await self.refresh_match_message(interaction, m, ended=ended,
                                             respond=False, notify=True)
            await self._award_and_record(interaction.channel, winner_id, loser_id, "no legal moves")
            return

        await _ack(f"Played **{m.last_spoken}**.")
        await self.refresh_match_message(interaction, m, selected=None,
                                         respond=False, notify=True)

    @group.command(name="move",
                   description="Make a move by typing squares (or pick piece → square on the board).")
    @app_commands.describe(opponent="Who you're playing", from_square="Square to move from (e.g. b6)",
                           to_square="Square to move to (e.g. c5)")
    async def move(self, interaction: discord.Interaction, opponent: discord.Member,
                   from_square: str, to_square: str):
        if not self._in_channel(interaction):
            await interaction.response.send_message(f"Checkers only runs in <#{CHECKERS_CHANNEL_ID}>.",
                                                     ephemeral=True)
            return
        m = self.match_between(interaction.user.id, opponent.id)
        if not m:
            await interaction.response.send_message("You don't have a match against them right now.",
                                                     ephemeral=True)
            return
        my_color = m.color_of(interaction.user.id)
        if m.turn != my_color:
            await interaction.response.send_message("It's not your move.", ephemeral=True)
            return

        fsq, tsq = parse_square(from_square), parse_square(to_square)
        if fsq is None or tsq is None:
            await interaction.response.send_message("That's not a real square (use e.g. `b6`).", ephemeral=True)
            return
        if m.chain_square is not None and fsq != m.chain_square:
            await interaction.response.send_message(
                f"You're mid-jump - you have to keep capturing with the piece on "
                f"**{square_name(m.chain_square)}**.", ephemeral=True)
            return

        match = next((mv for mv in self._legal_moves(m) if mv.frm == fsq and mv.to == tsq), None)
        if not match:
            await interaction.response.send_message("That's not a legal move right now.", ephemeral=True)
            return

        await self.play_move(interaction, m, match, interaction.user)

    @move.autocomplete("from_square")
    async def _from_autocomplete(self, interaction: discord.Interaction, current: str):
        opponent = interaction.namespace.opponent
        if not opponent:
            return []
        m = self.match_between(interaction.user.id, opponent.id)
        if not m or m.color_of(interaction.user.id) != m.turn:
            return []
        if m.chain_square is not None:
            squares = [m.chain_square]
        else:
            squares = sorted({mv.frm for mv in all_moves(m.board, m.turn)})
        out = []
        for sq in squares:
            name = square_name(sq)
            if current.lower() in name:
                piece = m.board.get(sq, "")
                out.append(app_commands.Choice(name=f"{name} ({piece_label(piece)})", value=name))
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
        fsq = parse_square(from_square)
        if fsq is None:
            return []
        out = []
        for mv in self._legal_moves(m):
            if mv.frm != fsq:
                continue
            name = square_name(mv.to)
            if current.lower() in name and name not in [o.value for o in out]:
                label = f"{name} (jump!)" if mv.captured else name
                out.append(app_commands.Choice(name=label, value=name))
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
        winner_id = m.black_id if interaction.user.id == m.red_id else m.red_id
        self.matches.pop(match_key, None)
        winner = interaction.guild.get_member(winner_id) if interaction.guild else None
        w_name = winner.display_name if winner else str(winner_id)
        ended = f"🏳️ {interaction.user.display_name} resigns. {w_name} wins."
        await self.refresh_match_message(interaction, m, ended=ended, respond=True, notify=True)
        await self._award_and_record(interaction.channel, winner_id, interaction.user.id, "resignation")

    @group.command(name="resign", description="Concede a match in progress.")
    @app_commands.describe(opponent="Who you're conceding to")
    async def resign(self, interaction: discord.Interaction, opponent: discord.Member):
        if not self._in_channel(interaction):
            await interaction.response.send_message(f"Checkers only runs in <#{CHECKERS_CHANNEL_ID}>.",
                                                     ephemeral=True)
            return
        m = self.match_between(interaction.user.id, opponent.id)
        if not m:
            await interaction.response.send_message("You don't have a match against them right now.",
                                                     ephemeral=True)
            return
        self.matches.pop(self._key(m.red_id, m.black_id), None)
        ended = f"🏳️ {interaction.user.display_name} resigns. {opponent.display_name} wins."
        await interaction.response.send_message(embed=discord.Embed(description=ended, color=0xC0392B))
        await self.refresh_match_message(interaction, m, ended=ended, respond=False, notify=True)
        await self._award_and_record(interaction.channel, opponent.id, interaction.user.id, "resignation")

    # -------------------------------------------------------------- stats

    @app_commands.command(name="checkersstats", description="Wins, losses, title, and active games.")
    @app_commands.describe(member="Whose stats to show (leave blank for your own)")
    async def checkersstats(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        if not self._in_channel(interaction):
            await interaction.response.send_message(f"Checkers only runs in <#{CHECKERS_CHANNEL_ID}>.",
                                                     ephemeral=True)
            return
        target = member or interaction.user
        rec = self.record(target.id)
        adorn = self.bot.get_cog("Adornments")
        title = adorn.title_of(target) if adorn else None

        active = self.matches_of(target.id)
        active_lines = []
        for m in active:
            opp_id = m.black_id if target.id == m.red_id else m.red_id
            opp = interaction.guild.get_member(opp_id)
            turn = "your move" if m.turn_id() == target.id else "their move"
            active_lines.append(f"vs {opp.display_name if opp else opp_id} ({turn})")

        desc = [f"**{rec['wins']}W - {rec['losses']}L**"]
        if title:
            desc.append(f"Title: **{title}**")
        desc.append(f"Point-earning wins today: {rec['daily']['count']}/{DAILY_WIN_CAP}")
        embed = discord.Embed(title=f"{target.display_name}'s Checkers record", description="\n".join(desc),
                              color=0xC0392B)
        embed.add_field(name="Active games", value="\n".join(active_lines) if active_lines else "*None right now.*",
                        inline=False)
        await interaction.response.send_message(embed=embed)

    # --------------------------------------------------------------- staff

    @group.command(name="reset", description="(staff) Wipe someone's checkers record.")
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
        await interaction.response.send_message(f"{member.display_name}'s checkers record has been wiped, and any "
                                                 f"of their active matches were dropped.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Checkers(bot))
