"""
/gonsomethingbroke - tell Gon something's broken.

Players describe what's wrong. A ticket receipt is posted in the channel
where they ran the command, and a copy goes to the staff broke-report
channel with a jump link. Staff can press **Close ticket** on either
message; the reporter is notified that it's resolved and fixed.

When TICKET_WEBHOOK_URL is set, the filed ticket is also POSTed as JSON
to that URL. The POST does not change what the player sees. Failures are
logged and do not block the ticket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.broke")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "broke_state.json"

# Staff inbox for "something broke" tickets.
REPORT_CHANNEL_ID = int(
    os.getenv("BROKE_REPORT_CHANNEL_ID", "1555383179802579005") or 0
)

COOLDOWN_SECONDS = 120
MAX_REPORT_LEN = 1000
COLOR = 0xC0392B
RESOLVED_COLOR = 0x2ECC71
CLOSE_CUSTOM_ID = "broke:close"

# External copy of a filed ticket. Unset URL means the feature is off.
WEBHOOK_TIMEOUT_SECONDS = 10
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.0-9A-Z^_`a-z|~]+$")


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("tickets", {})
                data.setdefault("by_msg", {})
                return data
        except (OSError, json.JSONDecodeError):
            log.exception("Broke state unreadable — starting fresh.")
    return {"tickets": {}, "by_msg": {}}


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _commit_sha() -> str | None:
    """Railway sets this on git deploys. Absent when the process has no sha."""
    sha = os.getenv("RAILWAY_GIT_COMMIT_SHA", "").strip()
    return sha or None


def _redact(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    return text.replace(secret, "[redacted]")


def _ticket_webhook_auth_headers(key: str, header_name: str) -> dict[str, str]:
    """Headers that carry the webhook secret.

    With no header name, the key is sent as ``Authorization: Bearer <key>``.
    When a header name is set, the raw key is sent under that name instead.
    An empty key sends no auth header. A header name that is not an HTTP
    token raises ValueError and does not include the key.
    """
    key = (key or "").strip()
    header_name = (header_name or "").strip()
    if not key:
        return {}
    if not header_name:
        return {"Authorization": f"Bearer {key}"}
    if _HEADER_NAME_RE.fullmatch(header_name) is None:
        raise ValueError("invalid webhook key header name")
    return {header_name: key}


def _build_broke_ticket_payload(
    *,
    text: str,
    reporter_id: int,
    reporter_name: str,
    channel_id: int,
    channel_name: str,
    guild_id: int | None,
    ticket_id: str,
    timestamp: str | None = None,
    commit_sha: str | None = None,
) -> dict[str, object]:
    """JSON body for one filed ticket. No category: this command has none."""
    payload: dict[str, object] = {
        "text": text,
        "reporter_id": str(reporter_id),
        "reporter_display_name": reporter_name,
        "channel_id": str(channel_id),
        "channel_name": channel_name,
        "guild_id": str(guild_id) if guild_id else None,
        "timestamp": timestamp or _utc_now_iso(),
        "ticket_id": ticket_id,
    }
    sha = (commit_sha or "").strip()
    if sha:
        payload["commit_sha"] = sha
    return payload


def _clip(text: str, limit: int = 300) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


async def _post_ticket_webhook(url: str, payload: dict[str, object]) -> None:
    """POST the ticket. Logs failures. Never raises to the caller."""
    key = os.getenv("TICKET_WEBHOOK_KEY", "").strip()
    ticket_id = payload.get("ticket_id") or ""
    try:
        header_name = os.getenv("TICKET_WEBHOOK_KEY_HEADER", "").strip()
        try:
            auth = _ticket_webhook_auth_headers(key, header_name)
        except ValueError:
            log.error(
                "TICKET_WEBHOOK_KEY_HEADER is not a valid HTTP header name; "
                "forwarding broke ticket %s without the key.",
                ticket_id,
            )
            auth = {}
        timeout = aiohttp.ClientTimeout(total=WEBHOOK_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json=payload,
                headers=auth,
                allow_redirects=False,
            ) as resp:
                raw = await resp.text()
                if resp.status < 200 or resp.status >= 300:
                    detail = _clip(_redact(raw, key))
                    log.warning(
                        "Broke ticket webhook returned HTTP %s for ticket %s%s",
                        resp.status,
                        ticket_id,
                        f" ({detail})" if detail else "",
                    )
                    return
        log.info("Forwarded broke ticket %s", ticket_id)
    except Exception as exc:
        log.warning(
            "Broke ticket webhook failed for ticket %s: %s",
            ticket_id,
            _redact(f"{type(exc).__name__}: {exc}", key),
        )


class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Close ticket",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=CLOSE_CUSTOM_ID,
        )

    async def callback(self, interaction: discord.Interaction):
        cog: Broke | None = interaction.client.get_cog("Broke")  # type: ignore[assignment]
        if cog is None:
            await interaction.response.send_message(
                "Broke tickets aren't loaded.", ephemeral=True
            )
            return
        await cog.close_ticket(interaction)


class BrokeTicketView(discord.ui.View):
    """Public ticket receipt — Close for staff."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CloseTicketButton())


