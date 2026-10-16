"""Easy practice opponent for Velmora Curse & Crown.

The tutor only acts through the rules engine. Most turns it plays a random
legal card, or ends early. It rarely bothers to block or heal, so a person
who plays the basics can beat it.

Discord user ids are never 0, so that id is reserved for the tutor.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from cogs.vcc.cards import HOUSE_CARD, card
from cogs.vcc.engine import Move

log = logging.getLogger("velmora.vcc")

CPU_ID = 0
TUTOR_NAME = "Tutor"

# Chance to stop with mana still unspent. Keeps the tutor from curving out.
END_EARLY = 0.42
# Chance to pick a Defense card when one would actually help.
DEFEND_CHANCE = 0.15
_STEP_CAP = 40


@dataclass
class CpuStep:
    move: Move
    kind: str
    card_id: str | None = None
    was_legal: bool = False


def pick_tutor_house(player_house: str, rng: random.Random) -> str:
    """A real house for the tutor's deck, other than the player's when possible."""
    choices = [house for house in HOUSE_CARD if house != player_house]
    if not choices:
        choices = list(HOUSE_CARD)
    return rng.choice(choices)


def legal_card_ids(game, pid: int) -> list[str]:
    """Cards the engine would accept from this hand, in hand order."""
    if game.winner is not None or game.current_id != pid or game.pending:
        return []
    player = game.players.get(pid)
    if player is None:
        return []
    legal = []
    for cid in player.hand:
        if cid not in player.hand:
            continue
        spec = card(cid)
        pay = spec.cost + player.next_cost_add
        ok, _why = game._playable(player, spec, pay)
        if ok:
            legal.append(cid)
    return legal


def choose_card(
    legal: list[str],
    *,
    my_ward: int,
    opp_ward: int,
    rng: random.Random,
) -> str | None:
    """A legal card id, or None to end the turn.

    None is also the answer when nothing can be played. Defense is chosen
    only on a small roll, and only when this side is hurting or behind.
    """
    if not legal:
        return None
    if rng.random() < END_EARLY:
        return None
    hurting = my_ward <= opp_ward or my_ward <= 10
    if hurting and rng.random() < DEFEND_CHANCE:
        defense = [cid for cid in legal if card(cid).tag == "Defense"]
        if defense:
            return rng.choice(defense)
    return rng.choice(list(legal))


def choose_discards(hand: list[str], need: int, rng: random.Random) -> list[str]:
    """Cards to drop. Usually random. Rarely keeps a Defense card."""
    pool = list(hand)
    if rng.random() < DEFEND_CHANCE:
        pool.sort(key=lambda cid: (card(cid).tag == "Defense", -card(cid).cost))
        return pool[:need]
    return rng.sample(pool, need)


def take_step(game, rng: random.Random | None = None) -> CpuStep:
    """One legal tutor action. The move is whatever the engine returned."""
    rng = game.rng if rng is None else rng
    pid = CPU_ID
    player = game.players[pid]
    if game.pending and game.pending.get("type") == "stash":
        options = [cid for cid in game.pending.get("options") or [] if cid]
        keep = rng.choice(options) if options else ""
        legal = keep in (game.pending.get("options") or [])
        move = game.choose(pid, keep)
        return CpuStep(move, "choose", keep, legal and move.ok)
    if game.pending and game.pending.get("type") == "discard":
        need = int(game.pending["need"])
        hand = list(player.hand)
        pick = choose_discards(hand, need, rng) if len(hand) >= need else list(hand)
        legal = (
            len(pick) == need
            and len(set(pick)) == need
            and all(cid in hand for cid in pick)
        )
        move = game.discard(pid, pick)
        return CpuStep(move, "discard", None, legal and move.ok)
    legal_ids = legal_card_ids(game, pid)
    opponent = game.other(pid)
    chosen = choose_card(
        legal_ids,
        my_ward=player.ward,
        opp_ward=opponent.ward,
        rng=rng,
    )
    if chosen is None:
        move = game.end_turn(pid)
        # Ending, or being told to discard, is a legal engine result.
        return CpuStep(move, "end", None, move.ok)
    move = game.play(pid, chosen)
    return CpuStep(move, "play", chosen, chosen in legal_ids and move.ok)


def run_cpu(game, rng: random.Random | None = None) -> list[CpuStep]:
    """Play the tutor's turn until it is the human's turn or the duel ends."""
    if not getattr(game, "practice", False):
        return []
    steps: list[CpuStep] = []
    guard = 0
    while game.winner is None and game.current_id == CPU_ID and guard < _STEP_CAP:
        guard += 1
        step = take_step(game, rng)
        steps.append(step)
        if not step.move.ok or not step.was_legal:
            log.warning("Practice tutor stopped (%s).", step.move.error or step.kind)
            break
    if game.winner is None and game.current_id == CPU_ID:
        log.warning("Practice tutor yielded the game.")
        move = game.forfeit(CPU_ID)
        steps.append(CpuStep(move, "forfeit", None, move.ok))
    return steps
