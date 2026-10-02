"""
The Forrest of Caden — solo Telltale-style story (staff test: Ch 1–2).

    /forrest start   — (staff) begin or restart Chapters 1–2
    /forrest resume  — (staff) continue your run
    /forrest status  — (staff) flags / chapter
    /forrest reset   — (staff) clear your save

No house points. Owner-locked buttons. Art from story_art_assets/.
"""

from __future__ import annotations

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
    ART,
    CITY_ESSENTIALS_2,
    CITY_ESSENTIALS_3,
    NODES,
    SCHOOL_MATERIALS,
    pronouns,
    render,
)

log = logging.getLogger("velmora.forrest")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "forrest_caden_state.json"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "story_art_assets"

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
    """Staff-only test of The Forrest of Caden, chapters 1–2."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = _load()
        self._sessions: dict[int, "StorySession"] = {}

    def _staff(self, user: discord.abc.User) -> bool:
        store = self.bot.get_cog("Store")
        if store and hasattr(user, "guild_permissions"):
            return bool(store.is_staff(user))
        return False

    def get_save(self, user_id: int) -> dict:
        players = self.state.setdefault("players", {})
        key = str(user_id)
        if key not in players:
            players[key] = _default_save()
        return players[key]

    def write(self) -> None:
        _save(self.state)

    # ---------------------------------------------------------------- commands

    forrest = app_commands.Group(
        name="forrest",
        description="(staff test) The Forrest of Caden — solo story, Ch 1–2.",
    )

    @forrest.command(name="start", description="(staff) Start or restart The Forrest of Caden (Ch 1–2).")
    async def forrest_start(self, interaction: discord.Interaction):
        if not self._staff(interaction.user):
            await interaction.response.send_message(
                "Staff-only while this story is in test.", ephemeral=True
            )
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

    @forrest.command(name="resume", description="(staff) Resume your Forrest of Caden run.")
    async def forrest_resume(self, interaction: discord.Interaction):
        if not self._staff(interaction.user):
            await interaction.response.send_message(
                "Staff-only while this story is in test.", ephemeral=True
            )
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

    @forrest.command(name="status", description="(staff) Your Forrest flags and chapter.")
    async def forrest_status(self, interaction: discord.Interaction):
        if not self._staff(interaction.user):
            await interaction.response.send_message(
                "Staff-only while this story is in test.", ephemeral=True
            )
            return
        save = self.get_save(interaction.user.id)
        flags = save.get("flags", {})
        lines = [
            f"**Node:** `{save.get('node')}` (page {save.get('page', 0)})",
            f"**Chapter:** {save.get('chapter', 1)}",
            f"**Protagonist:** {save.get('protagonist') or '—'}",
            f"**Gus with party:** {save.get('gus_with_party', True)}",
            f"**School kit:** {', '.join(save.get('school_materials') or []) or '—'}",
            f"**City kit:** {', '.join(save.get('city_essentials') or []) or '—'}",
            f"**Flags:** `{json.dumps(flags, ensure_ascii=False)}`",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @forrest.command(name="reset", description="(staff) Clear your Forrest save.")
    async def forrest_reset(self, interaction: discord.Interaction):
        if not self._staff(interaction.user):
            await interaction.response.send_message(
                "Staff-only while this story is in test.", ephemeral=True
            )
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
        # dynamic pages
        if nid == "sneak_done":
            return self._sneak_done_node()
        if nid == "train":
            return self._train_node()
        if nid == "gus_ella_talk":
            return self._gus_talk_node()
        if nid == "morning":
            return self._morning_node()
        return node

    def _sneak_done_node(self) -> dict:
        if self.save.get("gus_with_party", True):
            pages = [
                "You taste cold air that isn’t castle air. For one stupid second you could cry with relief. "
                "Gus elbows you, whispering something dumb so none of you have to admit how hard your hearts are going.\n\n"
                "All three of you made it out."
            ]
            empath = (
                "Freedom feels illegal. You keep waiting for the castle to yank you back by the collar."
            )
        else:
            pages = [
                "Mordy’s voice stops the night cold. Gus steps forward before you can — glasses crooked, chin up, "
                "already volunteering to be the problem.\n\n"
                "**Gus:** mouths *Go.*\n\n"
                "Ella’s hand finds your sleeve and pulls. Leaving him feels like biting through your own tongue. "
                "He says he’ll catch up. You want to believe him the way you used to believe summer was endless."
            ]
            empath = (
                "Loyalty has a sound when it breaks a little. It’s quiet. It follows you out into the dark."
            )
        return {"art": "sneak", "pages": pages, "empath": empath, "goto": "city_arrive"}

    def _train_node(self) -> dict:
        p = self.p()
        if self.save.get("gus_with_party", True):
            pages = [
                render(
                    "The train toward Caden is half-empty. Ella sits by the window. Gus invents a bit about the "
                    "robbers having terrible taste in alleyways until even Ella snorts.\n\n"
                    "**Ella:** quieter, when he goes looking for water — “We’re doing what’s right. If something’s "
                    "wrong out there… I trust you.”\n\n"
                    "Gus comes back mid-sentence with snacks and a joke that lands soft. The heaviness thins. "
                    "Ella doesn’t reach for your hand. She doesn’t need to, not with him filling the air.\n\n"
                    "Outside, the hills darken toward the Forrest.",
                    p,
                )
            ]
            self.save.setdefault("flags", {})["ella_hand"] = False
            art = "train_trio"
        else:
            pages = [
                render(
                    "The train toward Caden is half-empty. Ella sits by the window. Without Gus, the quiet has room "
                    "to mean things.\n\n"
                    "**Ella:** “We’re doing what’s right. If something’s wrong out there… I trust you.”\n\n"
                    "It isn’t a speech. It’s heavier than one. You feel the weight of it in your ribs — her faith, "
                    "your fear, the empty seat where Gus should be making this easier.\n\n"
                    "She takes your hand.\n"
                    "Not asking. Not performing. Just *here*.\n\n"
                    "The cart jolts at a crossing. Boots hit the step. Gus — breathless, glasses fogged, bag half-zipped — "
                    "hauls himself in before the door shuts.\n\n"
                    "He sees.\n\n"
                    "For a second nobody speaks. Then he laughs wrong and sits across from you both like nothing "
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

    def _gus_talk_node(self) -> dict:
        flags = self.save.setdefault("flags", {})
        if flags.get("gus_saw_hand"):
            pages = [
                "**Gus:** doesn’t joke first. That’s how you know.\n\n"
                "“I saw. On the train. Her hand.” He stares at the floorboards. “I’m not mad at you. I don’t think. "
                "I’m mad at the timing. At me for not being there. At… yeah.”\n\n"
                "He asks if you told her to — you didn’t. He asks if you like her that way — you don’t, and saying it "
                "out loud feels like putting down something sharp carefully.\n\n"
                "**Gus:** “Okay. Then I still… I still want to try. Or I don’t. You told me "
                + ("to tell her." if flags.get("gus_advice") == "tell" else "something about her.")
                + " I’m remembering that. Just— don’t let me be the last to know if the world tilts again.”"
            ]
            empath = "Serious tone. His jealousy isn’t ugly — it’s scared. You feel it and don’t flinch away."
            flags["gus_talk"] = "serious"
        else:
            pages = [
                "**Gus:** paces once, then sits.\n\n"
                "“She’s been laughing more. With me. Or near me. I can’t tell which and it’s killing me in a "
                "stupid way.” He grins, small. “You were right there the whole time and somehow I still need a referee.”\n\n"
                "You talk. Not forever. Long enough that the lamp burns lower.\n\n"
                "**Gus:** “Tomorrow we find them. Tonight I pretend I’m brave about Ella. Deal?”"
            ]
            empath = "Lighter. Hopeful. The crush is still loud — just not bleeding."
            flags["gus_talk"] = "light"
        # surface advice flag for later
        if "gus_advice" not in flags and self.save.get("flags", {}).get("gus_advice"):
            pass
        # copy from top-level if stored via set on choices - we store in flags via set handler
        self.cog.write()
        return {"art": "inn", "pages": pages, "empath": empath, "goto": "morning"}

    def _morning_node(self) -> dict:
        p = self.p()
        if self.save.get("flags", {}).get("has_nox"):
            body = (
                "Supply check at dawn: water, what you bought, what you didn’t lose to robbers. "
                "Nox waits by the door like the day already belongs to him.\n\n"
                "You step toward the tree line. He follows without being asked."
            )
        elif self.save.get("flags", {}).get("cave_tip"):
            body = (
                "Supply check at dawn: water, what you bought, what you didn’t lose to robbers.\n\n"
                "You write it once in the margin of the trail map, small, so you won’t pretend you forgot:\n"
                "**Don’t sleep in the cave.**"
            )
        else:
            body = (
                "Supply check at dawn: water, what you bought, what you didn’t lose to robbers.\n\n"
                "No dog. No warning. Just the three of you and a map that ends where the green begins."
            )
        pages = [
            render(body + "\n\nThe Forrest of Caden stands waiting. Still no word of {missing}.", p)
        ]
        return {"art": "edge", "pages": pages, "goto": "ch2_end"}

    async def show(self, destination) -> None:
        """destination: interaction.followup or channel."""
        await self._render(destination, edit=False)

    async def refresh(self) -> None:
        if self.message:
            await self._render(self.message, edit=True)

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
        # show empath only on last page of node when no more continue
        more_pages = page_i < len(flat) - 1
        empath = node.get("empath") if not more_pages else None
        if empath and save.get("protagonist"):
            text = text + "\n\n*" + render(empath, self.p()) + "*"

        title = "The Forrest of Caden"
        if save.get("chapter") == 2:
            title += " — Chapter 2"
        else:
            title += " — Chapter 1"

        embed = discord.Embed(title=title, description=text[:4096], color=EMBED_COLOR)
        if save.get("protagonist"):
            embed.set_footer(text="Mack · looking for Yuna")

        file = None
        path = _art_path(art_key)
        if path:
            file = discord.File(path, filename=path.name)
            embed.set_image(url=f"attachment://{path.name}")

        # view
        view: discord.ui.View
        if more_pages:
            view = ContinueView(self)
        elif node.get("mini") == "pick_materials":
            view = MaterialPickView(self, SCHOOL_MATERIALS, need=2, flag_key="school_materials", nxt=node.get("goto"))
        elif node.get("mini") == "pick_essentials":
            opts = CITY_ESSENTIALS_3 if save.get("gus_with_party", True) else CITY_ESSENTIALS_2
            need = 3 if save.get("gus_with_party", True) else 2
            view = MaterialPickView(self, opts, need=need, flag_key="city_essentials", nxt=node.get("goto"))
        elif node.get("mini") == "sneak":
            view = SneakView(self)
        elif node.get("mini") == "city_fight":
            view = FightView(self)
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

        if edit and isinstance(destination, discord.Message):
            self.message = await destination.edit(**kwargs)
        elif edit and self.message:
            self.message = await self.message.edit(**kwargs)
        else:
            # followup or channel
            send = destination.send if hasattr(destination, "send") else destination
            self.message = await send(**kwargs)

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
            await interaction.response.defer()
            await self.refresh()
            return
        # finished pages → goto or choices already shown; ContinueView with auto_goto
        nxt = auto_goto or node.get("goto")
        if nxt:
            await self._advance(nxt)
            await interaction.response.defer()
            await self.refresh()
        else:
            await interaction.response.defer()

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
                # also mirror gus_advice at top for status readability
                if k == "gus_advice":
                    self.save["flags"]["gus_advice"] = v
        await self._advance(choice.get("goto"))
        await interaction.response.defer()
        await self.refresh()


class ContinueView(discord.ui.View):
    def __init__(self, session: StorySession, auto_goto: Optional[str] = None):
        super().__init__(timeout=600)
        self.session = session
        self.auto_goto = auto_goto

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.primary)
    async def cont(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.continue_page(interaction, auto_goto=self.auto_goto)


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
    def __init__(self, session: StorySession, options: list[tuple], need: int, flag_key: str, nxt: str):
        super().__init__(timeout=600)
        self.session = session
        self.options = {o[0]: o for o in options}
        self.need = need
        self.flag_key = flag_key
        self.nxt = nxt
        self.picked: list[str] = []
        for key, label, _desc in options:
            self.add_item(MaterialButton(self, key, label))

    async def toggle(self, interaction: discord.Interaction, key: str):
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        if key in self.picked:
            self.picked.remove(key)
        else:
            if len(self.picked) >= self.need:
                await interaction.response.send_message(
                    f"Pick {self.need} only — tap one to unselect.", ephemeral=True
                )
                return
            self.picked.append(key)
        # update button styles
        for child in self.children:
            if isinstance(child, MaterialButton):
                child.style = (
                    discord.ButtonStyle.success
                    if child.key in self.picked
                    else discord.ButtonStyle.secondary
                )
        if len(self.picked) == self.need:
            self.session.save[self.flag_key] = list(self.picked)
            self.cog_write()
            await self.session._advance(self.nxt)
            await interaction.response.defer()
            await self.session.refresh()
        else:
            await interaction.response.edit_message(view=self)

    def cog_write(self):
        self.session.cog.write()


class MaterialButton(discord.ui.Button):
    def __init__(self, parent: MaterialPickView, key: str, label: str):
        super().__init__(label=label[:80], style=discord.ButtonStyle.secondary)
        self.parent_view = parent
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.toggle(interaction, self.key)


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
            caught = save.get("sneak_score", 0) < 2
            save["gus_with_party"] = not caught
            save.setdefault("flags", {})["sneak_caught"] = caught
            await self.session._advance("sneak_done")
            await interaction.response.defer()
            await self.session.refresh()
        else:
            await interaction.response.send_message(
                f"Round {r + 1}: {tip} ({save['sneak_round']}/3)", ephemeral=True
            )


class FightView(discord.ui.View):
    def __init__(self, session: StorySession):
        super().__init__(timeout=600)
        self.session = session
        hard = not session.save.get("gus_with_party", True)
        self.hard = hard
        self.hp = 3 if hard else 4
        self.enemy = 4 if hard else 3

    @discord.ui.button(label="Strike", style=discord.ButtonStyle.danger)
    async def strike(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._round(interaction, "strike")

    @discord.ui.button(label="Guard Ella", style=discord.ButtonStyle.primary)
    async def guard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._round(interaction, "guard")

    @discord.ui.button(label="Shove past", style=discord.ButtonStyle.secondary)
    async def shove(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._round(interaction, "shove")

    async def _round(self, interaction: discord.Interaction, action: str):
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("This isn’t your story.", ephemeral=True)
            return
        # simple resolution
        if action == "strike":
            self.enemy -= 2 if self.session.save.get("gus_with_party", True) else 1
            self.hp -= 1
            line = "You hit hard. They hit back."
        elif action == "guard":
            self.enemy -= 1
            self.hp -= 0 if random.random() < 0.5 else 1
            line = "You cover Ella. The world narrows to fists and breath."
        else:
            self.enemy -= 1
            self.hp -= 1 if self.hard else 0
            line = "You shove through — bags swinging, teeth gritted."

        if self.enemy <= 0:
            self.session.save.setdefault("flags", {})["city_fight"] = "won"
            self.session.save.setdefault("flags", {})["fight_hard"] = self.hard
            await self.session._advance("city_essentials")
            await interaction.response.defer()
            await self.session.refresh()
            return
        if self.hp <= 0:
            # lose items flavor but continue
            mats = self.session.save.get("school_materials") or []
            if mats:
                lost = mats.pop(0)
                self.session.save["school_materials"] = mats
                self.session.save.setdefault("flags", {})["lost_item"] = lost
            self.session.save.setdefault("flags", {})["city_fight"] = "barely"
            await self.session._advance("city_essentials")
            await interaction.response.defer()
            await self.session.refresh()
            return

        await interaction.response.send_message(
            f"{line}\n*You {self.hp} · Them {self.enemy}*"
            + (" · (harder without Gus)" if self.hard else " · (easier with three)"),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ForrestCaden(bot))
