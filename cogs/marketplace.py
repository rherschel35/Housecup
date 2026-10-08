"""
The Velmora Marketplace — spend (and sell for) house points.

    /market browse              - the catalogue and your scrolls / sell room today
    /market buy ingredient qty  - common/uncommon ingredients (staff-set prices)
    /market sell ingredient [batches] - sell ingredient sets (3) or Descent mats (25)
    /market scroll              - buy a Hex Scroll
    /market broomtoken          - buy a Speed or Altitude broom upgrade token
    /market title               - buy an exclusive shop title (searchable catalogue)
    /market room                - Room of Requirement; pings @headmasters
    /hexscroll member           - cast one owned Hex Scroll (30 min, random effect)
    /staff market sellreset member  - clear someone's daily sell-points cap
    /staff market setprice item pts - change Marketplace prices
    /staff market prices            - show current prices

Every spend deducts from the member's season contribution AND the house total
(same honesty as /bean). Sell earnings go to both, under a daily cap.
Rare, very_rare, and legendary place-ingredients cannot be bought or sold
(Pitch Resin included — forage only). Descent materials (any zone drop /
boss sigil) sell in batches of 25. Defaults: common 3 / uncommon 6 / scroll 30 /
title 50 / room 500 / broom token 50 / sell batch 3 / sell cap 21 — staff can
reprice via `/staff market setprice`.
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

# Defaults — live values live in marketplace.json under "prices" (staff can change).
DEFAULT_PRICES = {
    "buy_common": 3,
    "buy_uncommon": 6,
    "sell_payout": 3,
    "sell_daily_cap": 21,
    "scroll": 30,
    "title": 50,
    "room": 500,
    "broom_token": 50,
}
PRICE_LABELS = {
    "buy_common": "Buy common ingredient",
    "buy_uncommon": "Buy uncommon ingredient",
    "sell_payout": "Sell payout per batch",
    "sell_daily_cap": "Daily sell-points cap",
    "scroll": "Hex Scroll",
    "title": "Shop title",
    "room": "Room of Requirement",
    "broom_token": "Broom upgrade token",
}
# Back-compat aliases used by older imports / docs.
BUY_PRICE = {
    "common": DEFAULT_PRICES["buy_common"],
    "uncommon": DEFAULT_PRICES["buy_uncommon"],
}
SELL_SET_SIZE = 3                 # place ingredients (common/uncommon)
DESCENT_SELL_SET_SIZE = 25        # any Descent material
SELL_SET_PAYOUT = DEFAULT_PRICES["sell_payout"]
SELL_DAILY_CAP = DEFAULT_PRICES["sell_daily_cap"]
SELL_MAX_BATCHES = max(1, SELL_DAILY_CAP // max(1, SELL_SET_PAYOUT))
SCROLL_PRICE = DEFAULT_PRICES["scroll"]
SCROLL_DURATION_MIN = 30
TITLE_PRICE = DEFAULT_PRICES["title"]
ROOM_PRICE = DEFAULT_PRICES["room"]

SHOP_TITLES = [
    "Accio Self-Respect (No Response)",
    "The Sorting Hat Asked Me to Leave",
    "Emotional Support Dementor",
    "Professionally Unbothered",
    "Emotionally Unavailable Owl",
    "Expecto Patronum (Missed)",
    "My House Points Are In Another Castle",
    "Certified Potion Catastrophe",
    "Wandlessly Overconfident",
    "Prefers the Forbidden Section",
    "Talks to Paintings (They Don't Answer)",
    "Accidentally Hexed Myself Again",
    "Quidditch Practice Survivor",
    "Descended Too Deep, Emotionally",
    "Army of One (and a Niffler)",
    "Patronus Is Just Vibes",
    "Study Hall Legend",
    "Duel Night Casualty",
    "Please Stop Summoning Me",
    "Owl Post Left on Read",
    "Headmaster's Favorite Problem",
    "Sorted Wrong On Purpose",
    "Room of Requirement Regular",
    "Butterbeer Before Responsibility",
    "I Put the Chaos in Caldrin",
    "AsterWilde Experiment Gone Well",
    "Oakmont Loyalty Tester",
    "Thornmere Scheme Intern",
    "Veyren Clocked Your Aura",
    "Hex Me Once Shame On You",
    "Broom Token Millionaire",
    "Familiar Runs This Account",
    "Mirror Card Main Character",
    "Attack on Velmora MVP (Self-Declared)",
    "Quietly Judging Your Cast",
    "Spireheart Soft Launch",
    "Vault Eternal Door Dash",
    "Not That Kind of Dark Arts",
    "Detention Connoisseur",
    "House Cup Threat Level Midnight",
    "I Read the Compendium (Skimmed)",
    "Magically Exhausted",
    "Still Loading My Patronus",
    "Chronically Underleveled",
    "This Meeting Could've Been a Patronus",
    "Holding Grudges Professionally",
    "Soft Launch Dark Lord",
    "Wizard's Block",
    "Currently Experiencing Magical Difficulties",
    "Do Not Perceive Me",
    "Main Character Energy (NPC Budget)",
    "Hexed But Make It Fashion",
    "Running on Ambrosia and Spite",
    "My Familiar Filed a Complaint",
    "Unofficial Hall Monitor",
    "Lost in the Restricted Section",
    "Casting With My Eyes Closed",
    "Statistically Unlucky",
    "Please Advise the Sorting Hat",
    "Temporary Human Form",
    "Lore Accurate Disaster",
    "Points Mean Nothing To Me",
    "Emotionally Buffered",
    "Ghosted by My Own Owl",
    "Mid-Duel Crisis",
    "Built Different (Incorrectly)",
    "Seeking Adult Supervision",
    "One With the Chaos",
    "Wand Chose Me (Regretfully)",
    "Not Cooked, Just Warming Up",
    "House Pride, Personal Shame",
    "I Peak at 3am Magically",
    "Touch Grass (Transfigured)",
    "Social Battery: Critically Low",
    "Plot Armor Pending",
    "Accidentally Iconic",
    "Mentally In The Descent",
    "Friendly Fire Specialist",
    "I Contain Multitudes (Mostly Snacks)",
    "Respectfully Unhinged",
    "Buffering My Intentions",
    "Soft Launch Menace",
    "Did Not Read The Runes",
    "Cursed With Potential",
    "Looking For Group (Spiritually)",
    "Out Of Spell Slots",
    "Vibe Check Failed",
    "Permanently On A Side Quest",
    "Too Powerful To Parent",
    "Manifesting A Nap",
    "Chaos With A Library Card",
    "Low Key Legendary",
    "High Key Exhausted",
    "Final Boss Of Group Projects",
]

# Old shop-title spellings → current (migrate owned lists on load).
TITLE_RENAMES = {
    "Emotionally Support Dementor": "Emotional Support Dementor",
}

TRADEABLE = frozenset({"common", "uncommon"})  # common + uncommon place ingredients
# Never bought or sold, even if rarity would otherwise allow it.
NO_MARKET = frozenset({
    "pitch_resin",
})
# Descent drops — sellable in 25s, never bought from the shop.
DESCENT_SELLABLE = frozenset({
    "descent_poison_ichor",
    "descent_ember_shard",
    "descent_frost_core",
    "descent_storm_relic",
    "descent_light_dust",
    "descent_sigil",
})
BROOM_TOKEN_STATS = ("speed", "altitude")
BROOM_TOKEN_LABELS = {
    "speed": "Speed",
    "altitude": "Altitude / control",
}


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
        state.setdefault("broom_tokens", {})  # uid -> {speed: n, altitude: n}
        prices = state.setdefault("prices", {})
        for key, value in DEFAULT_PRICES.items():
            prices.setdefault(key, value)
        # Rewrite retired shop-title spellings so buyers keep their purchase.
        changed = False
        for uid, owned in list(state["titles"].items()):
            if not isinstance(owned, list):
                continue
            new_owned: list[str] = []
            seen: set[str] = set()
            for t in owned:
                t2 = TITLE_RENAMES.get(t, t)
                if t2 in seen:
                    changed = changed or (t2 != t)
                    continue
                seen.add(t2)
                if t2 != t:
                    changed = True
                new_owned.append(t2)
            state["titles"][uid] = new_owned
        if changed:
            log.info("Migrated renamed Marketplace titles in %s", STATE_PATH)
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

    def price(self, key: str) -> int:
        """Live Marketplace price (staff overrides in marketplace.json)."""
        if key not in DEFAULT_PRICES:
            raise KeyError(key)
        raw = (self.state.get("prices") or {}).get(key, DEFAULT_PRICES[key])
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = int(DEFAULT_PRICES[key])
        return max(0, n)

    def buy_prices(self) -> dict[str, int]:
        return {
            "common": self.price("buy_common"),
            "uncommon": self.price("buy_uncommon"),
        }

    def sell_payout(self) -> int:
        return max(1, self.price("sell_payout"))

    def sell_daily_cap(self) -> int:
        return max(0, self.price("sell_daily_cap"))

    def sell_max_batches(self) -> int:
        payout = self.sell_payout()
        cap = self.sell_daily_cap()
        return max(1, cap // payout) if payout else 1

    def set_price(self, key: str, points: int) -> tuple[int, int]:
        """Set a price key. Returns (old, new)."""
        if key not in DEFAULT_PRICES:
            raise KeyError(key)
        points = max(0, int(points))
        if key in ("sell_payout",) and points < 1:
            points = 1
        old = self.price(key)
        self.state.setdefault("prices", {})[key] = points
        self.save()
        return old, points

    def scroll_count(self, user_id: int) -> int:
        return int(self.state["scrolls"].get(str(user_id), 0))

    def broom_token_count(self, user_id: int, stat: str) -> int:
        if stat not in BROOM_TOKEN_STATS:
            return 0
        bag = self.state.setdefault("broom_tokens", {}).get(str(user_id), {})
        return int(bag.get(stat, 0))

    def grant_broom_token(self, user_id: int, stat: str, n: int = 1) -> None:
        if stat not in BROOM_TOKEN_STATS or n <= 0:
            return
        key = str(user_id)
        bag = self.state.setdefault("broom_tokens", {}).setdefault(key, {})
        bag[stat] = int(bag.get(stat, 0)) + n
        self.save()

    def spend_broom_token(self, user_id: int, stat: str) -> bool:
        if self.broom_token_count(user_id, stat) < 1:
            return False
        key = str(user_id)
        bag = self.state.setdefault("broom_tokens", {}).setdefault(key, {})
        bag[stat] = int(bag.get(stat, 0)) - 1
        if bag[stat] <= 0:
            bag.pop(stat, None)
        if not bag:
            self.state["broom_tokens"].pop(key, None)
        self.save()
        return True

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

    def _clear_sell_day(self, user_id: int) -> int:
        """Wipe today's sell earnings. Returns how many points were cleared."""
        key = str(user_id)
        had = self._sell_earned_today(user_id)
        self.state["sell_day"].pop(key, None)
        return had

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
        """Place ingredients the shop buys/sells — not Descent materials."""
        items = self._items()
        out = []
        for iid, meta in items.items():
            if iid in DESCENT_SELLABLE or iid in NO_MARKET:
                continue
            r = meta.get("rarity")
            if r not in TRADEABLE:
                continue
            if rarity and r != rarity:
                continue
            out.append(iid)
        out.sort(key=lambda i: (items[i]["rarity"], items[i]["name"].lower()))
        return out

    def _sell_set_size(self, item_id: str) -> int | None:
        """Batch size for a sellable item, or None if it can't be sold."""
        if item_id in NO_MARKET:
            return None
        if item_id in DESCENT_SELLABLE:
            return DESCENT_SELL_SET_SIZE
        meta = self._items().get(item_id)
        if meta and meta.get("rarity") in TRADEABLE:
            return SELL_SET_SIZE
        return None

    def _sellable_ids(self) -> list[str]:
        """Everything `/market sell` accepts (place ingredients + Descent mats)."""
        items = self._items()
        out = self._tradeable_ids() + [iid for iid in DESCENT_SELLABLE if iid in items]
        out.sort(key=lambda i: (
            0 if i not in DESCENT_SELLABLE else 1,
            items[i]["rarity"],
            items[i]["name"].lower(),
        ))
        return out

    def _item_choice_label(self, iid: str) -> str:
        it = self._items()[iid]
        prices = self.buy_prices()
        price = prices.get(it["rarity"], 0)
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
        speed_tok = self.broom_token_count(interaction.user.id, "speed")
        alt_tok = self.broom_token_count(interaction.user.id, "altitude")
        buy = self.buy_prices()
        sell_pay = self.sell_payout()
        sell_cap = self.sell_daily_cap()
        scroll_pts = self.price("scroll")
        title_pts = self.price("title")
        room_pts = self.price("room")
        broom_pts = self.price("broom_token")

        lines = [
            "Spend your personal points — every purchase comes out of your house total too.",
            "",
            "**🧪 Ingredients**",
            f"Buy · Common **{buy['common']}** pts each · Uncommon **{buy['uncommon']}** pts each",
            "Rare / very rare / legendary — not sold. Find them yourself.",
            "Pitch Resin — forage only; never bought or sold.",
            f"Sell · **{SELL_SET_SIZE}** of the same common/uncommon → **{sell_pay}** pts per batch",
            f"Descent materials · **{DESCENT_SELL_SET_SIZE}** of the same → **{sell_pay}** pts per batch",
            f"(use `batches:` to sell several at once · up to **{sell_cap}** pts/day from selling)",
            "",
            "**📜 Hex Scroll**",
            f"**{scroll_pts}** pts — cast with `/hexscroll` on a classmate. Lasts "
            f"**{SCROLL_DURATION_MIN}** minutes. The scroll picks the curse.",
            "",
            "**🧹 Broom tokens**",
            f"Buy with `/market broomtoken` — **{broom_pts}** pts each (Speed or Altitude).",
            "Spend a token with `/broomupgrade` (or use Pitch Resin / Descent mats).",
            f"You hold · Speed ×{speed_tok} · Altitude ×{alt_tok}",
            "",
            f"**👑 Titles** — **{title_pts}** pts each · **{len(SHOP_TITLES)}** for sale",
            f"Buy with `/market title` (start typing to search). Owned: "
            f"**{len(owned_titles)}**/{len(SHOP_TITLES)}.",
            "",
            f"**🚪 Room of Requirement** — **{room_pts}** pts",
            "Pays for a private room. Headmasters are pinged to open it for you.",
        ]

        embed = discord.Embed(
            title="🏪 Velmora Marketplace",
            description="\n".join(lines),
            color=h.get("color", 0xC4A35A),
        )
        # Title catalogue in fields (description can't hold ~100 lines).
        owned_set = set(owned_titles)
        chunk: list[str] = []
        field_i = 1
        for t in SHOP_TITLES:
            mark = " ✓" if t in owned_set else ""
            line = f"· {t}{mark}"
            trial = ("\n".join(chunk + [line])) if chunk else line
            if len(trial) > 1000 and chunk:
                embed.add_field(
                    name=f"Titles ({field_i})" if field_i > 1 else "Titles",
                    value="\n".join(chunk),
                    inline=False,
                )
                field_i += 1
                chunk = [line]
            else:
                chunk.append(line)
        if chunk:
            embed.add_field(
                name=f"Titles ({field_i})" if field_i > 1 else "Titles",
                value="\n".join(chunk),
                inline=False,
            )
        footer = f"Your points this season: {pts}"
        if h:
            footer += f" · {h['emoji']} {h['name']}"
        footer += (
            f" · Scrolls: {scrolls} · Broom tokens: S{speed_tok}/A{alt_tok} "
            f"· Sold today: {sold}/{sell_cap}"
        )
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ================================================================ buy

    @group.command(name="buy", description="Buy a common or uncommon ingredient with house points.")
    @app_commands.describe(ingredient="What to buy", quantity="How many (default 1)")
    async def buy(self, interaction: discord.Interaction, ingredient: str,
                  quantity: app_commands.Range[int, 1, 10] = 1):
        items = self._items()
        meta = items.get(ingredient)
        if not meta or ingredient in NO_MARKET or meta.get("rarity") not in TRADEABLE:
            await interaction.response.send_message(
                "The shop only sells **common** and **uncommon** ingredients.", ephemeral=True)
            return
        world = self._world()
        if not world:
            await interaction.response.send_message("The satchels are out of reach right now.", ephemeral=True)
            return

        unit = self.buy_prices().get(meta["rarity"])
        if unit is None:
            await interaction.response.send_message(
                "The shop only sells **common** and **uncommon** ingredients.", ephemeral=True)
            return
        price = unit * quantity
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

    @group.command(
        name="sell",
        description="Sell ingredients or Descent materials in batches. Optional batch count.",
    )
    @app_commands.describe(
        ingredient="Which material to sell",
        batches="How many batches to sell at once (default 1)",
    )
    async def sell(
        self,
        interaction: discord.Interaction,
        ingredient: str,
        batches: app_commands.Range[int, 1, 50] = 1,
    ):
        items = self._items()
        meta = items.get(ingredient)
        set_size = self._sell_set_size(ingredient)
        sell_pay = self.sell_payout()
        sell_cap = self.sell_daily_cap()
        max_batches = self.sell_max_batches()
        if batches > max_batches:
            await interaction.response.send_message(
                f"You can sell at most **{max_batches}** batch"
                f"{'' if max_batches == 1 else 'es'} under today's "
                f"**{sell_cap}**-pt sell cap (**{sell_pay}** pts each).",
                ephemeral=True,
            )
            return
        if not meta or set_size is None:
            await interaction.response.send_message(
                "You can sell **common/uncommon** place ingredients "
                f"(**{SELL_SET_SIZE}** → **{sell_pay}** pts) or **Descent materials** "
                f"(**{DESCENT_SELL_SET_SIZE}** → **{sell_pay}** pts).",
                ephemeral=True,
            )
            return
        world = self._world()
        if not world:
            await interaction.response.send_message("The satchels are out of reach right now.", ephemeral=True)
            return

        payout = sell_pay * batches
        need = set_size * batches
        earned = self._sell_earned_today(interaction.user.id)
        if earned + payout > sell_cap:
            room = max(0, sell_cap - earned)
            room_batches = room // sell_pay
            tip = (f" You still have room for **{room_batches}** batch"
                   f"{'' if room_batches == 1 else 'es'}." if room_batches
                   else " Come back tomorrow.")
            await interaction.response.send_message(
                f"**{batches}** batch{'' if batches == 1 else 'es'} would pay **{payout}** pts, "
                f"but today's sell cap is **{sell_cap}** "
                f"(you've earned **{earned}**).{tip}",
                ephemeral=True,
            )
            return

        async with world.lock:
            student = world.student(interaction.user)
            have = student.get("items", {}).get(ingredient, 0)
            if have < need:
                await interaction.response.send_message(
                    f"You need **{need}** × {meta['emoji']} **{meta['name']}** "
                    f"to sell **{batches}** batch{'' if batches == 1 else 'es'} "
                    f"of {set_size} (you have {have}).",
                    ephemeral=True,
                )
                return
            if not world.world.take(student, ingredient, need):
                await interaction.response.send_message(
                    "Something moved in your satchel — try again.", ephemeral=True)
                return
            world.save()

        paid = self._pay(
            interaction.user, payout,
            f"Marketplace: sold {meta['name']} ×{need}"
            + (f" ({batches} batches)" if batches > 1 else ""),
        )
        if "error" in paid:
            # Refund the items if the ledger failed after we took them.
            async with world.lock:
                student = world.student(interaction.user)
                world.world.give(student, ingredient, need)
                world.save()
            await interaction.response.send_message(paid["error"], ephemeral=True)
            return

        self._add_sell_earned(interaction.user.id, payout)
        self.save()

        from cogs.store import HOUSES
        h = HOUSES[paid["house"]]
        left_cap = self.sell_daily_cap() - self._sell_earned_today(interaction.user.id)
        batch_note = (f" ({batches} × {set_size})" if batches > 1 else "")
        await interaction.response.send_message(embed=discord.Embed(
            title="🏪 Sold",
            description=f"{interaction.user.display_name} sells "
                        f"{world.world.item_line(ingredient, need)}{batch_note}.\n\n"
                        f"**+{payout}** to {h['emoji']} {h['name']}.",
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
        for iid in self._sellable_ids():
            set_size = self._sell_set_size(iid)
            if set_size is None:
                continue
            n = have.get(iid, 0)
            if n < set_size:
                continue
            it = self._items()[iid]
            can_batches = n // set_size
            label = (f"{it['emoji']} {it['name']} ×{n} "
                     f"(sell {set_size} → {self.sell_payout()} pts"
                     + (f", up to {can_batches} batches" if can_batches > 1 else "")
                     + ")")[:100]
            if q and q not in label.lower() and q not in iid:
                continue
            out.append(app_commands.Choice(name=label, value=iid))
            if len(out) >= 25:
                break
        return out

    # ================================================================ broom token

    @group.command(
        name="broomtoken",
        description="Buy a Speed or Altitude broom upgrade token.",
    )
    @app_commands.describe(stat="Which broom flight stat the token upgrades")
    @app_commands.choices(
        stat=[
            app_commands.Choice(name="Speed", value="speed"),
            app_commands.Choice(name="Altitude / control", value="altitude"),
        ],
    )
    async def buy_broomtoken(
        self,
        interaction: discord.Interaction,
        stat: app_commands.Choice[str],
    ):
        from cogs.brooms import MAX_STAT, effective_stats

        brooms = self.bot.get_cog("Brooms")
        if not brooms:
            await interaction.response.send_message(
                "Broom fitting isn't available right now.", ephemeral=True
            )
            return
        broom = brooms.broom_of(interaction.user.id)
        if not broom:
            await interaction.response.send_message(
                "You need a broom first — `/broom`.", ephemeral=True
            )
            return
        stat_key = stat.value
        if stat_key not in BROOM_TOKEN_STATS:
            await interaction.response.send_message(
                "Pick **Speed** or **Altitude / control**.", ephemeral=True
            )
            return
        eff = effective_stats(broom)
        current = int(eff.get(stat_key, 0))
        label = BROOM_TOKEN_LABELS[stat_key]
        if current >= MAX_STAT:
            await interaction.response.send_message(
                f"Your broom's **{label}** is already **{MAX_STAT}/10** — "
                "no token needed.",
                ephemeral=True,
            )
            return
        pending = self.broom_token_count(interaction.user.id, stat_key)
        if current + pending >= MAX_STAT:
            await interaction.response.send_message(
                f"You already hold enough **{label}** tokens to max that stat. "
                "Spend them with `/broomupgrade` first.",
                ephemeral=True,
            )
            return
        price = self.price("broom_token")
        charged = self._charge(
            interaction.user,
            price,
            f"Marketplace: broom {label} token",
        )
        if "error" in charged:
            await interaction.response.send_message(charged["error"], ephemeral=True)
            return
        self.grant_broom_token(interaction.user.id, stat_key, 1)
        from cogs.store import HOUSES
        h = HOUSES[charged["house"]]
        held = self.broom_token_count(interaction.user.id, stat_key)
        await interaction.response.send_message(embed=discord.Embed(
            title="🏪 Broom token",
            description=(
                f"{interaction.user.display_name} buys a **{label}** broom token.\n\n"
                f"**−{price}** from {h['emoji']} {h['name']}.\n"
                f"Spend it with `/broomupgrade` → Marketplace broom token.\n"
                f"Tokens held for {label}: **{held}**."
            ),
            color=h["color"],
        ).set_footer(text=f"{price} pts each · Speed or Altitude"))

    # ================================================================ scroll

    @group.command(name="scroll", description="Buy a Hex Scroll. Cast with /hexscroll.")
    async def buy_scroll(self, interaction: discord.Interaction):
        price = self.price("scroll")
        charged = self._charge(interaction.user, price, "Marketplace: Hex Scroll")
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
                        f"**−{price}** from {h['emoji']} {h['name']}.",
            color=h["color"],
        ).set_footer(text=f"Scrolls in your bag: {n}"))

    # ================================================================ title

    @group.command(name="title", description="Buy an exclusive Marketplace title. Start typing to search.")
    @app_commands.describe(title="Which title (start typing to search)")
    async def buy_title(self, interaction: discord.Interaction, title: str):
        name = TITLE_RENAMES.get(title.strip(), title.strip())
        if name not in SHOP_TITLES:
            await interaction.response.send_message(
                "That title isn't for sale. Use `/market title` and pick from the list.",
                ephemeral=True,
            )
            return
        owned = self.titles_of(interaction.user.id)
        if name in owned:
            await interaction.response.send_message(
                f"You already own **{name}**. Equip it with `/title`.", ephemeral=True)
            return

        price = self.price("title")
        charged = self._charge(interaction.user, price, f"Marketplace: title — {name}")
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
                        f"**−{price}** from {h['emoji']} {h['name']}.",
            color=h["color"],
        ))

    @buy_title.autocomplete("title")
    async def _title_autocomplete(self, interaction: discord.Interaction, current: str):
        q = current.lower().strip()
        owned = set(self.titles_of(interaction.user.id))
        out: list[app_commands.Choice[str]] = []
        for t in SHOP_TITLES:
            if q and q not in t.lower():
                continue
            mark = " ✓" if t in owned else ""
            label = f"{t}{mark}"[:100]
            out.append(app_commands.Choice(name=label, value=t))
            if len(out) >= 25:
                break
        return out

    # ================================================================ room

    @group.command(name="room", description="Buy the Room of Requirement. Pings Headmasters.")
    async def buy_room(self, interaction: discord.Interaction):
        price = self.price("room")
        charged = self._charge(interaction.user, price, "Marketplace: Room of Requirement")
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
                        f"**−{price}** from {h['emoji']} {h['name']}.",
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
                f"You don't have a Hex Scroll. Buy one with `/market scroll` "
                f"({self.price('scroll')} pts).",
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

        # Limp Wand (and any fixed-duration curse) overrides the scroll's usual 30 min.
        mins = int(spell.get("fixed_minutes") or SCROLL_DURATION_MIN)
        flourish = random.choice(CAST_FLOURISHES)
        replaced = " (replacing the curse already on them)" if was_hexed else ""
        await interaction.response.send_message(embed=discord.Embed(
            title=f"📜 {spell['name']}!",
            description=f"**{interaction.user.display_name}** unfurls a Hex Scroll and {flourish} "
                        f"**{member.display_name}**.\n\n"
                        f"{member.mention} is hexed with **{spell['name']}** "
                        f"({spell['description']}){replaced} for "
                        f"**{mins}** minutes.",
            color=0x8B5CF6,
        ).set_footer(text=f"Scrolls left: {self.scroll_count(interaction.user.id)}"))

    async def sellreset(self, interaction: discord.Interaction, member: discord.Member):
        store = self._store()
        if not store or not store.is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        had = self._clear_sell_day(member.id)
        self.save()
        sell_cap = self.sell_daily_cap()
        if had:
            msg = (f"Cleared **{member.display_name}**'s market sell record "
                   f"(was **{had}/{sell_cap}** pts earned today). "
                   f"They can sell again up to **{sell_cap}**.")
        else:
            msg = (f"**{member.display_name}** had nothing on today's sell cap — "
                   f"still clear. They can sell up to **{sell_cap}** pts.")
        await interaction.response.send_message(msg, ephemeral=True)
        log.info("market sellreset by %s for %s (had %s)", interaction.user.id, member.id, had)

    async def staff_setprice(
        self,
        interaction: discord.Interaction,
        what: str,
        points: int,
    ):
        store = self._store()
        if not store or not store.is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        if what not in DEFAULT_PRICES:
            await interaction.response.send_message(
                "Unknown price key.", ephemeral=True,
            )
            return
        old, new = self.set_price(what, points)
        label = PRICE_LABELS.get(what, what)
        await interaction.response.send_message(
            f"**{label}** price: **{old}** → **{new}** pts.\n"
            f"`/market browse` and purchases use the new number immediately.",
            ephemeral=True,
        )
        log.info("market setprice %s %s→%s by %s", what, old, new, interaction.user.id)

    async def staff_prices(self, interaction: discord.Interaction):
        store = self._store()
        if not store or not store.is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        lines = [
            f"· **{PRICE_LABELS[k]}** (`{k}`): **{self.price(k)}** pts"
            + (f" (default {DEFAULT_PRICES[k]})" if self.price(k) != DEFAULT_PRICES[k] else "")
            for k in DEFAULT_PRICES
        ]
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🏪 Marketplace prices",
                description="\n".join(lines),
                color=0xC4A35A,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Marketplace(bot))
