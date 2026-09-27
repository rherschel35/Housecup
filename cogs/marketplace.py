"""
The Velmora Marketplace — spend (and sell for) house points.

    /market browse              - the catalogue and your scrolls / sell room today
    /market buy ingredient qty  - common 3 pts, uncommon 6 pts (rare+ not sold)
    /market sell ingredient     - trade in 3 of the same common/uncommon for 3 pts
    /market scroll              - buy a Hex Scroll (30 pts)
    /market title               - buy an exclusive shop title (50 pts)
    /market room                - Room of Requirement (100 pts); pings @headmasters
    /hexscroll member           - cast one owned Hex Scroll (30 min, random effect)

Every spend deducts from the member's season contribution AND the house total
(same honesty as /bean). Sell earnings go to both, capped at 21 pts/day.
Rare, very_rare, and legendary ingredients cannot be bought or sold.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.marketplace")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "marketplace.json"

HEADMASTER_ROLE_NAME = "headmasters"

BUY_PRICE = {"common": 3, "uncommon": 6}
SELL_SET_SIZE = 3
SELL_SET_PAYOUT = 3
SELL_DAILY_CAP = 21
SCROLL_PRICE = 30
SCROLL_DURATION_MIN = 30
TITLE_PRICE = 50
ROOM_PRICE = 100

SHOP_TITLES = [
    "Accio Self-Respect (No Response)",
    "The Sorting Hat Asked Me to Leave",
    "Emotionally Support Dementor",
]

TRADEABLE = frozenset(BUY_PRICE.keys())  # common + uncommon only


def today_str() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


class Marketplace(commands.Cog):
    group = app_commands.Group(name="market", description="The Velmora Marketplace.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self.rng = random.Random()

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read %s", STATE_PATH)
            state = {}
        state.setdefault("scrolls", {})       # uid -> count
        state.setdefault("titles", {})        # uid -> [title, ...]
        state.setdefault("sell_day", {})      # uid -> {date, earned}
        state.setdefault("rooms", [])         # purchase log for staff
        return state

    def save(self):
        tmp = STATE_PATH.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save %s", STATE_PATH)

    # ------------------------------------------------------------- helpers

    def _world(self):
        return self.bot.get_cog("World")

    def _store(self):
        return self.bot.get_cog("Store")

    def _hexes(self):
        return self.bot.get_cog("Hexes")

    def _items(self) -> dict:
        world = self._world()
        return world.world.items if world else {}

    def titles_of(self, user_id: int) -> list[str]:
        return list(self.state["titles"].get(str(user_id), []))

    def scroll_count(self, user_id: int) -> int:
        return int(self.state["scrolls"].get(str(user_id), 0))

    def _sell_earned_today(self, user_id: int) -> int:
        rec = self.state["sell_day"].get(str(user_id), {})
        if rec.get("date") != today_str():
            return 0
        return int(rec.get("earned", 0))

    def _add_sell_earned(self, user_id: int, pts: int):
        key = str(user_id)
        rec = self.state["sell_day"].get(key, {})
        if rec.get("date") != today_str():
            rec = {"date": today_str(), "earned": 0}
        rec["earned"] = int(rec.get("earned", 0)) + pts
        self.state["sell_day"][key] = rec

    def _charge(self, member, cost: int, reason: str) -> dict:
        """Deduct house points from member + house. Returns ok dict or {error}."""
        store = self._store()
        if store is None:
            return {"error": "The ledger isn't loaded."}
        house = store.member_house(member)
        if not house:
            return {"error": "You'll need a house before you can spend its points."}
        have = store.member_points(member.id, "season")
        if have < cost:
            return {"error": f"That costs **{cost}** points, and you only have **{have}** "
                             "this season."}
        store.record(
            house=house,
            delta=-cost,
            actor_id=self.bot.user.id if self.bot.user else 0,
            target_id=member.id,
            reason=reason,
        )
        return {"house": house, "cost": cost}

    def _pay(self, member, pts: int, reason: str) -> dict:
        store = self._store()
        if store is None:
            return {"error": "The ledger isn't loaded."}
        house = store.member_house(member)
        if not house:
            return {"error": "You'll need a house before you can earn points for one."}
        store.record(
            house=house,
            delta=pts,
            actor_id=self.bot.user.id if self.bot.user else 0,
            target_id=member.id,
            reason=reason,
        )
        return {"house": house, "pts": pts}

    def _tradeable_ids(self, rarity: str | None = None) -> list[str]:
        items = self._items()
        out = []
        for iid, meta in items.items():
            r = meta.get("rarity")
            if r not in TRADEABLE:
                continue
            if rarity and r != rarity:
                continue
            out.append(iid)
        out.sort(key=lambda i: (items[i]["rarity"], items[i]["name"].lower()))
        return out

    def _item_choice_label(self, iid: str) -> str:
        it = self._items()[iid]
        price = BUY_PRICE[it["rarity"]]
        return f"{it['emoji']} {it['name']} ({it['rarity']}, {price} pts)"[:100]

    # ================================================================ browse

    @group.command(name="browse", description="See the Marketplace catalogue and what you can sell today.")
    async def browse(self, interaction: discord.Interaction):
        from cogs.store import HOUSES
        store = self._store()
        pts = store.member_points(interaction.user.id, "season") if store else 0
        house = store.member_house(interaction.user) if store else None
        h = HOUSES.get(house or "", {})
        sold = self._sell_earned_today(interaction.user.id)
        scrolls = self.scroll_count(interaction.user.id)
        owned_titles = self.titles_of(interaction.user.id)

        lines = [
            "Spend your personal points — every purchase comes out of your house total too.",
            "",
            "**🧪 Ingredients**",
            f"Buy · Common **{BUY_PRICE['common']}** pts each · Uncommon **{BUY_PRICE['uncommon']}** pts each",
            "Rare / very rare / legendary — not sold. Find them yourself.",
            f"Sell · **{SELL_SET_SIZE}** of the same common or uncommon → **{SELL_SET_PAYOUT}** pts "
            f"(up to **{SELL_DAILY_CAP}** pts/day from selling)",
            "",
            "**📜 Hex Scroll**",
            f"**{SCROLL_PRICE}** pts — cast with `/hexscroll` on a classmate. Lasts "
            f"**{SCROLL_DURATION_MIN}** minutes. The scroll picks the curse.",
            "",
            "**👑 Titles** — **{TITLE_PRICE}** pts each (shop exclusive)",
        ]
        for t in SHOP_TITLES:
            mark = " ✓" if t in owned_titles else ""
            lines.append(f"· {t}{mark}")
        lines += [
            "",
            f"**🚪 Room of Requirement** — **{ROOM_PRICE}** pts",
            "Pays for a private room. Headmasters are pinged to open it for you.",
        ]

        embed = discord.Embed(
            title="🏪 Velmora Marketplace",
            description="\n".join(lines),
            color=h.get("color", 0xC4A35A),
        )
        footer = f"Your points this season: {pts}"
        if h:
            footer += f" · {h['emoji']} {h['name']}"
        footer += f" · Scrolls: {scrolls} · Sold today: {sold}/{SELL_DAILY_CAP}"
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ================================================================ buy

    @group.command(name="buy", description="Buy a common or uncommon ingredient with house points.")
    @app_commands.describe(ingredient="What to buy", quantity="How many (default 1)")
    async def buy(self, interaction: discord.Interaction, ingredient: str,
                  quantity: app_commands.Range[int, 1, 10] = 1):
        items = self._items()
        meta = items.get(ingredient)
        if not meta or meta.get("rarity") not in TRADEABLE:
            await interaction.response.send_message(
                "The shop only sells **common** and **uncommon** ingredients.", ephemeral=True)
            return
        world = self._world()
        if not world:
            await interaction.response.send_message("The satchels are out of reach right now.", ephemeral=True)
            return

        price = BUY_PRICE[meta["rarity"]] * quantity
        charged = self._charge(interaction.user, price,
                               f"Marketplace: bought {meta['name']} ×{quantity}")
        if "error" in charged:
            await interaction.response.send_message(charged["error"], ephemeral=True)
            return

        async with world.lock:
            student = world.student(interaction.user)
            world.world.give(student, ingredient, quantity)
            world.save()

        from cogs.store import HOUSES
        h = HOUSES[charged["house"]]
        line = world.world.item_line(ingredient, quantity)
        await interaction.response.send_message(embed=discord.Embed(
            title="🏪 Purchase",
            description=f"{interaction.user.display_name} buys {line}.\n\n"
                        f"**−{price}** from {h['emoji']} {h['name']}.",
            color=h["color"],
        ))

    @buy.autocomplete("ingredient")
    async def _buy_autocomplete(self, interaction: discord.Interaction, current: str):
        q = current.lower()
        out = []
        for iid in self._tradeable_ids():
            label = self._item_choice_label(iid)
            if q and q not in label.lower() and q not in iid:
                continue
            out.append(app_commands.Choice(name=label, value=iid))
            if len(out) >= 25:
                break
        return out

    # ================================================================ sell

    @group.command(name="sell", description="Sell 3 of the same common/uncommon ingredient for 3 points.")
    @app_commands.describe(ingredient="Which material (must have 3 of the same)")
    async def sell(self, interaction: discord.Interaction, ingredient: str):
        items = self._items()
        meta = items.get(ingredient)
        if not meta or meta.get("rarity") not in TRADEABLE:
            await interaction.response.send_message(
                "You can only sell **common** and **uncommon** ingredients, "
                "and only in sets of 3 of the same item.", ephemeral=True)
            return
        world = self._world()
        if not world:
            await interaction.response.send_message("The satchels are out of reach right now.", ephemeral=True)
            return

        earned = self._sell_earned_today(interaction.user.id)
        if earned + SELL_SET_PAYOUT > SELL_DAILY_CAP:
            await interaction.response.send_message(
                f"You've hit today's sell cap (**{SELL_DAILY_CAP}** points from selling). "
                "Come back tomorrow.", ephemeral=True)
            return

        async with world.lock:
            student = world.student(interaction.user)
            have = student.get("items", {}).get(ingredient, 0)
            if have < SELL_SET_SIZE:
                await interaction.response.send_message(
                    f"You need **{SELL_SET_SIZE}** × {meta['emoji']} **{meta['name']}** "
                    f"to sell a set (you have {have}).", ephemeral=True)
                return
            if not world.world.take(student, ingredient, SELL_SET_SIZE):
                await interaction.response.send_message(
                    "Something moved in your satchel — try again.", ephemeral=True)
                return
            world.save()

        paid = self._pay(interaction.user, SELL_SET_PAYOUT,
                         f"Marketplace: sold {meta['name']} ×{SELL_SET_SIZE}")
        if "error" in paid:
            # Refund the items if the ledger failed after we took them.
            async with world.lock:
                student = world.student(interaction.user)
                world.world.give(student, ingredient, SELL_SET_SIZE)
                world.save()
            await interaction.response.send_message(paid["error"], ephemeral=True)
            return

        self._add_sell_earned(interaction.user.id, SELL_SET_PAYOUT)
        self.save()

        from cogs.store import HOUSES
        h = HOUSES[paid["house"]]
        left_cap = SELL_DAILY_CAP - self._sell_earned_today(interaction.user.id)
        await interaction.response.send_message(embed=discord.Embed(
            title="🏪 Sold",
            description=f"{interaction.user.display_name} sells "
                        f"{world.world.item_line(ingredient, SELL_SET_SIZE)}.\n\n"
                        f"**+{SELL_SET_PAYOUT}** to {h['emoji']} {h['name']}.",
            color=h["color"],
        ).set_footer(text=f"{left_cap} sell-points left today" if left_cap
                     else "Sell cap reached for today"))

    @sell.autocomplete("ingredient")
    async def _sell_autocomplete(self, interaction: discord.Interaction, current: str):
        world = self._world()
        q = current.lower()
        have = {}
        if world:
            have = world.student(interaction.user).get("items", {})
        out = []
        for iid in self._tradeable_ids():
            n = have.get(iid, 0)
            if n < SELL_SET_SIZE:
                continue
            it = self._items()[iid]
            label = f"{it['emoji']} {it['name']} ×{n} (sell {SELL_SET_SIZE} → {SELL_SET_PAYOUT} pts)"[:100]
            if q and q not in label.lower() and q not in iid:
                continue
            out.append(app_commands.Choice(name=label, value=iid))
            if len(out) >= 25:
                break
        return out

    # ================================================================ scroll

    @group.command(name="scroll", description=f"Buy a Hex Scroll ({SCROLL_PRICE} pts). Cast with /hexscroll.")
    async def buy_scroll(self, interaction: discord.Interaction):
        charged = self._charge(interaction.user, SCROLL_PRICE, "Marketplace: Hex Scroll")
        if "error" in charged:
            await interaction.response.send_message(charged["error"], ephemeral=True)
            return
        key = str(interaction.user.id)
        self.state["scrolls"][key] = self.scroll_count(interaction.user.id) + 1
        self.save()

        from cogs.store import HOUSES
        h = HOUSES[charged["house"]]
        n = self.scroll_count(interaction.user.id)
        await interaction.response.send_message(embed=discord.Embed(
            title="📜 Hex Scroll purchased",
            description=f"{interaction.user.display_name} buys a Hex Scroll.\n\n"
                        f"Cast it with `/hexscroll` — lasts **{SCROLL_DURATION_MIN}** minutes, "
                        f"curse is a surprise.\n\n"
                        f"**−{SCROLL_PRICE}** from {h['emoji']} {h['name']}.",
            color=h["color"],
        ).set_footer(text=f"Scrolls in your bag: {n}"))

    # ================================================================ title

    @group.command(name="title", description=f"Buy an exclusive Marketplace title ({TITLE_PRICE} pts).")
    @app_commands.describe(title="Which title")
    @app_commands.choices(title=[
        app_commands.Choice(name=t, value=t) for t in SHOP_TITLES
    ])
    async def buy_title(self, interaction: discord.Interaction, title: app_commands.Choice[str]):
        name = title.value
        if name not in SHOP_TITLES:
            await interaction.response.send_message("That title isn't for sale.", ephemeral=True)
            return
        owned = self.titles_of(interaction.user.id)
        if name in owned:
            await interaction.response.send_message(
                f"You already own **{name}**. Equip it with `/title`.", ephemeral=True)
            return

        charged = self._charge(interaction.user, TITLE_PRICE, f"Marketplace: title — {name}")
        if "error" in charged:
            await interaction.response.send_message(charged["error"], ephemeral=True)
            return

        key = str(interaction.user.id)
        owned = self.titles_of(interaction.user.id)
        owned.append(name)
        self.state["titles"][key] = owned
        self.save()

        from cogs.store import HOUSES
        h = HOUSES[charged["house"]]
        await interaction.response.send_message(embed=discord.Embed(
            title="👑 Title purchased",
            description=f"{interaction.user.display_name} is now eligible for "
                        f"**{name}**.\n\nEquip it with `/title`.\n\n"
                        f"**−{TITLE_PRICE}** from {h['emoji']} {h['name']}.",
            color=h["color"],
        ))

    # ================================================================ room

    @group.command(name="room", description=f"Buy the Room of Requirement ({ROOM_PRICE} pts). Pings Headmasters.")
    async def buy_room(self, interaction: discord.Interaction):
        charged = self._charge(interaction.user, ROOM_PRICE, "Marketplace: Room of Requirement")
        if "error" in charged:
            await interaction.response.send_message(charged["error"], ephemeral=True)
            return

        from cogs.store import HOUSES
        h = HOUSES[charged["house"]]
        self.state["rooms"].append({
            "at": time.time(),
            "user_id": interaction.user.id,
            "house": charged["house"],
            "channel_id": interaction.channel_id,
        })
        # keep a short history
        self.state["rooms"] = self.state["rooms"][-100:]
        self.save()

        role = discord.utils.find(
            lambda r: r.name.lower() == HEADMASTER_ROLE_NAME,
            interaction.guild.roles if interaction.guild else [],
        )
        ping = role.mention if role else "**@headmasters**"

        embed = discord.Embed(
            title="🚪 Room of Requirement",
            description=f"{interaction.user.mention} ({h['emoji']} **{h['name']}**) "
                        f"has purchased the **Room of Requirement**.\n\n"
                        f"Headmasters — please open their room.\n\n"
                        f"**−{ROOM_PRICE}** from {h['emoji']} {h['name']}.",
            color=h["color"],
        )
        await interaction.response.send_message(
            content=f"{ping} — Room of Requirement purchase.",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

    # ================================================================ cast scroll

    @app_commands.command(name="hexscroll", description="Cast a Hex Scroll you bought. Lasts 30 minutes; curse is random.")
    @app_commands.describe(member="Who to hex")
    async def hexscroll(self, interaction: discord.Interaction, member: discord.Member):
        if member.bot:
            await interaction.response.send_message("You can't hex a bot.", ephemeral=True)
            return
        if self.scroll_count(interaction.user.id) < 1:
            await interaction.response.send_message(
                f"You don't have a Hex Scroll. Buy one with `/market scroll` ({SCROLL_PRICE} pts).",
                ephemeral=True)
            return
        hexes = self._hexes()
        if not hexes:
            await interaction.response.send_message("Hexes aren't loaded right now.", ephemeral=True)
            return

        from cogs.hexes import EFFECTS, CAST_FLOURISHES
        effect_key = self.rng.choice(list(EFFECTS.keys()))
        # Consume first so a failed apply doesn't soft-lock; re-grant on failure.
        key = str(interaction.user.id)
        self.state["scrolls"][key] = self.scroll_count(interaction.user.id) - 1
        if self.state["scrolls"][key] <= 0:
            self.state["scrolls"].pop(key, None)
        self.save()

        try:
            spell, was_hexed = hexes.apply_hex(
                target_id=member.id, effect=effect_key,
                duration_minutes=SCROLL_DURATION_MIN, cast_by=interaction.user.id,
            )
        except Exception:
            log.exception("Hex scroll apply failed")
            self.state["scrolls"][key] = self.scroll_count(interaction.user.id) + 1
            self.save()
            await interaction.response.send_message(
                "The scroll fizzled. It wasn't consumed — try again.", ephemeral=True)
            return

        flourish = random.choice(CAST_FLOURISHES)
        replaced = " (replacing the curse already on them)" if was_hexed else ""
        await interaction.response.send_message(embed=discord.Embed(
            title=f"📜 {spell['name']}!",
            description=f"**{interaction.user.display_name}** unfurls a Hex Scroll and {flourish} "
                        f"**{member.display_name}**.\n\n"
                        f"{member.mention} is hexed with **{spell['name']}** "
                        f"({spell['description']}){replaced} for "
                        f"**{SCROLL_DURATION_MIN}** minutes.",
            color=0x8B5CF6,
        ).set_footer(text=f"Scrolls left: {self.scroll_count(interaction.user.id)}"))


async def setup(bot: commands.Bot):
    await bot.add_cog(Marketplace(bot))
