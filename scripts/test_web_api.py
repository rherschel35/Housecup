#!/usr/bin/env python3
"""Read-only website API. No Discord token required.

The routes must answer only when WEB_API_KEY is set, must not write state,
and must not return place-secret phrases or other hidden game fields.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATE = Path(tempfile.mkdtemp(prefix="housecup-web-api-"))
os.environ["STATE_DIR"] = str(STATE)
os.environ["DISCORD_TOKEN"] = ""
os.environ["WEB_API_CACHE_SECONDS"] = "0"
os.environ.pop("WEB_API_KEY", None)
os.environ.pop("WEB_API_KEY_HEADER", None)

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from cogs.familiars import LOOKS  # noqa: E402
from cogs.vcc.web import create_app  # noqa: E402
from cogs.web_api import clear_caches, next_weekly  # noqa: E402

KEY = "test-web-api-key"
USER = 42
OTHER = 990001
NOW = int(time.time())
CANARIES = (
    "CANARY_WAND_WORDS_9f3a",
    "CANARY_OTHER_WAND_9f3a",
    "CANARY_PATRONUS_WORDS_9f3a",
    "CANARY_ANIMAGUS_WORDS_9f3a",
    "CANARY_BROOM_WORDS_9f3a",
    "CANARY_SECRET_PHRASE_9f3a",
    "CANARY_BREW_SEQUENCE_9f3a",
    "CANARY_LEDGER_REASON_9f3a",
    "CANARY_TRADE_NOTE_9f3a",
    "CANARY_WEAK_SPELL_9f3a",
    "CANARY_TALLY_9f3a",
    "CANARY_WARN_KEY_9f3a",
    "CANARY_PRACTICE_9f3a",
    "CANARY_JOURNAL_9f3a",
    "CANARY_ART_FILE_9f3a",
    "CANARY_WEEK_KEY_9f3a",
    "CANARY_OTHER_CUP_9f3a",
    "CANARY_FAMILIAR_DAY_9f3a",
    "CANARY_GEAR",
)
HIDDEN_NUMBERS = ("987654321", "771177", "9182736455", "5647382910")
FORBIDDEN_KEYS = {
    "words", "phrases", "phrase", "sequence", "answer", "answers", "secret",
    "secrets", "flags", "requires", "revealed", "rep", "tally", "weak",
    "weakness", "pairs", "spell_wins", "token", "tokens", "password",
}


def write(name: str, payload) -> None:
    path = STATE / name
    path.write_text(json.dumps(payload), encoding="utf-8")


def snapshot() -> dict[str, str]:
    found = {}
    for path in sorted(STATE.rglob("*")):
        if path.is_file():
            found[str(path.relative_to(STATE))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def keys_of(value, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key))
            keys_of(item, found)
    elif isinstance(value, list):
        for item in value:
            keys_of(item, found)


def seed() -> None:
    fox_look = next(iter(LOOKS["fox"]))
    write("points_store.json", {
        "season": {"number": 2, "name": "Season 2", "active": True, "started_at": 1700000000},
        "totals": {
            "season": {
                "houses": {
                    "thornmere": 20, "vashara": 20, "caldrin": 5, "veyren": 1, "moonveil": 0,
                },
                "members": {str(USER): 424242, str(OTHER): 3},
                "house_members": {"thornmere": {str(USER): 424242}},
            },
            "alltime": {
                "houses": {"thornmere": 50, "vashara": 10},
                "members": {str(USER): 80},
            },
        },
        "ledger": [{
            "reason": "CANARY_LEDGER_REASON_9f3a",
            "delta": 1,
            "target_id": USER,
        }],
        "archive": [{
            "number": 1,
            "name": "Season 1",
            "winner": "thornmere",
            "tied": [],
            "champions": [{"id": USER, "points": 10}],
            "winning_members": {str(USER): 10, str(OTHER): 1},
        }],
        "overrides": {},
        "settings": {"staff_role_id": 9182736455, "house_roles": {}},
    })
    write("world_state.json", {
        "students": {
            str(USER): {
                "name": "Stale Name",
                "items": {
                    "silverleaf": 2,
                    "dragon_scale": 1,
                    "strange_key": 3,
                    "broken_compass": 4,
                    "map_scrap": 0,
                },
                "flags": ["CANARY_SECRET_PHRASE_9f3a"],
                "revealed": {"garden": "CANARY_SECRET_PHRASE_9f3a"},
                "rep": {"garden": 9},
            },
        },
        "places": {
            "garden": {"event": {"key": "door", "phrase": "CANARY_SECRET_PHRASE_9f3a"}},
        },
    })
    write("potions_state.json", {
        "players": {
            str(USER): {
                "inventory": {
                    "felix_felicis": 2,
                    "beastcallers_draught": 0,
                    "not_a_potion": 4,
                },
                "sequence": ["CANARY_BREW_SEQUENCE_9f3a"],
                "discovered": ["felix_felicis"],
                "rep_xp": 50,
            },
        },
    })
    write("frogs_state.json", {
        "players": {
            str(USER): {
                "cards": {"lumen_aegis": 2, "CANARY_CARD": 3},
                "opened_on": "2020-01-01",
            },
        },
        "trades": {"t1": {"note": "CANARY_TRADE_NOTE_9f3a"}},
    })
    write("wands.json", {
        str(USER): {
            "wood": "Holly",
            "core": "Phoenix feather",
            "length": 11.25,
            "flexibility": "supple",
            "reading": "It chose you.",
            "words": "CANARY_WAND_WORDS_9f3a",
        },
        str(OTHER): {
            "wood": "Oak",
            "core": "Dragon heartstring",
            "words": "CANARY_OTHER_WAND_9f3a",
            "reading": "Someone else.",
        },
    })
    write("patronus.json", {
        "owners": {
            str(USER): {
                "animal": "Stag",
                "form": "A stag.",
                "reading": "Steady.",
                "words": "CANARY_PATRONUS_WORDS_9f3a",
            },
        },
    })
    write("animagus.json", {
        "forms": {
            str(USER): {
                "animal": "Fox",
                "form": "A fox.",
                "reading": "Clever.",
                "words": "CANARY_ANIMAGUS_WORDS_9f3a",
                "art": "CANARY_ART_FILE_9f3a.png",
            },
        },
        "echo_until": {str(USER): NOW + 60},
    })
    write("brooms.json", {
        "owners": {
            str(USER): {
                "model": "Not A Real Broom",
                "finish": "Dark handle.",
                "reading": "Yours.",
                "words": "CANARY_BROOM_WORDS_9f3a",
                "stats": {"speed": 4, "altitude": 6},
                "speed_bonus": 1,
            },
        },
    })
    write("familiars.json", {
        "members": {
            str(USER): {
                "species": "fox",
                "name": "Pip",
                "look": fox_look,
                "friendship": 12,
                "day": {"date": "CANARY_FAMILIAR_DAY_9f3a"},
            },
        },
    })
    write("beasts_state.json", {
        "collections": {str(USER): {"bramblehog": {"n": 1}}},
        "journals": {str(USER): {"bramblehog": {"notes": ["CANARY_JOURNAL_9f3a"]}}},
    })
    write("adornments_state.json", {
        "members": {
            str(USER): {
                "owned": {"moonbloom_locket": {}, "CANARY_GEAR": {}},
                "worn": {"necklace": "moonbloom_locket", "ring": "CANARY_GEAR"},
                "title": "Quiet Prefect",
                "nightwatch": False,
                "whistle_at": 5647382910,
            },
        },
    })
    write("triwizard.json", {
        "champions": [
            {
                "number": 3,
                "name": "Tri-Wizard Tournament III",
                "id": USER,
                "display_name": "Gon",
                "house": "thornmere",
            },
            {"number": 1, "id": OTHER, "name": "CANARY_OTHER_CUP_9f3a"},
        ],
    })
    write("duels.json", {
        "records": {
            str(USER): {"w": 4, "l": 771177, "trio_w": 1, "grand_w": 0},
            "7": {"w": 9},
        },
        "rep": {str(USER): 987654321},
        "streaks": {str(USER): 3},
        "bounties": {str(USER): {"at": 1}},
        "pairs": {f"{USER}-100": 6},
        "spell_wins": {str(USER): {"hex": 10}},
        "signature": {},
        "champion": {"user_id": USER, "week": "CANARY_WEEK_KEY_9f3a"},
        "duel_night": {
            "by": 9182736455,
            "at": NOW,
            "ends_at": NOW + 3600,
            "minutes": 60,
            "channel_id": 9182736455,
            "tally": {str(USER): {"wins": 1, "note": "CANARY_TALLY_9f3a"}},
        },
        "duel_night_schedule": {
            "enabled": True,
            "slots": [{"weekday": 5, "hour": 19, "minute": 0}],
            "minutes": 60,
            "role_ids": [9182736455],
            "last_warn_key": "CANARY_WARN_KEY_9f3a",
            "channel_id": 42,
        },
    })
    write("dementors.json", {
        "attack_schedule": {
            "enabled": True,
            "slots": [{"weekday": 5, "hour": 19, "minute": 0, "minutes": 10}],
            "role_ids": [9182736455],
            "last_warn_key": "CANARY_WARN_KEY_9f3a",
        },
        "event": {
            "practice": True,
            "name": "CANARY_PRACTICE_9f3a",
            "started_at": NOW,
            "ends_at": NOW + 600,
            "minutes": 10,
            "weak": "CANARY_WEAK_SPELL_9f3a",
            "tally": {"x": "CANARY_TALLY_9f3a"},
        },
        "active": {"weak": "CANARY_WEAK_SPELL_9f3a", "name": "Hidden Beast"},
    })
    (STATE / "place_secrets.json").write_text(
        json.dumps({"phrases": ["CANARY_SECRET_PHRASE_9f3a"]}),
        encoding="utf-8",
    )


class Role:
    def __init__(self, name: str, rid: int):
        self.name = name
        self.id = rid


class Member:
    def __init__(self):
        self.id = USER
        self.display_name = "Gon Thornmere"
        self.roles = [Role("House Thornmere", 5)]
        self.display_avatar = type("Avatar", (), {"url": "https://cdn.example/gon.png"})()


class Guild:
    def get_member(self, user_id: int):
        if user_id == USER:
            return Member()
        return None


class Bot:
    guilds = [Guild()]


def assert_clean(body: str, payload, *, allow_member_points: bool = False) -> None:
    for canary in CANARIES:
        assert canary not in body, canary
    for number in HIDDEN_NUMBERS:
        assert number not in body, number
    if not allow_member_points:
        assert "424242" not in body
    found: set[str] = set()
    keys_of(payload, found)
    leaked = found & FORBIDDEN_KEYS
    assert not leaked, sorted(leaked)


async def fetch(client: TestClient, path: str, *, key: str | None = KEY, header: str = "X-Web-Api-Key"):
    headers = {}
    if key is not None:
        headers[header] = key
    response = await client.get(path, headers=headers)
    text = await response.text()
    return response, text


async def main() -> None:
    source = (ROOT / "cogs" / "web_api.py").read_text(encoding="utf-8")
    assert "json.dump(" not in source
    assert "os.replace" not in source
    assert ".mkdir" not in source
    assert "open(" in source
    for line in source.splitlines():
        if "open(" in line:
            assert '"r"' in line or "'r'" in line, line

    chicago = ZoneInfo("America/Chicago")
    noon = datetime(2026, 10, 17, 12, 0, tzinfo=chicago)
    later = datetime(2026, 10, 17, 19, 3, tzinfo=chicago)
    slot = [{"weekday": 5, "hour": 19, "minute": 0}]
    assert next_weekly(slot, noon) == datetime(2026, 10, 17, 19, 0, tzinfo=chicago)
    assert next_weekly(slot, later) == datetime(2026, 10, 24, 19, 0, tzinfo=chicago)
    assert next_weekly([], noon) is None

    seed()
    before = snapshot()
    app = create_app(None, None, asyncio.Lock(), bot=Bot())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        health = await client.get("/health")
        assert health.status == 200
        assert (await health.json())["ok"] is True

        hidden, hidden_body = await fetch(client, "/api/site/standings", key=None)
        assert hidden.status == 404
        for canary in CANARIES:
            assert canary not in hidden_body

        os.environ["WEB_API_KEY"] = KEY
        missing, _missing_body = await fetch(client, "/api/site/standings", key=None)
        assert missing.status == 401
        wrong, _wrong_body = await fetch(client, "/api/site/events", key="nope")
        assert wrong.status == 401
        query = await client.get(f"/api/site/standings?key={KEY}")
        assert query.status == 401

        posted = await client.post("/api/site/standings", headers={"X-Web-Api-Key": KEY})
        assert posted.status == 405
        os.environ.pop("WEB_API_KEY", None)
        posted_off = await client.post("/api/site/standings", headers={"X-Web-Api-Key": KEY})
        assert posted_off.status == 404
        os.environ["WEB_API_KEY"] = KEY

        status, text = await fetch(client, "/api/site/standings")
        assert status.status == 200
        assert status.headers["Referrer-Policy"] == "no-referrer"
        assert status.headers["Cache-Control"] == "no-store"
        assert status.content_type == "application/json"
        standings = json.loads(text)
        assert_clean(text, standings)
        assert standings["season"]["name"] == "Season 2"
        assert standings["season"]["active"] is True
        by_key = {row["key"]: row for row in standings["houses"]}
        assert list(by_key) == ["vashara", "thornmere", "caldrin", "veyren", "moonveil"]
        assert by_key["vashara"]["name"] == "Oakmont"
        assert by_key["vashara"]["season_points"] == 20
        assert by_key["vashara"]["season_rank"] == 1
        assert by_key["thornmere"]["season_rank"] == 1
        assert by_key["thornmere"]["alltime_points"] == 50
        assert by_key["caldrin"]["season_rank"] == 3
        assert by_key["thornmere"]["color"] == "#3E8E5A"
        assert standings["leader"]["key"] == "vashara"
        assert standings["leader"]["margin"] == 0
        assert standings["leader"]["tied_with"][0]["name"] == "Thornmere"
        assert standings["last_cup"]["winner"]["name"] == "Thornmere"
        assert standings["warnings"] == []

        status, text = await fetch(client, f"/api/site/users/{USER}/profile")
        assert status.status == 200
        profile = json.loads(text)
        assert_clean(text, profile, allow_member_points=True)
        assert "424242" in text
        assert profile["name"] == "Gon Thornmere"
        assert profile["name"] != "Stale Name"
        assert profile["avatar_url"] == "https://cdn.example/gon.png"
        assert profile["house"]["key"] == "thornmere"
        assert profile["house"]["source"] == "role"
        assert profile["points"]["season"] == 424242
        assert profile["points"]["season_rank"] == 1
        assert profile["points"]["alltime"] == 80
        assert profile["honours"]["champion_of"] == ["Season 1"]
        assert profile["honours"]["cups"] == ["Season 1"]
        assert profile["duelling"]["title"] == "Novice"
        assert profile["duelling"]["wins"] == 4
        assert profile["duelling"]["rank"] == 2
        assert profile["duelling"]["duellists"] == 2
        assert profile["duelling"]["streak"] == 3
        assert profile["duelling"]["bounty"] is True
        assert profile["duelling"]["champion"] is True
        assert profile["duelling"]["signature"] == "the Hexer"
        assert profile["duelling"]["rival_user_id"] == "100"
        assert profile["wand"]["wood"] == "Holly"
        assert profile["wand"]["length"] == "11¼ inches"
        assert profile["wand"]["glows"] is True
        assert profile["patronus"]["animal"] == "Stag"
        assert profile["patronus"]["headline"] == "A silver stag"
        assert profile["animagus"]["animal"] == "Fox"
        assert profile["broom"]["speed"] == 5
        assert profile["broom"]["altitude"] == 6
        assert profile["familiar"]["name"] == "Pip"
        assert profile["familiar"]["tier"] == "Curious"
        assert profile["bestiary"]["befriended"] == 1
        assert profile["bestiary"]["rank"] == "Beast-Spotter"
        assert profile["bestiary"]["total"] > 50
        assert profile["adornments"]["title"] == "Quiet Prefect"
        assert profile["adornments"]["owned"] == 2
        assert profile["adornments"]["worn"] == [{"slot": "necklace", "name": "Moonbloom Locket"}]
        assert profile["triwizard"][0]["name"] == "Tri-Wizard Tournament III"
        assert profile["triwizard"][0]["house"]["name"] == "Thornmere"
        assert len(profile["triwizard"]) == 1
        assert "Oak" not in text

        points = json.loads((STATE / "points_store.json").read_text(encoding="utf-8"))
        points["overrides"][str(USER)] = "vashara"
        write("points_store.json", points)
        clear_caches()
        status, text = await fetch(client, f"/api/site/users/{USER}/profile")
        overridden = json.loads(text)
        assert overridden["house"]["key"] == "vashara"
        assert overridden["house"]["name"] == "Oakmont"
        assert overridden["house"]["source"] == "override"
        points["overrides"] = {}
        write("points_store.json", points)
        clear_caches()

        status, text = await fetch(client, f"/api/site/users/{USER}/satchel")
        satchel = json.loads(text)
        assert_clean(text, satchel, allow_member_points=True)
        assert satchel["potions"] == [{
            "id": "felix_felicis",
            "name": "Felix Felicis",
            "emoji": "🥇",
            "tier": "legendary",
            "count": 2,
            "blurb": satchel["potions"][0]["blurb"],
        }]
        assert "Beastcaller" not in text
        assert "not_a_potion" not in text
        ingredient_ids = [row["id"] for row in satchel["ingredients"]]
        assert ingredient_ids == ["dragon_scale", "silverleaf"]
        assert [row["count"] for row in satchel["ingredients"]] == [1, 2]
        item_ids = [row["id"] for row in satchel["items"]]
        assert item_ids == ["strange_key", "broken_compass"]
        assert [row["count"] for row in satchel["items"]] == [3, 4]
        assert satchel["items"][1]["name"] == "Broken Compass"
        assert "map_scrap" not in text

        status, text = await fetch(client, f"/api/site/users/{USER}/frogs")
        frogs = json.loads(text)
        assert_clean(text, frogs, allow_member_points=True)
        assert frogs["total"] == 50
        assert frogs["unique"] == 1
        assert frogs["copies"] == 2
        assert frogs["missing"] == 49
        owned = [row for row in frogs["cards"] if row["count"]]
        assert owned == [{
            "id": "lumen_aegis",
            "name": "Lumen Aegis",
            "emoji": "✨",
            "rarity": "mythic",
            "rarity_label": "✦ Mythic ✦",
            "kind": "spell",
            "count": 2,
            "blurb": owned[0]["blurb"],
        }]
        assert any(row["id"] == "expecto" and row["count"] == 0 for row in frogs["cards"])
        assert "CANARY_CARD" not in text
        found: set[str] = set()
        keys_of(frogs, found)
        assert "odds" not in found

        status, text = await fetch(client, "/api/site/events")
        events = json.loads(text)
        assert_clean(text, events)
        assert events["timezone"] == "America/Chicago"
        attack = events["attack"]
        assert attack["enabled"] is True
        assert attack["warn_minutes"] == 20
        assert attack["slots"][0]["weekday_label"] == "Saturday"
        assert attack["slots"][0]["hour"] == 19
        assert attack["slots"][0]["label"] == "10-minute"
        assert attack["slots"][0]["kill_pool"] == 300
        assert attack["next"]["weekday"] == 5
        assert attack["next"]["hour"] == 19
        assert events["duel_night"]["label"] == "1 hour"
        assert events["duel_night"]["next"]["hour"] == 19
        assert events["live"]["attack"] == {"active": False}
        assert events["live"]["duel_night"]["active"] is True
        assert events["live"]["duel_night"]["label"] == "1 hour"
        assert events["house_cup"]["houses"][0]["season_points"] == 20
        assert "Hidden Beast" not in text

        empty_status, empty_text = await fetch(client, "/api/site/users/7/satchel")
        assert empty_status.status == 200
        empty = json.loads(empty_text)
        assert empty["potions"] == []
        assert empty["ingredients"] == []
        assert empty["items"] == []

        bad, _bad_text = await fetch(client, "/api/site/users/abc/profile")
        assert bad.status == 400
        zero, _zero_text = await fetch(client, "/api/site/users/0/frogs")
        assert zero.status == 400
        unknown, _unknown_text = await fetch(client, "/api/site/nope")
        assert unknown.status == 404

        assert snapshot() == before

        os.environ["WEB_API_CACHE_SECONDS"] = "60"
        clear_caches()
        first, first_text = await fetch(client, "/api/site/standings")
        assert json.loads(first_text)["houses"][0]["season_points"] == 20
        cached_points = json.loads((STATE / "points_store.json").read_text(encoding="utf-8"))
        cached_points["totals"]["season"]["houses"]["vashara"] = 33
        cached_points["totals"]["season"]["houses"]["thornmere"] = 1
        write("points_store.json", cached_points)
        second, second_text = await fetch(client, "/api/site/standings")
        assert json.loads(second_text)["houses"][0]["season_points"] == 20
        os.environ["WEB_API_CACHE_SECONDS"] = "0"
        third, third_text = await fetch(client, "/api/site/standings")
        fresh = json.loads(third_text)
        assert fresh["houses"][0]["key"] == "vashara"
        assert fresh["houses"][0]["season_points"] == 33

        clear_caches()
        write("points_store.json", cached_points)
        warmed, warmed_text = await fetch(client, "/api/site/standings")
        assert json.loads(warmed_text)["houses"][0]["season_points"] == 33
        (STATE / "points_store.json").write_text("{not json", encoding="utf-8")
        stale, stale_text = await fetch(client, "/api/site/standings")
        assert stale.status == 200
        stale_body = json.loads(stale_text)
        assert stale_body["houses"][0]["season_points"] == 33
        assert "points_store.json" in stale_body["warnings"]

        legacy = {
            "attack_schedule": {
                "enabled": True,
                "weekdays": [5],
                "hour": 19,
                "minute": 30,
                "minutes": 10,
                "slots": [],
                "role_ids": [9182736455],
                "last_warn_key": "CANARY_WARN_KEY_9f3a",
            },
        }
        legacy_path = STATE / "dementors.json"
        legacy_path.write_text(json.dumps(legacy, indent=2), encoding="utf-8")
        legacy_bytes = legacy_path.read_bytes()
        clear_caches()
        legacy_status, legacy_text = await fetch(client, "/api/site/events")
        assert legacy_path.read_bytes() == legacy_bytes
        legacy_body = json.loads(legacy_text)
        assert legacy_body["attack"]["slots"][0]["minute"] == 30
        assert legacy_body["attack"]["slots"][0]["hour"] == 19
        assert_clean(legacy_text, legacy_body, allow_member_points=True)

        live = json.loads(legacy_bytes)
        # Restore a public Attack without copying the legacy warn key into the response.
        write("dementors.json", {
            "attack_schedule": legacy["attack_schedule"],
            "event": {
                "name": "Attack on Velmora",
                "started_at": NOW,
                "ends_at": NOW + 600,
                "minutes": 10,
                "weak": "CANARY_WEAK_SPELL_9f3a",
                "tally": {"note": "CANARY_TALLY_9f3a"},
                "channels": {"1": "CANARY_SECRET_PHRASE_9f3a"},
            },
        })
        clear_caches()
        live_status, live_text = await fetch(client, "/api/site/events")
        assert live_status.status == 200
        live_body = json.loads(live_text)
        assert live_body["live"]["attack"]["active"] is True
        assert live_body["live"]["attack"]["name"] == "Attack on Velmora"
        assert_clean(live_text, live_body, allow_member_points=True)

        frogs_path = STATE / "frogs_state.json"
        frogs_path.unlink()
        clear_caches()
        gone, gone_text = await fetch(client, f"/api/site/users/{USER}/frogs")
        assert gone.status == 200
        assert json.loads(gone_text)["unique"] == 0
        assert not frogs_path.exists()

        os.environ["WEB_API_KEY_HEADER"] = "X-Velmora-Key"
        clear_caches()
        still_old, _old_text = await fetch(client, "/api/site/standings")
        assert still_old.status == 401
        renamed, _renamed_text = await fetch(
            client, "/api/site/standings", header="X-Velmora-Key",
        )
        assert renamed.status == 200

        health_again = await client.get("/health")
        assert health_again.status == 200
    finally:
        await client.close()

    print("website api ok")


if __name__ == "__main__":
    asyncio.run(main())
