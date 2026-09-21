"""
The ledger itself: where points live, how they're written down, and how a
member's house is worked out.

Every award or deduction is appended to a ledger AND folded into running
totals, so standings are instant and history stays readable. Totals are
kept for two scopes at once:

    season   - resets when a House Cup season ends
    alltime  - never resets; the hall of fame

State is written to STATE_DIR/points_store.json. On Railway that should be
a mounted volume, otherwise every redeploy wipes the scores.
"""

import json
import logging
import os
import time
import uuid
from pathlib import Path

import discord
from discord.ext import commands

log = logging.getLogger("velmora.store")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STORE_PATH = STATE_DIR / "points_store.json"

# How many ledger entries to keep for /history and /undo. Totals are kept
# separately and are never trimmed, so trimming history never loses points.
LEDGER_LIMIT = 500

# The five houses of Velmora. Key -> display name, colour, emblem.
HOUSES = {
    "vashara": {
        "name": "Vashara",
        "color": 0x4C9A7D,
        "emoji": "\U0001F418",  # elephant
        "motto": "The first house. Medicine, patience, and care for the fragile.",
    },
    "moonveil": {
        "name": "Moonveil",
        "color": 0x8B5FBF,
        "emoji": "\U0001F315",  # full moon
        "motto": "Curiosity without a leash. For research purposes, of course.",
    },
    "veyren": {
        "name": "Veyren",
        "color": 0xD9A441,
        "emoji": "\U0001F98C",  # deer
        "motto": "Some bonds need no words.",
    },
    "caldrin": {
        "name": "Caldrin",
        "color": 0x3FA9A0,
        "emoji": "\U0001F52D",  # telescope
        "motto": "Build it anyway. Ask forgiveness of the blueprint later.",
    },
    "thornmere": {
        "name": "Thornmere",
        "color": 0xB8434F,
        "emoji": "\U0001F43A",  # wolf
        "motto": "Think it through. Then think it through again.",
    },
}

HOUSE_KEYS = tuple(HOUSES.keys())


def house_display(key: str) -> str:
    """'moonveil' -> '🌕 House Moonveil' for embeds and lists."""
    h = HOUSES.get(key)
    if not h:
        return key.title()
    return f"{h['emoji']} House {h['name']}"


def _blank_state() -> dict:
    return {
        "version": 1,
        "season": {"number": 1, "name": "Season 1", "started_at": time.time()},
        "totals": {
            "season": {"houses": {}, "members": {}, "house_members": {}},
            "alltime": {"houses": {}, "members": {}},
        },
        "ledger": [],
        "archive": [],
        "overrides": {},
        "settings": {
            "staff_role_id": None,
            "announce_channel_id": None,
            "announce_weekday": 6,  # 0=Monday ... 6=Sunday
            "announce_hour": 18,    # local to the bot's TZ (UTC on Railway)
            "house_roles": {},      # house key -> role id
            "last_announced_week": None,
        },
    }


def _rebuild_house_members(state: dict) -> dict:
    """Reconstruct this season's per-house member totals from the ledger.
    Exact as long as the ledger still holds the whole season, which it does
    for any season shorter than the ledger limit."""
    season = state.get("season", {}).get("number")
    rebuilt: dict = {}
    for entry in state.get("ledger", []):
        if entry.get("undone") or not entry.get("target_id"):
            continue
        if entry.get("season") != season:
            continue
        bucket = rebuilt.setdefault(entry["house"], {})
        key = str(entry["target_id"])
        bucket[key] = bucket.get(key, 0) + entry["delta"]
    return rebuilt


