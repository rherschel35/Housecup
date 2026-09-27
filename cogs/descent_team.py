"""
Team Descent — a 3-wizard party climb using the expanded creature roster.

    /descendparty   - open a party lobby (needs 3)
    Join / Leave / Start on the lobby message
    Same Descent channels as solo.

Party runs are separate from solo floor progress. Stats come from each
member's solo Descent record (so grinding solo still matters). Monster
HP/ATK are scaled for three fighters. Each round every living member
picks an action; once all have locked in, damage resolves and the
monster swings at a random living target.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.descent import (
    ASSETS_DIR,
    BOSS_IMAGE,
    BOSS_NAMES,
    CAST_AP_COST,
    DEFEND_AP_COST,
    DESCENT_CHANNEL_IDS,
    ELEMENT_EMOJI,
    ELEMENTS,
    HEAL_AP_COST,
    MONSTER_IMAGE,
    MONSTER_NAMES,
    MONSTERS_PER_FLOOR,
    SECONDARY_CHANCE,
    SECONDARY_ELEMENT,
    SPELL_NAME,
    WEAK_TO,
    _descent_channel_hint,
    monster_stats,
    player_stats,
    zone_for,
)

log = logging.getLogger("velmora.descent_team")

PARTY_SIZE = 3
# Team monsters hit harder/tankier than solo equivalents.
TEAM_HP_MULT = 2.6
TEAM_ATK_MULT = 1.35
TEAM_DEF_MULT = 1.2
LOBBY_TIMEOUT = 10 * 60
ROUND_TIMEOUT = 120


@dataclass
class PartyMember:
    user_id: int
    display_name: str
    hp: int
    hp_max: int
    atk: int
    defense: int
    ap: int
    ap_max: int
    action: Optional[str] = None  # element key / strike / heal / defend / rest
    defending: bool = False


@dataclass
class TeamFight:
    party_id: str
    member_ids: list[int]
    members: dict[int, PartyMember]
    floor: int
    monster_index: int
    is_boss: bool
    name: str
    emoji: str
    element: str
    kind: str
    weak: list[str]
    m_hp: int
    m_hp_max: int
    m_atk: int
    m_def: int
    log_lines: list[str] = field(default_factory=list)
    message: Optional[discord.Message] = None

    def living(self) -> list[PartyMember]:
        return [m for m in self.members.values() if m.hp > 0]

    def all_acted(self) -> bool:
        living = self.living()
        return bool(living) and all(m.action for m in living)

    def embed(self) -> discord.Embed:
        zone = zone_for(self.floor)
        title = f"{self.emoji} Team Floor {self.floor} — {self.name}"
        e = discord.Embed(title=title, color=0x8B5CF6)
        e.description = (
            f"**{zone['name']}** · Monster {self.monster_index}/{MONSTERS_PER_FLOOR}"
            + (" · **BOSS**" if self.is_boss else "")
        )
        e.add_field(
            name="Monster",
            value=f"HP **{self.m_hp}/{self.m_hp_max}** · ATK {self.m_atk} · DEF {self.m_def}\n"
                  f"Element {ELEMENT_EMOJI[self.element]}",
            inline=False,
        )
        lines = []
        for mid in self.member_ids:
            m = self.members[mid]
            status = "💀" if m.hp <= 0 else ("🛡️" if m.defending else "❤️")
            act = f" · _{m.action}_" if m.action and m.hp > 0 else (" · *waiting*" if m.hp > 0 else "")
            lines.append(
                f"{status} **{m.display_name}** HP {max(0, m.hp)}/{m.hp_max} · "
                f"AP {m.ap}/{m.ap_max}{act}"
            )
        e.add_field(name="Party", value="\n".join(lines), inline=False)
        if self.log_lines:
            e.add_field(name="Last round", value="\n".join(self.log_lines[-6:]), inline=False)
        e.set_image(url="attachment://monster.png")
        e.set_footer(text="Everyone living must pick an action each round.")
        return e


class PartyLobby:
    def __init__(self, cog: "DescentTeam", host: discord.Member):
        self.cog = cog
        self.host_id = host.id
        self.members: list[discord.Member] = [host]
        self.message: Optional[discord.Message] = None
        self.started = False
        self.created_at = time.time()

    def embed(self) -> discord.Embed:
        slots = "\n".join(
            f"{'👑' if m.id == self.host_id else '⚔️'} {m.display_name}"
            for m in self.members
        )
        empty = PARTY_SIZE - len(self.members)
        if empty:
            slots += "\n" + "\n".join("▫️ *open*" for _ in range(empty))
        e = discord.Embed(
            title="🕳️ Team Descent — party lobby",
            description=(
                f"Need **{PARTY_SIZE}** wizards. Host starts when full.\n"
                f"Uses each member's solo Descent stats. Separate from solo floors."
            ),
            color=0x6C5CE7,
        )
        e.add_field(name=f"Party ({len(self.members)}/{PARTY_SIZE})", value=slots, inline=False)
        return e


class JoinButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Join", style=discord.ButtonStyle.success, emoji="✋")

    async def callback(self, interaction: discord.Interaction):
        lobby: PartyLobby = self.view.lobby
        if lobby.started:
            await interaction.response.send_message("This party already descended.", ephemeral=True)
            return
        if interaction.user.id in lobby.cog.busy:
            await interaction.response.send_message("You're already in a team fight or lobby.", ephemeral=True)
            return
        if any(m.id == interaction.user.id for m in lobby.members):
            await interaction.response.send_message("You're already in this party.", ephemeral=True)
            return
        if len(lobby.members) >= PARTY_SIZE:
            await interaction.response.send_message("Party is full.", ephemeral=True)
            return
        lobby.members.append(interaction.user)
        lobby.cog.busy.add(interaction.user.id)
        await interaction.response.edit_message(embed=lobby.embed(), view=self.view)


class LeaveButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Leave", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        lobby: PartyLobby = self.view.lobby
        if lobby.started:
            await interaction.response.send_message("Too late — the party is underground.", ephemeral=True)
            return
        if interaction.user.id == lobby.host_id:
            await interaction.response.send_message("Host can't leave — cancel instead.", ephemeral=True)
            return
        before = len(lobby.members)
        lobby.members = [m for m in lobby.members if m.id != interaction.user.id]
        if len(lobby.members) < before:
            lobby.cog.busy.discard(interaction.user.id)
        await interaction.response.edit_message(embed=lobby.embed(), view=self.view)


class StartButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Descend", style=discord.ButtonStyle.primary, emoji="🕳️")

    async def callback(self, interaction: discord.Interaction):
        lobby: PartyLobby = self.view.lobby
        if interaction.user.id != lobby.host_id:
            await interaction.response.send_message("Only the host can start.", ephemeral=True)
            return
        if len(lobby.members) != PARTY_SIZE:
            await interaction.response.send_message(
                f"Need exactly {PARTY_SIZE} wizards.", ephemeral=True)
            return
        if lobby.started:
            await interaction.response.send_message("Already started.", ephemeral=True)
            return
        lobby.started = True
        await interaction.response.defer()
        await lobby.cog.start_party_run(interaction, lobby)
        self.view.stop()


class CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        lobby: PartyLobby = self.view.lobby
        if interaction.user.id != lobby.host_id:
            await interaction.response.send_message("Only the host can cancel.", ephemeral=True)
            return
        for m in lobby.members:
            lobby.cog.busy.discard(m.id)
        lobby.cog.lobbies.pop(lobby.host_id, None)
        await interaction.response.edit_message(
            content="Party lobby cancelled.", embed=None, view=None)
        self.view.stop()


class LobbyView(discord.ui.View):
    def __init__(self, lobby: PartyLobby):
        super().__init__(timeout=LOBBY_TIMEOUT)
        self.lobby = lobby
        self.add_item(JoinButton())
        self.add_item(LeaveButton())
        self.add_item(StartButton())
        self.add_item(CancelButton())

    async def on_timeout(self):
        if self.lobby.started:
            return
        for m in self.lobby.members:
            self.lobby.cog.busy.discard(m.id)
        self.lobby.cog.lobbies.pop(self.lobby.host_id, None)
        if self.lobby.message:
            try:
                await self.lobby.message.edit(content="Lobby timed out.", embed=None, view=None)
            except discord.HTTPException:
                pass


class TeamActionButton(discord.ui.Button):
    def __init__(self, action: str, label: str, emoji: str, style=discord.ButtonStyle.secondary,
                 disabled: bool = False):
        super().__init__(label=label, emoji=emoji, style=style, disabled=disabled)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.lock_action(interaction, self.view.fight, self.action)


class TeamFightView(discord.ui.View):
    def __init__(self, cog: "DescentTeam", fight: TeamFight):
        super().__init__(timeout=ROUND_TIMEOUT)
        self.cog = cog
        self.fight = fight
        for el in ELEMENTS:
            self.add_item(TeamActionButton(
                el, SPELL_NAME[el], ELEMENT_EMOJI[el], discord.ButtonStyle.primary))
        self.add_item(TeamActionButton("strike", "Strike", "⚔️", discord.ButtonStyle.danger))
        self.add_item(TeamActionButton("heal", "Heal", "💚"))
        self.add_item(TeamActionButton("defend", "Defend", "🛡️"))
        self.add_item(TeamActionButton("rest", "Rest", "💤"))

    async def on_timeout(self):
        # Auto-rest anyone who didn't act so the round can resolve.
        fight = self.fight
        if fight.party_id not in self.cog.fights:
            return
        changed = False
        for m in fight.living():
            if not m.action:
                m.action = "rest"
                changed = True
        if changed:
            await self.cog.resolve_round(fight)


class DescentTeam(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lobbies: dict[int, PartyLobby] = {}  # host_id -> lobby
        self.fights: dict[str, TeamFight] = {}    # party_id -> fight
        self.busy: set[int] = set()

    def _solo(self):
        return self.bot.get_cog("Descent")

    def _make_monster(self, floor: int, monster_index: int):
        zone = zone_for(floor)
        primary = zone["element"]
        is_boss = (floor % 10 == 0 and monster_index == MONSTERS_PER_FLOOR)
        if is_boss:
            element = primary
            name, emoji, kind = BOSS_NAMES[floor]
            weak = [WEAK_TO[element], WEAK_TO[WEAK_TO[element]]]
        else:
            secondary = SECONDARY_ELEMENT[primary]
            element = secondary if random.random() < SECONDARY_CHANCE else primary
            name, emoji, kind = random.choice(MONSTER_NAMES[element])
            weak = [WEAK_TO[element]]
        m_hp, m_atk, m_def = monster_stats(floor, is_boss)
        m_hp = round(m_hp * TEAM_HP_MULT)
        m_atk = round(m_atk * TEAM_ATK_MULT)
        m_def = round(m_def * TEAM_DEF_MULT)
        return name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def

    async def _monster_file(self, fight: TeamFight) -> discord.File:
        import io
        from cogs.monster_art import render as render_monster

        filename = BOSS_IMAGE.get(fight.floor) if fight.is_boss else MONSTER_IMAGE.get(fight.name)
        if filename:
            path = ASSETS_DIR / filename
            if path.exists():
                return discord.File(path, filename="monster.png")
        png = render_monster(fight.name, fight.element, fight.kind, fight.is_boss)
        return discord.File(io.BytesIO(png), filename="monster.png")

    def _party_id(self, members: list[discord.Member]) -> str:
        ids = "-".join(str(i) for i in sorted(m.id for m in members))
        return f"team:{ids}:{int(time.time())}"

    async def start_party_run(self, interaction: discord.Interaction, lobby: PartyLobby):
        solo = self._solo()
        if not solo:
            await interaction.followup.send("Solo Descent isn't loaded — can't read party stats.")
            return
        members = list(lobby.members)
        party_id = self._party_id(members)
        party_members: dict[int, PartyMember] = {}
        for m in members:
            rec = solo.record(m.id)
            hp, atk, defense = player_stats(rec)
            ap_max = rec.get("max_ap", 5)
            party_members[m.id] = PartyMember(
                user_id=m.id, display_name=m.display_name,
                hp=hp, hp_max=hp, atk=atk, defense=defense,
                ap=ap_max, ap_max=ap_max,
            )
        # Start at the lowest solo floor among the party (min 1).
        start_floor = min(solo.record(m.id)["floor"] for m in members)
        start_floor = max(1, min(start_floor, 100))

        name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def = self._make_monster(
            start_floor, 1)
        fight = TeamFight(
            party_id=party_id,
            member_ids=[m.id for m in members],
            members=party_members,
            floor=start_floor,
            monster_index=1,
            is_boss=is_boss,
            name=name, emoji=emoji, element=element, kind=kind, weak=weak,
            m_hp=m_hp, m_hp_max=m_hp, m_atk=m_atk, m_def=m_def,
        )
        self.fights[party_id] = fight
        self.lobbies.pop(lobby.host_id, None)
        file = await self._monster_file(fight)
        view = TeamFightView(self, fight)
        msg = await interaction.followup.send(embed=fight.embed(), view=view, file=file)
        fight.message = msg

    async def lock_action(self, interaction: discord.Interaction, fight: TeamFight, action: str):
        if interaction.user.id not in fight.members:
            await interaction.response.send_message("You're not in this party.", ephemeral=True)
            return
        member = fight.members[interaction.user.id]
        if member.hp <= 0:
            await interaction.response.send_message("You're down.", ephemeral=True)
            return
        if member.action:
            await interaction.response.send_message(
                f"You already locked in **{member.action}** this round.", ephemeral=True)
            return
        # AP checks
        cost = 0
        if action in ELEMENTS:
            cost = CAST_AP_COST
        elif action == "heal":
            cost = HEAL_AP_COST
        elif action == "defend":
            cost = DEFEND_AP_COST
        if member.ap < cost:
            await interaction.response.send_message(
                f"Not enough AP (need {cost}, have {member.ap}).", ephemeral=True)
            return
        member.action = action
        await interaction.response.send_message(
            f"Locked in **{SPELL_NAME.get(action, action)}**.", ephemeral=True)
        if fight.message:
            try:
                await fight.message.edit(embed=fight.embed())
            except discord.HTTPException:
                pass
        if fight.all_acted():
            await self.resolve_round(fight)

    async def resolve_round(self, fight: TeamFight):
        if fight.party_id not in self.fights:
            return
        lines: list[str] = []
        # Reset defend flags
        for m in fight.members.values():
            m.defending = False

        # Player actions
        for mid in fight.member_ids:
            m = fight.members[mid]
            if m.hp <= 0 or not m.action:
                continue
            action = m.action
            m.action = None
            if action == "rest":
                gain = max(1, m.ap_max // 2)
                m.ap = min(m.ap_max, m.ap + gain)
                lines.append(f"💤 **{m.display_name}** rests (+{gain} AP).")
                continue
            if action == "defend":
                m.ap -= DEFEND_AP_COST
                m.defending = True
                lines.append(f"🛡️ **{m.display_name}** braces.")
                continue
            if action == "heal":
                m.ap -= HEAL_AP_COST
                healed = max(8, round(m.hp_max * 0.22))
                m.hp = min(m.hp_max, m.hp + healed)
                lines.append(f"💚 **{m.display_name}** heals for **{healed}**.")
                continue
            # damage
            if action in ELEMENTS:
                m.ap -= CAST_AP_COST
                base = m.atk + random.randint(2, 8)
                label = SPELL_NAME[action]
                if action == fight.element:
                    base = round(base * 0.5)
                    note = " (resisted)"
                elif action in fight.weak:
                    base = round(base * 2.0)
                    note = " (weakness!)"
                else:
                    note = ""
            else:  # strike
                base = m.atk + random.randint(1, 5)
                label = "Strike"
                note = ""
            dmg = max(1, base - fight.m_def // 3)
            fight.m_hp = max(0, fight.m_hp - dmg)
            lines.append(f"⚔️ **{m.display_name}** {label} for **{dmg}**{note}.")

        fight.log_lines = lines

        if fight.m_hp <= 0:
            await self._on_monster_down(fight)
            return

        # Monster counterattack — hit one living member (boss: chance to cleave 2)
        living = fight.living()
        if not living:
            await self._on_party_wipe(fight)
            return
        targets = random.sample(living, k=min(2 if fight.is_boss and len(living) > 1 else 1, len(living)))
        for t in targets:
            raw = fight.m_atk + random.randint(0, 6)
            if t.defending:
                raw = max(1, raw // 2)
            dmg = max(1, raw - t.defense // 3)
            t.hp = max(0, t.hp - dmg)
            lines.append(
                f"{fight.emoji} **{fight.name}** hits **{t.display_name}** for **{dmg}**"
                + (" (defended)" if t.defending else "") + "."
            )
        fight.log_lines = lines

        if not fight.living():
            await self._on_party_wipe(fight)
            return

        file = await self._monster_file(fight)
        view = TeamFightView(self, fight)
        if fight.message:
            try:
                await fight.message.edit(embed=fight.embed(), view=view, attachments=[file])
            except discord.HTTPException:
                log.exception("Failed to refresh team fight message")

    async def _on_monster_down(self, fight: TeamFight):
        lines = list(fight.log_lines)
        lines.append(f"✅ **{fight.name}** falls!")
        cleared = fight.monster_index
        if cleared >= MONSTERS_PER_FLOOR:
            if fight.floor >= 100:
                await self._finish_run(fight, victory=True, extra="\n".join(lines) + "\n\n🏆 **Floor 100 cleared!**")
                return
            fight.floor += 1
            fight.monster_index = 1
            # Heal party a bit between floors
            for m in fight.members.values():
                if m.hp > 0:
                    m.hp = min(m.hp_max, m.hp + max(5, m.hp_max // 10))
                    m.ap = m.ap_max
            lines.append(f"🕳️ **Floor {fight.floor - 1} cleared!** Advancing…")
        else:
            fight.monster_index = cleared + 1

        name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def = self._make_monster(
            fight.floor, fight.monster_index)
        fight.name, fight.emoji, fight.element, fight.kind = name, emoji, element, kind
        fight.weak, fight.is_boss = weak, is_boss
        fight.m_hp = fight.m_hp_max = m_hp
        fight.m_atk, fight.m_def = m_atk, m_def
        for m in fight.members.values():
            m.action = None
            m.defending = False
        fight.log_lines = lines

        file = await self._monster_file(fight)
        view = TeamFightView(self, fight)
        if fight.message:
            await fight.message.edit(embed=fight.embed(), view=view, attachments=[file])

    async def _on_party_wipe(self, fight: TeamFight):
        await self._finish_run(
            fight, victory=False,
            extra="\n".join(fight.log_lines) + f"\n\n💀 The party falls on floor **{fight.floor}**.")

    async def _finish_run(self, fight: TeamFight, victory: bool, extra: str):
        self.fights.pop(fight.party_id, None)
        for mid in fight.member_ids:
            self.busy.discard(mid)
        # Small house-point toast on victory
        if victory:
            store = self.bot.get_cog("Store")
            guild = fight.message.guild if fight.message else None
            if store and self.bot.user and guild:
                for mid in fight.member_ids:
                    member = guild.get_member(mid)
                    house = store.member_house(member) if member else None
                    if house:
                        store.record(
                            house=house, delta=8,
                            actor_id=self.bot.user.id, target_id=mid,
                            reason="Team Descent: floor 100 clear",
                        )
        color = 0x2ECC71 if victory else 0xC0392B
        e = discord.Embed(
            title="🏆 Team Descent complete" if victory else "💀 Team Descent failed",
            description=extra,
            color=color,
        )
        if fight.message:
            try:
                await fight.message.edit(embed=e, view=None, attachments=[])
            except discord.HTTPException:
                pass

    # --------------------------------------------------------------- commands

    @app_commands.command(name="descendparty", description="Open a 3-wizard Team Descent lobby.")
    async def descendparty(self, interaction: discord.Interaction):
        if interaction.channel_id not in DESCENT_CHANNEL_IDS:
            await interaction.response.send_message(_descent_channel_hint(), ephemeral=True)
            return
        if interaction.user.id in self.busy:
            await interaction.response.send_message(
                "You're already in a team lobby or fight.", ephemeral=True)
            return
        if interaction.user.id in self.lobbies:
            await interaction.response.send_message(
                "You already have a lobby open.", ephemeral=True)
            return
        lobby = PartyLobby(self, interaction.user)
        self.lobbies[interaction.user.id] = lobby
        self.busy.add(interaction.user.id)
        view = LobbyView(lobby)
        await interaction.response.send_message(embed=lobby.embed(), view=view)
        lobby.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(DescentTeam(bot))
