"""
Birthdays. A student saves the month and day once. At midnight
America/Chicago on that day, the bot posts one shout-out and grants one
reward. The year is written down before the gift is handed out, so a
restart cannot grant it twice.

    /birthday register   - save month (dropdown) and day (autocomplete)
    /birthday view       - show the saved date
    /birthday staff set  - staff change someone's date
    /birthday staff clear - staff clear it (the year's gift still counts)

February 29 is celebrated on February 28 in a year that is not a leap year,
still only once.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import logging
import os
import random
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.velmora_channels import EVENT_ANNOUNCE_CHANNEL_ID

log = logging.getLogger("velmora.birthdays")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "birthdays.json"

try:
    from zoneinfo import ZoneInfo
    CHICAGO = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover - Windows without tzdata
    CHICAGO = dt.timezone.utc

CANDLE = 0xF2C14E

# Small chance the draw comes from the rare pool. Everything else is the
# ordinary weighted pool of house points, potion ingredients, and a coin.
RARE_CHANCE = 0.08

MONTHS = (
    (1, "January"),
    (2, "February"),
    (3, "March"),
    (4, "April"),
    (5, "May"),
    (6, "June"),
    (7, "July"),
    (8, "August"),
    (9, "September"),
    (10, "October"),
    (11, "November"),
    (12, "December"),
)
MONTH_CHOICES = [app_commands.Choice(name=name, value=num) for num, name in MONTHS]
MONTH_NAME = {num: name for num, name in MONTHS}

# kind "points" is house points (the currency). "item" is a satchel
# ingredient or material. "gear" is a cosmetic piece with no perk.
# "frog" is a Chocolate Frog card. Weights are relative inside each pool.
COMMON_REWARDS = (
    {"id": "points_5", "kind": "points", "amount": 5, "weight": 34,
     "label": "+5 house points"},
    {"id": "points_8", "kind": "points", "amount": 8, "weight": 16,
     "label": "+8 house points"},
    {"id": "silverleaf", "kind": "item", "item": "silverleaf", "n": 3, "weight": 14,
     "label": "Silverleaf ×3"},
    {"id": "dewmint", "kind": "item", "item": "dewmint", "n": 3, "weight": 14,
     "label": "Dewmint ×3"},
    {"id": "emberleaf", "kind": "item", "item": "emberleaf", "n": 2, "weight": 10,
     "label": "Emberleaf ×2"},
    {"id": "moonbloom", "kind": "item", "item": "moonbloom_petal", "n": 1, "weight": 8,
     "label": "Moonbloom Petal"},
    {"id": "coin", "kind": "item", "item": "dead_currency_coin", "n": 1, "weight": 4,
     "label": "Dead Currency Coin"},
)
RARE_REWARDS = (
    {"id": "points_25", "kind": "points", "amount": 25, "weight": 50,
     "label": "+25 house points"},
    {"id": "phoenix", "kind": "item", "item": "phoenix_down", "n": 1, "weight": 20,
     "label": "Phoenix Down"},
    {"id": "sunbell", "kind": "gear", "gear": "sunbell_ring", "weight": 20,
     "label": "Sunbell Ring"},
    {"id": "expecto", "kind": "frog", "card": "expecto", "weight": 10,
     "label": "Expecto Patronum frog card"},
)


def days_in_month(month: int | None) -> int:
    """How many days the dropdown should offer. February includes the 29th."""
    if month is None:
        return 31
    try:
        month_n = int(month)
    except (TypeError, ValueError):
        return 31
    if not 1 <= month_n <= 12:
        return 31
    # 2024 is a leap year, so February offers 29.
    return calendar.monthrange(2024, month_n)[1]


def valid_birthday(month: int, day: int) -> bool:
    try:
        month_n = int(month)
        day_n = int(day)
    except (TypeError, ValueError):
        return False
    if not 1 <= month_n <= 12:
        return False
    return 1 <= day_n <= days_in_month(month_n)


def format_birthday(month: int, day: int) -> str:
    return f"{MONTH_NAME.get(int(month), 'Unknown')} {int(day)}"


def is_birthday_today(month: int, day: int, when: dt.date) -> bool:
    """True on the saved month and day.

    February 29 is celebrated on February 28 when the year is not a leap
    year, so that birthday still receives its one gift.
    """
    try:
        month_n = int(month)
        day_n = int(day)
    except (TypeError, ValueError):
        return False
    if month_n == when.month and day_n == when.day:
        return True
    if month_n == 2 and day_n == 29 and not calendar.isleap(when.year):
        return when.month == 2 and when.day == 28
    return False


def reward_label(reward: dict) -> str:
    return str(reward.get("label") or reward.get("id") or "a gift")


def draw_birthday_reward(rng) -> dict:
    """One gift. ``rare`` is true only when the small rare roll hits."""
    rare = rng.random() < RARE_CHANCE
    pool = RARE_REWARDS if rare else COMMON_REWARDS
    weights = [int(row["weight"]) for row in pool]
    picked = dict(rng.choices(pool, weights=weights, k=1)[0])
    picked["rare"] = rare
    return picked


def _user(state: dict, user_id) -> dict:
    return state.setdefault("users", {}).setdefault(str(user_id), {})


def save_birthday(state: dict, user_id, month: int, day: int) -> dict:
    rec = _user(state, user_id)
    rec["month"] = int(month)
    rec["day"] = int(day)
    rec.setdefault("rewarded_years", [])
    return rec


def clear_birthday(state: dict, user_id) -> bool:
    """Drop the saved date. Years already gifted stay, so the gift is not repeated."""
    rec = state.get("users", {}).get(str(user_id))
    if not rec or "month" not in rec:
        return False
    rec.pop("month", None)
    rec.pop("day", None)
    return True


def claim_birthday_year(state: dict, user_id, year: int) -> bool:
    """Mark ``year`` gifted. False if this user was already marked for it."""
    rec = state.get("users", {}).get(str(user_id))
    if not rec:
        return False
    years = rec.setdefault("rewarded_years", [])
    year_n = int(year)
    if year_n in years:
        return False
    years.append(year_n)
    return True


def plan_birthday_grants(state: dict, when: dt.date, rng, *, only_user_ids: set[str] | None = None) -> list[tuple[str, dict]]:
    """Claim one reward per matching birthday for ``when.year``.

    The year is recorded on the state before the caller delivers anything.
    A second call, including one made after the state is saved and loaded,
    returns nothing for a year already claimed. ``only_user_ids`` limits
    the scan to people the bot can still see; None scans everyone saved.
    """
    granted: list[tuple[str, dict]] = []
    users = state.get("users") or {}
    for uid, rec in list(users.items()):
        if only_user_ids is not None and str(uid) not in only_user_ids:
            continue
        try:
            month = int(rec["month"])
            day = int(rec["day"])
        except (KeyError, TypeError, ValueError):
            continue
        if not is_birthday_today(month, day, when):
            continue
        if not claim_birthday_year(state, uid, when.year):
            continue
        granted.append((str(uid), draw_birthday_reward(rng)))
    return granted


async def complete_day(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Day dropdown. Typing narrows it; February offers 29."""
    month = getattr(getattr(interaction, "namespace", None), "month", None)
    try:
        month_n = int(month) if month is not None else None
    except (TypeError, ValueError):
        month_n = None
    text = (current or "").strip()
    days = [d for d in range(1, days_in_month(month_n) + 1) if not text or str(d).startswith(text)]
    return [app_commands.Choice(name=str(d), value=str(d)) for d in days[:25]]


