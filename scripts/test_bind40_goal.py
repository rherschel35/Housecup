#!/usr/bin/env python3
"""Daily Descent challenge is 75 floor-40+ monsters, and in-progress days survive.

The challenge length is BIND_BONUS_GOAL (not the live 200/day recruit cap, and
not the 10-monster floor gauntlet). A Chicago day already past 75 still pays
once, keeps its counter, and shows 75/75 on the checklist that feeds /gpa.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-bind40-"))
os.environ.setdefault("DISCORD_TOKEN", "")

from cogs.castles import (  # noqa: E402
    BIND_BONUS_GOAL,
    BIND_BONUS_MIN_FLOOR,
    BIND_BONUS_POINTS,
    DAILY_RECRUIT_CAP,
    Castles,
    chicago_day,
)
from cogs.checklist import Checklist  # noqa: E402
from cogs.descent import MONSTERS_PER_FLOOR  # noqa: E402
from cogs.help import HELP  # noqa: E402
from cogs.store import Store  # noqa: E402


class Bot:
    def __init__(self):
        self.user = type("User", (), {"id": 1})()
        self.cogs = {}

    def get_cog(self, name):
        return self.cogs.get(name)


class Member:
    def __init__(self, uid: int, name: str = "Tester"):
        self.id = uid
        self.display_name = name
        self.roles = []


def ledger_reasons(store: Store) -> list[str]:
    return [row.get("reason") or "" for row in store.state["ledger"]]


def bind_item(checklist: Checklist, member: Member):
    items = checklist._collect(member, time.time())
    matches = [item for item in items if item.label == "Army binds (F40+)"]
    assert len(matches) == 1
    return matches[0]


def main() -> None:
    assert BIND_BONUS_GOAL == 75
    assert BIND_BONUS_MIN_FLOOR == 40
    assert BIND_BONUS_POINTS == 10
    # Recruit cap and dungeon floor length stay put. Commands were not retuned.
    assert DAILY_RECRUIT_CAP == 200
    assert MONSTERS_PER_FLOOR == 10

    descent_note = HELP["descent"]["note"]
    castles_note = HELP["castles"]["note"]
    assert "Bind 75 floor-40+ monsters in a Central day for +10 house points once." in descent_note
    assert "75 floor-40+ binds in a Central day = +10 house points." in castles_note
    assert "200 floor-40+" not in descent_note
    assert "200 floor-40+" not in castles_note

    pip = json.loads((ROOT / "data" / "pip_knowledge.json").read_text(encoding="utf-8"))
    by_id = {entry["id"]: entry["answer"] for entry in pip["entries"]}
    assert "army binds (75 floor-40+ for +10)" in by_id["checklist"]
    assert "Bind **75** floor-**40**+ monsters" in by_id["descent"]
    assert "Bind **75** floor-**40**+ monsters" in by_id["castles_army"]
    assert "200/day" in by_id["castles_army"]
    assert "200 floor" not in by_id["checklist"]
    assert "**200**" not in by_id["descent"]

    bot = Bot()
    store = Store(bot)
    castles = Castles(bot)
    checklist = Checklist(bot)
    bot.cogs["Store"] = store
    bot.cogs["Castles"] = castles
    bot.cogs["Checklist"] = checklist

    member = Member(42)
    store.set_override(member.id, "caldrin")

    # Still short of the new total.
    assert castles.note_bind40(member, floor=39, n=10) == 0
    assert castles.bind40_today(member.id) == 0
    assert castles.note_bind40(member, floor=50, n=74) == 0
    assert castles.bind40_today(member.id) == 74
    assert castles.bind40_bonus_claimed(member.id) is False
    short = bind_item(checklist, member)
    assert short.done is False
    assert short.detail.startswith("**74/75** floor-40+ bound")
    assert ledger_reasons(store) == []

    # The 75th monster completes and pays once.
    assert castles.note_bind40(member, floor=40, n=1) == BIND_BONUS_POINTS
    assert castles.bind40_today(member.id) == 75
    assert castles.bind40_bonus_claimed(member.id) is True
    done = bind_item(checklist, member)
    assert done.done is True
    assert done.detail.startswith("**75/75** floor-40+ bound")
    assert "+10 pts claimed" in done.detail
    reasons = ledger_reasons(store)
    assert reasons == ["Bound 75 floor-40+ Descent monsters today"]
    assert store.state["totals"]["season"]["houses"]["caldrin"] == 10

    # Further wins keep the counter and do not pay again.
    assert castles.note_bind40(member, floor=80, n=3) == 0
    assert castles.bind40_today(member.id) == 78
    assert len(ledger_reasons(store)) == 1
    capped = bind_item(checklist, member)
    assert capped.done is True
    assert capped.detail.startswith("**75/75**")

    # A day already past the old 200 goal, not yet paid, settles without
    # resetting the counter or requiring another fight.
    ahead = Member(77)
    store.set_override(ahead.id, "caldrin")
    prec = castles.prec(ahead.id)
    prec["bind40_day"] = chicago_day()
    prec["bind40_today"] = 180
    prec["bind40_bonus"] = False
    castles.save()
    assert castles.settle_bind40(ahead) == BIND_BONUS_POINTS
    assert castles.bind40_today(ahead.id) == 180
    assert castles.bind40_bonus_claimed(ahead.id) is True
    assert castles.settle_bind40(ahead) == 0
    assert castles.note_bind40(ahead, floor=90, n=1) == 0
    assert castles.bind40_today(ahead.id) == 181
    ahead_item = bind_item(checklist, ahead)
    assert ahead_item.done is True
    assert ahead_item.detail.startswith("**75/75**")
    assert ledger_reasons(store).count("Bound 75 floor-40+ Descent monsters today") == 2
    assert store.state["totals"]["season"]["houses"]["caldrin"] == 20

    # Already claimed under the old total stays claimed.
    finished = Member(88)
    store.set_override(finished.id, "caldrin")
    prec = castles.prec(finished.id)
    prec["bind40_day"] = chicago_day()
    prec["bind40_today"] = 200
    prec["bind40_bonus"] = True
    castles.save()
    assert castles.settle_bind40(finished) == 0
    assert castles.note_bind40(finished, floor=40, n=1) == 0
    assert castles.bind40_today(finished.id) == 201
    assert len(ledger_reasons(store)) == 2

    print("OK")


if __name__ == "__main__":
    main()
