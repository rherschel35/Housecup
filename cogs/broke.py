"""
/gonsomethingbroke - tell Gon something's broken.

Players describe what's wrong. A ticket receipt is posted in the channel
where they ran the command, and a copy goes to the staff broke-report
channel with a jump link. Staff can press **Close ticket** on either
message; the reporter is notified that it's resolved and fixed.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path

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
