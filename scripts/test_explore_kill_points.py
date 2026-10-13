#!/usr/bin/env python3
"""Explore-stirred monster kills fill the Explore points checklist.

The checklist line "Explore points — x/3 pts left" reads student day["points"]
and resets at America/Chicago midnight. A kill stirred by /explore still pays
its full house points, and those points also fill that line up to 3. Later
kills keep paying in full. The checklist credit is not a second payout.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-explore-kills-"))
os.environ.setdefault("DISCORD_TOKEN", "")

from cogs.dementors import (  # noqa: E402
    CREATURES,
    Dementors,
    format_kill_footer,
)
from cogs.store import Store  # noqa: E402
from cogs.world import WorldCog  # noqa: E402
from cogs.world_engine import POINTS_PER_DAY, next_central_midnight, today  # noqa: E402

CHI = ZoneInfo("America/Chicago")


class Bot:
    def __init__(self):
        self.user = None
        self.cogs = {}
        self.channel = Channel()

    def get_cog(self, name):
        return self.cogs.get(name)

    def get_channel(self, channel_id):
        return self.channel

    async def fetch_channel(self, channel_id):
        return self.channel


class Channel:
    id = 1

    async def send(self, *args, **kwargs):
        class Msg:
            id = 99
        return Msg()


class Member:
    def __init__(self, uid: int, name: str = "Tester"):
        self.id = uid
        self.display_name = name
        self.roles = []


def checklist_detail(student: dict) -> str:
    """Same string the checklist builds for Explore points."""
    pts_used = int(student.get("day", {}).get("points", 0))
    pts_left = max(0, POINTS_PER_DAY - pts_used)
    return f"**{pts_left}/{POINTS_PER_DAY}** pts left"


def ledger_deltas(store: Store) -> list[int]:
    return [int(row["delta"]) for row in store.state["ledger"]]


async def main() -> None:
    assert POINTS_PER_DAY == 3
    assert CREATURES["dementor"]["points"] == 3
    assert CREATURES["shadow_wolf"]["points"] == 3
    assert CREATURES["wisp_swarm"]["points"] == 3
    assert CREATURES["stone_golem"]["points"] == 4
    assert CREATURES["nightweaver"]["points"] == 4
    assert CREATURES["storm_sprite"]["points"] == 5

    late = dt.datetime(2026, 10, 13, 23, 45, tzinfo=CHI).timestamp()
    early = dt.datetime(2026, 10, 14, 0, 5, tzinfo=CHI).timestamp()
    midnight = dt.datetime(2026, 10, 14, 0, 0, tzinfo=CHI)
    assert today(late) == "2026-10-13"
    assert today(early) == "2026-10-14"
    assert next_central_midnight(late) == int(midnight.timestamp())

    bot = Bot()
    store = Store(bot)
    world = WorldCog(bot)
    threats = Dementors(bot)
    threats.lock = asyncio.Lock()
    bot.cogs = {"Store": store, "World": world, "Dementors": threats}

    # Cap and Central-midnight reset on the counter the checklist reads.
    reset_member = Member(10, "Reset")
    store.state["overrides"][str(reset_member.id)] = "caldrin"
    reset_student = world.student(reset_member)
    assert world.world.allow_points(reset_student, 3, late) == 3
    assert world.world.allow_points(reset_student, 3, late) == 0
    assert reset_student["day"]["date"] == "2026-10-13"
    assert reset_student["day"]["points"] == 3
    assert checklist_detail(reset_student) == "**0/3** pts left"
    assert world.world.allow_points(reset_student, 5, early) == 3
    assert reset_student["day"]["date"] == "2026-10-14"
    assert reset_student["day"]["points"] == 3

    # Defeat footers stay the full creature amount.
    assert format_kill_footer([(3, "caldrin")]) == "+3 for House Caldrin"
    assert format_kill_footer([(3, "caldrin")], points_word=True) == (
        "+3 points for House Caldrin"
    )
    assert format_kill_footer([(4, "caldrin"), (4, "thornmere")]) == (
        "+4 for House Caldrin • +4 for House Thornmere"
    )
    assert format_kill_footer([(5, "caldrin")]) == "+5 for House Caldrin"
    assert format_kill_footer([(0, None)]) == ""

    # /explore marks the spawn; /forage does not. Wandering keeps the mark.
    assert await threats.try_ambient_spawn(111, "dementor", finder_id=1, pay_explore_points=True)
    assert threats.state["active"]["pay_explore_points"] is True
    assert threats.state["active"]["finder_id"] == 1
    threats.state["active"] = None
    assert await threats.try_ambient_spawn(111, "shadow_wolf", finder_id=1)
    assert "pay_explore_points" not in threats.state["active"]
    threats.state["active"] = {
        "creature": "storm_sprite",
        "channel_id": 111,
        "message_id": 1,
        "spawned_at": 0,
        "hits": [1],
        "finder_id": 1,
        "exclusive_until": 1,
        "pay_explore_points": True,
    }
    await threats._wander(threats.state["active"])
    assert threats.state["active"].get("pay_explore_points") is True
    assert "finder_id" not in threats.state["active"]

    # A non-explore kill pays in full and leaves the checklist alone.
    # An explore kill pays in full AND fills the line (a 5-point sprite
    # still pays 5; the line clamps at 3). A later kill pays in full too.
    hunter = Member(20, "Hunter")
    store.state["overrides"][str(hunter.id)] = "caldrin"
    hunter_student = world.student(hunter)
    before = len(store.state["ledger"])
    awarded, house = await threats._award_kill_points(
        hunter, CREATURES["storm_sprite"]["points"], "Defeated a Storm Sprite", explore=False,
    )
    assert (awarded, house) == (5, "caldrin")
    assert hunter_student.get("day", {}).get("points", 0) == 0
    assert checklist_detail(hunter_student) == "**3/3** pts left"
    awarded, house = await threats._award_kill_points(
        hunter, CREATURES["storm_sprite"]["points"], "Defeated a Storm Sprite", explore=True,
    )
    assert (awarded, house) == (5, "caldrin")
    assert hunter_student["day"]["points"] == 3
    assert checklist_detail(hunter_student) == "**0/3** pts left"
    awarded, house = await threats._award_kill_points(
        hunter, CREATURES["dementor"]["points"], "Banished a Dementor", explore=True,
    )
    assert (awarded, house) == (3, "caldrin")
    assert hunter_student["day"]["points"] == 3
    assert checklist_detail(hunter_student) == "**0/3** pts left"
    assert world.world.allow_points(hunter_student, 2) == 0
    assert ledger_deltas(store)[before:] == [5, 5, 3]
    assert [row["reason"] for row in store.state["ledger"][before:]] == [
        "Defeated a Storm Sprite",
        "Defeated a Storm Sprite",
        "Banished a Dementor",
    ]
    # One ledger row per kill, at the full creature amount. 5 + 5 + 3.
    assert store.state["totals"]["season"]["houses"]["caldrin"] == 13
    assert store.state["totals"]["season"]["members"][str(hunter.id)] == 13

    # Checklist progress already at 2. A 4-point explore kill still pays 4
    # and only moves the line from 2 to 3.
    finder = Member(30, "Finder")
    store.state["overrides"][str(finder.id)] = "thornmere"
    finder_student = world.student(finder)
    assert world.world.allow_points(finder_student, 2) == 2
    assert checklist_detail(finder_student) == "**1/3** pts left"
    awarded, house = await threats._award_kill_points(
        finder, CREATURES["stone_golem"]["points"], "Defeated a Stone Golem", explore=True,
    )
    assert (awarded, house) == (4, "thornmere")
    assert finder_student["day"]["points"] == 3
    assert checklist_detail(finder_student) == "**0/3** pts left"
    assert store.state["ledger"][-1]["delta"] == 4
    assert store.state["ledger"][-1]["reason"] == "Defeated a Stone Golem"
    assert world.world.note_explore_kill(finder_student, 5) == 0

    # Yesterday's finished line does not block today's explore kill.
    stale = Member(40, "Stale")
    store.state["overrides"][str(stale.id)] = "veyren"
    stale_student = world.student(stale)
    stale_student["day"] = {"date": "2000-01-01", "points": 3}
    awarded, house = await threats._award_kill_points(
        stale, CREATURES["wisp_swarm"]["points"], "Defeated a Wisp Swarm", explore=True,
    )
    assert (awarded, house) == (3, "veyren")
    assert stale_student["day"]["date"] == today()
    assert stale_student["day"]["points"] == 3
    assert checklist_detail(stale_student) == "**0/3** pts left"

    # No house: nothing is paid and the checklist does not move.
    stranger = Member(50, "Stranger")
    stranger_student = world.student(stranger)
    rows = len(store.state["ledger"])
    awarded, house = await threats._award_kill_points(
        stranger, 3, "Defeated a Shadow Wolf", explore=True,
    )
    assert (awarded, house) == (0, None)
    assert stranger_student.get("day", {}).get("points", 0) == 0
    assert len(store.state["ledger"]) == rows

    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
