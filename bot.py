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
COMMAND_SYNC_REVISION = 19
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
    # Forrest of Caden parked for later — re-add "cogs.forrest_caden" when ready.
)


async def _leave_if_unauthorized(guild: discord.Guild) -> bool:
    if ALLOWED_GUILD_IDS and guild.id not in ALLOWED_GUILD_IDS:
        log.warning("Not authorized for guild %r (id=%s) - leaving.", guild.name, guild.id)
        await guild.leave()
        return True
    return False


_synced = False
_sync_lock = asyncio.Lock()
_last_sync_errors: list[str] = []
_last_sync_outcome: str = "full"  # "full" | "wipe_only" (set during sync_commands)
WIPE_VERIFY_ATTEMPTS = 5
WIPE_VERIFY_DELAY_SEC = 2.0


def _clear_sync_errors() -> None:
    _last_sync_errors.clear()


def _note_sync_error(message: str) -> None:
    log.error(message)
    short = message.strip()
    if short and short not in _last_sync_errors:
        _last_sync_errors.append(short[:500])


def sync_failure_hint() -> str:
    """Short staff-facing detail after a failed sync (Railway logs have the rest)."""
    if not _last_sync_errors:
        return ""
    return " Details: " + " | ".join(_last_sync_errors[:3])


def sync_outcome_is_wipe_only() -> bool:
    return _last_sync_outcome == "wipe_only"


# TEMPORARY (Sept 26): guild hit Discord's 200-creates/day cap. Global sync uses
# a separate bucket. Flip back to False after the guild limit has reset AND this
# hash-skip path has been redeployed, so Railway restarts stop burning creates.
SYNC_GLOBALLY = True


DISCORD_COMMAND_GROUP_MAX_CHARS = 8000


def _command_group_json_size(cmd) -> int:
    import json

    return len(json.dumps(cmd.to_dict(bot.tree), separators=(",", ":")))


def _warn_oversized_command_groups() -> None:
    """Discord rejects command groups whose serialized definition exceeds 8000 chars."""
    from discord import app_commands

    for cmd in bot.tree.get_commands():
        if not isinstance(cmd, app_commands.Group):
            continue
        size = _command_group_json_size(cmd)
        if size > DISCORD_COMMAND_GROUP_MAX_CHARS:
            log.error(
                "Slash group /%s is %s chars (Discord max %s) — global sync will fail.",
                cmd.name,
                size,
                DISCORD_COMMAND_GROUP_MAX_CHARS,
            )


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
        _note_sync_error(f"{label} timed out after {limit}s (Discord rate limit?).")
        return None
    except discord.HTTPException as exc:
        # 30034 = daily application command creates exhausted.
        code = getattr(exc, "code", "?")
        _note_sync_error(f"{label} failed ({exc.status} {code}): {exc}")
        return None


STALE_CARE_COMMANDS = ("play", "feed", "pet", "scout")


def _sync_targets() -> set[int]:
    targets = set(ALLOWED_GUILD_IDS or ())
    if DEV_GUILD_ID:
        targets.add(int(DEV_GUILD_ID))
    for guild in bot.guilds:
        targets.add(guild.id)
    return targets


async def _delete_guild_commands_individually(app_id: int, guild_id: int) -> bool:
    try:
        fetched = await bot.http.get_guild_commands(app_id, guild_id)
    except Exception:
        log.exception("Could not list guild %s commands for per-command delete", guild_id)
        return False

    for cmd in fetched:
        cmd_id = cmd.get("id")
        if not cmd_id:
            continue
        try:
            await bot.http.delete_guild_command(app_id, guild_id, cmd_id)
        except discord.HTTPException as exc:
            _note_sync_error(
                f"Guild {guild_id} delete {cmd.get('name')!r} failed ({exc.status} "
                f"{getattr(exc, 'code', '?')})"
            )
            return False

    try:
        remaining = await bot.http.get_guild_commands(app_id, guild_id)
    except Exception:
        log.exception("Could not verify guild %s after per-command delete", guild_id)
        return False
    if remaining:
        names = [c.get("name") for c in remaining]
        _note_sync_error(f"Guild {guild_id} still has commands: {names[:15]}")
        return False
    return True


