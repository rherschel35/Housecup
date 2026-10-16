#!/usr/bin/env python3
"""Every set won is credited, including the series loser's.

A 2–1 credits the winner 2 and the loser 1 on the night tally and the
lifetime record, even when the series bank (match_wins) is larger.
A forfeit or fizzle mid-series credits the sets already won.
Each settled result is written to the duel state file immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-set-credit-"))
os.environ.setdefault("DISCORD_TOKEN", "")

from cogs.duels import DUELS_PATH, Duel, Duels  # noqa: E402
from cogs.store import Store  # noqa: E402


class Bot:
    def __init__(self):
        self.user = type("User", (), {"id": 1})()
        self.cogs = {}
        self.channel = Channel()

    def get_cog(self, name):
        return self.cogs.get(name)

    def get_channel(self, channel_id):
        return self.channel


class Channel:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        embeds = kwargs.get("embeds")
        if embeds is None and kwargs.get("embed") is not None:
            embeds = [kwargs["embed"]]
        self.sent.append(list(embeds or []))


class Member:
    def __init__(self, uid: int, name: str):
        self.id = uid
        self.display_name = name
        self.roles = []


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def tally_wins(duels: Duels, uid: int) -> int:
    night = duels.state.get("duel_night") or {}
    entry = (night.get("tally") or {}).get(str(uid)) or {}
    return int(entry.get("wins") or 0)


def saved_results() -> list[dict]:
    with open(DUELS_PATH, encoding="utf-8") as fh:
        return json.load(fh).get("settled_results") or []


def assert_result(row: dict, score: str, credits: dict[int, int]) -> None:
    assert isinstance(row.get("timestamp"), (int, float))
    assert row["timestamp"] > 0
    assert row["set_score"] == score
    players = row["players"]
    assert players and all("id" in p and "name" in p for p in players)
    credited = row["wins_credited"]
    for uid, wins in credits.items():
        assert int(credited[str(uid)]) == wins
        assert any(int(p["id"]) == uid and p["name"] for p in players)


async def test_two_one_and_abandoned(duels: Duels, store: Store) -> None:
    def house(uid: int, key: str) -> None:
        store.set_override(uid, key)

    # Off-night: a 2–1 whose series bank is 4 still pays 2 and 1, and both
    # land on the lifetime record.
    w, l = Member(201, "Ada"), Member(202, "Bea")
    house(w.id, "thornmere")
    house(l.id, "vashara")
    before = len(saved_results()) if DUELS_PATH.exists() else 0
    off = duels.settle(w, l, match_wins=4, winner_sets=2, loser_sets=1)
    assert off["awarded"] == 2 and off["loser_awarded"] == 1
    assert off["night"] is False
    assert duels.record_of(w.id)["w"] == 2
    assert duels.record_of(l.id)["w"] == 1
    assert duels.record_of(w.id)["l"] == 1
    assert duels.record_of(l.id)["l"] == 2
    disk = saved_results()
    assert len(disk) == before + 1
    assert_result(disk[-1], "2-1", {w.id: 2, l.id: 1})
    assert disk[-1]["players"][0]["name"] == "Ada"
    assert disk[-1]["players"][1]["name"] == "Bea"

    ok, _msg, _night = duels._begin_duel_night(
        minutes=30, by=1, channel_id=42, guild_id=1,
    )
    assert ok

    # Normal finish of a best-of that is sitting at 2–1. match_wins is the
    # unplayed series bank (3). Both the night tally and the record get 2 and 1.
    sw, sl = Member(203, "SeriesW"), Member(204, "SeriesL")
    house(sw.id, "thornmere")
    house(sl.id, "vashara")
    match = Duel(duels, None, sw, sl, best_of=5)
    assert match.match_wins == 3 and match.series_to_win == 3
    match.state = "active"
    match.started_at = time.time()
    match.set_score = {sw.id: 2, sl.id: 1}
    duels.mark_busy(match, sw, sl)
    await match._finish(sw, sl, forfeit=False)
    assert tally_wins(duels, sw.id) == 2
    assert tally_wins(duels, sl.id) == 1
    assert duels.record_of(sw.id)["w"] == 2
    assert duels.record_of(sl.id)["w"] == 1
    assert_result(saved_results()[-1], "2-1", {sw.id: 2, sl.id: 1})
    assert sw.id not in duels.active and sl.id not in duels.active

    # Forfeit mid-series: sets already won, not the series bank of 4.
    fw, fl = Member(205, "ForfeitW"), Member(206, "ForfeitL")
    house(fw.id, "caldrin")
    house(fl.id, "moonveil")
    forfeited = Duel(duels, None, fw, fl, best_of=7)
    assert forfeited.match_wins == 4
    forfeited.state = "active"
    forfeited.started_at = time.time()
    forfeited.set_score = {fw.id: 2, fl.id: 1}
    duels.mark_busy(forfeited, fw, fl)
    await forfeited._finish(fw, fl, forfeit=True)
    assert tally_wins(duels, fw.id) == 2
    assert tally_wins(duels, fl.id) == 1
    assert duels.record_of(fw.id)["w"] == 2
    assert duels.record_of(fl.id)["w"] == 1
    row = saved_results()[-1]
    assert row["ending"] == "forfeit"
    assert_result(row, "2-1", {fw.id: 2, fl.id: 1})

    # Fizzle mid-series banks the same sets and does not hand out the series bank.
    xw, xl = Member(207, "FizzleW"), Member(208, "FizzleL")
    house(xw.id, "veyren")
    house(xl.id, "thornmere")
    fizzled = Duel(duels, None, xw, xl, best_of=9)
    assert fizzled.match_wins == 5
    fizzled.state = "active"
    fizzled.started_at = time.time()
    fizzled.set_score = {xw.id: 2, xl.id: 1}
    duels.mark_busy(fizzled, xw, xl)
    await fizzled._bank_abandoned_sets(ending="fizzle")
    assert tally_wins(duels, xw.id) == 2
    assert tally_wins(duels, xl.id) == 1
    assert duels.record_of(xw.id)["w"] == 2
    assert duels.record_of(xl.id)["w"] == 1
    assert duels.streak_of(xw.id) == 0
    row = saved_results()[-1]
    assert row["ending"] == "fizzle"
    assert_result(row, "2-1", {xw.id: 2, xl.id: 1})
    assert xw.id not in duels.busy and xl.id not in duels.busy

    # Draw uses the same abandoned-set path.
    dw, dl = Member(209, "DrawW"), Member(210, "DrawL")
    house(dw.id, "thornmere")
    house(dl.id, "caldrin")
    drawn = Duel(duels, None, dw, dl, best_of=7)
    drawn.state = "active"
    drawn.started_at = time.time()
    drawn.set_score = {dw.id: 1, dl.id: 1}
    duels.mark_busy(drawn, dw, dl)
    await drawn._bank_abandoned_sets(ending="draw")
    assert tally_wins(duels, dw.id) == 1
    assert tally_wins(duels, dl.id) == 1
    assert duels.record_of(dw.id)["w"] == 1
    assert duels.record_of(dl.id)["w"] == 1
    assert saved_results()[-1]["ending"] == "draw"

    # The rows are on disk, not only on the in-memory list.
    assert duels.state["settled_results"][-1]["set_score"] == saved_results()[-1]["set_score"]


async def test_finale_logs_every_player(duels: Duels, store: Store, bot: Bot) -> None:
    night = duels.state["duel_night"]
    assert night is not None
    capture = Capture()
    logger = logging.getLogger("velmora.duels")
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    try:
        embeds = await duels._finalize_duel_night(night, reason="staff")
    finally:
        logger.removeHandler(capture)
    text = "\n".join(embed.description or "" for embed in embeds)
    posted = "\n".join(
        embed.description or ""
        for batch in bot.channel.sent
        for embed in batch
    )
    # Staff finale returns the embeds; the auto-expire post is the other path.
    # Either surface has to name each duelist's wins.
    blob = text or posted
    for uid, wins in ((203, 2), (204, 1), (205, 2), (206, 1), (207, 2), (208, 1)):
        assert f"<@{uid}>: **{wins}** wins" in blob
    logged = [line for line in capture.lines if line.startswith("Duel Night tally:")]
    assert logged
    by_id = {}
    for line in logged:
        # id=203 display_name=SeriesW wins=2 pool_share=75 bonuses=20 total=95
        parts = dict(
            piece.split("=", 1) for piece in line.split("Duel Night tally: ", 1)[1].split()
        )
        by_id[int(parts["id"])] = parts
        for key in ("id", "display_name", "wins", "pool_share", "bonuses", "total"):
            assert key in parts and parts[key] != ""
    assert by_id[203]["display_name"] == "SeriesW"
    assert by_id[203]["wins"] == "2"
    assert by_id[204]["display_name"] == "SeriesL"
    assert by_id[204]["wins"] == "1"
    assert int(by_id[203]["pool_share"]) + int(by_id[203]["bonuses"]) == int(by_id[203]["total"])


async def test_trio_and_grand_games(duels: Duels, store: Store) -> None:
    """Rounds and steps already won are credited, including for the side that loses."""
    duels.state["duel_night"] = None
    duels.save()
    ok, _msg, _night = duels._begin_duel_night(
        minutes=30, by=1, channel_id=42, guild_id=1,
    )
    assert ok

    class Signup:
        cog = duels
        is_house = True
        house_a = "thornmere"
        house_b = "vashara"
        message = None

    signup = Signup()
    signup.team_a = [Member(301, "A1"), Member(302, "A2"), Member(303, "A3")]
    signup.team_b = [Member(304, "B1"), Member(305, "B2"), Member(306, "B3")]
    for m in signup.team_a:
        store.set_override(m.id, "thornmere")
        duels.busy.add(m.id)
    for m in signup.team_b:
        store.set_override(m.id, "vashara")
        duels.busy.add(m.id)

    # Build a TrioMatch without going through Discord signup.
    from cogs.duels import TrioMatch

    trio = TrioMatch(signup)
    trio.score_a = 1
    trio.score_b = 0
    await trio._bank_partial("fizzle")
    for m in signup.team_a:
        assert duels.record_of(m.id)["w"] == 1
        assert tally_wins(duels, m.id) == 1
        assert duels.record_of(m.id)["trio_w"] == 0
    for m in signup.team_b:
        assert duels.record_of(m.id)["w"] == 0
        assert tally_wins(duels, m.id) == 0
    row = saved_results()[-1]
    assert row["mode"] == "trio" and row["ending"] == "fizzle"
    assert row["set_score"] == "1-0"
    assert int(row["wins_credited"]["301"]) == 1
    assert int(row["wins_credited"]["304"]) == 0
    assert all(m.id not in duels.busy for m in signup.team_a + signup.team_b)

    gw, gl = Member(401, "GrandW"), Member(402, "GrandL")
    store.set_override(gw.id, "caldrin")
    store.set_override(gl.id, "moonveil")
    from cogs.duels import GrandDuel

    grand = GrandDuel(duels, None, gw, gl)
    grand.started_at = time.time()
    grand.state = "playing"
    grand.score = {gw.id: 6, gl.id: 4}
    duels.mark_busy(grand, gw, gl)
    await grand._finish(gw, gl)
    assert duels.record_of(gw.id)["grand_w"] == 1
    assert duels.record_of(gl.id)["grand_l"] == 1
    assert duels.record_of(gw.id)["w"] == 6
    assert duels.record_of(gl.id)["w"] == 4
    assert tally_wins(duels, gw.id) == 6
    assert tally_wins(duels, gl.id) == 4
    row = saved_results()[-1]
    assert row["mode"] == "grand" and row["set_score"] == "6-4"
    assert_result(row, "6-4", {gw.id: 6, gl.id: 4})

    # A Grand call with no step score still does not join the night tally.
    bare_w, bare_l = Member(403, "BareW"), Member(404, "BareL")
    store.set_override(bare_w.id, "thornmere")
    store.set_override(bare_l.id, "vashara")
    bare = duels.settle_grand(bare_w, bare_l)
    assert bare["night"] is False
    assert tally_wins(duels, bare_w.id) == 0
    assert duels.record_of(bare_w.id)["w"] == 0
    assert duels.record_of(bare_w.id)["grand_w"] == 1


async def main() -> None:
    bot = Bot()
    store = Store(bot)
    duels = Duels(bot)
    bot.cogs["Store"] = store
    bot.cogs["Duels"] = duels
    await test_two_one_and_abandoned(duels, store)
    await test_finale_logs_every_player(duels, store, bot)
    await test_trio_and_grand_games(duels, store)
    print("Duel set credit: ok")


if __name__ == "__main__":
    asyncio.run(main())
