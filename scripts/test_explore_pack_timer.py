#!/usr/bin/env python3
"""Two-person explore threats don't wait out the exclusive countdown.

A creature stirred by /explore gives the finder 25 seconds alone. That
window is a live countdown on the arrival post, and it used to keep
blocking everyone else even after the finder had already landed a hit.
On a two-person creature the first clean hit now removes that countdown
and opens the fight. The second hit resolves immediately. If the second
hit never comes, the original deadline still closes the fight with no
reward. A one-person creature still resolves on its only hit.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-pack-timer-"))
os.environ.setdefault("DISCORD_TOKEN", "")

import discord  # noqa: E402

from cogs.dementors import (  # noqa: E402
    CREATURES,
    Dementors,
    SpellPick,
    exclusive_countdown_note,
    partial_pack_should_expire,
    strip_exclusive_countdown,
)
from cogs.store import Store  # noqa: E402
from cogs.world import WorldCog  # noqa: E402


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


class Msg:
    def __init__(self, embed, mid: int):
        self.id = mid
        self.embeds = [embed] if embed is not None else []

    async def edit(self, **kwargs):
        if "embed" in kwargs:
            self.embeds = [kwargs["embed"]]


class Channel:
    def __init__(self):
        self.id = 111
        self.sent: list[Msg] = []

    async def send(self, *args, **kwargs):
        embed = kwargs.get("embed")
        if embed is None and args:
            embed = args[0] if isinstance(args[0], discord.Embed) else None
        msg = Msg(embed, 100 + len(self.sent))
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


class User:
    def __init__(self, uid: int, name: str):
        self.id = uid
        self.display_name = name
        self.mention = f"<@{uid}>"
        self.roles = []


class Guild:
    def __init__(self, members: list[User]):
        self._members = {m.id: m for m in members}

    def get_member(self, uid):
        return self._members.get(uid)


class Interaction:
    def __init__(self, user: User, guild: Guild, channel_id: int = 111):
        self.user = user
        self.guild = guild
        self.channel_id = channel_id
        self.response = Response()


class Patronus:
    def patronus_of(self, user_id: int):
        if user_id == 1:
            return {"animal": "stag"}
        return None

    def art_file_for(self, user_id: int, field: str = "defeat_art"):
        return None


def _embed(response: Response) -> discord.Embed:
    return response.sent[-1]["embed"]


def _active_arrival(threats: Dementors) -> Msg:
    mid = threats.state["active"]["message_id"]
    for msg in threats.bot.channel.sent:
        if msg.id == mid:
            return msg
    raise AssertionError("arrival message missing")


async def _drain(task: asyncio.Task | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def main() -> None:
    # Rewards and pack sizes stay what they were.
    assert CREATURES["shadow_wolf"]["pack"] == 1
    assert CREATURES["shadow_wolf"]["points"] == 3
    assert CREATURES["dementor"]["pack"] == 1
    assert CREATURES["dementor"]["points"] == 3
    assert CREATURES["stone_golem"]["pack"] == 2
    assert CREATURES["stone_golem"]["points"] == 4
    assert CREATURES["nightweaver"]["pack"] == 2
    assert CREATURES["stone_golem"]["weak"] == "bind"
    assert CREATURES["shadow_wolf"]["weak"] == "hex"

    note = exclusive_countdown_note(7, 1_700_000_000)
    assert note.startswith("\n\n⏳ ")
    assert "<t:1700000000:R>" in note
    arrival = "A shape built out of loose stone hauls itself upright."
    combined = arrival + note
    assert strip_exclusive_countdown(combined) == arrival
    assert strip_exclusive_countdown(arrival) == arrival
    assert strip_exclusive_countdown("") == ""
    assert strip_exclusive_countdown(None) == ""

    future = time.time() + 10_000
    past = time.time() - 10
    assert partial_pack_should_expire(None, future) is False
    assert partial_pack_should_expire(
        {"creature": "stone_golem", "hits": [], "partial_expires_at": past}, past + 1,
    ) is False
    assert partial_pack_should_expire(
        {"creature": "stone_golem", "hits": [1], "partial_expires_at": future}, time.time(),
    ) is False
    assert partial_pack_should_expire(
        {"creature": "stone_golem", "hits": [1], "partial_expires_at": past}, time.time(),
    ) is True
    assert partial_pack_should_expire(
        {"creature": "stone_golem", "hits": [1, 2], "partial_expires_at": past}, time.time(),
    ) is False
    assert partial_pack_should_expire(
        {"creature": "nope", "hits": [1], "partial_expires_at": past}, time.time(),
    ) is False

    bot = Bot()
    store = Store(bot)
    world = WorldCog(bot)
    threats = Dementors(bot)
    threats.lock = asyncio.Lock()
    bot.cogs = {
        "Store": store,
        "World": world,
        "Dementors": threats,
        "Patronus": Patronus(),
    }
    finder = User(1, "Finder")
    friend = User(2, "Friend")
    store.state["overrides"][str(finder.id)] = "caldrin"
    store.state["overrides"][str(friend.id)] = "thornmere"
    guild = Guild([finder, friend])

    # The arrival post still shows the countdown when the creature appears.
    assert await threats.try_ambient_spawn(
        111, "stone_golem", finder_id=finder.id, pay_explore_points=True,
    )
    active = threats.state["active"]
    assert active["creature"] == "stone_golem"
    assert active["finder_id"] == finder.id
    until = active["exclusive_until"]
    assert until > time.time()
    arrival_msg = _active_arrival(threats)
    assert "⏳" in (arrival_msg.embeds[0].description or "")
    blocked = threats._finder_blocks(active, friend.id)
    assert blocked and "until" in blocked
    assert threats._finder_blocks(active, finder.id) is None

    # A miss does not open the fight or take the countdown down.
    miss = Interaction(finder, guild)
    await threats.resolve_cast(miss, SpellPick("hex"))
    assert miss.response.sent[-1]["content"] == "Nothing happens."
    assert miss.response.sent[-1]["ephemeral"] is True
    assert threats.state["active"]["hits"] == []
    assert "⏳" in (_active_arrival(threats).embeds[0].description or "")
    assert threats._finder_blocks(threats.state["active"], friend.id)

    # First clean hit: countdown gone, friend may cast, deadline unchanged,
    # fight still open, no reward yet.
    rows_before = len(store.state["ledger"])
    first = Interaction(finder, guild)
    started = time.perf_counter()
    await threats.resolve_cast(first, SpellPick("bind"))
    assert time.perf_counter() - started < 1
    assert threats.state["active"]["hits"] == [finder.id]
    assert threats.state["active"]["exclusive_until"] == until
    assert threats.state["active"]["partial_expires_at"] == until
    assert threats._finder_blocks(threats.state["active"], friend.id) is None
    desc = _active_arrival(threats).embeds[0].description or ""
    assert "⏳" not in desc
    assert desc  # arrival text stays
    progress = _embed(first.response)
    assert progress.title is None
    assert finder.mention in (progress.description or "")
    assert len(store.state["ledger"]) == rows_before
    pending = threats._partial_expiry_task
    assert pending is not None and not pending.done()

    # The finder cannot finish their own two-person creature.
    again = Interaction(finder, guild)
    await threats.resolve_cast(again, SpellPick("bind"))
    assert "someone else" in again.response.sent[-1]["content"]
    assert threats.state["active"]["hits"] == [finder.id]

    # Second hit resolves immediately, while the old deadline is still ahead.
    second = Interaction(friend, guild)
    await threats.resolve_cast(second, SpellPick("bind"))
    assert time.time() < until
    assert threats.state["active"] is None
    victory = _embed(second.response)
    assert victory.title == "✨ The Stone Golem is defeated"
    assert finder.mention in (victory.description or "")
    assert friend.mention in (victory.description or "")
    assert "slips away" not in (victory.description or "").lower()
    assert [row["delta"] for row in store.state["ledger"][rows_before:]] == [4, 4]
    assert [row["reason"] for row in store.state["ledger"][rows_before:]] == [
        "Defeated a Stone Golem",
        "Defeated a Stone Golem",
    ]
    # Full creature amount, checklist clamps at 3. Not a second payout.
    assert world.student(finder)["day"]["points"] == 3
    assert world.student(friend)["day"]["points"] == 3
    await _drain(pending)
    assert all("slips away" not in (m.embeds[0].description or "") for m in bot.channel.sent if m.embeds)

    # One-person creatures still resolve on the single hit, countdown included.
    assert await threats.try_ambient_spawn(
        111, "shadow_wolf", finder_id=finder.id, pay_explore_points=True,
    )
    wolf_until = threats.state["active"]["exclusive_until"]
    assert "⏳" in (_active_arrival(threats).embeds[0].description or "")
    wolf_rows = len(store.state["ledger"])
    wolf = Interaction(finder, guild)
    t0 = time.perf_counter()
    await threats.resolve_cast(wolf, SpellPick("hex"))
    assert time.perf_counter() - t0 < 1
    assert time.time() < wolf_until
    assert threats.state["active"] is None
    wolf_embed = _embed(wolf.response)
    assert wolf_embed.title == "✨ The Shadow Wolf is defeated"
    # The spawn message itself no longer counts down.
    spawn_left = [m for m in bot.channel.sent if m.embeds and "Shadow Wolf" in (m.embeds[0].title or "")]
    assert spawn_left and "⏳" not in (spawn_left[-1].embeds[0].description or "")
    assert store.state["ledger"][wolf_rows]["delta"] == 3
    assert store.state["ledger"][wolf_rows]["reason"] == "Defeated a Shadow Wolf"

    # Dementor (also one hit) resolves immediately and drops the countdown.
    assert await threats.try_ambient_spawn(111, "dementor", finder_id=finder.id)
    assert "patronus" in (_active_arrival(threats).embeds[0].description or "").lower()
    assert "⏳" in (_active_arrival(threats).embeds[0].description or "")
    ban = Interaction(finder, guild)
    await threats.resolve_cast(ban, SpellPick("patronus"))
    assert threats.state["active"] is None
    assert _embed(ban.response).title == "✨ The Dementor is banished"
    demon_posts = [m for m in bot.channel.sent if m.embeds and "Dementor" in (m.embeds[0].title or "")]
    assert demon_posts and "⏳" not in (demon_posts[-1].embeds[0].description or "")
    assert "patronus" in (demon_posts[-1].embeds[0].description or "").lower()

    # Only one hit, original deadline passes: creature slips away, no reward.
    threats.state["active"] = None
    assert await threats.try_ambient_spawn(
        111, "nightweaver", finder_id=finder.id, pay_explore_points=True,
    )
    # Keep the cast itself inside the window, then pull the hidden deadline
    # forward. The expiry must follow partial_expires_at, not a fresh 25s.
    threats.state["active"]["exclusive_until"] = time.time() + 30
    slip_rows = len(store.state["ledger"])
    opened = Interaction(finder, guild)
    await threats.resolve_cast(opened, SpellPick("disarm"))
    assert threats.state["active"]["hits"] == [finder.id]
    assert "⏳" not in (_active_arrival(threats).embeds[0].description or "")
    old_timer = threats._partial_expiry_task
    threats.state["active"]["partial_expires_at"] = time.time() + 0.05
    threats._arm_partial_expiry()
    await _drain(old_timer)
    task = threats._partial_expiry_task
    assert task is not None
    await task
    assert threats.state["active"] is None
    slip = bot.channel.sent[-1].embeds[0]
    assert "Nightweaver" in (slip.description or "")
    assert "slips away" in (slip.description or "")
    assert len(store.state["ledger"]) == slip_rows
    assert world.student(finder)["day"]["points"] == 3  # unchanged from the earlier wolf

    # A first hit after the window already ended does not start a hidden
    # expiry. The creature stays until someone finishes it.
    threats.state["active"] = None
    assert await threats.try_ambient_spawn(111, "stone_golem", finder_id=finder.id)
    threats.state["active"]["exclusive_until"] = time.time() - 5
    late = Interaction(finder, guild)
    await threats.resolve_cast(late, SpellPick("bind"))
    assert threats.state["active"]["hits"] == [finder.id]
    assert "partial_expires_at" not in threats.state["active"]
    assert threats._partial_expiry_task is None
    assert threats._finder_blocks(threats.state["active"], friend.id) is None
    finish = Interaction(friend, guild)
    await threats.resolve_cast(finish, SpellPick("bind"))
    assert threats.state["active"] is None
    assert _embed(finish.response).title == "✨ The Stone Golem is defeated"

    # Staff-style summon: no finder, no countdown, no hidden expiry.
    # Still resolves on the second hit, and stays open after the first.
    threats.state["active"] = {
        "creature": "stone_golem",
        "channel_id": 111,
        "message_id": _plant(bot, "A golem stands here. No clock."),
        "spawned_at": time.time(),
        "hits": [],
    }
    staff_rows = len(store.state["ledger"])
    await threats.resolve_cast(Interaction(finder, guild), SpellPick("bind"))
    assert threats.state["active"]["hits"] == [finder.id]
    assert "partial_expires_at" not in threats.state["active"]
    assert threats._partial_expiry_task is None
    assert "⏳" not in (bot.channel.sent[-1].embeds[0].description or "")
    await threats.resolve_cast(Interaction(friend, guild), SpellPick("bind"))
    assert threats.state["active"] is None
    assert [row["delta"] for row in store.state["ledger"][staff_rows:]] == [4, 4]

    # Event waves have no explore countdown. Two hits still resolve at once,
    # and a one-person wave still resolves on the first hit.
    threats.state["event"] = {
        "name": "Attack on Velmora",
        "channels": {
            "111": {"creature": "stone_golem", "hits": [], "message_id": 1, "spawned_at": time.time()},
        },
        "tally": {},
    }
    ev1 = Interaction(finder, guild)
    await threats.resolve_cast(ev1, SpellPick("bind"))
    assert "channels" in threats.state["event"]
    assert threats.state["event"]["channels"]["111"]["hits"] == [finder.id]
    assert _embed(ev1.response).title is None
    ev2 = Interaction(friend, guild)
    await threats.resolve_cast(ev2, SpellPick("bind"))
    assert "111" not in threats.state["event"]["channels"]
    assert _embed(ev2.response).title == "✨ The Stone Golem is defeated"
    assert threats.state["event"]["tally"][str(finder.id)]["kills"] == 1
    assert threats.state["event"]["tally"][str(friend.id)]["rep"] == 4

    threats.state["event"]["channels"]["111"] = {
        "creature": "shadow_wolf", "hits": [], "message_id": 2, "spawned_at": time.time(),
    }
    evw = Interaction(finder, guild)
    await threats.resolve_cast(evw, SpellPick("hex"))
    assert "111" not in threats.state["event"]["channels"]
    assert _embed(evw.response).title == "✨ The Shadow Wolf is defeated"

    # Zero hits never trip the hidden expiry, even if a deadline is stale.
    threats.state["event"] = None
    threats.state["active"] = {
        "creature": "stone_golem",
        "channel_id": 111,
        "message_id": 1,
        "spawned_at": time.time(),
        "hits": [],
        "finder_id": finder.id,
        "exclusive_until": past,
        "partial_expires_at": past,
    }
    await threats._partial_expiry_wait(1, past)
    assert threats.state["active"]["hits"] == []

    print("OK")


def _plant(bot: Bot, description: str) -> int:
    embed = discord.Embed(description=description, title="🪨 A Stone Golem has appeared")
    msg = Msg(embed, 100 + len(bot.channel.sent))
    bot.channel.sent.append(msg)
    return msg.id


if __name__ == "__main__":
    asyncio.run(main())
