"""
Anti-spam guard. Personal cool-downs only — no mutes, bans, or timeouts.

    /antispam on      - (staff) turn the guard on
    /antispam off     - (staff) turn the guard off
    /antispam status  - (staff) whether it's on, and the cool-down ladder

Trip: 5 messages in 8 seconds, per user per channel (sliding window).
When a user trips (or is already on cool-down), their overflowing messages
are deleted and the castle posts one soft public line per trip:

    The castle asks you to catch your breath…

Cool-down ladder within a rolling 24h window of trips
(resets after 24h quiet → back to early tiers):
    1st trip → 20s personal pause
    2nd trip → 30s
    3rd+    → 60s each

Ignores bots, webhooks, and staff (Store.is_staff). Server-wide.
Runs before Hexes so spam is deleted, not mangled — Hexes calls
should_block() at the start of its on_message.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.antispam")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "antispam.json"

# Trip detection: N messages inside WINDOW_SECONDS, per user per channel.
WINDOW_SECONDS = 8
THRESHOLD = 5

# Cool-down ladder (seconds) keyed by trip count within the 24h window.
# Index 0 = 1st trip, 1 = 2nd, 2+ = 3rd and beyond.
COOLDOWN_LADDER = (20, 30, 60)
TRIP_HISTORY_WINDOW = 24 * 3600

SOFT_LINE = "The castle asks you to catch your breath…"


class AntiSpam(commands.Cog):
    group = app_commands.Group(
        name="antispam",
        description="(staff) Personal cool-down anti-spam guard.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        # (user_id, channel_id) -> recent message timestamps (sliding window)
        self._windows: dict[tuple[int, int], deque[float]] = defaultdict(
            lambda: deque(maxlen=THRESHOLD)
        )
        # Message IDs suppressed this process — Hexes can check should_block.
        self._suppressed: set[int] = set()

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        except (OSError, json.JSONDecodeError):
            log.exception("Anti-spam state unreadable - starting fresh.")
            state = {}
        state.setdefault("enabled", True)
        state.setdefault("users", {})
        return state

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save anti-spam state.")

    # -------------------------------------------------------- permissions

    def _is_staff(self, member) -> bool:
        store = self.bot.get_cog("Store")
        if store is not None:
            return store.is_staff(member)
        perms = getattr(member, "guild_permissions", None)
        return bool(
            perms
            and (getattr(perms, "manage_guild", False)
                 or getattr(perms, "administrator", False))
        )

    # --------------------------------------------------------- cool-downs

    def _user_rec(self, user_id: int) -> dict:
        key = str(user_id)
        rec = self.state["users"].setdefault(key, {"trips": [], "cooldown_until": 0})
        rec.setdefault("trips", [])
        rec.setdefault("cooldown_until", 0)
        return rec

    def _prune_trips(self, rec: dict, now: float) -> list[float]:
        trips = [t for t in rec.get("trips", []) if now - t < TRIP_HISTORY_WINDOW]
        rec["trips"] = trips
        return trips

    def _cooldown_for_trip_count(self, trip_number: int) -> int:
        """trip_number is 1-based (1 = first trip in the window)."""
        if trip_number <= 1:
            return COOLDOWN_LADDER[0]
        if trip_number == 2:
            return COOLDOWN_LADDER[1]
        return COOLDOWN_LADDER[2]

    def is_on_cooldown(self, user_id: int, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        rec = self.state["users"].get(str(user_id))
        if not rec:
            return False
        return float(rec.get("cooldown_until") or 0) > now

    def should_block(self, message: discord.Message) -> bool:
        """True if anti-spam has suppressed this message or the author is paused.

        Hexes (and any other message rewriter) should call this at the start
        of on_message and return early when True, so spam is deleted rather
        than mangled/relayed.
        """
        if not self.state.get("enabled", True):
            return False
        if message.id in self._suppressed:
            return True
        if message.guild is None or getattr(message.author, "bot", False):
            return False
        if message.webhook_id is not None:
            return False
        return self.is_on_cooldown(message.author.id)

    # ----------------------------------------------------------- handling

    def _exempt(self, message: discord.Message) -> bool:
        if not self.state.get("enabled", True):
            return True
        if message.guild is None:
            return True
        if getattr(message.author, "bot", False) or message.webhook_id is not None:
            return True
        member = message.author
        if isinstance(member, discord.Member) and self._is_staff(member):
            return True
        return False

    def _record_trip(self, user_id: int, now: float) -> int:
        """Register a trip; return cool-down seconds applied."""
        rec = self._user_rec(user_id)
        trips = self._prune_trips(rec, now)
        trips.append(now)
        rec["trips"] = trips
        seconds = self._cooldown_for_trip_count(len(trips))
        rec["cooldown_until"] = now + seconds
        self.save()
        return seconds

    async def _delete(self, message: discord.Message) -> None:
        self._suppressed.add(message.id)
        # Bound memory: keep only recent suppressions.
        if len(self._suppressed) > 500:
            # Drop an arbitrary half; IDs are not ordered, but that's fine.
            self._suppressed = set(list(self._suppressed)[-250:])
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.DiscordException:
            log.exception(
                "Could not delete spam message in #%s",
                getattr(message.channel, "name", message.channel.id),
            )

    async def _soft_line(self, channel: discord.abc.Messageable) -> None:
        try:
            await channel.send(SOFT_LINE)
        except discord.DiscordException:
            log.exception("Could not post anti-spam soft line.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if self._exempt(message):
            return

        now = time.time()
        uid = message.author.id
        cid = message.channel.id

        # Already on a personal pause — delete, no extra soft line.
        if self.is_on_cooldown(uid, now):
            await self._delete(message)
            return

        window = self._windows[(uid, cid)]
        # Drop timestamps outside the sliding window.
        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()
        window.append(now)

        if len(window) < THRESHOLD:
            return

        # Trip: clear the window so we don't re-trip on every following message
        # (cool-down handles those), apply ladder, delete, soft line once.
        window.clear()
        self._record_trip(uid, now)
        await self._delete(message)
        await self._soft_line(message.channel)

    # ------------------------------------------------------------ commands

    @group.command(name="on", description="(staff) Turn the anti-spam guard on.")
    async def antispam_on(self, interaction: discord.Interaction):
        if not self._is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff can change the anti-spam guard.", ephemeral=True
            )
            return
        self.state["enabled"] = True
        self.save()
        await interaction.response.send_message(
            "Anti-spam is **on**. Five messages in eight seconds earns a personal pause.",
            ephemeral=True,
        )

    @group.command(name="off", description="(staff) Turn the anti-spam guard off.")
    async def antispam_off(self, interaction: discord.Interaction):
        if not self._is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff can change the anti-spam guard.", ephemeral=True
            )
            return
        self.state["enabled"] = False
        self.save()
        await interaction.response.send_message(
            "Anti-spam is **off**. No cool-downs will be applied.",
            ephemeral=True,
        )

    @group.command(name="status", description="(staff) Whether anti-spam is on, and the ladder.")
    async def antispam_status(self, interaction: discord.Interaction):
        if not self._is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff can check the anti-spam guard.", ephemeral=True
            )
            return
        enabled = self.state.get("enabled", True)
        now = time.time()
        active = sum(
            1
            for rec in self.state.get("users", {}).values()
            if float(rec.get("cooldown_until") or 0) > now
        )
        embed = discord.Embed(
            title="Anti-spam",
            description=(
                f"**{'On' if enabled else 'Off'}** · "
                f"{THRESHOLD} messages in {WINDOW_SECONDS}s per user per channel\n\n"
                f"Cool-down ladder (rolling 24h):\n"
                f"· 1st trip → {COOLDOWN_LADDER[0]}s\n"
                f"· 2nd trip → {COOLDOWN_LADDER[1]}s\n"
                f"· 3rd+ → {COOLDOWN_LADDER[2]}s each\n\n"
                f"Currently paused: **{active}**"
            ),
            color=0x5B7C5A if enabled else 0x5A5A5A,
        )
        embed.set_footer(text="Personal pauses only — no mutes, bans, or timeouts.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiSpam(bot))
