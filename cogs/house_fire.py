"""
Common Room Fire — house teamwork daily.

    /stoke   - feed your house fire (once / day, private reply + art)
    /fire    - see every house's flame today

Rules (America/Chicago day):
  • 5 unique stokers keeps a house fire alive
  • At midnight, the house(s) with the *most* stokers earn +3 Cup
    (must have kept the fire alive — 5+)
  • Any house under 5 is announced as gone out in the event channel,
    with an embarrassing sarcastic line
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import os
import random
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
ART_DIR = Path(__file__).resolve().parent.parent / "fire_art_assets"
STOKE_ART = ART_DIR / "stoke.jpg"

FIRE_CHANNEL_IDS = with_study_hall_and_duels_potions()

try:
    from zoneinfo import ZoneInfo
    CHICAGO = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    CHICAGO = dt.timezone.utc

# Unique stokers needed to keep the fire alive for the day
STOKE_THRESHOLD = int(os.getenv("FIRE_STOKE_THRESHOLD", "5"))
# Cup points to the house(s) with the most stokers (must be alive)
WINNER_PTS = int(os.getenv("FIRE_WINNER_PTS", "3"))
EMBER = 0xE07A3D

OUTAGE_LINES = (
    "{emoji} **{name}** let the hearth die ({n}/{need}). The portraits are taking notes. Unkind ones.",
    "{emoji} **{name}** — fire's out ({n}/{need}). Even the soot looks disappointed.",
    "{emoji} **{name}** couldn't muster {need} hands ({n}). Cold ashes. Hot shame.",
    "{emoji} **{name}**'s common room is a fridge now ({n}/{need}). Someone fetch a match. Or pride.",
    "{emoji} **{name}** extinguished itself through sheer neglect ({n}/{need}). Historic, in a way.",
    "{emoji} **{name}** — {n} stoker(s). Needed {need}. The broom cupboard was warmer.",
    "{emoji} **{name}** went dark ({n}/{need}). Prefects are drafting a very polite scolding.",
    "{emoji} **{name}** treated the fire like optional homework ({n}/{need}). Graded: F for Flame.",
)


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


def _stoke_file() -> discord.File | None:
    if not STOKE_ART.exists():
        return None
    return discord.File(io.BytesIO(STOKE_ART.read_bytes()), filename="stoke.jpg")


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

    def _shame_channel_id(self) -> int:
        """Fires-out + winner tally post here."""
        raw = os.getenv("EVENT_ANNOUNCE_CHANNEL_ID", "").strip()
        if raw.isdigit():
            return int(raw)
        return EVENT_ANNOUNCE_CHANNEL_ID

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
        days = self.state.get("days", {})
        if len(days) > 16:
            for old in sorted(days)[:-14]:
                days.pop(old, None)
        self.save()
        n = len(stokers)
        meta = HOUSES[house]
        alive = n >= STOKE_THRESHOLD
        embed = discord.Embed(
            title=f"{meta['emoji']} Fire stoked — {meta['name']}",
            description=(
                f"You feed the common-room fire.\n\n"
                f"{_flame_label(n)} · **{n}/{STOKE_THRESHOLD}** stokers today\n"
                + (
                    f"Alive for tonight. Most stokers at midnight earn the house "
                    f"**+{WINNER_PTS}**."
                    if alive
                    else f"Needs **{STOKE_THRESHOLD - n}** more before midnight "
                    f"or it goes out — publicly."
                )
            ),
            color=meta["color"],
        )
        file = _stoke_file()
        if file:
            embed.set_image(url="attachment://stoke.jpg")
            await interaction.response.send_message(
                embed=embed, file=file, ephemeral=True
            )
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="fire", description="How every house's common-room fire is doing today.")
    async def fire_status(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        day = today()
        bucket = self.day_bucket(day)
        lines = []
        counts = {hk: len(bucket.get(hk, [])) for hk in HOUSE_KEYS}
        best = max(counts.values()) if counts else 0
        for hk in HOUSE_KEYS:
            meta = HOUSES[hk]
            n = counts[hk]
            mark = "✅" if n >= STOKE_THRESHOLD else "⬜"
            lead = " · leading" if n == best and n > 0 else ""
            lines.append(
                f"{mark} {meta['emoji']} **{meta['name']}** — "
                f"{_flame_label(n)} · **{n}/{STOKE_THRESHOLD}**{lead}"
            )
        embed = discord.Embed(
            title="🔥 Common Room Fires",
            description="\n".join(lines),
            color=EMBER,
        )
        embed.set_footer(
            text=(
                f"America/Chicago day {day} · {STOKE_THRESHOLD}+ keeps it alive · "
                f"most stokers = +{WINNER_PTS} at midnight"
            )
        )
        await interaction.response.send_message(embed=embed)

    async def _payout_if_needed(self) -> None:
        """Score yesterday's fires once we're on a new Chicago day."""
        now = dt.datetime.now(CHICAGO)
        yesterday = (now.date() - dt.timedelta(days=1)).isoformat()
        if self._last_payout_day == yesterday:
            return
        if yesterday not in self.state.get("days", {}):
            self._last_payout_day = yesterday
            self.save()
            return

        store = self.bot.get_cog("Store")
        counts = {hk: len(self.stokers(hk, yesterday)) for hk in HOUSE_KEYS}
        best = max(counts.values()) if counts else 0
        winners = [
            hk
            for hk, n in counts.items()
            if n == best and n >= STOKE_THRESHOLD
        ]

        tally_lines: list[str] = []
        outage_lines: list[str] = []

        for hk in HOUSE_KEYS:
            meta = HOUSES[hk]
            n = counts[hk]
            if hk in winners and store:
                store.record(
                    house=hk,
                    delta=WINNER_PTS,
                    actor_id=self.bot.user.id if self.bot.user else 0,
                    target_id=0,
                    reason=f"Common room fire — most stokers ({n})",
                )
                tally_lines.append(
                    f"🏆 {meta['emoji']} **{meta['name']}** kept it roaring "
                    f"with **{n}** stokers — **+{WINNER_PTS}**"
                )
            elif n >= STOKE_THRESHOLD:
                tally_lines.append(
                    f"✅ {meta['emoji']} **{meta['name']}** stayed lit ({n}) — "
                    f"not enough hands to take the crown."
                )
            else:
                line = random.choice(OUTAGE_LINES).format(
                    emoji=meta["emoji"],
                    name=meta["name"],
                    n=n,
                    need=STOKE_THRESHOLD,
                )
                outage_lines.append(line)
                tally_lines.append(
                    f"❄️ {meta['emoji']} **{meta['name']}** — out ({n}/{STOKE_THRESHOLD})"
                )

        if best > 0 and not winners:
            tally_lines.insert(
                0,
                f"No house reached **{STOKE_THRESHOLD}** stokers. "
                f"No **+{WINNER_PTS}** tonight. Tragic.",
            )

        self._last_payout_day = yesterday
        self.save()

        ch = self.bot.get_channel(self._shame_channel_id())
        if not (ch and hasattr(ch, "send")):
            return

        try:
            if outage_lines:
                shame = discord.Embed(
                    title="🔥 Fires that went out",
                    description="\n".join(outage_lines),
                    color=0x5A3A2A,
                )
                shame.set_footer(text=f"Chicago day {yesterday} · needed {STOKE_THRESHOLD}")
                await ch.send(embed=shame)

            if tally_lines:
                tally = discord.Embed(
                    title="🔥 Midnight fire tally",
                    description="\n".join(tally_lines),
                    color=EMBER,
                )
                tally.set_footer(
                    text=f"For Chicago day {yesterday} · most alive stokers = +{WINNER_PTS}"
                )
                await ch.send(embed=tally)
        except discord.DiscordException:
            log.exception("Fire announce failed")

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
