"""
The Tri-Wizard Tournament roll of champions.

The tournament itself is run by hand, outside the bot. The bot only keeps
the record, so a champion is remembered on their profile for good.

    /triwizard crown @winner [name]   - staff, record a champion
    /triwizard history                - anyone, every champion so far
    /triwizard remove <number>        - staff, undo a mistaken entry

Crowning does not award house points. If the winner should earn points,
staff give them with /award, so points stay entirely a human decision.
"""

import datetime
import json
import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.triwizard")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
TRIWIZARD_PATH = STATE_DIR / "triwizard.json"

EMBLEM = "\U0001F3C5"  # sports medal - the House Cup already uses the trophy


class TriWizard(commands.Cog):
    group = app_commands.Group(name="triwizard", description="The Tri-Wizard Tournament champions.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.records = self._load()

    # ------------------------------------------------------------- storage

    def _load(self) -> list:
        try:
            with open(TRIWIZARD_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("champions", [])
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError):
            log.exception("Tri-Wizard records unreadable - starting empty.")
            return []

    def save(self) -> None:
        try:
            TRIWIZARD_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = TRIWIZARD_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"champions": self.records}, f, indent=2)
            os.replace(tmp, TRIWIZARD_PATH)
        except OSError:
            log.exception("Could not save Tri-Wizard records.")

    # --------------------------------------------------------------- record

    def next_number(self) -> int:
        return max((r["number"] for r in self.records), default=0) + 1

    def crown(self, member, house: str | None, name: str = "", now: float = None) -> dict:
        number = self.next_number()
        record = {
            "number": number,
            "name": name.strip() or f"Tri-Wizard Tournament {_roman(number)}",
            "id": member.id,
            "display_name": member.display_name,
            "house": house,
            "at": now if now is not None else time.time(),
        }
        self.records.append(record)
        self.save()
        return record

    def remove(self, number: int) -> dict | None:
        for i, r in enumerate(self.records):
            if r["number"] == number:
                gone = self.records.pop(i)
                self.save()
                return gone
        return None

    def titles_of(self, user_id: int) -> list[dict]:
        return [r for r in self.records if r["id"] == user_id]

    def _is_staff(self, member) -> bool:
        store = self.bot.get_cog("Store")
        if store is not None:
            return store.is_staff(member)
        perms = getattr(member, "guild_permissions", None)
        return bool(perms and getattr(perms, "manage_guild", False))

    # ------------------------------------------------------------ commands

    @group.command(name="crown", description="Record the winner of a Tri-Wizard Tournament.")
    @app_commands.describe(winner="Who won",
                           name="What to call this tournament (optional - it numbers itself)")
    async def crown_cmd(self, interaction: discord.Interaction, winner: discord.Member,
                        name: str = ""):
        if not self._is_staff(interaction.user):
            await interaction.response.send_message("Only staff can crown a champion.",
                                                    ephemeral=True)
            return
        if winner.bot:
            await interaction.response.send_message(
                "The ghosts have had quite enough of that tournament.", ephemeral=True
            )
            return

        from cogs.store import HOUSES
        store = self.bot.get_cog("Store")
        house = store.member_house(winner) if store else None
        record = self.crown(winner, house, name)

        meta = HOUSES.get(house)
        wins = len(self.titles_of(winner.id))
        embed = discord.Embed(
            title=f"{EMBLEM} Champion of the {record['name']}",
            description=(f"**{winner.display_name}**"
                         + (f" of {meta['emoji']} House {meta['name']}" if meta else "")
                         + "\n\n*The maze has a new name to remember.*"),
            color=meta["color"] if meta else 0xD9A441,
        )
        if wins > 1:
            embed.set_footer(text=f"Their {_ordinal(wins)} Tri-Wizard title.")
        await interaction.response.send_message(content=winner.mention, embed=embed)

    @group.command(name="history", description="Every Tri-Wizard champion so far.")
    async def history_cmd(self, interaction: discord.Interaction):
        if not self.records:
            await interaction.response.send_message(
                "No Tri-Wizard champion has been crowned yet.", ephemeral=True
            )
            return

        from cogs.store import HOUSES
        lines = []
        for r in sorted(self.records, key=lambda r: r["number"], reverse=True)[:20]:
            when = datetime.datetime.fromtimestamp(r["at"], datetime.timezone.utc)
            emblem = HOUSES[r["house"]]["emoji"] + " " if r.get("house") in HOUSES else ""
            lines.append(f"`#{r['number']}` **{r['name']}** — {emblem}<@{r['id']}> "
                         f"({when:%b %Y})")

        embed = discord.Embed(title=f"{EMBLEM} Tri-Wizard Champions",
                              description="\n".join(lines), color=0xD9A441)
        await interaction.response.send_message(embed=embed)

    @group.command(name="remove", description="Remove a Tri-Wizard entry made by mistake.")
    @app_commands.describe(number="The tournament number shown in /triwizard history")
    async def remove_cmd(self, interaction: discord.Interaction, number: int):
        if not self._is_staff(interaction.user):
            await interaction.response.send_message("Only staff can change the record.",
                                                    ephemeral=True)
            return
        gone = self.remove(number)
        if gone is None:
            await interaction.response.send_message(f"There's no tournament #{number}.",
                                                    ephemeral=True)
            return
        await interaction.response.send_message(
            f"Removed #{number}, **{gone['name']}** (<@{gone['id']}>).", ephemeral=True
        )


def _roman(n: int) -> str:
    """1 -> I, 4 -> IV. Tournaments read better numbered like this."""
    out = ""
    for value, numeral in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
                           (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
                           (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += numeral
            n -= value
    return out


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


async def setup(bot: commands.Bot):
    await bot.add_cog(TriWizard(bot))
