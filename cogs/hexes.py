"""
Headmaster hexes - a prank spell a Headmaster can cast on a student so that,
without warning, whatever they type comes out cursed.

    /hex member:<@user> effect:<pick one> duration:<minutes>   - cast it
    /unhex member:<@user>                                      - lift it early
    /hexlist                                                   - who's currently hexed

Mechanically: a bot can't rewrite someone else's sent message, so this
deletes the cursed member's message the instant it lands and reposts it
through a per-channel webhook wearing their name and avatar, with the text
mangled. To everyone watching, it looks like their own words just came out
wrong - there's no "a bot did this" tell.

Needs the bot to hold Manage Messages (to delete the original) and Manage
Webhooks (to create/reuse the relay webhook) in this server.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("velmora.hexes")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "hexes_state.json"

HEADMASTER_ROLE_NAME = "headmasters"  # matched case-insensitively; rename here if the role's name changes
RELAY_WEBHOOK_NAME = "Velmora Hex Relay"

WORD = re.compile(r"[A-Za-z']+")
WORD_WITH_EDGES = re.compile(r"^([^a-zA-Z]*)([a-zA-Z]+)([^a-zA-Z]*)$")
VOWELS = "aeiouAEIOU"

NONSENSE_WORDS = ["blorp", "fwibble", "quonk", "zamble", "hobnock", "skarn", "wibbet", "glorm", "nizzle", "florp"]

OLDE_WIZARD_MAP = {
    "you": "thou", "your": "thine", "yours": "thine own", "you're": "thou art",
    "hello": "hark", "hi": "hark", "hey": "hark thee",
    "is": "art", "are": "art", "am": "art",
    "my": "mine own", "me": "mine own self", "i": "I, verily,",
    "yes": "aye", "no": "nay", "really": "verily", "very": "most exceedingly",
    "cool": "most excellent", "okay": "very well", "ok": "very well",
    "please": "prithee", "sorry": "I beg thy pardon",
}
FORMALITIS_MAP = {
    "hey": "greetings and salutations", "hi": "good day to you", "hello": "good day to you",
    "yeah": "indubitably", "yes": "indeed", "yep": "quite so",
    "lol": "how terribly droll", "lmao": "a most amusing turn of events",
    "no": "regrettably, no", "nah": "regrettably, no",
    "cool": "most satisfactory", "nice": "most satisfactory",
    "gonna": "shall presently", "wanna": "should very much like to", "gotta": "must, with some urgency,",
    "dude": "esteemed colleague", "bro": "esteemed colleague", "man": "esteemed colleague",
    "thanks": "you have my utmost gratitude", "thx": "you have my utmost gratitude",
    "ok": "very well", "okay": "very well", "sup": "how do you fare",
}
PIRATES_TONGUE_MAP = {
    "you": "ye", "your": "yer", "you're": "ye be", "yours": "yer own",
    "my": "me", "is": "be", "are": "be", "am": "be",
    "the": "th'", "yes": "aye", "hello": "ahoy", "hi": "ahoy", "hey": "ahoy",
    "friend": "matey", "friends": "mateys", "stop": "avast", "money": "doubloons",
    "drink": "grog", "food": "grub", "boat": "ship", "car": "ship",
    "no": "nay", "okay": "aye aye", "ok": "aye aye",
}


def _split_word(word: str) -> Optional[tuple[str, str, str]]:
    m = WORD_WITH_EDGES.match(word)
    return m.groups() if m else None  # (leading punctuation, core letters, trailing punctuation)


def _match_case(core: str, replacement: str) -> str:
    if core.isupper():
        return replacement.upper()
    if core[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def _word_swap(text: str, mapping: dict) -> str:
    def repl(m: re.Match) -> str:
        w = m.group(0)
        rep = mapping.get(w.lower())
        if rep is None:
            return w
        return _match_case(w, rep)
    return WORD.sub(repl, text)


def fx_reversed(text: str) -> str:
    return text[::-1]


def fx_vowelless(text: str) -> str:
    return re.sub(r"[aeiouAEIOU]", "", text)


def fx_piglatin(text: str) -> str:
    def repl(m: re.Match) -> str:
        parts = _split_word(m.group(0))
        if not parts:
            return m.group(0)
        lead, core, trail = parts
        if core[0] in VOWELS:
            new = core + "way"
        else:
            i = 0
            while i < len(core) and core[i] not in VOWELS:
                i += 1
            new = core[i:] + core[:i] + "ay"
        return lead + _match_case(core, new) + trail
    return re.sub(r"[A-Za-z]+", repl, text)


def fx_shout(text: str) -> str:
    return "".join(c.upper() if random.random() < 0.5 else c.lower() for c in text)


def fx_stutter(text: str) -> str:
    def repl(m: re.Match) -> str:
        parts = _split_word(m.group(0))
        if not parts or random.random() > 0.35:
            return m.group(0)
        lead, core, trail = parts
        if len(core) < 2:
            return m.group(0)
        n = 1 if len(core) <= 3 else random.choice([1, 2])
        return f"{lead}{core[:n]}-{core}{trail}"
    return re.sub(r"[A-Za-z]+", repl, text)


def fx_babbling(text: str) -> str:
    def repl(m: re.Match) -> str:
        parts = _split_word(m.group(0))
        if not parts or random.random() > 0.3:
            return m.group(0)
        lead, core, trail = parts
        return lead + _match_case(core, random.choice(NONSENSE_WORDS)) + trail
    return re.sub(r"[A-Za-z]+", repl, text)


def fx_olde_wizard(text: str) -> str:
    return _word_swap(text, OLDE_WIZARD_MAP)


def fx_formalitis(text: str) -> str:
    return _word_swap(text, FORMALITIS_MAP)


def fx_pirates_tongue(text: str) -> str:
    text = _word_swap(text, PIRATES_TONGUE_MAP)
    if random.random() < 0.4:
        text = text.rstrip() + random.choice([" arr!", " arrr.", ", arr."])
    return text


EFFECTS = {
    "reversed": {"label": "Reversed", "func": fx_reversed},
    "vowelless": {"label": "Vowel-less", "func": fx_vowelless},
    "piglatin": {"label": "Pig Latin", "func": fx_piglatin},
    "shout": {"label": "Shout (random caps)", "func": fx_shout},
    "stutter": {"label": "Stutter", "func": fx_stutter},
    "babbling": {"label": "Babbling Curse", "func": fx_babbling},
    "olde_wizard": {"label": "Ye Olde Wizard", "func": fx_olde_wizard},
    "formalitis": {"label": "Formal-itis", "func": fx_formalitis},
    "pirates_tongue": {"label": "Pirate's Tongue", "func": fx_pirates_tongue},
}


class Hexes(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self._webhooks: dict[int, discord.Webhook] = {}  # channel_id -> cached relay webhook

    async def cog_load(self):
        self.expiry_sweep.start()

    async def cog_unload(self):
        self.expiry_sweep.cancel()

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read %s", STATE_PATH)
            state = {}
        state.setdefault("hexed", {})
        return state

    def save(self):
        tmp = STATE_PATH.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save %s", STATE_PATH)

    # -------------------------------------------------------- permissions

    @staticmethod
    def _is_headmaster(member: discord.Member) -> bool:
        perms = getattr(member, "guild_permissions", None)
        if perms is not None and (perms.administrator or perms.manage_guild):
            return True
        return any(r.name.lower() == HEADMASTER_ROLE_NAME for r in getattr(member, "roles", []))

    # ------------------------------------------------------------ casting

    @app_commands.command(name="hex", description="(Headmaster) Curse a student's messages with a prank spell.")
    @app_commands.describe(member="Who to hex", effect="Which curse to cast",
                           duration="How many minutes it lasts (0 = until lifted)")
    @app_commands.choices(effect=[app_commands.Choice(name=v["label"], value=k) for k, v in EFFECTS.items()])
    async def hex(self, interaction: discord.Interaction, member: discord.Member,
                  effect: app_commands.Choice[str], duration: app_commands.Range[int, 0, 10080]):
        if not self._is_headmaster(interaction.user):
            await interaction.response.send_message("Only a Headmaster may cast this.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("You can't hex a bot.", ephemeral=True)
            return

        was_hexed = str(member.id) in self.state["hexed"]
        expires_at = None if duration == 0 else time.time() + duration * 60
        self.state["hexed"][str(member.id)] = {
            "effect": effect.value, "expires_at": expires_at, "cast_by": interaction.user.id,
        }
        self.save()

        replaced_note = " (replacing the curse already on them)" if was_hexed else ""
        until = "until a Headmaster lifts it" if expires_at is None else f"for {duration} minute(s)"
        await interaction.response.send_message(
            f"🪄 {member.mention} has been hexed with **{effect.name}**{replaced_note}, {until}. They have not "
            f"been told.", ephemeral=True)

    @app_commands.command(name="unhex", description="(Headmaster) Lift a hex early.")
    @app_commands.describe(member="Whose hex to lift")
    async def unhex(self, interaction: discord.Interaction, member: discord.Member):
        if not self._is_headmaster(interaction.user):
            await interaction.response.send_message("Only a Headmaster may lift this.", ephemeral=True)
            return
        existed = self.state["hexed"].pop(str(member.id), None)
        self.save()
        if existed:
            await interaction.response.send_message(f"The hex on {member.mention} has been lifted.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{member.mention} isn't currently hexed.", ephemeral=True)

    @app_commands.command(name="hexlist", description="(Headmaster) Show who's currently hexed.")
    async def hexlist(self, interaction: discord.Interaction):
        if not self._is_headmaster(interaction.user):
            await interaction.response.send_message("Only a Headmaster may see this.", ephemeral=True)
            return
        self._purge_expired()
        entries = self.state["hexed"]
        if not entries:
            await interaction.response.send_message("Nobody is currently hexed.", ephemeral=True)
            return
        lines = []
        for uid, rec in entries.items():
            member = interaction.guild.get_member(int(uid))
            name = member.display_name if member else uid
            label = EFFECTS.get(rec["effect"], {}).get("label", rec["effect"])
            if rec["expires_at"] is None:
                remaining = "until lifted"
            else:
                mins_left = max(0, int((rec["expires_at"] - time.time()) // 60))
                remaining = f"~{mins_left} min left"
            lines.append(f"**{name}** — {label} ({remaining})")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # -------------------------------------------------------------- upkeep

    def _purge_expired(self):
        now = time.time()
        stale = [uid for uid, rec in self.state["hexed"].items()
                if rec["expires_at"] is not None and rec["expires_at"] <= now]
        for uid in stale:
            self.state["hexed"].pop(uid, None)
        if stale:
            self.save()

    @tasks.loop(minutes=5)
    async def expiry_sweep(self):
        self._purge_expired()

    @expiry_sweep.before_loop
    async def _before_sweep(self):
        await self.bot.wait_until_ready()

    # --------------------------------------------------------- the prank

    async def _relay_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        cached = self._webhooks.get(channel.id)
        if cached:
            return cached
        try:
            hooks = await channel.webhooks()
            hook = next((h for h in hooks if h.name == RELAY_WEBHOOK_NAME), None)
            if hook is None:
                hook = await channel.create_webhook(name=RELAY_WEBHOOK_NAME, reason="Headmaster hex relay")
            self._webhooks[channel.id] = hook
            return hook
        except discord.DiscordException:
            log.exception("Could not get/create the hex relay webhook in #%s", getattr(channel, "name", channel.id))
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        if not message.content or not message.content.strip():
            return

        rec = self.state["hexed"].get(str(message.author.id))
        if not rec:
            return
        if rec["expires_at"] is not None and rec["expires_at"] <= time.time():
            self.state["hexed"].pop(str(message.author.id), None)
            self.save()
            return

        effect = EFFECTS.get(rec["effect"])
        if not effect:
            return
        cursed = effect["func"](message.content)
        if not cursed.strip():
            cursed = message.content  # never post an empty message

        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            return
        hook = await self._relay_webhook(channel)
        if hook is None:
            return

        try:
            await message.delete()
        except discord.DiscordException:
            log.exception("Could not delete a hexed message in #%s", channel.name)
            return

        try:
            files = [await a.to_file() for a in message.attachments] if message.attachments else []
            await hook.send(content=cursed[:2000], username=message.author.display_name,
                            avatar_url=message.author.display_avatar.url, files=files)
        except discord.DiscordException:
            log.exception("Could not repost a hexed message in #%s", channel.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Hexes(bot))