async def _wipe_guild_commands(guild_id: int) -> bool:
    """Delete every guild-scoped slash command so globals are visible.

    Uses the raw HTTP bulk-upsert (empty list) so we don't depend on the
    local CommandTree guild map. Does not reinstall — guild copies were
    what kept flat /familiar · /play alive.
    """
    app_id = bot.application_id
    if app_id is None:
        _note_sync_error(f"Cannot wipe guild {guild_id} — application_id unset.")
        return False

    # Drop any local guild map so we never accidentally re-PUT old cmds later.
    bot.tree.clear_commands(guild=discord.Object(id=guild_id))

    try:
        result = await bot.http.bulk_upsert_guild_commands(app_id, guild_id, [])
    except discord.HTTPException as exc:
        _note_sync_error(
            f"Guild {guild_id} bulk wipe HTTP {exc.status} "
            f"{getattr(exc, 'code', '?')}: {exc}"
        )
        return False
    except Exception:
        log.exception("Guild wipe crashed for %s", guild_id)
        _note_sync_error(f"Guild {guild_id} wipe crashed — see logs.")
        return False

    if result:
        log.warning(
            "Guild %s bulk wipe body listed %d commands (verifying via GET): %s",
            guild_id,
            len(result),
            [c.get("name") for c in result[:20]],
        )

    for attempt in range(WIPE_VERIFY_ATTEMPTS):
        if attempt:
            await asyncio.sleep(WIPE_VERIFY_DELAY_SEC)
        try:
            fetched = await bot.http.get_guild_commands(app_id, guild_id)
        except Exception:
            log.exception("Could not fetch guild %s commands after wipe", guild_id)
            continue
        if not fetched:
            log.info(
                "Guild %s slash commands CLEARED — globals will show through.",
                guild_id,
            )
            return True
        names = [c.get("name") for c in fetched]
        log.warning(
            "Guild %s still has %d command(s) after wipe (try %s/%s): %s",
            guild_id,
            len(fetched),
            attempt + 1,
            WIPE_VERIFY_ATTEMPTS,
            names[:20],
        )

    log.warning("Guild %s bulk wipe did not stick — deleting commands one-by-one.", guild_id)
    if await _delete_guild_commands_individually(app_id, guild_id):
        log.info("Guild %s cleared via per-command DELETE.", guild_id)
        return True
    return False


