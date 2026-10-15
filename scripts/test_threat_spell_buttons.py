#!/usr/bin/env python3
"""Wild Threats carry public green spell buttons, solo and Attack wave.

The row is shuffled whenever it is drawn, five buttons to a row. The spell
that works is still CREATURES[id]["weak"]. A wrong press locks that player
out of that monster and posts a random teasing line in the channel. "Not
yet" and "already out" stay ephemeral. There is no /cast command. On an
Attack wave, an auto-pick player treats any button as the right spell.
Solo fights do not auto-pick. The Patronus button banishes a Dementor
whether or not that player has cast /patronus.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-spell-buttons-"))
os.environ.setdefault("DISCORD_TOKEN", "")

import discord  # noqa: E402

from cogs.dementors import (  # noqa: E402
    BUTTONS_PER_ROW,
    CREATURES,
    LOCKED_OUT_MESSAGE,
    MAX_SPELL_BUTTONS,
    SPELL_LABEL,
    SPELL_ORDER,
    WRONG_SPELL_BY_CREATURE,
    WRONG_SPELL_BY_SPELL,
    WRONG_SPELL_LINES,
    Dementors,
    SpellPick,
    ThreatSpellView,
    render_wrong_spell,
    shuffled_spell_order,
    spell_button_slots,
    wrong_spell_announcement,
    wrong_spell_choices,
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


class Channel:
    def __init__(self):
        self.id = 111
        self.sent: list[Msg] = []

    async def send(self, *args, **kwargs):
        embed = kwargs.get("embed")
        if embed is None and args and isinstance(args[0], discord.Embed):
            embed = args[0]
        content = kwargs.get("content")
        if content is None and args and isinstance(args[0], str):
            content = args[0]
        msg = Msg(embed, 100 + len(self.sent), kwargs.get("view"), content, kwargs)
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
    def __init__(self, user: User, guild: Guild, channel_id: int = 111, message=None):
        self.user = user
        self.guild = guild
        self.channel_id = channel_id
        self.response = Response()
        self.message = message


class Patronus:
    def patronus_of(self, user_id: int):
        if user_id == 1:
            return {"animal": "stag"}
        return None

    def art_file_for(self, user_id: int, field: str = "defeat_art"):
        return None


class ReverseRng:
    """Puts the working spell at the other end on every draw, then picks line 0."""

    def shuffle(self, seq):
        seq.reverse()

    def choice(self, seq):
        return seq[0]


def _active_arrival(threats: Dementors) -> Msg:
    mid = threats.state["active"]["message_id"]
    for msg in threats.bot.channel.sent:
        if msg.id == mid:
            return msg
    raise AssertionError("arrival message missing")


def _enabled(view) -> bool:
    return bool(view) and all(not child.disabled for child in view.children)


def _disabled(view) -> bool:
    return bool(view) and view.children and all(child.disabled for child in view.children)


def _check_pools() -> None:
    placeholders = ("{member}", "{spell}", "{creature}")
    assert len(WRONG_SPELL_LINES) >= 10
    pools = [WRONG_SPELL_LINES, *WRONG_SPELL_BY_SPELL.values(), *WRONG_SPELL_BY_CREATURE.values()]
    for pool in pools:
        assert pool
        for line in pool:
            for token in placeholders:
                assert token in line, line
            rendered = render_wrong_spell(line, "<@7>", "Hex", "Stone Golem")
            assert "<@7>" in rendered and "Hex" in rendered and "Stone Golem" in rendered
            assert "{member}" not in rendered

    assert set(WRONG_SPELL_BY_SPELL) == set(SPELL_ORDER)
    assert set(WRONG_SPELL_BY_CREATURE) == set(CREATURES)
    golem = wrong_spell_choices("hex", "stone_golem")
    weaver = wrong_spell_choices("hex", "nightweaver")
    assert any("pebble" in line for line in golem)
    assert all("pebble" not in line for line in weaver)
    assert any("mustache" in line for line in golem)
    assert all("mustache" not in line for line in wrong_spell_choices("ward", "stone_golem"))
    assert len(golem) >= 10

    user = User(7, "Lemur Lovegood")
    rng = random.Random(1)
    seen = {
        wrong_spell_announcement(rng, user, "hex", "stone_golem")
        for _ in range(40)
    }
    assert len(seen) > 1
    for line in seen:
        assert user.mention in line
        assert "Hex" in line
        assert "Stone Golem" in line
        assert line in {
            render_wrong_spell(template, user.mention, "Hex", "Stone Golem")
            for template in wrong_spell_choices("hex", "stone_golem")
        }

    # The two lines the owner quoted are in the generic pool, placeholders and all.
    assert any(line.startswith("{member} cast {spell} at the {creature}.") for line in WRONG_SPELL_LINES)
    assert any("wet sparkler" in line for line in WRONG_SPELL_LINES)


def _check_layout() -> None:
    order = ["bind", "hex", "ward", "disarm", "patronus", "mirror"]
    slots = spell_button_slots(order)
    assert [row for _, row in slots] == [0, 0, 0, 0, 0, 1]
    assert all(row < 5 for _, row in slots)
    wide = spell_button_slots([f"s{i}" for i in range(40)])
    assert len(wide) == MAX_SPELL_BUTTONS == 25
    assert max(row for _, row in wide) == (MAX_SPELL_BUTTONS - 1) // BUTTONS_PER_ROW
    counts: dict[int, int] = {}
    for _, row in wide:
        counts[row] = counts.get(row, 0) + 1
    assert all(n <= BUTTONS_PER_ROW for n in counts.values())

    view = ThreatSpellView(order)
    assert view.timeout is None
    assert [child.label for child in view.children] == ["Bind", "Hex", "Ward", "Disarm", "Patronus", "Mirror"]
    assert [child.row for child in view.children] == [0, 0, 0, 0, 0, 1]
    assert all(child.style == discord.ButtonStyle.success for child in view.children)
    assert all(not child.disabled for child in view.children)
    assert all(str(child.custom_id).startswith("threatspell:") for child in view.children)

    rng = ReverseRng()
    shuffled = shuffled_spell_order(rng)
    assert shuffled == list(reversed(SPELL_ORDER))
    assert shuffled[0] != SPELL_ORDER[0]


async def main() -> None:
    _check_pools()
    _check_layout()

    # The button is the one spell that works.
    assert CREATURES["dementor"]["weak"] == "patronus"
    assert CREATURES["shadow_wolf"]["weak"] == "hex"
    assert CREATURES["wisp_swarm"]["weak"] == "mirror"
    assert CREATURES["storm_sprite"]["weak"] == "ward"
    assert CREATURES["stone_golem"]["weak"] == "bind"
    assert CREATURES["nightweaver"]["weak"] == "disarm"
    assert set(SPELL_ORDER) == {"patronus", "hex", "ward", "disarm", "bind", "mirror"}

    bot = Bot()
    store = Store(bot)
    world = WorldCog(bot)
    threats = Dementors(bot)
    threats.lock = asyncio.Lock()
    threats.rng = ReverseRng()
    bot.cogs = {"Store": store, "World": world, "Dementors": threats, "Patronus": Patronus()}
    finder = User(1, "Lemur Lovegood")
    friend = User(2, "Friend")
    store.state["overrides"][str(finder.id)] = "caldrin"
    store.state["overrides"][str(friend.id)] = "thornmere"
    guild = Guild([finder, friend])

    # /explore posts the buttons on the public monster message.
    assert await threats.try_ambient_spawn(
        111, "stone_golem", finder_id=finder.id, pay_explore_points=True,
    )
    active = threats.state["active"]
    assert active["spell_buttons"] is True
    assert active["locked_out"] == []
    arrival = _active_arrival(threats)
    assert arrival.kwargs.get("ephemeral") is not True
    view = arrival.view
    assert isinstance(view, ThreatSpellView)
    assert [child.label for child in view.children] == [
        SPELL_LABEL[spell] for spell in reversed(SPELL_ORDER)
    ]
    assert all(child.style == discord.ButtonStyle.success for child in view.children)
    assert _enabled(view)
    assert "⏳" in (arrival.embeds[0].description or "")

    threats.state["active"] = None
    # /forage is a fightable threat too, so it gets the same public row.
    assert await threats.try_ambient_spawn(111, "shadow_wolf", finder_id=finder.id)
    assert threats.state["active"]["spell_buttons"] is True
    assert "pay_explore_points" not in threats.state["active"]
    forage_view = bot.channel.sent[-1].view
    assert isinstance(forage_view, ThreatSpellView)
    assert all(child.style == discord.ButtonStyle.success for child in forage_view.children)
    threats.state["active"] = None

    # Back on the golem. Someone else is early: private note, no lockout.
    assert await threats.try_ambient_spawn(
        111, "stone_golem", finder_id=finder.id, pay_explore_points=True,
    )
    early = Interaction(friend, guild, message=_active_arrival(threats))
    await threats.on_public_spell_click(early, "bind")
    assert early.response.sent[-1]["ephemeral"] is True
    assert "stirred" in early.response.sent[-1]["content"]
    assert threats.state["active"]["locked_out"] == []
    assert threats.state["active"]["hits"] == []
    assert _enabled(_active_arrival(threats).view)

    # Auto-pick does not skip a solo monster. A wrong button still locks them out.
    threats._can_auto_cast = lambda user_id: True
    auto = Interaction(finder, guild, message=_active_arrival(threats))
    await threats.on_public_spell_click(auto, "hex")
    assert auto.response.sent[-1].get("ephemeral") is not True
    assert threats.state["active"]["hits"] == []
    assert threats.state["active"]["locked_out"] == [finder.id]
    threats.state["active"]["locked_out"] = []
    threats._can_auto_cast = Dementors._can_auto_cast.__get__(threats, Dementors)

    # The public button miss names them and locks only them out.
    wrong = Interaction(finder, guild, message=_active_arrival(threats))
    await threats.on_public_spell_click(wrong, "hex")
    posted = wrong.response.sent[-1]
    assert posted.get("ephemeral") is not True
    expected = render_wrong_spell(
        wrong_spell_choices("hex", "stone_golem")[0],
        finder.mention,
        "Hex",
        "Stone Golem",
    )
    assert posted["content"] == expected
    assert finder.mention in posted["content"]
    assert "Hex" in posted["content"]
    assert "Stone Golem" in posted["content"]
    assert threats.state["active"]["locked_out"] == [finder.id]
    assert threats.state["active"]["hits"] == []
    assert _enabled(_active_arrival(threats).view)

    # Already out: private, and not another public joke.
    again = Interaction(finder, guild, message=_active_arrival(threats))
    await threats.on_public_spell_click(again, "ward")
    assert again.response.sent[-1]["content"] == LOCKED_OUT_MESSAGE
    assert again.response.sent[-1]["ephemeral"] is True
    assert threats.state["active"]["locked_out"] == [finder.id]
    assert threats.state["active"]["hits"] == []

    # Friend still may not cast during the exclusive window.
    blocked = Interaction(friend, guild, message=_active_arrival(threats))
    await threats.on_public_spell_click(blocked, "bind")
    assert blocked.response.sent[-1]["ephemeral"] is True
    assert friend.id not in threats.state["active"]["locked_out"]

    # Open the window, then the right spell still resolves in public.
    threats.state["active"]["exclusive_until"] = time.time() - 1
    threats.state["active"]["locked_out"] = []
    first = Interaction(finder, guild, message=_active_arrival(threats))
    await threats.on_public_spell_click(first, "bind")
    progress = first.response.sent[-1]["embed"]
    assert progress.title is None
    assert finder.mention in (progress.description or "")
    assert first.response.sent[-1].get("ephemeral") is not True
    assert threats.state["active"]["hits"] == [finder.id]
    assert _enabled(_active_arrival(threats).view)

    rows_before = len(store.state["ledger"])
    second = Interaction(friend, guild, message=_active_arrival(threats))
    await threats.on_public_spell_click(second, "bind")
    assert threats.state["active"] is None
    victory = second.response.sent[-1]["embed"]
    assert victory.title == "✨ The Stone Golem is defeated"
    assert second.response.sent[-1].get("ephemeral") is not True
    assert [row["delta"] for row in store.state["ledger"][rows_before:]] == [4, 4]
    golem_posts = [m for m in bot.channel.sent if m.embeds and "Stone Golem" in (m.embeds[0].title or "")]
    assert golem_posts and _disabled(golem_posts[-1].view)
    assert all(child.style == discord.ButtonStyle.success for child in golem_posts[-1].view.children)

    # One clean hit still defeats a one-person creature and disables the row.
    assert await threats.try_ambient_spawn(
        111, "shadow_wolf", finder_id=finder.id, pay_explore_points=True,
    )
    wolf_msg = _active_arrival(threats)
    wolf = Interaction(finder, guild, message=wolf_msg)
    await threats.on_public_spell_click(wolf, "hex")
    assert threats.state["active"] is None
    assert wolf.response.sent[-1]["embed"].title == "✨ The Shadow Wolf is defeated"
    assert _disabled(wolf_msg.view)

    # A miss does not start the finish clock. A real hit does, and a slip
    # disables the buttons with no reward.
    assert await threats.try_ambient_spawn(
        111, "nightweaver", finder_id=finder.id, pay_explore_points=True,
    )
    slip_rows = len(store.state["ledger"])
    night = _active_arrival(threats)
    bad = Interaction(finder, guild, message=night)
    await threats.on_public_spell_click(bad, "hex")
    assert threats.state["active"]["hits"] == []
    assert "partial_expires_at" not in threats.state["active"]
    assert _enabled(night.view)
    threats.state["active"]["locked_out"] = []
    opened = Interaction(finder, guild, message=night)
    await threats.on_public_spell_click(opened, "disarm")
    assert threats.state["active"]["hits"] == [finder.id]
    assert threats.state["active"]["partial_expires_at"] > time.time()
    message_id = threats.state["active"]["message_id"]
    threats.state["active"]["partial_expires_at"] = time.time() - 1
    await threats._partial_expiry_wait(message_id, time.time() - 1)
    assert threats.state["active"] is None
    assert _disabled(night.view)
    slip = bot.channel.sent[-1].embeds[0]
    assert "slips away" in (slip.description or "")
    assert len(store.state["ledger"]) == slip_rows

    # Dementor: a wrong spell locks them out. The Patronus button itself
    # banishes it, even for a player who has never cast /patronus.
    assert await threats.try_ambient_spawn(
        111, "dementor", finder_id=finder.id, pay_explore_points=True,
    )
    demon = _active_arrival(threats)
    bad_patron = Interaction(friend, guild, message=demon)
    threats.state["active"]["exclusive_until"] = time.time() - 1
    await threats.on_public_spell_click(bad_patron, "hex")
    assert bad_patron.response.sent[-1].get("ephemeral") is not True
    assert "Hex" in bad_patron.response.sent[-1]["content"]
    assert "Dementor" in bad_patron.response.sent[-1]["content"]
    assert threats.state["active"]["locked_out"] == [friend.id]
    assert _enabled(demon.view)
    bare = Interaction(User(9, "No Patronus"), guild, message=demon)
    await threats.on_public_spell_click(bare, "patronus")
    assert threats.state["active"] is None
    bare_reply = bare.response.sent[-1]
    assert bare_reply.get("ephemeral") is not True
    assert bare_reply["embed"].title == "✨ The Dementor is banished"
    assert "silver patronus" in (bare_reply["embed"].description or "").lower()
    assert "/patronus" not in (bare_reply["embed"].description or "")
    assert _disabled(demon.view)

    # Stale buttons after the fight is gone get disabled, and the note is private.
    threats.state["active"] = None
    stale = Interaction(finder, guild, message=demon)
    await threats.on_public_spell_click(stale, "patronus")
    assert stale.response.sent[-1]["ephemeral"] is True
    assert _disabled(demon.view)

    # Staff summon is spawn() with no finder and no explore checklist mark.
    threats.state["active"] = None
    summoned = await threats.spawn(111, "shadow_wolf")
    assert summoned is not None
    assert threats.state["active"]["creature"] == "shadow_wolf"
    assert threats.state["active"]["spell_buttons"] is True
    assert threats.state["active"]["locked_out"] == []
    assert "finder_id" not in threats.state["active"]
    assert "pay_explore_points" not in threats.state["active"]
    summoned_msg = _active_arrival(threats)
    assert isinstance(summoned_msg.view, ThreatSpellView)
    assert all(child.style == discord.ButtonStyle.success for child in summoned_msg.view.children)
    assert [child.row for child in summoned_msg.view.children] == [0, 0, 0, 0, 0, 1]
    summon_miss = Interaction(friend, guild, message=summoned_msg)
    await threats.on_public_spell_click(summon_miss, "ward")
    assert summon_miss.response.sent[-1].get("ephemeral") is not True
    assert "Ward" in summon_miss.response.sent[-1]["content"]
    assert "Shadow Wolf" in summon_miss.response.sent[-1]["content"]
    assert threats.state["active"]["locked_out"] == [friend.id]
    assert _enabled(summoned_msg.view)
    summon_hit = Interaction(finder, guild, message=summoned_msg)
    await threats.on_public_spell_click(summon_hit, "hex")
    assert threats.state["active"] is None
    assert summon_hit.response.sent[-1]["embed"].title == "✨ The Shadow Wolf is defeated"
    assert _disabled(summoned_msg.view)

    # Ambient sightings use the same spawn() and the same buttons.
    threats.state["channel_ids"] = [111]
    threats.state["active"] = None
    sighting = await threats.spawn(creature_id="wisp_swarm")
    assert sighting is not None
    assert threats.state["active"]["creature"] == "wisp_swarm"
    assert threats.state["active"]["spell_buttons"] is True
    assert "finder_id" not in threats.state["active"]
    sighting_msg = _active_arrival(threats)
    assert isinstance(sighting_msg.view, ThreatSpellView)
    wisp = Interaction(finder, guild, message=sighting_msg)
    await threats.on_public_spell_click(wisp, "mirror")
    assert threats.state["active"] is None
    assert wisp.response.sent[-1]["embed"].title == "✨ The Wisp Swarm is defeated"
    assert _disabled(sighting_msg.view)

    # Attack waves use the same public green row. A wrong press locks out.
    # Auto-pick treats any button as the weakness. The Patronus button
    # banishes a Dementor with no saved patronus. The solo monster
    # underneath is a different fight.
    threats.state["active"] = None
    threats.state["event"] = None
    threats._roll_creature = lambda: "shadow_wolf"
    started = time.time()
    threats.state["event"] = {
        "name": "Attack on Velmora",
        "started_at": started,
        "channels": {},
        "tally": {},
        "channel_ids": [111],
        "practice": False,
    }
    payloads = threats._prepare_wave(threats.state["event"])
    assert isinstance(payloads[0]["view"], ThreatSpellView)
    assert all(child.style == discord.ButtonStyle.success for child in payloads[0]["view"].children)
    await threats._deliver_wave(started, payloads)
    wave = threats.state["event"]["channels"]["111"]
    assert wave["creature"] == "shadow_wolf"
    assert wave["spell_buttons"] is True
    assert wave["locked_out"] == []
    wave_msg = next(m for m in bot.channel.sent if m.id == wave["message_id"])
    assert isinstance(wave_msg.view, ThreatSpellView)
    assert _enabled(wave_msg.view)

    miss = Interaction(friend, guild, message=wave_msg)
    await threats.on_public_spell_click(miss, "ward")
    assert miss.response.sent[-1].get("ephemeral") is not True
    assert "Ward" in miss.response.sent[-1]["content"]
    assert "Shadow Wolf" in miss.response.sent[-1]["content"]
    assert threats.state["event"]["channels"]["111"]["locked_out"] == [friend.id]
    assert _enabled(wave_msg.view)

    again = Interaction(friend, guild, message=wave_msg)
    await threats.on_public_spell_click(again, "hex")
    assert again.response.sent[-1]["content"] == LOCKED_OUT_MESSAGE
    assert again.response.sent[-1]["ephemeral"] is True

    threats._can_auto_cast = lambda user_id: user_id == finder.id
    auto_hit = Interaction(finder, guild, message=wave_msg)
    await threats.on_public_spell_click(auto_hit, "bind")
    assert "111" not in threats.state["event"]["channels"]
    assert auto_hit.response.sent[-1]["embed"].title == "✨ The Shadow Wolf is defeated"
    assert finder.id not in wave["locked_out"]
    assert _disabled(wave_msg.view)
    threats._can_auto_cast = Dementors._can_auto_cast.__get__(threats, Dementors)

    # Next wave disables the previous post's buttons.
    old_wave_msg = wave_msg
    started_next = time.time()
    threats.state["event"]["started_at"] = started_next
    # The wolf is already gone; put a stand-in post back so delivery has
    # something to disable, then roll a fresh creature over it.
    threats.state["event"]["channels"] = {
        "111": {
            "creature": "shadow_wolf",
            "message_id": old_wave_msg.id,
            "hits": [],
            "spell_buttons": True,
            "locked_out": [],
        },
    }
    # The defeat already disabled old_wave_msg. Re-enable a fresh view so
    # the sweep is what turns it off.
    old_wave_msg.view = ThreatSpellView(list(SPELL_ORDER))
    threats._roll_creature = lambda: "wisp_swarm"
    nxt = threats._prepare_wave(threats.state["event"])
    await threats._deliver_wave(started_next, nxt)
    assert _disabled(old_wave_msg.view)
    fresh = threats.state["event"]["channels"]["111"]
    assert fresh["creature"] == "wisp_swarm"
    assert fresh["message_id"] != old_wave_msg.id
    assert fresh["locked_out"] == []

    # Dementor wave: a wrong spell locks out. The Patronus button banishes
    # it for a player who has never cast /patronus.
    threats.state["event"] = None
    started_d = time.time()
    threats._roll_creature = lambda: "dementor"
    threats.state["event"] = {
        "name": "Attack on Velmora",
        "started_at": started_d,
        "channels": {},
        "tally": {},
        "channel_ids": [111],
    }
    await threats._deliver_wave(started_d, threats._prepare_wave(threats.state["event"]))
    demon = threats.state["event"]["channels"]["111"]
    demon_msg = next(m for m in bot.channel.sent if m.id == demon["message_id"])
    bad = Interaction(friend, guild, message=demon_msg)
    await threats.on_public_spell_click(bad, "hex")
    assert friend.id in threats.state["event"]["channels"]["111"]["locked_out"]
    assert _enabled(demon_msg.view)
    bare = Interaction(User(9, "No Patronus"), guild, message=demon_msg)
    await threats.on_public_spell_click(bare, "patronus")
    assert "111" not in threats.state["event"]["channels"]
    bare_wave = bare.response.sent[-1]
    assert bare_wave.get("ephemeral") is not True
    assert bare_wave["embed"].title == "✨ The Dementor is banished"
    assert "silver patronus" in (bare_wave["embed"].description or "").lower()
    assert "/patronus" not in (bare_wave["embed"].description or "")
    assert str(9) in threats.state["event"]["tally"]
    assert _disabled(demon_msg.view)

    # A solo monster and a wave in the same channel do not share a lockout.
    threats.state["event"] = None
    assert await threats.try_ambient_spawn(111, "stone_golem", finder_id=finder.id)
    solo_msg = _active_arrival(threats)
    threats.state["active"]["exclusive_until"] = time.time() - 1
    started_both = time.time()
    threats._roll_creature = lambda: "wisp_swarm"
    threats.state["event"] = {
        "name": "Attack on Velmora",
        "started_at": started_both,
        "channels": {},
        "tally": {},
        "channel_ids": [111],
    }
    await threats._deliver_wave(started_both, threats._prepare_wave(threats.state["event"]))
    both = threats.state["event"]["channels"]["111"]
    both_msg = next(m for m in bot.channel.sent if m.id == both["message_id"])
    solo_bad = Interaction(finder, guild, message=solo_msg)
    await threats.on_public_spell_click(solo_bad, "hex")
    assert finder.id in threats.state["active"]["locked_out"]
    assert finder.id not in threats.state["event"]["channels"]["111"]["locked_out"]
    wave_hit = Interaction(friend, guild, message=both_msg)
    await threats.on_public_spell_click(wave_hit, "mirror")
    assert "111" not in threats.state["event"]["channels"]
    assert threats.state["active"]["creature"] == "stone_golem"
    assert _disabled(both_msg.view)
    assert _enabled(solo_msg.view)

    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