def _parse_day(raw: str) -> int | None:
    text = (raw or "").strip()
    if not text.isdigit():
        return None
    return int(text)


class Birthdays(commands.Cog):
    group = app_commands.Group(
        name="birthday",
        description="Save your birthday, or see the one you saved.",
    )
    staff_group = app_commands.Group(
        name="staff",
        description="Change or clear a saved birthday.",
        parent=group,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.rng = random.Random()
        self.state = self._load()

    async def cog_load(self):
        self.midnight_tick.start()

    async def cog_unload(self):
        self.midnight_tick.cancel()

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"users": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Birthday state unreadable — starting empty.")
            return {"users": {}}
        if not isinstance(data, dict):
            return {"users": {}}
        data.setdefault("users", {})
        return data

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save birthday state.")

    def _channel_id(self) -> int:
        """Shout-outs land on the event announcement board.

        That is the same channel Attack notices and the midnight fire tally
        use. BIRTHDAY_CHANNEL_ID overrides it; otherwise
        EVENT_ANNOUNCE_CHANNEL_ID (env, then the shared default).
        """
        raw = os.getenv("BIRTHDAY_CHANNEL_ID", "").strip()
        if raw.isdigit():
            return int(raw)
        raw = os.getenv("EVENT_ANNOUNCE_CHANNEL_ID", "").strip()
        if raw.isdigit():
            return int(raw)
        return EVENT_ANNOUNCE_CHANNEL_ID

    def _is_staff(self, member) -> bool:
        store = self.bot.get_cog("Store")
        if store:
            return bool(store.is_staff(member))
        perms = getattr(member, "guild_permissions", None)
        return bool(perms and (getattr(perms, "manage_guild", False) or getattr(perms, "administrator", False)))

    async def _resolve_member(self, user_id: int):
        for guild in getattr(self.bot, "guilds", []) or []:
            member = guild.get_member(user_id)
            if member is not None:
                return member
            fetch = getattr(guild, "fetch_member", None)
            if fetch is None:
                continue
            try:
                return await fetch(user_id)
            except discord.HTTPException:
                continue
        return None

    async def _grant_points(self, member, amount: int) -> str | None:
        store = self.bot.get_cog("Store")
        if not store or member is None:
            return None
        house = store.member_house(member)
        if not house:
            return None
        entry = store.record(
            house=house,
            delta=int(amount),
            actor_id=self.bot.user.id if getattr(self.bot, "user", None) else 0,
            target_id=member.id,
            reason="Birthday gift",
        )
        if not entry:
            return None
        from cogs.store import HOUSES
        meta = HOUSES.get(house) or {}
        emoji = meta.get("emoji") or ""
        name = meta.get("name") or house
        return f"**+{int(amount)}** house points for {emoji} {name}".strip()

    async def _grant_item(self, member, item_id: str, n: int) -> str | None:
        world = self.bot.get_cog("World")
        if world is None or member is None:
            return None

        def _give() -> str | None:
            if item_id not in world.world.items:
                return None
            student = world.student(member)
            world.world.give(student, item_id, n)
            world.save()
            return world.world.item_line(item_id, n)

        lock = getattr(world, "lock", None)
        if lock is None:
            return _give()
        async with lock:
            return _give()

    def _grant_gear(self, member, key: str) -> str | None:
        adorn = self.bot.get_cog("Adornments")
        if adorn is None or member is None:
            return None
        from cogs.gear_data import GEAR
        if key not in GEAR:
            return None
        if not adorn.award(member.id, key):
            return None
        adorn.save()
        return f"the **{GEAR[key]['name']}** — a cosmetic, wear it from `/jewelbox`"

    def _grant_frog(self, member, card_id: str) -> str | None:
        frogs = self.bot.get_cog("Frogs")
        if frogs is None or member is None:
            return None
        from cogs.frogs import CARDS
        if card_id not in CARDS:
            return None
        frogs.add_card(member.id, card_id)
        meta = CARDS[card_id]
        return f"a Chocolate Frog card — {meta.get('emoji', '')} **{meta['name']}**".strip()

    async def _apply_reward(self, member, reward: dict) -> str:
        """Hand out the drawn gift. If that kind can't land, fall back to an ingredient."""
        kind = reward.get("kind")
        line = None
        if kind == "points":
            line = await self._grant_points(member, int(reward.get("amount") or 0))
            if line is None:
                line = await self._grant_item(member, "silverleaf", 3)
        elif kind == "item":
            line = await self._grant_item(member, reward.get("item") or "", int(reward.get("n") or 1))
        elif kind == "gear":
            line = self._grant_gear(member, reward.get("gear") or "")
            if line is None:
                line = await self._grant_item(member, "dew_diamond", 1)
        elif kind == "frog":
            line = self._grant_frog(member, reward.get("card") or "")
            if line is None:
                line = await self._grant_points(member, 15)
        return line or reward_label(reward)

    async def _shout(self, member, user_id: str, reward: dict, line: str, when: dt.date) -> None:
        channel = None
        if self.bot is not None:
            channel = self.bot.get_channel(self._channel_id())
            if channel is None:
                fetch = getattr(self.bot, "fetch_channel", None)
                if fetch is not None:
                    try:
                        channel = await fetch(self._channel_id())
                    except discord.HTTPException:
                        channel = None
        if channel is None or not hasattr(channel, "send"):
            log.info("Birthday shout-out for %s had nowhere to post.", user_id)
            return
        mention = getattr(member, "mention", None) or f"<@{user_id}>"
        name = getattr(member, "display_name", None) or "Someone"
        rare = "A rare one, this year.\n\n" if reward.get("rare") else ""
        embed = discord.Embed(
            title=f"🎂 Happy birthday, {name}",
            description=f"{mention} — the castle noticed.\n\n{rare}**Gift:** {line}",
            color=CANDLE,
        )
        embed.set_footer(text=f"America/Chicago · {when.isoformat()} · one gift a year")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Birthday shout-out failed for %s", user_id)

    async def _grant_due(self) -> None:
        when = dt.datetime.now(CHICAGO).date()
        # Only look up people whose birthday is today and who have not
        # already been gifted this year. Everyone else waits.
        visible: set[str] = set()
        for uid, rec in list((self.state.get("users") or {}).items()):
            try:
                month = int(rec["month"])
                day = int(rec["day"])
                user_id = int(uid)
            except (KeyError, TypeError, ValueError):
                continue
            if not is_birthday_today(month, day, when):
                continue
            if when.year in (rec.get("rewarded_years") or []):
                continue
            if await self._resolve_member(user_id):
                visible.add(str(uid))
        if not visible:
            return
        plans = plan_birthday_grants(self.state, when, self.rng, only_user_ids=visible)
        if not plans:
            return
        # Persist the claimed years before delivery. A restart loads this
        # file and will not draw the gift again.
        self.save()
        for uid, reward in plans:
            member = await self._resolve_member(int(uid))
            line = await self._apply_reward(member, reward)
            await self._shout(member, uid, reward, line, when)

    @tasks.loop(minutes=1)
    async def midnight_tick(self) -> None:
        try:
            await self._grant_due()
        except Exception:
            log.exception("Birthday tick failed")

    @midnight_tick.before_loop
    async def before_midnight_tick(self) -> None:
        await self.bot.wait_until_ready()

    def _already_saved(self, user_id) -> bool:
        rec = (self.state.get("users") or {}).get(str(user_id)) or {}
        return "month" in rec and "day" in rec

    @group.command(name="register", description="Save your birthday once. Pick a month and a day.")
    @app_commands.describe(month="Month", day="Day")
    @app_commands.choices(month=MONTH_CHOICES)
    @app_commands.autocomplete(day=complete_day)
    async def register(
        self,
        interaction: discord.Interaction,
        month: app_commands.Choice[int],
        day: str,
    ):
        if self._already_saved(interaction.user.id):
            rec = self.state["users"][str(interaction.user.id)]
            await interaction.response.send_message(
                f"Your birthday is already saved as **{format_birthday(rec['month'], rec['day'])}**. "
                "Staff can change it with `/birthday staff set`.",
                ephemeral=True,
            )
            return
        day_n = _parse_day(day)
        if day_n is None or not valid_birthday(month.value, day_n):
            await interaction.response.send_message(
                "Pick a real day for that month. February can be the 29th.",
                ephemeral=True,
            )
            return
        save_birthday(self.state, interaction.user.id, month.value, day_n)
        self.save()
        extra = ""
        if month.value == 2 and day_n == 29:
            extra = " In a year that isn't a leap year, the gift goes out on February 28."
        await interaction.response.send_message(
            f"Saved. Your birthday is **{format_birthday(month.value, day_n)}**.{extra} "
            "The shout-out and one gift go out at midnight Central. Once a year.",
            ephemeral=True,
        )

    @group.command(name="view", description="See the birthday you saved.")
    async def view(self, interaction: discord.Interaction):
        rec = (self.state.get("users") or {}).get(str(interaction.user.id)) or {}
        if "month" not in rec or "day" not in rec:
            await interaction.response.send_message(
                "You haven't saved a birthday. Use `/birthday register`.",
                ephemeral=True,
            )
            return
        when = dt.datetime.now(CHICAGO).date()
        years = rec.get("rewarded_years") or []
        gifted = when.year in years
        if is_birthday_today(rec["month"], rec["day"], when) and not gifted:
            status = "Today is the day. The shout-out goes to the announcement channel, once."
        elif gifted:
            status = "This year's gift has already been sent."
        else:
            status = "The gift goes out at midnight Central, on the day."
        leap = ""
        if int(rec["month"]) == 2 and int(rec["day"]) == 29:
            leap = " In a year that isn't a leap year, that's February 28."
        await interaction.response.send_message(
            f"Your birthday is **{format_birthday(rec['month'], rec['day'])}**.{leap}\n{status}",
            ephemeral=True,
        )

    @staff_group.command(name="set", description="Change someone's saved birthday.")
    @app_commands.describe(member="Whose birthday", month="Month", day="Day")
    @app_commands.choices(month=MONTH_CHOICES)
    @app_commands.autocomplete(day=complete_day)
    async def staff_set(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        month: app_commands.Choice[int],
        day: str,
    ):
        if not self._is_staff(interaction.user):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        day_n = _parse_day(day)
        if day_n is None or not valid_birthday(month.value, day_n):
            await interaction.response.send_message(
                "Pick a real day for that month. February can be the 29th.",
                ephemeral=True,
            )
            return
        save_birthday(self.state, member.id, month.value, day_n)
        self.save()
        await interaction.response.send_message(
            f"Saved **{member.display_name}**'s birthday as **{format_birthday(month.value, day_n)}**. "
            "A year that already received a gift does not receive another.",
            ephemeral=True,
        )

    @staff_group.command(name="clear", description="Clear someone's saved birthday.")
    @app_commands.describe(member="Whose birthday to clear")
    async def staff_clear(self, interaction: discord.Interaction, member: discord.Member):
        if not self._is_staff(interaction.user):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if not clear_birthday(self.state, member.id):
            await interaction.response.send_message(
                f"**{member.display_name}** doesn't have a birthday saved.",
                ephemeral=True,
            )
            return
        self.save()
        await interaction.response.send_message(
            f"Cleared **{member.display_name}**'s birthday. "
            "If this year was already gifted, it stays gifted.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Birthdays(bot))
