"""
Whenever someone writes an Unforgivable Curse in chat (Avada Kedavra, Crucio,
or Imperio), the bot answers with something ridiculous. Not real spells —
just the castle's sense of humor.
"""

from __future__ import annotations

import logging
import random
import re
import time

import discord
from discord.ext import commands

log = logging.getLogger("velmora.avada")

# Loose matches for the three Unforgivables.
CURSES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bavada[\s\-']*kedavra\b", re.IGNORECASE), "Avada Kedavra"),
    (re.compile(r"\bcrucio\b", re.IGNORECASE), "Crucio"),
    (re.compile(r"\bimperio\b", re.IGNORECASE), "Imperio"),
)

# Per-user cool-down so Unforgivables don't become a spam button.
USER_COOLDOWN = 45.0

LINES = (
    "Your wand immediately snitches on you to the nearest portrait. Loudly.",
    "You cast with full drama… and produce a sad little spark that smells like burnt toast.",
    "Everyone heard that. Including your crush. Especially your crush.",
    "A first-year gasps, then asks if that was for a class project. You die inside.",
    "Your familiar slowly backs away and sits with someone else's friends.",
    "The broom you claimed earlier rotates 180° and pretends you're a stranger.",
    "Pip Wick: \"Mate. In PUBLIC? Absolute menace behavior. Pocket it.\"",
    "A house-elf confiscates your wand, labels it \"THEATRICAL,\" and leaves a juice box.",
    "The ghosts start a slow clap. It is not supportive.",
    "Your house chat lights up with \"who just tried THAT\" before you can delete anything.",
    "You trip over the pronunciation and accidentally compliment someone's shoes instead.",
    "The curse fizzles into confetti. Pink confetti. Your reputation does not recover.",
    "A Headmaster walks past without looking up: \"Detention writes itself, apparently.\"",
    "Mordy's socks slap you gently across the face and leave. No notes. No mercy.",
    "Your patronus appears just long enough to facepalm, then vanishes in shame.",
    "Someone screenshots the channel. Forever.",
    "The Sorting Hat yells from a cupboard: \"I TOLD THEM ABOUT THIS ONE.\"",
    "You get a Howler mid-cast that just screams \"TOUCH GRASS\" for thirty seconds.",
    "A suit of armor hands you a participation sticker that says \"Almost Cool.\"",
    "Your satchel dumps its contents in protest. A single lonely button rolls away.",
    "Crucio? The only thing suffering is the group chat's secondhand embarrassment.",
    "Imperio fails because nobody here takes orders from you on a good day.",
    "Avada Kedavra? Bold of you to main-character this hard in a school hallway.",
    "A peacock from Vashara appears, looks you up and down, and leaves unimpressed.",
    "The curse asks for your student ID, checks the list, and declines service.",
    "You meant to sound terrifying. You sounded like a ringtone from 2009.",
    "A portrait of a founder mouths \"cringe\" and draws the curtain.",
    "Your duel title briefly changes to \"Needs Supervision\" in everyone's mind.",
    "The Room of Requirement opens, throws a blanket over your head, and locks again.",
    "Somewhere, a Dementor is laughing. That's somehow worse than being attacked.",
)


def _matched_curse(content: str) -> str | None:
    for pattern, name in CURSES:
        if pattern.search(content):
            return name
    return None


class AvadaBanter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldowns: dict[int, float] = {}
        self.rng = random.Random()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        content = message.content or ""
        curse = _matched_curse(content)
        if not curse:
            return

        now = time.time()
        ready_at = self._cooldowns.get(message.author.id, 0.0)
        if now < ready_at:
            return
        self._cooldowns[message.author.id] = now + USER_COOLDOWN

        line = self.rng.choice(LINES)
        try:
            await message.reply(
                f"☠️ **{curse}?**\n{line}",
                mention_author=False,
            )
        except discord.HTTPException:
            try:
                await message.channel.send(
                    f"☠️ **{curse}?** ({message.author.display_name})\n{line}"
                )
            except discord.DiscordException:
                log.exception("Could not deliver Unforgivable banter")


async def setup(bot: commands.Bot):
    await bot.add_cog(AvadaBanter(bot))
