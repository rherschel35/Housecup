#!/usr/bin/env python3
"""Practice drills do not count as progress.

`/practice potion` and `/practice monster` must not move the daily potion
challenge, an open quest, the slash-usage tally, the Explore checklist, or
the points ledger.

The usage listener is the hook that sees both commands. The persistent
wild-threat view is the hook that used to share button ids with practice
monsters and could resolve a click as a real fight.
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-practice-progress-"))
os.environ.setdefault("DISCORD_TOKEN", "")

import discord  # noqa: E402

from cogs.checklist import Checklist  # noqa: E402
from cogs.dementors import (  # noqa: E402
    CREATURES,
    PRACTICE_SPELL_PREFIX,
    SPELL_ORDER,
    THREAT_SPELL_PREFIX,
    Dementors,
    ThreatSpellButton,
    ThreatSpellView,
    spell_from_custom_id,
)
from cogs.potions import ACTIONS, Potions  # noqa: E402
from cogs.practice import PRACTICE_COMMANDS, Practice, tracks_progress  # noqa: E402
from cogs.quests import Quests  # noqa: E402
from cogs.store import Store  # noqa: E402
from cogs.usage import Usage  # noqa: E402
from cogs.world import WorldCog  # noqa: E402
from cogs.world_engine import today as central_today  # noqa: E402


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

    async def wait_until_ready(self):
        return None


class Msg:
    def __init__(self, embed, mid: int, view=None, content=None):
        self.id = mid
        self.embeds = [embed] if embed is not None else []
        self.view = view
        self.content = content
        self.components = []

    async def edit(self, **kwargs):
        if "embed" in kwargs:
            self.embeds = [kwargs["embed"]]
        if "view" in kwargs:
            self.view = kwargs["view"]


_NEXT_ID = 500


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
        msg = Msg(embed, _next_id(), kwargs.get("view"), content)
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
        self.bot = False


class Guild:
    def __init__(self, members: list[User]):
        self.id = 1
        self._members = {m.id: m for m in members}

    def get_member(self, uid):
        return self._members.get(uid)


class Interaction:
    client = None

    def __init__(self, user: User, guild, channel=None, message=None, data=None):
        self.user = user
        self.guild = guild
        self.channel = channel
        self.channel_id = channel.id if channel is not None else None
        self.response = Response()
        self.message = message
        self.data = data

    async def original_response(self):
        last = self.response.sent[-1]
        msg = Msg(last.get("embed"), _next_id(), last.get("view"))
        if self.channel is not None:
            self.channel.sent.append(msg)
        return msg


class NamedCommand:
    def __init__(self, qualified_name: str, extras=None):
        self.qualified_name = qualified_name
        self.extras = extras or {}


def _potion_line(checklist: Checklist, member: User):
    items = checklist._collect(member, time.time())
    matches = [item for item in items if item.label == "Potion brews"]
    assert len(matches) == 1
    return matches[0]


async def _brew(potions: Potions, user: User, guild: Guild, actions: list[str]):
    start = Interaction(user, guild, Channel(424242))
    await potions.start_practice_brew(start)
    opened = start.response.sent[-1]
    view = opened["view"]
    for action in actions:
        button = next(child for child in view.children if child.action == action)
        click = Interaction(user, guild, start.channel)
        await button.callback(click)
        view = click.response.sent[-1].get("view")
    return opened


async def main() -> None:
    assert PRACTICE_COMMANDS == frozenset({"practice potion", "practice monster"})
    for cmd in Practice.group.commands:
        assert len(cmd.description) <= 100
        assert cmd.extras.get("practice") is True
        assert tracks_progress(cmd) is False
    assert tracks_progress(NamedCommand("brew")) is True
    assert tracks_progress(NamedCommand("vcc practice")) is True
    assert tracks_progress(NamedCommand("practice potion")) is False
    assert spell_from_custom_id("threatspell:practice:hex") == "hex"
    assert spell_from_custom_id("threatspell:hex") == "hex"
    assert spell_from_custom_id("threatspell:practice:hex") != "practice:hex"

    persistent = ThreatSpellView(list(SPELL_ORDER))
    practice_row = ThreatSpellView(list(SPELL_ORDER), practice=True)
    persistent_ids = {child.custom_id for child in persistent.children}
    practice_ids = {child.custom_id for child in practice_row.children}
    assert persistent_ids.isdisjoint(practice_ids)
    assert all(str(cid).startswith(THREAT_SPELL_PREFIX) for cid in persistent_ids)
    assert all(str(cid).startswith(PRACTICE_SPELL_PREFIX) for cid in practice_ids)
    assert all(not child.practice for child in persistent.children)
    assert all(child.practice for child in practice_row.children)

    bot = Bot()
    Interaction.client = bot
    store = Store(bot)
    world = WorldCog(bot)
    potions = Potions(bot)
    threats = Dementors(bot)
    threats.lock = asyncio.Lock()
    usage = Usage(bot)
    checklist = Checklist(bot)
    quests = Quests(bot)
    practice = Practice(bot)
    bot.cogs = {
        "Store": store,
        "World": world,
        "Potions": potions,
        "Dementors": threats,
        "Usage": usage,
        "Checklist": checklist,
        "Quests": quests,
    }

    day = central_today()
    brewer = User(7, "Brewer")
    guild = Guild([brewer])
    store.state["overrides"][str(brewer.id)] = "caldrin"
    potions.state["players"][str(brewer.id)] = {
        "rep_xp": 40,
        "discovered": ["beastcallers_draught"],
        "inventory": {"beastcallers_draught": 2},
        "active": [],
        "felix_free_at": time.time() + 100_000,
        "brew_day": {"date": day, "count": 4, "bonus": False},
    }
    student = world.student(brewer)
    student["items"] = {"moth_dust": 3}
    student["day"] = {"date": day, "points": 1, "explore": 2, "forage": 0}

    quests.state["active"]["daily"] = {
        "task": {
            "kind": "choice",
            "prompt": "Which house keeps the cup?",
            "options": ["Caldrin", "Thornmere"],
            "correct_index": 0,
        },
        "winners": [],
        "attempted": [],
        "channel_id": bot.channel.id,
        "message_id": 999,
        "posted_at": time.time(),
        "closed": False,
    }
    quests.state["last_posted"]["daily"] = time.time()
    snap_quests = copy.deepcopy(quests.state)
    snap_usage = copy.deepcopy(usage.state)
    snap_checklist = copy.deepcopy(checklist.state)

    # One short of the daily potion challenge. A counted practice brew
    # would finish it and pay the +3.
    assert "4/5" in _potion_line(checklist, brewer).detail
    assert _potion_line(checklist, brewer).done is False

    await _brew(potions, brewer, guild, ["stir", "heat", "simmer"])
    direct = Interaction(brewer, guild, Channel(999001))
    await practice.potion.callback(practice, direct)
    await usage.on_app_command_completion(direct, Practice.potion)

    posted = await threats.post_practice_monster(bot.channel, "shadow_wolf")
    assert posted.view.practice is True
    assert CREATURES["shadow_wolf"]["weak"] == "hex"
    summon = Interaction(brewer, guild, bot.channel)
    await practice.monster.callback(practice, summon)
    await usage.on_app_command_completion(summon, Practice.monster)

    # The persistent real-fight button, not the practice one. Same message
    # as a live spawn, which is what the old shared custom id could hit.
    threats.state["active"] = {
        "creature": "shadow_wolf",
        "channel_id": bot.channel.id,
        "message_id": posted.id,
        "spawned_at": time.time(),
        "hits": [],
        "spell_buttons": True,
        "locked_out": [],
        "pay_explore_points": True,
    }
    threats.state["event"] = {
        "name": "Attack on Velmora",
        "practice": False,
        "tally": {"9": {"rep": 4, "kills": 1}},
        "channels": {},
        "ends_at": time.time() + 3600,
    }
    scored: list = []

    async def spy_resolve(*_args, **_kwargs):
        scored.append("resolve")

    async def spy_award(*_args, **_kwargs):
        scored.append("award")

    threats.resolve_cast = spy_resolve
    threats._award_kill_points = spy_award
    real_button = ThreatSpellButton("hex", row=0, practice=False)
    await real_button.callback(Interaction(brewer, guild, bot.channel, message=posted))

    assert scored == []
    assert usage.state["counts"] == {}
    assert int(usage.state.get("total") or 0) == 0
    assert usage.state["counts"] == snap_usage["counts"]
    assert quests.state == snap_quests
    assert potions.state["players"][str(brewer.id)]["brew_day"]["count"] == 4
    assert potions.state["players"][str(brewer.id)]["rep_xp"] == 40
    assert world.student(brewer)["day"]["points"] == 1
    assert world.student(brewer)["day"]["explore"] == 2
    assert store.state["ledger"] == []
    progress = checklist.sync_progress(brewer)
    assert progress["bonus_pts"] == 0
    assert "4/5" in _potion_line(checklist, brewer).detail
    assert checklist.state["players"][str(brewer.id)]["full_completions"] == 0
    # sync_progress writes today's snapshot; it must not invent a clear.
    assert checklist.state["players"][str(brewer.id)].get("full_completions") == snap_checklist.get(
        "players", {}
    ).get(str(brewer.id), {}).get("full_completions", 0)

    # A real brew still fills the daily potion challenge.
    paid = potions.try_award_brew_completion(brewer)
    assert paid == 3
    assert potions.state["players"][str(brewer.id)]["brew_day"]["count"] == 5
    assert any(
        row.get("reason") == "Completed 5 potion brews today"
        for row in store.state["ledger"]
    )

    # A real fight button still reaches the resolver.
    real_msg = await bot.channel.send(embed=discord.Embed(title="Wisp"), view=ThreatSpellView(["mirror"]))
    threats.state["active"] = {
        "creature": "wisp_swarm",
        "channel_id": bot.channel.id,
        "message_id": real_msg.id,
        "spawned_at": time.time(),
        "hits": [],
        "spell_buttons": True,
        "locked_out": [],
    }
    live = ThreatSpellButton("mirror", row=0, practice=False)
    await live.callback(Interaction(brewer, guild, bot.channel, message=real_msg))
    assert scored == ["resolve"]

    # Disabling a practice row keeps the practice ids, so the persistent
    # real-fight view still cannot catch a late click.
    await threats._disable_threat_buttons(bot.channel.id, posted.id)
    disabled = posted.view
    assert disabled is not None and disabled.practice is True
    assert all(str(child.custom_id).startswith(PRACTICE_SPELL_PREFIX) for child in disabled.children)

    brew = NamedCommand("brew")
    await usage.on_app_command_completion(Interaction(brewer, guild, bot.channel), brew)
    assert usage.state["counts"] == {"brew": 1}
    assert "practice potion" not in usage.state["counts"]
    assert "practice monster" not in usage.state["counts"]

    # Practice buttons are Stir / Heat / Simmer, same labels as a real cauldron,
    # and they are not challenge answer ids.
    opened = (await _brew(potions, brewer, guild, []))
    labels = [child.label for child in opened["view"].children]
    assert labels == list(ACTIONS.values())
    assert all(not str(getattr(child, "custom_id", "")).startswith("velmora:challenge:")
               for child in opened["view"].children)

    quests.scheduler.cancel()
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
