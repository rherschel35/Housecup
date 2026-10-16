#!/usr/bin/env python3
"""/frog show posts a card the player owns, and nothing else.

The subcommand is already on /frog. This locks the contract: autocomplete
only offers owned cards, the post is public and names the shower, and
showing a card does not move it or pay house points.
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-frog-show-"))
os.environ.setdefault("DISCORD_TOKEN", "")

import discord  # noqa: E402

from cogs.frogs import CARDS, Frogs  # noqa: E402
from cogs.store import Store  # noqa: E402


class Bot:
    def __init__(self):
        self.user = type("U", (), {"id": 1})()
        self.cogs = {}

    def get_cog(self, name):
        return self.cogs.get(name)


class Response:
    def __init__(self):
        self.sent: list[dict] = []
        self.deferred = False

    async def send_message(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})

    async def defer(self, **kwargs):
        self.deferred = True
        self.sent.append({"defer": True, **kwargs})


class Followup:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})


class User:
    def __init__(self, uid: int, name: str):
        self.id = uid
        self.display_name = name
        self.mention = f"<@{uid}>"
        self.bot = False


class Interaction:
    def __init__(self, user: User):
        self.user = user
        self.response = Response()
        self.followup = Followup()
        self.guild = type("G", (), {"id": 1})()
        self.channel_id = 42


async def _show(frogs: Frogs, user: User, card: str) -> Interaction:
    interaction = Interaction(user)
    await frogs.frog_show.callback(frogs, interaction, card)
    return interaction


async def main() -> None:
    names = sorted(cmd.name for cmd in Frogs.frog.commands)
    assert names == ["album", "give", "open", "show", "trade"]
    show = Frogs.frog.get_command("show")
    assert show is not None
    assert len(show.description) <= 100
    assert show.parameters[0].name == "card"

    bot = Bot()
    store = Store(bot)
    frogs = Frogs(bot)
    bot.cogs = {"Store": store, "Frogs": frogs}
    owner = User(4, "Collector")
    store.state["overrides"][str(owner.id)] = "caldrin"
    frogs.add_card(owner.id, "orbitcat")

    awards: list = []
    orig = store.record

    def spy_record(**kwargs):
        awards.append(kwargs)
        return orig(**kwargs)

    store.record = spy_record
    assert await frogs.show_autocomplete(Interaction(User(5, "Empty")), "") == []
    snap_frogs = copy.deepcopy(frogs.state)
    snap_ledger = copy.deepcopy(store.state["ledger"])
    snap_totals = copy.deepcopy(store.state["totals"])

    owned = await frogs.show_autocomplete(Interaction(owner), "")
    assert [choice.value for choice in owned] == ["orbitcat"]
    assert owned[0].name == CARDS["orbitcat"]["name"]
    assert await frogs.show_autocomplete(Interaction(owner), "griffin") == []

    missing = await _show(frogs, owner, "griffin")
    assert missing.response.sent[-1]["ephemeral"] is True
    assert missing.followup.sent == []
    assert "don't have" in missing.response.sent[-1]["content"]

    shown = await _show(frogs, owner, "orbitcat")
    assert shown.response.deferred is True
    assert shown.response.sent[-1].get("ephemeral") is not True
    post = shown.followup.sent[-1]
    assert post.get("ephemeral") is not True
    embed = post["embed"]
    assert owner.display_name in embed.title
    assert CARDS["orbitcat"]["name"] in embed.title
    assert CARDS["orbitcat"]["blurb"] in (embed.description or "")
    assert "Common" in (embed.description or "")
    assert embed.image.url == "attachment://frog_orbitcat.png"
    art = post["file"]
    assert art.filename == "frog_orbitcat.png"
    assert art.fp.read(8) == b"\x89PNG\r\n\x1a\n"

    assert awards == []
    assert frogs.state == snap_frogs
    assert store.state["ledger"] == snap_ledger
    assert store.state["totals"] == snap_totals
    assert frogs.counts(owner.id) == {"orbitcat": 1}

    # Give still demands a spare. Show did not loosen that list.
    assert await frogs.give_autocomplete(Interaction(owner), "") == []
    print("OK")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
