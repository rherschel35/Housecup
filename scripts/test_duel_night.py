#!/usr/bin/env python3
"""House Duel Night pays a pool at the end, not double points per win.

30-minute sample (227 night wins):

    Gon 56 Thornmere, Griffy 14 Thornmere,
    ALIEN 35 Oakmont, JJ 2 Oakmont,
    Twitchy 26, mara 26, Gherkin 17 Veyren,
    Lemur 23 Caldrin,
    Kas 23, Lumiere 5 AsterWilde.

Expected player totals: Gon 71, ALIEN 48, Lemur 35, Twitchy 28, mara 28,
Kas 25, Gherkin 18, everyone else 0. Houses: Veyren 84 (most duelists +10),
Thornmere 71, Oakmont 48, Caldrin 35, AsterWilde 25.

Outside the night, duel points and the daily checklist stamps stay as they
were. Attack's kill-share helper stays uncapped unless max_each is passed.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import discord

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-duel-night-"))
os.environ.setdefault("DISCORD_TOKEN", "")

from cogs.dementors import kill_share_awards as attack_kill_share  # noqa: E402
from cogs.duels import (  # noqa: E402
    DUEL_NIGHT_HOUSE_BONUS,
    DUEL_NIGHT_HOUSE_SHARE_BONUS,
    DUEL_NIGHT_MVP_BONUS,
    DUEL_NIGHT_POOL_MAX,
    DUELS_PATH,
    GRAND_REWARD_POINTS,
    RIVAL_AFTER,
    REWARDED_PER_DAY,
    Duel,
    Duels,
    duel_night_payout,
    _ephemeral_board_kwargs,
    edit_private_board,
    edit_public_board,
    pair_key,
)
from cogs.point_share import kill_share_awards  # noqa: E402
from cogs.store import STORE_PATH, Store  # noqa: E402

GON, GRIFFY = 101, 102
ALIEN, JJ = 103, 104
TWITCHY, MARA, GHERKIN = 105, 106, 107
LEMUR = 108
KAS, LUMIERE = 109, 110

SAMPLE = [
    {"uid": GON, "wins": 56, "house": "thornmere"},
    {"uid": GRIFFY, "wins": 14, "house": "thornmere"},
    {"uid": ALIEN, "wins": 35, "house": "vashara"},
    {"uid": JJ, "wins": 2, "house": "vashara"},
    {"uid": TWITCHY, "wins": 26, "house": "veyren"},
    {"uid": MARA, "wins": 26, "house": "veyren"},
    {"uid": GHERKIN, "wins": 17, "house": "veyren"},
    {"uid": LEMUR, "wins": 23, "house": "caldrin"},
    {"uid": KAS, "wins": 23, "house": "moonveil"},
    {"uid": LUMIERE, "wins": 5, "house": "moonveil"},
]
EXPECTED_PLAYERS = {
    GON: 71,
    ALIEN: 48,
    LEMUR: 35,
    TWITCHY: 28,
    MARA: 28,
    KAS: 25,
    GHERKIN: 18,
    GRIFFY: 0,
    JJ: 0,
    LUMIERE: 0,
}
EXPECTED_HOUSES = {
    "veyren": 84,
    "thornmere": 71,
    "vashara": 48,
    "caldrin": 35,
    "moonveil": 25,
}


class Bot:
    def __init__(self):
        self.user = type("User", (), {"id": 1})()
        self.cogs = {}
        self.guilds = []
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


def night_ledger(store: Store) -> list[dict]:
    return [
        row for row in store.state["ledger"]
        if str(row.get("reason") or "").startswith("Duel Night")
    ]


def tally_wins(duels: Duels, uid: int) -> int:
    night = duels.state.get("duel_night") or {}
    entry = (night.get("tally") or {}).get(str(uid)) or {}
    return int(entry.get("wins") or 0)


def test_kill_share_matches_attack() -> None:
    assert attack_kill_share is kill_share_awards
    # floor(35 × 1 / 2) = 17, the same truncation Attack relies on.
    assert kill_share_awards(
        [{"uid": 1, "kills": 1}, {"uid": 2, "kills": 1}],
        pool=35, top_n=7, total_kills=2,
    ) == {1: 17, 2: 17}
    cut = kill_share_awards(
        [{"uid": 1, "kills": 5}, {"uid": 2, "kills": 5}, {"uid": 3, "kills": 9}],
        pool=100, top_n=2, total_kills=19,
    )
    assert 3 not in cut
    assert cut[1] == int(100 * 5 / 19)
    capped = kill_share_awards(
        [{"uid": 1, "kills": 10}],
        pool=400, top_n=7, total_kills=10, max_each=75,
    )
    assert capped == {1: 75}
    from cogs.dementors import Dementors
    attack_src = inspect.getsource(Dementors._finalize_event)
    assert "kill_share_awards(" in attack_src
    assert "max_each" not in attack_src


def test_payout_math() -> None:
    payout = duel_night_payout(SAMPLE, minutes=30)
    assert payout["total_wins"] == 227
    assert payout["pool_size"] == 250
    assert sum(SAMPLE[i]["wins"] for i in range(len(SAMPLE))) == 227
    for uid, total in EXPECTED_PLAYERS.items():
        assert payout["player_total"].get(uid, 0) == total, (uid, payout["player_total"])
    assert payout["house_total"] == EXPECTED_HOUSES
    assert payout["active_houses"] == {"veyren"}
    assert payout["mvp_uids"] == {GON}
    assert ALIEN in payout["share_uids"]
    assert LEMUR in payout["share_uids"]
    assert GON not in payout["share_uids"]
    assert KAS not in payout["share_uids"]
    assert payout["pool"][GON] == 61
    assert payout["pool"][ALIEN] == 38
    assert payout["pool"][TWITCHY] == 28
    assert payout["pool"][MARA] == 28
    assert payout["pool"][LEMUR] == 25
    assert payout["pool"][KAS] == 25
    assert payout["pool"][GHERKIN] == 18
    assert GRIFFY not in payout["pool"]
    assert payout["best_turnout"] == 3

    hour = duel_night_payout(
        [{"uid": 1, "wins": 100, "house": "veyren"}], minutes=60,
    )
    assert hour["pool_size"] == 400
    assert hour["pool"][1] == DUEL_NIGHT_POOL_MAX == 75
    assert hour["player_total"][1] == 75 + DUEL_NIGHT_MVP_BONUS + DUEL_NIGHT_HOUSE_SHARE_BONUS
    assert hour["house_total"]["veyren"] == hour["player_total"][1] + DUEL_NIGHT_HOUSE_BONUS

    exact = duel_night_payout([
        {"uid": 1, "wins": 17, "house": "veyren"},
        {"uid": 2, "wins": 3, "house": "veyren"},
    ], minutes=30)
    assert 1 in exact["share_uids"] and 2 not in exact["share_uids"]
    under = duel_night_payout([
        {"uid": 1, "wins": 16, "house": "veyren"},
        {"uid": 2, "wins": 4, "house": "veyren"},
    ], minutes=30)
    assert 1 not in under["share_uids"]

    tied = duel_night_payout([
        {"uid": 1, "wins": 5, "house": "veyren"},
        {"uid": 2, "wins": 5, "house": "caldrin"},
    ], minutes=30)
    assert tied["mvp_uids"] == {1, 2}
    assert tied["active_houses"] == {"veyren", "caldrin"}
    assert tied["pool"][1] == 75 and tied["pool"][2] == 75


def test_rules_text(duels: Duels) -> None:
    intro = duels._duel_night_intro_embed(30).description
    assert "250" in intro and "75" in intro and "85%" in intro
    assert "no rematch limit" in intro.lower()
    assert "forfeit" in intro.lower()
    assert "3 / 4 / 5 / 26" in intro
    hour = duels._duel_night_intro_embed(60).description
    assert "400" in hour
    src = inspect.getsource(Duels.night_schedule_tick.coro)
    assert "_take_expired_duel_night" in src
    assert "Nothing is paid until it ends" in src
    assert "Rematch as much as you want" in src
    # Expiry runs before the schedule-enabled return.
    assert src.index("_take_expired_duel_night") < src.index('if not sched.get("enabled")')


async def test_live_rules(duels: Duels, store: Store) -> None:
    n = 1000

    def member(name: str) -> Member:
        nonlocal n
        n += 1
        return Member(n, name)

    def house(uid: int, key: str) -> None:
        store.set_override(uid, key)

    # Off-night: one point, one checklist stamp. Rival still doubles.
    w, l = member("Ada"), member("Bea")
    house(w.id, "thornmere")
    house(l.id, "vashara")
    outcome = duels.settle(w, l)
    assert outcome["awarded"] == 1 and outcome["reason"] is None
    assert duels.rewarded_today(w.id) == 1
    assert outcome["night"] is False

    rival_w, rival_l = member("Rae"), member("Lee")
    house(rival_w.id, "thornmere")
    house(rival_l.id, "caldrin")
    duels.state["pairs"][pair_key(rival_w.id, rival_l.id)] = RIVAL_AFTER - 1
    rival = duels.settle(rival_w, rival_l)
    assert rival["awarded"] == 2 and rival["rival"] is True
    assert duels.rewarded_today(rival_w.id) == 2

    series_w, series_l = member("Series"), member("Sets")
    house(series_w.id, "thornmere")
    house(series_l.id, "moonveil")
    series = duels.settle(
        series_w, series_l, match_wins=3, winner_sets=3, loser_sets=1,
    )
    assert series["awarded"] == 3
    assert series["loser_awarded"] == 1
    assert duels.rewarded_today(series_w.id) == 3
    assert duels.rewarded_today(series_l.id) == 1

    forfeit_w, forfeit_l = member("Forfeit"), member("Idle")
    house(forfeit_w.id, "thornmere")
    house(forfeit_l.id, "vashara")
    forfeited = duels.settle(
        forfeit_w, forfeit_l, forfeit=True, played_winner_sets=0,
        played_loser_sets=0, winner_sets=1, match_wins=1,
    )
    assert forfeited["awarded"] == 1
    assert duels.rewarded_today(forfeit_w.id) == 1

    # During the night: tally only. Checklist stamps are not spent.
    ok, _msg, _night = duels._begin_duel_night(
        minutes=30, by=1, channel_id=42, guild_id=1,
    )
    assert ok
    night_w, night_l = member("Night"), member("Foe")
    house(night_w.id, "thornmere")
    house(night_l.id, "vashara")
    before_ledger = len(store.state["ledger"])
    night_out = duels.settle(night_w, night_l)
    assert night_out["awarded"] == 0 and night_out["reason"] == "night"
    assert night_out["night_wins"] == 1
    assert tally_wins(duels, night_w.id) == 1
    assert duels.rewarded_today(night_w.id) == 0
    assert len(store.state["ledger"]) == before_ledger

    # Rematch is not capped.
    again = duels.settle(night_w, night_l)
    assert again["reason"] == "night"
    assert tally_wins(duels, night_w.id) == 2
    assert duels.rewarded_today(night_w.id) == 0

    # Rival double does not apply, and does not consume two checklist slots.
    rw, rl = member("RivalNight"), member("RivalFoe")
    house(rw.id, "thornmere")
    house(rl.id, "caldrin")
    duels.state["pairs"][pair_key(rw.id, rl.id)] = RIVAL_AFTER - 1
    rival_night = duels.settle(rw, rl)
    assert rival_night["awarded"] == 0 and rival_night["rival"] is True
    assert tally_wins(duels, rw.id) == 1
    assert duels.rewarded_today(rw.id) == 0

    sw, sl = member("Bank"), member("Other")
    house(sw.id, "veyren")
    house(sl.id, "caldrin")
    banked = duels.settle(sw, sl, match_wins=4, winner_sets=4, loser_sets=1)
    assert banked["night_wins"] == 4 and banked["loser_night_wins"] == 1
    assert tally_wins(duels, sw.id) == 4
    assert tally_wins(duels, sl.id) == 1
    assert duels.rewarded_today(sw.id) == 0
    assert duels.rewarded_today(sl.id) == 0

    same_a, same_b = member("Same"), member("Mate")
    house(same_a.id, "veyren")
    house(same_b.id, "veyren")
    same = duels.settle(same_a, same_b)
    assert same["reason"] == "same-house"
    assert tally_wins(duels, same_a.id) == 0

    fw, fl = member("Timeout"), member("Absent")
    house(fw.id, "thornmere")
    house(fl.id, "moonveil")
    no_play = duels.settle(
        fw, fl, forfeit=True, played_winner_sets=0, played_loser_sets=0,
        winner_sets=1, match_wins=26,
    )
    assert no_play["reason"] == "no-play"
    assert tally_wins(duels, fw.id) == 0
    assert duels.rewarded_today(fw.id) == 0

    pw, pl = member("Partial"), member("Left")
    house(pw.id, "thornmere")
    house(pl.id, "vashara")
    partial = duels.settle(
        pw, pl, match_wins=4, winner_sets=2, loser_sets=1, forfeit=True,
        played_winner_sets=2, played_loser_sets=1,
    )
    assert partial["night_wins"] == 2 and partial["loser_night_wins"] == 1
    assert tally_wins(duels, pw.id) == 2
    assert tally_wins(duels, pl.id) == 1
    assert partial["awarded"] == 0

    gw, gl = member("Grand"), member("Foe")
    house(gw.id, "caldrin")
    house(gl.id, "thornmere")
    grand_before = len(store.state["ledger"])
    grand = duels.settle_grand(gw, gl)
    assert grand["awarded"] == GRAND_REWARD_POINTS
    assert grand["night"] is False
    assert duels.grand_rewarded_today(gw.id) == 1
    assert tally_wins(duels, gw.id) == 0
    assert store.state["ledger"][-1]["delta"] == GRAND_REWARD_POINTS
    assert len(store.state["ledger"]) == grand_before + 1

    # Ending the night does not erase the checklist: the night duelist can
    # still take today's paid win afterwards.
    duels.state["duel_night"] = None
    duels.save()
    after = duels.settle(night_w, night_l)
    assert after["awarded"] == 1
    assert duels.rewarded_today(night_w.id) == 1
    assert duels.rewarded_today(night_w.id) <= REWARDED_PER_DAY
    assert night_ledger(store) == []


async def test_expiry_tick(duels: Duels, store: Store, bot: Bot) -> None:
    now = time.time()
    duels.state["duel_night"] = {
        "by": 1,
        "at": now - 60,
        "ends_at": now + 600,
        "minutes": 30,
        "tally": {
            str(row["uid"]): {"wins": row["wins"], "pts": 0, "house": row["house"]}
            for row in SAMPLE
        },
        "channel_id": 42,
        "guild_id": 1,
    }
    duels.state["duel_night_schedule"]["enabled"] = False
    duels.save()

    await duels.night_schedule_tick.coro(duels)
    assert duels.state.get("duel_night") is not None
    assert night_ledger(store) == []

    duels.state["duel_night"]["ends_at"] = time.time() - 1
    duels.save()
    await duels.night_schedule_tick.coro(duels)
    assert duels.state.get("duel_night") is None

    by_target = defaultdict(int)
    by_house = defaultdict(int)
    reasons = set()
    for row in night_ledger(store):
        by_target[row["target_id"]] += int(row["delta"])
        by_house[row["house"]] += int(row["delta"])
        reasons.add(row["reason"])
    # House bonus is recorded on Veyren's top duelist (Twitchy, lower id than mara).
    assert by_target[GON] == 71
    assert by_target[ALIEN] == 48
    assert by_target[LEMUR] == 35
    assert by_target[TWITCHY] == 28 + DUEL_NIGHT_HOUSE_BONUS
    assert by_target[MARA] == 28
    assert by_target[KAS] == 25
    assert by_target[GHERKIN] == 18
    assert by_target.get(GRIFFY, 0) == 0
    assert by_target.get(JJ, 0) == 0
    assert by_target.get(LUMIERE, 0) == 0
    assert dict(by_house) == EXPECTED_HOUSES
    assert "Duel Night pool share" in reasons
    assert "Duel Night MVP bonus" in reasons
    assert "Duel Night house share bonus" in reasons
    assert "Duel Night most active house" in reasons

    text = "\n".join(
        embed.description or ""
        for batch in bot.channel.sent
        for embed in batch
    )
    assert "<@101>: **56** wins, pool **+61**, MVP **+10** = **71**" in text
    assert "<@103>: **35** wins, pool **+38**, house share **+10** = **48**" in text
    assert "<@108>: **23** wins, pool **+25**, house share **+10** = **35**" in text
    assert "<@105>: **26** wins, pool **+28** = **28**" in text
    assert "<@106>: **26** wins, pool **+28** = **28**" in text
    assert "<@109>: **23** wins, pool **+25** = **25**" in text
    assert "<@107>: **17** wins, pool **+18** = **18**" in text
    assert "<@102>: **14** wins, pool **+0** (outside top 7) = **0**" in text
    assert "<@104>: **2** wins, pool **+0** (outside top 7) = **0**" in text
    assert "<@110>: **5** wins, pool **+0** (outside top 7) = **0**" in text
    assert "Thornmere" in text and "Oakmont" in text and "Veyren" in text
    assert "Caldrin" in text and "AsterWilde" in text
    assert "Veyren: **+84**" in text
    assert "Thornmere: **+71**" in text
    assert "Oakmont: **+48**" in text
    assert "Caldrin: **+35**" in text
    assert "AsterWilde: **+25**" in text
    assert "Most distinct duelists: **Veyren** (+10)." in text
    assert "**227**" in text and "**250**" in text

    paid = len(night_ledger(store))
    await duels.night_schedule_tick.coro(duels)
    assert len(night_ledger(store)) == paid

    with open(DUELS_PATH, encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved.get("duel_night") is None
    with open(STORE_PATH, encoding="utf-8") as fh:
        json.load(fh)
    assert not DUELS_PATH.with_suffix(".tmp").exists()
    assert not STORE_PATH.with_suffix(".tmp").exists()


def _webhook_expired() -> discord.HTTPException:
    response = type("Resp", (), {"status": 401, "reason": "Unauthorized"})()
    return discord.HTTPException(
        response, {"code": 50027, "message": "Invalid Webhook Token"}
    )


def test_private_board_omits_empty_view() -> None:
    kwargs = _ephemeral_board_kwargs(object(), None)
    assert "view" not in kwargs and kwargs["ephemeral"] is True
    view = object()
    assert _ephemeral_board_kwargs(object(), view)["view"] is view
    # The crash is view.is_finished() when view is None. Both open paths omit it.
    for fn in (Duel.open_cast_board,):
        src = inspect.getsource(fn)
        assert "_ephemeral_board_kwargs" in src
        assert "view=view" not in src


async def test_expired_boards_repost() -> None:
    class Dead:
        id = 5

        async def edit(self, **kwargs):
            raise _webhook_expired()

    class Fresh:
        id = 9

    class Chan:
        def __init__(self):
            self.sent = []

        async def send(self, *args, **kwargs):
            self.sent.append((args, kwargs))
            return Fresh()

    match = type("Match", (), {})()
    match.message = Dead()
    match.channel = Chan()
    match.private_boards = {7: Dead()}
    match._cast_nudged = set()
    await edit_public_board(match, embed=None, view=None)
    assert match.message.id == 9
    await edit_private_board(match, 7, Dead(), embed=None, view=None)
    assert 7 not in match.private_boards
    nudge = match.channel.sent[-1][0][0]
    assert "<@7>" in nudge and "Open my cast board" in nudge
    await edit_private_board(match, 7, Dead(), embed=None, view=None)
    assert sum("Open my cast board" in (args[0] if args else "") for args, _ in match.channel.sent) == 1


async def test_series_survives_the_clock(duels: Duels, store: Store) -> None:
    """Sets won in a series count, including after Duel Night's clock."""
    assert duels.state.get("duel_night") is None
    ok, _msg, _night = duels._begin_duel_night(
        minutes=30, by=1, channel_id=42, guild_id=1,
    )
    assert ok
    w, l = Member(901, "SeriesW"), Member(902, "SeriesL")
    store.set_override(w.id, "thornmere")
    store.set_override(l.id, "vashara")
    match = Duel(duels, None, w, l, best_of=7)
    match.state = "active"
    # Started a minute ago so moving ends_at into the past still leaves
    # started_at < ends_at. A sub-second test would otherwise invert them.
    match.started_at = time.time() - 60
    duels.mark_busy(match, w, l)

    # Fizzle/draw banks finished sets, not the unplayed series bank of 4.
    drawn = duels.settle(
        w, l, match_wins=4, winner_sets=2, loser_sets=1,
        sets_only=True, played_winner_sets=2, played_loser_sets=1,
        started_at=match.started_at,
    )
    assert drawn["night_wins"] == 2 and drawn["loser_night_wins"] == 1
    assert drawn["awarded"] == 0
    assert duels.streak_of(w.id) == 0
    assert duels.record_of(w.id)["w"] == 2
    assert duels.record_of(l.id)["w"] == 1
    assert tally_wins(duels, w.id) == 2
    assert tally_wins(duels, l.id) == 1

    # Forfeit with only the set score (no played_* ) still banks both sides.
    fw, fl = Member(903, "ForfeitW"), Member(904, "ForfeitL")
    store.set_override(fw.id, "caldrin")
    store.set_override(fl.id, "moonveil")
    forfeited = duels.settle(
        fw, fl, match_wins=5, winner_sets=3, loser_sets=2, forfeit=True,
        started_at=match.started_at,
    )
    assert forfeited["night_wins"] == 3 and forfeited["loser_night_wins"] == 2
    assert tally_wins(duels, fw.id) == 3
    assert tally_wins(duels, fl.id) == 2

    duels.state["duel_night"]["ends_at"] = match.started_at + 30
    assert duels.pop_expired_duel_night() is None
    late = duels.settle(
        w, l, now=time.time(), match_wins=4, winner_sets=4, loser_sets=1,
        started_at=match.started_at,
    )
    assert late["reason"] == "night"
    assert late["night_wins"] == 4 and late["loser_night_wins"] == 1
    assert tally_wins(duels, w.id) == 6
    assert tally_wins(duels, l.id) == 2

    aw, al = Member(905, "AfterW"), Member(906, "AfterL")
    store.set_override(aw.id, "thornmere")
    store.set_override(al.id, "vashara")
    after = duels.settle(aw, al, now=time.time(), started_at=time.time())
    assert after["reason"] != "night"
    assert tally_wins(duels, aw.id) == 0

    duels.release_match(match)
    popped = duels.pop_expired_duel_night()
    assert popped is not None
    tally = popped["tally"]
    assert int(tally[str(w.id)]["wins"]) == 6
    assert int(tally[str(l.id)]["wins"]) == 2
    assert int(tally[str(fw.id)]["wins"]) == 3
    assert int(tally[str(fl.id)]["wins"]) == 2

    # Neither side finished a set: nothing is banked.
    zilch = duels.settle(
        w, l, sets_only=True, winner_sets=0, loser_sets=0, match_wins=26,
    )
    assert zilch["reason"] == "no-play" and zilch["night_wins"] == 0
    assert duels.record_of(w.id)["w"] == 2 + 4


async def main() -> None:
    test_kill_share_matches_attack()
    test_payout_math()
    bot = Bot()
    store = Store(bot)
    duels = Duels(bot)
    bot.cogs["Store"] = store
    bot.cogs["Duels"] = duels
    test_rules_text(duels)
    test_private_board_omits_empty_view()
    await test_expired_boards_repost()
    await test_live_rules(duels, store)
    await test_expiry_tick(duels, store, bot)
    await test_series_survives_the_clock(duels, store)
    print("Duel Night pool payout: ok")


if __name__ == "__main__":
    asyncio.run(main())
