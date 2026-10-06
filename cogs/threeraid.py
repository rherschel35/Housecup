"""
3Raid — a weekly 3-player strategy raid, separate from solo Descent.

    /raid          - open (or re-show) the raid lobby in the 3Raid channel
    /raidstatus    - your weekly clear status
    /staff raid reset  - clear someone's weekly lockout / force-end a run

Flow: lobby → claim Attacker / Specialty / Tank → spend 6 skill points
(max 2 per spell, min 1 in your primary lane) → hard party Start checks →
10 encounters → 25 house points each on clear (once per player per week).

Power resets every raid. Descent stats never carry in.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.threeraid")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "threeraid_state.json"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "3raid_art_assets"

THREERAID_CHANNEL_ID = 1553925340043157504

PARTY_SIZE = 3
SKILL_BUDGET = 6
SKILL_MAX = 2
POINTS_ON_CLEAR = 25
ROUND_TIMEOUT = 90
SIGNUP_TIMEOUT = 1800
FIGHT_VIEW_TIMEOUT = 1800

BASE_AP = 5
AP_REGEN = 1


def week_key(now: float | None = None) -> str:
    d = datetime.datetime.fromtimestamp(now if now is not None else time.time(),
                                        datetime.timezone.utc)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _channel_hint() -> str:
    return f"3Raid can only be played in <#{THREERAID_CHANNEL_ID}>."


# ------------------------------------------------------------------ spells
# rank 0 = locked. Spending points raises rank 1 or 2.

SPELLS: dict[str, dict] = {
    # Attacker
    "incendio": {
        "role": "attacker", "label": "Incendio", "emoji": "🔥", "ap": 2,
        "kind": "damage", "element": "fire", "primary": True,
        "desc": "Solid fire damage. Strong vs ice shields.",
    },
    "glacius": {
        "role": "attacker", "label": "Glacius", "emoji": "❄️", "ap": 2,
        "kind": "damage", "element": "ice", "primary": True,
        "desc": "Solid ice damage. Strong vs fire shields.",
    },
    "fulgur": {
        "role": "attacker", "label": "Fulgur", "emoji": "⚡", "ap": 2,
        "kind": "damage", "element": "storm", "primary": True,
        "desc": "Solid lightning damage. Strong vs armored elites.",
    },
    "quick_hex": {
        "role": "attacker", "label": "Quick Hex", "emoji": "✨", "ap": 0,
        "kind": "damage", "element": None, "primary": False,
        "desc": "Low chip damage. Free when you're short on AP.",
    },
    "fracture": {
        "role": "attacker", "label": "Fracture", "emoji": "💥", "ap": 2,
        "kind": "damage", "element": None, "primary": True,
        "desc": "Medium damage. Huge bonus if the foe is Exposed / unshielded after a break.",
    },
    "arcane_lance": {
        "role": "attacker", "label": "Arcane Lance", "emoji": "🔱", "ap": 4,
        "kind": "damage", "element": None, "primary": True,
        "desc": "High damage, high AP. Punished hard if you Lance into a shield.",
    },
    "coup": {
        "role": "attacker", "label": "Coup de Grâce", "emoji": "☠️", "ap": 3,
        "kind": "damage", "element": None, "primary": True,
        "desc": "Big hit only under 25% HP. Fizzles otherwise.",
    },
    # Specialty
    "mend": {
        "role": "specialty", "label": "Mend", "emoji": "💚", "ap": 2,
        "kind": "heal", "primary": True,
        "desc": "Solid single-target heal.",
    },
    "circle": {
        "role": "specialty", "label": "Circle of Relief", "emoji": "🌿", "ap": 3,
        "kind": "heal_aoe", "primary": True,
        "desc": "Small heal on all living allies.",
    },
    "aegis": {
        "role": "specialty", "label": "Aegis", "emoji": "🔵", "ap": 2,
        "kind": "ally_shield", "primary": True,
        "desc": "Put an absorb shield on one ally.",
    },
    "ward_break": {
        "role": "specialty", "label": "Ward Break", "emoji": "🔓", "ap": 2,
        "kind": "break", "primary": True,
        "desc": "Strip the enemy shield / barrier. Opens Fracture windows.",
    },
    "sanctuary": {
        "role": "specialty", "label": "Sanctuary", "emoji": "🕊️", "ap": 4,
        "kind": "panic", "primary": False,
        "desc": "Big heal on one ally plus a short shield. Once-per-fight feel.",
    },
    # Tank
    "provoke": {
        "role": "tank", "label": "Provoke", "emoji": "🫵", "ap": 1,
        "kind": "taunt", "primary": True, "mitigation": True,
        "desc": "Force the monster to target you this round.",
    },
    "bulwark": {
        "role": "tank", "label": "Bulwark", "emoji": "🛡️", "ap": 2,
        "kind": "self_mit", "primary": True, "mitigation": True,
        "desc": "Heavy self-mitigation this round.",
    },
    "guard": {
        "role": "tank", "label": "Guard", "emoji": "🤝", "ap": 2,
        "kind": "cover", "primary": True, "mitigation": True,
        "desc": "Cut damage aimed at a chosen ally this round.",
    },
    "shatter_bash": {
        "role": "tank", "label": "Shatter Bash", "emoji": "🔨", "ap": 2,
        "kind": "expose", "primary": False, "mitigation": False,
        "desc": "Low damage + Expose the foe (helps Fracture).",
    },
}

ROLE_ORDER = ("attacker", "specialty", "tank")
ROLE_META = {
    "attacker": {"label": "Attacker", "emoji": "⚔️", "hp": 110, "atk": 24, "defense": 8},
    "specialty": {"label": "Specialty", "emoji": "🔮", "hp": 105, "atk": 10, "defense": 10},
    "tank": {"label": "Tank", "emoji": "🛡️", "hp": 180, "atk": 12, "defense": 20},
}


def spells_for(role: str) -> list[str]:
    return [sid for sid, s in SPELLS.items() if s["role"] == role]


def blank_player() -> dict:
    return {"clears": {}, "last_clear_week": None}


# ------------------------------------------------------------- encounters
# (index, name, kind, hp, atk, defense, traits)
# traits: shield_start, shield_element, cleave_every, armored, boss

ENCOUNTERS: list[dict] = [
    {
        "index": 1, "name": "Ashling Skulk", "kind": "trash",
        "hp": 220, "atk": 18, "defense": 4,
        "traits": {"element": "fire"},
        "image": "Ashling_Skulk.png", "emoji": "🔥",
    },
    {
        "index": 2, "name": "Rift Gnawer", "kind": "trash",
        "hp": 240, "atk": 20, "defense": 5,
        "traits": {"element": "void"},
        "image": "Rift_Gnawer.png", "emoji": "🕳️",
    },
    {
        "index": 3, "name": "Sporebound Sentinel", "kind": "trash",
        "hp": 280, "atk": 17, "defense": 8,
        "traits": {"element": "nature", "shield_start": True, "shield_element": "nature", "shield_hp": 60},
        "image": "Sporebound_Sentinel.png", "emoji": "🍄",
    },
    {
        "index": 4, "name": "The Wardkeeper", "kind": "elite",
        "hp": 480, "atk": 22, "defense": 12,
        "traits": {
            "element": "arcane", "shield_start": True, "shield_element": "arcane",
            "shield_hp": 100, "shield_refresh_every": 4, "armored": True,
        },
        "image": "The_Wardkeeper.png", "emoji": "🔷",
    },
    {
        "index": 5, "name": "Cinder Hound", "kind": "trash",
        "hp": 300, "atk": 26, "defense": 6,
        "traits": {"element": "fire"},
        "image": "Cinder_Hound.png", "emoji": "🐕",
    },
    {
        "index": 6, "name": "Veinroot Stalker", "kind": "trash",
        "hp": 320, "atk": 23, "defense": 9,
        "traits": {"element": "nature"},
        "image": "Veinroot_Stalker.png", "emoji": "🌿",
    },
    {
        "index": 7, "name": "Storm-Touched Warden", "kind": "trash",
        "hp": 340, "atk": 25, "defense": 10,
        "traits": {"element": "storm", "cleave_every": 3, "armored": True},
        "image": "Storm-Touched_Warden.png", "emoji": "🌩️",
    },
    {
        "index": 8, "name": "The Cleaver Choir", "kind": "elite",
        "hp": 540, "atk": 24, "defense": 10,
        "traits": {"element": "hollow", "cleave_every": 2},
        "image": "The_Cleaver_Choir.png", "emoji": "🪓",
    },
    {
        "index": 9, "name": "Hollowbound Reaver", "kind": "trash",
        "hp": 380, "atk": 30, "defense": 11,
        "traits": {"element": "hollow"},
        "image": "Hollowbound_Reaver.png", "emoji": "💀",
    },
    {
        "index": 10, "name": "The Hollow Triad", "kind": "boss",
        "hp": 680, "atk": 24, "defense": 12,
        "traits": {
            "element": "hollow", "shield_start": True, "shield_element": "hollow",
            "shield_hp": 120, "shield_refresh_every": 5, "cleave_every": 3,
            "armored": True, "boss": True,
        },
        "image": "The_Hollow_Triad.png", "emoji": "👑",
    },
]


def mitigate(raw: float, defense: int) -> int:
    return max(1, int(round(raw * (100 / (100 + max(0, defense))))))


def bar(cur: int, mx: int, width: int = 10) -> str:
    if mx <= 0:
        return "░" * width
    filled = max(0, min(width, int(round(width * max(0, cur) / mx))))
    return "█" * filled + "░" * (width - filled)


# ================================================================== lobby

class Lobby:
    def __init__(self, host_id: int):
        self.host_id = host_id
        self.roles: dict[str, Optional[int]] = {r: None for r in ROLE_ORDER}
        self.drafts: dict[int, dict[str, int]] = {}  # user_id -> spell_id -> rank
        self.confirmed: set[int] = set()
        self.message: Optional[discord.Message] = None
        self.created_at = time.time()
        self.cancelled = False

    def member_ids(self) -> list[int]:
        return [uid for uid in self.roles.values() if uid is not None]

    def role_of(self, user_id: int) -> Optional[str]:
        for role, uid in self.roles.items():
            if uid == user_id:
                return role
        return None

    def full(self) -> bool:
        return all(uid is not None for uid in self.roles.values())

    def draft_for(self, user_id: int) -> dict[str, int]:
        role = self.role_of(user_id)
        if not role:
            return {}
        if user_id not in self.drafts:
            self.drafts[user_id] = {sid: 0 for sid in spells_for(role)}
        return self.drafts[user_id]

    def points_spent(self, user_id: int) -> int:
        return sum(self.draft_for(user_id).values())

    def primary_spent(self, user_id: int) -> int:
        draft = self.draft_for(user_id)
        return sum(rank for sid, rank in draft.items() if SPELLS[sid].get("primary"))

    def hard_check(self, user_id: int) -> Optional[str]:
        role = self.role_of(user_id)
        if not role:
            return "No role claimed."
        draft = self.draft_for(user_id)
        spent = sum(draft.values())
        if spent != SKILL_BUDGET:
            return f"Spend all {SKILL_BUDGET} skill points (you've spent {spent})."
        if self.primary_spent(user_id) < 1:
            return "Put at least 1 point into a primary-lane spell."
        if user_id not in self.confirmed:
            return "Confirm your draft."
        if role == "attacker":
            dmg = sum(draft.get(sid, 0) for sid, s in SPELLS.items()
                      if s["role"] == "attacker" and s["kind"] == "damage" and sid != "quick_hex")
            if dmg < 3:
                return "Attacker needs at least 3 total Damage ranks (not counting Quick Hex)."
        elif role == "tank":
            mit = sum(draft.get(sid, 0) for sid, s in SPELLS.items()
                      if s["role"] == "tank" and s.get("mitigation"))
            if mit < 2:
                return "Tank needs at least 2 total Mitigation ranks (Provoke / Bulwark / Guard)."
        elif role == "specialty":
            if draft.get("ward_break", 0) < 1:
                return "Specialty needs at least 1 rank in Ward Break."
        return None

    def can_start(self) -> Optional[str]:
        if not self.full():
            return "Need one Attacker, one Specialty, and one Tank."
        for uid in self.member_ids():
            err = self.hard_check(uid)
            if err:
                role = self.role_of(uid)
                return f"<@{uid}> ({ROLE_META[role]['label']}): {err}"
        return None

    def embed(self) -> discord.Embed:
        e = discord.Embed(
            title="🏰 3Raid Lobby",
            description=(
                "Exactly **3** wizards. Claim a role, spend **6** skill points "
                f"(max **{SKILL_MAX}** per spell, min **1** primary), then Confirm.\n"
                "Power resets every raid. **25** house points each on clear, once per week."
            ),
            color=0x2C3E50,
        )
        for role in ROLE_ORDER:
            meta = ROLE_META[role]
            uid = self.roles[role]
            if uid is None:
                value = "— open —"
            else:
                spent = self.points_spent(uid)
                mark = "✅" if uid in self.confirmed else f"`{spent}/{SKILL_BUDGET}` pts"
                value = f"<@{uid}> {mark}"
            e.add_field(name=f"{meta['emoji']} {meta['label']}", value=value, inline=True)
        err = self.can_start()
        e.set_footer(text="Ready to start." if err is None else f"Blocked: {err}")
        return e


# ================================================================== fight

class Fighter:
    def __init__(self, user_id: int, role: str, ranks: dict[str, int]):
        meta = ROLE_META[role]
        self.user_id = user_id
        self.role = role
        self.ranks = dict(ranks)
        self.hp_max = meta["hp"]
        self.hp = meta["hp"]
        self.atk = meta["atk"]
        self.defense = meta["defense"]
        self.ap_max = BASE_AP
        self.ap = BASE_AP
        self.shield = 0
        self.alive = True
        # per-round flags cleared each round start
        self.mit_mult = 1.0
        self.covering: Optional[int] = None  # user_id being guarded
        self.sanctuary_used = False

    def display(self, guild: Optional[discord.Guild] = None) -> str:
        meta = ROLE_META[self.role]
        name = f"<@{self.user_id}>"
        status = "💀" if not self.alive else f"{bar(self.hp, self.hp_max, 8)} {self.hp}/{self.hp_max}"
        sh = f" 🔵{self.shield}" if self.shield else ""
        ap = f"⚡{self.ap}/{self.ap_max}" if self.alive else ""
        return f"{meta['emoji']} {name} {status}{sh} {ap}".strip()


class RaidFight:
    """One encounter inside an active RaidRun."""

    def __init__(self, encounter: dict, fighters: dict[int, Fighter]):
        self.encounter = encounter
        self.fighters = fighters  # user_id -> Fighter
        self.m_hp_max = encounter["hp"]
        self.m_hp = encounter["hp"]
        self.m_atk = encounter["atk"]
        self.m_def = encounter["defense"]
        traits = encounter["traits"]
        self.traits = traits
        self.m_shield = int(traits.get("shield_hp", 0)) if traits.get("shield_start") else 0
        self.m_shield_max = int(traits.get("shield_hp", 0))
        self.exposed = False
        self.round = 0
        self.log: list[str] = []
        self.pending: dict[int, dict] = {}  # user_id -> {spell, target?}
        self.taunt_target: Optional[int] = None
        self.telegraph_cleave = False
        self.finished = False
        self.won = False
        self.message: Optional[discord.Message] = None

    def living(self) -> list[Fighter]:
        return [f for f in self.fighters.values() if f.alive]

    def by_role(self, role: str) -> Optional[Fighter]:
        for f in self.fighters.values():
            if f.role == role and f.alive:
                return f
            if f.role == role:
                return f
        return None

    def all_acted(self) -> bool:
        return all(f.user_id in self.pending for f in self.living())

    def begin_round(self):
        self.round += 1
        self.pending.clear()
        self.taunt_target = None
        for f in self.fighters.values():
            f.mit_mult = 1.0
            f.covering = None
        # shield refresh
        every = self.traits.get("shield_refresh_every")
        if every and self.round > 1 and (self.round - 1) % every == 0 and self.m_hp > 0:
            self.m_shield = self.m_shield_max
            self.exposed = False
            self.log.append(f"{self.encounter['emoji']} **{self.encounter['name']}** raises a ward.")
        # cleave telegraph
        every_c = self.traits.get("cleave_every")
        if every_c and self.round % every_c == 0:
            self.telegraph_cleave = True
            self.log.append(f"⚠️ **{self.encounter['name']}** winds up a **cleave**!")
        else:
            self.telegraph_cleave = False

    def embed(self, run_index: int) -> discord.Embed:
        enc = self.encounter
        kind = enc["kind"].upper()
        title = f"{enc['emoji']} Encounter {run_index}/10 — {enc['name']}"
        if enc["kind"] != "trash":
            title += f" ({kind})"
        e = discord.Embed(title=title, color=0x8B0000 if enc["kind"] == "boss" else 0x34495E)
        e.set_image(url="attachment://monster.png")
        sh = f"\n🔵 Ward {self.m_shield}/{self.m_shield_max}" if self.m_shield > 0 else ""
        exp = "\n💥 **Exposed**" if self.exposed else ""
        e.add_field(
            name=enc["name"],
            value=f"{bar(self.m_hp, self.m_hp_max)} {max(0, self.m_hp)}/{self.m_hp_max}{sh}{exp}",
            inline=False,
        )
        party_lines = [f.display() for f in self.fighters.values()]
        e.add_field(name="Party", value="\n".join(party_lines), inline=False)
        waiting = [f"<@{f.user_id}>" for f in self.living() if f.user_id not in self.pending]
        if not self.finished:
            e.add_field(
                name=f"Round {self.round}",
                value=("Waiting on: " + ", ".join(waiting)) if waiting else "Resolving…",
                inline=False,
            )
        if self.log:
            e.add_field(name="Last beats", value="\n".join(self.log[-4:]), inline=False)
        e.set_footer(text=f"{ROUND_TIMEOUT}s per round · pick your spell below")
        return e


class RaidRun:
    def __init__(self, lobby: Lobby):
        self.roles = dict(lobby.roles)  # role -> user_id
        self.drafts = {uid: dict(lobby.drafts[uid]) for uid in lobby.member_ids()}
        self.encounter_index = 1  # 1..10
        self.fight: Optional[RaidFight] = None
        self.message: Optional[discord.Message] = None
        self.finished = False
        self.cleared = False

    def member_ids(self) -> list[int]:
        return list(self.drafts.keys())

    def make_fighters(self) -> dict[int, Fighter]:
        out = {}
        for role, uid in self.roles.items():
            out[uid] = Fighter(uid, role, self.drafts[uid])
        return out


# ================================================================== views

class ClaimRoleButton(discord.ui.Button):
    def __init__(self, role: str):
        meta = ROLE_META[role]
        super().__init__(label=meta["label"], emoji=meta["emoji"],
                         style=discord.ButtonStyle.primary, custom_id=f"raid:claim:{role}")
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.claim_role(interaction, self.view.lobby, self.role)


class LeaveLobbyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Leave", style=discord.ButtonStyle.secondary, emoji="🚪")

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.leave_lobby(interaction, self.view.lobby)


class DraftButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Draft Skills", style=discord.ButtonStyle.success, emoji="📜")

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.open_draft(interaction, self.view.lobby)


class StartRaidButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Start Raid", style=discord.ButtonStyle.danger, emoji="▶️")

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.start_raid(interaction, self.view.lobby)


class CancelLobbyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.cancel_lobby(interaction, self.view.lobby)


class LobbyView(discord.ui.View):
    def __init__(self, cog: "ThreeRaid", lobby: Lobby):
        super().__init__(timeout=SIGNUP_TIMEOUT)
        self.cog = cog
        self.lobby = lobby
        for role in ROLE_ORDER:
            self.add_item(ClaimRoleButton(role))
        self.add_item(DraftButton())
        self.add_item(StartRaidButton())
        self.add_item(LeaveLobbyButton())
        self.add_item(CancelLobbyButton())

    async def on_timeout(self):
        await self.cog.lobby_timeout(self.lobby)


class DraftAdjustButton(discord.ui.Button):
    def __init__(self, spell_id: str, delta: int, row: int):
        spell = SPELLS[spell_id]
        label = f"{'+' if delta > 0 else '−'}{spell['label']}"
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.success if delta > 0 else discord.ButtonStyle.secondary,
            row=row,
        )
        self.spell_id = spell_id
        self.delta = delta

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.adjust_draft(interaction, self.view.lobby, self.spell_id, self.delta)


class DraftConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Confirm Draft", style=discord.ButtonStyle.danger, emoji="✅", row=4)

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.confirm_draft(interaction, self.view.lobby)


class DraftView(discord.ui.View):
    def __init__(self, cog: "ThreeRaid", lobby: Lobby, user_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.lobby = lobby
        self.user_id = user_id
        role = lobby.role_of(user_id)
        spells = spells_for(role)
        # Discord: 5 rows. Put +/- pairs; with 7 attacker spells we only show + buttons
        # and a separate − row pattern: one + per spell across rows 0-3, confirm on 4.
        # Simpler: each spell gets a + button; long-press style via repeated clicks.
        # Also add a Reset and Confirm.
        for i, sid in enumerate(spells[:5]):
            self.add_item(DraftAdjustButton(sid, +1, row=0 if i < 5 else 1))
        if len(spells) > 5:
            for i, sid in enumerate(spells[5:]):
                self.add_item(DraftAdjustButton(sid, +1, row=1))
        for i, sid in enumerate(spells[:5]):
            self.add_item(DraftAdjustButton(sid, -1, row=2))
        if len(spells) > 5:
            for i, sid in enumerate(spells[5:]):
                self.add_item(DraftAdjustButton(sid, -1, row=3))
        self.add_item(DraftConfirmButton())


class SpellButton(discord.ui.Button):
    def __init__(self, spell_id: str, fighter: Fighter, row: int):
        spell = SPELLS[spell_id]
        rank = fighter.ranks.get(spell_id, 0)
        disabled = (rank < 1) or (fighter.ap < spell["ap"]) or (not fighter.alive)
        if spell_id == "sanctuary" and fighter.sanctuary_used:
            disabled = True
        label = f"{spell['label']} r{rank}"
        if spell["ap"]:
            label += f" ({spell['ap']}⚡)"
        super().__init__(
            label=label[:80],
            emoji=spell["emoji"],
            style=discord.ButtonStyle.primary if rank else discord.ButtonStyle.secondary,
            disabled=disabled,
            row=row,
        )
        self.spell_id = spell_id

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.pick_spell(interaction, self.view.run, self.spell_id)


class RestButton(discord.ui.Button):
    def __init__(self, fighter: Fighter):
        super().__init__(
            label="Rest (refill AP)",
            emoji="😮‍💨",
            style=discord.ButtonStyle.danger,
            disabled=not fighter.alive or fighter.ap >= fighter.ap_max,
            row=4,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.submit_action(interaction, self.view.run, "rest", None)


class FightView(discord.ui.View):
    def __init__(self, cog: "ThreeRaid", run: RaidRun, for_user: int):
        super().__init__(timeout=FIGHT_VIEW_TIMEOUT)
        self.cog = cog
        self.run = run
        fight = run.fight
        fighter = fight.fighters[for_user]
        spells = [sid for sid in spells_for(fighter.role) if fighter.ranks.get(sid, 0) >= 1]
        for i, sid in enumerate(spells[:5]):
            self.add_item(SpellButton(sid, fighter, row=0))
        for sid in spells[5:10]:
            self.add_item(SpellButton(sid, fighter, row=1))
        self.add_item(RestButton(fighter))


class ActButton(discord.ui.Button):
    def __init__(self, user_id: int, label: str, emoji: str, disabled: bool):
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.success if not disabled else discord.ButtonStyle.secondary,
            disabled=disabled,
        )
        self.owner_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your action.", ephemeral=True)
            return
        await self.view.cog.open_act(interaction, self.view.run)


class PublicFightView(discord.ui.View):
    def __init__(self, cog: "ThreeRaid", run: RaidRun):
        super().__init__(timeout=FIGHT_VIEW_TIMEOUT)
        self.cog = cog
        self.run = run
        fight = run.fight
        for role in ROLE_ORDER:
            uid = run.roles[role]
            f = fight.fighters[uid]
            meta = ROLE_META[role]
            done = uid in fight.pending or not f.alive or fight.finished
            self.add_item(ActButton(uid, meta["label"], meta["emoji"], disabled=done))


# ================================================================== cog

class ThreeRaid(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self.lobby: Optional[Lobby] = None
        self.run: Optional[RaidRun] = None
        self._resolve_lock = asyncio.Lock()

    def _load(self) -> dict:
        if STATE_PATH.exists():
            try:
                return json.loads(STATE_PATH.read_text())
            except Exception:
                log.exception("Failed to load threeraid state")
        return {"players": {}}

    def save(self):
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        os.replace(tmp, STATE_PATH)

    def record(self, user_id: int) -> dict:
        key = str(user_id)
        if key not in self.state["players"]:
            self.state["players"][key] = blank_player()
        return self.state["players"][key]

    def _in_channel(self, interaction: discord.Interaction) -> bool:
        return interaction.channel_id == THREERAID_CHANNEL_ID

    def _busy(self, user_id: int) -> bool:
        if self.lobby and user_id in self.lobby.member_ids():
            return True
        if self.run and not self.run.finished and user_id in self.run.member_ids():
            return True
        return False

    # -------------------------------------------------------------- commands

    @app_commands.command(name="raid", description="Open the 3Raid lobby (party of 3).")
    async def raid(self, interaction: discord.Interaction):
        if not self._in_channel(interaction):
            await interaction.response.send_message(_channel_hint(), ephemeral=True)
            return
        if self.run and not self.run.finished:
            await interaction.response.send_message(
                f"A raid is already in progress at encounter "
                f"**{self.run.encounter_index}/10**. Keep fighting on the raid message.",
                ephemeral=True,
            )
            return
        if self.lobby and not self.lobby.cancelled:
            await interaction.response.send_message(
                content="A lobby is already open — claim a role on that message.",
                embed=self.lobby.embed(),
                ephemeral=True,
            )
            return

        lobby = Lobby(interaction.user.id)
        self.lobby = lobby
        await interaction.response.send_message(embed=lobby.embed(), view=LobbyView(self, lobby))
        lobby.message = await interaction.original_response()

    @app_commands.command(name="raidstatus", description="Your 3Raid weekly clear status.")
    async def raidstatus(self, interaction: discord.Interaction):
        if not self._in_channel(interaction):
            await interaction.response.send_message(_channel_hint(), ephemeral=True)
            return
        rec = self.record(interaction.user.id)
        wk = week_key()
        cleared = rec.get("last_clear_week") == wk
        total = len(rec.get("clears") or {})
        e = discord.Embed(title="🏰 3Raid Status", color=0x2C3E50)
        e.add_field(name="This week", value="✅ Cleared (points claimed)" if cleared else "⬜ Not cleared yet")
        e.add_field(name="Lifetime clears", value=str(total))
        if self.run and not self.run.finished and interaction.user.id in self.run.member_ids():
            e.add_field(name="Active run", value=f"Encounter {self.run.encounter_index}/10", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def raidreset(self, interaction: discord.Interaction,
                        member: Optional[discord.Member] = None,
                        end_run: bool = False):
        store = self.bot.get_cog("Store")
        if not store or not store.is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        notes = []
        if member:
            rec = self.record(member.id)
            rec["last_clear_week"] = None
            self.save()
            notes.append(f"Cleared weekly lockout for {member.display_name}.")
        if end_run:
            self.run = None
            self.lobby = None
            notes.append("Ended the active raid/lobby.")
        if not notes:
            notes.append("Nothing to do — pass member and/or end_run.")
        await interaction.response.send_message(" ".join(notes), ephemeral=True)

    # -------------------------------------------------------------- lobby ops

    async def claim_role(self, interaction: discord.Interaction, lobby: Lobby, role: str):
        if self.lobby is not lobby or lobby.cancelled:
            await interaction.response.send_message("That lobby is closed.", ephemeral=True)
            return
        if self.run and not self.run.finished:
            await interaction.response.send_message("A raid is already running.", ephemeral=True)
            return
        uid = interaction.user.id
        if lobby.roles[role] == uid:
            await interaction.response.send_message("You already have that role.", ephemeral=True)
            return
        if lobby.roles[role] is not None:
            await interaction.response.send_message("That role is taken.", ephemeral=True)
            return
        # leave previous role if any
        prev = lobby.role_of(uid)
        if prev:
            lobby.roles[prev] = None
            lobby.drafts.pop(uid, None)
            lobby.confirmed.discard(uid)
        lobby.roles[role] = uid
        lobby.draft_for(uid)  # init
        lobby.confirmed.discard(uid)
        await interaction.response.edit_message(embed=lobby.embed(), view=LobbyView(self, lobby))

    async def leave_lobby(self, interaction: discord.Interaction, lobby: Lobby):
        if self.lobby is not lobby or lobby.cancelled:
            await interaction.response.send_message("That lobby is closed.", ephemeral=True)
            return
        uid = interaction.user.id
        role = lobby.role_of(uid)
        if not role:
            await interaction.response.send_message("You're not in this lobby.", ephemeral=True)
            return
        lobby.roles[role] = None
        lobby.drafts.pop(uid, None)
        lobby.confirmed.discard(uid)
        await interaction.response.edit_message(embed=lobby.embed(), view=LobbyView(self, lobby))

    async def cancel_lobby(self, interaction: discord.Interaction, lobby: Lobby):
        if self.lobby is not lobby:
            await interaction.response.send_message("That lobby is closed.", ephemeral=True)
            return
        if interaction.user.id != lobby.host_id:
            store = self.bot.get_cog("Store")
            if not store or not store.is_staff(interaction.user):
                await interaction.response.send_message("Only the host (or staff) can cancel.", ephemeral=True)
                return
        lobby.cancelled = True
        self.lobby = None
        await interaction.response.edit_message(
            embed=discord.Embed(title="🏰 3Raid Lobby", description="Lobby cancelled.", color=0x7F8C8D),
            view=None,
        )

    async def lobby_timeout(self, lobby: Lobby):
        if self.lobby is lobby:
            lobby.cancelled = True
            self.lobby = None
            if lobby.message:
                try:
                    await lobby.message.edit(
                        embed=discord.Embed(title="🏰 3Raid Lobby", description="Lobby timed out.", color=0x7F8C8D),
                        view=None,
                    )
                except Exception:
                    pass

    def _draft_embed(self, lobby: Lobby, user_id: int) -> discord.Embed:
        role = lobby.role_of(user_id)
        meta = ROLE_META[role]
        draft = lobby.draft_for(user_id)
        spent = sum(draft.values())
        lines = []
        for sid in spells_for(role):
            spell = SPELLS[sid]
            rank = draft.get(sid, 0)
            prim = "★" if spell.get("primary") else " "
            lines.append(f"`{prim}` {spell['emoji']} **{spell['label']}** r{rank}/{SKILL_MAX} — {spell['desc']}")
        e = discord.Embed(
            title=f"📜 {meta['emoji']} {meta['label']} Draft",
            description=f"Points: **{spent}/{SKILL_BUDGET}** · max {SKILL_MAX}/spell · ★ = primary lane",
            color=0x1ABC9C,
        )
        e.add_field(name="Spells", value="\n".join(lines), inline=False)
        tip = lobby.hard_check(user_id)
        if tip and "Confirm" not in tip:
            e.set_footer(text=tip)
        elif user_id in lobby.confirmed:
            e.set_footer(text="Draft confirmed ✅")
        else:
            e.set_footer(text="Confirm when ready")
        return e

    async def open_draft(self, interaction: discord.Interaction, lobby: Lobby):
        if self.lobby is not lobby or lobby.cancelled:
            await interaction.response.send_message("That lobby is closed.", ephemeral=True)
            return
        if lobby.role_of(interaction.user.id) is None:
            await interaction.response.send_message("Claim a role first.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=self._draft_embed(lobby, interaction.user.id),
            view=DraftView(self, lobby, interaction.user.id),
            ephemeral=True,
        )

    async def adjust_draft(self, interaction: discord.Interaction, lobby: Lobby,
                           spell_id: str, delta: int):
        if self.lobby is not lobby or lobby.cancelled:
            await interaction.response.send_message("That lobby is closed.", ephemeral=True)
            return
        uid = interaction.user.id
        role = lobby.role_of(uid)
        if not role or SPELLS[spell_id]["role"] != role:
            await interaction.response.send_message("Not your spell.", ephemeral=True)
            return
        draft = lobby.draft_for(uid)
        lobby.confirmed.discard(uid)
        new = draft.get(spell_id, 0) + delta
        if new < 0 or new > SKILL_MAX:
            await interaction.response.send_message(
                f"Rank must stay between 0 and {SKILL_MAX}.", ephemeral=True
            )
            return
        if delta > 0 and sum(draft.values()) >= SKILL_BUDGET:
            await interaction.response.send_message("No points left — remove some first.", ephemeral=True)
            return
        draft[spell_id] = new
        if lobby.message:
            try:
                await lobby.message.edit(embed=lobby.embed(), view=LobbyView(self, lobby))
            except Exception:
                pass
        await interaction.response.edit_message(
            embed=self._draft_embed(lobby, uid),
            view=DraftView(self, lobby, uid),
        )

    async def confirm_draft(self, interaction: discord.Interaction, lobby: Lobby):
        if self.lobby is not lobby or lobby.cancelled:
            await interaction.response.send_message("That lobby is closed.", ephemeral=True)
            return
        uid = interaction.user.id
        if lobby.role_of(uid) is None:
            await interaction.response.send_message("Claim a role first.", ephemeral=True)
            return
        spent = lobby.points_spent(uid)
        if spent != SKILL_BUDGET:
            await interaction.response.send_message(
                f"Spend all {SKILL_BUDGET} points first (you've spent {spent}).", ephemeral=True
            )
            return
        if lobby.primary_spent(uid) < 1:
            await interaction.response.send_message(
                "Put at least 1 point into a primary-lane (★) spell.", ephemeral=True
            )
            return
        # soft-check role requirements so they see issues early
        lobby.confirmed.add(uid)
        err = lobby.hard_check(uid)
        if err and "Confirm" not in err:
            lobby.confirmed.discard(uid)
            await interaction.response.send_message(err, ephemeral=True)
            return
        if lobby.message:
            try:
                await lobby.message.edit(embed=lobby.embed(), view=LobbyView(self, lobby))
            except Exception:
                pass
        await interaction.response.edit_message(
            embed=self._draft_embed(lobby, uid),
            view=None,
        )
        await interaction.followup.send("Draft confirmed ✅", ephemeral=True)

    async def start_raid(self, interaction: discord.Interaction, lobby: Lobby):
        if self.lobby is not lobby or lobby.cancelled:
            await interaction.response.send_message("That lobby is closed.", ephemeral=True)
            return
        err = lobby.can_start()
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        run = RaidRun(lobby)
        self.run = run
        self.lobby = None
        lobby.cancelled = True
        await interaction.response.defer()
        await interaction.edit_original_response(
            embed=discord.Embed(
                title="🏰 3Raid Starting",
                description="Roles locked. Power reset. Entering encounter 1/10…",
                color=0xC0392B,
            ),
            view=None,
        )
        await self._begin_encounter(interaction.channel, run)

    # -------------------------------------------------------------- combat

    async def _monster_file(self, encounter: dict) -> Optional[discord.File]:
        path = ASSETS_DIR / encounter["image"]
        if not path.exists():
            return None
        return discord.File(path, filename="monster.png")

    async def _begin_encounter(self, channel: discord.abc.Messageable, run: RaidRun):
        enc = ENCOUNTERS[run.encounter_index - 1]
        fighters = run.make_fighters()
        fight = RaidFight(enc, fighters)
        run.fight = fight
        fight.begin_round()
        file = await self._monster_file(enc)
        kwargs = {
            "embed": fight.embed(run.encounter_index),
            "view": PublicFightView(self, run),
            "content": " ".join(f"<@{uid}>" for uid in run.member_ids()),
        }
        if file:
            kwargs["file"] = file
        msg = await channel.send(**kwargs)
        fight.message = msg
        run.message = msg

    async def open_act(self, interaction: discord.Interaction, run: RaidRun):
        if self.run is not run or run.finished or not run.fight or run.fight.finished:
            await interaction.response.send_message("That fight is over.", ephemeral=True)
            return
        fight = run.fight
        uid = interaction.user.id
        if uid not in fight.fighters:
            await interaction.response.send_message("You're not in this raid.", ephemeral=True)
            return
        if uid in fight.pending:
            await interaction.response.send_message("You already acted this round.", ephemeral=True)
            return
        fighter = fight.fighters[uid]
        if not fighter.alive:
            await interaction.response.send_message("You're down.", ephemeral=True)
            return
        await interaction.response.send_message(
            content=f"Round {fight.round} — pick a spell (⚡ {fighter.ap}/{fighter.ap_max})",
            view=FightView(self, run, uid),
            ephemeral=True,
        )

    async def pick_spell(self, interaction: discord.Interaction, run: RaidRun, spell_id: str):
        if self.run is not run or not run.fight or run.fight.finished:
            await interaction.response.send_message("That fight is over.", ephemeral=True)
            return
        fight = run.fight
        uid = interaction.user.id
        fighter = fight.fighters.get(uid)
        if not fighter or not fighter.alive:
            await interaction.response.send_message("Can't act.", ephemeral=True)
            return
        if uid in fight.pending:
            await interaction.response.send_message("You already acted this round.", ephemeral=True)
            return
        spell = SPELLS[spell_id]
        if fighter.ranks.get(spell_id, 0) < 1:
            await interaction.response.send_message("That spell isn't drafted.", ephemeral=True)
            return
        if fighter.ap < spell["ap"]:
            await interaction.response.send_message("Not enough AP.", ephemeral=True)
            return
        if spell_id == "sanctuary" and fighter.sanctuary_used:
            await interaction.response.send_message("Sanctuary already used this fight.", ephemeral=True)
            return

        needs_target = spell["kind"] in ("heal", "ally_shield", "cover", "panic")
        if needs_target:
            opts = []
            for f in fight.fighters.values():
                if not f.alive and spell["kind"] != "heal":
                    continue
                if not f.alive:
                    continue
                meta = ROLE_META[f.role]
                opts.append(discord.SelectOption(
                    label=f"{meta['label']}",
                    value=str(f.user_id),
                    emoji=meta["emoji"],
                    description=f"{f.hp}/{f.hp_max} HP",
                ))
            if not opts:
                await interaction.response.send_message("No valid targets.", ephemeral=True)
                return
            view = discord.ui.View(timeout=60)
            view.run = run  # type: ignore[attr-defined]
            view.cog = self  # type: ignore[attr-defined]

            class _TSelect(discord.ui.Select):
                def __init__(self):
                    super().__init__(placeholder="Choose target…", options=opts)

                async def callback(self, inner: discord.Interaction):
                    await self.view.cog.submit_action(inner, self.view.run, spell_id, int(self.values[0]))

            view.add_item(_TSelect())
            await interaction.response.send_message(
                f"Choose a target for **{spell['label']}**:", view=view, ephemeral=True
            )
            return

        await self.submit_action(interaction, run, spell_id, None)

    async def submit_action(self, interaction: discord.Interaction, run: RaidRun,
                            spell_id: str, target_id: Optional[int]):
        if self.run is not run or not run.fight or run.fight.finished:
            await interaction.response.send_message("That fight is over.", ephemeral=True)
            return
        fight = run.fight
        uid = interaction.user.id
        fighter = fight.fighters.get(uid)
        if not fighter or not fighter.alive:
            await interaction.response.send_message("Can't act.", ephemeral=True)
            return
        if uid in fight.pending:
            await interaction.response.send_message("You already acted this round.", ephemeral=True)
            return

        if spell_id == "rest":
            fight.pending[uid] = {"spell": "rest", "target": None}
        else:
            spell = SPELLS[spell_id]
            if fighter.ranks.get(spell_id, 0) < 1:
                await interaction.response.send_message("That spell isn't drafted.", ephemeral=True)
                return
            if fighter.ap < spell["ap"]:
                await interaction.response.send_message("Not enough AP.", ephemeral=True)
                return
            fight.pending[uid] = {"spell": spell_id, "target": target_id}

        label = "Rest" if spell_id == "rest" else SPELLS[spell_id]["label"]
        try:
            await interaction.response.edit_message(content=f"Locked in: **{label}**", view=None)
        except Exception:
            await interaction.response.send_message(f"Locked in: **{label}**", ephemeral=True)

        if fight.message:
            try:
                await fight.message.edit(
                    embed=fight.embed(run.encounter_index),
                    view=PublicFightView(self, run),
                    attachments=fight.message.attachments,
                )
            except Exception:
                try:
                    file = await self._monster_file(fight.encounter)
                    kwargs = {"embed": fight.embed(run.encounter_index), "view": PublicFightView(self, run)}
                    if file:
                        kwargs["attachments"] = [file]
                    await fight.message.edit(**kwargs)
                except Exception:
                    log.exception("Could not refresh raid fight message")

        if fight.all_acted():
            async with self._resolve_lock:
                if fight.finished or not fight.all_acted():
                    return
                await self._resolve_round(run)

    async def _resolve_round(self, run: RaidRun):
        fight = run.fight
        if not fight or fight.finished:
            return
        # Snapshot pending then clear so a double-trigger can't re-resolve.
        pending = dict(fight.pending)
        fight.pending.clear()

        # Resolve in Attacker / Specialty / Tank role order (ROLE_ORDER).
        order = []
        for role in ROLE_ORDER:
            uid = run.roles[role]
            if uid in pending:
                order.append(uid)

        lines: list[str] = []
        for uid in order:
            action = pending[uid]
            fighter = fight.fighters[uid]
            if not fighter.alive:
                continue
            spell_id = action["spell"]
            target_id = action.get("target")
            if spell_id == "rest":
                fighter.ap = fighter.ap_max
                lines.append(f"😮‍💨 <@{uid}> rests — AP refilled.")
                continue
            spell = SPELLS[spell_id]
            rank = fighter.ranks.get(spell_id, 1)
            fighter.ap -= spell["ap"]
            line = await self._apply_spell(fight, fighter, spell_id, rank, target_id)
            lines.append(line)
            if fight.m_hp <= 0:
                break

        fight.log.extend(lines)

        if fight.m_hp <= 0:
            await self._on_encounter_win(run)
            return

        # Monster action
        mlines = self._monster_act(fight)
        fight.log.extend(mlines)

        if not fight.living():
            await self._on_encounter_loss(run)
            return

        # AP regen for living
        for f in fight.living():
            f.ap = min(f.ap_max, f.ap + AP_REGEN)

        fight.begin_round()
        await self._refresh_fight_message(run)

    async def _apply_spell(self, fight: RaidFight, fighter: Fighter, spell_id: str,
                           rank: int, target_id: Optional[int]) -> str:
        spell = SPELLS[spell_id]
        enc = fight.encounter
        rscale = 1.0 + 0.35 * (rank - 1)  # r1=1.0, r2=1.35

        if spell["kind"] == "damage":
            return self._apply_damage_spell(fight, fighter, spell_id, rank, rscale)

        if spell["kind"] == "heal":
            target = fight.fighters.get(target_id) if target_id else None
            if not target or not target.alive:
                return f"{spell['emoji']} <@{fighter.user_id}>'s {spell['label']} finds no target."
            heal = int(round((28 + 10 * rank) * rscale))
            target.hp = min(target.hp_max, target.hp + heal)
            return f"💚 <@{fighter.user_id}> Mends <@{target.user_id}> for **{heal}**."

        if spell["kind"] == "heal_aoe":
            heal = int(round((12 + 6 * rank) * rscale))
            for f in fight.living():
                f.hp = min(f.hp_max, f.hp + heal)
            return f"🌿 <@{fighter.user_id}> Circle of Relief heals the party for **{heal}** each."

        if spell["kind"] == "ally_shield":
            target = fight.fighters.get(target_id) if target_id else None
            if not target or not target.alive:
                return f"{spell['emoji']} <@{fighter.user_id}>'s Aegis finds no target."
            amt = int(round((35 + 15 * rank) * rscale))
            target.shield += amt
            return f"🔵 <@{fighter.user_id}> Aegis on <@{target.user_id}> (**{amt}** absorb)."

        if spell["kind"] == "break":
            if fight.m_shield <= 0:
                fight.exposed = True
                return f"🔓 <@{fighter.user_id}> Ward Break — no ward up, but the foe is **Exposed**."
            broken = fight.m_shield
            fight.m_shield = 0
            fight.exposed = True
            return f"🔓 <@{fighter.user_id}> Ward Break shatters the ward (**{broken}**) — foe **Exposed**!"

        if spell["kind"] == "panic":
            target = fight.fighters.get(target_id) if target_id else None
            if not target or not target.alive:
                return f"{spell['emoji']} <@{fighter.user_id}>'s Sanctuary finds no target."
            fighter.sanctuary_used = True
            heal = int(round((45 + 15 * rank) * rscale))
            sh = int(round((40 + 15 * rank) * rscale))
            target.hp = min(target.hp_max, target.hp + heal)
            target.shield += sh
            return (f"🕊️ <@{fighter.user_id}> Sanctuary on <@{target.user_id}> — "
                    f"heal **{heal}**, shield **{sh}**.")

        if spell["kind"] == "taunt":
            fight.taunt_target = fighter.user_id
            return f"🫵 <@{fighter.user_id}> Provokes **{enc['name']}**."

        if spell["kind"] == "self_mit":
            fighter.mit_mult = 0.45 if rank >= 2 else 0.55
            return f"🛡️ <@{fighter.user_id}> Bulwark — heavy mitigation this round."

        if spell["kind"] == "cover":
            target = fight.fighters.get(target_id) if target_id else None
            if not target or not target.alive:
                return f"{spell['emoji']} <@{fighter.user_id}>'s Guard finds no target."
            fighter.covering = target.user_id
            return f"🤝 <@{fighter.user_id}> Guards <@{target.user_id}>."

        if spell["kind"] == "expose":
            base = fighter.atk * (0.45 * rscale)
            dmg = mitigate(base, fight.m_def)
            if fight.m_shield > 0:
                absorbed = min(fight.m_shield, dmg)
                fight.m_shield -= absorbed
                dmg -= absorbed
            fight.m_hp -= dmg
            fight.exposed = True
            return f"🔨 <@{fighter.user_id}> Shatter Bash for **{dmg}** — foe **Exposed**!"

        return f"<@{fighter.user_id}> casts {spell['label']}."

    def _apply_damage_spell(self, fight: RaidFight, fighter: Fighter,
                            spell_id: str, rank: int, rscale: float) -> str:
        spell = SPELLS[spell_id]
        enc = fight.encounter
        traits = fight.traits
        shield_el = traits.get("shield_element")

        if spell_id == "quick_hex":
            base = fighter.atk * (0.35 * rscale)
        elif spell_id == "fracture":
            base = fighter.atk * (0.95 * rscale)
            if fight.exposed or fight.m_shield <= 0:
                base *= 1.85
            elif fight.m_shield > 0:
                base *= 0.55
        elif spell_id == "arcane_lance":
            base = fighter.atk * (1.55 * rscale)
            if fight.m_shield > 0:
                base *= 0.4
        elif spell_id == "coup":
            pct = fight.m_hp / fight.m_hp_max if fight.m_hp_max else 1
            if pct > 0.25:
                return (f"☠️ <@{fighter.user_id}> Coup de Grâce fizzles "
                        f"(foe still above 25% HP).")
            base = fighter.atk * (2.2 * rscale)
        else:
            # elemental pokes
            base = fighter.atk * (1.05 * rscale)
            el = spell.get("element")
            if fight.m_shield > 0 and shield_el:
                if (el == "ice" and shield_el == "fire") or (el == "fire" and shield_el == "ice"):
                    base *= 1.4
                elif el == "storm" and traits.get("armored"):
                    base *= 1.25
            elif el == "storm" and traits.get("armored"):
                base *= 1.25

        dmg = mitigate(base, fight.m_def)
        note = ""
        if fight.m_shield > 0:
            absorbed = min(fight.m_shield, dmg)
            fight.m_shield -= absorbed
            dmg -= absorbed
            note = f" (ward ate {absorbed})"
            if fight.m_shield <= 0:
                fight.exposed = True
                note += ", ward broken!"
        fight.m_hp -= dmg
        return (f"{spell['emoji']} <@{fighter.user_id}> **{spell['label']}** "
                f"deals **{dmg}**{note}.")

    def _monster_act(self, fight: RaidFight) -> list[str]:
        lines = []
        living = fight.living()
        if not living:
            return lines
        enc = fight.encounter

        # Who gets hit
        if fight.telegraph_cleave:
            targets = list(living)
            lines.append(f"{enc['emoji']} **{enc['name']}** cleaves the party!")
        else:
            if fight.taunt_target and fight.fighters.get(fight.taunt_target) and fight.fighters[fight.taunt_target].alive:
                targets = [fight.fighters[fight.taunt_target]]
            else:
                # focus lowest HP% non-tank if possible, else lowest
                targets = [min(living, key=lambda f: f.hp / max(1, f.hp_max))]
            lines.append(f"{enc['emoji']} **{enc['name']}** strikes!")

        for target in targets:
            raw = fight.m_atk * (0.85 if len(targets) > 1 else 1.0)
            # Guard: if someone is covering this target, cut damage
            for f in living:
                if f.covering == target.user_id:
                    raw *= 0.5
                    lines.append(f"🤝 <@{f.user_id}>'s Guard softens the blow.")
                    break
            raw *= target.mit_mult
            dmg = mitigate(raw, target.defense)
            if target.shield > 0:
                absorbed = min(target.shield, dmg)
                target.shield -= absorbed
                dmg -= absorbed
                if absorbed:
                    lines.append(f"🔵 Aegis absorbs **{absorbed}** for <@{target.user_id}>.")
            target.hp -= dmg
            lines.append(f"→ <@{target.user_id}> takes **{dmg}**.")
            if target.hp <= 0:
                target.hp = 0
                target.alive = False
                target.shield = 0
                lines.append(f"💀 <@{target.user_id}> falls!")

        return lines

    async def _refresh_fight_message(self, run: RaidRun):
        fight = run.fight
        if not fight or not fight.message:
            return
        file = await self._monster_file(fight.encounter)
        try:
            kwargs = {"embed": fight.embed(run.encounter_index), "view": PublicFightView(self, run)}
            if file:
                kwargs["attachments"] = [file]
            await fight.message.edit(**kwargs)
        except Exception:
            log.exception("Could not refresh raid fight message")

    async def _on_encounter_win(self, run: RaidRun):
        fight = run.fight
        fight.finished = True
        fight.won = True
        fight.m_hp = 0
        enc = fight.encounter
        file = await self._monster_file(enc)
        embed = discord.Embed(
            title=f"✅ Encounter {run.encounter_index}/10 cleared — {enc['name']}",
            description="\n".join(fight.log[-6:]) or "The foe falls.",
            color=0x27AE60,
        )
        try:
            kwargs = {"embed": embed, "view": None, "content": None}
            if file:
                kwargs["attachments"] = [file]
            if fight.message:
                await fight.message.edit(**kwargs)
        except Exception:
            log.exception("Could not edit win message")

        if run.encounter_index >= 10:
            await self._on_raid_clear(run)
            return

        run.encounter_index += 1
        channel = fight.message.channel if fight.message else None
        if channel is None:
            return
        await channel.send(
            f"Encounter {run.encounter_index - 1} down. Next: "
            f"**{ENCOUNTERS[run.encounter_index - 1]['name']}**…"
        )
        await self._begin_encounter(channel, run)

    async def _on_encounter_loss(self, run: RaidRun):
        fight = run.fight
        fight.finished = True
        fight.won = False
        enc = fight.encounter
        embed = discord.Embed(
            title=f"💀 Wiped on Encounter {run.encounter_index}/10 — {enc['name']}",
            description=(
                "The party falls. Use `/raid` to open a new lobby and try again.\n"
                "(Weekly clear points are only awarded on a full 10/10 clear.)"
            ),
            color=0x922B21,
        )
        if fight.log:
            embed.add_field(name="Last beats", value="\n".join(fight.log[-4:]), inline=False)
        try:
            if fight.message:
                await fight.message.edit(embed=embed, view=None)
        except Exception:
            pass
        # Post a retry prompt on the same encounter via new lobby only —
        # wipe ends the run so drafts can be reconsidered.
        channel = fight.message.channel if fight.message else None
        self.run = None
        if channel:
            await channel.send("Raid ended in a wipe. `/raid` when you're ready to reform.")

    async def _on_raid_clear(self, run: RaidRun):
        run.finished = True
        run.cleared = True
        wk = week_key()
        channel = run.fight.message.channel if run.fight and run.fight.message else None
        store = self.bot.get_cog("Store")
        lines = ["**The Hollow Triad falls. Raid clear!**", ""]
        for uid in run.member_ids():
            rec = self.record(uid)
            already = rec.get("last_clear_week") == wk
            member = None
            if channel and getattr(channel, "guild", None):
                member = channel.guild.get_member(uid)
            pts_line = ""
            if already:
                pts_line = "⬜ already claimed points this week"
            else:
                house = store.member_house(member) if store and member else None
                if store and house and member:
                    store.record(
                        house=house,
                        delta=POINTS_ON_CLEAR,
                        actor_id=self.bot.user.id if self.bot.user else 0,
                        target_id=uid,
                        reason="3Raid: weekly clear",
                    )
                    pts_line = f"✅ +{POINTS_ON_CLEAR} house points"
                else:
                    pts_line = "⚠️ no house role — no points awarded"
                rec["last_clear_week"] = wk
                clears = rec.setdefault("clears", {})
                clears[wk] = clears.get(wk, 0) + 1
            lines.append(f"<@{uid}> — {pts_line}")
        self.save()
        self.run = None
        if channel:
            await channel.send(embed=discord.Embed(
                title="🏰 3Raid Cleared",
                description="\n".join(lines),
                color=0xF1C40F,
            ))


async def setup(bot: commands.Bot):
    await bot.add_cog(ThreeRaid(bot))
