#!/usr/bin/env python3
"""Practice potion and practice monster award nothing.

They also leave brew tallies, Felix cooldown, ingredient satchels, the
explore checklist, the daily spawn counter, the live monster, and any
Attack tally untouched.
"""

from __future__ import annotations

import asyncio
import copy
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-practice-"))
os.environ.setdefault("DISCORD_TOKEN", "")

import discord  # noqa: E402

from cogs.dementors import (  # noqa: E402
    CREATURES,
    EVENT_WARN_MINUTES,
    LOCKED_OUT_MESSAGE,
    SPAWN_WEIGHTS,
    SPELL_ORDER,
    SPAWNS_PER_DAY,
    Dementors,
    ThreatSpellView,
)
from cogs.duels import DUEL_NIGHT_WARN_MINUTES  # noqa: E402
from cogs.potions import (  # noqa: E402
    ACTIONS,
    PRACTICE_SEQUENCE,
    BrewView,
    Potions,
)
from cogs.practice import Practice  # noqa: E402
from cogs.store import Store  # noqa: E402
from cogs.world import WorldCog  # noqa: E402


class Bot:
    def __init__(self):
        self.user = type("U", (), {"id": 1})()
        self.cogs = {}
        self.channel = Channel(111)

    def get_cog(self, name):
        return self.cogs.get(name)

    def get_channel(self, channel_id):
        if channel_id == self.channel.id:
            return self.channel
        return None

    async def fetch_channel(self, channel_id):
        found = self.get_channel(channel_id)
        if found is None:
            raise discord.NotFound(response=None, message="missing")  # type: ignore[arg-type]
        return found


class Msg:
    def __init__(self, embed, mid: int, view=None, content=None, kwargs=None):
        self.id = mid
        self.embeds = [embed] if embed is not None else []
        self.view = view
        self.content = content
        self.kwargs = kwargs or {}

    async def edit(self, **kwargs):
        if "embed" in kwargs:
            self.embeds = [kwargs["embed"]]
        if "view" in kwargs:
            self.view = kwargs["view"]


_NEXT_ID = 100


def _next_id() -> int:
    global _NEXT_ID
    _NEXT_ID += 1
    return _NEXT_ID


class Channel:
    def __init__(self, cid: int):
        self.id = cid
        self.sent: list[Msg] = []

    async def send(self, *args, **kwargs):
        embed = kwargs.get("embed")
        if embed is None and args and isinstance(args[0], discord.Embed):
            embed = args[0]
        content = kwargs.get("content")
        if content is None and args and isinstance(args[0], str):
            content = args[0]
        msg = Msg(embed, _next_id(), kwargs.get("view"), content, kwargs)
        self.sent.append(msg)
        return msg

    async def fetch_message(self, message_id):
        for msg in self.sent:
            if msg.id == message_id:
                return msg
        raise discord.NotFound(response=None, message="missing")  # type: ignore[arg-type]


class Response:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})

    async def edit_message(self, **kwargs):
        self.sent.append(kwargs)


class User:
    def __init__(self, uid: int, name: str):
        self.id = uid
        self.display_name = name
        self.mention = f"<@{uid}>"
        self.roles = []


class Guild:
    def __init__(self, members: list[User]):
        self.id = 1
        self._members = {m.id: m for m in members}

    def get_member(self, uid):
        return self._members.get(uid)


class Interaction:
    client = None

    def __init__(self, user: User, guild: Guild | None, channel: Channel | None = None, message=None):
        self.user = user
        self.guild = guild
        self.channel = channel
        self.channel_id = channel.id if channel is not None else None
        self.response = Response()
        self.message = message
        self._original = None

    async def original_response(self):
        if self._original is not None:
            return self._original
        last = self.response.sent[-1]
        msg = Msg(last.get("embed"), _next_id(), last.get("view"))
        if self.channel is not None:
            self.channel.sent.append(msg)
        self._original = msg
        return msg


class ReverseRng:
    def shuffle(self, seq):
        seq.reverse()

    def choice(self, seq):
        return seq[0]

    def choices(self, population, weights=None, k=1):
        return [population[0]]

    def random(self):
        return 0.0


def _styles(view) -> dict:
    return {child.action: child.style for child in view.children}


def _spell_styles(view) -> dict:
    return {child.spell_value: child.style for child in view.children}


