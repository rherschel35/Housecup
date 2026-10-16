"""Rules engine for Velmora Curse & Crown.

Pure game state: no Discord, no sockets. A move returns a ``Move``.
When ``error`` is set, the state was not changed. A ``notice`` means the
state changed and the player still has something to do (discard, or keep
one card from Squirrel's Stash).

Spec (Oct 16 2026):
- Ward starts at 20. The duel ends when a player's Ward hits 0.
- Each deck is the 30 shared spells plus that player's own house card.
- Start with 5 cards. Draw 2 at the start of each turn you actually take.
- Mana is 3 on your first turn, +1 each of your turns, capped at 8.
  Wildgrowth raises that cap by 1 for the rest of the game.
- Up to 2 attack/defense cards per turn. Lemur's Sleight raises that by 1.
- On your 3rd, 6th, 9th... turn you may also play 1 specialist.
- End the turn holding at most 6 cards. Draws may put you over 6 during
  the turn; the turn does not pass until you discard down to 6.
- Healing cannot raise Ward above 20, including Glacier Ward. Ward that
  is already above 20 is left where it is.
- A skipped turn (Permafrost) does not draw, refresh mana, or count toward
  the specialist schedule.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from cogs.vcc.cards import CARDS, HOUSE_CARD, HOUSE_LABEL, SCHOOLS, Card, card

WARD_START = 20
OPENING_HAND = 5
DRAW_PER_TURN = 2
HAND_LIMIT = 6
MANA_START = 3
MANA_CAP = 8
HEX_CAP = 5


@dataclass
class Move:
    error: str | None = None
    notice: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _ok(notice: str | None = None) -> Move:
    return Move(notice=notice)


def _err(message: str) -> Move:
    return Move(error=message)


@dataclass
class Player:
    id: int
    name: str
    house: str
    ward: int = WARD_START
    mana: int = 0
    mana_granted: int = 0
    mana_cap_bonus: int = 0
    block: int = 0
    deck: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    discard: list[str] = field(default_factory=list)
    turns_played: int = 0
    spells_played: int = 0
    specialists_played: int = 0
    spell_slots: int = 2
    shade_step: bool = False
    ice_mirror: bool = False
    arm_thorns: bool = False
    thorns_live: bool = False
    next_cost_add: int = 0
    cancel_one_cost: bool = False
    cancel_next: bool = False
    no_fire: bool = False
    arm_no_fire: bool = False
    skip_next: bool = False
    heal_at_turn_start: int = 0
    fire_bonus: int = 0
    dash_used: bool = False
    extra_turn: bool = False
    schools_played: set[str] = field(default_factory=set)
    fire_cards_played: int = 0

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "house": self.house,
            "ward": self.ward,
            "mana": self.mana,
            "mana_granted": self.mana_granted,
            "mana_cap_bonus": self.mana_cap_bonus,
            "block": self.block,
            "deck": list(self.deck),
            "hand": list(self.hand),
            "discard": list(self.discard),
            "turns_played": self.turns_played,
            "spells_played": self.spells_played,
            "specialists_played": self.specialists_played,
            "spell_slots": self.spell_slots,
            "shade_step": self.shade_step,
            "ice_mirror": self.ice_mirror,
            "arm_thorns": self.arm_thorns,
            "thorns_live": self.thorns_live,
            "next_cost_add": self.next_cost_add,
            "cancel_one_cost": self.cancel_one_cost,
            "cancel_next": self.cancel_next,
            "no_fire": self.no_fire,
            "arm_no_fire": self.arm_no_fire,
            "skip_next": self.skip_next,
            "heal_at_turn_start": self.heal_at_turn_start,
            "fire_bonus": self.fire_bonus,
            "dash_used": self.dash_used,
            "extra_turn": self.extra_turn,
            "schools_played": sorted(self.schools_played),
            "fire_cards_played": self.fire_cards_played,
        }
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Player":
        p = cls(id=int(data["id"]), name=data["name"], house=data["house"])
        for key, value in data.items():
            if key == "schools_played":
                p.schools_played = set(value)
            elif key in ("id", "name", "house"):
                continue
            else:
                setattr(p, key, value)
        return p


class Game:
    def __init__(self) -> None:
        self.order: list[int] = []
        self.players: dict[int, Player] = {}
        self.current_index: int = 0
        self.round: int = 0
        self.winner: int | None = None
        self.reason: str | None = None  # ward | forfeit | timeout
        self.pending: dict | None = None
        self.events: list[str] = []
        self.feed: list[dict] = []
        self.last_shadow: dict | None = None
        self.rng = random.Random()
        self.practice: bool = False
        self.cpu_id: int | None = None

    # ---------------------------------------------------------------- build

    @classmethod
    def new(
        cls,
        *,
        challenger_id: int,
        challenger_name: str,
        challenger_house: str,
        opponent_id: int,
        opponent_name: str,
        opponent_house: str,
        seed: int | None = None,
        decks: dict[int, list[str]] | None = None,
        practice: bool = False,
        cpu_id: int | None = None,
    ) -> "Game":
        if practice:
            if opponent_house not in HOUSE_CARD:
                raise ValueError("the practice tutor needs a known house")
            if challenger_house not in HOUSE_CARD:
                challenger_house = ""
        elif challenger_house not in HOUSE_CARD or opponent_house not in HOUSE_CARD:
            raise ValueError("both players need a known house")
        game = cls()
        game.practice = practice
        game.cpu_id = cpu_id
        game.rng.seed(seed if seed is not None else random.randrange(1 << 30))
        game.order = [challenger_id, opponent_id]
        for pid, name, house in (
            (challenger_id, challenger_name, challenger_house),
            (opponent_id, opponent_name, opponent_house),
        ):
            player = Player(id=pid, name=name, house=house)
            if decks and pid in decks:
                player.deck = list(decks[pid])
            else:
                player.deck = _build_deck(house, game.rng)
            game.players[pid] = player
        for player in game.players.values():
            game._draw(player, OPENING_HAND)
        game._begin_turn()
        return game

    def to_dict(self) -> dict:
        state = self.rng.getstate()
        return {
            "order": list(self.order),
            "players": {str(pid): p.to_dict() for pid, p in self.players.items()},
            "current_index": self.current_index,
            "round": self.round,
            "winner": self.winner,
            "reason": self.reason,
            "pending": self.pending,
            "events": list(self.events),
            "feed": list(self.feed),
            "last_shadow": self.last_shadow,
            "practice": self.practice,
            "cpu_id": self.cpu_id,
            "rng": {
                "version": state[0],
                "state": list(state[1]),
                "gauss": state[2],
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Game":
        game = cls()
        game.order = [int(pid) for pid in data["order"]]
        game.players = {
            int(pid): Player.from_dict(raw) for pid, raw in data["players"].items()
        }
        game.current_index = int(data["current_index"])
        game.round = int(data["round"])
        game.winner = int(data["winner"]) if data.get("winner") is not None else None
        game.reason = data.get("reason")
        game.pending = data.get("pending")
        game.events = list(data.get("events") or [])
        game.feed = list(data.get("feed") or [])
        game.last_shadow = data.get("last_shadow")
        game.practice = bool(data.get("practice"))
        game.cpu_id = int(data["cpu_id"]) if data.get("cpu_id") is not None else None
        rng = data.get("rng")
        if rng:
            game.rng.setstate((
                rng["version"],
                tuple(rng["state"]),
                rng["gauss"],
            ))
        return game

    # ---------------------------------------------------------------- queries

    @property
    def current_id(self) -> int:
        return self.order[self.current_index]

    @property
    def current(self) -> Player:
        return self.players[self.current_id]

    def other_id(self, pid: int) -> int:
        return self.order[0] if pid == self.order[1] else self.order[1]

    def other(self, pid: int) -> Player:
        return self.players[self.other_id(pid)]

    def player(self, pid: int) -> Player:
        return self.players[pid]

    def over(self) -> bool:
        return self.winner is not None

    # ---------------------------------------------------------------- moves

    def play(self, pid: int, card_id: str) -> Move:
        if self.winner is not None:
            return _err("The duel is over.")
        if pid != self.current_id:
            return _err("It's not your turn.")
        if self.pending:
            if self.pending["type"] == "stash":
                return _err("Keep one of the stashed cards first.")
            return _err("Discard down to 6 first.")
        player = self.players.get(pid)
        if player is None or card_id not in player.hand:
            return _err("That card isn't in your hand.")
        if card_id not in CARDS:
            return _err("That card isn't in the deck.")
        spec = card(card_id)
        if player.no_fire and spec.school == "fire":
            return _err("Rootbind keeps you from playing Fire this turn.")
        if spec.slot == "specialist":
            if player.turns_played <= 0 or player.turns_played % 3 != 0:
                return _err("A specialist card can only be played every 3rd turn.")
            if player.specialists_played >= 1:
                return _err("You've already played a specialist this turn.")
        elif player.spells_played >= player.spell_slots:
            return _err("You've played your attack and defense cards for this turn.")

        pay = spec.cost + player.next_cost_add
        if player.mana < pay:
            return _err(f"You need {pay} mana.")

        player.mana -= pay
        if player.next_cost_add:
            player.next_cost_add = 0
        player.hand.remove(card_id)

        if spec.slot == "specialist":
            player.specialists_played += 1
        else:
            player.spells_played += 1

        cancelled = False
        if player.cancel_next:
            player.cancel_next = False
            cancelled = True
            if spec.cost == 1:
                player.cancel_one_cost = False
        elif spec.cost == 1 and player.cancel_one_cost:
            player.cancel_one_cost = False
            cancelled = True

        wards_before = {who.id: who.ward for who in self.players.values()}
        log_at = len(self.events)
        if cancelled:
            player.discard.append(card_id)
            self._log(f"{player.name}'s {spec.name} fizzles.")
            self._remember_play(player, spec, log_at, wards_before)
            self._note_death()
            return _ok()

        if spec.school in SCHOOLS:
            player.schools_played.add(spec.school)
        if spec.school == "fire":
            player.fire_cards_played += 1

        self._resolve(player, spec)
        self._remember_play(player, spec, log_at, wards_before)
        # Stash pulls its draws aside. Anything else lands in the discard.
        if self.pending and self.pending.get("type") == "stash" and card_id in self.pending.get("hold", ()):
            pass
        elif card_id not in player.hand and card_id not in player.discard:
            in_pending = False
            if self.pending and card_id in (self.pending.get("options") or []):
                in_pending = True
            if not in_pending:
                player.discard.append(card_id)
        self._note_death()
        return _ok()

    def end_turn(self, pid: int) -> Move:
        if self.winner is not None:
            return _err("The duel is over.")
        if pid != self.current_id:
            return _err("It's not your turn.")
        if self.pending:
            if self.pending["type"] == "stash":
                return _err("Keep one of the stashed cards first.")
            need = self.pending["need"]
            return _err(f"Discard {need} card{'s' if need != 1 else ''} first.")
        player = self.players[pid]
        extra = len(player.hand) - HAND_LIMIT
        if extra > 0:
            self.pending = {"type": "discard", "need": extra}
            noun = "card" if extra == 1 else "cards"
            return _ok(f"Discard {extra} {noun}.")
        self._pass_turn()
        return _ok()

    def discard(self, pid: int, card_ids: list[str]) -> Move:
        if self.winner is not None:
            return _err("The duel is over.")
        if pid != self.current_id:
            return _err("It's not your turn.")
        if not self.pending or self.pending.get("type") != "discard":
            return _err("You're not discarding.")
        need = int(self.pending["need"])
        if len(card_ids) != need or len(set(card_ids)) != need:
            noun = "card" if need == 1 else "cards"
            return _err(f"Choose {need} {noun} to discard.")
        player = self.players[pid]
        for cid in card_ids:
            if cid not in player.hand:
                return _err("That card isn't in your hand.")
        for cid in card_ids:
            player.hand.remove(cid)
            player.discard.append(cid)
        self.pending = None
        self._log(f"{player.name} discards down to {len(player.hand)}.")
        self._pass_turn()
        return _ok()

    def choose(self, pid: int, keep_id: str) -> Move:
        if self.winner is not None:
            return _err("The duel is over.")
        if pid != self.current_id:
            return _err("It's not your turn.")
        if not self.pending or self.pending.get("type") != "stash":
            return _err("There's nothing to keep.")
        options = list(self.pending["options"])
        if keep_id not in options:
            return _err("That card isn't one of the two.")
        player = self.players[pid]
        player.hand.append(keep_id)
        for cid in options:
            if cid != keep_id:
                player.discard.append(cid)
        self.pending = None
        self._log(f"{player.name} keeps {card(keep_id).name} from the stash.")
        return _ok()

    def forfeit(self, pid: int) -> Move:
        if self.winner is not None:
            return _err("The duel is over.")
        if pid not in self.players:
            return _err("You're not in this duel.")
        self.winner = self.other_id(pid)
        self.reason = "forfeit"
        self.pending = None
        self._log(f"{self.players[pid].name} forfeits.")
        return _ok()

    def timeout(self) -> Move:
        if self.winner is not None:
            return _err("The duel is over.")
        pid = self.current_id
        self.winner = self.other_id(pid)
        self.reason = "timeout"
        self.pending = None
        self._log(f"{self.players[pid].name} runs out of time.")
        return _ok()

    # ---------------------------------------------------------------- view

    def view_for(self, pid: int, *, now: float | None = None, deadline: float | None = None) -> dict:
        """What this player is allowed to see. The other hand stays hidden."""
        you = self.players[pid]
        opp = self.other(pid)
        seconds = None
        if deadline is not None and now is not None and self.winner is None:
            seconds = max(0, int(deadline - now))
        specialist = "locked"
        if you.turns_played and you.turns_played % 3 == 0:
            specialist = "spent" if you.specialists_played else "ready"
        pending = None
        if self.pending and self.pending["type"] == "discard" and pid == self.current_id:
            pending = {"type": "discard", "need": self.pending["need"]}
        elif self.pending and self.pending["type"] == "stash" and pid == self.current_id:
            pending = {
                "type": "stash",
                "cards": [self._public_card(cid, you, playable=False) for cid in self.pending["options"]],
            }
        winner_name = self.players[self.winner].name if self.winner is not None else None
        loser_id = self.other_id(self.winner) if self.winner is not None else None
        loser_name = self.players[loser_id].name if loser_id is not None else None
        summary = _summary(self, winner_name, loser_name)
        if self.practice and summary:
            summary = "Practice. " + summary
        return {
            "over": self.winner is not None,
            "reason": self.reason,
            "winner_id": self.winner,
            "winner_name": winner_name,
            "loser_name": loser_name,
            "you_won": (self.winner == pid) if self.winner is not None else None,
            "summary": summary,
            "practice": bool(self.practice),
            "your_turn": pid == self.current_id and self.winner is None,
            "active_name": self.current.name,
            "round": self.round,
            "seconds_left": seconds,
            "spells_played": self.current.spells_played,
            "spell_slots": self.current.spell_slots,
            "specialist": specialist,
            "you": self._public_player(you, reveal_hand=True),
            "opponent": self._public_player(opp, reveal_hand=False),
            "pending": pending,
            "log": self.events[-18:],
            "feed": list(self.feed[-40:]),
            "last_turn": self._last_turn(pid),
        }

    def _public_player(self, player: Player, *, reveal_hand: bool) -> dict:
        meta = HOUSE_LABEL.get(player.house, {"name": player.house, "emoji": ""})
        data = {
            "id": player.id,
            "name": player.name,
            "house": meta["name"],
            "house_key": player.house,
            "house_emoji": meta["emoji"],
            "ward": player.ward,
            "ward_max": WARD_START,
            "mana": player.mana,
            "mana_max": player.mana_granted,
            "block": player.block,
            "hand_count": len(player.hand),
            "hand_limit": HAND_LIMIT,
            "turn": player.turns_played,
            "deck": len(player.deck),
            "statuses": _statuses(player),
        }
        if reveal_hand:
            data["hand"] = [
                self._public_card(cid, player, playable=True) for cid in player.hand
            ]
        return data

    def _public_card(self, cid: str, owner: Player, *, playable: bool) -> dict:
        spec = card(cid)
        pay = spec.cost + (owner.next_cost_add if owner.id == self.current_id else 0)
        why = ""
        can = False
        if playable:
            can, why = self._playable(owner, spec, pay)
        return {
            "id": spec.id,
            "name": spec.name,
            "school": spec.school,
            "school_name": spec.school_name,
            "cost": spec.cost,
            "pay": pay,
            "slot": spec.slot,
            "tag": spec.tag,
            "text": spec.text,
            "art": spec.art,
            "playable": can,
            "why": why,
        }

    def _playable(self, owner: Player, spec: Card, pay: int) -> tuple[bool, str]:
        if self.winner is not None:
            return False, "The duel is over."
        if owner.id != self.current_id:
            return False, "Wait for your turn."
        if self.pending:
            return False, "Finish your choice first."
        if owner.no_fire and spec.school == "fire":
            return False, "You can't play Fire this turn."
        if spec.slot == "specialist":
            if owner.turns_played <= 0 or owner.turns_played % 3 != 0:
                return False, "Specialists are every 3rd turn."
            if owner.specialists_played >= 1:
                return False, "Specialist already played."
        elif owner.spells_played >= owner.spell_slots:
            return False, "No attack or defense left this turn."
        if owner.mana < pay:
            return False, f"Need {pay} mana."
        return True, ""

    # ---------------------------------------------------------------- flow

    def _begin_turn(self) -> None:
        guard = 0
        while self.winner is None and guard < 4:
            guard += 1
            player = self.current
            if player.skip_next:
                player.skip_next = False
                player.extra_turn = False
                self._log(f"{player.name} is frozen solid and skips the turn.")
                self._remember_note(player, "skip", "skips the turn")
                self.current_index = 1 - self.current_index
                continue
            self.round += 1
            player.turns_played += 1
            player.spells_played = 0
            player.specialists_played = 0
            player.spell_slots = 2
            player.no_fire = player.arm_no_fire
            player.arm_no_fire = False
            player.thorns_live = False
            opponent = self.other(player.id)
            if opponent.arm_thorns:
                opponent.thorns_live = True
                opponent.arm_thorns = False
            if player.heal_at_turn_start:
                amount = player.heal_at_turn_start
                player.heal_at_turn_start = 0
                gained = self._heal(player, amount)
                if gained:
                    self._log(f"{player.name} mends {gained} Ward. Ward {player.ward}.")
                else:
                    self._log(f"{player.name}'s Ward is already full.")
            cap = MANA_CAP + player.mana_cap_bonus
            # Refill to this player's own turn max: 3, 4, 5, 6, 7, then 8.
            # Unspent mana is replaced. It is not topped up to the cap early.
            player.mana = min(MANA_START - 1 + player.turns_played, cap)
            player.mana_granted = player.mana
            drawn = self._draw(player, DRAW_PER_TURN)
            self._log(
                f"{player.name}'s turn {player.turns_played}. "
                f"Drew {len(drawn)}. Mana {player.mana}."
            )
            return

    def _pass_turn(self) -> None:
        if self.winner is not None:
            return
        player = self.current
        extra = len(player.hand) - HAND_LIMIT
        if extra > 0:
            self.pending = {"type": "discard", "need": extra}
            return
        self._remember_empty_pass(player)
        opponent = self.other(player.id)
        opponent.thorns_live = False
        player.no_fire = False
        if player.extra_turn:
            player.extra_turn = False
            self._log(f"{player.name} dashes into another turn.")
            self._begin_turn()
            return
        self.current_index = 1 - self.current_index
        self._begin_turn()

    def _draw(self, player: Player, n: int) -> list[str]:
        drawn: list[str] = []
        for _ in range(n):
            if not player.deck:
                if not player.discard:
                    break
                player.deck = player.discard
                player.discard = []
                self.rng.shuffle(player.deck)
                self._log(f"{player.name} reshuffles the discard into the deck.")
            drawn.append(player.deck.pop())
        player.hand.extend(drawn)
        return drawn

    def _note_death(self) -> None:
        if self.winner is not None:
            return
        dead = [pid for pid, player in self.players.items() if player.ward <= 0]
        if not dead:
            return
        if len(dead) == 1:
            self.winner = self.other_id(dead[0])
        else:
            # Both Ward totals hit 0 on the same card. The player who
            # struck (the current turn) connected first.
            opponent = self.other_id(self.current_id)
            self.winner = self.current_id if opponent in dead else opponent
        self.reason = "ward"
        self.pending = None
        winner = self.players[self.winner]
        loser = self.players[self.other_id(self.winner)]
        self._log(f"{winner.name} breaks {loser.name}'s Ward.")

    def _log(self, text: str) -> None:
        self.events.append(text)
        if len(self.events) > 80:
            del self.events[:40]

    def _heal(self, player: Player, amount: int) -> int:
        """Raise Ward toward 20. Ward already above 20 is left alone."""
        if amount <= 0 or player.ward >= WARD_START:
            return 0
        gained = min(amount, WARD_START - player.ward)
        player.ward += gained
        return gained

    def _remember_play(self, player: Player, spec: Card, log_at: int, before: dict) -> None:
        self.feed.append({
            "kind": "play",
            "actor_id": player.id,
            "actor": player.name,
            "card_id": spec.id,
            "card": spec.name,
            "art": spec.art,
            "turn": player.turns_played,
            "round": self.round,
            "result": self._play_result(player, self.events[log_at:], before),
        })
        self._trim_feed()

    def _remember_note(self, player: Player, kind: str, result: str) -> None:
        self.feed.append({
            "kind": kind,
            "actor_id": player.id,
            "actor": player.name,
            "turn": player.turns_played,
            "round": self.round,
            "result": result,
        })
        self._trim_feed()

    def _remember_empty_pass(self, player: Player) -> None:
        played = any(
            item.get("kind") == "play"
            and item.get("actor_id") == player.id
            and item.get("turn") == player.turns_played
            for item in self.feed
        )
        if played:
            return
        self._remember_note(player, "pass", "ended the turn with no card")

    def _trim_feed(self) -> None:
        if len(self.feed) > 80:
            del self.feed[:40]

    def _play_result(self, actor: Player, lines: list[str], before: dict) -> str:
        bits: list[str] = []
        for who in (self.other(actor.id), actor):
            delta = who.ward - before.get(who.id, who.ward)
            if delta < 0:
                bits.append(f"{-delta} dmg to {who.name}")
            elif delta > 0:
                bits.append(f"{who.name} gains {delta} Ward")
        extras: list[str] = []
        for line in lines:
            if bits and self._ward_recap(line):
                continue
            text = line.rstrip(".")
            if text:
                extras.append(text)
        parts = bits + extras
        return "; ".join(parts) if parts else "no effect"

    @staticmethod
    def _ward_recap(line: str) -> bool:
        if "already full" in line:
            return False
        if " hits " in line and ("damage" in line or "blocked" in line or "no Ward lost" in line):
            return True
        if "from the blast" in line:
            return True
        if " Ward." in line and ("gains " in line or "heals " in line or "mends " in line):
            return True
        return False

    def _last_turn(self, pid: int) -> dict | None:
        if self.winner is not None or pid != self.current_id:
            return None
        opp = self.other(pid)
        skipped = bool(
            self.feed
            and self.feed[-1].get("kind") == "skip"
            and self.feed[-1].get("actor_id") == opp.id
        )
        if skipped:
            return {
                "name": opp.name,
                "turn": opp.turns_played,
                "plays": [],
                "skipped": True,
            }
        if opp.turns_played <= 0:
            return None
        plays = [
            item for item in self.feed
            if item.get("actor_id") == opp.id
            and item.get("kind") in ("play", "pass")
            and item.get("turn") == opp.turns_played
        ]
        return {
            "name": opp.name,
            "turn": opp.turns_played,
            "plays": plays,
            "skipped": False,
        }

    def _suffer(self, player: Player, amount: int) -> None:
        player.ward = max(0, player.ward - amount)

    def _chip(self, player: Player, amount: int) -> None:
        dmg = amount
        if player.block and dmg:
            absorbed = min(player.block, dmg)
            player.block -= absorbed
            dmg -= absorbed
        if dmg:
            player.ward = max(0, player.ward - dmg)

    def _fire_packets(self, player: Player, packets: list[int]) -> list[int]:
        if player.fire_bonus and packets:
            packets = list(packets)
            packets[0] += player.fire_bonus
            player.fire_bonus = 0
        return packets

    def _strike(
        self,
        attacker: Player,
        defender: Player,
        packets: list[int],
        *,
        ignore_block: bool = False,
        name: str,
    ) -> None:
        """Resolve one attack. Shade Step dodges the whole card. Ice Mirror
        bounces it once. Blaze Wall thorns answer a hit that was not dodged
        and not bounced."""
        if defender.shade_step:
            defender.shade_step = False
            self._log(f"{defender.name} steps into shadow. {name} misses.")
            return
        target = defender
        reflected = False
        if defender.ice_mirror:
            defender.ice_mirror = False
            target = attacker
            reflected = True
            self._log(f"{defender.name}'s Ice Mirror hurls {name} back.")
            if attacker.shade_step:
                attacker.shade_step = False
                self._log(f"{attacker.name} dodges the reflected spell.")
                return
        blocked = 0
        dealt = 0
        for raw in packets:
            dmg = raw
            if dmg <= 0:
                continue
            if not ignore_block and target.block:
                absorbed = min(target.block, dmg)
                target.block -= absorbed
                dmg -= absorbed
                blocked += absorbed
            if dmg:
                target.ward = max(0, target.ward - dmg)
                dealt += dmg
        bits = []
        if blocked:
            bits.append(f"{blocked} blocked")
        if dealt:
            bits.append(f"{dealt} damage")
        elif packets:
            bits.append("no Ward lost")
        detail = ", ".join(bits) if bits else "no effect"
        self._log(f"{name} hits {target.name}: {detail}. Ward {target.ward}.")
        hit = any(p > 0 for p in packets)
        if hit and not reflected and defender.thorns_live:
            before = attacker.ward
            self._chip(attacker, 1)
            lost = before - attacker.ward
            if lost:
                self._log(f"The Blaze Wall bites {attacker.name} for {lost}. Ward {attacker.ward}.")
            elif attacker.block >= 0:
                self._log(f"The Blaze Wall sparks against {attacker.name}'s Block.")

    def _random_discard(self, player: Player) -> str | None:
        if not player.hand:
            return None
        cid = self.rng.choice(player.hand)
        player.hand.remove(cid)
        player.discard.append(cid)
        return cid

    def _remember_shadow(self, payload: dict) -> None:
        self.last_shadow = payload

    # ---------------------------------------------------------------- cards

    def _resolve(self, player: Player, spec: Card) -> None:
        opponent = self.other(player.id)
        cid = spec.id
        if cid == "ember-lance":
            self._strike(player, opponent, self._fire_packets(player, [2]), name=spec.name)
        elif cid == "cinder-spray":
            self._strike(player, opponent, self._fire_packets(player, [1, 1]), name=spec.name)
        elif cid == "kindle":
            player.fire_bonus += 2
            self._log(f"{player.name}'s next Fire card deals +{player.fire_bonus}.")
        elif cid == "blaze-wall":
            self._strike(player, opponent, self._fire_packets(player, [3]), name=spec.name)
            player.arm_thorns = True
            self._log(f"A wall of fire waits for {opponent.name}'s next turn.")
        elif cid == "phoenix-spark":
            self._strike(player, opponent, self._fire_packets(player, [2]), name=spec.name)
            if opponent.ward <= 0:
                gained = self._heal(player, 3)
                if gained:
                    self._log(f"The phoenix sparks. {player.name} heals {gained}. Ward {player.ward}.")
                else:
                    self._log(f"The phoenix sparks, but {player.name}'s Ward is already full.")
        elif cid == "inferno":
            self._strike(player, opponent, self._fire_packets(player, [6]), name=spec.name)
            for who in (player, opponent):
                tossed = self._random_discard(who)
                if tossed:
                    self._log(f"{who.name} discards {card(tossed).name}.")
        elif cid == "frostveil":
            player.block += 2
            self._log(f"{player.name} gains 2 Block ({player.block}).")
        elif cid == "chill-touch":
            self._strike(player, opponent, [1], name=spec.name)
            opponent.next_cost_add += 1
            self._log(f"{opponent.name}'s next card costs +1 mana.")
        elif cid == "ice-mirror":
            player.ice_mirror = True
            self._log(f"{player.name} raises an Ice Mirror.")
        elif cid == "snow-shroud":
            player.block += 3
            drawn = self._draw(player, 1)
            got = card(drawn[0]).name if drawn else "nothing"
            self._log(f"{player.name} gains 3 Block ({player.block}) and draws {got}.")
        elif cid == "glacier-ward":
            gained = self._heal(player, 5)
            if gained:
                self._log(f"{player.name} gains {gained} Ward. Ward {player.ward}.")
            else:
                self._log(f"{player.name}'s Ward is already full.")
        elif cid == "permafrost":
            opponent.skip_next = True
            self._log(f"{opponent.name} will skip their next turn.")
        elif cid == "bloom":
            gained = self._heal(player, 2)
            if gained:
                self._log(f"{player.name} heals {gained}. Ward {player.ward}.")
            else:
                self._log(f"{player.name}'s Ward is already full.")
        elif cid == "thornlash":
            self._strike(player, opponent, [2], ignore_block=True, name=spec.name)
        elif cid == "sapling-shield":
            player.block += 2
            player.heal_at_turn_start += 1
            self._log(f"{player.name} gains 2 Block ({player.block}) and will heal 1 next turn.")
        elif cid == "rootbind":
            opponent.arm_no_fire = True
            self._log(f"{opponent.name} can't play Fire on their next turn.")
        elif cid == "moonwell":
            gained = self._heal(player, 4)
            if gained:
                self._log(f"{player.name} heals {gained}. Ward {player.ward}.")
            else:
                self._log(f"{player.name}'s Ward is already full.")
        elif cid == "wildgrowth":
            player.mana_cap_bonus += 1
            player.mana += 1
            player.mana_granted += 1
            self._log(
                f"{player.name}'s max mana is now {MANA_CAP + player.mana_cap_bonus}. "
                f"Mana {player.mana}."
            )
        elif cid == "hush":
            opponent.cancel_one_cost = True
            self._remember_shadow({"card": cid, "target": opponent.id, "applied": True})
            self._log(f"{opponent.name}'s next 1-cost card will fizzle.")
        elif cid == "pickpocket-charm":
            if not opponent.hand:
                self._log(f"{opponent.name} has nothing to steal.")
                self._remember_shadow({"card": cid, "thief": player.id, "victim": opponent.id, "card_taken": None})
            else:
                stolen = self.rng.choice(opponent.hand)
                opponent.hand.remove(stolen)
                player.hand.append(stolen)
                self._remember_shadow({
                    "card": cid, "thief": player.id, "victim": opponent.id, "card_taken": stolen,
                })
                self._log(f"{player.name} steals {card(stolen).name}.")
        elif cid == "curse-of-clumsy-fingers":
            tossed = self._random_discard(opponent)
            self._remember_shadow({
                "card": cid, "victim": opponent.id, "discarded": tossed,
            })
            if tossed:
                self._log(f"{opponent.name} drops {card(tossed).name}.")
            else:
                self._log(f"{opponent.name} has nothing to drop.")
        elif cid == "shade-step":
            player.shade_step = True
            self._remember_shadow({"card": cid, "target": player.id})
            self._log(f"{player.name} is ready to dodge the next attack.")
        elif cid == "hex-swap":
            high = player if player.ward >= opponent.ward else opponent
            low = opponent if high is player else player
            transfer = min(abs(player.ward - opponent.ward), HEX_CAP)
            if transfer:
                high.ward -= transfer
                low.ward += transfer
            self._remember_shadow({
                "card": cid,
                "high": high.id,
                "low": low.id,
                "amount": transfer,
            })
            self._log(
                f"Ward shifts by {transfer}. "
                f"{player.name} {player.ward}, {opponent.name} {opponent.ward}."
            )
        elif cid == "eclipse":
            saved = opponent.block
            opponent.block = 0
            opponent.cancel_next = True
            self._remember_shadow({
                "card": cid,
                "target": opponent.id,
                "blocks": saved,
                "cancel_applied": True,
            })
            self._log(f"Eclipse wipes {opponent.name}'s Block and will cancel their next card.")
        elif cid == "wishing-coin":
            heads = self.rng.randrange(2) == 0
            if heads:
                self._draw(player, 2)
                self._log(f"The coin lands heads. {player.name} draws 2.")
            else:
                self._log(f"The coin lands tails. {player.name} draws nothing.")
        elif cid == "lantern-seal":
            self._lantern(player)
        elif cid == "fate-shuffle":
            for who in (player, opponent):
                who.deck.extend(who.hand)
                who.hand.clear()
                self.rng.shuffle(who.deck)
                self._draw(who, 4)
            self._log("Both hands return to the deck. Each duelist draws 4.")
        elif cid == "comet-call":
            amount = min(4, len(player.hand))
            self._strike(player, opponent, [amount], name=spec.name)
        elif cid == "starfall":
            amount = len(player.schools_played)
            self._strike(player, opponent, [amount], name=spec.name)
        elif cid == "supernova":
            self._strike(player, opponent, [8], name=spec.name)
            self._suffer(player, 3)
            self._log(f"{player.name} takes 3 from the blast. Ward {player.ward}.")
        elif cid == "squirrels-stash":
            drawn = self._draw(player, 2)
            for drawn_id in drawn:
                if drawn_id in player.hand:
                    player.hand.remove(drawn_id)
            if len(drawn) >= 2:
                self.pending = {"type": "stash", "options": drawn[:2]}
                self._log(f"{player.name} digs out two cards and must keep one.")
            elif len(drawn) == 1:
                player.hand.append(drawn[0])
                self._log(f"{player.name} finds {card(drawn[0]).name} in the stash.")
            else:
                self._log("The stash is empty.")
        elif cid == "bear-hug":
            player.block += 4
            self._log(f"{player.name} gains 4 Block ({player.block}).")
            self._strike(player, opponent, [1], name=spec.name)
        elif cid == "lemurs-sleight":
            player.spell_slots += 1
            self._log(f"{player.name} can play {player.spell_slots} attack or defense cards this turn.")
        elif cid == "antelope-dash":
            if player.dash_used:
                self._log(f"{player.name} has already dashed this game.")
            else:
                player.dash_used = True
                player.extra_turn = True
                self._log(f"{player.name} will take another turn.")
        elif cid == "wolf-howl":
            amount = 3 + player.fire_cards_played
            self._strike(player, opponent, [amount], name=spec.name)
        else:
            self._log(f"{spec.name} does nothing. That shouldn't happen.")

    def _lantern(self, player: Player) -> None:
        shadow = self.last_shadow
        self.last_shadow = None
        if not shadow:
            self._log(f"{player.name}'s lantern finds no shadow to seal.")
            return
        kind = shadow.get("card")
        if kind == "hush":
            target = self.players[shadow["target"]]
            if target.cancel_one_cost and shadow.get("applied"):
                target.cancel_one_cost = False
                self._log("Lantern Seal snuffs the hush.")
            else:
                self._log("The hush has already been spent.")
        elif kind == "shade-step":
            target = self.players[shadow["target"]]
            if target.shade_step:
                target.shade_step = False
                self._log("Lantern Seal breaks the shade.")
            else:
                self._log("The shade has already been used.")
        elif kind == "hex-swap":
            high = self.players[shadow["high"]]
            low = self.players[shadow["low"]]
            amount = int(shadow.get("amount") or 0)
            give = min(amount, low.ward)
            low.ward -= give
            high.ward += give
            self._log(
                f"Lantern Seal undoes the swap. "
                f"{high.name} {high.ward}, {low.name} {low.ward}."
            )
        elif kind == "pickpocket-charm":
            thief = self.players[shadow["thief"]]
            victim = self.players[shadow["victim"]]
            taken = shadow.get("card_taken")
            if taken and taken in thief.hand:
                thief.hand.remove(taken)
                victim.hand.append(taken)
                self._log(f"Lantern Seal returns {card(taken).name}.")
            else:
                self._log("The stolen card is already gone.")
        elif kind == "curse-of-clumsy-fingers":
            victim = self.players[shadow["victim"]]
            tossed = shadow.get("discarded")
            if tossed and tossed in victim.discard:
                victim.discard.remove(tossed)
                victim.hand.append(tossed)
                self._log(f"Lantern Seal returns {card(tossed).name} to {victim.name}'s hand.")
            else:
                self._log("That discarded card can't be called back.")
        elif kind == "eclipse":
            target = self.players[shadow["target"]]
            if shadow.get("cancel_applied") and target.cancel_next:
                target.cancel_next = False
            if target.block == 0:
                target.block = int(shadow.get("blocks") or 0)
            self._log(f"Lantern Seal lifts the eclipse. Block {target.block}.")
        else:
            self._log("Lantern Seal flares, but that shadow left no trace.")


def _build_deck(house: str, rng: random.Random) -> list[str]:
    ids = [cid for cid, spec in CARDS.items() if spec.school != "house"]
    extra = HOUSE_CARD.get(house)
    if extra:
        ids.append(extra)
    rng.shuffle(ids)
    return ids


def _statuses(player: Player) -> list[str]:
    labels: list[str] = []
    if player.block:
        labels.append(f"Block {player.block}")
    if player.shade_step:
        labels.append("Shade Step")
    if player.ice_mirror:
        labels.append("Ice Mirror")
    if player.thorns_live:
        labels.append("Blaze Wall")
    elif player.arm_thorns:
        labels.append("Blaze Wall next turn")
    if player.skip_next:
        labels.append("Skips next turn")
    if player.no_fire:
        labels.append("Can't play Fire")
    elif player.arm_no_fire:
        labels.append("Fire sealed next turn")
    if player.cancel_next:
        labels.append("Next card cancelled")
    if player.cancel_one_cost:
        labels.append("Next 1-cost cancelled")
    if player.next_cost_add:
        labels.append(f"Next card +{player.next_cost_add} mana")
    if player.fire_bonus:
        labels.append(f"Next Fire +{player.fire_bonus}")
    if player.extra_turn:
        labels.append("Extra turn")
    if player.mana_cap_bonus:
        labels.append(f"Max mana +{player.mana_cap_bonus}")
    if player.heal_at_turn_start:
        labels.append(f"Heal {player.heal_at_turn_start} next turn")
    return labels


def _summary(game: Game, winner: str | None, loser: str | None) -> str | None:
    if game.winner is None or not winner or not loser:
        return None
    if game.reason == "forfeit":
        return f"{loser} forfeits. {winner} wins."
    if game.reason == "timeout":
        return f"{loser} runs out of time. {winner} wins."
    return f"{winner} breaks {loser}'s Ward."
