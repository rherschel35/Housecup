"""
The Velmora house points keeper.

Tracks the House Cup: who earned what, which house is ahead, and who took
the cup when the season closed. No AI, no personality - it's a ledger with
a scoreboard, and it should be boring and correct.
"""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("velmora.points-bot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")  # optional: instant command sync while testing


def _parse_guild_ids(env_value: str | None):
    """Comma-separated list of servers this bot is allowed to sit in."""
    if not env_value:
        return None
    ids = set()
    for part in env_value.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids or None


ALLOWED_GUILD_IDS = _parse_guild_ids(os.getenv("ALLOWED_GUILD_IDS"))

intents = discord.Intents.default()
intents.members = True  # needed to read house roles and resolve members
intents.message_content = True  # needed for secrets typed in a place's channel (e.g. #gardens)

bot = commands.Bot(command_prefix="!velmora-points-unused-", intents=intents, help_command=None)

INITIAL_COGS = (
    "cogs.store",
    "cogs.points",
    "cogs.board",
    "cogs.admin",
    "cogs.countdown",
    "cogs.quests",
    "cogs.wands",
    "cogs.patronus",
    "cogs.duels",
    "cogs.beans",
    "cogs.triwizard",
    "cogs.profile",
    "cogs.help",
    "cogs.world",
    "cogs.rumors",
    "cogs.dementors",
    "cogs.familiars",
    "cogs.beasts",
    "cogs.adornments",
    "cogs.reaction_roles",
    "cogs.descent",
    "cogs.quidditch",
)


async def _leave_if_unauthorized(guild: discord.Guild) -> bool:
    if ALLOWED_GUILD_IDS and guild.id not in ALLOWED_GUILD_IDS:
        log.warning("Not authorized for guild %r (id=%s) - leaving.", guild.name, guild.id)
        await guild.leave()
        return True
    return False


_synced = False


async def sync_commands():
    """Register slash commands where they'll appear fastest.

    Commands registered for all of Discord ("global") can take a long time
    to show up in people's apps, so a brand-new command may not pop up when
    someone types /. This bot only ever lives in Velmora, so it registers
    straight to the server instead - those appear immediately.

    The old global copies are then cleared, otherwise every command would
    show up twice in the list.
    """
    # on_ready fires again after every reconnect; syncing once per start is
    # enough, and avoids hammering Discord's rate limits.
    global _synced
    if _synced:
        return
    _synced = True

    targets = set(ALLOWED_GUILD_IDS or ())
    if DEV_GUILD_ID:
        targets.add(int(DEV_GUILD_ID))

    if not targets:
        synced = await bot.tree.sync()
        log.info("Synced %d global commands (no server set, so registered globally)",
                 len(synced))
        return

    for guild_id in targets:
        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        log.info("Synced %d commands to server %s", len(synced), guild_id)

    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()
    log.info("Cleared global commands so nothing appears twice")


@bot.event
async def on_guild_join(guild: discord.Guild):
    await _leave_if_unauthorized(guild)


@bot.event
async def on_ready():
    log.info("Points keeper online. Logged in as %s (id=%s)", bot.user, bot.user.id)

    for guild in list(bot.guilds):
        await _leave_if_unauthorized(guild)

    try:
        await sync_commands()
    except Exception:
        log.exception("Slash command sync failed")

    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="the House Cup")
    )


async def main():
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")

    async with bot:
        for cog in INITIAL_COGS:
            await bot.load_extension(cog)
            log.info("Loaded %s", cog)
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
