#!/usr/bin/env python3
"""Birthday gifts are claimed once per Chicago year, and stay claimed after a reload."""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-birthday-"))
os.environ.setdefault("DISCORD_TOKEN", "")

from cogs.birthdays import (  # noqa: E402
    COMMON_REWARDS,
    RARE_CHANCE,
    RARE_REWARDS,
    claim_birthday_year,
    clear_birthday,
    days_in_month,
    draw_birthday_reward,
    is_birthday_today,
    plan_birthday_grants,
    save_birthday,
    valid_birthday,
)


class AlwaysRare:
    def random(self):
        return 0.0

    def choices(self, seq, weights=None, k=1):
        return [seq[0]]


class NeverRare:
    def random(self):
        return 0.99

    def choices(self, seq, weights=None, k=1):
        weights = list(weights or [])
        index = weights.index(max(weights)) if weights else 0
        return [seq[index]]


def _state() -> dict:
    return {"users": {}}


def main() -> None:
    assert RARE_CHANCE < 0.15
    assert sum(row["weight"] for row in COMMON_REWARDS) == 100
    assert sum(row["weight"] for row in RARE_REWARDS) == 100
    assert {row["kind"] for row in COMMON_REWARDS} >= {"points", "item"}
    assert {row["kind"] for row in RARE_REWARDS} >= {"points", "item", "gear", "frog"}

    rare = draw_birthday_reward(AlwaysRare())
    assert rare["rare"] is True
    assert rare["id"] == RARE_REWARDS[0]["id"]
    ordinary = draw_birthday_reward(NeverRare())
    assert ordinary["rare"] is False
    assert ordinary["kind"] == "points"
    assert ordinary["amount"] == 5

    assert valid_birthday(2, 29)
    assert valid_birthday(4, 30)
    assert not valid_birthday(4, 31)
    assert not valid_birthday(2, 30)
    assert not valid_birthday(13, 1)
    assert days_in_month(2) == 29
    assert days_in_month(4) == 30
    assert days_in_month(None) == 31

    # October 15, 2026 is not a leap-day edge.
    when = dt.date(2026, 10, 15)
    state = _state()
    save_birthday(state, 7, 10, 15)
    save_birthday(state, 8, 1, 1)
    first = plan_birthday_grants(state, when, random.Random(1))
    assert [uid for uid, _reward in first] == ["7"]
    assert 2026 in state["users"]["7"]["rewarded_years"]
    assert state["users"]["8"].get("rewarded_years") == []
    assert plan_birthday_grants(state, when, random.Random(2)) == []

    # A restart loads the same years and must not grant again.
    reloaded = json.loads(json.dumps(state))
    assert plan_birthday_grants(reloaded, when, random.Random(3)) == []
    assert claim_birthday_year(reloaded, 7, 2026) is False
    nxt = plan_birthday_grants(reloaded, dt.date(2027, 10, 15), random.Random(4))
    assert [uid for uid, _reward in nxt] == ["7"]
    assert reloaded["users"]["7"]["rewarded_years"] == [2026, 2027]

    # Someone else with the same day still gets their own gift.
    save_birthday(reloaded, 9, 10, 15)
    other = plan_birthday_grants(reloaded, when, random.Random(5))
    assert [uid for uid, _reward in other] == ["9"]
    assert plan_birthday_grants(reloaded, when, random.Random(6)) == []

    # February 29: once on Feb 28 in a common year, and on Feb 29 in a leap year.
    leap = _state()
    save_birthday(leap, 1, 2, 29)
    assert is_birthday_today(2, 29, dt.date(2026, 2, 28))
    assert not is_birthday_today(2, 29, dt.date(2026, 3, 1))
    assert not is_birthday_today(2, 29, dt.date(2028, 2, 28))
    assert is_birthday_today(2, 29, dt.date(2028, 2, 29))
    common_year = plan_birthday_grants(leap, dt.date(2026, 2, 28), random.Random(7))
    assert len(common_year) == 1
    assert plan_birthday_grants(leap, dt.date(2026, 2, 28), random.Random(8)) == []
    assert plan_birthday_grants(leap, dt.date(2028, 2, 28), random.Random(9)) == []
    leap_day = plan_birthday_grants(leap, dt.date(2028, 2, 29), random.Random(10))
    assert len(leap_day) == 1
    assert leap["users"]["1"]["rewarded_years"] == [2026, 2028]

    # Clearing the date does not forget a year that was already gifted.
    assert clear_birthday(leap, 1) is True
    assert "month" not in leap["users"]["1"]
    save_birthday(leap, 1, 2, 29)
    assert plan_birthday_grants(leap, dt.date(2026, 2, 28), random.Random(11)) == []
    assert clear_birthday(leap, 99) is False

    # A not-yet-visible player can be skipped without burning the year.
    waiting = _state()
    save_birthday(waiting, 4, 10, 15)
    assert plan_birthday_grants(waiting, when, random.Random(12), only_user_ids=set()) == []
    assert waiting["users"]["4"].get("rewarded_years") == []
    seen = plan_birthday_grants(waiting, when, random.Random(13), only_user_ids={"4"})
    assert len(seen) == 1
    assert plan_birthday_grants(waiting, when, random.Random(14), only_user_ids={"4"}) == []

    print("OK")


if __name__ == "__main__":
    main()
