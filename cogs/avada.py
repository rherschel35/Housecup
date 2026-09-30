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
    "Absolutely not. The paintings just filed a noise complaint.",
    "The Sorting Hat peeks out of a cupboard and whispers, \"try friendship.\"",
    "A house-elf appears, confiscates your wand for \"unsafe theatrics,\" and leaves a biscuit.",
    "Somewhere, a Dementor sighs and mutters, \"amateur hour.\"",
    "The castle lights flicker… then the PA system plays a tiny sad trombone.",
    "Your wand coughs. A single rubber duck falls out. Nobody knows why.",
    "The curse bounces off a nearby suit of armor, which then challenges you to checkers.",
    "Headmasters have been pinged. They are not impressed. They are snacking.",
    "The spell fails because you didn't say \"please.\" Manners, darling.",
    "A peacock from Vashara's crest materializes, judges you, and leaves.",
    "The ghosts form a flash mob and chant \"CHOOSE LIFE\" until you stop.",
    "Your familiar covers its ears. Your broom pretends it doesn't know you.",
    "The curse politely declines and books itself a spa day instead.",
    "A sticky note appears on your forehead: \"Do not curse classmates. − Management.\"",
    "The Room of Requirement opens just long enough to throw a pillow at you.",
    "Mordy's socks materialize midair, slap the curse aside, and vanish again.",
    "A first-year in the corridor applauds. That somehow makes it worse.",
    "The curse tries to leave, trips on a stair, and asks for directions to Detention.",
    "Your house points briefly consider leaving you. They stay. Barely.",
    "Pip Wick pops up: \"Aye, that's illegal AND embarrassing. Pocket it.\"",
    "Crucio? The only thing in pain is everyone's patience.",
    "Imperio fails because your target is already doing whatever they want anyway.",
    "Ministry owl arrives with a pamphlet titled \"Have You Tried Talking It Out?\"",
    "A portrait of a founder covers its eyes and pretends this corridor is empty.",
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
