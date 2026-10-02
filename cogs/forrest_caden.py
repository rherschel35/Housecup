"""
The Forrest of Caden — solo Telltale-style story (private test: Ch 1–2).

    /forrest start   — begin or restart Chapters 1–2
    /forrest resume  — continue your run
    /forrest status  — flags / chapter
    /forrest reset   — clear your save

Locked to Headmaster Gon Vale and one test channel while Ch 1–2 are in trial.
No house points. Owner-locked buttons. Art from story_art_assets/.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.forrest_story_script import (
    ABILITIES,
    ART,
    CITY_ESSENTIALS_2,
    CITY_ESSENTIALS_3,
    HEX_PAIRS,
    HEX_ROUNDS_MAX,
    HEX_ROUNDS_TO_WIN,
    HEX_SPELL_POOL,
    MATERIAL_LABELS,
    NODES,
    SCHOOL_MATERIALS,
    SKILL_POINTS_MAX_PER,
    SKILL_POINTS_TOTAL,
    ability_mod,
    default_abilities,
    format_abilities,
    hex_option_count,
    hex_seconds,
    pronouns,
    render,
)

log = logging.getLogger("velmora.forrest")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "forrest_caden_state.json"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "story_art_assets"

# Private test lock — only Gon, only the break-room channel he named.
# Override with FORREST_CHANNEL_ID / FORREST_TESTER_IDS if needed.
FORREST_CHANNEL_ID = int(os.getenv("FORREST_CHANNEL_ID", "1555383179802579005") or 0)
FORREST_TESTER_IDS = frozenset(
    int(x) for x in (os.getenv("FORREST_TESTER_IDS", "555141900802457630") or "").split(",")
    if x.strip().isdigit()
) or frozenset({555141900802457630})  # Headmaster Gon Vale

EMBED_COLOR = 0x2F4F3E
MAX_DESC = 3800


def _load() -> dict:
    if not STATE_PATH.exists():
        return {"players": {}}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"players": {}}


def _save(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _default_save() -> dict:
    return {
        "node": "title",
        "page": 0,
        "chapter": 1,
        "flags": {},
        "protagonist": "mack",
        "abilities": default_abilities(),
        "gus_with_party": True,
        "school_materials": [],
        "city_essentials": [],
        "sneak_score": 0,
        "sneak_round": 0,
        "active": False,
        "finished_ch2": False,
    }


def _art_path(key: str | None) -> Optional[Path]:
    if not key:
        return None
    name = ART.get(key, key)
    if not name:
        return None
    path = ASSETS_DIR / name
    return path if path.is_file() else None


def _chunk(text: str, limit: int = MAX_DESC) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n\n"):
        candidate = (cur + "\n\n" + para).strip() if cur else para
        if len(candidate) <= limit:
            cur = candidate
        else:
            if cur:
                parts.append(cur)
            if len(para) <= limit:
                cur = para
            else:
                # hard wrap
                while len(para) > limit:
                    parts.append(para[:limit])
                    para = para[limit:]
                cur = para
    if cur:
        parts.append(cur)
    return parts or [text[:limit]]


class ForrestCaden(commands.Cog):
    """Private test of The Forrest of Caden, chapters 1–2 — tester + channel only."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = _load()
        self._sessions: dict[int, "StorySession"] = {}

    def _allowed(self, interaction: discord.Interaction) -> str | None:
        """None if ok; otherwise an ephemeral refusal line."""
        if interaction.user.id not in FORREST_TESTER_IDS:
            return "This story is in private test — not open yet."
        if FORREST_CHANNEL_ID and interaction.channel_id != FORREST_CHANNEL_ID:
            return f"Run this in <#{FORREST_CHANNEL_ID}> while testing."
        return None

    def get_save(self, user_id: int) -> dict:
        players = self.state.setdefault("players", {})
        key = str(user_id)
        if key not in players:
            players[key] = _default_save()
        save = players[key]
        # Older saves: migrate / fill the three-skill sheet.
        if "abilities" not in save or not isinstance(save.get("abilities"), dict):
            save["abilities"] = default_abilities()
        else:
            ab = save["abilities"]
            # Legacy six-ability sheet → three skills.
            if "atk" not in ab and any(k in ab for k in ("str", "dex", "int", "cha")):
                save["abilities"] = {
                    "atk": int(ab.get("str", 0) or 0),
                    "wis": max(int(ab.get("wis", 0) or 0), int(ab.get("int", 0) or 0), int(ab.get("cha", 0) or 0)),
                    "ste": int(ab.get("dex", 0) or 0),
                }
            for ab_key in (a[0] for a in ABILITIES):
                save["abilities"].setdefault(ab_key, 0)
        return save

    def write(self) -> None:
        _save(self.state)

    # ---------------------------------------------------------------- commands

    forrest = app_commands.Group(
        name="forrest",
        description="(private test) The Forrest of Caden — solo story, Ch 1–2.",
    )

    @forrest.command(name="start", description="Start or restart The Forrest of Caden (Ch 1–2).")
    async def forrest_start(self, interaction: discord.Interaction):
        refuse = self._allowed(interaction)
        if refuse:
            await interaction.response.send_message(refuse, ephemeral=True)
            return
        save = _default_save()
        save["active"] = True
        save["protagonist"] = "mack"
        self.state.setdefault("players", {})[str(interaction.user.id)] = save
        self.write()
        await interaction.response.defer()
        session = StorySession(self, interaction.user.id, interaction.channel)
        self._sessions[interaction.user.id] = session
        await session.show(interaction.followup)

    @forrest.command(name="resume", description="Resume your Forrest of Caden run.")
    async def forrest_resume(self, interaction: discord.Interaction):
        refuse = self._allowed(interaction)
        if refuse:
            await interaction.response.send_message(refuse, ephemeral=True)
            return
        save = self.get_save(interaction.user.id)
        if not save.get("active") and not save.get("finished_ch2"):
            if save.get("node") in (None, "title") and not save.get("protagonist"):
                await interaction.response.send_message(
                    "No run yet. Use `/forrest start`.", ephemeral=True
                )
                return
        save["active"] = True
        self.write()
        await interaction.response.defer()
        session = StorySession(self, interaction.user.id, interaction.channel)
        self._sessions[interaction.user.id] = session
        await session.show(interaction.followup)

    @forrest.command(name="status", description="Your Forrest flags and chapter.")
    async def forrest_status(self, interaction: discord.Interaction):
        refuse = self._allowed(interaction)
        if refuse:
            await interaction.response.send_message(refuse, ephemeral=True)
            return
        save = self.get_save(interaction.user.id)
        flags = save.get("flags", {})
        lines = [
            f"**Node:** `{save.get('node')}` (page {save.get('page', 0)})",
            f"**Chapter:** {save.get('chapter', 1)}",
            f"**Protagonist:** {save.get('protagonist') or '—'}",
            f"**Skills:** {format_abilities(save.get('abilities'))}",
            f"**Gus with party:** {save.get('gus_with_party', True)}",
            f"**School kit:** {', '.join(save.get('school_materials') or []) or '—'}",
            f"**City kit:** {', '.join(save.get('city_essentials') or []) or '—'}",
            f"**Flags:** `{json.dumps(flags, ensure_ascii=False)}`",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @forrest.command(name="reset", description="Clear your Forrest save.")
    async def forrest_reset(self, interaction: discord.Interaction):
        refuse = self._allowed(interaction)
        if refuse:
            await interaction.response.send_message(refuse, ephemeral=True)
            return
        self.state.setdefault("players", {})[str(interaction.user.id)] = _default_save()
        self.write()
        self._sessions.pop(interaction.user.id, None)
        await interaction.response.send_message("Forrest save cleared.", ephemeral=True)


class StorySession:
    def __init__(self, cog: ForrestCaden, user_id: int, channel):
        self.cog = cog
        self.user_id = user_id
        self.channel = channel
        self.message: Optional[discord.Message] = None

    @property
    def save(self) -> dict:
        return self.cog.get_save(self.user_id)

    def p(self) -> dict[str, str]:
        protag = self.save.get("protagonist") or "mack"
        return pronouns(protag)

    def node(self) -> dict:
        nid = self.save.get("node") or "title"
        node = NODES.get(nid)
        if not node:
            return {"pages": ["(missing node)"], "end": True}
        if nid == "dream_bridge":
            return self._dream_bridge_node()
        if nid == "wake_letter":
            return self._wake_letter_node()
        if nid == "wake_breakfast":
            return self._wake_breakfast_node()
        if nid == "wake_write":
            return self._wake_write_node()
        if nid == "sneak_done":
            return self._sneak_done_node()
        if nid == "city_arrive":
            return self._city_arrive_node()
        if nid == "city_essentials":
            return self._city_essentials_node()
        if nid == "train":
            return self._train_node()
        if nid == "ch2_arrive":
            return self._ch2_arrive_node()
        if nid == "gus_ella_talk":
            return self._gus_talk_node()
        if nid == "morning":
            return self._morning_node()
        return node

    def _inventory_line(self) -> str:
        school = [
            MATERIAL_LABELS.get(k, k) for k in (self.save.get("school_materials") or [])
        ]
        city = [
            MATERIAL_LABELS.get(k, k) for k in (self.save.get("city_essentials") or [])
        ]
        bits = []
        if school:
            bits.append("School pack: " + ", ".join(school))
        if city:
            bits.append("City stall: " + ", ".join(city))
        if self.save.get("flags", {}).get("has_nox"):
            bits.append("Companion: Nox")
        if self.save.get("flags", {}).get("cave_tip"):
            bits.append("Note: Don't sleep in the cave")
        return "\n".join(f"• {b}" for b in bits) if bits else "• (empty pockets and stubborn hope)"

    def _dream_bridge_node(self) -> dict:
        flags = self.save.get("flags", {})
        check = flags.get("last_check") or {}
        p = self.p()
        if flags.get("saw_the_hand"):
            outcome = render(
                "For half a heartbeat you see it clearly: a long, pale hand with too many joints, reaching from behind "
                "a tree toward {missing}'s shoulder, not yours.",
                p,
            )
        else:
            outcome = "You see only shadow, and the dark closing like a door."
        roll_line = ""
        if check.get("roll") is not None:
            roll_line = "\n\n" + self._format_check_result(check)
        pages = [
            outcome
            + roll_line
            + "\n\nShe reached a hand toward you —"
        ]
        return {"art": "scare", "pages": pages, "goto": "dream_wake"}

    def _wake_letter_node(self) -> dict:
        check = (self.save.get("flags") or {}).get("last_check") or {}
        roll_bit = (
            "\n\n" + self._format_check_result(check)
            if check.get("roll") is not None
            else ""
        )
        ok = bool(check.get("ok"))
        if ok:
            body = (
                "You read it again, slower. The ink is Yuna's, but near the torn edge the paper is faintly warped, as if "
                "it got wet and dried wrong. Under the last unfinished line, pressed hard enough to leave a ghost on the "
                "next sheet, you catch three words she almost didn't write:\n\n"
                "***Don't come alone.***\n\n"
                "Your stomach drops. The owl never brought a second page."
            )
            self.save.setdefault("flags", {})["letter_clue"] = True
        else:
            body = (
                "You read it again. July. The joke about the sapwood tree. The sentence that stops mid-thought as if she "
                "meant to finish it tomorrow.\n\n"
                "If there's more in it, your eyes won't give it to you. Not this morning. Not with your hands shaking."
            )
        self.cog.write()
        return {"art": "scare", "pages": [body + roll_bit], "goto": "gus_morning"}

    def _wake_breakfast_node(self) -> dict:
        check = (self.save.get("flags") or {}).get("last_check") or {}
        roll_bit = (
            "\n\n" + self._format_check_result(check)
            if check.get("roll") is not None
            else ""
        )
        ok = bool(check.get("ok"))
        if ok:
            body = (
                "The house table is a storm of chatter and toast. You scan for Yuna out of habit — and notice something "
                "else. Two other seats that should be filled aren't. A third-year from Thornmere. A quiet Raven-lane "
                "transfer who always sat near the end.\n\n"
                "Absence has a pattern this morning. You don't like patterns that look like teeth."
            )
            self.save.setdefault("flags", {})["noticed_other_missing"] = True
        else:
            body = (
                "The house table is noise and elbows and someone stealing jam. You look for Yuna until your eyes hurt. "
                "She's not there. Beyond that, the morning blurs into ordinary first-day chaos, and you can't tell if "
                "anyone else is missing or if your fear is inventing company."
            )
        self.cog.write()
        return {"art": "missing_seat", "pages": [body + roll_bit], "goto": "gus_morning"}

    def _wake_write_node(self) -> dict:
        check = (self.save.get("flags") or {}).get("last_check") or {}
        roll_bit = (
            "\n\n" + self._format_check_result(check)
            if check.get("roll") is not None
            else ""
        )
        ok = bool(check.get("ok"))
        if ok:
            body = (
                "You write fast, too honest, ink blotting where your hand presses. *Where are you. I'm coming. Wait for "
                "me.* You tie it to the owl with fingers that won't stay steady.\n\n"
                "The bird lifts, circles once, and vanishes into the grey. You don't know if it will find her. But it "
                "left. That has to count for something."
            )
            self.save.setdefault("flags", {})["owl_sent"] = True
        else:
            body = (
                "You write. You rewrite. The owl on the perch watches you with the bored contempt of a creature that has "
                "delivered worse panic than yours.\n\n"
                "When you finally hold out the letter, the owl ruffles, turns its head, and refuses to take it. Outside, "
                "the sky is already filling with other wings. Yours stays put, stubborn as a locked door."
            )
            self.save.setdefault("flags", {})["owl_refused"] = True
        self.cog.write()
        return {"art": "scare", "pages": [body + roll_bit], "goto": "gus_morning"}

    def _city_arrive_node(self) -> dict:
        with_gus = self.save.get("gus_with_party", True)
        atk = ability_mod(self.save.get("abilities"), "Attack")
        opts = hex_option_count(atk)
        secs = hex_seconds(with_gus)
        tip = (
            f"🎲 **Attack — counter the hex.** They cast; you have **{secs} seconds** to pick the right spell "
            f"({'Gus buys you time' if with_gus else 'alone — move fast'}). "
            f"**{opts}** choices"
            + (f" (Attack +{atk} thins the wrong answers)" if atk else " (raise Attack to see fewer fakes)")
            + f". First to **{HEX_ROUNDS_TO_WIN}** clean counters wins."
        )
        if with_gus:
            setup = (
                "The city doesn't care that you're fifth-years with a noble reason. It cares that you're young, weighed "
                "down with bags, and looking the wrong way on the wrong corner.\n\n"
                "A hooded figure steps out of the dark as if they rehearsed it: wand already raised, voice flat, no face "
                "to read under the hood.\n\n"
                '**Thief:** "Bags. Quietly. Nobody needs to get clever."\n\n'
                "You put yourself in front of Ella. She's clutching her bag as if it's the only solid thing left in the "
                "world. Gus is right beside you, glasses crooked, hands half raised, making a noise that is definitely "
                "not a joke."
            )
            art = "city_fight_trio"
        else:
            setup = (
                "The city doesn't care that you're fifth-years with a noble reason. It cares that you're young, weighed "
                "down with bags, and looking the wrong way on the wrong corner.\n\n"
                "A hooded figure steps out of the dark as if they rehearsed it: wand already raised, voice flat, no face "
                "to read under the hood.\n\n"
                '**Thief:** "Bags. Quietly. Nobody needs to get clever."\n\n'
                "You put yourself in front of Ella. She's clutching her bag as if it's the only solid thing left in the "
                "world. Gus isn't here; he's still back at the castle. The empty space beside you feels like one more threat."
            )
            art = "city_fight_duo"
        # Page 0 = read the ambush; page 1 (last) attaches HexCounterView and starts the timer.
        pages = [setup, tip + "\n\nTheir wand tip brightens — counters incoming."]
        return {
            "art": art,
            "pages": pages,
            "mini": "city_fight",
            "goto": "city_essentials",
        }

    def _city_essentials_node(self) -> dict:
        """Recap the robbery before the stall — not just 'fought, now shop'."""
        flags = self.save.setdefault("flags", {})
        outcome = flags.get("city_fight") or "won"
        with_gus = self.save.get("gus_with_party", True)
        hits = int(flags.get("hex_hits") or 0)
        blocks = int(flags.get("hex_blocks") or 0)
        lost = flags.get("lost_item")
        lost_label = MATERIAL_LABELS.get(lost, lost) if lost else None

        if outcome == "won":
            if with_gus:
                fight = (
                    f"The last hex dies on your shield. The thief bolts — boots skidding, hood flapping — and the "
                    f"alley swallows them. Gus whoops once, then winces like he didn't mean to be loud. Ella is still "
                    f"holding her bag in both hands, knuckles white, breathing like she forgot how until just now.\n\n"
                    f"You counted it without meaning to: **{blocks}** hexes turned, **{hits}** that kissed you on the way past. "
                    f"Nobody's bleeding badly. Your hands disagree — they're shaking, and you pretend it's the cold."
                )
            else:
                fight = (
                    f"The last hex dies on your shield. The thief bolts into the dark. Without Gus, the quiet afterward "
                    f"is too big — just you, Ella, and the echo of someone else's wand-work.\n\n"
                    f"**{blocks}** counters. **{hits}** hits you didn't want. Ella's bag is still yours to protect. "
                    f"Your hands are shaking; you pretend it's the cold."
                )
        else:
            if with_gus:
                fight = (
                    f"You don't win clean. A hex lands hard enough that the world tips; Gus hauls you sideways while "
                    f"Ella swears in a language that isn't for classrooms. The thief takes what they can grab and runs — "
                    f"not everything, but enough to feel like a lesson.\n\n"
                    f"**{blocks}** spells you caught. **{hits}** that caught you."
                    + (
                        f" Something from the school pack is gone — **{lost_label}** — vanished with the hood."
                        if lost_label
                        else " Your pride is lighter than your bag."
                    )
                    + " Afterwards your hands shake, and you stop pretending it's only the cold."
                )
            else:
                fight = (
                    f"You don't win clean. Without Gus, there's no one to yank you out of the worst of it. Ella pulls "
                    f"you into the lantern light of a late stall while the thief's footsteps fade.\n\n"
                    f"**{blocks}** counters. **{hits}** hits."
                    + (
                        f" You're missing **{lost_label}** from the school pack."
                        if lost_label
                        else ""
                    )
                    + " Your hands won't stay still."
                )

        stall = (
            "\n\nOne market stall still has its lantern lit — oil, wool, a knife that looks like it has opinions. "
            "The last train toward Caden won't wait on your nerves.\n\n"
            "**Choose essentials** for the road."
            + (" More options with Gus here." if with_gus else " Travel light — Gus isn't with you yet.")
        )
        return {
            "art": "materials",
            "pages": [fight + stall],
            "mini": "pick_essentials",
            "goto": "train",
        }

    def _sneak_done_node(self) -> dict:
        if self.save.get("gus_with_party", True):
            pages = [
                "You taste cold air that isn't castle air. For one stupid second you could cry with relief. Gus elbows "
                "you and whispers something daft so none of you has to admit how hard your hearts are going.\n\n"
                "**All three of you made it out.**"
            ]
            empath = (
                "Freedom feels illegal. You keep waiting for the castle to haul you back by the collar."
            )
        else:
            pages = [
                "Mordy's voice stops the night cold. Gus steps forward before you can, glasses crooked, chin up, already "
                "volunteering to be the problem.\n\n"
                '**Gus:** *(mouthing)* "Go."\n\n'
                "Ella's hand finds your sleeve and pulls. Leaving him feels like biting through your own tongue. He'll "
                "catch up, he said. You want to believe him the way you used to believe summer was endless."
            ]
            empath = (
                "Loyalty has a sound when it cracks a little. It's quiet, and it follows you out into the dark."
            )
        return {"art": "sneak", "pages": pages, "empath": empath, "goto": "city_arrive"}

    def _train_node(self) -> dict:
        p = self.p()
        if self.save.get("gus_with_party", True):
            pages = [
                render(
                    "The train toward Caden is half empty and smells of old upholstery and someone's forgotten pasty. "
                    "Ella takes the window seat. Gus invents an elaborate theory about the thief having terrible taste "
                    "in alleyways until even Ella snorts.\n\n"
                    "When he goes off in search of the trolley, Ella speaks more quietly.\n\n"
                    '**Ella:** "We\'re doing what\'s right. If something\'s wrong out there… I trust you."\n\n'
                    "Gus comes back mid-sentence, arms full of sweets, with a joke that lands soft. The heaviness thins. "
                    "Ella doesn't reach for your hand. She doesn't need to, not with him there filling the air.\n\n"
                    "Outside, the hills darken toward the Forrest.",
                    p,
                )
            ]
            self.save.setdefault("flags", {})["ella_hand"] = False
            art = "train_trio"
        else:
            pages = [
                render(
                    "The train toward Caden is half empty. Ella takes the window seat. Without Gus, the quiet has room "
                    "to mean things.\n\n"
                    '**Ella:** "We\'re doing what\'s right. If something\'s wrong out there… I trust you."\n\n'
                    "It isn't a speech. It's heavier than a speech. You feel its weight in your ribs: her faith, your "
                    "fear, the empty seat where Gus should be making all this easier.\n\n"
                    "She takes your hand. She isn't asking and she isn't performing. She's just here.\n\n"
                    "The carriage jolts over a crossing. Boots hit the step. Gus, breathless, glasses fogged, bag half "
                    "fastened, hauls himself aboard a heartbeat before the doors close.\n\n"
                    "He sees.\n\n"
                    "For a moment nobody speaks. Then he laughs wrong and sits across from you both as if nothing "
                    "happened, which means everything did.\n\n"
                    "Outside, the hills darken toward the Forrest.",
                    p,
                )
            ]
            self.save["gus_with_party"] = True
            self.save.setdefault("flags", {})["ella_hand"] = True
            self.save.setdefault("flags", {})["gus_saw_hand"] = True
            art = "train_hand"
        self.cog.write()
        return {"art": art, "pages": pages, "goto": "ch1_end"}

    def _ch2_arrive_node(self) -> dict:
        p = self.p()
        gus_line = (
            "Gus clears his throat, as though that might make this less awful.\n\n"
            if self.save.get("gus_with_party", True)
            else ""
        )
        pages = [
            render(
                "The train sighs to a stop in Caden, the last town before the Forrest. It smells of lake water, fresh "
                "bread and woodsmoke. Beyond the crooked rooftops the trees begin, and they're dark even at noon.\n\n"
                "You unfold the photograph of {missing}. In it she's grinning and waving at you, the way she did the "
                "summer it was taken, as if nothing could ever change.\n\n"
                + gus_line
                + "Ella takes the photograph from you gently, as though it might break.\n\n"
                "You start asking.",
                p,
            )
        ]
        return {"art": "caden", "chapter": 2, "pages": pages, "goto": "ask_shops"}

    def _gus_talk_node(self) -> dict:
        flags = self.save.setdefault("flags", {})
        advice = flags.get("gus_advice")
        if advice == "tell":
            advice_line = "You told me to tell her."
        elif advice == "dont":
            advice_line = "You told me to leave it."
        elif advice == "careful":
            advice_line = "You told me not to half-do it."
        else:
            advice_line = "You told me something about her."
        if flags.get("gus_saw_hand"):
            pages = [
                "Gus doesn't make a joke first. That's how you know.\n\n"
                '**Gus:** "I saw. On the train. Her hand." He stares hard at the floorboards. "I\'m not angry with you. '
                "I don't think. I'm angry at the timing. At me, for not being there. At… yeah.\"\n\n"
                "He asks if you asked her to. You didn't. He asks if you like her that way. You don't, and saying it out "
                "loud feels like setting down something sharp very carefully.\n\n"
                f'**Gus:** "Okay. Then I still… I still want to try. Or I don\'t. {advice_line} I\'m remembering that. '
                'Just — don\'t let me be the last to know if the world tips over again."'
            ]
            empath = "His jealousy isn't ugly. It's frightened. You feel it, and you don't flinch away."
            flags["gus_talk"] = "serious"
        else:
            pages = [
                "Gus paces once, then sits on the end of the bed.\n\n"
                '**Gus:** "She\'s been laughing more. With me. Or *near* me. I can\'t tell which, and it\'s killing me in '
                'a really stupid way." His grin is small. "You were right there the whole time, and somehow I still need '
                'a referee."\n\n'
                "You talk, not forever, but long enough for the lamp to burn low.\n\n"
                '**Gus:** "Tomorrow we find Yuna. Tonight I pretend I\'m brave about Ella. Deal?"'
            ]
            empath = "It's lighter now, and hopeful. The crush is still loud, but it's no longer bleeding."
            flags["gus_talk"] = "light"
        self.cog.write()
        return {"art": "inn", "pages": pages, "empath": empath, "goto": "morning"}

    def _morning_node(self) -> dict:
        p = self.p()
        inv = self._inventory_line()
        head = (
            "At dawn you check your supplies: water, whatever you bought, whatever the thief didn't take.\n\n"
            f"**Pack**\n{inv}\n\n"
        )
        if self.save.get("flags", {}).get("has_nox"):
            body = (
                "Nox waits by the door as though the day already belongs to him. When you step toward the tree line, he "
                "follows without being asked."
            )
        elif self.save.get("flags", {}).get("cave_tip"):
            body = (
                "You write it once in the margin of the trail map, small, so you can't pretend you forgot:\n"
                "***Don't sleep in the cave.***"
            )
        else:
            body = (
                "No dog. No warning. Just the three of you and a map that ends where the green begins."
            )
        pages = [
            render(
                head + body + "\n\nThe Forrest of Caden stands waiting. There's still no word of {missing}.",
                p,
            )
        ]
        return {"art": "edge", "pages": pages, "goto": "ch2_end"}

    async def show(self, destination) -> None:
        """destination: interaction.followup or channel."""
        await self._render(destination, edit=False)

    async def refresh(self, interaction: Optional[discord.Interaction] = None) -> None:
        """Redraw current node on the existing public story message.

        Always edits the component host message (or ``self.message``). Never
        uses ``edit_original_response``, which can retarget an ephemeral reply
        and wipe the player's progress when that reply is dismissed.
        """
        try:
            if interaction is not None:
                host = interaction.message or self.message
                if host is None:
                    await self._render(self.channel, edit=False)
                    return
                self.message = host
                await self._render_on_host(interaction, host)
            elif self.message is not None:
                await self._render(self.message, edit=True)
            else:
                await self._render(self.channel, edit=False)
        except Exception:
            log.exception("Forrest refresh failed; posting a new story message")
            try:
                await self._render(self.channel, edit=False)
            except Exception:
                log.exception("Forrest fallback send also failed")

    async def _render_on_host(
        self, interaction: discord.Interaction, host: discord.Message
    ) -> None:
        """Acknowledge the button and redraw ``host`` — never a 2nd/ephemeral post."""
        if not interaction.response.is_done():
            # Prefer atomic edit of the button's message (public).
            await self._render(interaction, edit=True)
            return
        # Already acknowledged (e.g. deferred update) — edit the host Message object.
        await self._render(host, edit=True)

    async def _render(self, destination, edit: bool) -> None:
        save = self.save
        node = self.node()
        # apply dynamic page art
        art_key = node.get("art")
        page_i = save.get("page", 0)
        art_pages = node.get("art_pages") or {}
        if page_i in art_pages:
            art_key = art_pages[page_i]

        pages = node.get("pages") or [""]
        # expand long pages
        flat: list[str] = []
        for pg in pages:
            flat.extend(_chunk(render(pg, self.p()) if save.get("protagonist") or "{you}" not in pg else pg))
        if not flat:
            flat = ["…"]

        # if page past end, advance
        if page_i >= len(flat) and not node.get("choices") and not node.get("mini") and not node.get("end"):
            await self._advance(node.get("goto"))
            return await self._render(destination, edit)

        if page_i >= len(flat):
            page_i = len(flat) - 1
            save["page"] = page_i

        text = flat[page_i]
        # One-shot roll line from a check that advanced into this node (no 2nd Discord message).
        flags = save.setdefault("flags", {})
        roll_blurb = flags.pop("roll_blurb", None)
        if roll_blurb:
            text = f"{roll_blurb}\n\n{text}"
            self.cog.write()
        # show empath only on last page of node when no more continue
        more_pages = page_i < len(flat) - 1
        empath = node.get("empath") if not more_pages else None
        if empath and save.get("protagonist"):
            text = (
                text
                + "\n\n✨ **Empath's Sense**\n"
                + "*"
                + render(empath, self.p())
                + "*"
            )

        title = "The Forrest of Caden"
        if save.get("chapter") == 2:
            title += " — Chapter 2"
        else:
            title += " — Chapter 1"

        embed = discord.Embed(title=title, description=text[:4096], color=EMBED_COLOR)
        if save.get("protagonist"):
            skills = format_abilities(save.get("abilities"))
            embed.set_footer(text=f"Mack · looking for Yuna · {skills}")

        file = None
        path = _art_path(art_key)
        if path:
            file = discord.File(path, filename=path.name)
            embed.set_image(url=f"attachment://{path.name}")

        # view
        view: discord.ui.View
        if more_pages:
            view = ContinueView(self)
        elif node.get("mini") == "assign_skills":
            view = SkillAssignView(self, nxt=node.get("goto"))
        elif node.get("mini") == "check_roll":
            view = CheckRollView(self, node.get("check") or {}, node.get("goto"))
        elif node.get("mini") == "pick_materials":
            view = MaterialPickView(self, SCHOOL_MATERIALS, need=2, flag_key="school_materials", nxt=node.get("goto"))
        elif node.get("mini") == "pick_essentials":
            opts = CITY_ESSENTIALS_3 if save.get("gus_with_party", True) else CITY_ESSENTIALS_2
            need = 3 if save.get("gus_with_party", True) else 2
            view = MaterialPickView(self, opts, need=need, flag_key="city_essentials", nxt=node.get("goto"))
        elif node.get("mini") == "sneak":
            view = SneakView(self)
        elif node.get("mini") == "city_fight":
            view = HexCounterView(self)
        elif node.get("choices"):
            view = ChoiceView(self, node["choices"])
        elif node.get("end"):
            view = discord.ui.View(timeout=120)
            save["active"] = False
            if save.get("node") == "ch2_end":
                save["finished_ch2"] = True
            self.cog.write()
        else:
            # auto-continue to goto
            view = ContinueView(self, auto_goto=node.get("goto"))

        kwargs = {"embed": embed, "view": view}
        if file and not edit:
            kwargs["file"] = file
        elif file and edit:
            kwargs["attachments"] = [file]

        if edit and isinstance(destination, discord.Interaction):
            # Edit the component's public message only. Never edit_original_response
            # (that can rewrite an ephemeral send_message and look like a 2nd post).
            host = destination.message or self.message
            if not destination.response.is_done():
                await destination.response.edit_message(**kwargs)
                self.message = destination.message or host
            elif host is not None:
                self.message = await host.edit(**kwargs)
            else:
                # Slash-start / no host: public followup only (never ephemeral).
                self.message = await destination.followup.send(**kwargs)
        elif edit and isinstance(destination, discord.Message):
            self.message = await destination.edit(**kwargs)
        elif edit and self.message:
            self.message = await self.message.edit(**kwargs)
        else:
            # followup or channel
            send = destination.send if hasattr(destination, "send") else destination
            self.message = await send(**kwargs)

        # Timed Attack mini-game: start the first hex window on the live message.
        if isinstance(view, HexCounterView) and self.message is not None:
            await view.begin(self.message)

    async def _advance(self, goto: Optional[str]) -> None:
        if not goto:
            return
        self.save["node"] = goto
        self.save["page"] = 0
        if goto.startswith("ch2") or NODES.get(goto, {}).get("chapter") == 2:
            self.save["chapter"] = 2
        self.cog.write()

    async def continue_page(self, interaction: discord.Interaction, auto_goto: Optional[str] = None) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        node = self.node()
        pages = node.get("pages") or [""]
        flat: list[str] = []
        for pg in pages:
            t = render(pg, self.p()) if self.save.get("protagonist") or "{" not in pg else pg
            flat.extend(_chunk(t))
        page_i = self.save.get("page", 0)
        if page_i < len(flat) - 1:
            self.save["page"] = page_i + 1
            self.cog.write()
            await self.refresh(interaction)
            return
        # finished pages → goto or choices already shown; ContinueView with auto_goto
        nxt = auto_goto or node.get("goto")
        if nxt:
            await self._advance(nxt)
            await self.refresh(interaction)
        else:
            if not interaction.response.is_done():
                await interaction.response.defer()

    def _apply_check(self, check: dict) -> dict:
        """Roll d20 + ability mod vs DC; store on flags['last_check']."""
        skill = check.get("skill") or "Check"
        dc = int(check.get("dc") or 12)
        mod = ability_mod(self.save.get("abilities"), skill)
        d20 = random.randint(1, 20)
        total = d20 + mod
        ok = total >= dc
        result = {
            "skill": skill,
            "dc": dc,
            "d20": d20,
            "mod": mod,
            "roll": total,  # kept as total for older display paths
            "ok": ok,
        }
        flags = self.save.setdefault("flags", {})
        flags["last_check"] = result
        if check.get("success_flag") and ok:
            flags[check["success_flag"]] = True
        elif check.get("success_flag") and not ok:
            flags.setdefault(check["success_flag"], False)
        self.cog.write()
        return result

    @staticmethod
    def _format_check_result(result: dict) -> str:
        mod = int(result.get("mod") or 0)
        d20 = result.get("d20")
        total = result.get("roll")
        if d20 is None:
            d20 = total
        sign = f"+{mod}" if mod >= 0 else str(mod)
        ok = "success" if result.get("ok") else "failure"
        return (
            f"*{result.get('skill', 'Check')} {d20} {sign} = **{total}** "
            f"vs DC {result.get('dc', 12)} — {ok}.*"
        )

    async def pick_choice(self, interaction: discord.Interaction, choice: dict) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        for k, v in (choice.get("set") or {}).items():
            if k == "protagonist":
                self.save["protagonist"] = v
            elif k == "chapter":
                self.save["chapter"] = v
            else:
                self.save.setdefault("flags", {})[k] = v
        if choice.get("check"):
            result = self._apply_check(choice["check"])
            # Keep the roll on the main story message — never a 2nd ephemeral post.
            goto = choice.get("goto")
            if goto in ("sebastian", "ask_library"):
                self.save.setdefault("flags", {})["roll_blurb"] = self._format_check_result(result)
            # wake_* / dream_bridge nodes already render last_check in their text
        await self._advance(choice.get("goto"))
        await self.refresh(interaction)


class ContinueView(discord.ui.View):
    def __init__(self, session: StorySession, auto_goto: Optional[str] = None):
        super().__init__(timeout=600)
        self.session = session
        self.auto_goto = auto_goto

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.primary)
    async def cont(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.continue_page(interaction, auto_goto=self.auto_goto)


class SkillAssignView(discord.ui.View):
    """Spend SKILL_POINTS_TOTAL among Attack / Wisdom / Stealth, then Continue."""

    def __init__(self, session: StorySession, nxt: Optional[str]):
        super().__init__(timeout=600)
        self.session = session
        self.nxt = nxt
        self._busy = False
        # Working copy — saved only on confirm.
        base = session.save.get("abilities") or default_abilities()
        self.abilities = {k: int(base.get(k, 0) or 0) for k, _, _ in ABILITIES}
        # If save already spent points (resume mid-assign), keep them; else start clean.
        if sum(self.abilities.values()) != SKILL_POINTS_TOTAL:
            self.abilities = default_abilities()
        for key, label, _desc in ABILITIES:
            self.add_item(SkillAbilityButton(self, key, label))
        self.confirm_btn = SkillConfirmButton(self)
        self.add_item(self.confirm_btn)
        self._sync()

    def _spent(self) -> int:
        return sum(self.abilities.values())

    def _sync(self) -> None:
        spent = self._spent()
        left = SKILL_POINTS_TOTAL - spent
        for child in self.children:
            if isinstance(child, SkillAbilityButton):
                mod = self.abilities[child.key]
                child.label = f"{child.ability_label} +{mod}"
                child.style = (
                    discord.ButtonStyle.success if mod > 0 else discord.ButtonStyle.secondary
                )
        ready = left == 0
        self.confirm_btn.disabled = not ready
        self.confirm_btn.style = (
            discord.ButtonStyle.primary if ready else discord.ButtonStyle.secondary
        )
        self.confirm_btn.label = (
            f"Continue ({spent}/{SKILL_POINTS_TOTAL})"
            if not ready
            else f"Continue with these ({spent})"
        )

    async def bump(self, interaction: discord.Interaction, key: str):
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        if self._busy:
            await interaction.response.send_message("One moment…", ephemeral=True)
            return
        cur = self.abilities[key]
        # Tap cycles: add a point until max, then clear that ability back to 0.
        if cur >= SKILL_POINTS_MAX_PER:
            self.abilities[key] = 0
        elif self._spent() >= SKILL_POINTS_TOTAL:
            # No points left — clear this ability so they can reallocate.
            if cur > 0:
                self.abilities[key] = 0
            else:
                await interaction.response.send_message(
                    f"All {SKILL_POINTS_TOTAL} points are spent — tap a green ability to free points.",
                    ephemeral=True,
                )
                return
        else:
            self.abilities[key] = cur + 1
        self._sync()
        await interaction.response.edit_message(view=self)
        self.session.message = interaction.message

    async def confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        if self._busy:
            await interaction.response.send_message("One moment…", ephemeral=True)
            return
        if self._spent() != SKILL_POINTS_TOTAL:
            await interaction.response.send_message(
                f"Spend all {SKILL_POINTS_TOTAL} points, then Continue.",
                ephemeral=True,
            )
            return
        for key, mod in self.abilities.items():
            if mod < 0 or mod > SKILL_POINTS_MAX_PER:
                await interaction.response.send_message(
                    f"Max +{SKILL_POINTS_MAX_PER} in any ability.",
                    ephemeral=True,
                )
                return
        self._busy = True
        self.session.save["abilities"] = dict(self.abilities)
        self.session.cog.write()
        await self.session._advance(self.nxt)
        await self.session.refresh(interaction)


class SkillAbilityButton(discord.ui.Button):
    def __init__(self, parent: SkillAssignView, key: str, label: str):
        super().__init__(label=f"{label} +0", style=discord.ButtonStyle.secondary)
        self.parent_view = parent
        self.key = key
        self.ability_label = label

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.bump(interaction, self.key)


class SkillConfirmButton(discord.ui.Button):
    def __init__(self, parent: SkillAssignView):
        super().__init__(
            label=f"Continue (0/{SKILL_POINTS_TOTAL})",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=4,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.confirm(interaction)


class CheckRollView(discord.ui.View):
    """Single d20 roll (e.g. dream Wisdom save)."""

    def __init__(self, session: StorySession, check: dict, nxt: Optional[str]):
        super().__init__(timeout=600)
        self.session = session
        self.check = check or {}
        self.nxt = nxt
        label = (check.get("button") or "Roll")[:80]
        self.add_item(CheckRollButton(self, label))

    async def do_roll(self, interaction: discord.Interaction):
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        # Edit the same public story message in place — never a 2nd/ephemeral post.
        self.session._apply_check(self.check)
        if self.nxt:
            await self.session._advance(self.nxt)
        await self.session.refresh(interaction)


class CheckRollButton(discord.ui.Button):
    def __init__(self, parent: CheckRollView, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.do_roll(interaction)


class ChoiceView(discord.ui.View):
    def __init__(self, session: StorySession, choices: list[dict]):
        super().__init__(timeout=600)
        self.session = session
        for ch in choices[:5]:
            self.add_item(ChoiceButton(session, ch))


class ChoiceButton(discord.ui.Button):
    def __init__(self, session: StorySession, choice: dict):
        super().__init__(label=choice["label"][:80], style=discord.ButtonStyle.secondary)
        self.session = session
        self.choice = choice

    async def callback(self, interaction: discord.Interaction):
        await self.session.pick_choice(interaction, self.choice)


class MaterialPickView(discord.ui.View):
    """Toggle items, then press Continue once you have exactly `need` picks."""

    def __init__(self, session: StorySession, options: list[tuple], need: int, flag_key: str, nxt: str):
        super().__init__(timeout=600)
        self.session = session
        self.options = {o[0]: o for o in options}
        self.need = need
        self.flag_key = flag_key
        self.nxt = nxt
        self.picked: list[str] = []
        self._busy = False
        for key, label, _desc in options:
            self.add_item(MaterialButton(self, key, label))
        self.confirm_btn = MaterialConfirmButton(self)
        self.add_item(self.confirm_btn)
        self._sync()

    def _sync(self) -> None:
        for child in self.children:
            if isinstance(child, MaterialButton):
                child.style = (
                    discord.ButtonStyle.success
                    if child.key in self.picked
                    else discord.ButtonStyle.secondary
                )
        n = len(self.picked)
        ready = n == self.need
        self.confirm_btn.disabled = not ready
        self.confirm_btn.style = (
            discord.ButtonStyle.primary if ready else discord.ButtonStyle.secondary
        )
        self.confirm_btn.label = (
            f"Continue ({n}/{self.need})" if not ready else f"Continue with these ({n})"
        )

    async def toggle(self, interaction: discord.Interaction, key: str):
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        if self._busy:
            await interaction.response.send_message("One moment…", ephemeral=True)
            return
        if key in self.picked:
            self.picked.remove(key)
        else:
            if len(self.picked) >= self.need:
                await interaction.response.send_message(
                    f"Pick {self.need} only — tap a green one to unselect, then Continue.",
                    ephemeral=True,
                )
                return
            self.picked.append(key)
        self._sync()
        await interaction.response.edit_message(view=self)
        self.session.message = interaction.message

    async def confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        if self._busy:
            await interaction.response.send_message("One moment…", ephemeral=True)
            return
        if len(self.picked) != self.need:
            await interaction.response.send_message(
                f"Pick exactly {self.need}, then press Continue.", ephemeral=True
            )
            return
        self._busy = True
        self.session.save[self.flag_key] = list(self.picked)
        self.session.cog.write()
        await self.session._advance(self.nxt)
        await self.session.refresh(interaction)


class MaterialButton(discord.ui.Button):
    def __init__(self, parent: MaterialPickView, key: str, label: str):
        super().__init__(label=label[:80], style=discord.ButtonStyle.secondary)
        self.parent_view = parent
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.toggle(interaction, self.key)


class MaterialConfirmButton(discord.ui.Button):
    def __init__(self, parent: MaterialPickView):
        super().__init__(
            label="Continue (0/0)",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=4,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.confirm(interaction)


class SneakView(discord.ui.View):
    """3 rounds of Wait / Move / Distract. Need 2+ good picks."""

    GOOD = {
        0: "wait",   # footsteps pass
        1: "distract",  # portrait cough
        2: "move",  # gap in patrol
    }

    def __init__(self, session: StorySession):
        super().__init__(timeout=600)
        self.session = session
        session.save["sneak_round"] = 0
        session.save["sneak_score"] = 0
        session.cog.write()
        self._label()

    def _label(self):
        r = self.session.save.get("sneak_round", 0) + 1
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                pass
        # rebuild buttons each round via edit - fixed three buttons

    @discord.ui.button(label="Wait", style=discord.ButtonStyle.secondary)
    async def wait(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "wait")

    @discord.ui.button(label="Move", style=discord.ButtonStyle.primary)
    async def move(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "move")

    @discord.ui.button(label="Distract", style=discord.ButtonStyle.secondary)
    async def distract(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "distract")

    async def _pick(self, interaction: discord.Interaction, action: str):
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        save = self.session.save
        r = save.get("sneak_round", 0)
        if action == self.GOOD.get(r):
            save["sneak_score"] = save.get("sneak_score", 0) + 1
            tip = "Clear."
        else:
            tip = "Close — the dark almost notices you."
        save["sneak_round"] = r + 1
        self.session.cog.write()
        if save["sneak_round"] >= 3:
            # Stealth bonus counts as free progress toward escaping.
            ste = ability_mod(save.get("abilities"), "Stealth")
            effective = save.get("sneak_score", 0) + ste
            caught = effective < 2
            save["gus_with_party"] = not caught
            save.setdefault("flags", {})["sneak_caught"] = caught
            save.setdefault("flags", {})["sneak_stealth"] = ste
            await self.session._advance("sneak_done")
            await self.session.refresh(interaction)
        else:
            # Edit the same story message so progress isn't wiped by a 2nd post.
            await interaction.response.edit_message(
                content=None,
                embed=discord.Embed(
                    title="The Forrest of Caden — Chapter 1",
                    description=(
                        f"**Sneak past Mordy** — round {save['sneak_round']}/3\n\n"
                        f"{tip}\n\n"
                        f"*Score {save.get('sneak_score', 0)} clear so far"
                        f" · Stealth +{ability_mod(save.get('abilities'), 'Stealth')}*"
                    ),
                    color=EMBED_COLOR,
                ),
                view=self,
                attachments=[],
            )
            self.session.message = interaction.message


class HexCounterView(discord.ui.View):
    """Attack mini-game: counter a hex before the timer. Reused for all Attack fights."""

    def __init__(self, session: StorySession):
        super().__init__(timeout=120)
        self.session = session
        self.with_gus = bool(session.save.get("gus_with_party", True))
        self.attack = ability_mod(session.save.get("abilities"), "Attack")
        self.seconds = hex_seconds(self.with_gus)
        self.n_options = hex_option_count(self.attack)
        self.blocks = 0
        self.hits = 0
        self.round_i = 0
        self.current: Optional[dict] = None
        self.correct: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._round_open = False
        self._started = False
        self._finished = False
        self.message: Optional[discord.Message] = None
        # Placeholder until begin() builds the first hex row.
        self.add_item(
            discord.ui.Button(
                label="Brace…",
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
        )

    async def begin(self, message: discord.Message) -> None:
        if self._started or self._finished:
            return
        self._started = True
        self.message = message
        self.session.message = message
        await self._start_round()

    def _cancel_timer(self) -> None:
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()

    async def _start_round(self) -> None:
        if self._finished:
            return
        self.round_i += 1
        pair = random.choice(HEX_PAIRS)
        self.current = pair
        self.correct = pair["counter"]
        wrongs = [s for s in HEX_SPELL_POOL if s != self.correct]
        opts = [self.correct] + random.sample(wrongs, max(0, self.n_options - 1))
        random.shuffle(opts)

        self.clear_items()
        for spell in opts:
            self.add_item(HexSpellButton(self, spell))

        self._round_open = True
        await self._paint(
            (
                f"**Hex incoming — round {self.round_i}/{HEX_ROUNDS_MAX}**\n\n"
                f"The thief snaps **{pair['hex']}** at you.\n\n"
                f"Counter it. **{self.seconds} seconds.**\n"
                f"*{self.n_options} spells"
                + (f" · Attack +{self.attack}" if self.attack else "")
                + (" · Gus steadies your aim" if self.with_gus else " · no Gus — faster")
                + f" · score {self.blocks}–{self.hits}*"
            )
        )
        self._cancel_timer()
        self._task = asyncio.create_task(self._watchdog())

    async def _watchdog(self) -> None:
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if not self._round_open or self._finished:
                return
            self._round_open = False
            await self._resolve(None)

    async def pick(self, interaction: discord.Interaction, spell: str) -> None:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        async with self._lock:
            if not self._round_open or self._finished:
                if not interaction.response.is_done():
                    await interaction.response.defer()
                return
            self._round_open = False
            self._cancel_timer()
            if not interaction.response.is_done():
                await interaction.response.defer()
            await self._resolve(spell)

    async def _resolve(self, spell: Optional[str]) -> None:
        pair = self.current or {}
        ok = spell is not None and spell == self.correct
        if ok:
            self.blocks += 1
            line = pair.get("block") or "You turn it."
            result = f"✅ **{self.correct}** — {line}"
        else:
            self.hits += 1
            if spell is None:
                result = f"⏱ Too slow. **{pair.get('hex', 'The hex')}** lands. {pair.get('hit', '')}"
            else:
                result = (
                    f"❌ **{spell}** misses the counter. "
                    f"**{pair.get('hex', 'The hex')}** hits. {pair.get('hit', '')}"
                )

        won = self.blocks >= HEX_ROUNDS_TO_WIN
        lost = self.hits >= HEX_ROUNDS_TO_WIN
        done_rounds = self.round_i >= HEX_ROUNDS_MAX
        if won or lost or done_rounds:
            await self._finish(won=(self.blocks > self.hits) or won, bridge=result)
            return

        await self._paint(
            f"{result}\n\n*Breath. Next hex… ({self.blocks}–{self.hits})*"
        )
        await asyncio.sleep(1.1)
        await self._start_round()

    async def _finish(self, *, won: bool, bridge: str) -> None:
        self._finished = True
        self._round_open = False
        self._cancel_timer()
        self.clear_items()
        flags = self.session.save.setdefault("flags", {})
        flags["hex_blocks"] = self.blocks
        flags["hex_hits"] = self.hits
        flags["fight_hard"] = not self.with_gus
        if won:
            flags["city_fight"] = "won"
        else:
            flags["city_fight"] = "barely"
            mats = self.session.save.get("school_materials") or []
            if mats:
                lost_item = mats.pop(0)
                self.session.save["school_materials"] = mats
                flags["lost_item"] = lost_item
        self.session.cog.write()
        await self.session._advance("city_essentials")
        # Redraw essentials on the same public message (no interaction token here).
        try:
            await self.session.refresh(None)
        except Exception:
            log.exception("Hex fight finish refresh failed")

    async def _paint(self, description: str) -> None:
        if self.message is None:
            return
        embed = discord.Embed(
            title="The Forrest of Caden — Chapter 1",
            description=description[:4096],
            color=EMBED_COLOR,
        )
        skills = format_abilities(self.session.save.get("abilities"))
        embed.set_footer(text=f"Mack · looking for Yuna · {skills}")
        try:
            self.message = await self.message.edit(
                content=None, embed=embed, view=self, attachments=[]
            )
            self.session.message = self.message
        except Exception:
            log.exception("HexCounterView paint failed")


class HexSpellButton(discord.ui.Button):
    def __init__(self, parent: HexCounterView, spell: str):
        super().__init__(label=spell[:80], style=discord.ButtonStyle.primary)
        self.parent_view = parent
        self.spell = spell

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.pick(interaction, self.spell)


async def setup(bot: commands.Bot):
    await bot.add_cog(ForrestCaden(bot))
