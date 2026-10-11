"""
Common Room Fire — house teamwork daily.

    /stoke   - feed your house fire (once / day)
    /fire    - see every house's flame today

If enough distinct members stoke before midnight (America/Chicago), that
house earns +1 Cup point. A quiet fire is public shame at the daily tally.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.store import HOUSES, HOUSE_KEYS, house_display
from cogs.velmora_channels import (
    EVENT_ANNOUNCE_CHANNEL_ID,
    channel_mentions,
    with_study_hall_and_duels_potions,
)
from cogs.world_engine import today

log = logging.getLogger("velmora.housefire")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "house_fire_state.json"

FIRE_CHANNEL_IDS = with_study_hall_and_duels_potions()

try:
    from zoneinfo import ZoneInfo
    CHICAGO = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    CHICAGO = dt.timezone.utc

# Unique stokers needed for the midnight +1
STOKE_THRESHOLD = int(os.getenv("FIRE_STOKE_THRESHOLD", "5"))
EMBER = 0xE07A3D


def _channel_hint() -> str:
    return f"House fires are tended in {channel_mentions(FIRE_CHANNEL_IDS)}."


def _flame_label(count: int) -> str:
    if count <= 0:
        return "❄️ Cold ashes"
    if count < max(2, STOKE_THRESHOLD // 2):
        return "🕯️ Dying ember"
    if count < STOKE_THRESHOLD:
        return "🔥 Warm but hungry"
    return "🔥🔥 Roaring"


class HouseFire(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self._last_payout_day = self.state.get("last_payout_day", "")
        self.midnight_tick.start()

    def cog_unload(self) -> None:
        self.midnight_tick.cancel()

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"days": {}, "last_payout_day": ""}
        except (OSError, json.JSONDecodeError):
            log.exception("House fire state unreadable — starting empty.")
            return {"days": {}, "last_payout_day": ""}
        data.setdefault("days", {})
        data.setdefault("last_payout_day", "")
        return data

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.state["last_payout_day"] = self._last_payout_day
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save house fire state.")

    async def _require_channel(self, interaction: discord.Interaction) -> bool:
        if interaction.channel_id in FIRE_CHANNEL_IDS:
            return True
        await interaction.response.send_message(_channel_hint(), ephemeral=True)
        return False

    def day_bucket(self, day: str | None = None) -> dict:
        day = day or today()
        bucket = self.state.setdefault("days", {}).setdefault(day, {})
        for hk in HOUSE_KEYS:
            bucket.setdefault(hk, [])
        return bucket

    def stokers(self, house: str, day: str | None = None) -> list[int]:
        bucket = self.day_bucket(day)
        return [int(x) for x in bucket.get(house, [])]

    @app_commands.command(name="stoke", description="Tend your house's common-room fire (once per day).")
    async def stoke(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        store = self.bot.get_cog("Store")
        if not store:
            await interaction.response.send_message(
                "The ledger isn't loaded.", ephemeral=True
            )
            return
        house = store.member_house(interaction.user)
        if not house:
            await interaction.response.send_message(
                "You need a house before you can tend its fire.", ephemeral=True
            )
            return
        day = today()
        bucket = self.day_bucket(day)
        stokers = [int(x) for x in bucket.get(house, [])]
        if interaction.user.id in stokers:
            await interaction.response.send_message(
                f"You've already stoked {house_display(house)}'s fire today. "
                f"It's {_flame_label(len(stokers)).lower()} "
                f"({len(stokers)}/{STOKE_THRESHOLD}).",
                ephemeral=True,
            )
            return
        stokers.append(interaction.user.id)
        bucket[house] = stokers
        # prune old days (keep ~14)
        days = self.state.get("days", {})
        if len(days) > 16:
            for old in sorted(days)[:-14]:
                days.pop(old, None)
        self.save()
        n = len(stokers)
        meta = HOUSES[house]
        embed = discord.Embed(
            title=f"{meta['emoji']} Fire stoked — {meta['name']}",
            description=(
                f"{interaction.user.mention} feeds the common-room fire.\n\n"
                f"{_flame_label(n)} · **{n}/{STOKE_THRESHOLD}** stokers today\n"
                + (
                    "Midnight will bring the house **+1** if it stays roaring."
                    if n >= STOKE_THRESHOLD
                    else "Needs more hands before midnight…"
                )
            ),
            color=meta["color"],
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="fire", description="How every house's common-room fire is doing today.")
    async def fire_status(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        day = today()
        bucket = self.day_bucket(day)
        lines = []
        for hk in HOUSE_KEYS:
            meta = HOUSES[hk]
            n = len(bucket.get(hk, []))
            mark = "✅" if n >= STOKE_THRESHOLD else "⬜"
            lines.append(
                f"{mark} {meta['emoji']} **{meta['name']}** — "
                f"{_flame_label(n)} · **{n}/{STOKE_THRESHOLD}**"
            )
        embed = discord.Embed(
            title="🔥 Common Room Fires",
            description="\n".join(lines),
            color=EMBER,
        )
        embed.set_footer(
            text=f"America/Chicago day {day} · {STOKE_THRESHOLD}+ stokers = +1 at midnight"
        )
        await interaction.response.send_message(embed=embed)

    def _announce_channel_id(self) -> int:
        raw = os.getenv("EVENT_ANNOUNCE_CHANNEL_ID", "").strip()
        if raw.isdigit():
            return int(raw)
        return EVENT_ANNOUNCE_CHANNEL_ID

    async def _payout_if_needed(self) -> None:
        """Pay yesterday's fires once we're on a new Chicago day."""
        now = dt.datetime.now(CHICAGO)
        # Pay for the day that just ended when we roll past midnight.
        yesterday = (now.date() - dt.timedelta(days=1)).isoformat()
        if self._last_payout_day == yesterday:
            return
        if yesterday not in self.state.get("days", {}):
            # Nothing recorded — still mark paid so we don't loop forever
            # once the day exists empty… only skip if truly nothing ever.
            self._last_payout_day = yesterday
            self.save()
            return

        store = self.bot.get_cog("Store")
        lines = []
        for hk in HOUSE_KEYS:
            meta = HOUSES[hk]
            n = len(self.stokers(hk, yesterday))
            if n >= STOKE_THRESHOLD and store:
                store.record(
                    house=hk,
                    delta=1,
                    actor_id=self.bot.user.id if self.bot.user else 0,
                    target_id=0,
                    reason=f"Common room fire ({n} stokers)",
                )
                lines.append(
                    f"✅ {meta['emoji']} **{meta['name']}** roared ({n}) — **+1**"
                )
            elif n <= 0:
                lines.append(
                    f"❄️ {meta['emoji']} **{meta['name']}** — cold ashes. Shameful."
                )
            else:
                lines.append(
                    f"🕯️ {meta['emoji']} **{meta['name']}** faded ({n}/{STOKE_THRESHOLD}). No bonus."
                )

        self._last_payout_day = yesterday
        self.save()

        ch = self.bot.get_channel(self._announce_channel_id())
        if ch and hasattr(ch, "send") and lines:
            embed = discord.Embed(
                title="🔥 Midnight fire tally",
                description="\n".join(lines),
                color=EMBER,
            )
            embed.set_footer(text=f"For Chicago day {yesterday}")
            try:
                await ch.send(embed=embed)
            except discord.DiscordException:
                log.exception("Fire tally announce failed")

    @tasks.loop(minutes=1)
    async def midnight_tick(self) -> None:
        try:
            await self._payout_if_needed()
        except Exception:
            log.exception("House fire midnight tick failed")

    @midnight_tick.before_loop
    async def before_midnight_tick(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(HouseFire(bot))
