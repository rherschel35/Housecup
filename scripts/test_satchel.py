#!/usr/bin/env python3
"""/satchel lists brewed potions with the same names and counts as /potions.

World items stay in the satchel. Potion ingredients and everything else are
separate sections, and looking does not brew, drink, spend, or pay points.
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
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="housecup-satchel-"))
os.environ.setdefault("DISCORD_TOKEN", "")

from cogs.potions import RECIPES  # noqa: E402
from cogs.world import GREEN, POTION_INGREDIENT_IDS, WorldCog  # noqa: E402
from cogs.world_engine import blank_student  # noqa: E402


class Bot:
    def __init__(self):
        self.user = type("U", (), {"id": 1})()
        self.cogs = {}

    def get_cog(self, name):
        return self.cogs.get(name)


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
        self.bot = False


class Interaction:
    def __init__(self, user: User):
        self.user = user
        self.response = Response()
        self.guild = type("G", (), {"id": 1})()
        self.channel_id = 42


class PotionsStub:
    """Just the inventory /potions reads. record() would touch brew progress."""

    def __init__(self):
        self.state = {"players": {}}
        self.record_calls = 0

    def record(self, user_id):
        self.record_calls += 1
        raise AssertionError("satchel must not open the potions record")


def _potions_satchel_lines(inventory: dict) -> list[str]:
    """Same expression /potions uses for its Satchel field, unknown keys aside."""
    return [
        f"{RECIPES[k]['emoji']} {RECIPES[k]['name']} ×{n}"
        for k, n in inventory.items()
        if n > 0 and k in RECIPES
    ]


def _section(text: str, title: str) -> str:
    marker = f"**{title}**"
    start = text.index(marker)
    rest = text[start + len(marker):]
    nxt = len(rest)
    for other in ("**Potions**", "**Ingredients**", "**Items**"):
        if other == marker:
            continue
        at = rest.find(other)
        if at != -1:
            nxt = min(nxt, at)
    return rest[:nxt]


async def _open(world: WorldCog, user: User) -> Interaction:
    interaction = Interaction(user)
    await world.satchel.callback(world, interaction)
    return interaction


async def main() -> None:
    assert WorldCog.satchel.name == "satchel"
    assert WorldCog.satchel.description == "Look inside your satchel. Only you can see it."
    assert "felix_felicis" in RECIPES
    assert "silverleaf" in POTION_INGREDIENT_IDS
    assert "moth_dust" in POTION_INGREDIENT_IDS
    assert "stardust_ledger_page" in POTION_INGREDIENT_IDS
    assert "strange_key" not in POTION_INGREDIENT_IDS
    assert "descent_sigil" not in POTION_INGREDIENT_IDS
    assert "map_scrap" not in POTION_INGREDIENT_IDS

    bot = Bot()
    world = WorldCog(bot)
    potions = PotionsStub()
    bot.cogs = {"World": world, "Potions": potions}

    empty_user = User(3, "Empty")
    empty = await _open(world, empty_user)
    assert empty.response.sent == [{
        "content": "🎒 Your satchel is empty. Go and explore somewhere.",
        "ephemeral": True,
    }]
    assert str(empty_user.id) not in potions.state["players"]

    owner = User(7, "Rook")
    student = blank_student(owner.display_name)
    student["items"] = {
        "silverleaf": 2,
        "moth_dust": 1,
        "stardust_ledger_page": 1,
        "strange_key": 1,
        "descent_sigil": 3,
        "map_scrap": 1,
    }
    world.state["students"][str(owner.id)] = student
    inventory = {
        "felix_felicis": 2,
        "beastcallers_draught": 1,
        "phoenix_tears": 0,
        "not_a_potion": 4,
    }
    potions.state["players"][str(owner.id)] = {
        "rep_xp": 40,
        "discovered": ["felix_felicis"],
        "inventory": inventory,
        "active": [{"effect": "liquid_luck"}],
        "felix_free_at": 123,
        "brew_day": {"date": "2026-10-16", "count": 4, "bonus": False},
    }
    snap_student = copy.deepcopy(world.state["students"][str(owner.id)])
    snap_potions = copy.deepcopy(potions.state)

    shown = await _open(world, owner)
    assert potions.record_calls == 0
    assert world.state["students"][str(owner.id)] == snap_student
    assert potions.state == snap_potions

    sent = shown.response.sent[-1]
    assert sent["content"] is None
    assert sent["ephemeral"] is True
    embed = sent["embed"]
    assert embed.color.value == GREEN
    assert embed.author.name == "🎒 Rook's satchel"
    assert embed.fields == []
    text = embed.description
    assert text.startswith("**Potions**\n")
    assert text.index("**Potions**") < text.index("**Ingredients**") < text.index("**Items**")

    expected_potions = _potions_satchel_lines(inventory)
    assert expected_potions == [
        f"{RECIPES['felix_felicis']['emoji']} Felix Felicis ×2",
        f"{RECIPES['beastcallers_draught']['emoji']} Beastcaller's Draught ×1",
    ]
    potion_section = _section(text, "Potions")
    assert potion_section.strip() == "\n".join(expected_potions)
    assert "Phoenix Tears" not in text
    assert "not_a_potion" not in text
    assert "*Nothing brewed yet.*" not in text

    ingredients = _section(text, "Ingredients")
    items = _section(text, "Items")
    leaf = world.world.items["silverleaf"]
    dust = world.world.items["moth_dust"]
    page = world.world.items["stardust_ledger_page"]
    key = world.world.items["strange_key"]
    sigil = world.world.items["descent_sigil"]
    scrap = world.world.items["map_scrap"]
    assert f"{page['emoji']} **{page['name']}** ×1 - *{page['desc']}*" in ingredients
    assert f"{dust['emoji']} **{dust['name']}** ×1 - *{dust['desc']}*" in ingredients
    assert f"{leaf['emoji']} **{leaf['name']}** ×2 - *{leaf['desc']}*" in ingredients
    assert ingredients.index("**Very Rare**") < ingredients.index("**Common**")
    assert ingredients.index(dust["name"]) < ingredients.index(leaf["name"])
    assert "Strange Key" not in ingredients
    assert "Vault Sigil" not in ingredients
    assert "Map Scrap" not in ingredients

    assert f"{sigil['emoji']} **{sigil['name']}** ×3 - *{sigil['desc']}*" in items
    assert f"{key['emoji']} **{key['name']}** ×1 - *{key['desc']}*" in items
    assert f"{scrap['emoji']} **{scrap['name']}** ×1 - *{scrap['desc']}*" in items
    assert items.index("**Very Rare**") < items.index("**Rare**")
    assert items.index(sigil["name"]) < items.index(key["name"])
    assert "Silverleaf" not in items
    assert "Moth Dust" not in items
    assert "Stardust Ledger Page" not in items

    assert embed.footer.text == "12 things carried · /use or /offer them where you are."

    # Brewed potions alone are still a satchel. World items are untouched.
    brewer = User(8, "Brewer")
    potions.state["players"][str(brewer.id)] = {"inventory": {"whiskers_luck_draught": 2}}
    only = await _open(world, brewer)
    only_text = only.response.sent[-1]["embed"].description
    assert only.response.sent[-1]["ephemeral"] is True
    assert "empty" not in only_text.lower()
    assert f"{RECIPES['whiskers_luck_draught']['emoji']} Whisker's Luck Draught ×2" in only_text
    assert "**Ingredients**" not in only_text
    assert "**Items**" not in only_text
    assert only.response.sent[-1]["embed"].footer.text == (
        "2 things carried · /use or /offer them where you are."
    )
    assert world.state["students"][str(brewer.id)]["items"] == {}

    # No brewed potions still gets the Potions section, from the same empty inventory.
    carrier = User(9, "Carrier")
    world.state["students"][str(carrier.id)] = blank_student(carrier.display_name)
    world.state["students"][str(carrier.id)]["items"] = {"strange_key": 1}
    plain = await _open(world, carrier)
    plain_text = plain.response.sent[-1]["embed"].description
    assert plain_text.startswith("**Potions**\n*Nothing brewed yet.*")
    assert "**Items**" in plain_text
    assert "Strange Key** ×1" in plain_text
    assert "**Ingredients**" not in plain_text
    assert plain.response.sent[-1]["embed"].footer.text == (
        "1 things carried · /use or /offer them where you are."
    )

    # A missing Potions cog still shows what the world satchel is holding.
    bot.cogs.pop("Potions")
    fallback = await _open(world, owner)
    fallback_text = fallback.response.sent[-1]["embed"].description
    assert fallback_text.startswith("**Potions**\n*Nothing brewed yet.*")
    assert "Silverleaf** ×2" in fallback_text
    assert "Felix" not in fallback_text
    assert world.state["students"][str(owner.id)]["items"] == snap_student["items"]

    print("OK")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
