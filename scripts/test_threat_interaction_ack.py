#!/usr/bin/env python3
"""A slow message edit must not eat Discord's three-second click window.

The correct Patronus on a Dementor, a regular monster kill, a click on a
monster that is already gone, and a practice monster all used to edit the
post before answering the interaction. Discord then showed "didn't respond",
and the failed reply was logged at debug. The click is acknowledged first.
Victory lines go out as a followup after the edit and the point award.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-spell-ack-"))
os.environ.setdefault("DISCORD_TOKEN", "")

import discord  # noqa: E402

from cogs.dementors import (  # noqa: E402
    CREATURES,
    Dementors,
    ThreatSpellButton,
    ThreatSpellView,
    _unknown_interaction,
    format_kill_footer,
)
from cogs.store import Store  # noqa: E402

# Long enough that awaiting it before the ack would show up in the order.
SLOW = 0.1
NOTHING_HERE = "The air here feels perfectly normal. Nothing to banish."
PRACTICE_OVER = "This practice fight is over."


class _HTTP:
    status = 404
    reason = "Not Found"


def _not_found(code: int, message: str) -> discord.NotFound:
    return discord.NotFound(_HTTP(), {"code": code, "message": message})


class Timeline:
    def __init__(self):
        self.events: list[tuple[str, float]] = []

    def mark(self, name: str) -> None:
        self.events.append((name, time.perf_counter()))

    def times(self, name: str) -> list[float]:
        return [stamp for event, stamp in self.events if event == name]


class Msg:
    def __init__(self, embed, mid: int, view, channel: "Channel"):
        self.id = mid
        self.embeds = [embed] if embed is not None else []
        self.view = view
        self.attachments = []
        self.components = []
        self.channel = channel

    async def edit(self, **kwargs):
        self.channel.timeline.mark("edit-start")
        await asyncio.sleep(SLOW)
        self.channel.timeline.mark("edit-end")
        if "embed" in kwargs:
            self.embeds = [kwargs["embed"]]
        if "view" in kwargs:
            self.view = kwargs["view"]


class Channel:
    def __init__(self):
        self.id = 111
        self.sent: list[Msg] = []
        self.timeline = Timeline()

    async def send(self, *args, **kwargs):
        embed = kwargs.get("embed")
        if embed is None and args and isinstance(args[0], discord.Embed):
            embed = args[0]
        msg = Msg(embed, 100 + len(self.sent), kwargs.get("view"), self)
        self.sent.append(msg)
        return msg

    async def fetch_message(self, message_id):
        self.timeline.mark("fetch-start")
        await asyncio.sleep(SLOW)
        self.timeline.mark("fetch-end")
        for msg in self.sent:
            if msg.id == message_id:
                return msg
        raise _not_found(10008, "Unknown Message")


class Response:
    def __init__(self, timeline: Timeline):
        self.timeline = timeline
        self.sent: list[dict] = []
        self.deferred = None
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content=None, **kwargs):
        self.timeline.mark("ack")
        self._done = True
        self.sent.append({"content": content, **kwargs})

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False):
        self.timeline.mark("ack")
        self._done = True
        self.deferred = {"ephemeral": ephemeral, "thinking": thinking}


class Followup:
    def __init__(self, timeline: Timeline):
        self.timeline = timeline
        self.sent: list[dict] = []

    async def send(self, content=None, **kwargs):
        self.timeline.mark("followup")
        self.sent.append({"content": content, **kwargs})


class User:
    def __init__(self, uid: int, name: str):
        self.id = uid
        self.display_name = name
        self.mention = f"<@{uid}>"
        self.roles = []


class Guild:
    def __init__(self, members: list[User]):
        self._members = {member.id: member for member in members}

    def get_member(self, uid):
        return self._members.get(uid)


class Interaction:
    def __init__(self, user: User, guild: Guild, message, timeline: Timeline):
        self.user = user
        self.guild = guild
        self.channel_id = 111
        self.message = message
        self.response = Response(timeline)
        self.followup = Followup(timeline)
        self.client = None


class Bot:
    def __init__(self):
        self.user = type("U", (), {"id": 1})()
        self.cogs = {}
        self.channel = Channel()

    def get_cog(self, name):
        return self.cogs.get(name)

    def get_channel(self, channel_id):
        if channel_id == self.channel.id:
            return self.channel
        return None

    async def fetch_channel(self, channel_id):
        found = self.get_channel(channel_id)
        if found is None:
            raise _not_found(10003, "Unknown Channel")
        return found


def _disabled(view) -> bool:
    return bool(view) and bool(view.children) and all(child.disabled for child in view.children)


def _assert_ack_before_edit(timeline: Timeline, *, followup: bool) -> None:
    """The acknowledgement is recorded before a slow edit starts."""
    acks = timeline.times("ack")
    edits = timeline.times("edit-start")
    edit_ends = timeline.times("edit-end")
    assert len(acks) == 1, timeline.events
    assert edits, "the slow message edit did not run"
    assert len(edits) == len(edit_ends)
    assert acks[0] <= min(edits)
    assert max(edit_ends) - min(edits) >= SLOW * 0.5
    follows = timeline.times("followup")
    if followup:
        assert len(follows) == 1
        assert follows[0] >= max(edit_ends)
    else:
        assert follows == []


def _active_message(threats: Dementors) -> Msg:
    mid = threats.state["active"]["message_id"]
    for msg in threats.bot.channel.sent:
        if msg.id == mid:
            return msg
    raise AssertionError("arrival message missing")


async def _click(threats: Dementors, user: User, guild: Guild, message, spell: str) -> Interaction:
    timeline = Timeline()
    threats.bot.channel.timeline = timeline
    interaction = Interaction(user, guild, message, timeline)
    await threats.on_public_spell_click(interaction, spell)
    return interaction


def _check_logs(records: list[logging.LogRecord]) -> None:
    expired = [record for record in records if "interaction expired or unknown" in record.getMessage()]
    assert expired, [record.getMessage() for record in records]
    assert all(record.levelno == logging.WARNING for record in expired)
    assert all(record.levelno != logging.DEBUG for record in expired)


async def main() -> None:
    assert _unknown_interaction(_not_found(10062, "Unknown interaction")) is True
    assert _unknown_interaction(_not_found(10008, "Unknown Message")) is False

    bot = Bot()
    store = Store(bot)
    threats = Dementors(bot)
    threats.lock = asyncio.Lock()
    bot.cogs = {"Store": store, "Dementors": threats}
    finder = User(1, "Finder")
    store.state["overrides"][str(finder.id)] = "caldrin"
    guild = Guild([finder])

    # Correct Patronus. The countdown edit and the button edit are slow,
    # and the victory line still names the banishment and the house points.
    rows = len(store.state["ledger"])
    assert await threats.try_ambient_spawn(111, "dementor", finder_id=finder.id)
    demon = _active_message(threats)
    banished = await _click(threats, finder, guild, demon, "patronus")
    _assert_ack_before_edit(threats.bot.channel.timeline, followup=True)
    assert banished.response.deferred == {"ephemeral": False, "thinking": True}
    assert banished.response.sent == []
    victory = banished.followup.sent[-1]
    assert victory.get("ephemeral") is not True
    assert victory["embed"].title == "✨ The Dementor is banished"
    assert finder.mention in (victory["embed"].description or "")
    assert "silver" in (victory["embed"].description or "").lower()
    assert victory["embed"].footer.text == format_kill_footer(
        [(CREATURES["dementor"]["points"], "caldrin")], points_word=True,
    )
    assert threats.state["active"] is None
    assert _disabled(demon.view)
    paid = store.state["ledger"][rows:]
    assert [row["delta"] for row in paid] == [CREATURES["dementor"]["points"]]
    assert [row["reason"] for row in paid] == ["Banished a Dementor"]

    # The monster is already gone. The private note is the acknowledgement,
    # and only then does the button edit run.
    stale = await _click(threats, finder, guild, demon, "patronus")
    _assert_ack_before_edit(threats.bot.channel.timeline, followup=False)
    assert stale.response.deferred is None
    assert stale.followup.sent == []
    note = stale.response.sent[-1]
    assert note["content"] == NOTHING_HERE
    assert note["ephemeral"] is True
    assert _disabled(demon.view)
    assert len(store.state["ledger"]) == rows + 1

    # A one-hit creature uses the same order: ack, edit, award, followup.
    rows = len(store.state["ledger"])
    assert await threats.try_ambient_spawn(111, "shadow_wolf", finder_id=finder.id)
    wolf = _active_message(threats)
    killed = await _click(threats, finder, guild, wolf, "hex")
    _assert_ack_before_edit(threats.bot.channel.timeline, followup=True)
    assert killed.response.deferred == {"ephemeral": False, "thinking": True}
    assert killed.response.sent == []
    wolf_embed = killed.followup.sent[-1]["embed"]
    assert wolf_embed.title == "✨ The Shadow Wolf is defeated"
    assert finder.mention in (wolf_embed.description or "")
    assert wolf_embed.footer.text == format_kill_footer(
        [(CREATURES["shadow_wolf"]["points"], "caldrin")],
    )
    assert threats.state["active"] is None
    assert _disabled(wolf.view)
    paid = store.state["ledger"][rows:]
    assert [row["delta"] for row in paid] == [CREATURES["shadow_wolf"]["points"]]
    assert [row["reason"] for row in paid] == ["Defeated a Shadow Wolf"]

    # Practice victory: no points, same acknowledgement order, same wording.
    rows = len(store.state["ledger"])
    posted = await threats.post_practice_monster(bot.channel, "shadow_wolf")
    timeline = Timeline()
    bot.channel.timeline = timeline
    practice = Interaction(finder, guild, posted, timeline)
    await threats.on_practice_spell_click(practice, "hex", view=posted.view)
    _assert_ack_before_edit(timeline, followup=True)
    assert practice.response.deferred == {"ephemeral": False, "thinking": True}
    assert practice.response.sent == []
    practice_embed = practice.followup.sent[-1]["embed"]
    assert practice_embed.title == "Practice · The Shadow Wolf is defeated"
    assert practice_embed.footer.text == "Practice · no house points"
    assert finder.mention in (practice_embed.description or "")
    assert posted.id not in threats.practice_fights
    assert _disabled(posted.view)
    assert len(store.state["ledger"]) == rows

    # A second press on that practice fight answers, then greys the row.
    timeline = Timeline()
    bot.channel.timeline = timeline
    again = Interaction(finder, guild, posted, timeline)
    await threats.on_practice_spell_click(again, "hex", view=posted.view)
    _assert_ack_before_edit(timeline, followup=False)
    assert again.response.deferred is None
    assert again.followup.sent == []
    over = again.response.sent[-1]
    assert over["content"] == PRACTICE_OVER
    assert over["ephemeral"] is True
    assert len(store.state["ledger"]) == rows

    # An expired or unknown interaction is a warning, not a debug line.
    logger = logging.getLogger("velmora.dementors")
    level = logger.level
    logger.setLevel(logging.DEBUG)
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger.addHandler(handler)
    try:
        class Expired:
            async def on_public_spell_click(self, interaction, spell_value):
                raise _not_found(10062, "Unknown interaction")

        class Client:
            def get_cog(self, name):
                return Expired() if name == "Dementors" else None

        button = ThreatSpellButton("patronus", row=0)
        click = Interaction(finder, guild, demon, Timeline())
        click.client = Client()
        await button.callback(click)
        view = ThreatSpellView(["patronus"])
        await view.on_error(click, _not_found(10062, "Unknown interaction"), view.children[0])
        _check_logs(records)

        class OtherMissing:
            async def on_public_spell_click(self, interaction, spell_value):
                raise _not_found(10008, "Unknown Message")

        class OtherClient:
            def get_cog(self, name):
                return OtherMissing() if name == "Dementors" else None

        other = ThreatSpellButton("hex", row=0)
        missed = Interaction(finder, guild, demon, Timeline())
        missed.client = OtherClient()
        try:
            await other.callback(missed)
        except discord.NotFound as exc:
            assert exc.code == 10008
        else:
            raise AssertionError("a non-expired NotFound was swallowed")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)

    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
