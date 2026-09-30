"""
The Velmora house points keeper.

Tracks the House Cup: who earned what, which house is ahead, and who took
the cup when the season closed. No AI, no personality - it's a ledger with
a scoreboard, and it should be boring and correct.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path

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

# Slash-command sync is expensive (Discord's 200 creates/day per guild). Railway
# redeploys on every GitHub push, so we only talk to Discord when the command
# tree actually changed. Set FORCE_COMMAND_SYNC=1 to push anyway.
FORCE_COMMAND_SYNC = os.getenv("FORCE_COMMAND_SYNC", "").strip() in ("1", "true", "True", "yes")
SYNC_TIMEOUT_SECONDS = int(os.getenv("COMMAND_SYNC_TIMEOUT", "45"))
# Chess & checkers stay unloaded until ready. Set ENABLE_BOARD_GAMES=1 to ship them.
ENABLE_BOARD_GAMES = os.getenv("ENABLE_BOARD_GAMES", "").strip() in ("1", "true", "True", "yes")

DATA_DIR = Path(__file__).resolve().parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
SYNC_STATE_PATH = STATE_DIR / "command_sync.json"


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
    "cogs.brooms",
    "cogs.broom_race",
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
    "cogs.threeraid",
    "cogs.quidditch",
    "cogs.potions",
    "cogs.antispam",  # before hexes so spam is deleted, not mangled
    "cogs.hexes",
    "cogs.marketplace",
    *(("cogs.chess", "cogs.checkers") if ENABLE_BOARD_GAMES else ()),
    "cogs.pip_wick",
    "cogs.checklist",
    "cogs.avada",
)


async def _leave_if_unauthorized(guild: discord.Guild) -> bool:
    if ALLOWED_GUILD_IDS and guild.id not in ALLOWED_GUILD_IDS:
        log.warning("Not authorized for guild %r (id=%s) - leaving.", guild.name, guild.id)
        await guild.leave()
        return True
    return False


_synced = False
# TEMPORARY (Sept 26): guild hit Discord's 200-creates/day cap. Global sync uses
# a separate bucket. Flip back to False after the guild limit has reset AND this
# hash-skip path has been redeployed, so Railway restarts stop burning creates.
SYNC_GLOBALLY = True


def _command_payload() -> list:
    """Stable JSON-serializable snapshot of the slash command tree."""
    payload = []
    for command in bot.tree._get_all_commands(guild=None):
        payload.append(command.to_dict(bot.tree))
    return payload


def _fingerprint(mode: str, targets: set[int]) -> str:
    blob = json.dumps(
        {
            "mode": mode,
            "targets": sorted(targets),
            "commands": _command_payload(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load_sync_state() -> dict:
    try:
        with open(SYNC_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read %s - treating as empty.", SYNC_STATE_PATH)
        return {}


def _save_sync_state(state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SYNC_STATE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, SYNC_STATE_PATH)
    except OSError:
        log.exception("Could not write command sync state to %s", SYNC_STATE_PATH)


async def _run_sync(coro, label: str):
    """Run a Discord sync call, but never hang the bot on a rate limit wait."""
    try:
        return await asyncio.wait_for(coro, timeout=SYNC_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log.error(
            "%s timed out after %ss (likely waiting on a Discord rate limit) - "
            "continuing without finishing sync so the bot stays online.",
            label,
            SYNC_TIMEOUT_SECONDS,
        )
        return None
    except discord.HTTPException as exc:
        # 30034 = daily application command creates exhausted.
        log.error("%s failed (%s %s): %s", label, exc.status, getattr(exc, "code", "?"), exc)
        return None


async def sync_commands():
    """Register slash commands only when the tree (or sync mode) changed.

    Railway redeploys on every GitHub push. Re-PUTting the whole command tree
    on each boot burned Discord's daily create budget and froze the bot. Skip
    when nothing changed; FORCE_COMMAND_SYNC=1 overrides.
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

    mode = "global" if (SYNC_GLOBALLY or not targets) else "guild"
    fingerprint = _fingerprint(mode, targets)
    previous = _load_sync_state()

    if (
        not FORCE_COMMAND_SYNC
        and previous.get("fingerprint") == fingerprint
        and previous.get("mode") == mode
    ):
        log.info(
            "Slash commands unchanged (%s mode, %s) - skipping Discord sync.",
            mode,
            fingerprint[:12],
        )
        return

    if FORCE_COMMAND_SYNC:
        log.info("FORCE_COMMAND_SYNC set - syncing even if unchanged.")
    else:
        log.info(
            "Slash commands changed (%s -> %s, %s mode) - syncing with Discord.",
            (previous.get("fingerprint") or "none")[:12],
            fingerprint[:12],
            mode,
        )

    ok = False
    if mode == "global":
        synced = await _run_sync(bot.tree.sync(), "Global command sync")
        if synced is not None:
            log.info("Synced %d global commands", len(synced))
            ok = True
    else:
        ok = True
        for guild_id in targets:
            guild = discord.Object(id=guild_id)
            # One overwrite per sync. (A clear-then-rebuild step used to live here; it doubled
            # the requests and tripped Discord's rate limit during rapid redeploys.)
            bot.tree.copy_global_to(guild=guild)
            synced = await _run_sync(
                bot.tree.sync(guild=guild),
                f"Guild command sync ({guild_id})",
            )
            if synced is None:
                ok = False
                break
            log.info("Synced %d commands to server %s", len(synced), guild_id)

        if ok:
            bot.tree.clear_commands(guild=None)
            cleared = await _run_sync(bot.tree.sync(), "Clear global commands")
            if cleared is None:
                ok = False
            else:
                log.info("Cleared global commands so nothing appears twice")

    if ok:
        _save_sync_state(
            {
                "fingerprint": fingerprint,
                "mode": mode,
                "targets": sorted(targets),
                "synced_at": time.time(),
            }
        )
    else:
        log.warning(
            "Command sync did not finish cleanly - not saving fingerprint, "
            "so the next boot will retry."
        )


@bot.event
async def on_guild_join(guild: discord.Guild):
    await _leave_if_unauthorized(guild)


@bot.event
async def on_ready():
    log.info("Points keeper online. Logged in as %s (id=%s)", bot.user, bot.user.id)

    try:
        from cogs.wizard_assets_bootstrap import ensure_wizard_assets
        from cogs import mirror_art
        ready = await asyncio.to_thread(ensure_wizard_assets)
        mirror_art.refresh_asset_paths()
        if ready:
            log.info("Mirror wizard assets ready at %s", mirror_art.ASSETS)
        else:
            log.warning("Mirror wizard assets not ready — /wizard portraits may fail")
    except Exception:
        log.exception("Wizard asset bootstrap failed")

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
