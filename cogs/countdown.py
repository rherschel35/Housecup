"""
A countdown to the next challenge.

    /countdown set <days> <hours> [minutes] [title] [ping]  - staff
    /countdown status                                       - anyone
    /countdown cancel                                       - staff

One countdown runs at a time. The posted message edits itself as the clock
runs down, and announces itself when it reaches zero.
"""

import json
import logging
import math
import os
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("velmora.countdown")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "countdown.json"

BAR_WIDTH = 14
MAX_DAYS = 365


def _fmt_remaining(seconds: int) -> str:
    """'2 days, 4 hours' - the two largest units that matter, so it reads
    like a sentence rather than a stopwatch."""
    if seconds <= 0:
        return "now"
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60

    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    # Minutes only matter once the big units are nearly gone.
    if minutes and not days:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if not parts:
        parts.append("less than a minute")
    return ", ".join(parts[:2])


def _bar(started_at: float, ends_at: float, now: float) -> str:
    span = max(1.0, ends_at - started_at)
    done = min(1.0, max(0.0, (now - started_at) / span))
    filled = round(BAR_WIDTH * done)
    return "█" * filled + "·" * (BAR_WIDTH - filled)


class Countdown(commands.Cog):
    group = app_commands.Group(name="countdown", description="Count down to the next challenge.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.exception("Countdown state unreadable - starting empty.")
            return {}

    def _save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save countdown state.")

    def _is_staff(self, member) -> bool:
        """Defer to the points ledger's staff rule when it's loaded, so both
        halves of the bot agree on who counts as staff."""
        store = self.bot.get_cog("Store")
        if store is not None:
            return store.is_staff(member)
        perms = getattr(member, "guild_permissions", None)
        return bool(perms and (getattr(perms, "manage_guild", False)
                               or getattr(perms, "administrator", False)))

    # -------------------------------------------------------------- embeds

    def _embed(self, *, done: bool = False) -> discord.Embed:
        cd = self.state
        ends_at = cd["ends_at"]
        now = time.time()
        # Round UP: a countdown set to "3 days, 4 hours" should say exactly
        # that, not "3 days, 3 hours" a millisecond later.
        remaining = math.ceil(ends_at - now)

        if done or remaining <= 0:
            embed = discord.Embed(
                title=cd.get("title") or "The challenge begins",
                description="**It's time.**",
                color=0xD9A441,
            )
            embed.set_footer(text="Velmora")
            return embed

        stamp = int(ends_at)
        embed = discord.Embed(
            title=cd.get("title") or "Next challenge",
            description=(
                f"Begins in **{_fmt_remaining(remaining)}**\n"
                f"<t:{stamp}:F> — <t:{stamp}:R>\n\n"
                f"`{_bar(cd['started_at'], ends_at, now)}`"
            ),
            color=0x6C5CE7,
        )
        embed.set_footer(text="Velmora • the clock updates itself")
        return embed

    # ---------------------------------------------------------- the ticker

    @tasks.loop(minutes=1)
    async def tick(self):
        """Refresh the posted message, and fire once when it hits zero."""
        cd = self.state
        if not cd or cd.get("finished"):
            return

        channel = self.bot.get_channel(cd.get("channel_id"))
        if channel is None:
            return

        try:
            message = await channel.fetch_message(cd["message_id"])
        except discord.NotFound:
            # Someone deleted the countdown message - stop chasing it.
            log.info("Countdown message is gone; clearing.")
            self.state = {}
            self._save()
            return
        except discord.DiscordException:
            log.exception("Could not fetch the countdown message.")
            return

        expired = time.time() >= cd["ends_at"]
        try:
            await message.edit(embed=self._embed(done=expired))
        except discord.DiscordException:
            log.exception("Could not edit the countdown message.")
            return

        if expired:
            cd["finished"] = True
            self._save()
            ping = f"<@&{cd['ping_role_id']}> " if cd.get("ping_role_id") else ""
            title = cd.get("title") or "The challenge"
            try:
                await channel.send(f"{ping}**{title}** starts now.")
            except discord.DiscordException:
                log.exception("Could not post the countdown announcement.")

    @tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ commands

    @group.command(name="set", description="Start a countdown to the next challenge.")
    @app_commands.describe(
        days="How many days from now",
        hours="How many hours on top of that",
        minutes="And minutes, if you want to be precise",
        title="What's being counted down to",
        ping="A role to ping when it hits zero",
    )
    async def set_countdown(self, interaction: discord.Interaction,
                            days: int = 0, hours: int = 0, minutes: int = 0,
                            title: str = "", ping: discord.Role = None):
        if not self._is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff can start a countdown.", ephemeral=True
            )
            return

        total = days * 86400 + hours * 3600 + minutes * 60
        if total <= 0:
            await interaction.response.send_message(
                "Give me a length — days, hours and minutes all came to zero.",
                ephemeral=True,
            )
            return
        if days > MAX_DAYS:
            await interaction.response.send_message(
                f"That's more than {MAX_DAYS} days out. Pick something nearer.", ephemeral=True
            )
            return

        now = time.time()
        replacing = bool(self.state and not self.state.get("finished"))

        self.state = {
            "title": title.strip(),
            "started_at": now,
            "ends_at": now + total,
            "channel_id": interaction.channel_id,
            "message_id": None,
            "ping_role_id": ping.id if ping else None,
            "finished": False,
        }

        await interaction.response.send_message(
            ("Replacing the countdown that was already running." if replacing
             else "Countdown started."),
            ephemeral=True,
        )
        message = await interaction.channel.send(embed=self._embed())
        self.state["message_id"] = message.id
        self._save()

        try:
            await message.pin()
        except discord.DiscordException:
            # Pinning is a nicety; a missing permission shouldn't break it.
            log.info("Could not pin the countdown message.")

    @group.command(name="status", description="How long until the next challenge?")
    async def status(self, interaction: discord.Interaction):
        cd = self.state
        if not cd:
            await interaction.response.send_message(
                "Nothing is being counted down right now.", ephemeral=True
            )
            return
        if cd.get("finished"):
            await interaction.response.send_message(
                f"**{cd.get('title') or 'The challenge'}** has already started.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=self._embed(), ephemeral=True)

    @group.command(name="cancel", description="Call off the running countdown.")
    async def cancel(self, interaction: discord.Interaction):
        if not self._is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff can cancel a countdown.", ephemeral=True
            )
            return
        if not self.state:
            await interaction.response.send_message("There's nothing running.", ephemeral=True)
            return

        cd, self.state = self.state, {}
        self._save()

        channel = self.bot.get_channel(cd.get("channel_id"))
        if channel and cd.get("message_id"):
            try:
                message = await channel.fetch_message(cd["message_id"])
                await message.edit(
                    embed=discord.Embed(
                        title=cd.get("title") or "Countdown",
                        description="*Called off.*",
                        color=0x5A5A5A,
                    )
                )
                await message.unpin()
            except discord.DiscordException:
                pass

        await interaction.response.send_message("Countdown cancelled.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Countdown(bot))
