"""Tables, per-player tokens, and the on-disk duel record.

Raw tokens live in memory so a restart can keep an already-shared link
working (only the hash is stored) and so "My table" can show it again.
After a restart the raw token is gone; asking for the link mints a new
one and the previous link stops working.

Nothing in here logs a token. Callers must not either.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import secrets
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cogs.vcc.cpu import CPU_ID, TUTOR_NAME, pick_tutor_house, run_cpu
from cogs.vcc.engine import Game

log = logging.getLogger("velmora.vcc")

TOMBSTONE_SECONDS = 600


def mint_token() -> tuple[str, str]:
    """Return ``(raw, sha256)``. Only the hash is written to disk."""
    raw = secrets.token_urlsafe(32)
    return raw, digest(raw)


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class Challenge:
    id: str
    channel_id: int
    guild_id: int
    message_id: int | None
    challenger_id: int
    challenger_name: str
    challenger_house: str
    opponent_id: int
    opponent_name: str
    opponent_house: str
    created: float
    deadline: float
    wager: int = 0
    mode: str = "card"  # "wager" during a wager night, otherwise "card"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "message_id": self.message_id,
            "challenger_id": self.challenger_id,
            "challenger_name": self.challenger_name,
            "challenger_house": self.challenger_house,
            "opponent_id": self.opponent_id,
            "opponent_name": self.opponent_name,
            "opponent_house": self.opponent_house,
            "created": self.created,
            "deadline": self.deadline,
            "wager": int(self.wager),
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Challenge":
        return cls(
            id=data["id"],
            channel_id=int(data["channel_id"]),
            guild_id=int(data["guild_id"]),
            message_id=int(data["message_id"]) if data.get("message_id") else None,
            challenger_id=int(data["challenger_id"]),
            challenger_name=data["challenger_name"],
            challenger_house=data["challenger_house"],
            opponent_id=int(data["opponent_id"]),
            opponent_name=data["opponent_name"],
            opponent_house=data["opponent_house"],
            created=float(data["created"]),
            deadline=float(data["deadline"]),
            wager=int(data.get("wager") or 0),
            mode=data.get("mode") or "card",
        )


@dataclass
class Table:
    id: str
    channel_id: int
    guild_id: int
    message_id: int | None
    created: float
    deadline: float
    tokens: dict[str, int]  # digest -> player id
    game: Game
    end_seen: bool = False
    points_applied: bool = False
    announced: bool = False
    points: int = 0
    point_note: str = ""
    settling: bool = False
    wager: int = 0
    mode: str = "card"
    prize: dict | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "message_id": self.message_id,
            "created": self.created,
            "deadline": self.deadline,
            "tokens": {digest_hex: int(pid) for digest_hex, pid in self.tokens.items()},
            "game": self.game.to_dict(),
            "end_seen": self.end_seen,
            "points_applied": self.points_applied,
            "announced": self.announced,
            "points": self.points,
            "point_note": self.point_note,
            "wager": int(self.wager),
            "mode": self.mode,
            "prize": self.prize,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Table":
        return cls(
            id=data["id"],
            channel_id=int(data["channel_id"]),
            guild_id=int(data["guild_id"]),
            message_id=int(data["message_id"]) if data.get("message_id") else None,
            created=float(data["created"]),
            deadline=float(data["deadline"]),
            tokens={str(k): int(v) for k, v in (data.get("tokens") or {}).items()},
            game=Game.from_dict(data["game"]),
            end_seen=bool(data.get("end_seen")),
            points_applied=bool(data.get("points_applied")),
            announced=bool(data.get("announced")),
            points=int(data.get("points") or 0),
            point_note=data.get("point_note") or "",
            wager=int(data.get("wager") or 0),
            mode=data.get("mode") or "card",
            prize=data.get("prize") if isinstance(data.get("prize"), dict) else None,
        )


@dataclass
class Action:
    status: int
    error: str | None = None
    notice: str | None = None
    view: dict | None = None
    game_id: str | None = None
    just_ended: bool = False
    pushes: list = field(default_factory=list)  # [(digest, view)]


class Book:
    def __init__(self, path: Path, *, turn_timeout: float = 240, accept_timeout: float = 120):
        self.path = path
        self.turn_timeout = turn_timeout
        self.accept_timeout = accept_timeout
        self.challenges: dict[str, Challenge] = {}
        self.games: dict[str, Table] = {}
        self.by_user: dict[int, tuple[str, str]] = {}
        self.by_digest: dict[str, str] = {}
        self.raw_memory: dict[tuple[str, int], str] = {}
        self.tombstones: dict[str, dict] = {}
        self.spellbook: dict[str, dict[str, int]] = {}
        self.wager_night: dict | None = None
        self.load()

    # ---------------------------------------------------------------- disk

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read VCC state at %s", self.path)
            return
        for raw in (data.get("challenges") or {}).values():
            try:
                challenge = Challenge.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                continue
            self.challenges[challenge.id] = challenge
            self.by_user[challenge.challenger_id] = ("challenge", challenge.id)
            self.by_user[challenge.opponent_id] = ("challenge", challenge.id)
        for raw in (data.get("games") or {}).values():
            try:
                table = Table.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                log.exception("Skipped an unreadable VCC table")
                continue
            self.games[table.id] = table
            for pid in table.game.order:
                self.by_user[pid] = ("game", table.id)
            for token_hash, pid in table.tokens.items():
                self.by_digest[token_hash] = table.id
        self.tombstones = dict(data.get("tombstones") or {})
        self.spellbook = _clean_spellbook(data.get("spellbook") or {})
        night = data.get("wager_night")
        self.wager_night = dict(night) if isinstance(night, dict) else None

    def save(self) -> None:
        payload = {
            "version": 1,
            "challenges": {cid: c.to_dict() for cid, c in self.challenges.items()},
            "games": {gid: g.to_dict() for gid, g in self.games.items()},
            "tombstones": self.tombstones,
            "spellbook": self.spellbook,
            "wager_night": self.wager_night,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Could not write VCC state to %s", self.path)

    # ---------------------------------------------------------------- busy

    def busy(self, user_id: int) -> str | None:
        """``challenge``, ``game``, or None."""
        loc = self.by_user.get(int(user_id))
        if not loc:
            return None
        kind, ident = loc
        if kind == "challenge" and ident in self.challenges:
            return "challenge"
        if kind == "game" and ident in self.games and not self.games[ident].announced:
            game = self.games[ident].game
            if game.winner is None or not self.games[ident].announced:
                return "game"
        return None

    def in_match(self, user_id: int) -> bool:
        loc = self.by_user.get(int(user_id))
        if not loc:
            return False
        kind, ident = loc
        if kind == "challenge":
            return ident in self.challenges
        if kind == "game":
            table = self.games.get(ident)
            return bool(table and table.game.winner is None)
        return False

    # ---------------------------------------------------------------- challenges

    def open_challenge(
        self,
        *,
        channel_id: int,
        guild_id: int,
        challenger_id: int,
        challenger_name: str,
        challenger_house: str,
        opponent_id: int,
        opponent_name: str,
        opponent_house: str,
        wager: int = 0,
        mode: str = "card",
        now: float | None = None,
    ) -> Challenge:
        now = time.time() if now is None else now
        challenge = Challenge(
            id=uuid.uuid4().hex,
            channel_id=channel_id,
            guild_id=guild_id,
            message_id=None,
            challenger_id=challenger_id,
            challenger_name=challenger_name,
            challenger_house=challenger_house,
            opponent_id=opponent_id,
            opponent_name=opponent_name,
            opponent_house=opponent_house,
            created=now,
            deadline=now + self.accept_timeout,
            wager=int(wager),
            mode=mode if mode in ("card", "wager") else "card",
        )
        self.challenges[challenge.id] = challenge
        self.by_user[challenger_id] = ("challenge", challenge.id)
        self.by_user[opponent_id] = ("challenge", challenge.id)
        self.save()
        return challenge

    def drop_challenge(self, challenge_id: str) -> Challenge | None:
        challenge = self.challenges.pop(challenge_id, None)
        if not challenge:
            return None
        for pid in (challenge.challenger_id, challenge.opponent_id):
            loc = self.by_user.get(pid)
            if loc and loc == ("challenge", challenge.id):
                self.by_user.pop(pid, None)
        self.save()
        return challenge

    def expire_challenges(self, now: float) -> list[Challenge]:
        gone = [c for c in self.challenges.values() if now >= c.deadline]
        for challenge in gone:
            self.drop_challenge(challenge.id)
        return gone

    # ---------------------------------------------------------------- games

    def start_game(self, challenge: Challenge, *, now: float | None = None, seed: int | None = None) -> dict[int, str]:
        """Begin the duel. Returns ``{player_id: raw_token}`` once."""
        now = time.time() if now is None else now
        self.challenges.pop(challenge.id, None)
        for pid in (challenge.challenger_id, challenge.opponent_id):
            loc = self.by_user.get(pid)
            if loc and loc[0] == "challenge":
                self.by_user.pop(pid, None)
        game = Game.new(
            challenger_id=challenge.challenger_id,
            challenger_name=challenge.challenger_name,
            challenger_house=challenge.challenger_house,
            opponent_id=challenge.opponent_id,
            opponent_name=challenge.opponent_name,
            opponent_house=challenge.opponent_house,
            seed=seed,
        )
        table = Table(
            id=challenge.id,
            channel_id=challenge.channel_id,
            guild_id=challenge.guild_id,
            message_id=challenge.message_id,
            created=now,
            deadline=now + self.turn_timeout,
            tokens={},
            game=game,
            wager=int(challenge.wager),
            mode=challenge.mode if challenge.mode in ("card", "wager") else "card",
        )
        raws: dict[int, str] = {}
        for pid in game.order:
            raw, token_hash = mint_token()
            table.tokens[token_hash] = pid
            self.by_digest[token_hash] = table.id
            self.raw_memory[(table.id, pid)] = raw
            raws[pid] = raw
            self.by_user[pid] = ("game", table.id)
        self.games[table.id] = table
        self.save()
        return raws

    def start_practice(
        self,
        *,
        channel_id: int,
        guild_id: int,
        player_id: int,
        player_name: str,
        player_house: str,
        now: float | None = None,
        seed: int | None = None,
    ) -> str:
        """Open a practice table. Returns the human's raw token once.

        The tutor has no token and is not stored as a user, so a second
        practice table can still open for someone else.
        """
        now = time.time() if now is None else now
        house = player_house if player_house else ""
        tutor_house = pick_tutor_house(house, random.Random(seed if seed is not None else now))
        game = Game.new(
            challenger_id=player_id,
            challenger_name=player_name,
            challenger_house=house,
            opponent_id=CPU_ID,
            opponent_name=TUTOR_NAME,
            opponent_house=tutor_house,
            seed=seed,
            practice=True,
            cpu_id=CPU_ID,
        )
        table = Table(
            id=uuid.uuid4().hex,
            channel_id=channel_id,
            guild_id=guild_id,
            message_id=None,
            created=now,
            deadline=now + self.turn_timeout,
            tokens={},
            game=game,
            mode="practice",
            wager=0,
        )
        raw, token_hash = mint_token()
        table.tokens[token_hash] = player_id
        self.by_digest[token_hash] = table.id
        self.raw_memory[(table.id, player_id)] = raw
        self.by_user[player_id] = ("game", table.id)
        self.games[table.id] = table
        self.save()
        return raw

    def abandon_practice(self, user_id: int) -> None:
        """Drop an unstarted practice table when Discord never took the post."""
        uid = int(user_id)
        loc = self.by_user.get(uid)
        if not loc or loc[0] != "game":
            return
        table = self.games.get(loc[1])
        if table is None or table.mode != "practice" or table.game.winner is not None:
            return
        for token_hash, pid in list(table.tokens.items()):
            self.by_digest.pop(token_hash, None)
            self.raw_memory.pop((table.id, pid), None)
        self.games.pop(table.id, None)
        if self.by_user.get(uid) == ("game", table.id):
            self.by_user.pop(uid, None)
        self.save()

    def remember_raw(self, game_id: str, user_id: int) -> str | None:
        return self.raw_memory.get((game_id, int(user_id)))

    def active_game_for(self, user_id: int) -> Table | None:
        loc = self.by_user.get(int(user_id))
        if not loc or loc[0] != "game":
            return None
        table = self.games.get(loc[1])
        if not table or table.game.winner is not None:
            return None
        return table

    def rotate_link(self, user_id: int) -> str | None:
        """Mint a replacement token. The previous link stops working."""
        table = self.active_game_for(user_id)
        if table is None:
            return None
        stale = [token_hash for token_hash, pid in table.tokens.items() if pid == user_id]
        for token_hash in stale:
            table.tokens.pop(token_hash, None)
            self.by_digest.pop(token_hash, None)
            self.tombstones.pop(token_hash, None)
        raw, token_hash = mint_token()
        table.tokens[token_hash] = user_id
        self.by_digest[token_hash] = table.id
        self.raw_memory[(table.id, user_id)] = raw
        self.save()
        return raw

    def _locate(self, raw: str) -> tuple[Table | None, int | None, dict | None]:
        token_hash = digest(raw)
        game_id = self.by_digest.get(token_hash)
        if game_id and game_id in self.games:
            table = self.games[game_id]
            pid = table.tokens.get(token_hash)
            if pid is not None:
                return table, pid, None
        stone = self.tombstones.get(token_hash)
        if stone:
            return None, None, stone
        return None, None, None

    def _finish_cpu_turn(self, table: Table, now: float) -> bool:
        """Let the tutor act when the turn is theirs. True if the duel ended."""
        if table.mode != "practice" or table.game.winner is not None:
            return False
        if table.game.current_id != CPU_ID:
            return False
        run_cpu(table.game)
        ended = self._mark_end(table)
        if table.game.winner is None:
            self._touch(table, now)
        self.save()
        return ended

    def _mark_end(self, table: Table) -> bool:
        if table.game.winner is None or table.end_seen:
            return False
        table.end_seen = True
        return True

    def _touch(self, table: Table, now: float) -> None:
        if table.game.winner is None:
            table.deadline = now + self.turn_timeout

    def view_action(self, raw: str, *, now: float) -> Action:
        table, pid, stone = self._locate(raw)
        if stone:
            return Action(status=200, view=_tombstone_view(stone), game_id=stone.get("game_id"))
        if table is None or pid is None:
            return Action(status=404, error="This table link doesn't work.")
        self._finish_cpu_turn(table, now)
        return Action(
            status=200,
            view=table.game.view_for(pid, now=now, deadline=table.deadline),
            game_id=table.id,
            pushes=self.live_views(table, now),
        )

    def _act(self, raw: str, *, now: float, fn) -> Action:
        table, pid, stone = self._locate(raw)
        if stone:
            return Action(status=410, error="The duel is over.", view=_tombstone_view(stone))
        if table is None or pid is None:
            return Action(status=404, error="This table link doesn't work.")
        move = fn(table.game, pid)
        if move.error:
            return Action(
                status=400,
                error=move.error,
                view=table.game.view_for(pid, now=now, deadline=table.deadline),
                game_id=table.id,
            )
        just_ended = self._mark_end(table)
        if not just_ended:
            just_ended = self._finish_cpu_turn(table, now)
        if not just_ended and table.game.winner is None:
            self._touch(table, now)
        self.save()
        return Action(
            status=200,
            notice=move.notice,
            view=table.game.view_for(pid, now=now, deadline=table.deadline),
            game_id=table.id,
            just_ended=just_ended,
            pushes=self.live_views(table, now),
        )

    def play(self, raw: str, card_id: str, *, now: float) -> Action:
        return self._act(raw, now=now, fn=lambda game, pid: game.play(pid, card_id))

    def end_turn(self, raw: str, *, now: float) -> Action:
        return self._act(raw, now=now, fn=lambda game, pid: game.end_turn(pid))

    def discard(self, raw: str, card_ids: list[str], *, now: float) -> Action:
        return self._act(raw, now=now, fn=lambda game, pid: game.discard(pid, card_ids))

    def choose(self, raw: str, keep_id: str, *, now: float) -> Action:
        return self._act(raw, now=now, fn=lambda game, pid: game.choose(pid, keep_id))

    def forfeit(self, raw: str, *, now: float) -> Action:
        return self._act(raw, now=now, fn=lambda game, pid: game.forfeit(pid))

    def timeout_games(self, now: float) -> list[tuple[Table, list]]:
        """End stalled turns. Returns ``(table, pushes)`` for tables that just ended."""
        ended: list[tuple[Table, list]] = []
        for table in list(self.games.values()):
            if table.game.winner is not None:
                continue
            if table.mode == "practice" and table.game.current_id == CPU_ID:
                if self._finish_cpu_turn(table, now):
                    ended.append((table, self.live_views(table, now)))
                continue
            if now < table.deadline:
                continue
            table.game.timeout()
            if self._mark_end(table):
                pushes = self.live_views(table, now)
                self.save()
                ended.append((table, pushes))
        return ended

    def live_views(self, table: Table, now: float) -> list[tuple[str, dict]]:
        pairs = []
        for token_hash, pid in table.tokens.items():
            pairs.append((
                token_hash,
                table.game.view_for(pid, now=now, deadline=table.deadline),
            ))
        return pairs

    def expire_tokens(self, table: Table, *, now: float | None = None) -> list[str]:
        """Drop play rights. Digests become result-only tombstones."""
        now = time.time() if now is None else now
        game = table.game
        if game.winner is None:
            return []
        summary = game.view_for(game.order[0]).get("summary") if game.order else None
        digests = list(table.tokens.keys())
        for token_hash, pid in list(table.tokens.items()):
            you = game.players[pid]
            opp = game.other(pid)
            self.tombstones[token_hash] = {
                "game_id": table.id,
                "you_won": game.winner == pid,
                "summary": summary,
                "reason": game.reason,
                "you_name": you.name,
                "opp_name": opp.name,
                "you_ward": you.ward,
                "opp_ward": opp.ward,
                "you_house": you.house,
                "opp_house": opp.house,
                "until": now + TOMBSTONE_SECONDS,
            }
            self.by_digest.pop(token_hash, None)
            self.raw_memory.pop((table.id, pid), None)
        table.tokens.clear()
        for pid in game.order:
            loc = self.by_user.get(pid)
            if loc == ("game", table.id):
                # Keep the user pinned until the Discord post lands, then free them
                # in release_players. Leaving them busy would block a new challenge
                # if the post is slow, so free them now that the duel can't be played.
                self.by_user.pop(pid, None)
        self.save()
        return digests

    def sweep_tombstones(self, now: float) -> None:
        stale = [key for key, stone in self.tombstones.items() if float(stone.get("until") or 0) <= now]
        if not stale:
            return
        for key in stale:
            self.tombstones.pop(key, None)
        self.save()

    def unannounced(self) -> list[Table]:
        return [table for table in self.games.values() if table.end_seen and not table.announced]

    # --------------------------------------------------------------- rewards

    def spells_of(self, user_id: int) -> dict[str, int]:
        return dict(self.spellbook.get(str(user_id)) or {})

    def add_spell(self, user_id: int, card_id: str) -> tuple[int, bool]:
        """Add one spell card. The caller saves. Returns ``(count, is_new)``."""
        shelf = self.spellbook.setdefault(str(user_id), {})
        before = int(shelf.get(card_id, 0))
        shelf[card_id] = before + 1
        return shelf[card_id], before == 0

    def wager_night_on(self, now: float | None = None) -> bool:
        night = self.wager_night
        if not night:
            return False
        now = time.time() if now is None else now
        ends = night.get("ends_at")
        if ends is None:
            return True
        return now < float(ends)

    def begin_wager_night(
        self,
        *,
        by: int,
        channel_id: int,
        guild_id: int,
        minutes: int | None,
        now: float | None = None,
    ) -> tuple[bool, str, dict | None]:
        """Open a wager night. ``minutes`` is 30, 60, or None until staff end it."""
        from cogs.vcc.scoring import NIGHT_LENGTHS

        now = time.time() if now is None else now
        self.pop_expired_wager_night(now)
        if self.wager_night_on(now):
            return False, "already", None
        if minutes is not None and int(minutes) not in NIGHT_LENGTHS:
            return False, "length", None
        night = {
            "by": int(by),
            "at": now,
            "ends_at": None if minutes is None else now + int(minutes) * 60,
            "minutes": None if minutes is None else int(minutes),
            "channel_id": int(channel_id),
            "guild_id": int(guild_id),
        }
        self.wager_night = night
        self.save()
        return True, "started", night

    def end_wager_night(self) -> dict | None:
        night = self.wager_night
        if not night:
            return None
        self.wager_night = None
        self.save()
        return night

    def pop_expired_wager_night(self, now: float | None = None) -> dict | None:
        """Clear a night whose clock has run out. Open-ended nights stay."""
        now = time.time() if now is None else now
        night = self.wager_night
        if not night or self.wager_night_on(now):
            return None
        self.wager_night = None
        self.save()
        return night


def _tombstone_view(stone: dict) -> dict:
    """Public result only. No hands, no deck, no log."""
    return {
        "over": True,
        "tombstone": True,
        "you_won": bool(stone.get("you_won")),
        "summary": stone.get("summary"),
        "reason": stone.get("reason"),
        "your_turn": False,
        "you": {
            "name": stone.get("you_name"),
            "house_key": stone.get("you_house"),
            "ward": stone.get("you_ward"),
            "hand_count": None,
        },
        "opponent": {
            "name": stone.get("opp_name"),
            "house_key": stone.get("opp_house"),
            "ward": stone.get("opp_ward"),
            "hand_count": None,
        },
        "log": [],
        "pending": None,
    }


def _clean_spellbook(raw: dict) -> dict[str, dict[str, int]]:
    from cogs.vcc.cards import CARDS

    cleaned: dict[str, dict[str, int]] = {}
    if not isinstance(raw, dict):
        return cleaned
    for user_id, shelf in raw.items():
        if not isinstance(shelf, dict):
            continue
        kept = {}
        for card_id, count in shelf.items():
            if card_id not in CARDS:
                continue
            try:
                owned = int(count)
            except (TypeError, ValueError):
                continue
            if owned > 0:
                kept[str(card_id)] = owned
        if kept:
            cleaned[str(user_id)] = kept
    return cleaned


def public_base_url() -> str:
    """HTTPS origin for table links. ``VCC_PUBLIC_URL`` wins.

    If that's unset and Railway has attached a public domain, use
    ``RAILWAY_PUBLIC_DOMAIN`` so generating a domain is enough.
    """
    explicit = os.getenv("VCC_PUBLIC_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    railway = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip().strip("/")
    if railway:
        return "https://" + railway
    return ""
