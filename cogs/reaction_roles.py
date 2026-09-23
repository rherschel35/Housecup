"""
Reaction roles. Post one message; reacting to it hands out a role.

    /reactionroles addstatus <emoji> <role> <label>   - e.g. Champion / Alumni
    /reactionroles addhouse <emoji> <house>           - uses the role already
                                                         bound with /sethouserole
    /reactionroles remove <emoji>                     - drop a mapping
    /reactionroles post <channel>                     - publish the sign-up
                                                         message and react to it
    /reactionroles config                             - what's configured
    /reactionroles setmember <member> <category> <role>
                                                       - staff override: swap
                                                         someone's role by hand

Two independent categories share one message: "status" (Champion / Alumni,
or whatever a staff member sets up) and "house" (the five houses, reusing
whichever role /sethouserole already bound). Reacting hands out a role only
the first time - once someone holds a role in a category, reacting to
another emoji in that same category is undone (the reaction is removed) so
it's clear it didn't take. Un-reacting never takes a role away; the only way
to change someone's pick afterwards is a staff member using /reactionroles
setmember (or just editing their roles directly in Discord).
"""

import json
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.store import HOUSES

log = logging.getLogger("velmora.reactionroles")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "reaction_roles.json"

HOUSE_CHOICES = [
    app_commands.Choice(name=f"House {meta['name']}", value=key)
    for key, meta in HOUSES.items()
]
CATEGORY_CHOICES = [
    app_commands.Choice(name="Status (Champion/Alumni-style)", value="status"),
    app_commands.Choice(name="House", value="house"),
]


