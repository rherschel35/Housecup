"""
The world of Velmora: places students can explore, and the one student
record they all share. Pure Python, no Discord - testable on its own.

Every place (the Garden now; the Library, Observatory, Forbidden Woods...
later) is a JSON file in data/world/ with the same shape: areas,
atmospheres, creatures, actions, objects, discoveries, outcomes, templates,
items it can give, events, secrets and item uses.

Encounters are assembled from those tagged pieces - nothing is invented.
Any piece, secret or item use can carry a "requires" block:

    "requires": {
        "time": "night",                 # or "day"
        "events": ["door"],              # a place event must be active
        "academy": ["tournament"],       # an academy-wide event (staff toggle)
        "items": ["strange_key"],        # carried by the student
        "houses": ["thornmere"],         # the student's house
        "rep_min": 5, "rep_max": -3,     # how this place feels about them
        "flags": ["owl_spoke"],          # things they've done anywhere
        "not_flags": ["opened_cabinet"]
    }

and "house_weight": {"thornmere": 4, "caldrin": 0} to make it more common,
rarer, or impossible for a house.

Anti-repetition: for each pool, each player's most recent ~2/3 of it is set
aside; if rules and history leave nothing, the one they saw longest ago is
used.
"""

import datetime as dt
import math
import random
import re
import time
import uuid

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    TZ = dt.timezone.utc

RARITY_WEIGHT = {"common": 10, "uncommon": 5, "rare": 2, "very_rare": 0.6, "legendary": 0.1}
RARITY_LABEL = {"common": "Common", "uncommon": "Uncommon", "rare": "Rare",
                "very_rare": "Very Rare", "legendary": "Legendary"}
RARITY_ORDER = ["legendary", "very_rare", "rare", "uncommon", "common"]
OFFER_REP = {"common": 1, "uncommon": 1, "rare": 2, "very_rare": 3, "legendary": 4}

HOUSE_NAMES = {"caldrin": "Caldrin", "thornmere": "Thornmere", "veyren": "Veyren",
               "vashara": "Vashara", "moonveil": "Moonveil"}

# Reputation -> how a place feels about someone. Never shown as a number.
TIERS = [(-999, "hostile"), (-7, "wary"), (-2, "neutral"), (5, "friendly"), (15, "beloved")]

HISTORY_FRACTION = 0.65
POOLS = ("areas", "atmospheres", "creatures", "actions", "objects", "discoveries", "outcomes", "templates")

POINTS_PER_DAY = 3            # most house points exploration gives one person a day
CHOICE_REP_PER_DAY = 3
OFFER_REP_PER_DAY = 4
FROG_TRADES_PER_EVENT = 3
EVENT_TICK_CHANCE = 0.05      # per 5-minute tick while a place is quiet
EVENT_COOLDOWN = 2 * 3600


# ---------------------------------------------------------------- helpers

def now_local(now=None):
    return dt.datetime.fromtimestamp(now if now is not None else time.time(), TZ)


def today(now=None) -> str:
    return now_local(now).date().isoformat()


def is_night(now=None) -> bool:
    h = now_local(now).hour
    return h >= 20 or h < 6


