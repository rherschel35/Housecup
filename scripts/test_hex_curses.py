#!/usr/bin/env python3
"""Staff-only hex voices rephrase like Cowboy and stay off the shop.

Cowboy deletes a hexed member's chat and reposts a full in-character
rephrase through the relay webhook. Yoda, Rhyme, Dramatic Narrator, Haiku,
Toddler, Superhero, and Sheep use that same path, the same durations, and
the same safety checks. They are effect choices on /staffgame hex cast only —
Hex Scrolls never roll them.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-hex-"))
os.environ.setdefault("DISCORD_TOKEN", "discord-test-token-value")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-secret-token-value")

import discord  # noqa: E402
from discord import app_commands  # noqa: E402

from cogs.hexes import (  # noqa: E402
    EFFECTS,
    Hexes,
    _RHYME_PAIRS,
    _syllables,
    fx_country,
    fx_haiku,
    fx_narrator,
    fx_rhyme,
    fx_sheep,
    fx_superhero,
    fx_toddler,
    fx_yoda,
    scroll_effect_keys,
)
from cogs.marketplace import Marketplace  # noqa: E402
from cogs.staff import HEX_CHOICES, Staff, _complete_effect  # noqa: E402

STAFF_VOICES = (
    "yoda",
    "rhyme",
    "narrator",
    "haiku",
    "toddler",
    "superhero",
    "sheep",
)
ORIGINAL_EFFECTS = (
    "reversed",
    "runon",
    "piglatin",
    "country",
    "stutter",
    "cheerleader",
    "frog",
    "cat",
    "caveman",
    "pirates_tongue",
    "limp_wand",
)
VOICE_FUNCS = {
    "yoda": fx_yoda,
    "rhyme": fx_rhyme,
    "narrator": fx_narrator,
    "haiku": fx_haiku,
    "toddler": fx_toddler,
    "superhero": fx_superhero,
    "sheep": fx_sheep,
}
SECRETS = ("discord-test-token-value", "sk-test-secret-token-value")
CLASS_LUNCH = "I am going to class and then lunch"
LIBRARY = "I don't know where the library is"
HOMEWORK = "Please help me with the homework"
MEET = "Hello friend, I think we should meet tomorrow"


def _last_word(line: str) -> str:
    words = re.findall(r"[A-Za-z']+", line)
    assert words, line
    return words[-1].lower()


def _haiku_counts(text: str) -> list[int]:
    lines = text.splitlines()
    assert len(lines) == 3, text
    return [sum(_syllables(word) for word in line.split()) for line in lines]


def _check(cond: bool, detail: str) -> None:
    if not cond:
        raise AssertionError(detail)


def test_staff_voices_are_castable_and_not_sold() -> None:
    _check(set(ORIGINAL_EFFECTS) <= set(EFFECTS), "an original curse disappeared")
    _check(set(STAFF_VOICES) <= set(EFFECTS), "a new curse is missing from EFFECTS")
    scrolls = scroll_effect_keys()
    _check(list(scrolls) == [key for key in EFFECTS if key not in STAFF_VOICES], scrolls)
    for key in ORIGINAL_EFFECTS:
        _check(key in scrolls, f"{key} fell out of the Hex Scroll pool")
        _check(not EFFECTS[key].get("staff_only"), f"{key} was marked staff-only")
    names = {
        "yoda": "Yoda Curse",
        "rhyme": "Rhyme Curse",
        "narrator": "Dramatic Narrator Curse",
        "haiku": "Haiku Curse",
        "toddler": "Toddler Curse",
        "superhero": "Superhero Curse",
        "sheep": "Sheep Curse",
    }
    for key, name in names.items():
        spell = EFFECTS[key]
        _check(spell["name"] == name, spell["name"])
        _check(spell["func"] is VOICE_FUNCS[key], key)
        _check(spell.get("staff_only") is True, key)
        _check(not spell.get("blocks_wand"), key)
        _check(spell.get("fixed_minutes") is None, key)
        _check(key not in scrolls, f"{key} is buyable via Hex Scroll")
    _check(EFFECTS["country"]["name"] == "Cowboy Curse", "Cowboy was renamed")
    _check(EFFECTS["limp_wand"]["fixed_minutes"] == 60, "Limp Wand duration changed")
    src = inspect.getsource(Marketplace.hexscroll.callback)
    _check("scroll_effect_keys" in src, "hexscroll still rolls every EFFECTS key")
    _check("EFFECTS.keys()" not in src, src)


def test_autocomplete_stays_within_discord_limits() -> None:
    """Effect is autocomplete. A dropdown dies at 25 choices; suggestions never do."""
    _check(len(HEX_CHOICES) == len(EFFECTS), "staff effect list drifted from EFFECTS")
    blank = asyncio.run(_complete_effect(None, ""))
    _check(len(blank) <= 25, f"blank autocomplete returned {len(blank)}")
    _check(len(blank) == min(25, len(HEX_CHOICES)), [c.name for c in blank])
    blank_values = {choice.value for choice in blank}
    if len(HEX_CHOICES) <= 25:
        _check(blank_values == {choice.value for choice in HEX_CHOICES}, blank_values)
    for choice in HEX_CHOICES:
        _check(len(choice.name) <= 100, choice.name)
        _check(len(str(choice.value)) <= 100, choice.value)
        found = asyncio.run(_complete_effect(None, choice.name))
        _check(len(found) <= 25, choice.name)
        _check(any(item.value == choice.value for item in found), choice.name)
    yoda = asyncio.run(_complete_effect(None, "yoda"))
    _check(any(item.value == "yoda" for item in yoda), [c.name for c in yoda])
    cowboy = asyncio.run(_complete_effect(None, "cowboy"))
    _check(any(item.value == "country" for item in cowboy), [c.name for c in cowboy])

    command = Staff.hex_cast
    _check(isinstance(command, app_commands.Command), type(command))
    effect = command.get_parameter("effect")
    _check(effect is not None, "hex cast lost its effect option")
    _check(not effect.choices, "effect became a dropdown; Discord caps those at 25")
    _check(effect.autocomplete is not None, "effect autocomplete was removed")


def test_same_duration_rules_as_cowboy() -> None:
    class Bot:
        def get_cog(self, name):
            return None

    hexes = Hexes(Bot())
    spell, replaced = hexes.apply_hex(
        target_id=42, effect="yoda", duration_minutes=15, cast_by=7,
    )
    _check(spell["name"] == "Yoda Curse", spell)
    _check(replaced is False, "fresh hex looked like a replacement")
    yoda_until = hexes.state["hexed"]["42"]["expires_at"]
    _check(abs(yoda_until - time.time() - 15 * 60) < 5, yoda_until)

    cowboy, _ = hexes.apply_hex(
        target_id=9, effect="country", duration_minutes=15, cast_by=7,
    )
    _check(cowboy["name"] == "Cowboy Curse", cowboy)
    cowboy_until = hexes.state["hexed"]["9"]["expires_at"]
    _check(abs(cowboy_until - yoda_until) < 5, (cowboy_until, yoda_until))

    _, replaced = hexes.apply_hex(
        target_id=42, effect="sheep", duration_minutes=0, cast_by=7,
    )
    _check(replaced is True, "recast did not replace the live hex")
    _check(hexes.state["hexed"]["42"]["expires_at"] is None, "0 minutes should last until lift")
    _check(hexes.active_hex(42)["effect"] == "sheep", hexes.active_hex(42))

    limp, _ = hexes.apply_hex(
        target_id=3, effect="limp_wand", duration_minutes=5, cast_by=7,
    )
    _check(limp["fixed_minutes"] == 60, limp)
    _check(abs(hexes.state["hexed"]["3"]["expires_at"] - time.time() - 3600) < 5, "limp")


def test_relay_path_is_unchanged() -> None:
    """New voices are ordinary chat funcs, so they inherit Cowboy's repost and filters."""
    src = inspect.getsource(Hexes.on_message)
    for needle in (
        "should_block",
        'effect["func"]',
        "message.delete",
        "cursed[:2000]",
        "avatar_url",
        "display_name",
    ):
        _check(needle in src, f"relay no longer has {needle}")


