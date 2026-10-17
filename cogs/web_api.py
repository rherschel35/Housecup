"""Read-only JSON for the Velmora website.

Mounted beside the Curse & Crown routes. Every handler reads JSON under
STATE_DIR (or the shipped catalogs under data/) and returns a projection.
Nothing here awards points, mutates a cog, or writes a file.

The routes stay dark unless WEB_API_KEY is set. Callers send that secret
in X-Web-Api-Key, or in WEB_API_KEY_HEADER when that name is set.

Place-secret phrases, the three words behind a wand, brew sequences, duel
reputation, and live fight tallies are not part of any response.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import web

log = logging.getLogger("velmora.web_api")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
USER_ID_RE = re.compile(r"^[0-9]{1,20}$")
DEFAULT_HEADER = "X-Web-Api-Key"
WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
# Key names that must never leave this process, even if a builder slips.
_DROP_KEY = re.compile(
    r"phrase|secret|password|token|answer|sequence|weakness|"
    r"^words$|^flags$|^requires$|^revealed$|^spell_wins$|^rep$|^weak$|"
    r"^tally$|^pairs$|^not_flags$",
    re.IGNORECASE,
)

_libs = None
_files: "FileCache | None" = None
_payloads: "PayloadCache | None" = None


def _state_dir() -> Path:
    raw = (os.environ.get("STATE_DIR") or "").strip()
    if raw:
        return Path(raw)
    return DATA_DIR


def _api_key() -> str:
    return (os.environ.get("WEB_API_KEY") or "").strip()


def _header_name() -> str:
    raw = (os.environ.get("WEB_API_KEY_HEADER") or "").strip()
    if not raw or any(ord(ch) < 33 or ord(ch) > 126 or ch in ":/" for ch in raw):
        return DEFAULT_HEADER
    return raw


def _cache_seconds() -> float:
    raw = (os.environ.get("WEB_API_CACHE_SECONDS") or "").strip()
    if not raw:
        return 3.0
    try:
        value = float(raw)
    except ValueError:
        return 3.0
    if value < 0:
        return 0.0
    return min(value, 120.0)


def _chicago():
    try:
        return ZoneInfo("America/Chicago")
    except Exception:
        return timezone.utc


def _catalogs():
    """Public catalogs. Imported once, on the event-loop thread, at mount."""
    global _libs
    if _libs is not None:
        return _libs
    from types import SimpleNamespace

    from cogs.beasts import PLACES as BEAST_PLACES
    from cogs.beasts import rank_for as beast_rank_for
    from cogs.brooms import effective_stats
    from cogs.dementors import EVENT_PRESETS, EVENT_WARN_MINUTES, _preset_kill_pool
    from cogs.duels import (
        CHAMPION_ROLE_NAME,
        DUEL_NIGHT_PRESETS,
        DUEL_NIGHT_WARN_MINUTES,
        REP_GLOW,
        RIVAL_AFTER,
        SIGNATURE_TITLES,
        rank_for as duel_rank_for,
        signature_unlocks_for,
    )
    from cogs.familiars import FAMILIARS, LOOKS, TIERS
    from cogs.frogs import CARDS as FROG_CARDS
    from cogs.frogs import DECADE_PTS, MYTHIC_PTS
    from cogs.frogs import RARITY_LABEL as FROG_RARITY
    from cogs.gear_data import GEAR, SLOTS
    from cogs.patronus import CUSTOM_PATRONUSES
    from cogs.potions import RECIPES
    from cogs.store import HOUSE_KEYS, HOUSES
    from cogs.wands import _fmt_length
    from cogs.world_engine import RARITY_LABEL, RARITY_ORDER

    ingredient_ids = frozenset(
        item_id
        for recipe in RECIPES.values()
        if isinstance(recipe, dict)
        for item_id in (recipe.get("ingredients") or [])
        if isinstance(item_id, str)
    )
    _libs = SimpleNamespace(
        HOUSES=HOUSES,
        HOUSE_KEYS=HOUSE_KEYS,
        RECIPES=RECIPES,
        ingredient_ids=ingredient_ids,
        RARITY_LABEL=RARITY_LABEL,
        RARITY_ORDER=RARITY_ORDER,
        FROG_CARDS=FROG_CARDS,
        FROG_RARITY=FROG_RARITY,
        DECADE_PTS=DECADE_PTS,
        MYTHIC_PTS=MYTHIC_PTS,
        duel_rank_for=duel_rank_for,
        REP_GLOW=REP_GLOW,
        RIVAL_AFTER=RIVAL_AFTER,
        SIGNATURE_TITLES=SIGNATURE_TITLES,
        signature_unlocks_for=signature_unlocks_for,
        CHAMPION_ROLE_NAME=CHAMPION_ROLE_NAME,
        DUEL_NIGHT_PRESETS=DUEL_NIGHT_PRESETS,
        DUEL_NIGHT_WARN_MINUTES=DUEL_NIGHT_WARN_MINUTES,
        EVENT_PRESETS=EVENT_PRESETS,
        EVENT_WARN_MINUTES=EVENT_WARN_MINUTES,
        preset_kill_pool=_preset_kill_pool,
        beast_rank_for=beast_rank_for,
        BEAST_PLACES=BEAST_PLACES,
        FAMILIARS=FAMILIARS,
        LOOKS=LOOKS,
        TIERS=TIERS,
        GEAR=GEAR,
        SLOTS=SLOTS,
        CUSTOM_PATRONUSES=CUSTOM_PATRONUSES,
        fmt_length=_fmt_length,
        effective_stats=effective_stats,
    )
    return _libs


class FileCache:
    """Parsed JSON keyed by path, mtime, and size. Reads are the only I/O."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[int, int, object]] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def read(self, path: Path) -> tuple[object | None, str | None]:
        """Return (data, warning). Missing files are (None, None)."""
        key = str(path)
        try:
            st = path.stat()
        except FileNotFoundError:
            return None, None
        except OSError:
            log.warning("website api could not stat %s", path.name)
            return self._stale(key, path.name)

        with self._lock:
            hit = self._entries.get(key)
        if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2], None

        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeError, RecursionError):
            log.warning("website api could not read %s", path.name)
            return self._stale(key, path.name)

        with self._lock:
            self._entries[key] = (st.st_mtime_ns, st.st_size, data)
        return data, None

    def _stale(self, key: str, name: str) -> tuple[object | None, str]:
        with self._lock:
            hit = self._entries.get(key)
        if hit is not None:
            return hit[2], name
        return None, name


