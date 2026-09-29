"""
Pip Wick — hearthling messenger who answers how-to questions.

    /piphowdoi question:<text>  - keyword match against help + Compendium FAQ

Replies are public, posted through a per-channel webhook so the message
shows as **Pip Wick** with his portrait (not the main bot identity).
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.pip_wick")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
KNOWLEDGE_PATH = DATA_DIR / "pip_knowledge.json"
AVATAR_PATH = Path(__file__).resolve().parent.parent / "pip_wick_assets" / "pip_wick.jpg"

PIP_NAME = "Pip Wick"
PIP_WEBHOOK_NAME = "Pip Wick"
COMPENDIUM_HOME = "https://everything-velmora-andmore.netlify.app"

# Soft cool-down so Pip isn't summoned every two seconds.
USER_COOLDOWN = 8.0
MIN_SCORE = 12

NO_MATCH = (
    "Hrmm. Nothin' in the letters for that one. "
    "Try `/help`, peek the [Compendium]({home}) — "
    "or tag a **Headmaster** an' they'll sort ye out."
).format(home=COMPENDIUM_HOME)

OPENERS = (
    "Aye — dug this out o' the satchel for ye.",
    "Right then — found a letter that fits.",
    "Hold a tick… aye, this'll sort ye.",
)


def _norm(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s'/:+-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


# Everyday wording → Pip's keyword vocabulary (applied to the question).
_SYNONYMS = {
    "learn": "study",
    "learning": "study",
    "learnt": "study",
    "learned": "study",
    "creature": "beast",
    "creatures": "beasts",
    "critter": "beast",
    "critters": "beasts",
}


def _expand_query(text: str) -> str:
    """Normalize and fold synonyms so 'learn' matches 'study', etc."""
    q = _norm(text)
    if not q:
        return q
    parts = []
    for word in q.split():
        parts.append(_SYNONYMS.get(word, word))
    expanded = " ".join(parts)
    # Keep original words too so exact keyword phrases still hit.
    if expanded != q:
        return f"{q} {expanded}"
    return q


def _load_knowledge() -> dict:
    try:
        return json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    except Exception:
        log.exception("Failed to load Pip knowledge at %s", KNOWLEDGE_PATH)
        return {"compendium": COMPENDIUM_HOME, "entries": []}


def _score(query: str, entry: dict) -> int:
    """Higher is better. Longer keyword hits weigh more."""
    q = _expand_query(query)
    if not q:
        return 0
    best = 0
    total = 0
    for raw in entry.get("keywords", []):
        kw = _norm(raw)
        if not kw:
            continue
        if kw in q:
            hit = 10 + min(len(kw), 24)
            total += hit
            best = max(best, hit)
            continue
        # loose word overlap for multi-word keywords
        words = [w for w in kw.split() if len(w) > 2]
        if len(words) >= 2 and all(w in q for w in words):
            hit = 8 + len(words) * 2
            total += hit
            best = max(best, hit)
    # Prefer a strong single hit; add a little for multiple hits.
    return best + min(total - best, 20) // 2


def _pick_opener(query: str) -> str:
    # Stable-ish variety without RNG dependency on global state.
    idx = sum(ord(c) for c in query) % len(OPENERS)
    return OPENERS[idx]


def format_answer(entry: dict, question: str) -> str:
    body = entry.get("answer", "").strip()
    link = entry.get("link") or COMPENDIUM_HOME
    opener = _pick_opener(question)
    lines = [opener, "", body, "", f"*From Pip's letters · [Compendium]({link})*"]
    return "\n".join(lines)[:2000]


class PipWick(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.knowledge = _load_knowledge()
        self._webhooks: dict[int, discord.Webhook] = {}
        self._avatar_bytes: Optional[bytes] = None
        self._cooldowns: dict[int, float] = {}
        if AVATAR_PATH.is_file():
            try:
                self._avatar_bytes = AVATAR_PATH.read_bytes()
            except OSError:
                log.exception("Could not read Pip avatar at %s", AVATAR_PATH)

    def reload_knowledge(self) -> None:
        self.knowledge = _load_knowledge()

    def match(self, question: str) -> Optional[dict]:
        entries = self.knowledge.get("entries") or []
        scored = [( _score(question, e), e) for e in entries]
        scored = [(s, e) for s, e in scored if s >= MIN_SCORE]
        if not scored:
            return None
        scored.sort(key=lambda pair: (-pair[0], pair[1].get("id", "")))
        return scored[0][1]

    async def _pip_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        cached = self._webhooks.get(channel.id)
        if cached:
            return cached
        try:
            hooks = await channel.webhooks()
            hook = next((h for h in hooks if h.name == PIP_WEBHOOK_NAME), None)
            if hook is None:
                hook = await channel.create_webhook(
                    name=PIP_WEBHOOK_NAME,
                    avatar=self._avatar_bytes,
                    reason="Pip Wick how-do-I replies",
                )
            elif self._avatar_bytes:
                # Keep portrait fresh if staff deleted/recreated without art.
                try:
                    await hook.edit(avatar=self._avatar_bytes, reason="Pip Wick avatar refresh")
                except discord.DiscordException:
                    pass
            self._webhooks[channel.id] = hook
            return hook
        except discord.DiscordException:
            log.exception("Could not get/create Pip webhook in #%s", getattr(channel, "name", channel.id))
            return None

    async def _speak_as_pip(self, channel: discord.abc.Messageable, content: str) -> bool:
        if not isinstance(channel, discord.TextChannel):
            return False
        hook = await self._pip_webhook(channel)
        if hook is None:
            return False
        try:
            # Username + webhook avatar → shows as Pip Wick in the channel.
            await hook.send(content=content[:2000], username=PIP_NAME, wait=True)
            return True
        except discord.DiscordException:
            # Stale cache — drop and retry once.
            self._webhooks.pop(channel.id, None)
            hook = await self._pip_webhook(channel)
            if hook is None:
                return False
            try:
                await hook.send(content=content[:2000], username=PIP_NAME, wait=True)
                return True
            except discord.DiscordException:
                log.exception("Pip webhook send failed in #%s", channel.name)
                return False

    @app_commands.command(
        name="piphowdoi",
        description="Ask Pip Wick how to do something in Velmora (public answer).",
    )
    @app_commands.describe(question="What do you want to know? e.g. study a beast, get a wand")
    async def piphowdoi(self, interaction: discord.Interaction, question: str):
        question = (question or "").strip()
        if not question:
            await interaction.response.send_message(
                "Give Pip a lil' question to dig for — e.g. `study a beast`.",
                ephemeral=True,
            )
            return

        now = time.time()
        ready_at = self._cooldowns.get(interaction.user.id, 0.0)
        if now < ready_at:
            wait = int(ready_at - now) + 1
            await interaction.response.send_message(
                f"Pip's still tyin' his satchel — try again in **{wait}s**.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        self._cooldowns[interaction.user.id] = now + USER_COOLDOWN

        entry = self.match(question)
        content = format_answer(entry, question) if entry else NO_MATCH

        # Credit the asker so the public thread stays readable.
        header = f"*asked by {interaction.user.display_name}:* _{question[:180]}_"
        public = f"{header}\n\n{content}"[:2000]

        ok = await self._speak_as_pip(interaction.channel, public)
        if ok:
            await interaction.followup.send(
                "Pip's dug up an answer in the channel for ye ↓",
                ephemeral=True,
            )
            return

        # Fallback if webhooks aren't allowed — post publicly in-channel
        # (deferred followups stay ephemeral, so use channel.send).
        embed = discord.Embed(description=content, color=0x3E5C8A)
        try:
            if AVATAR_PATH.is_file():
                file = discord.File(AVATAR_PATH, filename="pip_wick.jpg")
                embed.set_author(name=PIP_NAME, icon_url="attachment://pip_wick.jpg")
                await interaction.channel.send(content=header, embed=embed, file=file)
            else:
                embed.set_author(name=PIP_NAME)
                await interaction.channel.send(content=header, embed=embed)
            await interaction.followup.send(
                "Pip answered in the channel (webhook unavailable in this channel).",
                ephemeral=True,
            )
        except discord.DiscordException:
            log.exception("Pip fallback send failed")
            await interaction.followup.send(
                "Couldn't post Pip's answer here — check channel permissions "
                "(Manage Webhooks / Send Messages).",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(PipWick(bot))
