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
# Discord rate-limit waits can exceed 45s; cancelling mid-wait left guild
# commands half-wiped and clients stuck on flat /familiar · /play.
SYNC_TIMEOUT_SECONDS = int(os.getenv("COMMAND_SYNC_TIMEOUT", "600"))
# Bump when sync *behavior* changes (e.g. also overwrite guild commands) so
# the next boot re-PUTs even if the slash tree fingerprint is unchanged.
COMMAND_SYNC_REVISION = 7
# Chess & checkers load by default. Set ENABLE_BOARD_GAMES=0 to unload them.
_ENABLE_BOARD_GAMES_RAW = os.getenv("ENABLE_BOARD_GAMES", "1").strip().lower()
ENABLE_BOARD_GAMES = _ENABLE_BOARD_GAMES_RAW not in ("0", "false", "no", "off", "")

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
    "cogs.staff",  # owns /staff root; other cogs nest staff tools under it
    "cogs.usage",  # counts slash uses; /staff usage top|unused|reset
    "cogs.points",
    "cogs.board",
    "cogs.admin",
    "cogs.countdown",
    "cogs.quests",
    "cogs.wands",
    "cogs.patronus",
    "cogs.animagus",
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
    "cogs.castles",
    "cogs.threeraid",
    "cogs.quidditch",
    "cogs.potions",
    "cogs.antispam",  # before hexes so spam is deleted, not mangled
    "cogs.hexes",
    # animagus echo relay runs after hexes so chat hexes keep priority
    "cogs.marketplace",
    *(("cogs.chess", "cogs.checkers") if ENABLE_BOARD_GAMES else ()),
    "cogs.pip_wick",
    "cogs.checklist",
    "cogs.avada",
    "cogs.broke",
    "cogs.forrest_caden",  # private test: The Forrest of Caden Ch 1–2 (Gon + break-room)
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
            "revision": COMMAND_SYNC_REVISION,
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


async def _run_sync(coro, label: str, *, timeout: int | None = None):
    """Run a Discord sync call.

    Default timeout is long enough for Discord.py to sleep through 429s.
    The old 45s cap cancelled those waits and left stale guild commands up.
    """
    limit = SYNC_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        if limit and limit > 0:
            return await asyncio.wait_for(coro, timeout=limit)
        return await coro
    except asyncio.TimeoutError:
        log.error(
            "%s timed out after %ss (likely waiting on a Discord rate limit) - "
            "continuing without finishing sync so the bot stays online.",
            label,
            limit,
        )
        return None
    except discord.HTTPException as exc:
        # 30034 = daily application command creates exhausted.
        log.error("%s failed (%s %s): %s", label, exc.status, getattr(exc, "code", "?"), exc)
        return None


STALE_CARE_COMMANDS = ("play", "feed", "pet", "scout")


def _sync_targets() -> set[int]:
    targets = set(ALLOWED_GUILD_IDS or ())
    if DEV_GUILD_ID:
        targets.add(int(DEV_GUILD_ID))
    for guild in bot.guilds:
        targets.add(guild.id)
    return targets


async def _wipe_guild_commands(guild_id: int) -> bool:
    """Delete every guild-scoped slash command so globals are visible.

    Uses the raw HTTP bulk-upsert (empty list) so we don't depend on the
    local CommandTree guild map. Does not reinstall — guild copies were
    what kept flat /familiar · /play alive.
    """
    app_id = bot.application_id
    if app_id is None:
        log.error("Cannot wipe guild %s — bot.application_id is unset.", guild_id)
        return False

    # Drop any local guild map so we never accidentally re-PUT old cmds later.
    bot.tree.clear_commands(guild=discord.Object(id=guild_id))

    try:
        result = await bot.http.bulk_upsert_guild_commands(app_id, guild_id, [])
    except discord.HTTPException as exc:
        log.error(
            "Guild wipe HTTP failed for %s (%s %s): %s",
            guild_id,
            exc.status,
            getattr(exc, "code", "?"),
            exc,
        )
        return False
    except Exception:
        log.exception("Guild wipe crashed for %s", guild_id)
        return False

    if result:
        log.error(
            "Guild %s wipe returned %d commands (expected 0): %s",
            guild_id,
            len(result),
            [c.get("name") for c in result[:20]],
        )
        return False

    try:
        fetched = await bot.http.get_guild_commands(app_id, guild_id)
    except Exception:
        log.exception("Could not fetch guild %s commands after wipe", guild_id)
        return False

    if fetched:
        names = [c.get("name") for c in fetched]
        log.error("Guild %s still has commands after wipe: %s", guild_id, names[:20])
        return False

    log.info("Guild %s slash commands CLEARED — globals will show through.", guild_id)
    return True