class PayloadCache:
    """Short reuse of a finished response so a page load does not restat."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict]] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get(self, key: str) -> dict | None:
        ttl = _cache_seconds()
        if ttl <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if hit is None or now - hit[0] > ttl:
                return None
            return hit[1]

    def put(self, key: str, payload: dict) -> None:
        if _cache_seconds() <= 0:
            return
        now = time.monotonic()
        with self._lock:
            self._entries[key] = (now, payload)
            if len(self._entries) <= 512:
                return
            oldest = min(self._entries, key=lambda item: self._entries[item][0])
            self._entries.pop(oldest, None)


def clear_caches() -> None:
    """Drop parsed files and finished responses. Tests use this."""
    if _files is not None:
        _files.clear()
    if _payloads is not None:
        _payloads.clear()


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(
        data,
        status=status,
        dumps=lambda obj: json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
    )


def _authorized(request: web.Request) -> bool:
    expected = _api_key()
    if not expected:
        return False
    given = request.headers.get(_header_name(), "")
    if not isinstance(given, str):
        return False
    given = given.strip()
    if not given:
        return False
    return hmac.compare_digest(
        hashlib.sha256(given.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def _scrub(value):
    if isinstance(value, dict):
        return {
            str(key): _scrub(item)
            for key, item in value.items()
            if not _DROP_KEY.search(str(key))
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _as_int(value, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if isinstance(value, float) and not value.is_integer():
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _positive(value) -> int | None:
    count = _as_int(value, default=0)
    if count <= 0 or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return count


def _text(value, limit: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _unix(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number)


def _same_id(value, user_id: int) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return int(value) == user_id
    except (TypeError, ValueError):
        return False


def _color(value) -> str:
    try:
        return f"#{int(value) & 0xFFFFFF:06X}"
    except (TypeError, ValueError):
        return "#6C5CE7"


def _house_public(key: str | None, *, source: str | None = None) -> dict | None:
    libs = _catalogs()
    meta = libs.HOUSES.get(key or "")
    if not meta:
        return None
    body = {
        "key": key,
        "name": meta["name"],
        "emoji": meta.get("emoji") or "",
        "color": _color(meta.get("color")),
        "motto": meta.get("motto") or "",
        "former_name": meta.get("former_name"),
    }
    if source:
        body["source"] = source
    return body


def _load(path: Path, warnings: list[str]):
    if _files is None:
        if path.name not in warnings:
            warnings.append(path.name)
        return None
    data, warning = _files.read(path)
    if warning and warning not in warnings:
        warnings.append(warning)
    return data


def _load_state(name: str, warnings: list[str]) -> dict | None:
    data = _load(_state_dir() / name, warnings)
    if data is None:
        return None
    if isinstance(data, dict):
        return data
    if name not in warnings:
        warnings.append(name)
    return None


def _cached_member(bot, user_id: int):
    if bot is None:
        return None
    try:
        guilds = list(getattr(bot, "guilds", ()) or ())
    except Exception:
        log.warning("website api could not read the guild cache")
        return None
    for guild in guilds:
        getter = getattr(guild, "get_member", None)
        if getter is None:
            continue
        try:
            member = getter(user_id)
        except Exception:
            log.warning("website api could not look up a cached member")
            continue
        if member is not None:
            return member
    return None


def _house_for(points: dict | None, user_id: int, member) -> dict | None:
    libs = _catalogs()
    points = points or {}
    override = _as_dict(points.get("overrides")).get(str(user_id))
    if override in libs.HOUSES:
        return _house_public(override, source="override")

    if member is None:
        return None
    roles = list(getattr(member, "roles", ()) or ())
    role_ids = set()
    for role in roles:
        rid = getattr(role, "id", None)
        if isinstance(rid, int):
            role_ids.add(rid)
    bound = _as_dict(_as_dict(points.get("settings")).get("house_roles"))
    for house_key, rid in bound.items():
        if house_key in libs.HOUSES and rid in role_ids:
            return _house_public(house_key, source="role")
    for role in roles:
        name = (getattr(role, "name", "") or "").lower()
        if not name:
            continue
        for house_key, meta in libs.HOUSES.items():
            needles = {meta["name"].lower(), *(alias.lower() for alias in meta.get("aliases") or ())}
            former = meta.get("former_name")
            if former:
                needles.add(str(former).lower())
            if any(needle and needle in name for needle in needles):
                return _house_public(house_key, source="role")
    return None


def _member_points(points: dict | None, user_id: int, scope: str) -> int:
    members = _as_dict(_as_dict(_as_dict(points).get("totals")).get(scope)).get("members")
    return _as_int(_as_dict(members).get(str(user_id)))


def _member_rank(points: dict | None, user_id: int, scope: str) -> int | None:
    members = _as_dict(_as_dict(_as_dict(points).get("totals")).get(scope)).get("members")
    rows = []
    for uid, pts in _as_dict(members).items():
        score = _as_int(pts)
        if score <= 0 or not str(uid).isdigit():
            continue
        rows.append((int(uid), score))
    rows.sort(key=lambda row: (-row[1], row[0]))
    for index, (uid, _score) in enumerate(rows, start=1):
        if uid == user_id:
            return index
    return None


def _house_rows(points: dict | None, scope: str) -> list[tuple[str, int]]:
    libs = _catalogs()
    houses = _as_dict(_as_dict(_as_dict(points).get("totals")).get(scope)).get("houses")
    houses = _as_dict(houses)
    rows = [(key, _as_int(houses.get(key))) for key in libs.HOUSE_KEYS]
    rows.sort(key=lambda row: (-row[1], libs.HOUSES[row[0]]["name"]))
    return rows


def _competition_rank(rows: list[tuple[str, int]]) -> dict[str, int]:
    ranks = {}
    last_score = None
    last_rank = 0
    for index, (key, score) in enumerate(rows, start=1):
        if score != last_score:
            last_rank = index
            last_score = score
        ranks[key] = last_rank
    return ranks


def _season_public(points: dict | None) -> dict:
    season = _as_dict(_as_dict(points).get("season"))
    number = season.get("number")
    return {
        "number": _as_int(number) if isinstance(number, (int, float)) and not isinstance(number, bool) else None,
        "name": _text(season.get("name"), 80),
        "active": bool(season.get("active", True)) if season else False,
        "started_at": _unix(season.get("started_at")),
    }


def _last_cup(points: dict | None) -> dict | None:
    archive = _as_list(_as_dict(points).get("archive"))
    if not archive:
        return None
    last = archive[-1]
    if not isinstance(last, dict):
        return None
    winner = last.get("winner")
    tied = [
        _house_public(key)
        for key in _as_list(last.get("tied"))
        if _house_public(key)
    ]
    return {
        "number": _as_int(last.get("number")) if last.get("number") is not None else None,
        "name": _text(last.get("name"), 80),
        "winner": _house_public(winner if isinstance(winner, str) else None),
        "tied": tied,
    }


def _leader(rows: list[tuple[str, int]]) -> dict | None:
    if not rows or rows[0][1] <= 0:
        return None
    top_key, top_points = rows[0]
    house = _house_public(top_key)
    tied = [
        _house_public(key)
        for key, score in rows
        if score == top_points and key != top_key and _house_public(key)
    ]
    second = rows[1][1] if len(rows) > 1 else 0
    return {
        "key": top_key,
        "name": house["name"] if house else top_key,
        "points": top_points,
        "margin": top_points - second if not tied else 0,
        "tied_with": tied,
    }


def build_standings() -> dict:
    warnings: list[str] = []
    points = _load_state("points_store.json", warnings)
    season_rows = _house_rows(points, "season")
    alltime_rows = _house_rows(points, "alltime")
    season_rank = _competition_rank(season_rows)
    alltime_rank = _competition_rank(alltime_rows)
    alltime_points = dict(alltime_rows)
    houses = []
    for order, (key, season_points) in enumerate(season_rows, start=1):
        public = _house_public(key) or {}
        public.update({
            "season_points": season_points,
            "alltime_points": alltime_points.get(key, 0),
            "season_rank": season_rank.get(key),
            "alltime_rank": alltime_rank.get(key),
            "order": order,
        })
        houses.append(public)
    return {
        "generated_at": int(time.time()),
        "season": _season_public(points),
        "houses": houses,
        "leader": _leader(season_rows),
        "last_cup": _last_cup(points),
        "warnings": warnings,
    }


def _honours(points: dict | None, user_id: int) -> dict:
    champion_of = []
    cups = []
    for record in _as_list(_as_dict(points).get("archive")):
        if not isinstance(record, dict):
            continue
        name = _text(record.get("name"), 80)
        if not name:
            continue
        if any(_same_id(champ.get("id"), user_id) for champ in _as_list(record.get("champions")) if isinstance(champ, dict)):
            champion_of.append(name)
        winners = record.get("winning_members")
        if isinstance(winners, dict) and str(user_id) in winners:
            cups.append(name)
    return {"champion_of": champion_of, "cups": cups}


def _signature_title(duels: dict | None, user_id: int) -> str | None:
    libs = _catalogs()
    tally = _as_dict(_as_dict(duels).get("spell_wins")).get(str(user_id))
    tally = _as_dict(tally)
    clean = {}
    for spell, count in tally.items():
        score = _positive(count)
        if isinstance(spell, str) and score:
            clean[spell] = score
    try:
        unlocks = libs.signature_unlocks_for(clean)
    except Exception:
        log.warning("website api skipped a duel signature")
        unlocks = []
    if not unlocks:
        key = _as_dict(_as_dict(duels).get("signature")).get(str(user_id))
        if isinstance(key, str) and key in libs.SIGNATURE_TITLES:
            return libs.SIGNATURE_TITLES[key]
        return None
    best = max(unlocks, key=lambda row: (row[1], clean.get(row[0], 0), row[0]))
    title = best[2]
    return title if isinstance(title, str) else None


def _rival_id(duels: dict | None, user_id: int) -> str | None:
    libs = _catalogs()
    me = str(user_id)
    best_count = -1
    best_id = None
    for key, count in _as_dict(_as_dict(duels).get("pairs")).items():
        if not isinstance(key, str) or "-" not in key:
            continue
        score = _positive(count)
        if score is None or score < libs.RIVAL_AFTER:
            continue
        left, right = key.split("-", 1)
        if me not in (left, right):
            continue
        other = right if left == me else left
        if not other.isdigit():
            continue
        other_id = int(other)
        if score > best_count or (score == best_count and (best_id is None or other_id > int(best_id))):
            best_count = score
            best_id = other
    return best_id


def _duelling(duels: dict | None, user_id: int) -> dict:
    libs = _catalogs()
    records = _as_dict(_as_dict(duels).get("records"))
    rec = _as_dict(records.get(str(user_id)))
    wins = _as_int(rec.get("w"))
    ladder = []
    for uid, row in records.items():
        if not str(uid).isdigit() or not isinstance(row, dict):
            continue
        score = _as_int(row.get("w"))
        if score > 0:
            ladder.append((int(uid), score))
    ladder.sort(key=lambda row: (-row[1], row[0]))
    rank = None
    for index, (uid, _score) in enumerate(ladder, start=1):
        if uid == user_id:
            rank = index
            break
    champion = _as_dict(_as_dict(duels).get("champion"))
    is_champion = _same_id(champion.get("user_id"), user_id)
    return {
        "title": libs.duel_rank_for(wins) if wins else "Untested",
        "wins": wins,
        "trio_wins": _as_int(rec.get("trio_w")),
        "grand_wins": _as_int(rec.get("grand_w")),
        "rank": rank,
        "duellists": len(ladder),
        "streak": max(0, _as_int(_as_dict(_as_dict(duels).get("streaks")).get(str(user_id)))),
        "bounty": str(user_id) in _as_dict(_as_dict(duels).get("bounties")),
        "champion": is_champion,
        "champion_title": libs.CHAMPION_ROLE_NAME if is_champion else None,
        "signature": _signature_title(duels, user_id),
        "rival_user_id": _rival_id(duels, user_id),
    }


def _wand_glows(duels: dict | None, user_id: int) -> bool:
    libs = _catalogs()
    rep = _as_int(_as_dict(_as_dict(duels).get("rep")).get(str(user_id)))
    return rep >= libs.REP_GLOW


def _wand_public(raw, glows: bool) -> dict | None:
    libs = _catalogs()
    wand = _as_dict(raw)
    wood = _text(wand.get("wood"), 80)
    core = _text(wand.get("core"), 80)
    if not wood and not core:
        return None
    length = None
    if wand.get("length") is not None:
        try:
            length = libs.fmt_length(float(wand["length"]))
        except Exception:
            length = None
    return {
        "wood": wood,
        "core": core,
        "length": length,
        "flexibility": _text(wand.get("flexibility"), 80),
        "reading": _text(wand.get("reading"), 600),
        "glows": glows,
    }


def _patronus_public(raw) -> dict | None:
    rec = _as_dict(raw)
    animal = _text(rec.get("animal"), 80)
    if not animal:
        return None
    label = _text(rec.get("label"), 120)
    return {
        "animal": animal,
        "headline": label or f"A silver {animal.lower()}",
        "form": _text(rec.get("form"), 600),
        "reading": _text(rec.get("reading"), 600),
    }


def _broom_public(raw) -> dict | None:
    libs = _catalogs()
    broom = _as_dict(raw)
    model = _text(broom.get("model"), 80)
    if not model:
        return None
    try:
        stats = libs.effective_stats(dict(broom))
    except Exception:
        log.warning("website api skipped broom stats")
        stats = {}
    return {
        "model": model,
        "finish": _text(broom.get("finish"), 300),
        "reading": _text(broom.get("reading"), 600),
        "speed": _as_int(_as_dict(stats).get("speed")) if stats else None,
        "altitude": _as_int(_as_dict(stats).get("altitude")) if stats else None,
    }


def _familiar_public(raw) -> dict | None:
    libs = _catalogs()
    rec = _as_dict(raw)
    species = rec.get("species")
    meta = libs.FAMILIARS.get(species)
    if not isinstance(species, str) or not meta:
        return None
    friendship = max(0, _as_int(rec.get("friendship")))
    tier_name = libs.TIERS[0][1]
    for threshold, name, _chance, _weights in libs.TIERS:
        if friendship >= threshold:
            tier_name = name
    look = rec.get("look") if isinstance(rec.get("look"), str) else None
    look_meta = libs.LOOKS.get(species, {}).get(look) if look else None
    return {
        "species": species,
        "label": meta.get("label"),
        "emoji": meta.get("emoji") or "",
        "name": _text(rec.get("name"), 24),
        "look": look,
        "look_label": _text(_as_dict(look_meta).get("label"), 80) if look_meta else None,
        "friendship": friendship,
        "tier": tier_name,
    }


def _beast_titles(collection: dict, catalog: dict) -> list[str]:
    libs = _catalogs()
    have = set(collection)
    titles = []
    for place, name in libs.BEAST_PLACES.items():
        keys = {
            key for key, beast in catalog.items()
            if isinstance(beast, dict) and beast.get("place") == place
        }
        if keys and keys <= have:
            titles.append(f"Keeper of {name.replace('The ', 'the ')}")
    legends = {
        key for key, beast in catalog.items()
        if isinstance(beast, dict) and beast.get("rarity") == "legendary"
    }
    if have & legends:
        titles.append("Legend-Tamer")
    if legends and legends <= have:
        titles.append("Mythkeeper")
    return titles


def _bestiary(beasts_state: dict | None, catalog: dict, user_id: int) -> dict:
    libs = _catalogs()
    collection = _as_dict(_as_dict(_as_dict(beasts_state).get("collections")).get(str(user_id)))
    count = len(collection)
    return {
        "befriended": count,
        "total": len(catalog),
        "rank": libs.beast_rank_for(count),
        "titles": _beast_titles(collection, catalog),
    }


def _adornments(state: dict | None, user_id: int) -> dict:
    libs = _catalogs()
    rec = _as_dict(_as_dict(_as_dict(state).get("members")).get(str(user_id)))
    owned = _as_dict(rec.get("owned"))
    worn_raw = _as_dict(rec.get("worn"))
    worn = []
    for slot in libs.SLOTS:
        key = worn_raw.get(slot)
        gear = libs.GEAR.get(key)
        if isinstance(key, str) and isinstance(gear, dict):
            worn.append({"slot": slot, "name": gear.get("name") or key})
    title = rec.get("title")
    if not isinstance(title, str) or title.strip().lower() == "none":
        title = None
    else:
        title = _text(title, 80)
    return {
        "owned": len(owned),
        "total": len(libs.GEAR),
        "title": title,
        "worn": worn,
    }


def _triwizard(state: dict | None, user_id: int) -> list[dict]:
    payload = state or {}
    rows = payload.get("champions") if isinstance(payload, dict) else None
    found = []
    for record in _as_list(rows):
        if not isinstance(record, dict) or not _same_id(record.get("id"), user_id):
            continue
        house = record.get("house")
        found.append({
            "number": _as_int(record.get("number")) if record.get("number") is not None else None,
            "name": _text(record.get("name"), 120),
            "house": _house_public(house if isinstance(house, str) else None),
        })
    return found


def _display_name(member, student: dict) -> str | None:
    if member is not None:
        name = _text(getattr(member, "display_name", None), 80)
        if name:
            return name
    return _text(student.get("name"), 80)


def _avatar_url(member) -> str | None:
    if member is None:
        return None
    avatar = getattr(member, "display_avatar", None)
    url = getattr(avatar, "url", None)
    if isinstance(url, str) and url.startswith("https://"):
        return url[:500]
    return None


def build_profile(user_id: int, member=None) -> dict:
    warnings: list[str] = []
    points = _load_state("points_store.json", warnings)
    world = _load_state("world_state.json", warnings)
    duels = _load_state("duels.json", warnings)
    wands = _load_state("wands.json", warnings)
    brooms = _load_state("brooms.json", warnings)
    patronus = _load_state("patronus.json", warnings)
    animagus = _load_state("animagus.json", warnings)
    familiars = _load_state("familiars.json", warnings)
    beasts_state = _load_state("beasts_state.json", warnings)
    adornments = _load_state("adornments_state.json", warnings)
    triwizard = _load_state("triwizard.json", warnings)
    catalog = _load(DATA_DIR / "beasts.json", warnings)
    beasts = catalog if isinstance(catalog, dict) else {}
    if catalog is not None and not isinstance(catalog, dict) and "beasts.json" not in warnings:
        warnings.append("beasts.json")

    student = _as_dict(_as_dict(_as_dict(world).get("students")).get(str(user_id)))
    owners = _as_dict(_as_dict(patronus).get("owners"))
    if not owners and isinstance(patronus, dict):
        owners = {
            uid: rec for uid, rec in patronus.items()
            if uid not in {"owners", "resets_used"} and isinstance(rec, dict)
        }
    custom = _catalogs().CUSTOM_PATRONUSES.get(str(user_id))
    patronus_raw = custom if isinstance(custom, dict) else owners.get(str(user_id))
    broom_owners = _as_dict(_as_dict(brooms).get("owners"))
    if not broom_owners and isinstance(brooms, dict):
        broom_owners = {uid: rec for uid, rec in brooms.items() if isinstance(rec, dict)}
    forms = _as_dict(_as_dict(animagus).get("forms"))
    if not forms and isinstance(animagus, dict):
        forms = {uid: rec for uid, rec in animagus.items() if isinstance(rec, dict) and rec.get("animal")}
    glows = _wand_glows(duels, user_id)
    return {
        "generated_at": int(time.time()),
        "user_id": str(user_id),
        "name": _display_name(member, student),
        "avatar_url": _avatar_url(member),
        "house": _house_for(points, user_id, member),
        "points": {
            "season": _member_points(points, user_id, "season"),
            "alltime": _member_points(points, user_id, "alltime"),
            "season_rank": _member_rank(points, user_id, "season"),
            "alltime_rank": _member_rank(points, user_id, "alltime"),
        },
        "honours": _honours(points, user_id),
        "duelling": _duelling(duels, user_id),
        "bestiary": _bestiary(beasts_state, beasts, user_id),
        "wand": _wand_public(_as_dict(wands).get(str(user_id)), glows),
        "patronus": _patronus_public(patronus_raw),
        "animagus": _animagus_public(forms.get(str(user_id))),
        "broom": _broom_public(broom_owners.get(str(user_id))),
        "familiar": _familiar_public(_as_dict(_as_dict(_as_dict(familiars).get("members")).get(str(user_id)))),
        "adornments": _adornments(adornments, user_id),
        "triwizard": _triwizard(triwizard, user_id),
        "warnings": warnings,
    }


def _animagus_public(raw) -> dict | None:
    rec = _as_dict(raw)
    animal = _text(rec.get("animal"), 80)
    if not animal:
        return None
    return {
        "animal": animal,
        "form": _text(rec.get("form"), 600),
        "reading": _text(rec.get("reading"), 600),
    }


def build_satchel(user_id: int) -> dict:
    libs = _catalogs()
    warnings: list[str] = []
    world = _load_state("world_state.json", warnings)
    potions = _load_state("potions_state.json", warnings)
    items = _load(DATA_DIR / "world" / "items.json", warnings)
    catalog = items if isinstance(items, dict) else {}
    student = _as_dict(_as_dict(_as_dict(world).get("students")).get(str(user_id)))
    carried = _as_dict(student.get("items"))
    inventory = _as_dict(
        _as_dict(_as_dict(_as_dict(potions).get("players")).get(str(user_id))).get("inventory")
    )

    potion_rows = []
    for recipe_id, recipe in libs.RECIPES.items():
        count = _positive(inventory.get(recipe_id))
        if not isinstance(recipe, dict) or count is None:
            continue
        potion_rows.append({
            "id": recipe_id,
            "name": recipe.get("name") or recipe_id,
            "emoji": recipe.get("emoji") or "",
            "tier": recipe.get("tier"),
            "count": count,
            "blurb": _text(recipe.get("blurb"), 400),
        })

    def carried_rows(ingredients: bool) -> list[dict]:
        rows = []
        for item_id, raw_count in carried.items():
            if not isinstance(item_id, str):
                continue
            count = _positive(raw_count)
            if count is None:
                continue
            is_ingredient = item_id in libs.ingredient_ids
            if is_ingredient != ingredients:
                continue
            meta = _as_dict(catalog.get(item_id))
            rows.append({
                "id": item_id,
                "name": _text(meta.get("name"), 80),
                "emoji": meta.get("emoji") or "",
                "rarity": meta.get("rarity") if isinstance(meta.get("rarity"), str) else None,
                "count": count,
                "description": _text(meta.get("desc"), 300),
            })
        order = {name: index for index, name in enumerate(libs.RARITY_ORDER)}

        def sort_key(row: dict):
            rarity = row.get("rarity")
            return (
                order.get(rarity, len(order)),
                (row.get("name") or row["id"]).lower(),
                row["id"],
            )

        rows.sort(key=sort_key)
        return rows

    return {
        "generated_at": int(time.time()),
        "user_id": str(user_id),
        "potions": potion_rows,
        "ingredients": carried_rows(True),
        "items": carried_rows(False),
        "warnings": warnings,
    }


def build_frogs(user_id: int) -> dict:
    libs = _catalogs()
    warnings: list[str] = []
    state = _load_state("frogs_state.json", warnings)
    owned = _as_dict(_as_dict(_as_dict(state).get("players")).get(str(user_id))).get("cards")
    owned = _as_dict(owned)
    kind_order = {"crest": 0, "spell": 1, "beast": 2}
    cards = []
    unique = 0
    copies = 0
    for card_id, meta in libs.FROG_CARDS.items():
        if not isinstance(meta, dict):
            continue
        count = _positive(owned.get(card_id)) or 0
        if count:
            unique += 1
            copies += count
        cards.append({
            "id": card_id,
            "name": meta.get("name") or card_id,
            "emoji": meta.get("emoji") or "",
            "rarity": meta.get("rarity"),
            "rarity_label": libs.FROG_RARITY.get(meta.get("rarity"), meta.get("rarity")),
            "kind": meta.get("kind"),
            "count": count,
            "blurb": _text(meta.get("blurb"), 300),
        })
    cards.sort(key=lambda row: (kind_order.get(row.get("kind"), 9), (row.get("name") or "").lower()))
    total = len(cards)
    return {
        "generated_at": int(time.time()),
        "user_id": str(user_id),
        "unique": unique,
        "copies": copies,
        "total": total,
        "missing": total - unique,
        "milestones": {
            "every_10_unique_points": libs.DECADE_PTS,
            "mythic_points": libs.MYTHIC_PTS,
        },
        "cards": cards,
        "warnings": warnings,
    }


def next_weekly(slots: list[dict], now: datetime) -> datetime | None:
    """Next Chicago-style weekly slot. A start more than two minutes ago waits a week."""
    by_day: dict[int, dict] = {}
    for slot in slots:
        try:
            day = int(slot["weekday"])
        except (KeyError, TypeError, ValueError):
            continue
        by_day.setdefault(day, slot)
    best = None
    for add in range(0, 8):
        day = (now + timedelta(days=add)).date()
        spec = by_day.get(day.weekday())
        if not spec:
            continue
        try:
            candidate = datetime(
                day.year, day.month, day.day,
                int(spec["hour"]), int(spec["minute"]),
                tzinfo=now.tzinfo,
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if add == 0 and candidate + timedelta(seconds=120) < now:
            continue
        if best is None or candidate < best:
            best = candidate
    return best


def _attack_slots(sched: dict) -> list[dict]:
    libs = _catalogs()
    default = _as_int(sched.get("minutes"), 10)
    if default not in libs.EVENT_PRESETS:
        default = 10
    raw = sched.get("slots") or []
    if not raw and sched.get("weekdays"):
        hour = _as_int(sched.get("hour"), 20)
        minute = _as_int(sched.get("minute"), 0)
        raw = [
            {"weekday": day, "hour": hour, "minute": minute, "minutes": default}
            for day in _as_list(sched.get("weekdays"))
        ]
    slots = []
    seen = set()
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        day = _as_int(item.get("weekday"), -1)
        hour = _as_int(item.get("hour"), -1)
        minute = _as_int(item.get("minute"), 0)
        if not (0 <= day <= 6 and 0 <= hour <= 23 and 0 <= minute <= 59):
            continue
        if day in seen:
            continue
        seen.add(day)
        minutes = _as_int(item.get("minutes"), default)
        if minutes not in libs.EVENT_PRESETS:
            minutes = default
        preset = libs.EVENT_PRESETS[minutes]
        slots.append({
            "weekday": day,
            "weekday_label": WEEKDAYS[day],
            "hour": hour,
            "minute": minute,
            "minutes": minutes,
            "label": preset.get("label"),
            "reward_top": int(preset.get("reward_top") or 0),
            "kill_pool": int(libs.preset_kill_pool(preset)),
        })
        if len(slots) >= 3:
            break
    slots.sort(key=lambda row: (row["weekday"], row["hour"], row["minute"]))
    return slots


def _night_slots(sched: dict) -> list[dict]:
    slots = []
    seen = set()
    for item in _as_list(sched.get("slots")):
        if not isinstance(item, dict):
            continue
        day = _as_int(item.get("weekday"), -1)
        hour = _as_int(item.get("hour"), -1)
        minute = _as_int(item.get("minute"), 0)
        if not (0 <= day <= 6 and 0 <= hour <= 23 and 0 <= minute <= 59):
            continue
        if day in seen:
            continue
        seen.add(day)
        slots.append({
            "weekday": day,
            "weekday_label": WEEKDAYS[day],
            "hour": hour,
            "minute": minute,
        })
        if len(slots) >= 3:
            break
    slots.sort(key=lambda row: (row["weekday"], row["hour"], row["minute"]))
    return slots


def _next_public(slots: list[dict], now: datetime) -> dict | None:
    when = next_weekly(slots, now)
    if when is None:
        return None
    return {
        "at": when.isoformat(),
        "unix": int(when.timestamp()),
        "weekday": when.weekday(),
        "weekday_label": WEEKDAYS[when.weekday()],
        "hour": when.hour,
        "minute": when.minute,
    }


def _live_attack(dementors: dict | None) -> dict:
    event = _as_dict(_as_dict(dementors).get("event"))
    if not event or event.get("practice"):
        return {"active": False}
    ends = _unix(event.get("ends_at"))
    if ends is not None and ends <= int(time.time()):
        return {"active": False}
    return {
        "active": True,
        "name": _text(event.get("name"), 80) or "Attack on Velmora",
        "started_at": _unix(event.get("started_at")),
        "ends_at": ends,
        "minutes": _as_int(event.get("minutes")) or None,
    }


def _live_night(duels: dict | None) -> dict:
    libs = _catalogs()
    night = _as_dict(duels).get("duel_night")
    night = night if isinstance(night, dict) else {}
    ends = _unix(night.get("ends_at"))
    if not night or ends is None or ends <= int(time.time()):
        return {"active": False}
    minutes = _as_int(night.get("minutes"))
    return {
        "active": True,
        "started_at": _unix(night.get("at")),
        "ends_at": ends,
        "minutes": minutes or None,
        "label": libs.DUEL_NIGHT_PRESETS.get(minutes),
    }


def build_events() -> dict:
    libs = _catalogs()
    warnings: list[str] = []
    dementors = _load_state("dementors.json", warnings)
    duels = _load_state("duels.json", warnings)
    standings = build_standings()
    for warning in standings.get("warnings") or []:
        if warning not in warnings:
            warnings.append(warning)
    now = datetime.now(_chicago())
    attack_sched = _as_dict(_as_dict(dementors).get("attack_schedule"))
    night_sched = _as_dict(_as_dict(duels).get("duel_night_schedule"))
    attack_slots = _attack_slots(attack_sched)
    night_slots = _night_slots(night_sched)
    attack_on = bool(attack_sched.get("enabled")) and bool(attack_slots)
    night_minutes = _as_int(night_sched.get("minutes"), 60)
    if night_minutes not in libs.DUEL_NIGHT_PRESETS:
        night_minutes = 60
    night_on = bool(night_sched.get("enabled")) and bool(night_slots)
    return {
        "generated_at": int(time.time()),
        "timezone": "America/Chicago",
        "attack": {
            "enabled": bool(attack_sched.get("enabled")),
            "warn_minutes": libs.EVENT_WARN_MINUTES,
            "slots": attack_slots,
            "next": _next_public(attack_slots, now) if attack_on else None,
        },
        "duel_night": {
            "enabled": bool(night_sched.get("enabled")),
            "minutes": night_minutes if night_sched else None,
            "label": libs.DUEL_NIGHT_PRESETS.get(night_minutes) if night_sched else None,
            "warn_minutes": libs.DUEL_NIGHT_WARN_MINUTES,
            "slots": night_slots,
            "next": _next_public(night_slots, now) if night_on else None,
        },
        "house_cup": {
            "season": standings["season"],
            "houses": standings["houses"],
            "leader": standings["leader"],
            "last_cup": standings["last_cup"],
        },
        "live": {
            "attack": _live_attack(dementors),
            "duel_night": _live_night(duels),
        },
        "warnings": warnings,
    }


def _user_id(request: web.Request) -> int | None:
    raw = request.match_info.get("user_id", "")
    if not USER_ID_RE.match(raw):
        return None
    try:
        user_id = int(raw)
    except ValueError:
        return None
    if user_id < 1:
        return None
    return user_id


def _gate(request: web.Request) -> web.Response | None:
    """None when the caller may read. 404 hides the API when the key is unset."""
    if not _api_key():
        raise web.HTTPNotFound()
    if not _authorized(request):
        return _json({"error": "Unauthorized."}, status=401)
    if request.method != "GET":
        return _json({"error": "Method not allowed."}, status=405)
    return None


async def _dispatch(request: web.Request, cache_key: str, builder) -> web.Response:
    denial = _gate(request)
    if denial is not None:
        return denial
    if _payloads is None:
        return _json({"error": "Couldn't read that right now."}, status=500)
    cached = _payloads.get(cache_key)
    if cached is not None:
        return _json(cached)
    try:
        payload = await asyncio.to_thread(builder)
    except Exception:
        log.exception("website api failed for %s", request.path)
        return _json({"error": "Couldn't read that right now."}, status=500)
    if not isinstance(payload, dict):
        return _json({"error": "Couldn't read that right now."}, status=500)
    payload = _scrub(payload)
    _payloads.put(cache_key, payload)
    return _json(payload)


async def standings(request: web.Request) -> web.Response:
    return await _dispatch(request, "standings", build_standings)


async def events(request: web.Request) -> web.Response:
    return await _dispatch(request, "events", build_events)


def _bad_user(request: web.Request) -> web.Response | None:
    denial = _gate(request)
    if denial is not None:
        return denial
    return _json({"error": "User id must be a Discord user id."}, status=400)


async def profile(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    if user_id is None:
        return _bad_user(request)
    denial = _gate(request)
    if denial is not None:
        return denial
    # Role lookup stays on the bot loop, and only after the key checks out.
    # The worker that reads JSON never touches Discord.
    member = _cached_member(request.app.get("bot"), user_id)
    return await _dispatch(
        request, f"profile:{user_id}", lambda: build_profile(user_id, member),
    )


async def satchel(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    if user_id is None:
        return _bad_user(request)
    return await _dispatch(request, f"satchel:{user_id}", lambda: build_satchel(user_id))


async def frogs(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    if user_id is None:
        return _bad_user(request)
    return await _dispatch(request, f"frogs:{user_id}", lambda: build_frogs(user_id))


def mount_readonly_api(app: web.Application) -> None:
    """Attach the website routes. Safe to call once, when the table app is built."""
    global _files, _payloads
    _catalogs()
    if _files is None:
        _files = FileCache()
    if _payloads is None:
        _payloads = PayloadCache()
    app.router.add_route("*", "/api/site/standings", standings)
    app.router.add_route("*", "/api/site/events", events)
    app.router.add_route("*", "/api/site/users/{user_id}/profile", profile)
    app.router.add_route("*", "/api/site/users/{user_id}/satchel", satchel)
    app.router.add_route("*", "/api/site/users/{user_id}/frogs", frogs)
