"""
Odd Jobs — interactive solo contract RPG.

    /job board     — see today's offers; Accept one
    /job play      — continue your active contract (buttons)
    /job status    — reputation + current job
    /job abandon   — drop the active contract

Pick-up anytime. No daily play cap. House points only on a successful
finish (small), so spam doesn't print Cup points forever.

Trial channel only for now.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.velmora_channels import EVENT_BOARD_TEST_CHANNEL_ID, channel_mentions

log = logging.getLogger("velmora.contracts")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "contracts_state.json"

JOB_CHANNEL_IDS = frozenset({EVENT_BOARD_TEST_CHANNEL_ID})

INK = 0xC45C26
BOARD_SLOTS = 3
POINTS_ON_CLEAR = 3
REP_ON_CLEAR = 1

# Each contract is a short interactive path: beats with labeled choices.
# next = beat index, or "end_good" / "end_ok" / "end_bad"
CONTRACTS: dict[str, dict[str, Any]] = {
    "lost_toad": {
        "title": "The Prefect's Lost Toad",
        "client": "Marlow the prefect",
        "blurb": "A toad named Sir Hopsalot is missing. Marlow is one apology from tears.",
        "rep_need": 0,
        "beats": [
            {
                "text": (
                    "Marlow corners you in the corridor, badge crooked.\n\n"
                    "\"Sir Hopsalot was *just* in my pocket. He likes warm places and bad decisions. "
                    "Help me find him before someone turns him into a demonstration.\""
                ),
                "choices": [
                    {"label": "Check the Library", "next": 1},
                    {"label": "Check the Kitchens", "next": 2},
                    {"label": "Ask the portraits", "next": 3},
                ],
            },
            {
                "text": (
                    "The Library is hush and lamp-glow. A first-year is whispering to a shelf. "
                    "Something plump just hopped behind the Restricted Section rope."
                ),
                "choices": [
                    {"label": "Duck the rope", "next": 4},
                    {"label": "Bribe the first-year with a sweet", "next": 5},
                    {"label": "Wait and listen", "next": 6},
                ],
            },
            {
                "text": (
                    "The Kitchens smell like butter and conspiracy. An elf eyes you.\n\n"
                    "\"If a toad came through here,\" they say, \"it owed rent.\""
                ),
                "choices": [
                    {"label": "Offer to wash pans", "next": 7},
                    {"label": "Follow the butter trail", "next": 5},
                    {"label": "Accuse the elf (politely)", "next": 6},
                ],
            },
            {
                "text": (
                    "A portrait of a bored knight yawns.\n\n"
                    "\"Fat green thing. Went that way. Or the other way. "
                    "I'm art, not a bloodhound.\""
                ),
                "choices": [
                    {"label": "Flatter the knight", "next": 5},
                    {"label": "Threaten to rehang him lower", "next": 6},
                    {"label": "Head for the Library anyway", "next": 1},
                ],
            },
            {
                "text": (
                    "You slip the rope. A book snaps at your ankle. Sir Hopsalot sits on a forbidden tome "
                    "like a king on a throne, blinking slowly."
                ),
                "choices": [
                    {"label": "Scoop him gently", "next": "end_good"},
                    {"label": "Lure him with a beetle", "next": "end_ok"},
                    {"label": "Yell for Marlow", "next": "end_bad"},
                ],
            },
            {
                "text": (
                    "Your lead pays off. Sir Hopsalot is wedged in a boot someone abandoned by the stairs, "
                    "looking extremely pleased with himself."
                ),
                "choices": [
                    {"label": "Extract toad + boot", "next": "end_good"},
                    {"label": "Shake the boot (carefully)", "next": "end_ok"},
                ],
            },
            {
                "text": (
                    "Your approach goes sideways. Sir Hopsalot rockets past you into a crowd of students. "
                    "Chaos. Screaming. One very judgmental ghost."
                ),
                "choices": [
                    {"label": "Dive after him", "next": "end_ok"},
                    {"label": "Pretend you were never involved", "next": "end_bad"},
                ],
            },
            {
                "text": (
                    "You wash pans until your fingers prune. The elf softens and points under a copper vat. "
                    "Sir Hopsalot is asleep in a ladle."
                ),
                "choices": [
                    {"label": "Lift the ladle like treasure", "next": "end_good"},
                    {"label": "Wake him with a tap", "next": "end_ok"},
                ],
            },
        ],
        "endings": {
            "end_good": (
                "You return Sir Hopsalot. Marlow almost hugs you, remembers the badge, and salutes instead. "
                "**Contract cleared — clean.**"
            ),
            "end_ok": (
                "You return Sir Hopsalot after a brief public spectacle. Marlow is grateful and mortified. "
                "**Contract cleared — messy.**"
            ),
            "end_bad": (
                "Sir Hopsalot is eventually recovered by someone else. Marlow pays you in disappointed silence. "
                "**Contract failed.**"
            ),
        },
    },
    "howler_delivery": {
        "title": "Do Not Open the Howler",
        "client": "Anonymous (red envelope)",
        "blurb": "Deliver a howler to the Astronomy Tower. Do not open it. Definitely do not open it.",
        "rep_need": 0,
        "beats": [
            {
                "text": (
                    "A red envelope thrusts itself into your hands. It is warm. It is vibrating. "
                    "A note pinned to it says only: **ASTRONOMY TOWER. UNOPENED.**"
                ),
                "choices": [
                    {"label": "Go straight there", "next": 1},
                    {"label": "Take a shortcut through the courtyard", "next": 2},
                    {"label": "Peek just a little", "next": 3},
                ],
            },
            {
                "text": (
                    "Stairs. So many stairs. The howler starts humming a funeral march. "
                    "A prefect asks what's in the envelope."
                ),
                "choices": [
                    {"label": "\"Official business\"", "next": 4},
                    {"label": "Sprint past them", "next": 5},
                    {"label": "Hand it over (bad idea)", "next": "end_bad"},
                ],
            },
            {
                "text": (
                    "In the courtyard a gust snatches at the envelope. It flaps like an angry bird. "
                    "Students point. Someone shouts \"IT'S GOING TO BLOW!\""
                ),
                "choices": [
                    {"label": "Pin it under your cloak", "next": 4},
                    {"label": "Chase it across the lawn", "next": 5},
                    {"label": "Open it to \"calm it down\"", "next": 3},
                ],
            },
            {
                "text": (
                    "You open it.\n\n"
                    "The howler unfolds into a screaming aunt who insults your hair, your house, "
                    "and your choice of socks — for two full minutes. Everyone hears. Everyone."
                ),
                "choices": [
                    {"label": "Still try to deliver the remains", "next": "end_ok"},
                    {"label": "Burn the evidence", "next": "end_bad"},
                ],
            },
            {
                "text": (
                    "You reach the Astronomy Tower door. A tired professor holds out a hand without looking up. "
                    "The envelope throbs once, politely."
                ),
                "choices": [
                    {"label": "Deliver unopened", "next": "end_good"},
                    {"label": "Warn them it's loud", "next": "end_ok"},
                ],
            },
            {
                "text": (
                    "You arrive sweaty, cloak askew, envelope dented but intact. "
                    "The professor raises an eyebrow that could cut glass."
                ),
                "choices": [
                    {"label": "Present it with dignity you do not have", "next": "end_ok"},
                    {"label": "Apologize and flee", "next": "end_ok"},
                ],
            },
        ],
        "endings": {
            "end_good": (
                "Delivery complete. The tower door shuts. Distant screaming begins professionally. "
                "**Contract cleared — discreet.**"
            ),
            "end_ok": (
                "The howler arrives. So does your reputation as \"that student.\" "
                "**Contract cleared — loud.**"
            ),
            "end_bad": (
                "The job collapses into gossip and ash. Whoever sent it will write again. Louder. "
                "**Contract failed.**"
            ),
        },
    },
    "portrait_liar": {
        "title": "The Lying Portrait",
        "client": "The Castle (allegedly)",
        "blurb": "One portrait has been giving false directions. Find which frame is lying.",
        "rep_need": 1,
        "beats": [
            {
                "text": (
                    "Three portraits watch you from a landing:\n"
                    "**Lady Vellum** (smug), **Sir Croft** (sleepy), **The Urn** (an urn with eyes).\n\n"
                    "Yesterday six students ended up in a broom cupboard. Someone lied."
                ),
                "choices": [
                    {"label": "Interrogate Lady Vellum", "next": 1},
                    {"label": "Interrogate Sir Croft", "next": 2},
                    {"label": "Interrogate The Urn", "next": 3},
                ],
            },
            {
                "text": (
                    "Lady Vellum fans herself.\n\n"
                    "\"I only tell the truth, darling. Croft sleeps through crimes. "
                    "The Urn is… modern.\""
                ),
                "choices": [
                    {"label": "Believe her; accuse Croft", "next": 4},
                    {"label": "Ask where the cupboard is", "next": 5},
                    {"label": "Cross-check with The Urn", "next": 3},
                ],
            },
            {
                "text": (
                    "Sir Croft snores, then one eye opens.\n\n"
                    "\"Directions? I pointed them to the kitchens. "
                    "Or the dungeon. Maps are a social construct.\""
                ),
                "choices": [
                    {"label": "Accuse Croft", "next": 4},
                    {"label": "Ask Lady Vellum next", "next": 1},
                    {"label": "Watch who flinches", "next": 5},
                ],
            },
            {
                "text": (
                    "The Urn stares.\n\n"
                    "\"I CONTAIN TRUTH,\" it intones. "
                    "Then, quieter: \"Also dust. Lady Vellum rewrote the corridor charm. I saw.\""
                ),
                "choices": [
                    {"label": "Confront Lady Vellum with this", "next": 5},
                    {"label": "Trust the urn completely", "next": "end_good"},
                    {"label": "Assume the urn is dramatic", "next": 4},
                ],
            },
            {
                "text": (
                    "You accuse the wrong frame. The landing erupts into painted shouting. "
                    "A suit of armor facepalms so hard it dents."
                ),
                "choices": [
                    {"label": "Apologize and rethink", "next": 5},
                    {"label": "Double down", "next": "end_bad"},
                ],
            },
            {
                "text": (
                    "You ask for a demonstration. Lady Vellum points \"left to the Great Hall.\" "
                    "Left is absolutely a cupboard. She smiles with too many teeth."
                ),
                "choices": [
                    {"label": "Call her out publicly", "next": "end_good"},
                    {"label": "Bargain: stop lying for a week", "next": "end_ok"},
                ],
            },
        ],
        "endings": {
            "end_good": (
                "Lady Vellum is rehung facing a blank wall for a week. Students cheer. "
                "**Contract cleared — justice.**"
            ),
            "end_ok": (
                "She promises to lie only on Tuesdays. The castle accepts this as reform. "
                "**Contract cleared — compromise.**"
            ),
            "end_bad": (
                "You leave with three enemies in oil paint. The cupboard claims two more victims. "
                "**Contract failed.**"
            ),
        },
    },
    "beast_babysit": {
        "title": "Babysit the Baby Kneazle",
        "client": "Care of Magical Creatures",
        "blurb": "One hour. One kneazle kitten. Do not let it eat a student's homework. Or a student.",
        "rep_need": 0,
        "beats": [
            {
                "text": (
                    "A kneazle kitten is placed in your arms. It weighs nothing and judges everything.\n\n"
                    "\"Just keep it calm,\" says the handler, already leaving. \"It hates whistling.\""
                ),
                "choices": [
                    {"label": "Offer a treat", "next": 1},
                    {"label": "Set it down to explore", "next": 2},
                    {"label": "Whistle a calming tune", "next": 3},
                ],
            },
            {
                "text": (
                    "It accepts the treat like a treaty. Then it steals your quill and dares you to negotiate."
                ),
                "choices": [
                    {"label": "Trade a second treat for the quill", "next": 4},
                    {"label": "Play chase (on purpose)", "next": 5},
                ],
            },
            {
                "text": (
                    "It explores under a bench, finds a forgotten sandwich, and declares war on mayonnaise."
                ),
                "choices": [
                    {"label": "Clean it up carefully", "next": 4},
                    {"label": "Let it win the sandwich war", "next": 5},
                    {"label": "Panic-scoop it", "next": 3},
                ],
            },
            {
                "text": (
                    "You whistled. The kneazle's fur stands on end. It teleports onto a curtain rod "
                    "and screams in a tiny, offended voice."
                ),
                "choices": [
                    {"label": "Apologize + soft talking", "next": 4},
                    {"label": "Climb after it", "next": 5},
                    {"label": "Call for backup", "next": "end_bad"},
                ],
            },
            {
                "text": (
                    "Peace returns. The kneazle curls on your forearm and purrs like a tiny engine. "
                    "The handler is due back any minute."
                ),
                "choices": [
                    {"label": "Freeze. Do not move. Do not breathe wrong.", "next": "end_good"},
                    {"label": "Risk one proud smile", "next": "end_ok"},
                ],
            },
            {
                "text": (
                    "Chaos, but manageable. You are covered in fluff. The kneazle looks triumphant. "
                    "Footsteps approach."
                ),
                "choices": [
                    {"label": "Present a mostly calm kitten", "next": "end_ok"},
                    {"label": "Hide the evidence (fluff)", "next": "end_bad"},
                ],
            },
        ],
        "endings": {
            "end_good": (
                "The handler nods. \"You're hired for next week.\" The kneazle forgives you. Probably. "
                "**Contract cleared — professional.**"
            ),
            "end_ok": (
                "The kitten survives. Your dignity does not. Still counts. "
                "**Contract cleared — fluffy.**"
            ),
            "end_bad": (
                "The kneazle is fine. You are banned from whistling in this corridor. "
                "**Contract failed.**"
            ),
        },
    },
    "moonlit_errand": {
        "title": "Moonlit Errand",
        "client": "A note that arrived by owl at dusk",
        "blurb": "Carry a sealed vial to the lake shore before midnight. Don't shake it. Don't ask.",
        "rep_need": 2,
        "beats": [
            {
                "text": (
                    "The vial is cold and full of swirling silver. The note says: "
                    "**LAKE. BEFORE MIDNIGHT. NO QUESTIONS.** The moon is already up."
                ),
                "choices": [
                    {"label": "Take the main path", "next": 1},
                    {"label": "Cut through the woods edge", "next": 2},
                    {"label": "Ask a ghost the time", "next": 3},
                ],
            },
            {
                "text": (
                    "The path is clear until a pair of older students block it, bored and curious. "
                    "\"What's in the bottle, then?\""
                ),
                "choices": [
                    {"label": "\"Medicine. Contagious.\"", "next": 4},
                    {"label": "Walk through them", "next": 5},
                    {"label": "Run", "next": 5},
                ],
            },
            {
                "text": (
                    "Woods edge air is damp. Something large moves between trees. "
                    "The vial brightens, as if interested."
                ),
                "choices": [
                    {"label": "Hold still until it passes", "next": 4},
                    {"label": "Hurry — don't shake", "next": 5},
                    {"label": "Shake it once to \"check\"", "next": "end_bad"},
                ],
            },
            {
                "text": (
                    "A ghost informs you it is \"nearly midnight in the emotional sense.\" "
                    "Helpful. Ish. The lake isn't far."
                ),
                "choices": [
                    {"label": "Thank them and go", "next": 4},
                    {"label": "Ask for an escort", "next": 5},
                ],
            },
            {
                "text": (
                    "You reach the shore. A figure in a hood waits without turning around. "
                    "A hand extends."
                ),
                "choices": [
                    {"label": "Hand over the vial", "next": "end_good"},
                    {"label": "Demand a name first", "next": "end_ok"},
                ],
            },
            {
                "text": (
                    "You arrive late enough that the hooded figure is tapping a foot. "
                    "The vial is intact. Barely."
                ),
                "choices": [
                    {"label": "Deliver with an apology", "next": "end_ok"},
                    {"label": "Toss it gently (why)", "next": "end_bad"},
                ],
            },
        ],
        "endings": {
            "end_good": (
                "The vial leaves your hands. The lake goes very still. You are not thanked. You are *trusted*. "
                "**Contract cleared — quiet excellence.**"
            ),
            "end_ok": (
                "Delivery accepted with a sigh. You'll be asked again when they're desperate. "
                "**Contract cleared — good enough.**"
            ),
            "end_bad": (
                "The vial protests. Silver mist. You wake on the shore with damp socks and no answers. "
                "**Contract failed.**"
            ),
        },
    },
}


def _channel_hint() -> str:
    return f"Odd Jobs run in {channel_mentions(JOB_CHANNEL_IDS)}."


def _blank_player() -> dict:
    return {
        "rep": 0,
        "cleared": 0,
        "failed": 0,
        "active": None,
        "offers": [],
        "offers_at": 0.0,
        "history": [],
    }


class Contracts(commands.Cog):
    """Interactive solo contracts — accept a job, play the beats."""

    job = app_commands.Group(
        name="job",
        description="Odd Jobs — interactive solo contracts (anytime).",
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
            log.exception("Contracts state unreadable — starting empty.")
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
            log.exception("Could not save contracts state.")

    async def _require_channel(self, interaction: discord.Interaction) -> bool:
        if interaction.channel_id in JOB_CHANNEL_IDS:
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
        p.setdefault("rep", 0)
        p.setdefault("cleared", 0)
        p.setdefault("failed", 0)
        p.setdefault("active", None)
        p.setdefault("offers", [])
        p.setdefault("offers_at", 0.0)
        p.setdefault("history", [])
        return p

    def _eligible(self, rep: int) -> list[str]:
        return [cid for cid, c in CONTRACTS.items() if int(c.get("rep_need", 0)) <= rep]

    def refresh_offers(self, p: dict, force: bool = False) -> list[str]:
        now = time.time()
        # Keep offers for 30 minutes unless forced or empty
        if (
            not force
            and p.get("offers")
            and now - float(p.get("offers_at") or 0) < 30 * 60
        ):
            return list(p["offers"])
        pool = self._eligible(int(p.get("rep") or 0))
        if not pool:
            pool = list(CONTRACTS)
        k = min(BOARD_SLOTS, len(pool))
        offers = self.rng.sample(pool, k=k)
        p["offers"] = offers
        p["offers_at"] = now
        self.save()
        return offers

    def board_embed(self, member: discord.abc.User, p: dict) -> discord.Embed:
        offers = self.refresh_offers(p)
        lines = []
        for i, cid in enumerate(offers, 1):
            c = CONTRACTS[cid]
            need = int(c.get("rep_need") or 0)
            lock = "" if int(p.get("rep") or 0) >= need else f" 🔒 needs rep {need}"
            lines.append(
                f"**{i}. {c['title']}**{lock}\n"
                f"{c['client']} — {c['blurb']}"
            )
        active = p.get("active")
        footer = "Accept a job below · anytime · trial channel"
        if active:
            ac = CONTRACTS.get(active.get("id", ""), {})
            footer = f"Active: {ac.get('title', '?')} · use /job play · {footer}"
        embed = discord.Embed(
            title="Odd Jobs — Contract Board",
            description="\n\n".join(lines) or "No jobs posted.",
            color=INK,
        )
        embed.add_field(
            name="You",
            value=(
                f"**Rep** {p.get('rep', 0)} · "
                f"Cleared {p.get('cleared', 0)} · "
                f"Failed {p.get('failed', 0)}"
            ),
            inline=False,
        )
        embed.set_footer(text=footer)
        embed.set_author(name=str(member.display_name))
        return embed

    def play_embed(self, member: discord.abc.User, p: dict, note: str = "") -> discord.Embed:
        active = p.get("active")
        if not active:
            return discord.Embed(
                title="Odd Jobs",
                description="No active contract. Open `/job board` and Accept one.",
                color=INK,
            )
        cid = active["id"]
        c = CONTRACTS[cid]
        beat_i = int(active.get("beat") or 0)
        beats = c["beats"]
        if beat_i < 0 or beat_i >= len(beats):
            text = "This job is in a weird state — `/job abandon` and take another."
            choices = []
        else:
            beat = beats[beat_i]
            text = beat["text"]
            choices = beat.get("choices") or []
        desc = text
        if note:
            desc = f"{note}\n\n{desc}"
        embed = discord.Embed(
            title=f"Job: {c['title']}",
            description=desc,
            color=INK,
        )
        if choices:
            embed.add_field(
                name="What do you do?",
                value="\n".join(f"• {ch['label']}" for ch in choices),
                inline=False,
            )
        embed.set_footer(text=f"{c['client']} · Rep {p.get('rep', 0)}")
        embed.set_author(name=str(member.display_name))
        return embed

    def _award_points(self, member: discord.Member, reason: str) -> bool:
        store = self.bot.get_cog("Store")
        if not store:
            return False
        house = store.member_house(member)
        if not house:
            return False
        entry = store.record(
            house=house,
            delta=POINTS_ON_CLEAR,
            actor_id=member.id,
            target_id=member.id,
            reason=reason,
        )
        return entry is not None

    def apply_ending(self, member: discord.Member, p: dict, ending_key: str) -> str:
        active = p.get("active") or {}
        cid = active.get("id")
        c = CONTRACTS.get(cid or "", {})
        text = (c.get("endings") or {}).get(ending_key, "The job ends.")
        success = ending_key in ("end_good", "end_ok")
        p["active"] = None
        hist = p.setdefault("history", [])
        hist.append({"id": cid, "ending": ending_key, "at": time.time()})
        p["history"] = hist[-20:]
        if success:
            p["cleared"] = int(p.get("cleared") or 0) + 1
            p["rep"] = int(p.get("rep") or 0) + REP_ON_CLEAR
            awarded = self._award_points(member, f"Odd Job: {c.get('title', cid)}")
            pts = f" **+{POINTS_ON_CLEAR}** house points." if awarded else ""
            self.save()
            return f"{text}{pts} *(Rep {p['rep']})*"
        p["failed"] = int(p.get("failed") or 0) + 1
        self.save()
        return f"{text} *(Rep {p.get('rep', 0)})*"

    def choose(self, member: discord.Member, p: dict, choice_index: int) -> tuple[str, bool]:
        """Apply a choice. Returns (message, still_active)."""
        active = p.get("active")
        if not active:
            return "No active contract.", False
        cid = active["id"]
        c = CONTRACTS[cid]
        beat_i = int(active.get("beat") or 0)
        beats = c["beats"]
        if beat_i >= len(beats):
            return "This job is stuck — abandon it.", False
        choices = beats[beat_i].get("choices") or []
        if choice_index < 0 or choice_index >= len(choices):
            return "That choice isn't available.", True
        nxt = choices[choice_index]["next"]
        if isinstance(nxt, str) and nxt.startswith("end_"):
            return self.apply_ending(member, p, nxt), False
        active["beat"] = int(nxt)
        p["active"] = active
        self.save()
        return f"You choose: **{choices[choice_index]['label']}**", True

    def start_contract(self, p: dict, cid: str) -> str:
        if p.get("active"):
            return "Finish or `/job abandon` your current job first."
        if cid not in CONTRACTS:
            return "That job isn't on the board."
        c = CONTRACTS[cid]
        if int(p.get("rep") or 0) < int(c.get("rep_need") or 0):
            return f"You need **rep {c['rep_need']}** for that job."
        if cid not in (p.get("offers") or []):
            return "That offer expired — `/job board` for a fresh list."
        p["active"] = {"id": cid, "beat": 0, "started_at": time.time()}
        # remove from offers so you can't double-accept
        p["offers"] = [x for x in p.get("offers", []) if x != cid]
        self.save()
        return f"Accepted: **{c['title']}**. Use the buttons — or `/job play`."

    # ---- commands ----

    @job.command(name="board", description="Open the Odd Jobs board and accept a contract.")
    async def job_board(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        p = self.player(interaction.user.id)
        self.refresh_offers(p, force=True)
        view = BoardView(self, interaction.user.id, list(p.get("offers") or []))
        await interaction.response.send_message(
            embed=self.board_embed(interaction.user, p),
            view=view,
            ephemeral=True,
        )

    @job.command(name="play", description="Continue your active Odd Job (interactive buttons).")
    async def job_play(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        p = self.player(interaction.user.id)
        if not p.get("active"):
            await interaction.response.send_message(
                "No active job. Open `/job board` first.", ephemeral=True
            )
            return
        view = PlayView(self, interaction.user.id)
        await interaction.response.send_message(
            embed=self.play_embed(interaction.user, p),
            view=view,
            ephemeral=True,
        )

    @job.command(name="status", description="Your Odd Jobs reputation and active contract.")
    async def job_status(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        p = self.player(interaction.user.id)
        active = p.get("active")
        if active and active.get("id") in CONTRACTS:
            c = CONTRACTS[active["id"]]
            job_line = f"**Active:** {c['title']} (beat {int(active.get('beat', 0)) + 1})"
        else:
            job_line = "**Active:** none — `/job board` to take one."
        embed = discord.Embed(
            title="Odd Jobs — Status",
            description=(
                f"{job_line}\n\n"
                f"**Rep** {p.get('rep', 0)} · "
                f"Cleared **{p.get('cleared', 0)}** · "
                f"Failed **{p.get('failed', 0)}**"
            ),
            color=INK,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @job.command(name="abandon", description="Drop your active Odd Job (counts as a fail).")
    async def job_abandon(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        p = self.player(interaction.user.id)
        if not p.get("active"):
            await interaction.response.send_message("Nothing to abandon.", ephemeral=True)
            return
        title = CONTRACTS.get(p["active"].get("id", ""), {}).get("title", "Job")
        p["active"] = None
        p["failed"] = int(p.get("failed") or 0) + 1
        self.save()
        await interaction.response.send_message(
            f"Abandoned **{title}**. It counts as a fail. The board will have other work.",
            ephemeral=True,
        )


class BoardView(discord.ui.View):
    def __init__(self, cog: Contracts, user_id: int, offers: list[str]):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        for i, cid in enumerate(offers[:BOARD_SLOTS]):
            c = CONTRACTS[cid]
            label = c["title"][:80]
            self.add_item(AcceptButton(cog, user_id, cid, label, i))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your board.", ephemeral=True)
            return False
        if interaction.channel_id not in JOB_CHANNEL_IDS:
            await interaction.response.send_message(_channel_hint(), ephemeral=True)
            return False
        return True


class AcceptButton(discord.ui.Button):
    def __init__(self, cog: Contracts, user_id: int, cid: str, label: str, row: int):
        super().__init__(
            label=f"Accept: {label}"[:80],
            style=discord.ButtonStyle.primary,
            row=min(row, 2),
        )
        self.cog = cog
        self.user_id = user_id
        self.cid = cid

    async def callback(self, interaction: discord.Interaction):
        p = self.cog.player(self.user_id)
        msg = self.cog.start_contract(p, self.cid)
        if p.get("active"):
            view = PlayView(self.cog, self.user_id)
            await interaction.response.edit_message(
                content=msg,
                embed=self.cog.play_embed(interaction.user, p),
                view=view,
            )
        else:
            await interaction.response.edit_message(
                content=msg,
                embed=self.cog.board_embed(interaction.user, p),
                view=BoardView(self.cog, self.user_id, list(p.get("offers") or [])),
            )


class PlayView(discord.ui.View):
    def __init__(self, cog: Contracts, user_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        p = cog.player(user_id)
        active = p.get("active") or {}
        cid = active.get("id")
        if not cid or cid not in CONTRACTS:
            return
        beat_i = int(active.get("beat") or 0)
        beats = CONTRACTS[cid]["beats"]
        if beat_i >= len(beats):
            return
        for i, ch in enumerate(beats[beat_i].get("choices") or []):
            self.add_item(ChoiceButton(cog, user_id, i, ch["label"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your job.", ephemeral=True)
            return False
        if interaction.channel_id not in JOB_CHANNEL_IDS:
            await interaction.response.send_message(_channel_hint(), ephemeral=True)
            return False
        return True


class ChoiceButton(discord.ui.Button):
    def __init__(self, cog: Contracts, user_id: int, index: int, label: str):
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.secondary,
            row=index // 2,
        )
        self.cog = cog
        self.user_id = user_id
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        p = self.cog.player(self.user_id)
        note, still = self.cog.choose(interaction.user, p, self.index)
        if still:
            view = PlayView(self.cog, self.user_id)
            await interaction.response.edit_message(
                embed=self.cog.play_embed(interaction.user, p, note),
                view=view,
            )
        else:
            await interaction.response.edit_message(
                content=note,
                embed=None,
                view=None,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Contracts(bot))
