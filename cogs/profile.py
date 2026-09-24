"""
/profile [member] - one player's whole story at Velmora.

    House and honours     - House Cup and Tri-Wizard titles, cups won
    Points                - this season and all time, with rank
    Duelling              - rank, ladder place, wins, streak, bounty, rival
    Bestiary              - collector rank, beasts befriended, beast titles
    Wand                  - what chose them, and why
    Patronus              - the shape their protection takes

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

from cogs.duels import rank_for as duel_title, REP_GLOW, CHAMPION_ROLE_NAME


def duel_ladder(records: dict) -> list[int]:
    """Everyone who has won a duel, best first: most wins, then fewest
    losses. (Only the order is shown - never anyone's losses or win rate.)"""
    rows = []
    for uid, rec in records.items():
        w, l = rec.get("w", 0), rec.get("l", 0)
        if w == 0:
            continue
        rows.append((int(uid), w, l))
    rows.sort(key=lambda r: (-r[1], r[2]))
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
        triwizard = self.bot.get_cog("TriWizard")
        tw_titles = triwizard.titles_of(member.id) if triwizard else []
        if tw_titles:
            n = len(tw_titles)
            header.append(f"\U0001F3C5 **Tri-Wizard Champion**" + (f" ×{n}" if n > 1 else ""))
        if duels and duels.is_champion(member.id):
            header.append(f"\U0001F3C6 **{CHAMPION_ROLE_NAME}** (Duelist of the Week)")

        sig = duels.signature_of(member.id) if duels else None
        adorn = self.bot.get_cog("Adornments")
        shown = adorn.title_of(member) if adorn else (sig[1] if sig else None)
        if shown:
            name_line = f"{member.display_name} {shown}" if shown.startswith("the ") else f"{member.display_name}, {shown}"
        else:
            name_line = member.display_name
        embed = discord.Embed(
            title=name_line,
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
                lines = [f"**{duel_title(w)}**"]
                if place:
                    lines.append(f"#{place} of {len(ladder)} duellists")
                lines.append(f"{w} win{'s' if w != 1 else ''}")
                streak = duels.streak_of(member.id)
                if streak >= 2:
                    lines.append(f"\U0001F525 {streak}-win streak")
                if duels.has_bounty(member.id):
                    lines.append("\U0001F3AF Bounty on their head")
                rivals = duels.rivals_of(member.id)
                if rivals:
                    guild = getattr(member, "guild", None)
                    rm = guild.get_member(rivals[0]) if guild else None
                    lines.append(f"Rival: {rm.display_name if rm else f'<@{rivals[0]}>'}")
                value = "\n".join(lines)
            else:
                value = "**Untested**\nHasn't duelled yet"
            embed.add_field(name="Duelling", value=value, inline=True)

        # ------------------------------------------------------------ beasts
        beasts_cog = self.bot.get_cog("Beasts")
        if beasts_cog:
            from cogs.beasts import rank_for as beast_rank
            n = beasts_cog.count_of(member.id)
            if n:
                titles = beasts_cog.titles_of(member.id)
                value = f"**{beast_rank(n)}**\n{n} of {len(beasts_cog.beasts)} befriended"
                if titles:
                    value += "\n" + "\n".join(f"\U0001F3C5 {t}" for t in titles[:4])
            else:
                value = "**Unacquainted**\nNo beasts befriended yet"
            embed.add_field(name="Bestiary", value=value, inline=True)

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

        # --------------------------------------------------------- Tri-Wizard
        # Only shown to those who've won one - it's rare, and an empty
        # section on everyone else's card would just be noise.
        if tw_titles:
            shown = "\n".join(f"Champion of the **{r['name']}**" for r in tw_titles[-4:])
            if len(tw_titles) > 4:
                shown += f"\n…and {len(tw_titles) - 4} more"
            embed.add_field(name="Tri-Wizard Tournament", value=shown, inline=False)

        # -------------------------------------------------------------- wand
        wand = wands.wand_of(member.id) if wands else None
        if wand:
            from cogs.wands import _fmt_length
            embed.add_field(
                name="Wand",
                value=(f"**{wand['wood']}, {wand['core'].lower()} core**"
                       + (" \u2728 *it glows*" if duels and duels.rep_of(member.id) >= REP_GLOW else "")
                       + "\n"
                       f"{_fmt_length(wand['length'])}, {wand['flexibility']}\n\n"
                       f"*{wand['reading']}*"),
                inline=False,
            )
        else:
            embed.add_field(name="Wand", value="Not chosen yet — `/wand` to find out.",
                            inline=False)

        # ---------------------------------------------------------- patronus
        # Only once they have a wand - without one there's nothing to cast from.
        patronus_cog = self.bot.get_cog("Patronus")
        patronus = patronus_cog.patronus_of(member.id) if patronus_cog else None
        if patronus:
            embed.add_field(
                name="Patronus",
                value=f"**A silver {patronus['animal'].lower()}**",
                inline=True,
            )
        elif wand:
            embed.add_field(name="Patronus", value="Not cast yet \u2014 `/patronus`.",
                            inline=True)

        # --------------------------------------------------------- familiar
        familiars_cog = self.bot.get_cog("Familiars")
        fam = familiars_cog.familiar_of(member.id) if familiars_cog else None
        if fam:
            from cogs.familiars import FAMILIARS, _tier
            meta = FAMILIARS[fam["species"]]
            _, tier_name, _, _ = _tier(fam["friendship"])
            label = f"**{fam['name']}** the {meta['label']}" if fam.get("name") else f"{meta['article']} {meta['label']}"
            embed.add_field(
                name="Familiar",
                value=f"{meta['emoji']} {label}\n{tier_name}",
                inline=True,
            )
        else:
            embed.add_field(name="Familiar", value="Not adopted yet \u2014 `/familiar`.",
                            inline=True)

        # -------------------------------------------------------- adornments
        if adorn:
            embed.add_field(name="Adornments", value=adorn.profile_line(member.id), inline=False)
        return embed

    @app_commands.command(name="profile", description="A player's wand, points, duels, beasts and honours.")
    @app_commands.describe(member="Whose profile (leave blank for your own)")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        if getattr(member, "bot", False):
            await interaction.response.send_message(
                "Ghosts don't keep profiles. They keep grudges.", ephemeral=True
            )
            return
        adorn = self.bot.get_cog("Adornments")
        if not adorn:
            await interaction.response.send_message(embed=self.build(member))
            return
        # the Mirror portrait takes a moment to draw
        await interaction.response.defer()
        try:
            await adorn.check_member(member)
        except Exception:
            log.exception("Gear check on /profile failed")
        embed = self.build(member)
        try:
            file = await adorn.mirror_file(member, name="mirror.png")
            embed.set_thumbnail(url="attachment://mirror.png")
            await interaction.followup.send(embed=embed, file=file)
        except Exception:
            log.exception("Mirror portrait for /profile failed")
            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