async def sync_commands(*, force: bool = False):
    """Register slash commands only when the tree (or sync mode) changed.

    Railway redeploys on every GitHub push. Re-PUTting the whole command tree
    on each boot burned Discord's daily create budget and froze the bot. Skip
    when nothing changed; FORCE_COMMAND_SYNC=1 (or force=True) overrides.

    Guild wipe runs first (and retries every boot until wipe_revision matches)
    so flat /familiar · /play stop shadowing nested globals — even if the
    staff slash /synccmds is invisible under the old guild tree.
    """
    global _synced, _last_sync_outcome

    async with _sync_lock:
        _clear_sync_errors()
        _last_sync_outcome = "full"

        # on_ready fires again after every reconnect; syncing once per start is
        # enough, and avoids hammering Discord's rate limits. Staff /staff sync
        # may call again with force=True in the same process.
        if _synced and not force:
            return True
        if not force:
            _synced = True

        targets = _sync_targets()
        mode = "global" if (SYNC_GLOBALLY or not targets) else "guild"
        fingerprint = _fingerprint(mode, targets)
        previous = _load_sync_state()
        wipe_needed = previous.get("wipe_revision") != COMMAND_SYNC_REVISION
        tree_unchanged = (
            previous.get("fingerprint") == fingerprint and previous.get("mode") == mode
        )
        skip_global_sync = (
            mode == "global" and tree_unchanged and not FORCE_COMMAND_SYNC
        )

        if (
            not force
            and not FORCE_COMMAND_SYNC
            and not wipe_needed
            and tree_unchanged
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
                "%s - syncing slash commands with Discord (wipe_needed=%s, skip_global=%s).",
                (
                    "Staff-forced sync" if force
                    else "FORCE_COMMAND_SYNC set" if FORCE_COMMAND_SYNC
                    else "Wipe revision bump"
                ),
                wipe_needed,
                skip_global_sync,
            )
        else:
            log.info(
                "Slash commands changed (%s -> %s, %s mode) - syncing with Discord.",
                (previous.get("fingerprint") or "none")[:12],
                fingerprint[:12],
                mode,
            )

        wipe_ok = True
        global_ok = True

        # 1) Wipe guild shadows FIRST — this is what unblocks nested /familiar.
        if mode == "global":
            if not targets:
                _note_sync_error(
                    "No guild ids to wipe — stale guild /familiar may remain. "
                    "Check ALLOWED_GUILD_IDS or that the bot is in the server."
                )
                wipe_ok = False
            for guild_id in sorted(targets):
                if not await _wipe_guild_commands(guild_id):
                    wipe_ok = False

        # 2) Push the current global (or guild-dev) tree.
        if mode == "global":
            if skip_global_sync:
                log.info(
                    "Slash tree fingerprint unchanged — skipping global PUT "
                    "(guild wipe only; avoids daily create cap / 30034)."
                )
                _last_sync_outcome = "wipe_only"
            else:
                synced = await _run_sync(bot.tree.sync(), "Global command sync")
                if synced is None:
                    global_ok = False
                else:
                    log.info("Synced %d global commands", len(synced))
                    names = sorted(c.name for c in synced)
                    if "familiar" not in names:
                        _note_sync_error("Global sync missing /familiar — tree may not have loaded.")
                        global_ok = False
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
                    global_ok = False
                    break
                names = sorted(c.name for c in synced)
                stale = sorted(n for n in STALE_CARE_COMMANDS if n in names)
                if stale or "familiar" not in names:
                    _note_sync_error(
                        f"Guild {guild_id} sync bad (stale={stale}, familiar={'familiar' in names})"
                    )
                    global_ok = False
                    break
                log.info("Synced %d commands to server %s", len(synced), guild_id)

            if global_ok:
                bot.tree.clear_commands(guild=None)
                cleared = await _run_sync(bot.tree.sync(), "Clear global commands")
                if cleared is None:
                    global_ok = False
                else:
                    log.info("Cleared global commands so nothing appears twice")

        ok = wipe_ok and (global_ok or skip_global_sync)
        if wipe_ok and not global_ok and not skip_global_sync and tree_unchanged:
            log.warning(
                "Global sync failed but slash tree unchanged — guild wipe alone should fix /familiar."
            )
            ok = True
            _last_sync_outcome = "wipe_only"

        if ok:
            _synced = True
            _save_sync_state(
                {
                    "fingerprint": fingerprint,
                    "mode": mode,
                    "targets": sorted(targets),
                    "guilds_wiped": mode == "global" and wipe_ok,
                    "wipe_revision": COMMAND_SYNC_REVISION if wipe_ok else previous.get("wipe_revision"),
                    "synced_at": time.time(),
                }
            )
            log.info(
                "Command sync OK — guilds wiped=%s wipe_revision=%s outcome=%s. "
                "Clients must fully quit Discord to refresh /familiar.",
                wipe_ok and mode == "global",
                COMMAND_SYNC_REVISION if wipe_ok else previous.get("wipe_revision"),
                _last_sync_outcome,
            )
        else:
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

    !swarmpractice [minutes] starts a no-points house practice swarm in this
    channel when the slash command is stuck.
    """
    if message.author.bot or not message.guild:
        return
    text = (message.content or "").strip()
    lower = text.lower()

    if lower.startswith("!swarmpractice"):
        store = bot.get_cog("Store")
        if not (store and store.can_run_house_practice(message.author)):
            await message.reply("That's for staff or Presidents.", mention_author=False)
            return
        cog = bot.get_cog("Dementors")
        if cog is None:
            await message.reply("Wild Threats isn't loaded.", mention_author=False)
            return
        minutes = 5
        parts = text.split()
        if len(parts) >= 2:
            try:
                minutes = int(parts[1])
            except ValueError:
                await message.reply(
                    "Usage: `!swarmpractice` or `!swarmpractice 10`",
                    mention_author=False,
                )
                return
        status = await message.reply("Starting house practice swarm…", mention_author=False)
        try:
            ok, msg, start = await cog._begin_practice(
                channel_id=message.channel.id,
                guild_id=message.guild.id,
                minutes=minutes,
            )
            await status.edit(content=msg)
            if ok and start:
                await cog._announce_practice_wave(start)
        except Exception as exc:
            log.exception("!swarmpractice failed")
            await status.edit(
                content=f"Practice swarm failed (`{type(exc).__name__}`). Check Railway logs."
            )
        return

    if lower not in ("!synccmds", "!wipecommands", "!synccommands"):
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
        if sync_outcome_is_wipe_only():
            lead = "Done. **Guild slash lists wiped** (global tree unchanged — no full Discord re-upload)."
        else:
            lead = "Done. Guild slash lists wiped; globals refreshed."
        await status.edit(
            content=(
                f"{lead}\n"
                "**Fully quit Discord** (swipe away on mobile) and reopen `/`.\n"
                "• `/familiar` should show status / adopt / feed / pet / play / scout\n"
                "• Top-level `/play` · `/feed` · `/scout` should be gone"
            )
        )
    else:
        await status.edit(
            content=(
                "Sync didn't finish cleanly (rate limit, timeout, or guild wipe failed). "
                "Wait a few minutes and type `!synccmds` again, or check Railway logs."
                f"{sync_failure_hint()}"
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
        _warn_oversized_command_groups()
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