def tone_for(rep: int) -> str:
    tier = "hostile"
    for floor, name in TIERS:
        if rep >= floor:
            tier = name
    return tier


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def normalize(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace("'", "")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def blank_student(name: str = "") -> dict:
    return {"name": name, "items": {}, "rep": {}, "revealed": {}, "flags": [],
            "seen": {}, "day": {}, "last_seen": {}, "visits": {}, "here": None}


class Ctx:
    """Everything a 'requires' block can ask about, for one student, right now."""

    def __init__(self, *, student, place, house=None, event=None, academy=(), now=None):
        self.student = student
        self.place = place
        self.house = house
        self.event = event               # key of the active event at this place
        self.academy = set(academy or ())
        self.now = now if now is not None else time.time()
        self.night = is_night(self.now)

    @property
    def rep(self) -> int:
        return self.student.get("rep", {}).get(self.place, 0)

    @property
    def tone(self) -> str:
        return tone_for(self.rep)


def meets(piece: dict, ctx: Ctx) -> bool:
    """Does this piece's tags and 'requires' block allow it right now?"""
    t = piece.get("time", "any")
    if t != "any" and (t == "night") != ctx.night:
        return False
    need_ev = piece.get("events")
    if need_ev and ctx.event not in need_ev:
        return False
    if ctx.event and ctx.event in piece.get("not_events", []):
        return False
    hw = piece.get("house_weight")
    if hw and ctx.house and hw.get(ctx.house, 1) <= 0:
        return False
    req = piece.get("requires")
    if not req:
        return True
    if req.get("time") and (req["time"] == "night") != ctx.night:
        return False
    if req.get("events") and ctx.event not in req["events"]:
        return False
    if req.get("academy") and not (set(req["academy"]) & ctx.academy):
        return False
    items = ctx.student.get("items", {})
    if any(items.get(i, 0) < 1 for i in req.get("items", [])):
        return False
    if req.get("houses") and ctx.house not in req["houses"]:
        return False
    if "rep_min" in req and ctx.rep < req["rep_min"]:
        return False
    if "rep_max" in req and ctx.rep > req["rep_max"]:
        return False
    flags = set(ctx.student.get("flags", []))
    if any(f not in flags for f in req.get("flags", [])):
        return False
    if any(f in flags for f in req.get("not_flags", [])):
        return False
    return True


class World:
    """All the places, plus the shared rules for the student record."""

    def __init__(self, places: dict, items: dict, rng=None):
        self.places = places            # key -> Place
        self.items = items              # every item in Velmora, keyed by id
        self.rng = rng or random.Random()
        for p in places.values():
            p.world = self
            p.rng = self.rng

    # ------------------------------------------------------------ daily counters

    def day(self, student: dict, now=None) -> dict:
        d = today(now)
        if student.get("day", {}).get("date") != d:
            student["day"] = {"date": d, "points": 0}
        return student["day"]

    def count(self, student: dict, key: str, now=None) -> int:
        return self.day(student, now).get(key, 0)

    def bump(self, student: dict, key: str, now=None):
        d = self.day(student, now)
        d[key] = d.get(key, 0) + 1

    # ------------------------------------------------------------ reputation

    def adjust_rep(self, student, place, delta, kind=None, now=None) -> int:
        if delta > 0 and kind:
            d = self.day(student, now)
            limit = CHOICE_REP_PER_DAY if kind == "choice" else OFFER_REP_PER_DAY
            key = f"{place}_{kind}_rep"
            room = max(0, limit - d.get(key, 0))
            delta = min(delta, room)
            d[key] = d.get(key, 0) + delta
        reps = student.setdefault("rep", {})
        reps[place] = max(-30, min(40, reps.get(place, 0) + delta))
        return delta

    def reveal_if_new(self, student, place) -> str | None:
        tier = tone_for(student.get("rep", {}).get(place, 0))
        revealed = student.setdefault("revealed", {})
        reveals = self.places[place].c.get("reveals", {})
        if tier != revealed.get(place, "neutral") and tier in reveals:
            revealed[place] = tier
            return reveals[tier]
        if tier == "neutral":
            revealed[place] = "neutral"
        return None

    def allow_points(self, student, delta, now=None) -> int:
        if delta <= 0:
            return delta
        d = self.day(student, now)
        room = max(0, POINTS_PER_DAY - d.get("points", 0))
        delta = min(delta, room)
        d["points"] = d.get("points", 0) + delta
        return delta

    def refund_points(self, student, delta):
        if delta > 0:
            student["day"]["points"] = max(0, student["day"].get("points", 0) - delta)

    # ------------------------------------------------------------ satchel

    def give(self, student, item_id, n=1):
        if item_id in self.items:
            inv = student.setdefault("items", {})
            inv[item_id] = inv.get(item_id, 0) + n

    def take(self, student, item_id, n=1) -> bool:
        have = student.get("items", {}).get(item_id, 0)
        if have < n:
            return False
        if have == n:
            del student["items"][item_id]
        else:
            student["items"][item_id] = have - n
        return True

    def item_line(self, item_id, n=1) -> str:
        it = self.items[item_id]
        return f"{it['emoji']} **{it['name']}**" + (f" ×{n}" if n != 1 else "")

    def set_flags(self, student, flags):
        have = student.setdefault("flags", [])
        for f in flags or []:
            if f not in have:
                have.append(f)

    def reward_points(self, item_id) -> int:
        if not item_id:
            return 0
        return {"very_rare": 1, "legendary": 2}.get(self.items[item_id]["rarity"], 0)


class Place:
    def __init__(self, key: str, content: dict):
        self.key = key
        self.c = content
        self.world = None
        self.rng = random.Random()
        self.pool_sizes = {k: len(content.get(k, [])) for k in POOLS}

    @property
    def name(self) -> str:
        return self.c.get("name", self.key.title())

    @property
    def items(self):
        return self.world.items

    # ------------------------------------------------------------ picking

    def pick(self, student, pool, candidates, weight=None, ctx=None):
        if not candidates:
            return None
        seen = student.setdefault("seen", {}).setdefault(f"{self.key}:{pool}", [])
        window = max(1, math.ceil(self.pool_sizes.get(pool, len(candidates)) * HISTORY_FRACTION))
        recent = set(seen[-window:])
        fresh = [c for c in candidates if c["id"] not in recent]

        def w(c):
            base = weight(c) if weight else 1.0
            if ctx and ctx.house and c.get("house_weight"):
                base *= c["house_weight"].get(ctx.house, 1)
            return base

        if fresh:
            choice = self.rng.choices(fresh, weights=[w(c) for c in fresh], k=1)[0]
        else:
            order = {pid: i for i, pid in enumerate(seen)}
            choice = min(candidates, key=lambda c: order.get(c["id"], -1))
        if choice["id"] in seen:
            seen.remove(choice["id"])
        seen.append(choice["id"])
        del seen[:-max(self.pool_sizes.get(pool, 50), 1)]
        return choice

    def _rarity_w(self, piece, ctx):
        w = RARITY_WEIGHT.get(piece.get("rarity", "common"), 5)
        if piece.get("events") and ctx.event in piece["events"]:
            w *= 8
        if piece.get("requires"):
            w *= 3   # conditional pieces are special - let people actually see them
        return w

    def _drop_weight(self, item_id, ctx, boost_rare=False):
        r = self.items[item_id]["rarity"]
        w = RARITY_WEIGHT[r]
        if boost_rare and r in ("rare", "very_rare", "legendary"):
            w *= 2.5
        for b in self.c.get("event_drop_boost", {}).get(ctx.event or "", []):
            if b == item_id:
                w *= 4
        return w

    @staticmethod
    def _zones_ok(piece, zones) -> bool:
        pz = piece.get("zones", ["any"])
        return "any" in pz or bool(set(pz) & set(zones))

    # ------------------------------------------------------------ explore

    def explore(self, ctx: Ctx, strange_house: str | None = None) -> dict:
        s = ctx.student
        tone = ctx.tone
        c = self.c

        areas = [a for a in c["areas"] if meets(a, ctx)]
        area = self.pick(s, "areas", areas, lambda a: 8.0 if (a.get("events") or a.get("requires")) else 1.0, ctx)

        atms = [a for a in c["atmospheres"] if meets(a, ctx) and self._zones_ok(a, area["zones"])]
        atm = self.pick(s, "atmospheres", atms, lambda a: 6.0 if a.get("events") else 1.0, ctx)
        mood = atm["mood"]

        kind = self.rng.choice(["creature"] * 50 + ["discovery"] * 35 + ["quiet"] * 15)
        if ctx.event and any(ctx.event in cr.get("events", []) for cr in c["creatures"]) and self.rng.random() < 0.7:
            kind = "creature"

        creature = action = obj = disc = None
        if kind == "creature":
            cres = [cr for cr in c["creatures"] if meets(cr, ctx) and self._zones_ok(cr, area["zones"])
                    and mood in cr["moods"]]
            if cres:
                creature = self.pick(s, "creatures", cres, lambda x: self._rarity_w(x, ctx), ctx)
                acts = [a for a in c["actions"] if creature["kind"] in a["kinds"]]
                objs = [o for o in c["objects"] if meets(o, ctx) and self._zones_ok(o, area["zones"])]
                action = self.pick(s, "actions", acts)
                obj = self.pick(s, "objects", objs, lambda x: self._rarity_w(x, ctx), ctx)
                if not (action and obj):
                    creature = None
            if not creature:
                kind = "discovery"
        if kind == "discovery":
            discs = [d for d in c["discoveries"] if meets(d, ctx) and self._zones_ok(d, area["zones"])
                     and mood in d.get("moods", [mood])]
            disc = self.pick(s, "discoveries", discs, lambda x: 4.0 if x.get("requires") else 1.0, ctx)
            if not disc:
                kind = "quiet"

        if strange_house and ctx.house == strange_house and c.get("strange_house") and self.rng.random() < 0.6:
            outcome_text = self.rng.choice(c["strange_house"])
        else:
            outs = [o for o in c["outcomes"] if tone in o["tones"] and meets(o, ctx)
                    and mood in o.get("moods", [mood])]
            out = self.pick(s, "outcomes", outs, lambda o: 4.0 if o.get("events") else 1.0, ctx)
            outcome_text = out["text"] if out else ""

        tpl = self.pick(s, "templates", [t for t in c["templates"] if t["kind"] == kind])
        f = {
            "loc": area["at"], "Loc": cap(area["at"]), "atmos": atm["text"], "outcome": outcome_text,
            "creature": creature["a"] if creature else "", "Creature": cap(creature["a"]) if creature else "",
            "creature_the": creature["the"] if creature else "", "action": action["text"] if action else "",
            "object": obj["a"] if obj else "", "object_the": obj["the"] if obj else "",
            "discovery": disc["text"] if disc else "", "Discovery": cap(disc["text"]) if disc else "",
        }
        text = re.sub(r"[ \t]+", " ", tpl["text"].format(**f).strip()).replace(" \n", "\n")

        reward = None
        drops = list(area.get("drops", []))
        for piece in (creature, obj, disc):
            if piece:
                drops += piece.get("drops", [])
        drops = [d for d in drops if d in self.items]
        luck = {"beloved": 0.7, "friendly": 0.6, "neutral": 0.5, "wary": 0.4, "hostile": 0.3}[tone]
        if drops and self.rng.random() < luck:
            boost = ctx.event in c.get("rare_boost_events", []) or tone == "beloved"
            reward = self.rng.choices(drops, weights=[self._drop_weight(d, ctx, boost) for d in drops], k=1)[0]
            self.world.give(s, reward)
        if disc and disc.get("flags"):
            self.world.set_flags(s, disc["flags"])

        s.setdefault("last_seen", {})[self.key] = ctx.now
        s.setdefault("visits", {})[self.key] = s.get("visits", {}).get(self.key, 0) + 1
        s["here"] = {"place": self.key, "at": ctx.now}
        return {"text": text, "area": area["id"], "creature": creature, "object": obj, "discovery": disc,
                "reward": reward, "tone": tone, "mood": mood, "night": ctx.night,
                "plant": bool(disc and disc.get("plant"))}

    # ------------------------------------------------------------ choices after an encounter

    def resolve_choice(self, ctx: Ctx, choice: str, subject: dict) -> dict:
        """subject is the creature met, or the plant/discovery found."""
        s = ctx.student
        lines = self.c["choice_lines"]
        the = subject.get("the", "it")
        fill = lambda t: t.replace("{The}", cap(the)).replace("{the}", the)
        drops = [d for d in subject.get("drops", []) if d in self.items]
        item, rep, points = None, 0, 0
        if choice == "feed":
            if self.rng.random() < 0.55 and drops:
                text, item = fill(self.rng.choice(lines["feed_good"])), self.rng.choice(drops)
            else:
                text = fill(self.rng.choice(lines["feed_meh"]))
            rep = 1
        elif choice == "follow":
            if self.rng.random() < 0.6:
                text = fill(self.rng.choice(lines["follow_good"]))
                pool = [i for i in self.c.get("item_ids", []) if self.items[i]["rarity"] in ("uncommon", "rare")]
                item = self.rng.choice(pool) if pool else None
            else:
                text = fill(self.rng.choice(lines["follow_lost"]))
        elif choice == "steal":
            if self.rng.random() < 0.45 and drops:
                text, item = fill(self.rng.choice(lines["steal_good"])), self.rng.choice(drops)
            else:
                text = fill(self.rng.choice(lines["steal_caught"]))
                if self.rng.random() < 0.5:
                    points = -1
            rep = -2
        elif choice == "tend":
            text, rep = self.rng.choice(lines["tend"]), 1
            if self.rng.random() < 0.35 and drops:
                item = self.rng.choice(drops)
        elif choice == "uproot":
            text, rep = self.rng.choice(lines["uproot"]), -3
            if drops:
                item = self.rng.choice(drops)
            if self.rng.random() < 0.3:
                points = -1
        else:
            text, rep = fill(self.rng.choice(lines["leave"])), 1
        rep = self.world.adjust_rep(s, self.key, rep, kind="choice", now=ctx.now)
        if item:
            self.world.give(s, item)
        return {"text": text, "item": item, "rep": rep, "points": points}

    # ------------------------------------------------------------ forage

    def forage(self, ctx: Ctx) -> dict:
        s = ctx.student
        areas = [a for a in self.c["areas"] if meets(a, ctx) and a.get("drops")]
        area = self.pick(s, "areas", areas, ctx=ctx)
        empty = {"beloved": 0.08, "friendly": 0.12, "neutral": 0.18, "wary": 0.3, "hostile": 0.45}[ctx.tone]
        drops = [d for d in area.get("drops", []) if d in self.items]
        if not drops or self.rng.random() < empty:
            line = self.rng.choice(self.c["forage_empty"])
            return {"text": line.format(loc=area["at"], Loc=cap(area["at"])), "item": None}
        boost = ctx.tone == "beloved" or ctx.event in self.c.get("rare_boost_events", [])
        item = self.rng.choices(drops, weights=[self._drop_weight(d, ctx, boost) for d in drops], k=1)[0]
        self.world.give(s, item)
        it = self.items[item]
        line = self.rng.choice(self.c["forage_lines"])
        s["here"] = {"place": self.key, "at": ctx.now}
        return {"text": line.format(loc=area["at"], Loc=cap(area["at"]), item=f"{it['emoji']} **{it['name']}**"),
                "item": item}

    # ------------------------------------------------------------ offerings

    def offer(self, ctx: Ctx, item_id) -> dict | None:
        s = ctx.student
        if not self.world.take(s, item_id):
            return None
        gained = self.world.adjust_rep(s, self.key, OFFER_REP[self.items[item_id]["rarity"]], kind="offer", now=ctx.now)
        lines = self.c["offer_lines"]
        if gained <= 0:
            text = lines["full"]
        elif ctx.tone in ("beloved", "friendly"):
            text = self.rng.choice(lines["warm"])
        else:
            text = self.rng.choice(lines["cold"])
        return {"text": text, "rep": gained}

    # ------------------------------------------------------------ item uses

    def use(self, ctx: Ctx, item_id: str) -> dict | None:
        """Try an item here. Returns what happens, or None if nothing does."""
        s = ctx.student
        if s.get("items", {}).get(item_id, 0) < 1:
            return None
        for u in self.c.get("uses", []):
            if u["item"] != item_id or not meets(u, ctx):
                continue
            if u.get("once") and f"use:{u['id']}" in s.get("flags", []):
                continue
            if u.get("consume"):
                self.world.take(s, item_id)
            got = u.get("reward")
            if got:
                self.world.give(s, got)
            self.world.set_flags(s, u.get("flags", []) + ([f"use:{u['id']}"] if u.get("once") else []))
            rep = self.world.adjust_rep(s, self.key, u.get("rep", 0))
            return {"text": u["response"], "item": got, "rep": rep, "points": u.get("points", 0)}
        return None

    # ------------------------------------------------------------ secrets

    def match_secret(self, message, ctx: Ctx, world_state: dict, event: dict | None):
        text = normalize(message)
        if not text:
            return None
        s = ctx.student
        ev_id = event["id"] if event else None
        found = world_state.get("server_found", [])
        for sec in self.c.get("secrets", []):
            if not any(normalize(p) in text for p in sec["phrases"]):
                continue
            if sec.get("event") and sec["event"] != ctx.event:
                continue
            if not meets(sec, ctx):
                continue
            once = sec.get("once", "user")
            sid = f"{self.key}:{sec['id']}"
            flag = f"{sid}@{ev_id}"
            if once == "user" and sid in s.get("flags", []):
                continue
            if once == "server" and sid in found:
                continue
            if once == "event" and flag in s.get("flags", []):
                continue
            if once == "server_event" and flag in found:
                continue
            return sec
        return None

    def claim_secret(self, sec, ctx: Ctx, world_state: dict, event: dict | None) -> dict:
        s = ctx.student
        once = sec.get("once", "user")
        ev_id = event["id"] if event else None
        sid = f"{self.key}:{sec['id']}"
        flag = f"{sid}@{ev_id}"
        found = world_state.setdefault("server_found", [])
        if once in ("user", "server"):
            self.world.set_flags(s, [sid])
        if once == "server":
            found.append(sid)
        if once in ("event", "server_event"):
            self.world.set_flags(s, [flag])
        if once == "server_event":
            found.append(flag)
        self.world.set_flags(s, sec.get("flags", []))
        for i in sec.get("consumes", []):
            self.world.take(s, i)
        item = sec.get("reward")
        if sec.get("reward_pool"):
            item = self.rng.choice(sec["reward_pool"])
        if item:
            self.world.give(s, item)
        rep = self.world.adjust_rep(s, self.key, sec.get("rep", 0))
        return {"text": sec["response"], "item": item, "rep": rep, "points": sec.get("points", 0)}

    def frog_trade(self, ctx: Ctx, event: dict) -> dict:
        s = ctx.student
        key = f"frogtrade@{event['id']}"
        s.setdefault("trades", {})
        done = s["trades"].get(key, 0)
        if done >= FROG_TRADES_PER_EVENT:
            return {"text": "🐸 The golden frog shakes its head. *\"You've had your trades. Come back next time.\"*", "item": None}
        tradeable = [i for i in s.get("items", {}) if self.items[i]["rarity"] in ("common", "uncommon", "rare")]
        if not tradeable:
            return {"text": "🐸 The golden frog looks at your empty hands, then at you, and sighs. *\"Bring me something first.\"*", "item": None}
        give_up = self.rng.choice(tradeable)
        self.world.take(s, give_up)
        roll = self.rng.random()
        if roll < 0.12:
            got = "frog_gold"
        elif roll < 0.3:
            got = "lost_button"
        else:
            pool = [i for i in self.c.get("item_ids", []) if self.items[i]["rarity"] in ("uncommon", "rare") and i != give_up]
            got = self.rng.choice(pool)
        self.world.give(s, got)
        s["trades"] = {key: done + 1}   # only the current event matters
        text = (f"🐸 The golden frog takes your {self.world.item_line(give_up)}, inspects it through a tiny monocle, "
                f"and pushes something across the stall: {self.world.item_line(got)}.")
        if got == "lost_button":
            text += " It looks extremely pleased with itself."
        return {"text": text, "item": got, "gave": give_up}

    # ------------------------------------------------------------ events

    def event_def(self, key):
        return next((e for e in self.c.get("events", []) if e["key"] == key), None)

    def maybe_start_event(self, pstate: dict, now: float, whisper_candidates):
        if pstate.get("event") or now - pstate.get("last_event_end", 0) < EVENT_COOLDOWN:
            return None
        if self.rng.random() >= EVENT_TICK_CHANCE:
            return None
        pool = [e for e in self.c.get("events", []) if not (e["key"] == "whisper" and not whisper_candidates)]
        if not pool:
            return None
        e = self.rng.choices(pool, weights=[x["weight"] for x in pool], k=1)[0]
        return self.start_event(pstate, e["key"], now, whisper_candidates)

    def start_event(self, pstate, key, now, whisper_candidates=None, minutes=None, house=None):
        e = self.event_def(key)
        if not e:
            return None
        mins = minutes or self.rng.randint(*e["minutes"])
        ev = {"id": uuid.uuid4().hex[:8], "key": key, "started": now, "ends": now + mins * 60}
        text = e["start"]
        if key == "whisper":
            if not whisper_candidates:
                return None
            ev["user_id"], ev["name"] = self.rng.choice(whisper_candidates)
            text = text.format(name=ev["name"])
        if key == "strange_house":
            ev["house"] = house or self.rng.choice(list(HOUSE_NAMES))
            text = text.format(house=HOUSE_NAMES[ev["house"]])
        ev["announce"] = f"{e['emoji']} **{e['title']}**\n{text}"
        pstate["event"] = ev
        return ev

    def end_event_if_due(self, pstate, now, force=False):
        ev = pstate.get("event")
        if not ev or (now < ev["ends"] and not force):
            return None
        e = self.event_def(ev["key"])
        text = e["end"]
        if ev["key"] == "strange_house":
            text = text.format(house=HOUSE_NAMES[ev["house"]])
        pstate["event"] = None
        pstate["last_event_end"] = now
        return f"{e['emoji']} {text}"
