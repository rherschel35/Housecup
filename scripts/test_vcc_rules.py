#!/usr/bin/env python3
"""Rules for Velmora Curse & Crown. No Discord token required.

Usage (from repo root):
  python scripts/test_vcc_rules.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-vcc-"))
os.environ.pop("DISCORD_TOKEN", None)

from cogs.vcc.book import Book  # noqa: E402
from cogs.vcc.cards import CARDS, HOUSE_CARD, SPECIALISTS, SPELL_IDS, card as card_of  # noqa: E402
from cogs.vcc.cpu import (  # noqa: E402
    CPU_ID,
    DEFEND_CHANCE,
    END_EARLY,
    TUTOR_NAME,
    choose_card,
    legal_card_ids,
    run_cpu,
)
from cogs.vcc.engine import HAND_LIMIT, Game  # noqa: E402
from cogs.vcc.scoring import (  # noqa: E402
    collectible_kind,
    dropdown_amounts,
    pick_from,
    points_to_move,
    stake_decision,
)
from cogs.vcc.web import Hub, create_app  # noqa: E402


FAILURES: list[str] = []


def check(cond: bool, message: str) -> None:
    if not cond:
        FAILURES.append(message)
        print("FAIL", message)


def sandbox(seed: int = 1, house_a: str = "caldrin", house_b: str = "thornmere") -> Game:
    return Game.new(
        challenger_id=1,
        challenger_name="Gon",
        challenger_house=house_a,
        opponent_id=2,
        opponent_name="Sky",
        opponent_house=house_b,
        seed=seed,
    )


def grip(game: Game, pid: int, cards: list[str], *, mana: int = 20, ward: int | None = None):
    player = game.player(pid)
    player.hand = list(cards)
    player.mana = mana
    player.mana_granted = max(player.mana_granted, mana)
    if ward is not None:
        player.ward = ward
    return player


def finish(game: Game) -> None:
    """End the current turn, discarding extras from the end of the hand."""
    check(game.winner is None, "finish() called after the duel ended")
    pid = game.current_id
    if game.pending and game.pending.get("type") == "stash":
        move = game.choose(pid, game.pending["options"][0])
        check(move.ok, f"stash choose: {move.error}")
    player = game.player(pid)
    move = game.end_turn(pid)
    check(move.ok, f"end turn: {move.error}")
    if game.pending and game.pending.get("type") == "discard":
        need = game.pending["need"]
        move = game.discard(pid, player.hand[-need:])
        check(move.ok, f"discard: {move.error}")


def test_catalog() -> None:
    check(len(SPELL_IDS) == 30, f"expected 30 spells, got {len(SPELL_IDS)}")
    check(len(CARDS) == 35, f"expected 35 cards, got {len(CARDS)}")
    specialist_ids = {cid for cid, card in CARDS.items() if card.slot == "specialist"}
    check(specialist_ids == set(SPECIALISTS), "specialist set drifted from the spec")
    for cid, card in CARDS.items():
        if card.school == "house":
            check(card.slot == "spell", f"{cid} should count as attack/defense")
    art = ROOT / "cogs" / "vcc" / "art"
    for cid in CARDS:
        check((art / f"{cid}.jpg").is_file(), f"missing art {cid}.jpg")
    page = (ROOT / "cogs" / "vcc" / "page.html").read_text(encoding="utf-8")
    script = (ROOT / "cogs" / "vcc" / "static" / "game.js").read_text(encoding="utf-8")
    check("Play" in script and "Back" in script, "card popup needs Play and Back")
    check("Practice — easy Tutor" in script, "practice label on the page")
    check("Velmora Curse" in page, "page title")


def test_opening_and_decks() -> None:
    game = sandbox(seed=2)
    gon = game.player(1)
    sky = game.player(2)
    check(len(gon.hand) == 7, f"first player should draw 2 onto 5, hand {len(gon.hand)}")
    check(len(sky.hand) == 5, f"second player opening hand {len(sky.hand)}")
    check(gon.mana == 3 and gon.turns_played == 1, "first turn mana 3")
    check(sky.mana == 0 and sky.turns_played == 0, "opponent has not started")
    for player, house in ((gon, "caldrin"), (sky, "thornmere")):
        owned = set(player.deck) | set(player.hand) | set(player.discard)
        check(len(owned) == 31, f"{house} deck size {len(owned)}")
        check(HOUSE_CARD[house] in owned, f"{house} missing its house card")
        for other, cid in HOUSE_CARD.items():
            if other != house:
                check(cid not in owned, f"{house} drew {cid}")
    move = game.end_turn(1)
    check(move.ok and game.pending and game.pending["need"] == 1, "opening hand discards to 6")
    tossed = gon.hand[-1]
    check(game.discard(1, [tossed]).ok, "discard down")
    check(len(gon.hand) == HAND_LIMIT, "hand should be 6 after the discard")
    check(game.current_id == 2, "turn should pass")


def test_mana_curve() -> None:
    game = sandbox(seed=3)
    seen: dict[int, int] = {}
    for _ in range(40):
        if game.current_id == 1:
            seen[game.current.turns_played] = game.current.mana
            if game.current.turns_played >= 8:
                break
        finish(game)
    check(seen.get(1) == 3, f"turn 1 mana {seen.get(1)}")
    check(seen.get(2) == 4, f"turn 2 mana {seen.get(2)}")
    check(seen.get(3) == 5, f"turn 3 mana {seen.get(3)}")
    check(seen.get(6) == 8, f"turn 6 mana {seen.get(6)}")
    check(seen.get(7) == 8 and seen.get(8) == 8, f"mana cap {seen}")


def test_wildgrowth_raises_cap() -> None:
    game = sandbox(seed=4)
    seen: dict[int, int] = {}
    grew = False
    for _ in range(50):
        if game.winner:
            break
        if game.current_id == 1:
            seen[game.current.turns_played] = game.current.mana
            if game.current.turns_played == 3 and not grew:
                player = game.current
                if "wildgrowth" in player.hand:
                    player.hand.remove("wildgrowth")
                player.hand.append("wildgrowth")
                player.mana = max(player.mana, 5)
                move = game.play(1, "wildgrowth")
                check(move.ok, f"wildgrowth {move.error}")
                check(player.mana_cap_bonus == 1 and player.mana == 1, "wildgrowth grants mana now")
                grew = True
            if game.current.turns_played >= 8 and grew:
                break
        finish(game)
    check(grew, "wildgrowth never resolved")
    check(seen.get(7) == 9 and seen.get(8) == 9, f"raised cap {seen}")


def test_spell_limit_and_sleight() -> None:
    game = sandbox()
    grip(game, 1, ["ember-lance", "chill-touch", "bloom"])
    check(game.play(1, "ember-lance").ok, "first attack")
    check(game.play(1, "chill-touch").ok, "second attack")
    blocked = game.play(1, "bloom")
    check(not blocked.ok, "third attack/defense should be refused")
    check(game.player(2).ward == 17, f"2+1 damage, ward {game.player(2).ward}")

    game = sandbox()
    grip(game, 1, ["lemurs-sleight", "ember-lance", "frostveil", "bloom"])
    check(game.play(1, "lemurs-sleight").ok, "sleight")
    check(game.player(1).spell_slots == 3, "sleight adds a slot")
    check(game.play(1, "ember-lance").ok and game.play(1, "frostveil").ok, "two more spells")
    check(not game.play(1, "bloom").ok, "fourth spell still refused")


def test_specialist_gate() -> None:
    game = sandbox()
    grip(game, 1, ["hush"])
    check(game.player(1).turns_played == 1, "turn 1")
    refused = game.play(1, "hush")
    check(not refused.ok and "3rd" in (refused.error or ""), refused.error or "missing error")
    game.player(1).turns_played = 3
    check(game.play(1, "hush").ok, "specialist on turn 3")
    check(not game.play(1, "hush").ok, "only one specialist")


def test_damage_block_dodge_mirror() -> None:
    game = sandbox()
    grip(game, 1, ["ember-lance"])
    game.player(2).block = 2
    game.play(1, "ember-lance")
    check(game.player(2).ward == 20 and game.player(2).block == 0, "block absorbs ember lance")

    game = sandbox()
    grip(game, 1, ["thornlash"])
    game.player(2).block = 5
    game.play(1, "thornlash")
    check(game.player(2).ward == 18 and game.player(2).block == 5, "thornlash ignores block")

    game = sandbox()
    grip(game, 1, ["thornlash"])
    game.player(2).shade_step = True
    game.player(2).block = 3
    game.play(1, "thornlash")
    check(game.player(2).ward == 20 and not game.player(2).shade_step, "shade dodges the attack")
    check(game.player(2).block == 3, "a dodge doesn't spend block")

    game = sandbox()
    grip(game, 1, ["ember-lance"], ward=20)
    game.player(2).ice_mirror = True
    game.player(1).ice_mirror = True
    game.play(1, "ember-lance")
    check(game.player(1).ward == 18, "mirror bounces the 2 damage")
    check(game.player(2).ward == 20 and not game.player(2).ice_mirror, "defender mirror is spent")
    check(game.player(1).ice_mirror, "bounce does not chain")

    game = sandbox()
    grip(game, 1, ["ember-lance"])
    game.player(2).shade_step = True
    game.player(2).ice_mirror = True
    game.play(1, "ember-lance")
    check(game.player(2).ward == 20 and game.player(2).ice_mirror and not game.player(2).shade_step,
          "shade beats mirror")


def test_kindle_and_blaze() -> None:
    game = sandbox()
    grip(game, 1, ["kindle", "ember-lance"])
    game.player(1).turns_played = 3
    check(game.play(1, "kindle").ok, "kindle")
    check(game.player(1).fire_bonus == 2, "kindle is stored")
    check(game.play(1, "ember-lance").ok, "buffed lance")
    check(game.player(2).ward == 16, f"2+2 damage, ward {game.player(2).ward}")
    check(game.player(1).fire_bonus == 0, "bonus spent")

    game = sandbox()
    grip(game, 1, ["blaze-wall"], ward=20)
    grip(game, 2, [], ward=20)
    check(game.play(1, "blaze-wall").ok, "blaze")
    check(game.player(2).ward == 17 and game.player(1).arm_thorns, "3 damage and thorns armed")
    finish(game)
    check(game.current_id == 2 and game.player(1).thorns_live, "thorns live on their turn")
    grip(game, 2, ["ember-lance"], mana=5, ward=game.player(2).ward)
    game.play(2, "ember-lance")
    check(game.player(1).ward == 18, "ember connects for 2")
    check(game.player(2).ward == 16, "blaze wall bites for 1")
    finish(game)
    check(not game.player(1).thorns_live, "thorns end after that turn")


def test_phoenix_inferno_supernova() -> None:
    game = sandbox()
    grip(game, 1, ["phoenix-spark"], ward=20)
    grip(game, 2, [], ward=2)
    game.play(1, "phoenix-spark")
    check(game.winner == 1 and game.reason == "ward", "phoenix finishes")
    check(game.player(1).ward == 23, "phoenix heals 3 on the kill")

    game = sandbox()
    grip(game, 1, ["phoenix-spark"])
    grip(game, 2, [], ward=10)
    game.play(1, "phoenix-spark")
    check(game.winner is None and game.player(1).ward == 20 and game.player(2).ward == 8, "no heal")

    game = sandbox()
    grip(game, 1, ["inferno", "bloom"], ward=20)
    grip(game, 2, ["frostveil"], ward=20)
    game.play(1, "inferno")
    check(game.player(2).ward == 14, "inferno 6")
    check(game.player(1).hand == [] and "bloom" in game.player(1).discard, "caster discards")
    check("frostveil" in game.player(2).discard and game.player(2).hand == [], "they discard")

    game = sandbox()
    grip(game, 1, ["supernova"], ward=20)
    grip(game, 2, [], ward=20)
    game.play(1, "supernova")
    check(game.player(2).ward == 12 and game.player(1).ward == 17, "8 and the 3 backlash")
    check(game.winner is None, "both still standing")

    game = sandbox()
    grip(game, 1, ["supernova"], ward=3)
    grip(game, 2, [], ward=20)
    game.play(1, "supernova")
    check(game.winner == 2 and game.player(1).ward == 0, "backlash can lose the duel")


def test_control_cards() -> None:
    game = sandbox()
    grip(game, 1, ["hush"])
    game.player(1).turns_played = 3
    game.play(1, "hush")
    finish(game)
    check(game.current_id == 2, "sky's turn")
    grip(game, 2, ["ember-lance"], mana=5)
    game.play(2, "ember-lance")
    check(game.player(1).ward == 20, "hush fizzles the 1-cost")
    check("ember-lance" in game.player(2).discard, "fizzled card is discarded")

    game = sandbox()
    grip(game, 1, ["hush"])
    game.player(1).turns_played = 3
    game.play(1, "hush")
    finish(game)
    grip(game, 2, ["cinder-spray"], mana=5)
    game.play(2, "cinder-spray")
    check(game.player(1).ward == 18, "hush ignores a 2-cost")
    check(game.player(2).cancel_one_cost, "the 1-cost hush is still waiting")

    game = sandbox()
    grip(game, 1, ["eclipse"])
    game.player(2).block = 4
    game.play(1, "eclipse")
    check(game.player(2).block == 0 and game.player(2).cancel_next, "eclipse wipes and cancels")

    game = sandbox()
    grip(game, 1, ["chill-touch"])
    game.play(1, "chill-touch")
    check(game.player(2).next_cost_add == 1 and game.player(2).ward == 19, "chill")
    finish(game)
    grip(game, 2, ["ember-lance", "bloom"], mana=1)
    refused = game.play(2, "ember-lance")
    check(not refused.ok and game.player(2).next_cost_add == 1, "can't pay, surcharge stays")
    game.player(2).mana = 3
    game.play(2, "ember-lance")
    check(game.player(2).mana == 1 and game.player(2).next_cost_add == 0, "surcharge spent")

    game = sandbox()
    grip(game, 1, ["rootbind"])
    game.play(1, "rootbind")
    finish(game)
    grip(game, 2, ["ember-lance", "frostveil"], mana=5)
    check(not game.play(2, "ember-lance").ok, "rootbind blocks fire")
    check(game.play(2, "frostveil").ok and game.player(2).block == 2, "other schools still play")

    game = sandbox()
    grip(game, 1, ["permafrost"])
    before = len(game.player(2).hand)
    # grip replaced only gon's hand; sky still has the opening 5
    game.play(1, "permafrost")
    finish(game)
    check(game.player(2).turns_played == 0, "a skipped turn is not one of theirs")
    check(len(game.player(2).hand) == before, "skip does not draw")
    check(game.current_id == 1 and game.player(1).turns_played == 2, "gon goes again")


def test_shadow_and_lantern() -> None:
    game = sandbox()
    game.current_index = 1
    game.player(2).turns_played = 3
    grip(game, 2, ["shade-step"], mana=5)
    game.play(2, "shade-step")
    check(game.player(2).shade_step, "shade up")
    game.current_index = 0
    grip(game, 1, ["lantern-seal"], mana=5)
    game.play(1, "lantern-seal")
    check(not game.player(2).shade_step, "lantern breaks unused shade")

    game = sandbox()
    grip(game, 1, ["hex-swap"], mana=10)
    game.player(1).turns_played = 3
    game.player(1).ward = 20
    game.player(2).ward = 10
    game.play(1, "hex-swap")
    check(game.player(1).ward == 15 and game.player(2).ward == 15, "diff 10 moves 5")
    finish(game)
    grip(game, 2, ["lantern-seal"], mana=5)
    game.play(2, "lantern-seal")
    check(game.player(1).ward == 20 and game.player(2).ward == 10, "lantern undoes the swap")

    game = sandbox()
    grip(game, 1, ["hex-swap"], mana=10)
    game.player(1).turns_played = 3
    game.player(1).ward = 20
    game.player(2).ward = 18
    game.play(1, "hex-swap")
    check(game.player(1).ward == 18 and game.player(2).ward == 20, "small swap is complete")

    game = sandbox()
    grip(game, 1, ["pickpocket-charm"], mana=5)
    game.player(1).turns_played = 3
    game.player(2).hand = ["frostveil"]
    game.play(1, "pickpocket-charm")
    check("frostveil" in game.player(1).hand and game.player(2).hand == [], "steal")

    game = sandbox()
    grip(game, 1, ["curse-of-clumsy-fingers"], mana=5)
    game.player(1).turns_played = 3
    game.player(2).hand = ["bloom"]
    game.play(1, "curse-of-clumsy-fingers")
    check(game.player(2).hand == [] and "bloom" in game.player(2).discard, "clumsy discard")


def test_star_and_house() -> None:
    heads = tails = False
    for seed in range(30):
        game = sandbox(seed=seed)
        grip(game, 1, ["wishing-coin"], mana=1)
        game.player(1).turns_played = 3
        game.player(1).deck = ["bloom"] * 8
        game.play(1, "wishing-coin")
        if len(game.player(1).hand) == 0:
            tails = True
        if len(game.player(1).hand) == 2:
            heads = True
    check(heads and tails, f"coin should be able to land both ways ({heads}, {tails})")

    game = sandbox()
    grip(game, 1, ["comet-call", "bloom", "frostveil", "moonwell"])
    game.play(1, "comet-call")
    check(game.player(2).ward == 17, "3 cards left in hand, 3 damage")

    game = sandbox()
    grip(game, 1, ["starfall"])
    game.player(1).schools_played = {"fire", "frost"}
    game.play(1, "starfall")
    check(game.player(2).ward == 17, "fire, frost, and star")

    game = sandbox()
    grip(game, 1, ["fate-shuffle"], mana=5)
    game.player(1).turns_played = 3
    game.player(1).deck = ["bloom"] * 6
    game.player(2).hand = ["frostveil"]
    game.player(2).deck = ["moonwell"] * 6
    game.play(1, "fate-shuffle")
    check(len(game.player(1).hand) == 4 and len(game.player(2).hand) == 4, "both draw 4")
    check("fate-shuffle" in game.player(1).discard, "fate itself is spent")

    game = sandbox()
    grip(game, 1, ["squirrels-stash"], mana=5)
    game.player(1).deck = ["bloom", "frostveil"]
    game.play(1, "squirrels-stash")
    check(game.pending and game.pending["type"] == "stash", "stash asks for a keep")
    check(game.pending["options"] == ["frostveil", "bloom"], f"draw order {game.pending}")
    check(not game.play(1, "bloom").ok, "can't play during the choice")
    check(game.choose(1, "bloom").ok, "keep bloom")
    check(game.player(1).hand == ["bloom"], "kept the chosen card")
    check("frostveil" in game.player(1).discard and "squirrels-stash" in game.player(1).discard, "other is discarded")

    game = sandbox()
    grip(game, 1, ["antelope-dash"], mana=5)
    game.play(1, "antelope-dash")
    finish(game)
    check(game.current_id == 1 and game.player(1).turns_played == 2, "extra turn")
    grip(game, 1, ["antelope-dash"], mana=5)
    game.play(1, "antelope-dash")
    finish(game)
    check(game.current_id == 2, "dash is once per game")

    game = sandbox()
    grip(game, 1, ["wolf-howl"])
    game.player(1).fire_cards_played = 2
    game.play(1, "wolf-howl")
    check(game.player(2).ward == 15, "3 plus 2 fire cards")

    game = sandbox()
    grip(game, 1, ["bear-hug"])
    game.play(1, "bear-hug")
    check(game.player(1).block == 4 and game.player(2).ward == 19, "bear hug")

    game = sandbox()
    grip(game, 1, ["glacier-ward"])
    game.play(1, "glacier-ward")
    check(game.player(1).ward == 25, "glacier ward stacks past 20")

    game = sandbox()
    grip(game, 1, ["snow-shroud"])
    game.player(1).deck = ["moonwell"]
    game.play(1, "snow-shroud")
    check(game.player(1).block == 3 and game.player(1).hand == ["moonwell"], "shroud draws")

    game = sandbox()
    grip(game, 1, ["sapling-shield"], ward=20)
    game.play(1, "sapling-shield")
    finish(game)
    game.player(1).ward = 10
    finish(game)
    check(game.current_id == 1 and game.player(1).ward == 11, "sapling heals next turn")


def test_privacy_end_and_roundtrip() -> None:
    game = sandbox(seed=9)
    secret = "eclipse"
    for player in game.players.values():
        player.hand = [cid for cid in player.hand if cid != secret]
        player.deck = [cid for cid in player.deck if cid != secret]
        player.discard = [cid for cid in player.discard if cid != secret]
    game.player(2).hand = [secret]
    game.events = []
    view = game.view_for(1)
    blob = json.dumps(view["opponent"])
    check("hand" not in view["opponent"], "opponent hand key leaked")
    check(secret not in blob, "secret card leaked into the opponent panel")
    check(isinstance(view["you"]["deck"], int), "deck count only")
    check(any(card["id"] for card in view["you"]["hand"]), "your hand is visible to you")

    grip(game, 1, ["ember-lance"])
    game.player(2).ward = 2
    game.play(1, "ember-lance")
    check(game.winner == 1 and game.reason == "ward", "ward 0 ends the duel")
    check(not game.play(1, "ember-lance").ok, "no plays after the end")
    check(game.forfeit(2).error, "forfeit after the end")

    game = sandbox()
    check(game.forfeit(1).ok and game.winner == 2 and game.reason == "forfeit", "forfeit")
    game = sandbox()
    check(game.timeout().ok and game.winner == 2 and game.reason == "timeout", "timeout loses")

    game = sandbox(seed=5)
    snap = game.to_dict()
    other = Game.from_dict(snap)
    drawn = game._draw(game.player(1), 1)
    drawn_other = other._draw(other.player(1), 1)
    check(drawn == drawn_other, "rng and deck survive a save")

    check(dropdown_amounts(night=False, balances=(100, 100), balances_known=True) == (0,), "outside a night the stake list is Free")
    check(dropdown_amounts(night=True, balances=None, balances_known=False) == (0, 5, 10), "night menu before houses are known")
    check(dropdown_amounts(night=True, balances=None, balances_known=True) == (0,), "no Cup balance means Free only")
    check(dropdown_amounts(night=True, balances=(6, 20), balances_known=True) == (0, 5), "10 is hidden when a house has 6")
    check(dropdown_amounts(night=True, balances=(10, 10), balances_known=True) == (0, 5, 10), "both houses can cover 10")
    check(dropdown_amounts(night=True, balances=(0, 40), balances_known=True) == (0,), "a house at 0 can only play Free")
    check(stake_decision(night=False, wager=0, balances=(0, 0)) == "ok", "free play outside a night")
    check(stake_decision(night=False, wager=5, balances=(100, 100)) == "no-night", "stakes wait for a wager night")
    check(stake_decision(night=True, wager=5, balances=None) == "no-balance", "no balance, no stake")
    check(stake_decision(night=True, wager=10, balances=(9, 40)) == "short", "neither house can overstake")
    check(stake_decision(night=True, wager=10, balances=(10, 12)) == "ok", "both houses cover 10")
    check(stake_decision(night=True, wager=7, balances=(20, 20)) == "invalid", "only Free, 5, and 10")
    moved, note = points_to_move(0, 50)
    check(moved == 0 and note == "free", "free games move no points")
    moved, note = points_to_move(10, 10)
    check(moved == 10 and note == "paid", "a covered stake moves in full")
    moved, note = points_to_move(10, 4)
    check(moved == 4 and note == "partial", "a house keeps everything beyond what it can cover")
    moved, note = points_to_move(5, 0)
    check(moved == 0 and note == "uncovered", "an empty house pays nothing")
    moved, note = points_to_move(5, -2)
    check(moved == 0 and note == "uncovered", "a negative house pays nothing")
    check(collectible_kind(0) == "spell" and collectible_kind(1) == "frog", "prizes alternate frog and spell")
    check(pick_from(tuple(CARDS), 0) in CARDS, "a prize can be any of the 35 cards")
    check(len(tuple(CARDS)) == 35, "spell album draws from all 35 cards")
    house_card = pick_from(tuple(CARDS), list(CARDS).index("squirrels-stash"))
    check(house_card == "squirrels-stash", "house cards are in the spell album")


def test_wager_night_and_spell_album() -> None:
    path = Path(tempfile.mkdtemp(prefix="vcc-wager-")) / "vcc_state.json"
    book = Book(path, turn_timeout=240, accept_timeout=60)
    check(not book.wager_night_on(1_000), "no wager night yet")
    ok, code, night = book.begin_wager_night(by=7, channel_id=9, guild_id=3, minutes=30, now=1_000)
    check(ok and code == "started" and night and night["ends_at"] == 1_000 + 30 * 60, "30 minute night")
    check(book.wager_night_on(1_000 + 29 * 60), "night still open just before the clock")
    check(not book.wager_night_on(1_000 + 30 * 60), "night closes when the clock hits")
    expired = book.pop_expired_wager_night(1_000 + 30 * 60)
    check(expired and expired["channel_id"] == 9, "expired night is handed back once")
    check(book.pop_expired_wager_night(1_000 + 30 * 60) is None, "a closed night is not announced twice")

    ok, _, _ = book.begin_wager_night(by=7, channel_id=9, guild_id=3, minutes=None, now=2_000)
    check(ok and book.wager_night_on(9_999_999), "an open-ended night stays up")
    again, code, _ = book.begin_wager_night(by=7, channel_id=9, guild_id=3, minutes=60, now=2_001)
    check(not again and code == "already", "a second night waits until the first ends")
    bad, code, _ = book.begin_wager_night(by=7, channel_id=9, guild_id=3, minutes=15, now=2_002)
    check(not bad and code == "already", "length isn't checked while a night is running")
    ended = book.end_wager_night()
    check(ended and not book.wager_night_on(2_003), "staff can end the night")
    bad, code, _ = book.begin_wager_night(by=7, channel_id=9, guild_id=3, minutes=15, now=2_004)
    check(not bad and code == "length", "length is 30 minutes, 1 hour, or until staff end it")

    challenge = book.open_challenge(
        channel_id=4,
        guild_id=3,
        challenger_id=1,
        challenger_name="Gon",
        challenger_house="caldrin",
        opponent_id=2,
        opponent_name="Sky",
        opponent_house="moonveil",
        wager=10,
        mode="wager",
        now=3_000,
    )
    check(challenge.wager == 10 and challenge.mode == "wager", "the challenge shows the stake")
    book.start_game(challenge, seed=3, now=3_001)
    table = book.games[challenge.id]
    check(table.wager == 10 and table.mode == "wager", "the table keeps the agreed stake")
    count, is_new = book.add_spell(1, "ember-lance")
    check(count == 1 and is_new, "first spell card is new")
    count, is_new = book.add_spell(1, "ember-lance")
    check(count == 2 and not is_new, "a second copy is a duplicate")
    book.add_spell(1, "not-a-card")
    book.save()
    reloaded = Book(path, turn_timeout=240, accept_timeout=60)
    check(reloaded.spells_of(1) == {"ember-lance": 2}, "unknown cards are dropped on load")
    check(reloaded.games[challenge.id].wager == 10, "the stake survives a restart")
    check(reloaded.games[challenge.id].mode == "wager", "wager mode survives a restart")


def _human_step(game: Game, rng: random.Random):
    pid = game.current_id
    if game.pending and game.pending.get("type") == "stash":
        return game.choose(pid, game.pending["options"][0])
    if game.pending and game.pending.get("type") == "discard":
        need = int(game.pending["need"])
        return game.discard(pid, list(game.player(pid).hand)[:need])
    legal = legal_card_ids(game, pid)
    attacks = [cid for cid in legal if card_of(cid).tag == "Attack"]
    pool = attacks or legal
    if pool:
        return game.play(pid, rng.choice(pool))
    return game.end_turn(pid)


def test_cpu_only_legal_moves() -> None:
    ends = attacks = defenses = 0
    for i in range(400):
        choice = choose_card(
            ["ember-lance", "frostveil"],
            my_ward=6,
            opp_ward=20,
            rng=random.Random(i),
        )
        if choice is None:
            ends += 1
        elif choice == "ember-lance":
            attacks += 1
        elif choice == "frostveil":
            defenses += 1
        else:
            check(False, "tutor picked a card that was not legal")
    check(choose_card([], my_ward=20, opp_ward=20, rng=random.Random(0)) is None, "no legal card means end")
    check(DEFEND_CHANCE < 0.25 and END_EARLY > 0.2, "tutor stays easy")
    check(attacks > 60, "most plays are random, so the attack still comes out")
    check(defenses < 200, "the tutor does not block every time it is losing")
    check(ends > 100, "tutor often ends without spending its mana")

    try:
        Game.new(
            challenger_id=5,
            challenger_name="Gon",
            challenger_house="nope",
            opponent_id=8,
            opponent_name="Sky",
            opponent_house="caldrin",
        )
        check(False, "a real duel still needs two houses")
    except ValueError:
        pass

    for seed in range(12):
        game = Game.new(
            challenger_id=5,
            challenger_name="Gon",
            challenger_house="caldrin",
            opponent_id=CPU_ID,
            opponent_name=TUTOR_NAME,
            opponent_house="moonveil",
            seed=seed,
            practice=True,
            cpu_id=CPU_ID,
        )
        rng = random.Random(1000 + seed)
        guard = 0
        while game.winner is None and guard < 500:
            guard += 1
            if game.current_id == CPU_ID:
                steps = run_cpu(game)
                check(bool(steps), f"tutor acted in seed {seed}")
                check(
                    all(step.was_legal and step.move.ok for step in steps),
                    f"tutor made an illegal move in seed {seed}",
                )
                check(
                    all(step.kind != "forfeit" for step in steps),
                    f"tutor yielded instead of playing in seed {seed}",
                )
                check(
                    game.winner is not None or game.current_id != CPU_ID,
                    f"tutor kept the turn in seed {seed}",
                )
            else:
                move = _human_step(game, rng)
                check(move.ok, f"human step failed in seed {seed}: {move.error}")
        check(game.winner is not None, f"practice game did not finish in seed {seed}")
        check(game.practice and game.cpu_id == CPU_ID, "practice flag stays on the game")

    trapped = Game.new(
        challenger_id=5,
        challenger_name="Gon",
        challenger_house="caldrin",
        opponent_id=CPU_ID,
        opponent_name=TUTOR_NAME,
        opponent_house="vashara",
        seed=4,
        practice=True,
        cpu_id=CPU_ID,
    )
    trapped.current_index = 1
    tutor = trapped.player(CPU_ID)
    tutor.hand = ["shade-step"]
    tutor.mana = 8
    tutor.turns_played = 1
    tutor.specialists_played = 0
    steps = run_cpu(trapped)
    check(all(step.was_legal and step.move.ok for step in steps), "specialist trap stayed legal")
    check(not tutor.shade_step, "tutor did not play a specialist off-turn")
    check("shade-step" in tutor.hand or "shade-step" in tutor.discard, "unplayed specialist stayed in the tutor's cards")
    restored = Game.from_dict(trapped.to_dict())
    check(restored.practice and restored.cpu_id == CPU_ID, "practice survives a save")


def test_practice_awards_nothing() -> None:
    from cogs.vcc_game import Vcc

    path = Path(tempfile.mkdtemp(prefix="vcc-practice-")) / "vcc_state.json"
    book = Book(path, turn_timeout=240, accept_timeout=60)
    book.begin_wager_night(by=7, channel_id=9, guild_id=3, minutes=None, now=50)
    raw = book.start_practice(
        channel_id=9,
        guild_id=3,
        player_id=5,
        player_name="Gon",
        player_house="",
        now=60,
        seed=2,
    )
    saved = path.read_text(encoding="utf-8")
    check(raw not in saved, "practice token was written to disk")
    table = next(iter(book.games.values()))
    check(table.mode == "practice" and table.wager == 0, "practice is not a wager")
    check(list(table.tokens.values()) == [5], "only the human gets a link")
    check(CPU_ID not in book.by_user, "the tutor is not a player account")
    game = table.game
    human = game.player(5)
    tutor = game.player(CPU_ID)
    check(tutor.name == TUTOR_NAME, "opponent is the tutor")
    check(
        len(human.hand) + len(human.deck) + len(human.discard) == 30,
        "a player with no house practices with the shared spells",
    )
    check(
        len(tutor.hand) + len(tutor.deck) + len(tutor.discard) == 31,
        "the tutor still has a house card",
    )
    view = game.view_for(5)
    check(view["practice"] is True, "the page is told this is practice")
    check("hand" not in view["opponent"], "practice still hides the other hand")
    while len(human.hand) > HAND_LIMIT:
        human.discard.append(human.hand.pop())
    action = book.end_turn(raw, now=61)
    check(action.status == 200, "ending a practice turn is accepted")
    check(action.view["practice"] is True, "the reply is still labeled practice")
    check("hand" not in action.view["opponent"], "the tutor's hand stays hidden after it plays")
    check(action.view["over"] or action.view["your_turn"], "the tutor finished before the reply")
    spells_before = book.spells_of(5)

    class Ledger:
        def __init__(self):
            self.user = type("User", (), {"id": 1})()
            self.records = []
            self.frogs = []

        def get_cog(self, _name):
            return self

        def record(self, **kwargs):
            self.records.append(kwargs)
            return {"id": "nope"}

        def add_card(self, *args, **kwargs):
            self.frogs.append(args)
            return 1, True

        def roll(self):
            return "lumen"

        def season_active(self):
            return True

        def house_totals(self, scope="season"):
            return [("caldrin", 40)]

    ledger = Ledger()
    table.game.winner = 5
    table.game.reason = "ward"
    table.points_applied = False
    points, note = Vcc(ledger)._award(table)
    label, text = Vcc(ledger)._reward_lines(table)
    check(points == 0 and note == "practice", "practice pays nothing")
    check(table.prize is None, "practice grants no card")
    check(not ledger.records and not ledger.frogs, "practice does not touch the ledger or the frog album")
    check(book.spells_of(5) == spells_before, "practice does not add a spell card")
    check(book.wager_night_on(70), "practice does not close a wager night")
    check(label == "Practice" and "No house points" in text, "the Discord result says practice and no points")
    check("wager night" in text and "records" in text, "the result says it is not counted")


def test_illegal_does_not_mutate() -> None:
    game = sandbox()
    hand = list(game.player(1).hand)
    mana = game.player(1).mana
    move = game.play(1, "inferno")
    check(not move.ok, "inferno isn't a free play from a random hand")
    check(game.player(1).hand == hand and game.player(1).mana == mana, "failed play is atomic")
    check(not game.play(2, hand[0]).ok, "can't play on the opponent's turn")


async def test_http() -> None:
    from aiohttp.test_utils import TestClient, TestServer

    path = Path(tempfile.mkdtemp(prefix="vcc-http-")) / "vcc_state.json"
    book = Book(path, turn_timeout=240, accept_timeout=60)
    lock = asyncio.Lock()
    app = create_app(book, Hub(), lock)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        challenge = book.open_challenge(
            channel_id=1,
            guild_id=1,
            challenger_id=1,
            challenger_name="Gon",
            challenger_house="caldrin",
            opponent_id=2,
            opponent_name="Sky",
            opponent_house="moonveil",
        )
        raws = book.start_game(challenge, seed=7)
        raw_gon = raws[1]
        saved = path.read_text(encoding="utf-8")
        check(raw_gon not in saved, "raw token was written to disk")
        game = book.games[challenge.id].game
        secret = "eclipse"
        for player in game.players.values():
            player.hand = [cid for cid in player.hand if cid != secret]
            player.deck = [cid for cid in player.deck if cid != secret]
            player.discard = [cid for cid in player.discard if cid != secret]
        game.events = []
        game.player(2).hand = [secret]

        state = await client.get(f"/api/{raw_gon}/state")
        body = await state.text()
        check(state.status == 200, f"state {state.status}")
        check(state.headers.get("Referrer-Policy") == "no-referrer", "referrer policy")
        check(secret not in body, "HTTP state leaked the other hand")
        sky = await client.get(f"/api/{raws[2]}/state")
        sky_body = await sky.text()
        check(secret in sky_body, "the owner can see their card")

        missing = await client.get("/api/not-a-real-token/state")
        check(missing.status == 404, "unknown token")
        art = await client.get("/art/moonwell.jpg")
        check(art.status == 200, "card art is served")
        slipped = await client.get("/art/../bot.py")
        check(slipped.status == 404, "art path escape")

        played = await client.post(f"/api/{raw_gon}/play", json={"card": "not-a-card"})
        check(played.status == 400, "bad play")
        quit_resp = await client.post(f"/api/{raw_gon}/forfeit", json={})
        quit_body = await quit_resp.json()
        check(quit_resp.status == 200 and quit_body.get("over"), "forfeit over the API")
        check(secret not in json.dumps(quit_body.get("opponent")), "forfeit view hides the hand")

        async with lock:
            digests = book.expire_tokens(book.games[challenge.id])
        check(digests, "tokens expire")
        after = await client.get(f"/api/{raw_gon}/state")
        after_body = await after.json()
        check(after.status == 200 and after_body.get("tombstone"), "result tombstone")
        check(secret not in json.dumps(after_body), "tombstone has no cards")
        again = await client.post(f"/api/{raw_gon}/play", json={"card": "bloom"})
        check(again.status == 410, "expired token cannot play")
    finally:
        await client.close()


def main() -> int:
    test_catalog()
    test_opening_and_decks()
    test_mana_curve()
    test_wildgrowth_raises_cap()
    test_spell_limit_and_sleight()
    test_specialist_gate()
    test_damage_block_dodge_mirror()
    test_kindle_and_blaze()
    test_phoenix_inferno_supernova()
    test_control_cards()
    test_shadow_and_lantern()
    test_star_and_house()
    test_privacy_end_and_roundtrip()
    test_wager_night_and_spell_album()
    test_cpu_only_legal_moves()
    test_practice_awards_nothing()
    test_illegal_does_not_mutate()
    asyncio.run(test_http())
    if FAILURES:
        print(f"{len(FAILURES)} failed")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
