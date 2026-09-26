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

CHEER_EMOJIS = ["📣", "🎉", "✨", "💜", "🙌", "🏆", "⭐"]
CHEER_OPENERS = ["OMG okay so like,", "Ahh wait,", "Okay but like,", "Bestie,", "Okay okay but hear me out,"]
CHEER_CLOSERS = ["GO VELMORA!!", "VELMORA PRIDE FOREVER", "we are SO velmora", "V-E-L-M-O-R-A!!", "GO GO VELMORA!!"]

PIRATES_TONGUE_MAP = {
    "you": "ye", "your": "yer", "you're": "ye be", "yours": "yer own",
    "my": "me", "is": "be", "are": "be", "am": "be",
    "the": "th'", "yes": "aye", "hello": "ahoy", "hi": "ahoy", "hey": "ahoy",
    "friend": "matey", "friends": "mateys", "stop": "avast", "money": "doubloons",
    "drink": "grog", "food": "grub", "boat": "ship", "car": "ship",
    "no": "nay", "okay": "aye aye", "ok": "aye aye",
}
COUNTRY_MAP = {
    "you": "y'all", "your": "yer", "you're": "y'all're", "yours": "yer own",
    "gonna": "fixin' to", "going": "fixin'", "friend": "partner", "friends": "partners",
    "hello": "howdy", "hi": "howdy", "hey": "howdy", "man": "fella", "guy": "fella", "dude": "fella",
    "girl": "gal", "boy": "young'un", "yes": "reckon so", "yeah": "reckon so", "yep": "yessir",
    "no": "nah, reckon not", "isn't": "ain't", "aren't": "ain't", "wasn't": "weren't",
    "is not": "ain't", "cool": "mighty fine", "great": "mighty fine", "awesome": "plumb amazing",
    "very": "plumb", "really": "sure as shootin'", "car": "truck", "money": "coin",
    "okay": "reckon so", "ok": "reckon so", "food": "grub", "crazy": "loco",
}
CHEER_MAP = {
    "yes": "YES OMG YES", "hi": "OMG HII", "hello": "OMG HELLO", "hey": "OMG HEYY",
    "good": "like SO good", "great": "literally amazing", "cool": "so iconic", "nice": "so iconic",
    "no": "no way, like NO", "friend": "bestie", "friends": "besties",
    "happy": "like SO happy", "excited": "SO hyped", "fun": "literally SO fun",
    "love": "am OBSESSED with", "like": "am OBSESSED with",
}
CAVEMAN_PRONOUN_MAP = {"i": "me", "my": "me", "we": "us", "our": "us", "myself": "me"}
CAVEMAN_FILLERS = {
    "a", "an", "the", "is", "are", "am", "was", "were", "be", "been", "being",
    "to", "of", "and", "but", "that", "this", "these", "those", "so", "very",
    "really", "just", "then", "well", "actually", "basically", "kind", "sort",
    "like", "um", "uh", "also", "too", "quite", "rather", "perhaps", "maybe",
    "please", "would", "could", "should", "will", "shall", "there", "here",
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


def fx_runon(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text)


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


def fx_country(text: str) -> str:
    text = _word_swap(text, COUNTRY_MAP)
    if random.random() < 0.4:
        text = text.rstrip() + random.choice([", y'all.", " — mighty fine, partner.", ", I reckon.",
                                              " ...that's the way it be, pardner."])
    return text


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


def fx_cheerleader(text: str) -> str:
    text = _word_swap(text, CHEER_MAP)
    if random.random() < 0.5:
        text = f"{random.choice(CHEER_OPENERS)} {text}"
    words = text.split(" ")
    out = []
    for w in words:
        out.append(w)
        if random.random() < 0.15:
            out.append(random.choice(CHEER_EMOJIS))
    text = " ".join(out)
    if random.random() < 0.6:
        text = f"{text.rstrip()} {random.choice(CHEER_CLOSERS)} {random.choice(CHEER_EMOJIS)}"
    return text


def _every_nth_word(text: str, replacement: str, n: int = 3) -> str:
    counter = 0

    def repl(m: re.Match) -> str:
        nonlocal counter
        parts = _split_word(m.group(0))
        core = parts[1] if parts else m.group(0)
        counter += 1
        if counter % n == 0:
            return _match_case(core, replacement)
        return m.group(0)
    return re.sub(r"[A-Za-z']+", repl, text)


def fx_frog(text: str) -> str:
    return _every_nth_word(text, "ribbit")


def fx_cat(text: str) -> str:
    text = _every_nth_word(text, "meow")

    def sentence_repl(m: re.Match) -> str:
        return f"{m.group(0)} *purrrr*"
    new_text, n = re.subn(r"[.!?]+", sentence_repl, text)
    return new_text if n else f"{text.rstrip()} *purrrr*"


def fx_caveman(text: str) -> str:
    text = _word_swap(text, CAVEMAN_PRONOUN_MAP)

    def repl(m: re.Match) -> str:
        return "" if m.group(0).lower() in CAVEMAN_FILLERS else m.group(0)
    result = re.sub(r"[A-Za-z']+", repl, text)
    result = re.sub(r"\s{2,}", " ", result)
    result = re.sub(r"\s+([,.!?])", r"\1", result).strip()
    if not result:
        result = "Ugh."
    prefix = random.choice(["Ugh. ", "Grr. ", "Hrm. ", ""])
    suffix = random.choice([" Ugh!", " Grr!", ""])
    return f"{prefix}{result}{suffix}".strip()


def fx_pirates_tongue(text: str) -> str:
    text = _word_swap(text, PIRATES_TONGUE_MAP)
    if random.random() < 0.4:
        text = text.rstrip() + random.choice([" arr!", " arrr.", ", arr."])
    return text


EFFECTS = {
    "reversed": {"name": "Backwards Curse", "description": "Every word comes out back-to-front.",
                "func": fx_reversed},
    "runon": {"name": "Run-On Curse", "description": "Deletes every space and punctuation mark - it all runs "
             "together into one impossible word.", "func": fx_runon},
    "piglatin": {"name": "Pig Latin Curse", "description": "Twists their words into Pig Latin.",
                "func": fx_piglatin},
    "country": {"name": "Cowboy Curse", "description": "Curses them to talk like they've never left the deep "
               "woods - full cowboy, full country twang.", "func": fx_country},
    "stutter": {"name": "Stutter Curse", "description": "Makes them stutter over random syllables.",
               "func": fx_stutter},
    "cheerleader": {"name": "Cheerleader Curse", "description": "Curses them into the most school-spirited Velmora "
                    "cheerleader alive.", "func": fx_cheerleader},
    "frog": {"name": "Frog Curse", "description": "Turns every third word into a ribbit.", "func": fx_frog},
    "cat": {"name": "Cat Curse", "description": "Turns every third word into a meow, and ends every sentence "
           "with a *purrrr*.", "func": fx_cat},
    "caveman": {"name": "Caveman Curse", "description": "Strips out every filler word - grunts and caveman talk "
               "only.", "func": fx_caveman},
    "pirates_tongue": {"name": "Pirate Curse", "description": "Curses them to talk like a pirate.",
                      "func": fx_pirates_tongue},
}

CAST_FLOURISHES = [
    "draws their wand with a flourish and levels it at",
    "steps forward, robes billowing, and points their wand straight at",
    "doesn't even blink before aiming their wand at",
    "raises their wand overhead like a conductor and swings it toward",
    "flicks their wand once, almost bored, in the direction of",
    "spins their wand once around a finger before snapping it toward",
]


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
    @app_commands.choices(effect=[app_commands.Choice(name=v["name"], value=k) for k, v in EFFECTS.items()])
    async def hex(self, interaction: discord.Interaction, member: discord.Member,
                  effect: app_commands.Choice[str], duration: app_commands.Range[int, 0, 10080]):
        if not self._is_headmaster(interaction.user):
            await interaction.response.send_message("Only a Headmaster may cast this.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("You can't hex a bot.", ephemeral=True)
            return

        spell = EFFECTS.get(effect.value)
        if spell is None:
            await interaction.response.send_message(
                "That curse doesn't exist anymore - your Discord app is showing a stale spell list. Force-quit "
                "and reopen Discord (or wait a bit for it to refresh) and try `/hex` again.", ephemeral=True)
            return
        was_hexed = str(member.id) in self.state["hexed"]
        expires_at = None if duration == 0 else time.time() + duration * 60
        self.state["hexed"][str(member.id)] = {
            "effect": effect.value, "expires_at": expires_at, "cast_by": interaction.user.id,
        }
        self.save()

        flourish = random.choice(CAST_FLOURISHES)
        await interaction.response.send_message(embed=discord.Embed(
            title=f"⚡ {spell['name']}!",
            description=f"**{interaction.user.display_name}** {flourish} **{member.display_name}**.",
            color=0x8B5CF6,
        ))

        replaced_note = " (replacing the curse already on them)" if was_hexed else ""
        until = "until a Headmaster lifts it" if expires_at is None else f"for {duration} minute(s)"
        await interaction.followup.send(
            f"🪄 {member.mention} is hexed with **{spell['name']}** ({spell['description']}){replaced_note}, "
            f"{until}.", ephemeral=True)

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
            spell = EFFECTS.get(rec["effect"], {})
            label = f"{spell.get('name', rec['effect'])} ({spell.get('description', '')})" if spell else rec["effect"]
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

        # Deleting + reposting via webhook loses Discord's native reply-thread indicator,
        # so if this was a reply, stitch a quoted line back in manually.
        if message.reference is not None and message.reference.message_id is not None:
            ref_msg = message.reference.resolved
            if isinstance(ref_msg, discord.DeletedReferencedMessage) or ref_msg is None:
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                except discord.DiscordException:
                    ref_msg = None
            if ref_msg is not None:
                snippet = (ref_msg.content or "*(no text)*").replace("\n", " ")
                if len(snippet) > 100:
                    snippet = snippet[:97] + "..."
                quote = f"> ↩️ replying to **{ref_msg.author.display_name}**: {snippet}\n"
                cursed = quote + cursed

        cursed = cursed[:2000]
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
