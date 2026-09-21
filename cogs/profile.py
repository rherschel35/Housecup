"""
/profile [member] - one player's whole story at Velmora.

    House and honours     - House Cup Champion titles, cups won
    Points                - this season and all time, with rank
    Duelling              - ladder rank, title, record
    Wand                  - what chose them, and why

House Cup honours come in two kinds:

    Champion   - the highest point earner in the house that won the season
                 (co-champions if they're level at the top)
    Cup won    - earned points for the winning house that season; everyone
                 who helped lift the cup gets to count it
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.profile")

# (minimum wins, title) - highest threshold first.
DUEL_TITLES = [
    (60, "Grand Duelist"),
    (30, "Master Duelist"),
    (15, "Duelist"),
    (5, "Adept"),
    (1, "Novice"),
    (0, "Untested"),
]


def duel_title(wins: int) -> str:
    for threshold, title in DUEL_TITLES:
        if wins >= threshold:
            return title
    return "Untested"


def duel_ladder(records: dict) -> list[int]:
    """Everyone who has duelled, best first: most wins, then best win rate,
    then fewest losses."""
    rows = []
    for uid, rec in records.items():
        w, l = rec.get("w", 0), rec.get("l", 0)
        if w + l == 0:
            continue
        rows.append((int(uid), w, w / (w + l), l))
    rows.sort(key=lambda r: (-r[1], -r[2], r[3]))
    return [r[0] for r in rows]


def _list_seasons(names: list[str], limit: int = 4) -> str:
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[-limit:]) + f" and {len(names) - limit} more"


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def build(self, member) -> discord.Embed:
        from cogs.store import HOUSES
        store = self.bot.get_cog("Store")
        duels = self.bot.get_cog("Duels")
        wands = self.bot.get_cog("Wands")

        house = store.member_house(member) if store else None
        meta = HOUSES.get(house)

        honours = store.honours(member.id) if store else {"champion_of": [], "cups": []}
        header = [f"{meta['emoji']} House {meta['name']}" if meta else "*Not sorted into a house yet*"]
        if honours["champion_of"]:
            n = len(honours["champion_of"])
            header.append(f"\U0001F3C6 **House Cup Champion**" + (f" ×{n}" if n > 1 else ""))

        embed = discord.Embed(
            title=member.display_name,
            description="\n".join(header),
            color=meta["color"] if meta else 0x6C5CE7,
        )
        avatar = getattr(getattr(member, "display_avatar", None), "url", None)
        if avatar:
            embed.set_thumbnail(url=avatar)

        # ------------------------------------------------------------ points
        if store:
            season = store.member_points(member.id, "season")
            alltime = store.member_points(member.id, "alltime")
            rank = store.member_rank(member.id, "season")
            rank_text = f" (#{rank})" if rank else ""
            embed.add_field(
                name="Points",
                value=f"This season **{season:,}**{rank_text}\nAll time **{alltime:,}**",
                inline=True,
            )

        # ---------------------------------------------------------- duelling
        if duels:
            records = duels.state.get("records", {})
            rec = records.get(str(member.id), {"w": 0, "l": 0})
            w, l = rec.get("w", 0), rec.get("l", 0)
            if w + l:
                ladder = duel_ladder(records)
                place = ladder.index(member.id) + 1 if member.id in ladder else None
                pct = round(100 * w / (w + l))
                value = (f"**{duel_title(w)}**\n"
                         + (f"Rank #{place} of {len(ladder)}\n" if place else "")
                         + f"{w}–{l} • {pct}% won")
            else:
                value = "**Untested**\nHasn't duelled yet"
            embed.add_field(name="Duelling", value=value, inline=True)

        # --------------------------------------------------------- House Cup
        cup_lines = []
        if honours["champion_of"]:
            cup_lines.append(f"Champion: {_list_seasons(honours['champion_of'])}")
        if honours["cups"]:
            n = len(honours["cups"])
            cup_lines.append(f"Cups won with their house: **{n}** "
                             f"({_list_seasons(honours['cups'])})")
        embed.add_field(
            name="House Cup",
            value="\n".join(cup_lines) if cup_lines else "No cups yet — the season's still open.",
            inline=False,
        )

        # -------------------------------------------------------------- wand
        wand = wands.wand_of(member.id) if wands else None
        if wand:
            from cogs.wands import _fmt_length
            embed.add_field(
                name="Wand",
                value=(f"**{wand['wood']}, {wand['core'].lower()} core**\n"
                       f"{_fmt_length(wand['length'])}, {wand['flexibility']}\n\n"
                       f"*{wand['reading']}*"),
                inline=False,
            )
        else:
            embed.add_field(name="Wand", value="Not chosen yet — `/wand` to find out.",
                            inline=False)
        return embed

    @app_commands.command(name="profile", description="A player's wand, points, duels and honours.")
    @app_commands.describe(member="Whose profile (leave blank for your own)")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        if getattr(member, "bot", False):
            await interaction.response.send_message(
                "Ghosts don't keep profiles. They keep grudges.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=self.build(member))


async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