def _blank_state() -> dict:
    return {"message": None, "status": {}, "houses": {}}


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()

    # ---------------------------------------------------------------- disk

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            base = _blank_state()
            for key, value in base.items():
                state.setdefault(key, value)
            return state
        except FileNotFoundError:
            return _blank_state()
        except (OSError, json.JSONDecodeError):
            log.exception("Reaction-role state unreadable - starting empty.")
            return _blank_state()

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Couldn't save reaction-role state.")

    def _store(self):
        return self.bot.get_cog("Store")

    async def _guard(self, interaction: discord.Interaction):
        store = self._store()
        if store is None:
            await interaction.response.send_message("The ledger isn't loaded.", ephemeral=True)
            return None
        if not store.is_staff(interaction.user):
            await interaction.response.send_message("That one's for staff only.", ephemeral=True)
            return None
        return store

    # ------------------------------------------------------------- lookups

    def _lookup(self, emoji_key: str):
        """emoji -> (category, entry) or (None, None)."""
        if emoji_key in self.state["status"]:
            return "status", self.state["status"][emoji_key]
        if emoji_key in self.state["houses"]:
            return "house", self.state["houses"][emoji_key]
        return None, None

    def _category_role_ids(self, category: str) -> set[int]:
        bucket = self.state["status"] if category == "status" else self.state["houses"]
        return {entry["role_id"] for entry in bucket.values()}

    # -------------------------------------------------------------- group

    reactionroles = app_commands.Group(name="reactionroles", description="Set up reaction-role sign-ups.")

    @reactionroles.command(name="addstatus", description="Add a Champion/Alumni-style reaction role.")
    @app_commands.describe(emoji="The emoji people react with", role="The role it grants",
                           label="What to call it in the sign-up post, e.g. 'Champion'")
    async def addstatus(self, interaction: discord.Interaction, emoji: str,
                        role: discord.Role, label: str):
        store = await self._guard(interaction)
        if store is None:
            return
        self.state["status"][emoji] = {"role_id": role.id, "label": label.strip() or role.name}
        self.save()
        await interaction.response.send_message(
            f"{emoji} now grants {role.mention} (\"{label}\"). Run `/reactionroles post` when ready.",
            ephemeral=True,
        )

    @reactionroles.command(name="addhouse", description="Add a house to the sign-up (reuses its /sethouserole binding).")
    @app_commands.describe(emoji="The emoji people react with", house="Which house")
    @app_commands.choices(house=HOUSE_CHOICES)
    async def addhouse(self, interaction: discord.Interaction, emoji: str,
                       house: app_commands.Choice[str]):
        store = await self._guard(interaction)
        if store is None:
            return
        role_id = store.settings.get("house_roles", {}).get(house.value)
        if not role_id:
            await interaction.response.send_message(
                f"House {HOUSES[house.value]['name']} isn't bound to a role yet - "
                "run `/sethouserole` first.", ephemeral=True)
            return
        self.state["houses"][emoji] = {"role_id": role_id, "house": house.value}
        self.save()
        await interaction.response.send_message(
            f"{emoji} now sorts into House {HOUSES[house.value]['name']}. "
            "Run `/reactionroles post` when ready.", ephemeral=True,
        )

    @reactionroles.command(name="remove", description="Remove an emoji from the sign-up.")
    @app_commands.describe(emoji="The emoji to drop")
    async def remove(self, interaction: discord.Interaction, emoji: str):
        store = await self._guard(interaction)
        if store is None:
            return
        removed = self.state["status"].pop(emoji, None) or self.state["houses"].pop(emoji, None)
        if not removed:
            await interaction.response.send_message(f"{emoji} isn't configured.", ephemeral=True)
            return
        self.save()
        await interaction.response.send_message(f"{emoji} removed from the sign-up.", ephemeral=True)

    @reactionroles.command(name="config", description="What the reaction-role sign-up currently looks like.")
    async def config(self, interaction: discord.Interaction):
        store = await self._guard(interaction)
        if store is None:
            return
        lines = [f"{emoji} — {e['label']}" for emoji, e in self.state["status"].items()]
        lines += [f"{emoji} — House {HOUSES[e['house']]['name']}"
                 for emoji, e in self.state["houses"].items() if e["house"] in HOUSES]
        msg = self.state.get("message")
        posted = (f"<#{msg['channel_id']}> (message {msg['message_id']})" if msg else "not posted yet")
        embed = discord.Embed(
            title="Reaction roles",
            description="\n".join(lines) if lines else "Nothing configured yet.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Posted", value=posted, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @reactionroles.command(name="post", description="Publish the sign-up message and react to it.")
    @app_commands.describe(channel="Where to post it")
    async def post(self, interaction: discord.Interaction, channel: discord.TextChannel):
        store = await self._guard(interaction)
        if store is None:
            return
        if not self.state["status"] and not self.state["houses"]:
            await interaction.response.send_message(
                "Nothing configured yet - add some with `/reactionroles addstatus` / `addhouse` first.",
                ephemeral=True)
            return

        lines = ["React below to pick yours. One pick per row - reacting to a second option in the "
                "same row won't do anything (ask a staff member if you need it changed)."]
        if self.state["status"]:
            lines.append("")
            lines.append("**Status**")
            lines += [f"{emoji} — {e['label']}" for emoji, e in self.state["status"].items()]
        if self.state["houses"]:
            lines.append("")
            lines.append("**House**")
            lines += [f"{emoji} — House {HOUSES[e['house']]['name']}"
                     for emoji, e in self.state["houses"].items() if e["house"] in HOUSES]

        embed = discord.Embed(title="Sort yourself into Velmora", description="\n".join(lines),
                              color=discord.Color.gold())
        message = await channel.send(embed=embed)
        for emoji in list(self.state["status"].keys()) + list(self.state["houses"].keys()):
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                log.exception("Couldn't react with %s - is it a valid emoji?", emoji)
        self.state["message"] = {"channel_id": channel.id, "message_id": message.id}
        self.save()
        await interaction.response.send_message(f"Posted in {channel.mention}.", ephemeral=True)

    @reactionroles.command(name="setmember", description="Staff override: set someone's role by hand.")
    @app_commands.describe(member="Who to change", category="Status or house",
                           role="The role to give them (their old one in that category is removed)")
    @app_commands.choices(category=CATEGORY_CHOICES)
    async def setmember(self, interaction: discord.Interaction, member: discord.Member,
                        category: app_commands.Choice[str], role: discord.Role):
        store = await self._guard(interaction)
        if store is None:
            return
        other_ids = self._category_role_ids(category.value) - {role.id}
        to_remove = [r for r in member.roles if r.id in other_ids]
        if to_remove:
            await member.remove_roles(*to_remove, reason="Reaction-role staff override")
        if role not in member.roles:
            await member.add_roles(role, reason="Reaction-role staff override")
        await interaction.response.send_message(
            f"{member.display_name} is now {role.mention}.", ephemeral=True)

    # ------------------------------------------------------------ listener

    async def _strip(self, payload: "discord.RawReactionActionEvent"):
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(payload.channel_id) or self.bot.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
            member = guild.get_member(payload.user_id)
            if member is not None:
                await message.remove_reaction(payload.emoji, member)
        except discord.HTTPException:
            log.exception("Couldn't strip a reaction for %s", payload.user_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: "discord.RawReactionActionEvent"):
        if payload.guild_id is None or self.bot.user and payload.user_id == self.bot.user.id:
            return
        msg = self.state.get("message")
        if not msg or payload.message_id != msg["message_id"]:
            return

        category, entry = self._lookup(str(payload.emoji))
        if entry is None:
            await self._strip(payload)
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = payload.member or guild.get_member(payload.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return

        role = guild.get_role(entry["role_id"])
        if role is None:
            log.warning("Reaction role %s points at a role that no longer exists.", payload.emoji)
            return
        if role in member.roles:
            return

        other_ids = self._category_role_ids(category) - {entry["role_id"]}
        if any(r.id in other_ids for r in member.roles):
            await self._strip(payload)
            try:
                await member.send(
                    "You've already got a role from that row, so that reaction didn't do "
                    "anything - ask a staff member if it needs to change.")
            except discord.HTTPException:
                pass
            return

        await member.add_roles(role, reason="Reaction role sign-up")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: "discord.RawReactionActionEvent"):
        # Un-reacting never takes a role away - picks are permanent until a
        # staff member changes them with /reactionroles setmember.
        return


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