def test_meaning_stays_clear() -> None:
    samples = {
        CLASS_LUNCH: ("class", "lunch"),
        LIBRARY: ("library",),
        HOMEWORK: ("homework",),
        MEET: ("tomorrow", "meet"),
    }
    for seed in range(12):
        random.seed(seed)
        for text, needles in samples.items():
            for name, func in VOICE_FUNCS.items():
                out = func(text)
                _check(bool(out and out.strip()), f"{name} blank for {text!r}")
                _check(out != text, f"{name} did not rephrase {text!r} -> {out!r}")
                low = out.lower()
                for needle in needles:
                    _check(needle in low, f"{name} lost {needle!r} in {out!r}")
                for secret in SECRETS:
                    _check(secret not in out, f"{name} printed a token")

        random.seed(seed)
        yoda = fx_yoda("I am hungry")
        _check(re.search(r"hungry, I am", yoda, re.I), yoda)
        question = fx_yoda("Are you going to class?")
        _check(re.search(r"going to class, are you", question, re.I), question)
        library = fx_yoda(LIBRARY)
        _check(re.search(r"library is, I do not", library, re.I), library)

        random.seed(seed)
        couplet = fx_rhyme(CLASS_LUNCH).splitlines()
        _check(len(couplet) == 2, couplet)
        pair = (_last_word(couplet[0]), _last_word(couplet[1]))
        _check(pair in _RHYME_PAIRS, pair)
        _check("class" in couplet[0].lower() and "class" in couplet[1].lower(), couplet)

        random.seed(seed)
        narrated = fx_narrator(CLASS_LUNCH)
        _check(re.search(r"\bthey\b", narrated, re.I), narrated)
        _check(not re.search(r"\bI\b", narrated), narrated)
        _check(not re.search(r"\bI'm\b", narrated, re.I), narrated)
        quoted = fx_narrator(HOMEWORK)
        _check("homework" in quoted.lower(), quoted)
        _check(not re.search(r"\bI\b", quoted), quoted)

        haiku = fx_haiku(CLASS_LUNCH)
        _check(_haiku_counts(haiku) == [5, 7, 5], haiku)
        _check("class" in haiku.lower() and "lunch" in haiku.lower(), haiku)
        _check("ah ah" not in haiku.lower(), haiku)
        loved = fx_yoda("I love this game but I am tired")
        _check(re.search(r"this game, I love", loved, re.I), loved)
        _check(re.search(r"tired, I am", loved, re.I), loved)
        _check(_haiku_counts(fx_haiku("")) == [5, 7, 5], fx_haiku(""))
        long = " ".join(["class"] * 40 + ["library"])
        packed = fx_haiku(long)
        _check(_haiku_counts(packed) == [5, 7, 5], packed)
        _check("class" in packed.lower() and "library" in packed.lower(), packed)

        random.seed(seed)
        baby = fx_toddler(CLASS_LUNCH).lower()
        _check("me" in baby and "gonna" in baby, baby)
        please = fx_toddler(HOMEWORK).lower()
        _check("pwease" in please and "homework" in please, please)

        random.seed(seed)
        hero = fx_superhero(CLASS_LUNCH)
        _check(hero.startswith("**"), hero)
        _check("!" in hero and "class" in hero.lower(), hero)

        random.seed(seed)
        sheep = fx_sheep(CLASS_LUNCH).lower()
        _check("baaa" in sheep and "class" in sheep and "lunch" in sheep, sheep)
        _check(sheep.count("baaa") >= 1, sheep)

        random.seed(seed)
        cowboy = fx_country("hello friend").lower()
        _check("howdy" in cowboy or "partner" in cowboy or "pardner" in cowboy, cowboy)


