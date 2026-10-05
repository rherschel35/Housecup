"""
Castles & Army PvP — six castles, Descent armies, Wed/Sat sieges.

    /castles       - map board: owners, perks, lock timers, siege/reinforce/abandon
    /army          - full roster (paged) + sacrifice 500 → ATK/DEF
                     (castles channel and every Descent channel)

Armies come from Descent wins.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.descent import DESCENT_CHANNEL_IDS
from cogs.velmora_channels import CASTLES_CHANNEL_IDS, channel_mentions

log = logging.getLogger("velmora.castles")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "castles_state.json"
ART_DIR = Path(__file__).resolve().parent.parent / "castle_art_assets"

try:
    from zoneinfo import ZoneInfo
    CHICAGO = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    CHICAGO = dt.timezone.utc

GOLD = 0xC9A227
SIEGE_COLOR = 0x8B2E2E
STONE = 0x5C6B7A

# Deploy caps (LOCKED)
DEFEND_REGULAR_CAP = 350
DEFEND_BOSS_CAP = 1
DEFEND_TYPE_CAP = 5
ATTACK_REGULAR_CAP = 200
ATTACK_BOSS_CAP = 2

ASSAULT_SECONDS = 10 * 60
DECISION_SECONDS = 30
LOCK_SECONDS = 20 * 60

SACRIFICE_COUNT = 500
# Per-monster sacrifice boost (percent points added to army ATK or DEF).
# Example: floor 10–39 → +0.00002% each.
SACRIFICE_PCT_BANDS = (
    (10, 39, 0.00002),
    (40, 69, 0.00004),
    (70, 90, 0.0001),
    (91, 100, 0.00014),
)
LEGACY_STEP_PCT = 5.0  # old flat ladder was +5% per 500-sacrifice step
WALL_DEF_MULT = 1.55    # matching wall-type troops
BOSS_WALL_DEF_MULT = 1.75  # Bannerhall boss wall

DAILY_RECRUIT_CAP = 200
ARMY_CAP = 5000  # total monsters (home + garrison + march) — only while PvP is live
ARMY_PAGE_SIZE = 12  # units listed per /army page
SUNDAY_CASTLE_POINTS = 50
BANNERHALL_PER_CASTLE = 30

SIEGE_WEEKDAYS = {2, 5}  # Wed=2, Sat=5 in datetime

ELEMENTS = ("poison", "fire", "ice", "lightning", "light")
ELEMENT_EMOJI = {
    "poison": "☠️",
    "fire": "🔥",
    "ice": "❄️",
    "lightning": "⚡",
    "light": "✨",
}


CASTLES: dict[str, dict] = {
    "deadlock_keep": {
        "key": "deadlock_keep",
        "name": "Deadlock Keep",
        "title": "Warden of Deadlock Keep",
        "perk": "Win all duel ties",
        "wall": "poison",
        "wall_label": "Poison",
        "art": "Deadlock_Keep.jpg",
        "color": 0x3D5C3A,
    },
    "fifth_muster": {
        "key": "fifth_muster",
        "name": "The Fifth Muster",
        "title": "Marshal of the Fifth Muster",
        "perk": "Every 5 Descent beats → +1 army monster",
        "wall": "fire",
        "wall_label": "Fire",
        "art": "Fifth_Muster.jpg",
        "color": 0xB8432E,
    },
    "surestroke_tower": {
        "key": "surestroke_tower",
        "name": "Surestroke Tower",
        "title": "Keeper of Surestroke Tower",
        "perk": "Wild Threat `/cast` auto-picks",
        "wall": "ice",
        "wall_label": "Ice",
        "art": "Surestroke_Tower.jpg",
        "color": 0x4A7B9E,
    },
    "triple_tithe": {
        "key": "triple_tithe",
        "name": "The Triple Tithe",
        "title": "Collector of the Triple Tithe",
        "perk": "+3 house pts on duel wins",
        "wall": "lightning",
        "wall_label": "Lightning",
        "art": "Triple_Tithe.jpg",
        "color": 0xC9A227,
    },
    "prelude_bastion": {
        "key": "prelude_bastion",
        "name": "Prelude Bastion",
        "title": "Guardian of Prelude Bastion",
        "perk": "Descent monsters start at half attack",
        "wall": "light",
        "wall_label": "Light",
        "art": "Prelude_Bastion.jpg",
        "color": 0xE8D5A3,
    },
    "bannerhall": {
        "key": "bannerhall",
        "name": "Bannerhall",
        "title": "Lord of Bannerhall",
        "perk": "House weekly +30 × N castles that house holds",
        "wall": "boss",
        "wall_label": "Boss",
        "art": "Bannerhall.jpg",
        "color": 0x6B3FA0,
    },
}

CASTLE_ORDER = list(CASTLES.keys())

TITLE_SCORES = {
    "Warden of Deadlock Keep": 76,
    "Marshal of the Fifth Muster": 76,
    "Keeper of Surestroke Tower": 76,
    "Collector of the Triple Tithe": 76,
    "Guardian of Prelude Bastion": 76,
    "Lord of Bannerhall": 78,
}


def _chicago_now(ts: Optional[float] = None) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts if ts is not None else time.time(), CHICAGO)


def chicago_day(ts: Optional[float] = None) -> str:
    return _chicago_now(ts).strftime("%Y-%m-%d")


def is_siege_day(ts: Optional[float] = None) -> bool:
    return _chicago_now(ts).weekday() in SIEGE_WEEKDAYS


def _read(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError):
        log.exception("Castles state unreadable — starting fresh.")
        return default


def blank_castle() -> dict:
    return {
        "owner_id": None,
        "house": None,
        "garrison": [],
        "reinforced": False,
        "locked_until": 0.0,
        "siege": None,
    }


def blank_player() -> dict:
    return {
        "army_atk_pct": 0.0,   # cumulative % points from sacrifices
        "army_def_pct": 0.0,
        "army_atk_steps": 0,   # legacy; migrated into army_*_pct
        "army_def_steps": 0,
        "recruit_day": "",
        "recruits_today": 0,
        "muster_beats": 0,
    }


def sacrifice_pct_for_unit(unit: dict) -> float:
    """Percent-point boost one sacrificed regular grants (0 if below floor 10)."""
    fl = int(unit.get("floor") or 0)
    for lo, hi, pct in SACRIFICE_PCT_BANDS:
        if lo <= fl <= hi:
            return pct
    return 0.0


def format_bonus_pct(pct: float) -> str:
    if abs(pct) < 1e-15:
        return "+0%"
    # Enough decimals for the tiny per-monster rates; trim trailing zeros.
    text = f"{pct:.8f}".rstrip("0").rstrip(".")
    if not text.startswith("-"):
        text = "+" + text
    return text + "%"


def unit_power(unit: dict, *, side: str, wall: str, atk_pct: float, def_pct: float) -> float:
    hp = max(1, int(unit.get("hp") or 1))
    atk = max(1, int(unit.get("atk") or 1))
    deff = max(1, int(unit.get("def") or 1))
    base = hp * 0.12 + (atk if side == "attack" else deff)
    if side == "attack":
        return base * (1.0 + float(atk_pct) / 100.0)
    mult = 1.0 + float(def_pct) / 100.0
    el = (unit.get("element") or "").lower()
    if wall == "boss" and unit.get("boss"):
        mult *= BOSS_WALL_DEF_MULT
    elif wall != "boss" and el == wall:
        mult *= WALL_DEF_MULT
    return base * mult


def force_floor_summary(units: list[dict]) -> str:
    """Descent floor range for a force — shown on battle reports."""
    floors = [int(u.get("floor") or 0) for u in units if int(u.get("floor") or 0) > 0]
    if not floors:
        return "— (no floor data)"
    lo, hi = min(floors), max(floors)
    avg = sum(floors) / len(floors)
    if lo == hi:
        return f"floor **{lo}** ({len(floors)} troops)"
    return f"floors **{lo}–{hi}** (avg {avg:.0f}, {len(floors)} troops)"


def _kill_by_damage(
    units: list[dict],
    damage: float,
    *,
    side: str,
    wall: str,
    atk_pct: float,
    def_pct: float,
) -> tuple[list[dict], list[dict]]:
    """Spend damage on weakest units first (soak = that unit's combat power)."""
    if not units or damage <= 0:
        return [], [dict(u) for u in units]

    copies = [dict(u) for u in units]
    order = sorted(
        range(len(copies)),
        key=lambda i: unit_power(
            copies[i], side=side, wall=wall, atk_pct=atk_pct, def_pct=def_pct
        ),
    )
    killed_idx: set[int] = set()
    rem = float(damage)
    for i in order:
        soak = max(
            1.0,
            unit_power(copies[i], side=side, wall=wall, atk_pct=atk_pct, def_pct=def_pct),
        )
        if rem + 1e-9 >= soak:
            rem -= soak
            killed_idx.add(i)
        else:
            break
    killed = [copies[i] for i in range(len(copies)) if i in killed_idx]
    remaining = [copies[i] for i in range(len(copies)) if i not in killed_idx]
    return killed, remaining


def sim_clash(
    attackers: list[dict],
    defenders: list[dict],
    *,
    wall: str,
    atk_pct: float,
    def_pct: float,
    rng: random.Random,
) -> dict:
    """One clash: each side deals damage = power; kills scale with that, not headcount %.

    Old formula applied a ~30%+ loss fraction to defender *count*, so a 1-troop
    poke could wipe a third of a huge garrison. Damage soak uses unit power.
    """
    att = [dict(u) for u in attackers]
    deff = [dict(u) for u in defenders]
    if not deff:
        return {
            "att_deployed": len(att),
            "def_deployed": 0,
            "att_killed": [],
            "def_killed": [],
            "att_remaining": att,
            "def_remaining": [],
            "cleared": True,
            "att_power": 0,
            "def_power": 0,
            "att_floors": force_floor_summary(att),
            "def_floors": force_floor_summary([]),
        }

    att_power = sum(
        unit_power(u, side="attack", wall=wall, atk_pct=atk_pct, def_pct=def_pct) for u in att
    )
    def_power = sum(
        unit_power(u, side="defend", wall=wall, atk_pct=atk_pct, def_pct=def_pct) for u in deff
    )
    # Light noise so identical armies aren't deterministic forever.
    att_power *= rng.uniform(0.92, 1.08)
    def_power *= rng.uniform(0.92, 1.08)

    # Attackers deal att_power into the garrison; defenders deal def_power into the march.
    def_killed, def_rem = _kill_by_damage(
        deff, att_power, side="defend", wall=wall, atk_pct=atk_pct, def_pct=def_pct
    )
    att_killed, att_rem = _kill_by_damage(
        att, def_power, side="attack", wall=wall, atk_pct=atk_pct, def_pct=def_pct
    )
    cleared = len(def_rem) == 0
    return {
        "att_deployed": len(att),
        "def_deployed": len(deff),
        "att_killed": att_killed,
        "def_killed": def_killed,
        "att_remaining": att_rem,
        "def_remaining": def_rem,
        "cleared": cleared,
        "att_power": int(att_power),
        "def_power": int(def_power),
        "att_floors": force_floor_summary(att),
        "def_floors": force_floor_summary(deff),
    }


def pick_force(army: list[dict], *, regular_cap: int, boss_cap: int, type_cap: Optional[int]) -> list[dict]:
    """Auto-select a deployable force from an army ledger (highest HP first)."""
    regulars = [u for u in army if not u.get("boss")]
    bosses = [u for u in army if u.get("boss")]
    regulars.sort(key=lambda u: int(u.get("hp") or 0), reverse=True)
    bosses.sort(key=lambda u: int(u.get("hp") or 0), reverse=True)

    chosen: list[dict] = []
    types: set[str] = set()

    for u in bosses[:boss_cap]:
        chosen.append(u)

    for u in regulars:
        if len([c for c in chosen if not c.get("boss")]) >= regular_cap:
            break
        el = (u.get("element") or "unknown").lower()
        if type_cap is not None and el not in types and len(types) >= type_cap:
            continue
        types.add(el)
        chosen.append(u)
    return chosen


def force_summary(units: list[dict]) -> str:
    if not units:
        return "empty"
    bosses = sum(1 for u in units if u.get("boss"))
    regs = len(units) - bosses
    by_el: dict[str, int] = {}
    for u in units:
        el = (u.get("element") or "?").lower()
        by_el[el] = by_el.get(el, 0) + 1
    mix = ", ".join(
        f"{ELEMENT_EMOJI.get(el, '•')}{n}" for el, n in sorted(by_el.items(), key=lambda x: -x[1])
    )
    return f"{regs} troops" + (f" + {bosses} boss" if bosses else "") + (f" ({mix})" if mix else "")


class Castles(commands.Cog):
    def __init__(self, bot: commands.Bot, rng: Optional[random.Random] = None):
        self.bot = bot
        self.rng = rng or random.Random()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = _read(STATE_PATH, {})
        self.state.setdefault("castles", {})
        self.state.setdefault("players", {})
        # Daily 200-bind cap only while PvP season is live. Pre-season: uncapped.
        self.state.setdefault("pvp_live", False)
        self.state.setdefault("pvp_live_staff_set", False)
        self.state.setdefault("siege_unlocked", False)  # staff: skip Wed/Sat + clear locks
        self.state.setdefault("sunday_paid", "")
        self.state.setdefault("bannerhall_week", "")
        # Existing deploys defaulted pvp_live True before season start — reopen binds
        # until staff explicitly flips the season on.
        if not self.state.get("pvp_live_staff_set"):
            self.state["pvp_live"] = False
        for key in CASTLE_ORDER:
            slot = self.state["castles"].setdefault(key, blank_castle())
            for k, v in blank_castle().items():
                slot.setdefault(k, v)
        self._siege_tasks: dict[str, asyncio.Task] = {}
        self.save()

    async def cog_load(self):
        self.sunday_loop.start()
        self.siege_watch.start()

    async def cog_unload(self):
        self.sunday_loop.cancel()
        self.siege_watch.cancel()
        for task in list(self._siege_tasks.values()):
            task.cancel()

    # ================================================================ storage

    def save(self) -> None:
        try:
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save castles state.")

    def prec(self, user_id: int) -> dict:
        r = self.state["players"].setdefault(str(user_id), blank_player())
        for k, v in blank_player().items():
            r.setdefault(k, v)
        # One-time migrate old flat +5%/step ladder into cumulative % points.
        if not r.get("_pct_migrated"):
            if float(r.get("army_atk_pct") or 0) == 0 and int(r.get("army_atk_steps") or 0):
                r["army_atk_pct"] = int(r["army_atk_steps"]) * LEGACY_STEP_PCT
            if float(r.get("army_def_pct") or 0) == 0 and int(r.get("army_def_steps") or 0):
                r["army_def_pct"] = int(r["army_def_steps"]) * LEGACY_STEP_PCT
            r["_pct_migrated"] = True
        return r

    def army_atk_pct(self, user_id: int) -> float:
        return float(self.prec(user_id).get("army_atk_pct") or 0)

    def army_def_pct(self, user_id: int) -> float:
        return float(self.prec(user_id).get("army_def_pct") or 0)

    def castle(self, key: str) -> dict:
        return self.state["castles"][key]

    def owner_castle_key(self, user_id: int) -> Optional[str]:
        for key, slot in self.state["castles"].items():
            if slot.get("owner_id") == user_id:
                return key
        return None

    def owns_castle(self, user_id: int, key: str) -> bool:
        return self.castle(key).get("owner_id") == user_id

    def titles_of(self, user_id: int) -> list[str]:
        key = self.owner_castle_key(user_id)
        if not key:
            return []
        return [CASTLES[key]["title"]]

    # ================================================================ channel

    def _in_channel(self, interaction: discord.Interaction) -> bool:
        return interaction.channel_id in CASTLES_CHANNEL_IDS

    def _army_channel_ok(self, interaction: discord.Interaction) -> bool:
        """ /army is allowed in the castles channel and all Descent rooms. """
        cid = interaction.channel_id
        return cid in CASTLES_CHANNEL_IDS or cid in DESCENT_CHANNEL_IDS

    async def _deny_channel(self, interaction: discord.Interaction) -> bool:
        if self._in_channel(interaction):
            return False
        msg = f"Castle commands only work in {channel_mentions(CASTLES_CHANNEL_IDS)}."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return True

    async def _deny_army_channel(self, interaction: discord.Interaction) -> bool:
        if self._army_channel_ok(interaction):
            return False
        allowed = CASTLES_CHANNEL_IDS | DESCENT_CHANNEL_IDS
        msg = f"/army only works in {channel_mentions(allowed)}."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return True

    # ================================================================ army access

    def _descent(self):
        return self.bot.get_cog("Descent")

    def army_of(self, user_id: int) -> list[dict]:
        descent = self._descent()
        if not descent:
            return []
        return list(descent.record(user_id).get("army") or [])

    def army_owned_count(self, user_id: int) -> int:
        """Total monsters you hold — home roster + garrison + active march."""
        n = len(self.army_of(user_id))
        key = self.owner_castle_key(user_id)
        if key:
            n += len(self.castle(key).get("garrison") or [])
        for slot in self.state.get("castles", {}).values():
            siege = slot.get("siege") or {}
            if siege.get("attacker_id") == user_id:
                n += len(siege.get("attack_force") or [])
        return n

    def _save_descent(self) -> None:
        descent = self._descent()
        if descent:
            descent.save()

    def remove_units_from_army(self, user_id: int, units: list[dict]) -> None:
        descent = self._descent()
        if not descent or not units:
            return
        ids = {int(u.get("id") or 0) for u in units}
        rec = descent.record(user_id)
        rec["army"] = [u for u in (rec.get("army") or []) if int(u.get("id") or 0) not in ids]
        self._save_descent()

    def return_units_to_army(self, user_id: int, units: list[dict]) -> None:
        descent = self._descent()
        if not descent or not units:
            return
        rec = descent.record(user_id)
        army = rec.setdefault("army", [])
        have = {int(u.get("id") or 0) for u in army}
        for u in units:
            uid = int(u.get("id") or 0)
            if uid and uid not in have:
                army.append(u)
                have.add(uid)
        self._save_descent()

    # ================================================================ recruit cap / musters

    def is_pvp_live(self) -> bool:
        return bool(self.state.get("pvp_live"))

    def recruit_allowed(self, user_id: int, n: int = 1) -> int:
        """How many of n recruits may join (0 if size or daily capped).

        Pre-season (`pvp_live` False): no size cap and no daily cap — every
        Descent win binds. Once PvP is live: ARMY_CAP total + 200/day.
        """
        if not self.is_pvp_live():
            return n
        room = max(0, ARMY_CAP - self.army_owned_count(user_id))
        if room <= 0:
            return 0
        prec = self.prec(user_id)
        day = chicago_day()
        if prec.get("recruit_day") != day:
            prec["recruit_day"] = day
            prec["recruits_today"] = 0
        left = max(0, DAILY_RECRUIT_CAP - int(prec.get("recruits_today") or 0))
        return min(n, left, room)

    def note_recruits(self, user_id: int, n: int) -> None:
        if n <= 0 or not self.is_pvp_live():
            return
        prec = self.prec(user_id)
        day = chicago_day()
        if prec.get("recruit_day") != day:
            prec["recruit_day"] = day
            prec["recruits_today"] = 0
        prec["recruits_today"] = int(prec.get("recruits_today") or 0) + n
        self.save()

    def fifth_muster_bonus(self, user_id: int) -> int:
        """Call after a Descent win. Returns how many extra units to clone (0 or 1)."""
        if not self.owns_castle(user_id, "fifth_muster"):
            return 0
        prec = self.prec(user_id)
        prec["muster_beats"] = int(prec.get("muster_beats") or 0) + 1
        self.save()
        if prec["muster_beats"] % 5 == 0:
            return 1
        return 0

    def prelude_halves_monster_atk(self, user_id: int) -> bool:
        return self.owns_castle(user_id, "prelude_bastion")

    def surestroke_auto_cast(self, user_id: int) -> bool:
        return self.owns_castle(user_id, "surestroke_tower")

    def deadlock_wins_ties(self, user_id: int) -> bool:
        return self.owns_castle(user_id, "deadlock_keep")

    def triple_tithe_bonus(self, user_id: int) -> int:
        return 3 if self.owns_castle(user_id, "triple_tithe") else 0

    # ================================================================ board text

    def lock_label(self, slot: dict, now: Optional[float] = None) -> str:
        now = now if now is not None else time.time()
        if slot.get("siege"):
            left = max(0, int(float(slot["siege"].get("ends_at") or 0) - now))
            return f"⚔️ Under assault — {left // 60}m {left % 60}s left"
        until = float(slot.get("locked_until") or 0)
        if until > now:
            left = int(until - now)
            return f"🔒 Locked {left // 60}m {left % 60}s"
        return "Open"

    def sieges_open(self, ts: Optional[float] = None) -> bool:
        """Wed/Sat normally; staff unlock lets sieges run any day."""
        if self.state.get("siege_unlocked"):
            return True
        return is_siege_day(ts)

    def overview_embed(self) -> discord.Embed:
        now = time.time()
        siege_open = self.sieges_open(now)
        lines = []
        for key in CASTLE_ORDER:
            meta = CASTLES[key]
            slot = self.castle(key)
            owner = slot.get("owner_id")
            owner_txt = f"<@{owner}>" if owner else "*unclaimed*"
            house = slot.get("house")
            house_bit = f" · {house}" if house else ""
            garr = force_summary(slot.get("garrison") or [])
            lines.append(
                f"**{meta['name']}** — {owner_txt}{house_bit}\n"
                f"└ {meta['perk']} · wall {meta['wall_label']} · garrison {garr} · {self.lock_label(slot, now)}"
            )
        if self.state.get("siege_unlocked"):
            day = "⚡ Staff unlock — sieges open any day"
        elif siege_open:
            day = "Siege day (Wed/Sat)"
        else:
            day = "No sieges today (Wed & Sat only)"
        embed = discord.Embed(
            title="🏰 Velmora Castles",
            description=f"{day}\n\n" + "\n\n".join(lines),
            color=GOLD,
        )
        embed.set_footer(text="One castle per player · Abandon before sieging another · /army for your ranks")
        return embed

    def castle_embed(self, key: str) -> discord.Embed:
        meta = CASTLES[key]
        slot = self.castle(key)
        owner = slot.get("owner_id")
        desc = [
            f"**Perk:** {meta['perk']}",
            f"**Wall:** {meta['wall_label']}",
            f"**Title:** {meta['title']}",
            f"**Owner:** {f'<@{owner}>' if owner else '*unclaimed*'}",
            f"**Garrison:** {force_summary(slot.get('garrison') or [])}",
            f"**Status:** {self.lock_label(slot)}",
            "Reinforce anytime except during a live assault (20m lock only blocks new attacks).",
        ]
        embed = discord.Embed(title=meta["name"], description="\n".join(desc), color=meta["color"])
        return embed

    def art_file(self, key: str) -> Optional[discord.File]:
        name = CASTLES[key]["art"]
        path = ART_DIR / name
        if path.is_file():
            return discord.File(path, filename=name)
        return None

    # ================================================================ ownership

    def assign_castle(self, key: str, member: discord.Member, house: Optional[str]) -> None:
        # Clear any prior ownership for this player.
        prior = self.owner_castle_key(member.id)
        if prior and prior != key:
            self._clear_owner(prior, return_garrison=True)
        slot = self.castle(key)
        # Previous owner loses it (their garrison dies with the walls / stays lost).
        if slot.get("owner_id") and slot["owner_id"] != member.id:
            # Surviving garrison already applied; leftover garrison wiped on capture.
            slot["garrison"] = []
        slot["owner_id"] = member.id
        slot["house"] = house
        slot["reinforced"] = False
        slot["siege"] = None
        slot["locked_until"] = 0.0
        self.save()

    def _clear_owner(self, key: str, *, return_garrison: bool) -> None:
        slot = self.castle(key)
        owner = slot.get("owner_id")
        garr = list(slot.get("garrison") or [])
        if return_garrison and owner and garr:
            self.return_units_to_army(owner, garr)
        slot["owner_id"] = None
        slot["house"] = None
        slot["garrison"] = []
        slot["reinforced"] = False
        slot["siege"] = None
        self.save()

    def abandon(self, user_id: int) -> Optional[str]:
        key = self.owner_castle_key(user_id)
        if not key:
            return None
        slot = self.castle(key)
        if slot.get("siege"):
            return "under_siege"
        self._clear_owner(key, return_garrison=True)
        return key

    # ================================================================ reinforce / attack

    def can_reinforce(self, user_id: int, key: str) -> tuple[bool, str]:
        """Reinforce anytime except during a live assault (lock timers don't block)."""
        slot = self.castle(key)
        if slot.get("owner_id") != user_id:
            return False, "You don't hold this castle."
        if slot.get("siege"):
            return False, "Can't reinforce during a live assault."
        return True, ""

    def do_reinforce(self, user_id: int, key: str) -> tuple[bool, str]:
        ok, why = self.can_reinforce(user_id, key)
        if not ok:
            return False, why
        slot = self.castle(key)
        army = self.army_of(user_id)
        # Units already in garrison stay; top up toward caps.
        current = list(slot.get("garrison") or [])
        current_ids = {int(u.get("id") or 0) for u in current}
        available = [u for u in army if int(u.get("id") or 0) not in current_ids]
        cur_regs = [u for u in current if not u.get("boss")]
        cur_bosses = [u for u in current if u.get("boss")]
        need_regs = max(0, DEFEND_REGULAR_CAP - len(cur_regs))
        need_bosses = max(0, DEFEND_BOSS_CAP - len(cur_bosses))
        types = {(u.get("element") or "").lower() for u in current if not u.get("boss")}
        type_room = max(0, DEFEND_TYPE_CAP - len(types))

        # Prefer filling existing types first, then new types if room.
        picked = []
        avail_bosses = [u for u in available if u.get("boss")]
        avail_regs = [u for u in available if not u.get("boss")]
        avail_bosses.sort(key=lambda u: int(u.get("hp") or 0), reverse=True)
        avail_regs.sort(key=lambda u: int(u.get("hp") or 0), reverse=True)

        for u in avail_bosses[:need_bosses]:
            picked.append(u)

        same_type = [u for u in avail_regs if (u.get("element") or "").lower() in types]
        new_type = [u for u in avail_regs if (u.get("element") or "").lower() not in types]
        for u in same_type:
            if len([p for p in picked if not p.get("boss")]) >= need_regs:
                break
            picked.append(u)
        new_types_used: set[str] = set()
        for u in new_type:
            if len([p for p in picked if not p.get("boss")]) >= need_regs:
                break
            el = (u.get("element") or "").lower()
            if el not in new_types_used:
                if len(new_types_used) >= type_room:
                    continue
                new_types_used.add(el)
            picked.append(u)

        if not picked and not current:
            return False, "Your army is empty — bind monsters in the Descent first."
        if not picked:
            if need_regs <= 0 and need_bosses <= 0:
                return False, f"Walls already at cap (**{force_summary(current)}**)."
            return False, "No more troops available to add from your army."

        self.remove_units_from_army(user_id, picked)
        slot["garrison"] = current + picked
        slot["reinforced"] = False  # legacy field; reinforce is no longer one-shot
        self.save()
        return True, f"Walls stocked: **{force_summary(slot['garrison'])}** (+{len(picked)} this reinforce)."

    def can_start_siege(self, user_id: int, key: str) -> tuple[bool, str]:
        if not self.sieges_open():
            return False, "Sieges are only on **Wednesday** and **Saturday** (Chicago time)."
        slot = self.castle(key)
        if slot.get("siege"):
            return False, "This castle is already under assault."
        now = time.time()
        if float(slot.get("locked_until") or 0) > now:
            left = int(float(slot["locked_until"]) - now)
            return False, f"Castle locked for {left // 60}m {left % 60}s after the last assault."
        if slot.get("owner_id") == user_id:
            return False, "You already hold this castle."
        owned = self.owner_castle_key(user_id)
        if owned:
            return False, f"Abandon **{CASTLES[owned]['name']}** before sieging another castle."
        army = self.army_of(user_id)
        if not army:
            return False, "You need an army from the Descent before you can siege."
        return True, ""

    def start_siege(self, attacker: discord.Member, key: str) -> tuple[bool, str, Optional[dict]]:
        ok, why = self.can_start_siege(attacker.id, key)
        if not ok:
            return False, why, None
        force = pick_force(
            self.army_of(attacker.id),
            regular_cap=ATTACK_REGULAR_CAP,
            boss_cap=ATTACK_BOSS_CAP,
            type_cap=None,
        )
        if not force:
            return False, "No deployable troops.", None
        self.remove_units_from_army(attacker.id, force)
        now = time.time()
        siege = {
            "attacker_id": attacker.id,
            "attack_force": force,
            "started_at": now,
            "ends_at": now + ASSAULT_SECONDS,
            "decision_deadline": None,
            "channel_id": None,
            "message_id": None,
            "waiting": False,
        }
        slot = self.castle(key)
        slot["siege"] = siege
        self.save()
        return True, f"Marching with **{force_summary(force)}**.", siege

    def end_siege(self, key: str, *, lock: bool, return_attackers: bool) -> None:
        slot = self.castle(key)
        siege = slot.get("siege") or {}
        attacker_id = siege.get("attacker_id")
        force = list(siege.get("attack_force") or [])
        if return_attackers and attacker_id and force:
            self.return_units_to_army(attacker_id, force)
        slot["siege"] = None
        slot["reinforced"] = False
        if lock:
            slot["locked_until"] = time.time() + LOCK_SECONDS
        self.save()
        task = self._siege_tasks.pop(key, None)
        if task:
            task.cancel()

    def apply_clash_deaths(self, key: str, report: dict) -> None:
        slot = self.castle(key)
        siege = slot.get("siege") or {}
        # Update remaining forces in place (dead already removed from ledgers by not returning).
        siege["attack_force"] = list(report.get("att_remaining") or [])
        slot["garrison"] = list(report.get("def_remaining") or [])
        # Dead units stay out of both ledgers (already pulled from army when deployed).
        self.save()

    # ================================================================ embeds / views

    def battle_report_embed(self, key: str, report: dict, attacker: discord.Member) -> discord.Embed:
        meta = CASTLES[key]
        slot = self.castle(key)
        owner_id = slot.get("owner_id")
        def_name = f"<@{owner_id}>" if owner_id else "Neutral walls"
        att_lost = len(report["att_killed"])
        def_lost = len(report["def_killed"])
        att_rem = len(report["att_remaining"])
        def_rem = len(report["def_remaining"])
        embed = discord.Embed(
            title=f"⚔️ Battle Report — {meta['name']}",
            description=f"**{attacker.display_name}** vs {def_name}",
            color=SIEGE_COLOR,
        )
        embed.add_field(
            name="Attacker",
            value=(
                f"Deployed: **{report['att_deployed']}**\n"
                f"Lost: **{att_lost}**\n"
                f"Remaining: **{att_rem}**\n"
                f"Power: {report.get('att_power', '—')}\n"
                f"Levels: {report.get('att_floors') or force_floor_summary(report.get('att_killed', []) + report.get('att_remaining', []))}"
            ),
        )
        embed.add_field(
            name="Defender",
            value=(
                f"Deployed: **{report['def_deployed']}**\n"
                f"Lost: **{def_lost}**\n"
                f"Remaining: **{def_rem}**\n"
                f"Power: {report.get('def_power', '—')}\n"
                f"Levels: {report.get('def_floors') or force_floor_summary(report.get('def_killed', []) + report.get('def_remaining', []))}"
            ),
        )
        if report.get("cleared"):
            embed.set_footer(text="Defense cleared — castle captured!")
        else:
            embed.set_footer(text=f"Attack again within {DECISION_SECONDS}s or the assault ends · Pull back anytime")
        return embed

    # ================================================================ staff

    async def staff_unlock_all(self, interaction: discord.Interaction) -> None:
        """Clear every castle lock timer and open sieges any day."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        cleared_locks = 0
        for key in CASTLE_ORDER:
            slot = self.castle(key)
            if float(slot.get("locked_until") or 0) > 0:
                cleared_locks += 1
            slot["locked_until"] = 0.0
            slot["reinforced"] = False
        self.state["siege_unlocked"] = True
        self.save()
        await interaction.response.send_message(
            f"🔓 All castles unlocked.\n"
            f"• Cleared **{cleared_locks}** lock timer(s)\n"
            f"• Sieges open **any day** until `/staff castles schedule`",
            ephemeral=True,
        )

    async def staff_restore_schedule(self, interaction: discord.Interaction) -> None:
        """Turn off staff unlock — Wed/Sat siege days again."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        was = bool(self.state.get("siege_unlocked"))
        self.state["siege_unlocked"] = False
        self.save()
        if was:
            msg = "📅 Staff unlock off — sieges back to **Wednesday & Saturday** only."
        else:
            msg = "Siege schedule was already normal (Wed & Sat only)."
        if self.sieges_open():
            msg += " (Today is a siege day.)"
        else:
            msg += " (No sieges today.)"
        await interaction.response.send_message(msg, ephemeral=True)

    async def staff_set_pvp_live(self, interaction: discord.Interaction, live: bool) -> None:
        """Turn the 200/day army bind cap on (season live) or off (pre-season)."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        self.state["pvp_live"] = bool(live)
        self.state["pvp_live_staff_set"] = True
        self.save()
        if live:
            msg = (
                f"🏰 Castle PvP marked **live** — Descent army binds capped at "
                f"**{DAILY_RECRUIT_CAP}/day**."
            )
        else:
            msg = (
                "🏰 Castle PvP marked **not live** — Descent army binds are "
                "**uncapped** (every win binds again)."
            )
        await interaction.response.send_message(msg, ephemeral=True)

    # ================================================================ commands

    @app_commands.command(name="castles", description="Castle map: owners, perks, reinforce, siege, abandon.")
    async def castles_cmd(self, interaction: discord.Interaction):
        if await self._deny_channel(interaction):
            return
        view = CastleBoardView(self)
        await interaction.response.send_message(embed=self.overview_embed(), view=view)

    @app_commands.command(
        name="army",
        description="Your full Descent army roster and sacrifice 500 → ATK/DEF.",
    )
    async def army_cmd(self, interaction: discord.Interaction):
        if await self._deny_army_channel(interaction):
            return
        await self.send_army_panel(interaction)

    async def send_army_panel(self, interaction: discord.Interaction, *, page: int = 0) -> None:
        """Ephemeral paged roster + sacrifice (castles + Descent channels)."""
        view = ArmyView(self, interaction.user.id, page=page)
        await interaction.response.send_message(
            embed=self.army_embed(interaction.user, page=view.page),
            view=view,
            ephemeral=True,
        )

    def army_pages(self, user_id: int) -> list[list[dict]]:
        army = sorted(
            self.army_of(user_id),
            key=lambda u: (
                -int(u.get("floor") or 0),
                -int(u.get("hp") or 0),
                int(u.get("id") or 0),
            ),
        )
        if not army:
            return [[]]
        return [
            army[i:i + ARMY_PAGE_SIZE]
            for i in range(0, len(army), ARMY_PAGE_SIZE)
        ]

    def army_embed(self, member: discord.Member, page: int = 0) -> discord.Embed:
        pages = self.army_pages(member.id)
        page = max(0, min(int(page), len(pages) - 1))
        army = self.army_of(member.id)
        prec = self.prec(member.id)
        day = chicago_day()
        if prec.get("recruit_day") != day:
            recruits = 0
        else:
            recruits = int(prec.get("recruits_today") or 0)
        bosses = sum(1 for u in army if u.get("boss"))
        by_el: dict[str, int] = {}
        by_floor: dict[int, int] = {}
        for u in army:
            el = (u.get("element") or "?").lower()
            by_el[el] = by_el.get(el, 0) + 1
            fl = int(u.get("floor") or 0)
            by_floor[fl] = by_floor.get(fl, 0) + 1
        mix = ", ".join(
            f"{ELEMENT_EMOJI.get(el, '•')} **{el}** ×{n}"
            for el, n in sorted(by_el.items(), key=lambda x: -x[1])
        ) or "—"
        floor_bits = [
            f"F{fl}×{by_floor[fl]}"
            for fl in sorted(by_floor, reverse=True)[:12]
        ]
        if len(by_floor) > 12:
            floor_bits.append("…")
        floors_txt = ", ".join(floor_bits) or "—"
        held = self.owner_castle_key(member.id)
        held_txt = CASTLES[held]["name"] if held else "none"
        chunk = pages[page]
        roster = "\n".join(
            f"{u.get('emoji', '•')} **{u.get('name', '?')}** · "
            f"F{u.get('floor', '?')} · HP {u.get('hp', '?')} · "
            f"ATK {u.get('atk', '?')} · DEF {u.get('def', '?')}"
            f"{' · boss' if u.get('boss') else ''}"
            for u in chunk
        ) or "*Empty — win Descent fights to bind monsters.*"
        # Discord field value max 1024; keep roster short enough.
        if len(roster) > 1000:
            roster = roster[:997] + "…"
        atk_pct = self.army_atk_pct(member.id)
        def_pct = self.army_def_pct(member.id)
        owned = self.army_owned_count(member.id)
        home = len(army)
        away = owned - home
        away_bit = f" · {away} on walls/march" if away else ""
        if self.is_pvp_live():
            size_line = f"**{owned}/{ARMY_CAP}** bound ({bosses} bosses at home{away_bit})"
            recruit_line = f"Recruit today: **{recruits}/{DAILY_RECRUIT_CAP}**\n"
        else:
            size_line = f"**{owned}** bound ({bosses} bosses at home{away_bit})"
            recruit_line = "Recruit: **uncapped** (PvP not live yet — no size or daily cap)\n"
        embed = discord.Embed(
            title=f"⚔️ {member.display_name}'s Army",
            description=(
                f"{size_line} · Castle: **{held_txt}**\n"
                f"Home roster: **{home}** · "
                + recruit_line
                + (
                    f"Sacrifice bonuses: ATK **{format_bonus_pct(atk_pct)}** · "
                    f"DEF **{format_bonus_pct(def_pct)}** "
                    f"(burn {SACRIFICE_COUNT} regulars; rate by floor)"
                )
            ),
            color=STONE,
        )
        embed.add_field(
            name="Sacrifice rates (per monster)",
            value=(
                "F10–39 → **+0.00002%** · F40–69 → **+0.00004%**\n"
                "F70–90 → **+0.0001%** · F91–100 → **+0.00014%**\n"
                "F1–9 → no boost · bosses can't be sacrificed"
            ),
            inline=False,
        )
        embed.add_field(name="Troop mix", value=mix, inline=False)
        embed.add_field(name="By floor", value=floors_txt, inline=False)
        embed.add_field(
            name=f"Full roster · page {page + 1}/{len(pages)}",
            value=roster,
            inline=False,
        )
        embed.set_footer(text=f"Sacrifice {SACRIFICE_COUNT} lowest-floor regulars → army ATK or DEF")
        return embed

    def sacrifice(self, user_id: int, track: str) -> tuple[bool, str]:
        if track not in ("atk", "def"):
            return False, "Pick ATK or DEF."
        army = self.army_of(user_id)
        fodder = [u for u in army if not u.get("boss")]
        if len(fodder) < SACRIFICE_COUNT:
            return False, f"Need **{SACRIFICE_COUNT}** regular monsters (you have {len(fodder)}). Bosses can't be sacrificed."
        # Burn lowest floors first (cheapest value), then weakest HP — keep deep troops.
        fodder.sort(key=lambda u: (int(u.get("floor") or 0), int(u.get("hp") or 0)))
        burn = fodder[:SACRIFICE_COUNT]
        gained = sum(sacrifice_pct_for_unit(u) for u in burn)
        self.remove_units_from_army(user_id, burn)
        prec = self.prec(user_id)
        key = "army_atk_pct" if track == "atk" else "army_def_pct"
        prec[key] = float(prec.get(key) or 0) + gained
        label = "ATK" if track == "atk" else "DEF"
        self.save()
        return True, (
            f"Sacrificed **{SACRIFICE_COUNT}** troops for "
            f"**{format_bonus_pct(gained)}** {label}. "
            f"Army {label} now **{format_bonus_pct(float(prec[key]))}**."
        )

    # ================================================================ siege runtime

    async def run_clash_and_report(self, interaction: discord.Interaction, key: str) -> None:
        slot = self.castle(key)
        siege = slot.get("siege")
        if not siege:
            await interaction.followup.send("No active assault.", ephemeral=True)
            return
        attacker_id = siege["attacker_id"]
        if interaction.user.id != attacker_id:
            await interaction.followup.send("Only the attacker drives the assault.", ephemeral=True)
            return
        now = time.time()
        if now >= float(siege.get("ends_at") or 0):
            self.end_siege(key, lock=True, return_attackers=True)
            await interaction.followup.send("The 10-minute assault clock ran out. Castle locked 20 minutes.")
            return

        meta = CASTLES[key]
        owner_id = slot.get("owner_id")
        report = sim_clash(
            siege.get("attack_force") or [],
            slot.get("garrison") or [],
            wall=meta["wall"],
            atk_pct=self.army_atk_pct(attacker_id),
            def_pct=self.army_def_pct(owner_id) if owner_id else 0.0,
            rng=self.rng,
        )
        self.apply_clash_deaths(key, report)

        attacker = interaction.user
        embed = self.battle_report_embed(key, report, attacker)

        if report["cleared"]:
            store = self.bot.get_cog("Store")
            house = store.member_house(attacker) if store else None
            # Remaining attackers return home; garrison already empty.
            survivors = list((slot.get("siege") or {}).get("attack_force") or [])
            self.end_siege(key, lock=False, return_attackers=False)
            if survivors:
                self.return_units_to_army(attacker.id, survivors)
            self.assign_castle(key, attacker, house)
            embed.description = (embed.description or "") + f"\n\n🏰 **{attacker.display_name}** captures **{meta['name']}**!"
            await interaction.followup.send(embed=embed)
            return

        siege = self.castle(key).get("siege") or {}
        siege["waiting"] = True
        siege["decision_deadline"] = time.time() + DECISION_SECONDS
        self.castle(key)["siege"] = siege
        self.save()

        view = SiegeDecisionView(self, key, attacker.id)
        msg = await interaction.followup.send(embed=embed, view=view)
        siege["message_id"] = msg.id
        siege["channel_id"] = interaction.channel_id
        self.save()
        self._arm_decision_timeout(key)

    def _arm_decision_timeout(self, key: str) -> None:
        old = self._siege_tasks.pop(key, None)
        if old:
            old.cancel()

        async def _wait():
            try:
                await asyncio.sleep(DECISION_SECONDS + 0.5)
                slot = self.castle(key)
                siege = slot.get("siege")
                if not siege or not siege.get("waiting"):
                    return
                deadline = float(siege.get("decision_deadline") or 0)
                if time.time() < deadline:
                    return
                self.end_siege(key, lock=True, return_attackers=True)
                ch_id = siege.get("channel_id")
                if ch_id:
                    ch = self.bot.get_channel(ch_id)
                    if ch:
                        try:
                            await ch.send(
                                f"⏱️ Assault on **{CASTLES[key]['name']}** timed out — "
                                f"no Attack again within {DECISION_SECONDS}s. Castle locked 20 minutes."
                            )
                        except discord.DiscordException:
                            pass
            except asyncio.CancelledError:
                return

        self._siege_tasks[key] = asyncio.create_task(_wait())

    async def pull_back(self, interaction: discord.Interaction, key: str) -> None:
        slot = self.castle(key)
        siege = slot.get("siege")
        if not siege or siege.get("attacker_id") != interaction.user.id:
            await interaction.response.send_message("You're not leading this assault.", ephemeral=True)
            return
        self.end_siege(key, lock=True, return_attackers=True)
        await interaction.response.send_message(
            f"🏳️ **{interaction.user.display_name}** pulls back from **{CASTLES[key]['name']}**. "
            "Castle locked 20 minutes — defender may reinforce."
        )

    async def attack_again(self, interaction: discord.Interaction, key: str) -> None:
        slot = self.castle(key)
        siege = slot.get("siege")
        if not siege or siege.get("attacker_id") != interaction.user.id:
            await interaction.response.send_message("You're not leading this assault.", ephemeral=True)
            return
        now = time.time()
        if now >= float(siege.get("ends_at") or 0):
            self.end_siege(key, lock=True, return_attackers=True)
            await interaction.response.send_message("The 10-minute assault clock ran out. Castle locked 20 minutes.")
            return
        deadline = float(siege.get("decision_deadline") or 0)
        if siege.get("waiting") and deadline and now > deadline:
            self.end_siege(key, lock=True, return_attackers=True)
            await interaction.response.send_message("Too late — decision window closed. Castle locked 20 minutes.")
            return
        siege["waiting"] = False
        siege["decision_deadline"] = None
        self.save()
        task = self._siege_tasks.pop(key, None)
        if task:
            task.cancel()
        await interaction.response.defer()
        await self.run_clash_and_report(interaction, key)

    # ================================================================ loops

    @tasks.loop(minutes=15)
    async def sunday_loop(self):
        now = _chicago_now()
        if now.weekday() != 6:
            return
        day = now.strftime("%Y-%m-%d")
        if self.state.get("sunday_paid") == day:
            return
        store = self.bot.get_cog("Store")
        if not store:
            return
        # Per-castle +50 to owner's house.
        paid_any = False
        for key in CASTLE_ORDER:
            slot = self.castle(key)
            owner = slot.get("owner_id")
            house = slot.get("house")
            if not owner or not house:
                continue
            store.record(
                house=house,
                delta=SUNDAY_CASTLE_POINTS,
                actor_id=self.bot.user.id if self.bot.user else 0,
                target_id=owner,
                reason=f"Sunday castle stipend — {CASTLES[key]['name']}",
            )
            paid_any = True

        # Bannerhall weekly: +30 × N castles that house holds (once per Chicago week).
        week = now.strftime("%Y-W%W")
        banner = self.castle("bannerhall")
        if banner.get("owner_id") and banner.get("house") and self.state.get("bannerhall_week") != week:
            house = banner["house"]
            n = sum(1 for k in CASTLE_ORDER if self.castle(k).get("house") == house)
            if n:
                store.record(
                    house=house,
                    delta=BANNERHALL_PER_CASTLE * n,
                    actor_id=self.bot.user.id if self.bot.user else 0,
                    target_id=banner["owner_id"],
                    reason=f"Bannerhall weekly — {n} castle(s) × {BANNERHALL_PER_CASTLE}",
                )
                self.state["bannerhall_week"] = week
                paid_any = True

        self.state["sunday_paid"] = day
        self.save()
        if paid_any:
            log.info("Sunday castle stipends paid for %s", day)

    @sunday_loop.before_loop
    async def _before_sunday(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=30)
    async def siege_watch(self):
        now = time.time()
        for key in CASTLE_ORDER:
            slot = self.castle(key)
            siege = slot.get("siege")
            if not siege:
                continue
            if now >= float(siege.get("ends_at") or 0):
                self.end_siege(key, lock=True, return_attackers=True)
                ch_id = siege.get("channel_id")
                if ch_id:
                    ch = self.bot.get_channel(ch_id)
                    if ch:
                        try:
                            await ch.send(
                                f"⏱️ Assault on **{CASTLES[key]['name']}** hit the 10-minute limit. "
                                "Castle locked 20 minutes."
                            )
                        except discord.DiscordException:
                            pass

    @siege_watch.before_loop
    async def _before_watch(self):
        await self.bot.wait_until_ready()


# ================================================================ UI


class CastleBoardView(discord.ui.View):
    def __init__(self, cog: Castles):
        super().__init__(timeout=300)
        self.cog = cog
        options = [
            discord.SelectOption(
                label=CASTLES[k]["name"],
                value=k,
                description=CASTLES[k]["perk"][:100],
            )
            for k in CASTLE_ORDER
        ]
        self.add_item(CastleSelect(options))


class CastleSelect(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(placeholder="Inspect a castle…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        cog: Castles = self.view.cog  # type: ignore
        key = self.values[0]
        art = cog.art_file(key)
        embed = cog.castle_embed(key)
        if art:
            embed.set_image(url=f"attachment://{CASTLES[key]['art']}")
        view = CastleActionsView(cog, key)
        kwargs = {"embed": embed, "view": view}
        if art:
            kwargs["attachments"] = [art]
        else:
            kwargs["attachments"] = []
        await interaction.response.edit_message(**kwargs)


class CastleActionsView(discord.ui.View):
    def __init__(self, cog: Castles, key: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.key = key

    @discord.ui.button(label="Back to map", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=self.cog.overview_embed(),
            attachments=[],
            view=CastleBoardView(self.cog),
        )

    @discord.ui.button(label="Reinforce", style=discord.ButtonStyle.primary)
    async def reinforce(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.channel_id not in CASTLES_CHANNEL_IDS:
            await interaction.response.send_message("Wrong channel.", ephemeral=True)
            return
        ok, msg = self.cog.do_reinforce(interaction.user.id, self.key)
        if ok:
            await interaction.response.send_message(f"🛡️ {msg}", ephemeral=True)
            # Refresh public board if still showing this castle.
            try:
                embed = self.cog.castle_embed(self.key)
                art = self.cog.art_file(self.key)
                if art:
                    embed.set_image(url=f"attachment://{CASTLES[self.key]['art']}")
                    await interaction.message.edit(embed=embed, attachments=[art], view=self)
                else:
                    await interaction.message.edit(embed=embed, view=self)
            except discord.DiscordException:
                pass
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Siege", style=discord.ButtonStyle.danger)
    async def siege(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.channel_id not in CASTLES_CHANNEL_IDS:
            await interaction.response.send_message("Wrong channel.", ephemeral=True)
            return
        ok, why = self.cog.can_start_siege(interaction.user.id, self.key)
        if not ok:
            await interaction.response.send_message(why, ephemeral=True)
            return
        view = ConfirmSiegeView(self.cog, self.key)
        await interaction.response.send_message(
            f"Siege **{CASTLES[self.key]['name']}**?\n"
            f"You'll march with up to **{ATTACK_REGULAR_CAP}** troops + **{ATTACK_BOSS_CAP}** bosses "
            f"(auto-picked from your strongest). 10-minute assault once you send.",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Abandon", style=discord.ButtonStyle.secondary)
    async def abandon(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.channel_id not in CASTLES_CHANNEL_IDS:
            await interaction.response.send_message("Wrong channel.", ephemeral=True)
            return
        if not self.cog.owns_castle(interaction.user.id, self.key):
            await interaction.response.send_message("You don't hold this castle.", ephemeral=True)
            return
        result = self.cog.abandon(interaction.user.id)
        if result == "under_siege":
            await interaction.response.send_message("Can't abandon during a live assault.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🏳️ **{interaction.user.display_name}** abandons **{CASTLES[self.key]['name']}**. "
            "Garrison returns home. Title dropped.",
        )


class ConfirmSiegeView(discord.ui.View):
    def __init__(self, cog: Castles, key: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.key = key

    @discord.ui.button(label="Send attack", style=discord.ButtonStyle.danger)
    async def send(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, msg, siege = self.cog.start_siege(interaction.user, self.key)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await interaction.response.send_message(
            f"⚔️ **{interaction.user.display_name}** marches on **{CASTLES[self.key]['name']}** — {msg}",
        )
        # First clash immediately.
        await interaction.followup.send("First clash resolving…")
        # Need a defer-style path: create a fake followup chain
        class _Shim:
            def __init__(self, inter):
                self._inter = inter
                self.user = inter.user
                self.channel_id = inter.channel_id
                self.followup = inter.followup
                self.response = inter.response

        await self.cog.run_clash_and_report(_Shim(interaction), self.key)  # type: ignore
        for child in self.children:
            child.disabled = True
        try:
            await interaction.edit_original_response(view=self)
        except discord.DiscordException:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Siege cancelled.", view=None)


class SiegeDecisionView(discord.ui.View):
    def __init__(self, cog: Castles, key: str, attacker_id: int):
        super().__init__(timeout=DECISION_SECONDS + 5)
        self.cog = cog
        self.key = key
        self.attacker_id = attacker_id

    @discord.ui.button(label="Attack again", style=discord.ButtonStyle.danger)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.attacker_id:
            await interaction.response.send_message("Only the attacker can press this.", ephemeral=True)
            return
        await self.cog.attack_again(interaction, self.key)

    @discord.ui.button(label="Pull back", style=discord.ButtonStyle.secondary)
    async def pull(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.attacker_id:
            await interaction.response.send_message("Only the attacker can press this.", ephemeral=True)
            return
        await self.cog.pull_back(interaction, self.key)


class ArmyView(discord.ui.View):
    def __init__(self, cog: Castles, user_id: int, page: int = 0):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        pages = cog.army_pages(user_id)
        self.page = max(0, min(int(page), len(pages) - 1))
        # Disable page buttons at the ends.
        for child in self.children:
            if getattr(child, "custom_id", None) == "army:prev":
                child.disabled = self.page <= 0
            elif getattr(child, "custom_id", None) == "army:next":
                child.disabled = self.page >= len(pages) - 1

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=self.cog.army_embed(interaction.user, page=self.page),
            view=ArmyView(self.cog, self.user_id, page=self.page),
        )

    @discord.ui.button(
        label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="army:prev", row=0,
    )
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your army.", ephemeral=True)
            return
        self.page = max(0, self.page - 1)
        await self._refresh(interaction)

    @discord.ui.button(
        label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="army:next", row=0,
    )
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your army.", ephemeral=True)
            return
        pages = self.cog.army_pages(self.user_id)
        self.page = min(len(pages) - 1, self.page + 1)
        await self._refresh(interaction)

    @discord.ui.button(
        label=f"Sacrifice {SACRIFICE_COUNT} → +ATK",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def sac_atk(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your army.", ephemeral=True)
            return
        ok, msg = self.cog.sacrifice(interaction.user.id, "atk")
        if ok:
            pages = self.cog.army_pages(self.user_id)
            self.page = min(self.page, len(pages) - 1)
            await interaction.response.edit_message(
                embed=self.cog.army_embed(interaction.user, page=self.page),
                view=ArmyView(self.cog, self.user_id, page=self.page),
            )
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(
        label=f"Sacrifice {SACRIFICE_COUNT} → +DEF",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def sac_def(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your army.", ephemeral=True)
            return
        ok, msg = self.cog.sacrifice(interaction.user.id, "def")
        if ok:
            pages = self.cog.army_pages(self.user_id)
            self.page = min(self.page, len(pages) - 1)
            await interaction.response.edit_message(
                embed=self.cog.army_embed(interaction.user, page=self.page),
                view=ArmyView(self.cog, self.user_id, page=self.page),
            )
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Castles(bot))