class Store(commands.Cog):
    """Data layer. Other cogs reach it with bot.get_cog('Store')."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()

    # ---------------------------------------------------------------- disk

    def _load(self) -> dict:
        try:
            with open(STORE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            # Fill in anything a older version of the file is missing, so an
            # upgrade never crashes on a key that didn't exist yet.
            base = _blank_state()
            for key, value in base.items():
                state.setdefault(key, value)
            for key, value in base["settings"].items():
                state["settings"].setdefault(key, value)
            for scope in ("season", "alltime"):
                state["totals"].setdefault(scope, {"houses": {}, "members": {}})
                state["totals"][scope].setdefault("houses", {})
                state["totals"][scope].setdefault("members", {})
            if "house_members" not in state["totals"]["season"]:
                state["totals"]["season"]["house_members"] = _rebuild_house_members(state)
            log.info(
                "Loaded ledger from %s (%d entries, season %s)",
                STORE_PATH,
                len(state.get("ledger", [])),
                state["season"].get("number"),
            )
            return state
        except FileNotFoundError:
            log.info("No ledger at %s yet - starting a fresh one.", STORE_PATH)
            return _blank_state()
        except (OSError, json.JSONDecodeError):
            # Keep the damaged file instead of overwriting it on the next
            # save - a points ledger is worth recovering by hand.
            try:
                backup = STORE_PATH.with_suffix(f".corrupt.{int(time.time())}.json")
                os.replace(STORE_PATH, backup)
                log.exception("Ledger at %s is unreadable - moved it to %s and started fresh.",
                              STORE_PATH, backup)
            except OSError:
                log.exception("Ledger at %s is unreadable and could not be set aside.", STORE_PATH)
            return _blank_state()

    def save(self) -> None:
        try:
            STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STORE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STORE_PATH)  # atomic: never leaves a half-written ledger
        except OSError:
            log.exception("Could not write the ledger to %s", STORE_PATH)

    # ------------------------------------------------------------ settings

    @property
    def settings(self) -> dict:
        return self.state["settings"]

    def set_setting(self, key: str, value) -> None:
        self.settings[key] = value
        self.save()

    def is_staff(self, member) -> bool:
        """Server admins always count. Beyond that, whoever holds the
        configured staff role."""
        perms = getattr(member, "guild_permissions", None)
        if perms is not None and (getattr(perms, "manage_guild", False) or getattr(perms, "administrator", False)):
            return True
        role_id = self.settings.get("staff_role_id")
        if not role_id:
            return False
        return any(r.id == role_id for r in getattr(member, "roles", []))

    # -------------------------------------------------------------- houses

    def member_house(self, member) -> str | None:
        """Which house this member belongs to, or None.

        A manual override wins. Otherwise the member's Discord roles are
        checked - first against explicitly bound role IDs, then by matching
        the house name inside the role's name, so a server that names its
        roles 'House Moonveil' or 'Moonveil Student' works with no setup.
        """
        override = self.state["overrides"].get(str(member.id))
        if override in HOUSES:
            return override

        bound = self.settings.get("house_roles", {})
        role_ids = {r.id for r in getattr(member, "roles", [])}
        for house_key, rid in bound.items():
            if rid in role_ids and house_key in HOUSES:
                return house_key

        for role in getattr(member, "roles", []):
            name = (role.name or "").lower()
            for house_key, meta in HOUSES.items():
                if meta["name"].lower() in name:
                    return house_key
        return None

    def set_override(self, user_id: int, house_key: str | None) -> None:
        if house_key is None:
            self.state["overrides"].pop(str(user_id), None)
        else:
            self.state["overrides"][str(user_id)] = house_key
        self.save()

    def bind_house_role(self, house_key: str, role_id: int) -> None:
        self.settings.setdefault("house_roles", {})[house_key] = role_id
        self.save()

    # -------------------------------------------------------------- ledger

    def record(self, *, house: str, delta: int, actor_id: int,
               target_id: int | None = None, reason: str = "") -> dict:
        """Write one award or deduction and fold it into both scopes."""
        entry = {
            "id": uuid.uuid4().hex[:8],
            "at": time.time(),
            "season": self.state["season"]["number"],
            "house": house,
            "delta": int(delta),
            "actor_id": actor_id,
            "target_id": target_id,
            "reason": reason.strip(),
            "undone": False,
        }
        self._apply(entry, sign=1)
        self.state["ledger"].append(entry)
        if len(self.state["ledger"]) > LEDGER_LIMIT:
            self.state["ledger"] = self.state["ledger"][-LEDGER_LIMIT:]
        self.save()
        return entry

    def _apply(self, entry: dict, sign: int) -> None:
        """Add (sign=1) or back out (sign=-1) an entry's effect on totals."""
        delta = entry["delta"] * sign
        for scope in ("season", "alltime"):
            houses = self.state["totals"][scope]["houses"]
            houses[entry["house"]] = houses.get(entry["house"], 0) + delta
            if entry.get("target_id"):
                members = self.state["totals"][scope]["members"]
                key = str(entry["target_id"])
                members[key] = members.get(key, 0) + delta
        # Who earned what FOR WHICH HOUSE this season - needed to crown the
        # champion, since a member's points are only credited to one house.
        if entry.get("target_id"):
            by_house = self.state["totals"]["season"].setdefault("house_members", {})
            bucket = by_house.setdefault(entry["house"], {})
            key = str(entry["target_id"])
            bucket[key] = bucket.get(key, 0) + delta

    def undo_last(self, *, actor_id: int | None = None) -> dict | None:
        """Reverse the most recent entry that hasn't already been undone."""
        for entry in reversed(self.state["ledger"]):
            if entry.get("undone"):
                continue
            self._apply(entry, sign=-1)
            entry["undone"] = True
            entry["undone_by"] = actor_id
            entry["undone_at"] = time.time()
            self.save()
            return entry
        return None

    # -------------------------------------------------------------- totals

    def house_totals(self, scope: str = "season") -> list[tuple[str, int]]:
        """Every house, highest first. Houses with no points still appear."""
        totals = self.state["totals"].get(scope, {}).get("houses", {})
        rows = [(key, int(totals.get(key, 0))) for key in HOUSE_KEYS]
        rows.sort(key=lambda r: (-r[1], HOUSES[r[0]]["name"]))
        return rows

    def member_totals(self, scope: str = "season", limit: int = 10) -> list[tuple[int, int]]:
        totals = self.state["totals"].get(scope, {}).get("members", {})
        rows = [(int(uid), int(pts)) for uid, pts in totals.items() if pts]
        rows.sort(key=lambda r: -r[1])
        return rows[:limit]

    def member_points(self, user_id: int, scope: str = "season") -> int:
        return int(self.state["totals"].get(scope, {}).get("members", {}).get(str(user_id), 0))

    def member_rank(self, user_id: int, scope: str = "season") -> int | None:
        rows = self.member_totals(scope=scope, limit=10_000)
        for i, (uid, _) in enumerate(rows, start=1):
            if uid == user_id:
                return i
        return None

    def history(self, user_id: int | None = None, limit: int = 10) -> list[dict]:
        entries = [e for e in self.state["ledger"] if not e.get("undone")]
        if user_id is not None:
            entries = [e for e in entries if e.get("target_id") == user_id]
        return list(reversed(entries))[:limit]

    # ------------------------------------------------------------- seasons

    def current_season(self) -> dict:
        return self.state["season"]

    def end_season(self) -> dict:
        """Archive the season, crown a winner, and zero the season scores.
        All-time totals are untouched."""
        standings = self.house_totals("season")
        top = standings[0] if standings else (None, 0)
        # A tie at the top means nobody is crowned outright.
        tied = [k for k, p in standings if p == top[1] and p != 0]
        winner = tied[0] if len(tied) == 1 else None

        # The champion is the highest point earner in the winning house.
        # Level at the top means co-champions; no outright house, no champion.
        champions, winning_members = [], {}
        if winner:
            winning_members = dict(
                self.state["totals"]["season"].get("house_members", {}).get(winner, {})
            )
            best = max(winning_members.values(), default=0)
            if best > 0:
                champions = [{"id": int(uid), "points": pts}
                             for uid, pts in winning_members.items() if pts == best]

        record = {
            "number": self.state["season"]["number"],
            "name": self.state["season"]["name"],
            "started_at": self.state["season"]["started_at"],
            "ended_at": time.time(),
            "standings": standings,
            "winner": winner,
            "tied": tied if len(tied) > 1 else [],
            "champions": champions,
            # Everyone who earned for the winning house - they share the cup.
            "winning_members": {uid: pts for uid, pts in winning_members.items() if pts > 0},
        }
        self.state["archive"].append(record)
        self.state["totals"]["season"] = {"houses": {}, "members": {}, "house_members": {}}
        self.state["season"] = {
            "number": record["number"] + 1,
            "name": f"Season {record['number'] + 1}",
            "started_at": time.time(),
        }
        self.save()
        return record

    def honours(self, user_id: int) -> dict:
        """A member's House Cup record across every finished season."""
        uid = str(user_id)
        champion_of, cups = [], []
        for record in self.state.get("archive", []):
            if any(c["id"] == user_id for c in record.get("champions", [])):
                champion_of.append(record["name"])
            if uid in record.get("winning_members", {}):
                cups.append(record["name"])
        return {"champion_of": champion_of, "cups": cups}

    def rename_season(self, name: str) -> None:
        self.state["season"]["name"] = name.strip() or self.state["season"]["name"]
        self.save()


async def setup(bot: commands.Bot):
    await bot.add_cog(Store(bot))