def test_empty_and_secret_inputs_stay_safe() -> None:
    for func in VOICE_FUNCS.values():
        for raw in ("", "   ", "...", "???"):
            out = func(raw)
            _check(bool(out and out.strip()), f"{func.__name__} emptied {raw!r}")
            _check(re.search(r"[A-Za-z]", out), out)
            for secret in SECRETS:
                _check(secret not in out, func.__name__)
        leaked = func(f"hello {SECRETS[0]} there")
        # The member typed the secret; the rephrase may keep their words.
        # The curse itself must not go looking up env tokens to add new ones.
        _check(SECRETS[1] not in leaked, leaked)


class _Hook:
    def __init__(self, sent: list):
        self.sent = sent

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class _Channel(discord.TextChannel):
    def __init__(self):
        self.id = 555
        self.name = "hall"


def test_repost_uses_the_same_webhook_relay() -> None:
    class Bot:
        def get_cog(self, name):
            return None

    hexes = Hexes(Bot())
    hexes.apply_hex(target_id=42, effect="haiku", duration_minutes=10, cast_by=1)
    sent: list = []
    deleted: list = []

    async def relay(channel):
        return _Hook(sent)

    async def delete():
        deleted.append(True)

    author = type("Author", (), {})()
    author.bot = False
    author.id = 42
    author.display_name = "Ada"
    author.display_avatar = type("Avatar", (), {"url": "https://example.com/a.png"})()
    message = type("Message", (), {})()
    message.guild = object()
    message.author = author
    message.webhook_id = None
    message.content = CLASS_LUNCH
    message.reference = None
    message.attachments = []
    message.channel = _Channel()
    message.delete = delete
    hexes._relay_webhook = relay

    asyncio.run(hexes.on_message(message))
    _check(deleted == [True], deleted)
    _check(len(sent) == 1, sent)
    body = sent[0]["content"]
    _check(_haiku_counts(body) == [5, 7, 5], body)
    _check("class" in body.lower() and "lunch" in body.lower(), body)
    _check(sent[0]["username"] == "Ada", sent[0])
    _check(sent[0]["avatar_url"] == "https://example.com/a.png", sent[0])
    _check(len(body) <= 2000, len(body))

    # Limp Wand still leaves chat alone.
    hexes.apply_hex(target_id=42, effect="limp_wand", duration_minutes=5, cast_by=1)
    deleted.clear()
    sent.clear()
    asyncio.run(hexes.on_message(message))
    _check(deleted == [], deleted)
    _check(sent == [], sent)


def main() -> None:
    test_staff_voices_are_castable_and_not_sold()
    test_autocomplete_stays_within_discord_limits()
    test_same_duration_rules_as_cowboy()
    test_relay_path_is_unchanged()
    test_meaning_stays_clear()
    test_empty_and_secret_inputs_stay_safe()
    test_repost_uses_the_same_webhook_relay()
    print("hex curse tests ok")


if __name__ == "__main__":
    main()