async def _brew(potions: Potions, user: User, guild: Guild, actions: list[str]):
    start = Interaction(user, guild, Channel(424242))
    await potions.start_practice_brew(start)
    opened = start.response.sent[-1]
    assert opened.get("ephemeral") is not True
    view = opened["view"]
    embed = opened["embed"]
    steps = [(embed, view)]
    for action in actions:
        button = next(child for child in view.children if child.action == action)
        click = Interaction(user, guild, start.channel)
        await button.callback(click)
        last = click.response.sent[-1]
        view = last.get("view")
        embed = last["embed"]
        steps.append((embed, view))
    return steps


async def main() -> None:
    assert PRACTICE_SEQUENCE == ("stir", "heat", "simmer")
    assert DUEL_NIGHT_WARN_MINUTES == 20
    assert EVENT_WARN_MINUTES == 20
    assert sorted(cmd.name for cmd in Practice.group.commands) == ["monster", "potion"]
    for cmd in Practice.group.commands:
        assert len(cmd.description) <= 100
    assert len(Practice.group.description) <= 100

    real = BrewView(None, 1, "beastcallers_draught", 0, 0, ["a", "b", "c"])
    assert all(child.style == discord.ButtonStyle.secondary for child in real.children)
    assert real.practice is False

    bot = Bot()
    Interaction.client = bot
    store = Store(bot)
    world = WorldCog(bot)
    potions = Potions(bot)
    threats = Dementors(bot)
    threats.lock = asyncio.Lock()
    threats.rng = ReverseRng()
    practice = Practice(bot)
    bot.cogs = {
        "Store": store,
        "World": world,
        "Potions": potions,
        "Dementors": threats,
    }

    brewer = User(7, "Brewer")
    friend = User(8, "Friend")
    guild = Guild([brewer, friend])
    store.state["overrides"][str(brewer.id)] = "caldrin"
    store.state["overrides"][str(friend.id)] = "thornmere"

    potions.state["players"][str(brewer.id)] = {
        "rep_xp": 40,
        "discovered": ["beastcallers_draught"],
        "inventory": {"beastcallers_draught": 2},
        "active": [{"type": "liquid_luck", "expires": 9_999_999_999}],
        "felix_free_at": time.time() + 100_000,
        "brew_day": {"date": "2026-10-16", "count": 4, "bonus": False},
    }
    student = world.student(brewer)
    student["items"] = {"moth_dust": 3, "sunbell": 1}
    student["day"] = {"date": "2026-10-16", "points": 1}

    threats.state["spawns_today"] = SPAWNS_PER_DAY
    threats.state["event"] = {
        "name": "Attack on Velmora",
        "practice": False,
        "tally": {"9": {"rep": 4, "kills": 1}},
        "channels": {},
        "ends_at": time.time() + 3600,
    }
    threats.state["channel_ids"] = [111]
    spawned = await threats.spawn(111, "wisp_swarm")
    assert spawned is not None
    real_view = bot.channel.sent[-1].view
    assert isinstance(real_view, ThreatSpellView)
    assert all(child.style == discord.ButtonStyle.success for child in real_view.children)

    awards: list = []
    orig_record = store.record

    def spy_record(**kwargs):
        awards.append(kwargs)
        return orig_record(**kwargs)

    store.record = spy_record
    orig_award = threats._award_kill_points

    async def spy_award(*args, **kwargs):
        awards.append(("kill", args[1] if len(args) > 1 else kwargs))
        return await orig_award(*args, **kwargs)

    threats._award_kill_points = spy_award

    def boom_credit(*_args, **_kwargs):
        raise AssertionError("practice credited an event tally")

    threats._credit_event = boom_credit

    async def boom_explore(*_args, **_kwargs):
        raise AssertionError("practice counted an explore kill")

    threats._count_explore_kill = boom_explore

    def boom_brew(*_args, **_kwargs):
        raise AssertionError("practice counted a potion brew")

    potions.try_award_brew_completion = boom_brew
    takes: list = []
    orig_take = world.world.take

    def spy_take(*args, **kwargs):
        takes.append(args)
        return orig_take(*args, **kwargs)

    world.world.take = spy_take

    snap_potions = copy.deepcopy(potions.state)
    snap_world = copy.deepcopy(world.state)
    snap_threats = copy.deepcopy(threats.state)
    snap_ledger = copy.deepcopy(store.state["ledger"])
    snap_totals = copy.deepcopy(store.state["totals"])
    saves = {"n": 0}
    for obj in (potions, threats, world, store):
        orig_save = obj.save

        def wrapped(orig_save=orig_save):
            saves["n"] += 1
            return orig_save()

        obj.save = wrapped

    # --- practice potion, any channel, highlighted buttons, no payout ---
    steps = await _brew(potions, brewer, guild, ["stir", "heat", "simmer"])
    assert _styles(steps[0][1]) == {
        "stir": discord.ButtonStyle.success,
        "heat": discord.ButtonStyle.secondary,
        "simmer": discord.ButtonStyle.secondary,
    }
    assert list(_styles(steps[0][1])) == list(ACTIONS)
    assert "Practice" in steps[0][0].title
    assert _styles(steps[1][1])["heat"] == discord.ButtonStyle.success
    assert _styles(steps[1][1])["stir"] == discord.ButtonStyle.secondary
    assert _styles(steps[2][1])["simmer"] == discord.ButtonStyle.success
    assert _styles(steps[2][1])["heat"] == discord.ButtonStyle.secondary
    done = steps[-1][0]
    assert done.title == "Practice · Brew complete"
    assert "no house points" in (done.description or "").lower()
    assert "Practice" in (done.footer.text or "")
    assert steps[-1][1] is None

    # A miss still teaches the real next step, and still pays nothing.
    missed = await _brew(potions, brewer, guild, ["heat", "heat", "simmer"])
    assert missed[-1][0].title == "Practice · Brew complete"
    ruined = await _brew(potions, brewer, guild, ["heat", "simmer", "stir"])
    assert ruined[-1][0].title == "Practice · The cauldron curdles"
    assert "no house points" in (ruined[-1][0].description or "").lower()

    stranger = Interaction(friend, guild, Channel(424242))
    view = steps[0][1]
    button = next(child for child in view.children if child.action == "stir")
    await button.callback(stranger)
    assert stranger.response.sent[-1]["ephemeral"] is True
    assert "/practice potion" in stranger.response.sent[-1]["content"]

    direct = Interaction(brewer, guild, Channel(999001))
    await practice.potion.callback(practice, direct)
    assert direct.response.sent[-1]["embed"].title.startswith("Practice")
    assert direct.channel.id not in (1553253009398693958, 1552401261184155648)

    nowhere = Interaction(brewer, None, None)
    await practice.potion.callback(practice, nowhere)
    assert nowhere.response.sent[-1]["ephemeral"] is True

    # --- practice monster: random pool, colored spells, no payout ---
    seen = set()
    roller = random.Random(1)
    threats.rng = roller
    for _ in range(30):
        posted = await threats.post_practice_monster(bot.channel)
        seen.add(threats.practice_fights[posted.id]["creature"])
    assert seen <= set(SPAWN_WEIGHTS)
    assert len(seen) > 1
    threats.rng = ReverseRng()

    wolf = await threats.post_practice_monster(bot.channel, "shadow_wolf")
    assert wolf.view.practice is True
    spell_styles = _spell_styles(wolf.view)
    assert spell_styles["hex"] == discord.ButtonStyle.primary
    assert all(
        style == discord.ButtonStyle.success
        for spell, style in spell_styles.items()
        if spell != "hex"
    )
    assert set(spell_styles) == set(SPELL_ORDER)
    assert [child.spell_value for child in wolf.view.children] == list(reversed(SPELL_ORDER))
    assert "Practice" in wolf.embeds[0].title
    assert "no house points" in (wolf.embeds[0].description or "").lower()
    assert wolf.embeds[0].footer.text == "Practice · no house points"
    assert threats.state["active"]["creature"] == "wisp_swarm"
    assert threats.state["spawns_today"] == snap_threats["spawns_today"]

    threats._can_auto_cast = lambda _user_id: True
    miss = Interaction(friend, guild, bot.channel, message=wolf)
    ward = next(child for child in wolf.view.children if child.spell_value == "ward")
    await ward.callback(miss)
    assert miss.response.sent[-1].get("ephemeral") is not True
    assert "Practice" in miss.response.sent[-1]["content"]
    assert threats.practice_fights[wolf.id]["hits"] == []
    assert threats.practice_fights[wolf.id]["locked_out"] == [friend.id]
    assert all(not child.disabled for child in wolf.view.children)

    locked = Interaction(friend, guild, bot.channel, message=wolf)
    await ward.callback(locked)
    assert locked.response.sent[-1]["content"] == LOCKED_OUT_MESSAGE
    assert locked.response.sent[-1]["ephemeral"] is True

    hit = Interaction(brewer, guild, bot.channel, message=wolf)
    hex_button = next(child for child in wolf.view.children if child.spell_value == "hex")
    await hex_button.callback(hit)
    victory = hit.response.sent[-1]["embed"]
    assert victory.title == "Practice · The Shadow Wolf is defeated"
    assert "Practice" in (victory.footer.text or "")
    assert "no house points" in (victory.footer.text or "").lower()
    assert wolf.id not in threats.practice_fights
    assert all(child.disabled for child in wolf.view.children)
    assert _spell_styles(wolf.view)["hex"] == discord.ButtonStyle.primary
    assert threats.state["active"]["creature"] == "wisp_swarm"

    golem = await threats.post_practice_monster(bot.channel, "stone_golem")
    bind = next(child for child in golem.view.children if child.spell_value == "bind")
    assert _spell_styles(golem.view)["bind"] == discord.ButtonStyle.primary
    first = Interaction(brewer, guild, bot.channel, message=golem)
    await bind.callback(first)
    assert first.response.sent[-1]["embed"].title == "Practice"
    assert threats.practice_fights[golem.id]["hits"] == [brewer.id]
    again = Interaction(brewer, guild, bot.channel, message=golem)
    await bind.callback(again)
    assert again.response.sent[-1]["ephemeral"] is True
    assert "someone else" in again.response.sent[-1]["content"]
    second = Interaction(friend, guild, bot.channel, message=golem)
    await bind.callback(second)
    assert second.response.sent[-1]["embed"].title == "Practice · The Stone Golem is defeated"
    assert golem.id not in threats.practice_fights

    demon = await threats.post_practice_monster(bot.channel, "dementor")
    assert _spell_styles(demon.view)["patronus"] == discord.ButtonStyle.primary
    patron = next(child for child in demon.view.children if child.spell_value == "patronus")
    ban = Interaction(brewer, guild, bot.channel, message=demon)
    await patron.callback(ban)
    assert ban.response.sent[-1]["embed"].title == "Practice · The Dementor is banished"
    assert demon.id not in threats.practice_fights

    lounge = Channel(8888)
    channels = {111: bot.channel, 8888: lounge}

    def _channel(cid):
        return channels.get(cid)

    bot.get_channel = _channel

    async def _fetch(cid):
        found = channels.get(cid)
        if found is None:
            raise discord.NotFound(response=None, message="missing")  # type: ignore[arg-type]
        return found

    bot.fetch_channel = _fetch

    summoned = Interaction(brewer, guild, lounge)
    await threats.start_practice_summon(summoned)
    posted = await summoned.original_response()
    creature_id = threats.practice_fights[posted.id]["creature"]
    assert creature_id in SPAWN_WEIGHTS
    assert "Practice" in posted.embeds[0].title
    spell = CREATURES[creature_id]["weak"]
    button = next(child for child in posted.view.children if child.spell_value == spell)
    await button.callback(Interaction(brewer, guild, lounge, message=posted))

    dm = Interaction(brewer, None, None)
    await threats.start_practice_summon(dm)
    assert dm.response.sent[-1]["ephemeral"] is True

    assert awards == []
    assert takes == []
    assert saves["n"] == 0
    assert potions.state == snap_potions
    assert world.state == snap_world
    assert threats.state == snap_threats
    assert store.state["ledger"] == snap_ledger
    assert store.state["totals"] == snap_totals
    assert world.student(brewer)["items"] == {"moth_dust": 3, "sunbell": 1}
    assert world.student(brewer)["day"]["points"] == 1
    assert potions.state["players"][str(brewer.id)]["brew_day"]["count"] == 4
    assert potions.state["players"][str(brewer.id)]["rep_xp"] == 40
    assert potions.state["players"][str(brewer.id)]["inventory"] == {"beastcallers_draught": 2}
    assert all(child.style == discord.ButtonStyle.success for child in real_view.children)
    assert all(not child.disabled for child in real_view.children)
    print("OK")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