class BrokeReportView(discord.ui.View):
    """Staff inbox copy — jump link + Close."""

    def __init__(self, jump_url: str | None = None):
        super().__init__(timeout=None)
        if jump_url:
            self.add_item(
                discord.ui.Button(
                    label="Go to ticket",
                    style=discord.ButtonStyle.link,
                    url=jump_url,
                )
            )
        self.add_item(CloseTicketButton())


class Broke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last: dict[int, float] = {}
        self._webhook_tasks: set[asyncio.Task[None]] = set()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = _load_state()
        # Persistent Close button (same custom_id on ticket + staff report).
        self.bot.add_view(BrokeTicketView())

    def save(self) -> None:
        try:
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save broke state.")

    def _cooldown_left(self, user_id: int, now: float) -> int:
        last = self._last.get(user_id, 0)
        if not last or now >= last + COOLDOWN_SECONDS:
            return 0
        return max(1, int(last + COOLDOWN_SECONDS - now))

    def _is_staff(self, member: discord.abc.User) -> bool:
        store = self.bot.get_cog("Store")
        if store and hasattr(member, "guild_permissions"):
            return bool(store.is_staff(member))
        perms = getattr(member, "guild_permissions", None)
        return bool(
            perms
            and (
                getattr(perms, "manage_guild", False)
                or getattr(perms, "administrator", False)
            )
        )

    def _remember(
        self,
        *,
        ticket_id: str,
        reporter_id: int,
        what: str,
        ticket_channel_id: int,
        ticket_msg_id: int,
        report_channel_id: int | None,
        report_msg_id: int | None,
    ) -> None:
        rec = {
            "reporter_id": reporter_id,
            "what": what,
            "ticket_channel_id": ticket_channel_id,
            "ticket_msg_id": ticket_msg_id,
            "report_channel_id": report_channel_id,
            "report_msg_id": report_msg_id,
            "opened_at": time.time(),
            "closed": False,
        }
        self.state.setdefault("tickets", {})[ticket_id] = rec
        by_msg = self.state.setdefault("by_msg", {})
        by_msg[str(ticket_msg_id)] = ticket_id
        if report_msg_id:
            by_msg[str(report_msg_id)] = ticket_id
        self.save()

    def _forward_ticket(
        self,
        interaction: discord.Interaction,
        what: str,
        ticket_id: str,
    ) -> None:
        """Schedule the webhook. Returns immediately. No-op when the URL is unset."""
        url = os.getenv("TICKET_WEBHOOK_URL", "").strip()
        if not url:
            return
        channel = interaction.channel
        reporter = interaction.user
        payload = _build_broke_ticket_payload(
            text=what,
            reporter_id=reporter.id,
            reporter_name=getattr(reporter, "display_name", "") or str(reporter),
            channel_id=getattr(channel, "id", 0) or 0,
            channel_name=getattr(channel, "name", "") or "",
            guild_id=interaction.guild_id,
            ticket_id=ticket_id,
            commit_sha=_commit_sha(),
        )
        task = asyncio.create_task(
            _post_ticket_webhook(url, payload),
            name=f"broke-webhook-{ticket_id}",
        )
        self._webhook_tasks.add(task)
        task.add_done_callback(self._webhook_tasks.discard)

    def _ticket_for_message(self, message_id: int) -> tuple[str, dict] | None:
        tid = self.state.get("by_msg", {}).get(str(message_id))
        if not tid:
            return None
        rec = self.state.get("tickets", {}).get(tid)
        if not isinstance(rec, dict):
            return None
        return tid, rec

    @staticmethod
    def _receipt_embed(
        *,
        what: str,
        author: discord.abc.User,
        resolved: bool = False,
        closer: discord.abc.User | None = None,
    ) -> discord.Embed:
        if resolved:
            embed = discord.Embed(
                title="✅ Ticket resolved",
                description=what,
                color=RESOLVED_COLOR,
                timestamp=discord.utils.utcnow(),
            )
            who = closer.display_name if closer else "staff"
            embed.set_footer(text=f"Marked resolved & fixed by {who}.")
        else:
            embed = discord.Embed(
                title="🛠️ Something broke",
                description=what,
                color=COLOR,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(
                text="Gon has been told. Staff can jump here from the report channel."
            )
        avatar = getattr(author, "display_avatar", None)
        kwargs = {"name": getattr(author, "display_name", str(author))}
        if avatar is not None:
            kwargs["icon_url"] = avatar.url
        embed.set_author(**kwargs)
        return embed

    @staticmethod
    def _staff_embed(
        *,
        what: str,
        reporter: discord.abc.User,
        channel_mention: str,
        jump_url: str,
        resolved: bool = False,
        closer: discord.abc.User | None = None,
    ) -> discord.Embed:
        if resolved:
            embed = discord.Embed(
                title="✅ Broke report — resolved",
                description=what,
                color=RESOLVED_COLOR,
                timestamp=discord.utils.utcnow(),
            )
            who = closer.display_name if closer else "staff"
            embed.set_footer(text=f"Closed by {who}. Reporter was notified.")
        else:
            embed = discord.Embed(
                title="🛠️ Broke report",
                description=what,
                color=COLOR,
                timestamp=discord.utils.utcnow(),
            )
        avatar = getattr(reporter, "display_avatar", None)
        kwargs = {"name": f"{reporter} ({reporter.id})"}
        if avatar is not None:
            kwargs["icon_url"] = avatar.url
        embed.set_author(**kwargs)
        embed.add_field(
            name="Submitted in",
            value=f"{channel_mention}\n[Jump to ticket]({jump_url})",
            inline=False,
        )
        return embed

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        if not self._is_staff(interaction.user):
            await interaction.response.send_message(
                "That's for staff.", ephemeral=True
            )
            return
        if interaction.message is None:
            await interaction.response.send_message(
                "Couldn't find this ticket message.", ephemeral=True
            )
            return

        found = self._ticket_for_message(interaction.message.id)
        if not found:
            await interaction.response.send_message(
                "This ticket isn't in the open list (maybe from before Close existed, "
                "or already cleared). You can still reply to the reporter manually.",
                ephemeral=True,
            )
            return
        ticket_id, rec = found
        if rec.get("closed"):
            await interaction.response.send_message(
                "This ticket is already closed.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        rec["closed"] = True
        rec["closed_at"] = time.time()
        rec["closed_by"] = interaction.user.id
        self.save()

        what = str(rec.get("what") or "")
        reporter_id = int(rec.get("reporter_id") or 0)
        ticket_channel_id = int(rec.get("ticket_channel_id") or 0)
        ticket_msg_id = int(rec.get("ticket_msg_id") or 0)
        report_channel_id = int(rec.get("report_channel_id") or 0) or None
        report_msg_id = int(rec.get("report_msg_id") or 0) or None

        reporter = None
        if interaction.guild and reporter_id:
            reporter = interaction.guild.get_member(reporter_id)
            if reporter is None:
                try:
                    reporter = await self.bot.fetch_user(reporter_id)
                except discord.HTTPException:
                    reporter = None
        if reporter is None:
            reporter = interaction.user  # fallback for embed author only

        jump_url = ""
        if ticket_channel_id and ticket_msg_id:
            jump_url = (
                f"https://discord.com/channels/{interaction.guild_id}/"
                f"{ticket_channel_id}/{ticket_msg_id}"
            )

        # Update the public ticket receipt.
        if ticket_channel_id and ticket_msg_id:
            try:
                tch = self.bot.get_channel(ticket_channel_id)
                if tch is None:
                    tch = await self.bot.fetch_channel(ticket_channel_id)
                tmsg = await tch.fetch_message(ticket_msg_id)  # type: ignore[union-attr]
                await tmsg.edit(
                    embed=self._receipt_embed(
                        what=what,
                        author=reporter,
                        resolved=True,
                        closer=interaction.user,
                    ),
                    view=None,
                )
            except discord.HTTPException:
                log.exception("Couldn't edit broke ticket receipt %s", ticket_msg_id)

        # Update the staff inbox copy.
        if report_channel_id and report_msg_id:
            try:
                rch = self.bot.get_channel(report_channel_id)
                if rch is None:
                    rch = await self.bot.fetch_channel(report_channel_id)
                rmsg = await rch.fetch_message(report_msg_id)  # type: ignore[union-attr]
                channel_mention = f"<#{ticket_channel_id}>" if ticket_channel_id else "—"
                await rmsg.edit(
                    embed=self._staff_embed(
                        what=what,
                        reporter=reporter,
                        channel_mention=channel_mention,
                        jump_url=jump_url or rmsg.jump_url,
                        resolved=True,
                        closer=interaction.user,
                    ),
                    view=None,
                )
            except discord.HTTPException:
                log.exception("Couldn't edit broke staff report %s", report_msg_id)

        # Notify the reporter in the ticket channel.
        notified = False
        if ticket_channel_id:
            try:
                tch = self.bot.get_channel(ticket_channel_id)
                if tch is None:
                    tch = await self.bot.fetch_channel(ticket_channel_id)
                mention = f"<@{reporter_id}>" if reporter_id else None
                note = discord.Embed(
                    title="✅ Ticket resolved & fixed",
                    description=(
                        f"Your broke report has been **resolved and fixed** "
                        f"by **{interaction.user.display_name}**.\n\n"
                        f"> {what[:400]}"
                        + ("…" if len(what) > 400 else "")
                    ),
                    color=RESOLVED_COLOR,
                    timestamp=discord.utils.utcnow(),
                )
                note.set_footer(text="Thanks for flagging it.")
                await tch.send(  # type: ignore[union-attr]
                    content=mention,
                    embed=note,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
                notified = True
            except discord.HTTPException:
                log.exception("Couldn't notify reporter in channel %s", ticket_channel_id)

        # Best-effort DM if channel notify failed.
        if not notified and reporter_id:
            try:
                user = reporter if isinstance(reporter, discord.User) else await self.bot.fetch_user(reporter_id)
                await user.send(
                    f"Your broke ticket was **resolved and fixed** by "
                    f"**{interaction.user.display_name}**.\n> {what[:400]}"
                )
                notified = True
            except discord.HTTPException:
                log.info("Couldn't DM reporter %s about closed ticket", reporter_id)

        await interaction.followup.send(
            "Ticket closed."
            + (" Reporter notified." if notified else " Couldn't reach the reporter — ping them if needed."),
            ephemeral=True,
        )

    @app_commands.command(
        name="gonsomethingbroke",
        description="Tell Gon something's broken — staff get a jump link to your ticket.",
    )
    @app_commands.describe(what="What's broken? Be as specific as you can.")
    async def gonsomethingbroke(self, interaction: discord.Interaction, what: str):
        what = (what or "").strip()
        if not what:
            await interaction.response.send_message(
                "Tell me what's broken — add a short description.", ephemeral=True
            )
            return
        if len(what) > MAX_REPORT_LEN:
            await interaction.response.send_message(
                f"Keep it under {MAX_REPORT_LEN} characters.", ephemeral=True
            )
            return

        now = time.time()
        left = self._cooldown_left(interaction.user.id, now)
        if left:
            await interaction.response.send_message(
                f"You just sent one — try again in {left}s.", ephemeral=True
            )
            return

        if interaction.channel is None or not isinstance(
            interaction.channel, (discord.TextChannel, discord.Thread)
        ):
            await interaction.response.send_message(
                "Run this in a text channel so staff can jump back to your ticket.",
                ephemeral=True,
            )
            return

        self._last[interaction.user.id] = now
        ticket_id = uuid.uuid4().hex[:10]

        receipt = self._receipt_embed(what=what, author=interaction.user)
        await interaction.response.send_message(
            embed=receipt, view=BrokeTicketView()
        )
        # The receipt is already posted. Scheduling must not delay the staff
        # copy or add a message the player can see.
        try:
            self._forward_ticket(interaction, what, ticket_id)
        except Exception as exc:
            log.warning(
                "Could not schedule broke ticket webhook: %s",
                type(exc).__name__,
            )
        try:
            ticket_msg = await interaction.original_response()
        except discord.HTTPException:
            log.exception("Couldn't fetch broke-ticket receipt message")
            return

        report_channel = self.bot.get_channel(REPORT_CHANNEL_ID)
        if report_channel is None:
            try:
                report_channel = await self.bot.fetch_channel(REPORT_CHANNEL_ID)
            except discord.HTTPException:
                log.exception("Broke report channel %s not found", REPORT_CHANNEL_ID)
                self._remember(
                    ticket_id=ticket_id,
                    reporter_id=interaction.user.id,
                    what=what,
                    ticket_channel_id=interaction.channel.id,
                    ticket_msg_id=ticket_msg.id,
                    report_channel_id=None,
                    report_msg_id=None,
                )
                await interaction.followup.send(
                    "Your ticket is posted here, but the staff inbox couldn't be reached. "
                    "Ping Gon if it's urgent.",
                    ephemeral=True,
                )
                return

        staff_embed = self._staff_embed(
            what=what,
            reporter=interaction.user,
            channel_mention=interaction.channel.mention,
            jump_url=ticket_msg.jump_url,
        )
        view = BrokeReportView(jump_url=ticket_msg.jump_url)
        try:
            report_msg = await report_channel.send(embed=staff_embed, view=view)
        except discord.HTTPException:
            log.exception("Couldn't post broke report to %s", REPORT_CHANNEL_ID)
            self._remember(
                ticket_id=ticket_id,
                reporter_id=interaction.user.id,
                what=what,
                ticket_channel_id=interaction.channel.id,
                ticket_msg_id=ticket_msg.id,
                report_channel_id=None,
                report_msg_id=None,
            )
            await interaction.followup.send(
                "Your ticket is posted here, but the staff inbox couldn't be reached. "
                "Ping Gon if it's urgent.",
                ephemeral=True,
            )
            return

        self._remember(
            ticket_id=ticket_id,
            reporter_id=interaction.user.id,
            what=what,
            ticket_channel_id=interaction.channel.id,
            ticket_msg_id=ticket_msg.id,
            report_channel_id=report_channel.id,
            report_msg_id=report_msg.id,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Broke(bot))
