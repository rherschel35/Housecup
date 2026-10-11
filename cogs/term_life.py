"""
Term Life — anytime school-day RPG sim (v1).

    /life play     — open your day board (buttons)
    /life status   — sheet only

No daily play limit. Actions spend Energy; Sleep restores it and advances
the clock. No house points in v1 — skills, bonds, and journal only.

Playable in study hall, open lounges, and the shared games lounge.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.velmora_channels import channel_mentions, with_study_hall_and_duels_potions

log = logging.getLogger("velmora.termlife")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "term_life_state.json"

TERM_LIFE_CHANNEL_IDS = with_study_hall_and_duels_potions()

INK = 0x3D5A4C
ENERGY_MAX = 6
ENERGY_REGEN_SEC = 20 * 60  # 1 energy / 20 real minutes while below max
SKILL_CAP = 50
BOND_CAP = 20
JOURNAL_KEEP = 8

PERIODS = ("morning", "afternoon", "evening", "night")
PERIOD_LABEL = {
    "morning": "Morning",
    "afternoon": "Afternoon",
    "evening": "Evening",
    "night": "Night",
}

PLACES = {
    "common_room": {
        "name": "Common Room",
        "emoji": "🔥",
        "blurb": "Sofas, low talk, someone's kettle always singing.",
        "study": False,
        "npc": None,
    },
    "library": {
        "name": "Library",
        "emoji": "📚",
        "blurb": "Dust, lamp-glow, and the soft terror of overdue books.",
        "study": True,
        "npc": "marlow",
    },
    "courtyard": {
        "name": "Courtyard",
        "emoji": "🌿",
        "blurb": "Stone warm from the sun, or slick from last night's rain.",
        "study": False,
        "npc": "marlow",
    },
    "kitchens": {
        "name": "Kitchens",
        "emoji": "🍞",
        "blurb": "Butter, copper, and elves who already know your order.",
        "study": False,
        "npc": "nix",
    },
    "woods_edge": {
        "name": "Woods Edge",
        "emoji": "🌲",
        "blurb": "The trees begin. You don't have to go further — yet.",
        "study": False,
        "npc": None,
    },
}

SKILLS = {
    "wit": {"name": "Wit", "emoji": "✨"},
    "nerve": {"name": "Nerve", "emoji": "🗡️"},
    "kindness": {"name": "Kindness", "emoji": "🕊️"},
}

NPCS = {
    "marlow": {
        "name": "Marlow",
        "title": "a sharp-eyed prefect",
        "places": ("library", "courtyard"),
    },
    "nix": {
        "name": "Nix",
        "title": "a kitchen elf with opinions",
        "places": ("kitchens",),
    },
}

STUDY_LINES = [
    "You copy a charm diagram until your wrist complains. It sticks anyway.",
    "A footnote argues with another footnote. You take sides.",
    "You whisper an incantation under your breath. The page warms.",
    "You lose twenty minutes to a margin doodle that accidentally helps.",
]

TALK_LINES = {
    "marlow": [
        "Marlow quizzes you on corridor rules, then pretends it wasn't a test.",
        "Marlow admits the prefect badge is heavier than it looks.",
        "Marlow shares a rumor, then makes you swear you didn't hear it here.",
    ],
    "nix": [
        "Nix slides you a bun 'for research purposes.' The research is hunger.",
        "Nix corrects your stirring advice with devastating politeness.",
        "Nix tells you which professor steals biscuits. You did not ask.",
    ],
}

WANDER_LINES = {
    "common_room": [
        "Someone's practicing levitation on a teacup. It goes… mostly well.",
        "You claim a sofa arm like territory. Nobody contests it.",
    ],
    "library": [
        "A book snaps shut on its own. You pretend that is normal.",
        "The silence here has weight. You borrow some of it.",
    ],
    "courtyard": [
        "A paper airplane circles once and surrenders to the fountain.",
        "You stretch. The sky does not comment.",
    ],
    "kitchens": [
        "A pan lids itself smugly. Nix pretends not to notice.",
        "The air smells like victory and cinnamon.",
    ],
    "woods_edge": [
        "Something large moves between trunks. It does not come closer.",
        "You count heartbeats. The woods count something else.",
    ],
}

EVENT_CHANCE = 0.22
EVENTS = [
    ("A lost first-year asks for the Great Hall. You point. Kindness hums.", "kindness", 1),
    ("You catch a falling inkwell midair. Show-off. Wit nods.", "wit", 1),
    ("A cold gust tests your spine. You stay. Nerve ticks up.", "nerve", 1),
    ("You find a chocolate frog card under a bench — already traded to bits.", None, 0),
    ("Rain starts. You look dramatic about it for exactly four seconds.", None, 0),
]


def _blank_player() -> dict:
    return {
        "place": "common_room",
        "period": "morning",
        "day": 1,
        "energy": ENERGY_MAX,
        "energy_updated_at": time.time(),
        "skills": {k: 0 for k in SKILLS},
        "bonds": {k: 0 for k in NPCS},
        "journal": [],
        "actions": 0,
    }


def _channel_hint() -> str:
    return f"Term Life runs in {channel_mentions(TERM_LIFE_CHANNEL_IDS)}."


class TermLife(commands.Cog):
    """Anytime school-day sim — skills, bonds, energy, no play cap."""

    life = app_commands.Group(
        name="life",
        description="Term Life — anytime school-day sim (no daily limit).",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.rng = random.Random()

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"players": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Term Life state unreadable — starting empty.")
            return {"players": {}}
        data.setdefault("players", {})
        return data

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save Term Life state.")

    def _in_channel(self, interaction: discord.Interaction) -> bool:
        return interaction.channel_id in TERM_LIFE_CHANNEL_IDS

    async def _require_channel(self, interaction: discord.Interaction) -> bool:
        if self._in_channel(interaction):
            return True
        await interaction.response.send_message(_channel_hint(), ephemeral=True)
        return False

    def player(self, user_id: int) -> dict:
        key = str(user_id)
        players = self.state.setdefault("players", {})
        if key not in players:
            players[key] = _blank_player()
            self.save()
        p = players[key]
        # migrate / fill
        p.setdefault("place", "common_room")
        p.setdefault("period", "morning")
        p.setdefault("day", 1)
        p.setdefault("energy", ENERGY_MAX)
        p.setdefault("energy_updated_at", time.time())
        p.setdefault("skills", {k: 0 for k in SKILLS})
        for sk in SKILLS:
            p["skills"].setdefault(sk, 0)
        p.setdefault("bonds", {k: 0 for k in NPCS})
        for nk in NPCS:
            p["bonds"].setdefault(nk, 0)
        p.setdefault("journal", [])
        p.setdefault("actions", 0)
        self._regen_energy(p)
        return p

    def _regen_energy(self, p: dict) -> None:
        now = time.time()
        last = float(p.get("energy_updated_at") or now)
        energy = int(p.get("energy") or 0)
        if energy >= ENERGY_MAX:
            p["energy"] = ENERGY_MAX
            p["energy_updated_at"] = now
            return
        gained = int((now - last) // ENERGY_REGEN_SEC)
        if gained <= 0:
            return
        p["energy"] = min(ENERGY_MAX, energy + gained)
        # keep fractional regen progress
        p["energy_updated_at"] = last + gained * ENERGY_REGEN_SEC
        if p["energy"] >= ENERGY_MAX:
            p["energy_updated_at"] = now

    def _note(self, p: dict, line: str) -> None:
        journal = p.setdefault("journal", [])
        journal.append(line)
        p["journal"] = journal[-JOURNAL_KEEP:]

    def _bump_skill(self, p: dict, skill: str, amount: int = 1) -> str:
        if skill not in SKILLS or amount <= 0:
            return ""
        before = int(p["skills"].get(skill, 0))
        after = min(SKILL_CAP, before + amount)
        p["skills"][skill] = after
        if after == before:
            return f"{SKILLS[skill]['name']} is maxed."
        return f"+{after - before} {SKILLS[skill]['name']} ({after}/{SKILL_CAP})"

    def _bump_bond(self, p: dict, npc: str, amount: int = 1) -> str:
        if npc not in NPCS or amount <= 0:
            return ""
        before = int(p["bonds"].get(npc, 0))
        after = min(BOND_CAP, before + amount)
        p["bonds"][npc] = after
        if after == before:
            return f"Bond with {NPCS[npc]['name']} is maxed."
        return f"+{after - before} bond with {NPCS[npc]['name']} ({after}/{BOND_CAP})"

    def _advance_period(self, p: dict) -> None:
        idx = PERIODS.index(p.get("period") or "morning")
        if idx >= len(PERIODS) - 1:
            p["period"] = "morning"
            p["day"] = int(p.get("day") or 1) + 1
        else:
            p["period"] = PERIODS[idx + 1]

    def _spend_energy(self, p: dict, cost: int = 1) -> Optional[str]:
        self._regen_energy(p)
        if int(p["energy"]) < cost:
            return (
                "You're out of energy. **Sleep** to restore it "
                f"(or wait — it returns slowly, 1 per {ENERGY_REGEN_SEC // 60} min)."
            )
        p["energy"] = int(p["energy"]) - cost
        p["energy_updated_at"] = time.time()
        p["actions"] = int(p.get("actions") or 0) + 1
        return None

    def _maybe_event(self, p: dict) -> str:
        if self.rng.random() > EVENT_CHANCE:
            return ""
        text, skill, amt = self.rng.choice(EVENTS)
        bits = [text]
        if skill and amt:
            gain = self._bump_skill(p, skill, amt)
            if gain:
                bits.append(gain)
        return " ".join(bits)

    def embed_for(self, member: discord.abc.User, p: dict, headline: str = "") -> discord.Embed:
        place = PLACES[p["place"]]
        period = PERIOD_LABEL.get(p["period"], p["period"])
        skills = " · ".join(
            f"{SKILLS[k]['emoji']} {SKILLS[k]['name']} {p['skills'].get(k, 0)}"
            for k in SKILLS
        )
        bonds = " · ".join(
            f"{NPCS[k]['name']} {p['bonds'].get(k, 0)}"
            for k in NPCS
        )
        npc_key = place.get("npc")
        npc_line = ""
        if npc_key and npc_key in NPCS:
            npc_line = f"\n**Here:** {NPCS[npc_key]['name']}, {NPCS[npc_key]['title']}."

        desc_parts = []
        if headline:
            desc_parts.append(headline)
        desc_parts.append(
            f"{place['emoji']} **{place['name']}** — {place['blurb']}{npc_line}"
        )
        embed = discord.Embed(
            title=f"Term Life — Day {p['day']}, {period}",
            description="\n\n".join(desc_parts),
            color=INK,
        )
        embed.add_field(
            name="Energy",
            value=f"{'▮' * int(p['energy'])}{'▯' * (ENERGY_MAX - int(p['energy']))} "
                  f"**{p['energy']}/{ENERGY_MAX}**",
            inline=False,
        )
        embed.add_field(name="Skills", value=skills or "—", inline=False)
        embed.add_field(name="Bonds", value=bonds or "—", inline=False)
        journal = p.get("journal") or []
        if journal:
            embed.add_field(
                name="Recent",
                value="\n".join(f"• {line}" for line in journal[-4:]),
                inline=False,
            )
        embed.set_footer(
            text="Anytime · no daily limit · Sleep restores energy · v1 (no house points yet)"
        )
        embed.set_author(name=str(member.display_name))
        return embed

    def view_for(self, user_id: int) -> "LifeView":
        return LifeView(self, user_id)

    # ---- actions (pure logic) ----

    def act_study(self, p: dict) -> tuple[str, bool]:
        place = PLACES[p["place"]]
        if not place.get("study"):
            return "Studying works best in the **Library**. Go there first.", False
        err = self._spend_energy(p)
        if err:
            return err, False
        line = self.rng.choice(STUDY_LINES)
        gain = self._bump_skill(p, "wit", 1)
        self._advance_period(p)
        event = self._maybe_event(p)
        parts = [line, gain]
        if event:
            parts.append(event)
        summary = " ".join(x for x in parts if x)
        self._note(p, f"Studied — {gain}")
        self.save()
        return summary, True

    def act_talk(self, p: dict) -> tuple[str, bool]:
        place = PLACES[p["place"]]
        npc = place.get("npc")
        if not npc or npc not in NPCS:
            return "Nobody you know well is here. Try the Library, Courtyard, or Kitchens.", False
        err = self._spend_energy(p)
        if err:
            return err, False
        line = self.rng.choice(TALK_LINES[npc])
        bond = self._bump_bond(p, npc, 1)
        # talking also gently trains kindness
        kind = self._bump_skill(p, "kindness", 1)
        self._advance_period(p)
        event = self._maybe_event(p)
        parts = [line, bond, kind]
        if event:
            parts.append(event)
        summary = " ".join(x for x in parts if x)
        self._note(p, f"Talked with {NPCS[npc]['name']} — {bond}")
        self.save()
        return summary, True

    def act_go(self, p: dict, dest: str) -> tuple[str, bool]:
        if dest not in PLACES:
            return "That place isn't on your map.", False
        if dest == p["place"]:
            return f"You're already at the **{PLACES[dest]['name']}**.", False
        err = self._spend_energy(p)
        if err:
            return err, False
        p["place"] = dest
        place = PLACES[dest]
        flavor = self.rng.choice(WANDER_LINES[dest])
        # woods edge trains nerve a little when you arrive
        extras = []
        if dest == "woods_edge":
            extras.append(self._bump_skill(p, "nerve", 1))
        elif dest == "kitchens":
            extras.append(self._bump_skill(p, "kindness", 1))
        self._advance_period(p)
        event = self._maybe_event(p)
        parts = [
            f"You head to the **{place['name']}**.",
            flavor,
            *[x for x in extras if x],
        ]
        if event:
            parts.append(event)
        summary = " ".join(parts)
        self._note(p, f"Went to {place['name']}")
        self.save()
        return summary, True

    def act_sleep(self, p: dict) -> tuple[str, bool]:
        # Sleep always available; does not require energy
        self._regen_energy(p)
        p["energy"] = ENERGY_MAX
        p["energy_updated_at"] = time.time()
        # Jump to next morning
        if p.get("period") == "morning" and int(p.get("actions") or 0) == 0:
            # brand-new morning with no actions — still allow rest fluff
            line = "You doze in a patch of sun. Energy full. The day hasn't really started."
        else:
            p["period"] = "morning"
            p["day"] = int(p.get("day") or 1) + 1
            line = (
                f"You sleep. Dream-scraps of ink and trees. "
                f"**Day {p['day']}** begins. Energy restored."
            )
        self._note(p, f"Slept — Day {p['day']} morning")
        self.save()
        return line, True

    def act_wander(self, p: dict) -> tuple[str, bool]:
        """Spend energy in place without moving — light flavor + small skill."""
        err = self._spend_energy(p)
        if err:
            return err, False
        place_key = p["place"]
        flavor = self.rng.choice(WANDER_LINES[place_key])
        skill = "wit"
        if place_key == "woods_edge":
            skill = "nerve"
        elif place_key in ("kitchens", "common_room"):
            skill = "kindness"
        elif place_key == "courtyard":
            skill = "nerve"
        gain = self._bump_skill(p, skill, 1)
        self._advance_period(p)
        event = self._maybe_event(p)
        parts = [flavor, gain]
        if event:
            parts.append(event)
        summary = " ".join(x for x in parts if x)
        self._note(p, f"Lingered in {PLACES[place_key]['name']}")
        self.save()
        return summary, True

    # ---- commands ----

    @life.command(name="play", description="Open your Term Life day board (anytime).")
    async def life_play(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        p = self.player(interaction.user.id)
        self.save()
        await interaction.response.send_message(
            embed=self.embed_for(interaction.user, p, "Your term continues whenever you like."),
            view=self.view_for(interaction.user.id),
            ephemeral=True,
        )

    @life.command(name="status", description="Your Term Life sheet (day, energy, skills, bonds).")
    async def life_status(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        p = self.player(interaction.user.id)
        self.save()
        await interaction.response.send_message(
            embed=self.embed_for(interaction.user, p),
            ephemeral=True,
        )


class LifeView(discord.ui.View):
    def __init__(self, cog: TermLife, user_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "That's someone else's term.", ephemeral=True
            )
            return False
        if interaction.channel_id not in TERM_LIFE_CHANNEL_IDS:
            await interaction.response.send_message(_channel_hint(), ephemeral=True)
            return False
        return True

    async def _refresh(
        self, interaction: discord.Interaction, headline: str, p: dict
    ) -> None:
        await interaction.response.edit_message(
            embed=self.cog.embed_for(interaction.user, p, headline),
            view=self,
        )

    @discord.ui.button(label="Study", style=discord.ButtonStyle.primary, row=0)
    async def btn_study(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = self.cog.player(self.user_id)
        text, _ = self.cog.act_study(p)
        await self._refresh(interaction, text, p)

    @discord.ui.button(label="Talk", style=discord.ButtonStyle.primary, row=0)
    async def btn_talk(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = self.cog.player(self.user_id)
        text, _ = self.cog.act_talk(p)
        await self._refresh(interaction, text, p)

    @discord.ui.button(label="Linger", style=discord.ButtonStyle.secondary, row=0)
    async def btn_linger(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = self.cog.player(self.user_id)
        text, _ = self.cog.act_wander(p)
        await self._refresh(interaction, text, p)

    @discord.ui.button(label="Sleep", style=discord.ButtonStyle.success, row=0)
    async def btn_sleep(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = self.cog.player(self.user_id)
        text, _ = self.cog.act_sleep(p)
        await self._refresh(interaction, text, p)

    @discord.ui.select(
        placeholder="Go somewhere…",
        row=1,
        options=[
            discord.SelectOption(
                label=meta["name"],
                value=key,
                emoji=meta["emoji"],
                description=meta["blurb"][:100],
            )
            for key, meta in PLACES.items()
        ],
    )
    async def sel_go(self, interaction: discord.Interaction, select: discord.ui.Select):
        p = self.cog.player(self.user_id)
        text, _ = self.cog.act_go(p, select.values[0])
        await self._refresh(interaction, text, p)


async def setup(bot: commands.Bot):
    await bot.add_cog(TermLife(bot))