async def sync_commands(*, force: bool = False):
    """Register slash commands only when the tree (or sync mode) changed.

    Railway redeploys on every GitHub push. Re-PUTting the whole command tree
    on each boot burned Discord's daily create budget and froze the bot. Skip
    when nothing changed; FORCE_COMMAND_SYNC=1 (or force=True) overrides.

    Guild wipe runs first (and retries every boot until wipe_revision matches)
    so flat /familiar · /play stop shadowing nested globals — even if the
    staff slash /synccmds is invisible under the old guild tree.
    """
    # on_ready fires again after every reconnect; syncing once per start is
    # enough, and avoids hammering Discord's rate limits. Staff /staff sync
    # may call again with force=True in the same process.
    global _synced
    if _synced and not force:
        return True
    if not force:
        _synced = True

    targets = _sync_targets()
    mode = "global" if (SYNC_GLOBALLY or not targets) else "guild"
    fingerprint = _fingerprint(mode, targets)
    previous = _load_sync_state()
    wipe_needed = previous.get("wipe_revision") != COMMAND_SYNC_REVISION

    if (
        not force
        and not FORCE_COMMAND_SYNC
        and not wipe_needed
        and previous.get("fingerprint") == fingerprint
        and previous.get("mode") == mode
        and previous.get("guilds_wiped")
    ):
        log.info(
            "Slash commands unchanged (%s mode, %s) - skipping Discord sync.",
            mode,
            fingerprint[:12],
        )
        return True

    if force or FORCE_COMMAND_SYNC or wipe_needed:
        log.info(
            "%s - syncing slash commands with Discord (wipe_needed=%s).",
            (
                "Staff-forced sync" if force
                else "FORCE_COMMAND_SYNC set" if FORCE_COMMAND_SYNC
                else "Wipe revision bump"
            ),
            wipe_needed,
        )
    else:
        log.info(
            "Slash commands changed (%s -> %s, %s mode) - syncing with Discord.",
            (previous.get("fingerprint") or "none")[:12],
            fingerprint[:12],
            mode,
        )

    ok = True

    # 1) Wipe guild shadows FIRST — this is what unblocks nested /familiar.
    if mode == "global":
        if not targets:
            log.warning(
                "No guild ids to wipe — stale guild commands may still shadow /familiar."
            )
            ok = False
        for guild_id in targets:
            if not await _wipe_guild_commands(guild_id):
                ok = False
                break

    # 2) Push the current global (or guild-dev) tree.
    if mode == "global":
        synced = await _run_sync(bot.tree.sync(), "Global command sync")
        if synced is None:
            ok = False
        else:
            log.info("Synced %d global commands", len(synced))
            names = sorted(c.name for c in synced)
            if "familiar" not in names:
                log.error("Global sync missing /familiar — tree may not have loaded.")
                ok = False
            if "synccmds" not in names:
                log.warning("Global sync missing /synccmds (may still be deploying).")
    else:
        for guild_id in targets:
            guild = discord.Object(id=guild_id)
            bot.tree.clear_commands(guild=guild)
            bot.tree.copy_global_to(guild=guild)
            synced = await _run_sync(
                bot.tree.sync(guild=guild),
                f"Guild command sync ({guild_id})",
            )
            if synced is None:
                ok = False
                break
            names = sorted(c.name for c in synced)
            stale = sorted(n for n in STALE_CARE_COMMANDS if n in names)
            if stale or "familiar" not in names:
                log.error(
                    "Guild %s sync bad (stale=%s familiar=%s)",
                    guild_id,
                    stale,
                    "familiar" in names,
                )
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
        _synced = True
        _save_sync_state(
            {
                "fingerprint": fingerprint,
                "mode": mode,
                "targets": sorted(targets),
                "guilds_wiped": mode == "global",
                "wipe_revision": COMMAND_SYNC_REVISION,
                "synced_at": time.time(),
            }
        )
        log.info(
            "Command sync OK — guilds wiped=%s wipe_revision=%s. "
            "Clients must fully quit Discord to refresh /familiar.",
            mode == "global",
            COMMAND_SYNC_REVISION,
        )
    else:
        # Allow another on_ready / staff sync attempt in this process.
        if not force:
            _synced = False
        log.warning(
            "Command sync did not finish cleanly - not saving fingerprint, "
            "so the next boot will retry. Staff: type !synccmds in chat "
            "(works even when slash /synccmds is missing)."
        )
    return ok


@bot.event
async def on_guild_join(guild: discord.Guild):
    await _leave_if_unauthorized(guild)


@bot.event
async def on_message(message: discord.Message):
    """Staff can type !synccmds even when slash /synccmds is invisible.

    Old guild-scoped /familiar · /play shadow the slash tree, so staff had no
    slash way to trigger a wipe. A plain chat trigger breaks that deadlock.
    """
    if message.author.bot or not message.guild:
        return
    text = (message.content or "").strip().lower()
    if text not in ("!synccmds", "!wipecommands", "!synccommands"):
        return

    store = bot.get_cog("Store")
    if not (store and store.is_staff(message.author)):
        await message.reply("That's for staff.", mention_author=False)
        return

    status = await message.reply(
        "Wiping guild slash commands and refreshing globals…",
        mention_author=False,
    )
    try:
        ok = await sync_commands(force=True)
    except Exception:
        log.exception("!synccmds failed")
        await status.edit(content="Command sync crashed — check Railway logs.")
        return

    if ok:
        await status.edit(
            content=(
                "Done. Guild slash lists wiped; globals refreshed.\n"
                "**Fully quit Discord** (swipe away on mobile) and reopen `/`.\n"
                "• `/familiar` should show status / adopt / feed / pet / play / scout\n"
                "• Top-level `/play` · `/feed` · `/scout` should be gone"
            )
        )
    else:
        await status.edit(
            content=(
                "Sync didn't finish cleanly (rate limit or timeout). "
                "Wait a few minutes and type `!synccmds` again, or check Railway logs."
            )
        )


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
        # Discord caps global slash commands at 100 top-level names. A single
        # failed cog must not take the whole House Cup bot down.
        for cog in INITIAL_COGS:
            try:
                await bot.load_extension(cog)
                log.info("Loaded %s", cog)
            except Exception:
                log.exception("Failed to load %s — continuing without it", cog)
        from cogs.staff_groups import nest_pure_staff_groups

        nest_pure_staff_groups(bot)
        top_level = len(bot.tree.get_commands())
        log.info("Slash command tree: %s top-level (Discord global cap is 100)", top_level)
        if top_level > 100:
            log.error(
                "Top-level slash command count %s exceeds Discord's 100 cap — "
                "some commands will not register until groups are nested further.",
                top_level,
            )
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
