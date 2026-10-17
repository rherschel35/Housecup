#!/usr/bin/env python3
"""/secrets lists what you've found, and a repeat phrase gets a reaction.

Wrong guesses and secrets whose conditions aren't met stay silent.
A phrase that can still be claimed is still awarded, even if the same
message also contains a secret that's already used.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-secrets-"))
os.environ.setdefault("DISCORD_TOKEN", "")

from cogs.velmora_channels import RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS  # noqa: E402
from cogs.world import (  # noqa: E402
    ALREADY_FOUND_EMOJI,
    HERE_MINUTES,
    PLACES_SECRET_NOTE,
    WorldCog,
)
from cogs.world_engine import Ctx, Place, blank_student, secret_label  # noqa: E402

CHI = ZoneInfo("America/Chicago")
NIGHT = dt.datetime(2026, 10, 16, 22, 0, tzinfo=CHI).timestamp()
DAY = dt.datetime(2026, 10, 16, 12, 0, tzinfo=CHI).timestamp()
GARDEN_CHANNEL = 555
SHED = "i knock on the gardener's shed"


class Bot:
    def __init__(self):
        self.user = type("U", (), {"id": 1})()
        self.cogs = {}

    def get_cog(self, name):
        return self.cogs.get(name)


class User:
    def __init__(self, uid: int, name: str = "Rook"):
        self.id = uid
        self.display_name = name
        self.bot = False
        self.mention = f"<@{uid}>"
        self.roles = []


class Response:
    def __init__(self):
        self.sent: list[dict] = []

    def is_done(self):
        return bool(self.sent)

    async def send_message(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})


class Interaction:
    def __init__(self, user: User):
        self.user = user
        self.response = Response()
        self.guild = object()
        self.channel_id = GARDEN_CHANNEL


class Message:
    def __init__(self, content: str, author: User, channel_id: int = GARDEN_CHANNEL):
        self.content = content
        self.author = author
        self.guild = object()
        self.channel = type("Ch", (), {"id": channel_id})()
        self.reactions: list[str] = []
        self.replies: list[dict] = []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def reply(self, **kwargs):
        self.replies.append(kwargs)


def _check(cond, detail) -> None:
    if not cond:
        raise AssertionError(detail)


def _embed_text(embed) -> str:
    parts = [embed.description or "", embed.author.name or ""]
    footer = getattr(embed.footer, "text", None) or ""
    parts.append(footer)
    for field in embed.fields:
        parts.append(field.name)
        parts.append(field.value)
    return "\n".join(parts)


def _garden_pair():
    content = {
        "name": "the Garden",
        "secrets": [
            {"id": "old_gate", "phrases": ["open the gate"], "once": "user", "response": "old"},
            {"id": "new_gate", "phrases": ["open the gate"], "once": "user", "response": "new"},
            {"id": "night_song", "phrases": ["sing to the flowers"], "once": "user", "time": "night", "response": "night"},
            {"id": "trade", "phrases": ["trade with the frog"], "once": "none", "event": "frog", "response": "FROG_TRADE"},
            {"id": "door", "phrases": ["knock on the door"], "once": "event", "event": "door", "response": "door"},
            {"id": "owl", "phrases": ["bow to the owl"], "once": "server", "response": "owl"},
        ],
    }
    return Place("garden", content)


def test_classify_keeps_claim_silence_and_used_apart() -> None:
    place = _garden_pair()
    student = blank_student("Rook")
    student["flags"] = ["garden:old_gate"]
    ctx = Ctx(student=student, place="garden", house="thornmere", now=DAY)
    state = {"server_found": []}

    kind, sec = place.classify_secret("open the gate", ctx, state, None)
    _check(kind == "claim" and sec["id"] == "new_gate", (kind, sec))
    _check(place.match_secret("open the gate", ctx, state, None)["id"] == "new_gate", "match")
    _check(place.already_found_secret("open the gate", ctx, state, None) is False, "used blocked claim")

    student["flags"].append("garden:new_gate")
    kind, sec = place.classify_secret("open the gate please", ctx, state, None)
    _check(kind == "used" and sec is None, (kind, sec))
    _check(place.match_secret("open the gate please", ctx, state, None) is None, "match used")
    _check(place.already_found_secret("open the gate please", ctx, state, None) is True, "already")

    kind, sec = place.classify_secret("hello there", ctx, state, None)
    _check(kind is None and sec is None, (kind, sec))
    _check(place.already_found_secret("hello there", ctx, state, None) is False, "wrong guess")

    # Found earlier, but it isn't night, so the conditions fail and it stays silent.
    student["flags"].append("garden:night_song")
    kind, _sec = place.classify_secret("sing to the flowers", ctx, state, None)
    _check(kind is None, kind)
    night = Ctx(student=student, place="garden", house="thornmere", now=NIGHT)
    kind, _sec = place.classify_secret("sing to the flowers", night, state, None)
    _check(kind == "used", kind)

    # Repeatable secrets are never "already found".
    frog_ctx = Ctx(student=student, place="garden", event="frog", now=DAY)
    frog_event = {"id": "aa", "key": "frog"}
    kind, sec = place.classify_secret("trade with the frog", frog_ctx, state, frog_event)
    _check(kind == "claim" and sec["id"] == "trade", (kind, sec))
    kind, _sec = place.classify_secret("trade with the frog", ctx, state, None)
    _check(kind is None, "frog without its event")

    # Event secrets only count as used for this event instance.
    event = {"id": "ev1", "key": "door"}
    door_ctx = Ctx(student=student, place="garden", event="door", now=DAY)
    kind, sec = place.classify_secret("knock on the door", door_ctx, state, event)
    _check(kind == "claim" and sec["id"] == "door", (kind, None if sec is None else sec["id"]))
    student["flags"].append("garden:door@ev1")
    kind, _sec = place.classify_secret("knock on the door", door_ctx, state, event)
    _check(kind == "used", kind)
    other = {"id": "ev2", "key": "door"}
    kind, sec = place.classify_secret("knock on the door", door_ctx, state, other)
    _check(kind == "claim" and sec["id"] == "door", "new event instance")
    quiet = Ctx(student=student, place="garden", now=DAY)
    kind, _sec = place.classify_secret("knock on the door", quiet, state, None)
    _check(kind is None, "event secret while the event is down")

    # Someone else already took a server secret. Conditions met → used.
    state = {"server_found": ["garden:owl"]}
    kind, _sec = place.classify_secret("bow to the owl", ctx, state, None)
    _check(kind == "used", kind)

    names = place.found_secret_names(student)
    _check("Old Gate" in names and "New Gate" in names and "Door" in names, names)
    _check("Owl" not in names, names)
    _check(all("gate" not in n.lower() or " " in n for n in names), names)
    _check("open the gate" not in " ".join(names).lower(), names)


def test_real_garden_secret_rules() -> None:
    world = WorldCog(Bot())
    garden = world.world.places["garden"]
    student = blank_student("Rook")
    day = Ctx(student=student, place="garden", house="caldrin", now=DAY)
    night = Ctx(student=student, place="garden", house="thornmere", now=NIGHT)
    state = {"server_found": []}

    _check(garden.match_secret(SHED, day, state, None)["id"] == "gardener_shed", "shed claim")
    student["flags"] = ["garden:gardener_shed"]
    _check(garden.match_secret(SHED, day, state, None) is None, "shed match after")
    _check(garden.already_found_secret("I knock on the gardener's shed!!!", day, state, None), "punct")

    # Right phrase, wrong house: silent, even after it's been found.
    student["flags"].append("garden:thornmere_howl")
    _check(garden.classify_secret("i howl at the brambles", day, state, None) == (None, None), "wrong house day")
    _check(garden.classify_secret("i howl at the brambles", night, state, None)[0] == "used", "right house night")

    # Crystal secret with the phrase but without the item stays silent.
    _check(
        garden.classify_secret("i place a crystal in the stone hand", day, state, None) == (None, None),
        "missing item",
    )
    student["flags"].append("garden:statue_offer")
    _check(
        garden.already_found_secret("i place a crystal in the stone hand", day, state, None) is False,
        "found but conditions unmet",
    )


def test_here_window_minutes() -> None:
    world = WorldCog(Bot())
    student = blank_student("Rook")
    now = 1_700_000_000
    _check(world.here_window(student, now) == (None, None), "no visit")
    student["here"] = {"place": "garden", "at": now - 20 * 60}
    _check(world.here_window(student, now) == ("garden", 40), world.here_window(student, now))
    student["here"]["at"] = now - (HERE_MINUTES * 60 - 1)
    _check(world.here_window(student, now) == ("garden", 1), world.here_window(student, now))
    student["here"]["at"] = now - (HERE_MINUTES * 60 - 61)
    _check(world.here_window(student, now) == ("garden", 2), world.here_window(student, now))
    student["here"]["at"] = now - HERE_MINUTES * 60
    _check(world.here_window(student, now) == (None, None), "expired")
    _check(world.where(student) is None, "where expired")
    _check(secret_label("gardener_shed") == "Gardener Shed", secret_label("gardener_shed"))


def test_secrets_command_hides_unfound_names_and_phrases() -> None:
    bot = Bot()
    world = WorldCog(bot)
    user = User(7, "Rook")
    now = 1_700_000_000
    student = blank_student("Rook")
    student["flags"] = ["garden:gardener_shed", "library:veyren_silent_read@old"]
    student["here"] = {"place": "library", "at": now - 15 * 60}
    world.state["students"][str(user.id)] = student

    # Freeze the window the command reads.
    original = world.here_window

    def frozen(s, now=None):
        return original(s, 1_700_000_000)

    world.here_window = frozen
    interaction = Interaction(user)
    before = set(world.state["students"])
    asyncio.run(world.secrets.callback(world, interaction))
    _check(set(world.state["students"]) == before, "secrets must not create a student")
    sent = interaction.response.sent[-1]
    _check(sent.get("ephemeral") is True, sent)
    embed = sent["embed"]
    _check(embed.author.name == "🗝️ Rook's secrets", embed.author.name)
    james = world.secrets_embed(User(8, "James"), student)
    _check(james.author.name == "🗝️ James' secrets", james.author.name)
    text = _embed_text(embed)
    _check("45 minutes" in embed.description, embed.description)
    _check("Library" in embed.description, embed.description)
    _check("Gardener Shed" in text, text)
    _check("Veyren Silent Read" in text, text)
    _check("Thornmere Howl" not in text, text)
    _check("None yet." in text, text)

    phrases = []
    for P in world.world.places.values():
        for sec in P.c.get("secrets", []):
            phrases.extend(sec.get("phrases") or [])
            if sec["id"] not in ("gardener_shed", "veyren_silent_read"):
                _check(secret_label(sec["id"]) not in text, secret_label(sec["id"]))
    for phrase in phrases:
        _check(phrase.lower() not in text.lower(), phrase)

    fields = [(f.name, f.value) for f in embed.fields]
    _check(len(fields) == 5, fields)
    order = ["Garden", "Library", "Dungeons", "Observatory", "Forbidden Woods"]
    for (name, _value), needle in zip(fields, order):
        _check(needle in name, name)
    garden_total = len(world.world.places["garden"].c["secrets"])
    library_total = len(world.world.places["library"].c["secrets"])
    _check(f"1/{garden_total}" in fields[0][0], fields[0][0])
    _check(f"1/{library_total}" in fields[1][0], fields[1][0])
    _check(fields[2][0].endswith("/9") or "/9" in fields[2][0], fields[2])
    _check("Wear the Keeper's Talisman" in (embed.footer.text or ""), embed.footer.text)

    class Talisman:
        def keepers_talisman_note(self, member):
            return "🌿 Garden — **3** still hidden"

    bot.cogs["Adornments"] = Talisman()
    interaction = Interaction(user)
    asyncio.run(world.secrets.callback(world, interaction))
    embed = interaction.response.sent[-1]["embed"]
    text = _embed_text(embed)
    _check("still hidden" in text, text)
    _check("Keeper's Talisman" in text, text)
    _check("i howl at the brambles" not in text.lower(), text)
    _check(interaction.response.sent[-1].get("ephemeral") is True, "talisman still private")

    cmd = WorldCog.__dict__["secrets"]
    _check(cmd.name == "secrets", cmd.name)
    _check(list(cmd.parameters) == [], list(cmd.parameters))
    _check(len(cmd.description) <= 100, cmd.description)
    adorn = (ROOT / "cogs" / "adornments.py").read_text(encoding="utf-8")
    _check('name="secrets"' not in adorn, "duplicate /secrets on adornments")


def test_talisman_note_counts_without_naming_secrets() -> None:
    from cogs.adornments import Adornments

    bot = Bot()
    world = WorldCog(bot)
    adorn = Adornments(bot)
    bot.cogs["World"] = world
    bot.cogs["Adornments"] = adorn
    user = User(11, "Rook")
    _check(adorn.keepers_talisman_note(user) is None, "no talisman")
    rec = adorn.rec(user.id)
    rec["owned"]["keepers_talisman"] = 1
    rec["worn"]["talisman"] = "keepers_talisman"
    world.state["students"][str(user.id)] = blank_student("Rook")
    world.state["students"][str(user.id)]["flags"] = ["garden:gardener_shed"]
    note = adorn.keepers_talisman_note(user)
    _check(note and "still hidden" in note, note)
    _check("Gardener Shed" not in note and "i knock" not in note.lower(), note)
    _check(adorn.has_perk(user.id, "secret_count"), "perk")


def test_places_explains_the_secret_rules() -> None:
    world = WorldCog(Bot())
    interaction = Interaction(User(3))
    asyncio.run(world.places.callback(world, interaction))
    sent = interaction.response.sent[-1]
    text = sent["embed"].description
    _check(PLACES_SECRET_NOTE in text, text)
    lowered = text.lower()
    for bit in (
        "last hour",
        "house",
        "time of day",
        "reputation",
        "items",
        "earlier secrets",
        "event",
        "won't trigger again",
        "reacts",
        "`/secrets`",
    ):
        _check(bit in lowered or bit in text, bit)
    _check("i howl" not in lowered, "places must not teach a phrase")


async def _listen(world: WorldCog, message: Message) -> None:
    await world.on_message(message)


def test_on_message_reacts_only_when_already_found() -> None:
    world = WorldCog(Bot())
    world.state["channels"]["garden"] = GARDEN_CHANNEL
    user = User(9, "Rook")
    world.student(user)
    s = world.state["students"][str(user.id)]
    s["here"] = {"place": "garden", "at": time.time()}

    first = Message(SHED, user)
    asyncio.run(_listen(world, first))
    _check(first.reactions == [], first.reactions)
    _check(len(first.replies) == 1, first.replies)
    _check(s.get("items", {}).get("map_scrap") == 1, s.get("items"))
    _check("garden:gardener_shed" in s.get("flags", []), s.get("flags"))

    again = Message("I knock on the gardener's shed!!!", user)
    items_before = dict(s.get("items", {}))
    flags_before = list(s.get("flags", []))
    asyncio.run(_listen(world, again))
    _check(again.reactions == [ALREADY_FOUND_EMOJI], again.reactions)
    _check(again.replies == [], again.replies)
    _check(s.get("items", {}) == items_before, s.get("items"))
    _check(s.get("flags", []) == flags_before, s.get("flags"))

    wrong = Message("just chatting about homework", user)
    asyncio.run(_listen(world, wrong))
    _check(wrong.reactions == [] and wrong.replies == [], (wrong.reactions, wrong.replies))

    unmet = Message("i place a crystal in the stone hand", user)
    asyncio.run(_listen(world, unmet))
    _check(unmet.reactions == [] and unmet.replies == [], "conditions unmet")
    s["flags"].append("garden:statue_offer")
    unmet_again = Message("i place a crystal in the stone hand", user)
    asyncio.run(_listen(world, unmet_again))
    _check(unmet_again.reactions == [] and unmet_again.replies == [], "found but blocked")

    elsewhere = Message(SHED, user, channel_id=999)
    asyncio.run(_listen(world, elsewhere))
    _check(elsewhere.reactions == [] and elsewhere.replies == [], "wrong channel")

    restricted = next(iter(RESTRICTED_OPEN_LOUNGE_CHANNEL_IDS))
    lounge = Message(SHED, user, channel_id=restricted)
    asyncio.run(_listen(world, lounge))
    _check(lounge.reactions == [] and lounge.replies == [], "restricted lounge")

    s["here"] = {"place": "garden", "at": time.time() - HERE_MINUTES * 60 - 5}
    stale = Message(SHED, user)
    asyncio.run(_listen(world, stale))
    _check(stale.reactions == [] and stale.replies == [], "window closed")


def main() -> None:
    test_classify_keeps_claim_silence_and_used_apart()
    test_real_garden_secret_rules()
    test_here_window_minutes()
    test_secrets_command_hides_unfound_names_and_phrases()
    test_talisman_note_counts_without_naming_secrets()
    test_places_explains_the_secret_rules()
    test_on_message_reacts_only_when_already_found()
    print("secrets found tests ok")


if __name__ == "__main__":
    main()
