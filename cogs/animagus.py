"""
Animagus forms. The animal finds you — you don't pick it.

    /animagus              - reveal your form (own three words), or see it again
    /animagus member:@x    - see someone else's
    /staff identity animagusreset @x  - let someone be found again

You give any three words (separate from wand/patronus/broom). The reader
maps the character behind them to one form from the catalog. Same words
always yield the same form. Purely cosmetic — except after you transform
(first reveal, or any later `/animagus` on yourself), every message you
send for one minute ends with that animal's sound (webhook relay, same
idea as Headmaster hexes).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("velmora.animagus")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
ANIMAGUS_PATH = STATE_DIR / "animagus.json"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "animagus_art_assets"
ART_VARIANTS = 5

MODEL = os.getenv("ANIMAGUS_MODEL", os.getenv("PATRONUS_MODEL", "claude-haiku-4-5-20251001"))
ECHO_SECONDS = 60
RELAY_WEBHOOK_NAME = "Velmora Animagus Relay"
GOLD = 0xC9A24A

# Headmaster: always become this animal on first /animagus (any three words).
# Same default Discord id as CAST_AUTO_USER_ID. Override with ANIMAGUS_FORCE_USER_ID;
# unset/0 disables. Animal override: ANIMAGUS_FORCE_ANIMAL (must be a FORMS key).
_raw_force_uid = os.getenv("ANIMAGUS_FORCE_USER_ID", "555141900802457630")
FORCE_USER_ID = (
    int(_raw_force_uid) if _raw_force_uid and str(_raw_force_uid).isdigit() else None
)
FORCE_ANIMAL = os.getenv("ANIMAGUS_FORCE_ANIMAL", "Wolf")
# Dedicated portrait for FORCE_USER_ID (black wolf, green eyes, moon chain).
FORCE_ART_FILE = os.getenv("ANIMAGUS_FORCE_ART", "Wolf_Headmaster.png")

# animal -> (personality blurb, chat sound). ~35 forms; distinct from Patronus pool.
FORMS = {
    "Cat": ("independent; chooses its people and keeps them", "*meow*"),
    "Dog": ("loyal without keeping score; stays when it's hard", "*woof*"),
    "Wolf": ("loyal to its pack above everything", "*awoo*"),
    "Fox": ("clever and self-reliant; slips out of trouble sideways", "*yip*"),
    "Owl": ("patient and wise; sees what others miss in the dark", "*hoot*"),
    "Raven": ("curious and sharp; carries secrets safely", "*caw*"),
    "Crow": ("watchful and wry; remembers every slight and every kindness", "*caw*"),
    "Eagle": ("sees the whole of things from far above", "*screech*"),
    "Hawk": ("focused and fast; never loses sight of its own", "*kee*"),
    "Falcon": ("fearless in a dive; commits completely", "*kak*"),
    "Stag": ("steady and proud; stands between danger and the herd", "*bellow*"),
    "Doe": ("gentle and alert; its strength is quiet vigilance", "*bleat*"),
    "Horse": ("strong and free; carries others through hard ground", "*neigh*"),
    "Hare": ("quick and watchful; bolts before the blow lands", "*thump*"),
    "Rabbit": ("soft-footed and careful; survives by noticing first", "*thump*"),
    "Badger": ("stubborn and fierce; defends its home to the last", "*snarl*"),
    "Bear": ("enormous warmth and enormous strength in the same body", "*growl*"),
    "Otter": ("playful and devoted; keeps its family close", "*chirr*"),
    "Seal": ("at ease in two worlds at once", "*bark*"),
    "Swan": ("graceful, fiercely protective of what it loves", "*hiss*"),
    "Goose": ("loud when it must be; will not be moved once decided", "*honk*"),
    "Mouse": ("brave in small ways, every single day", "*squeak*"),
    "Rat": ("resourceful in tight corners; finds a way through", "*squeak*"),
    "Bat": ("finds its way where there is no light at all", "*screech*"),
    "Snake": ("still, precise, and never wasteful with a strike", "*hiss*"),
    "Toad": ("unbothered by ugly weather; waits things out", "*croak*"),
    "Frog": ("leaps when the moment opens; lands somewhere new", "*ribbit*"),
    "Magpie": ("clever and bright; collects what others overlook", "*chatter*"),
    "Robin": ("cheerful in the coldest season", "*chirp*"),
    "Hedgehog": ("soft inside, impossible to rush", "*snuffle*"),
    "Lynx": ("solitary and perceptive; knows what's coming early", "*chuff*"),
    "Tiger": ("courage that doesn't need an audience", "*chuff*"),
    "Lion": ("leads from the front and takes the blows first", "*roar*"),
    "Dolphin": ("joyful and social; never leaves one of its own behind", "*click*"),
    "Ram": ("headstrong; meets every obstacle straight on", "*baa*"),
    "Squirrel": ("always preparing, so no one goes without", "*chitter*"),
}

assert len(FORMS) >= 30

READING_PROMPT = """You are reading a student's Animagus form at Velmora, a school of magic. An Animagus is not chosen — the animal a person becomes is pre-determined by their internal nature: personality, traits, and character. You are naming the shape they already are.

The student has given you three words — any words, in any order. These are NOT the same words as their wand. Read the character behind them. Do NOT match their words literally. If they name an animal, do not simply hand that animal back.

Choose exactly one animal from this list:
{animals}

Then write:
- "form": one short sentence describing the first shift into that animal (bodily, specific, a little uncanny)
- "reading": two or three sentences, speaking to the student directly, on why this is the shape their nature already takes. Warm. Refer to what the words revealed, not to the words themselves.

The student's words: "{words}"

Reply with ONLY a JSON object:
{{"animal": "...", "form": "...", "reading": "..."}}"""


def _words_of(text: str) -> list[str]:
    return re.findall(r"[\w']+", text or "")


def _form_record(animal: str) -> dict:
    blurb, sound = FORMS[animal]
    return {
        "animal": animal,
        "form": f"Your bones settle into the shape of a {animal.lower()} before you can argue.",
        "reading": f"The {animal.lower()} is {blurb}. That is what you already were.",
        "sound": sound,
    }


def fallback_animagus(words: str) -> dict:
    """No API: same words (any order) always yield the same form."""
    toks = sorted(w.lower() for w in _words_of(words))
    digest = hashlib.sha256(("animagus:" + " ".join(toks)).encode()).digest()
    animal = sorted(FORMS)[digest[0] % len(FORMS)]
    return _form_record(animal)


def forced_animagus(user_id: int) -> dict | None:
    """Fixed form for FORCE_USER_ID, if configured and the animal exists."""
    if FORCE_USER_ID is None or user_id != FORCE_USER_ID:
        return None
    match = next((a for a in FORMS if a.lower() == FORCE_ANIMAL.lower()), None)
    if not match:
        return None
    if match == "Wolf":
        return {
            "animal": "Wolf",
            "form": (
                "Your bones settle into black fur before you can argue — "
                "emerald eyes open on the dark, already measuring the ground ahead."
            ),
            "reading": (
                "A black wolf with green eyes. You are always reading the room, "
                "calculating the next move before anyone else has finished speaking — "
                "not for show, but so the pack comes out ahead. You do your best for them, "
                "every angle worked, every loyalty earned. That is what you already were."
            ),
            "sound": FORMS["Wolf"][1],
            "art": "headmaster",
        }
    rec = _form_record(match)
    rec["art"] = "headmaster"
    return rec


def art_filename(animal: str, variant: int) -> str:
    return f"{animal}_{variant}.png"


def art_path(animal: str, variant: int) -> Path:
    return ASSETS_DIR / art_filename(animal, variant)


def force_art_path() -> Path:
    return ASSETS_DIR / FORCE_ART_FILE


def pick_art_variant(user_id: int, animal: str) -> int:
    """Stable 1..ART_VARIANTS pick so the same person always sees the same portrait."""
    digest = hashlib.sha256(f"animagus-art:{user_id}:{animal}".encode()).digest()
    return (digest[0] % ART_VARIANTS) + 1


def resolve_portrait_path(user_id: int, rec: dict) -> Path | None:
    """Portrait on disk for this form, including the headmaster override."""
    animal = rec.get("animal")
    if not animal or animal not in FORMS:
        return None
    if FORCE_USER_ID is not None and user_id == FORCE_USER_ID:
        special = force_art_path()
        if special.is_file():
            return special
    art = rec.get("art")
    if art == "headmaster":
        special = force_art_path()
        if special.is_file():
            return special
    try:
        variant = int(art or pick_art_variant(user_id, animal))
    except (TypeError, ValueError):
        variant = pick_art_variant(user_id, animal)
    if variant < 1 or variant > ART_VARIANTS:
        variant = 1
    path = art_path(animal, variant)
    if path.is_file():
        return path
    for v in range(1, ART_VARIANTS + 1):
        alt = art_path(animal, v)
        if alt.is_file():
            return alt
    return None


def _clean(raw: dict) -> dict | None:
    try:
        animal = str(raw["animal"]).strip()
        form = str(raw["form"]).strip()
        reading = str(raw["reading"]).strip()
    except (KeyError, TypeError):
        return None
    match = next((a for a in FORMS if a.lower() == animal.lower()), None)
    if not match or not form or not reading:
        return None
    return {
        "animal": match,
        "form": form[:300],
        "reading": reading[:600],
        "sound": FORMS[match][1],
    }


class Animagus(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.state.setdefault("forms", {})       # uid -> {animal, form, reading, sound, words}
        self.state.setdefault("echo_until", {})  # uid -> unix ts
        self._webhooks: dict[int, discord.Webhook] = {}
        self.expiry_sweep.start()

    def cog_unload(self):
        self.expiry_sweep.cancel()

    def _load(self) -> dict:
        try:
            with open(ANIMAGUS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"forms": {}, "echo_until": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Animagus records unreadable - starting empty.")
            return {"forms": {}, "echo_until": {}}
        # Older shape: bare uid -> record
        if data and "forms" not in data and all(isinstance(v, dict) for v in data.values()):
            return {"forms": data, "echo_until": {}}
        return data

    def save(self) -> None:
        try:
            ANIMAGUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = ANIMAGUS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, ANIMAGUS_PATH)
        except OSError:
            log.exception("Could not save animagus records.")

    def form_of(self, user_id: int) -> dict | None:
        rec = self.state["forms"].get(str(user_id))
        if not rec:
            return None
        # Backfill sound if an older save missed it.
        animal = rec.get("animal")
        if animal in FORMS and not rec.get("sound"):
            rec["sound"] = FORMS[animal][1]
        # Backfill a stable portrait variant if missing.
        if animal in FORMS and not rec.get("art"):
            rec["art"] = pick_art_variant(user_id, animal)
        # Keep the headmaster's forced look/text current even on old saves.
        forced = forced_animagus(user_id)
        if forced and rec.get("animal") == forced["animal"]:
            for key in ("form", "reading", "sound", "art"):
                if forced.get(key) is not None:
                    rec[key] = forced[key]
        return rec

    def portrait_file(self, rec: dict, user_id: int | None = None) -> discord.File | None:
        """Attachable Animagus portrait, or None if that variant isn't on disk."""
        uid = user_id if user_id is not None else 0
        path = resolve_portrait_path(uid, rec)
        if path is None:
            return None
        return discord.File(path, filename="animagus.png")

    def release(self, user_id: int) -> bool:
        uid = str(user_id)
        gone = self.state["forms"].pop(uid, None) is not None
        self.state["echo_until"].pop(uid, None)
        if gone:
            self.save()
        return gone

    def echo_active(self, user_id: int, now: float | None = None) -> Optional[str]:
        """Animal sound while the post-cast echo window is open, else None."""
        now = now if now is not None else time.time()
        until = self.state["echo_until"].get(str(user_id))
        if not until or until <= now:
            return None
        rec = self.form_of(user_id)
        if not rec:
            return None
        return rec.get("sound") or (FORMS.get(rec.get("animal"), ("", None))[1])

    def start_echo(self, user_id: int, seconds: int = ECHO_SECONDS) -> None:
        self.state["echo_until"][str(user_id)] = time.time() + seconds
        self.save()

    async def read_form(self, words: str, user_id: int | None = None) -> dict:
        if user_id is not None:
            forced = forced_animagus(user_id)
            if forced is not None:
                return forced
        wands = self.bot.get_cog("Wands")
        client = getattr(wands, "client", None)
        if client is not None:
            prompt = READING_PROMPT.format(
                animals="\n".join(f"- {k}: {v[0]}" for k, v in FORMS.items()),
                words=words.replace('"', "'")[:200],
            )
            try:
                response = await client.messages.create(
                    model=MODEL, max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(getattr(b, "text", "") for b in response.content)
                match = re.search(r"\{.*\}", text, re.S)
                if match:
                    result = _clean(json.loads(match.group(0)))
                    if result:
                        return result
                log.warning("Animagus reading came back unusable; using the fallback.")
            except Exception:
                log.exception("Animagus reading failed; using the fallback.")
        return fallback_animagus(words)

    def embed_for(self, member, rec: dict, *, fresh: bool = False) -> discord.Embed:
        animal = rec["animal"]
        sound = rec.get("sound") or FORMS.get(animal, ("", "*…*"))[1]
        echoing = fresh or bool(self.echo_active(member.id))
        embed = discord.Embed(
            title=("The shift takes you…" if echoing else f"{member.display_name}'s Animagus"),
            description=(
                f"**{animal}**\n"
                f"*{rec['form']}*\n\n{rec['reading']}"
            ),
            color=GOLD,
        )
        if resolve_portrait_path(member.id, rec) is not None:
            embed.set_image(url="attachment://animagus.png")
        if echoing:
            embed.set_footer(
                text=f"For the next minute, your messages end with {sound}"
            )
        else:
            embed.set_footer(
                text=(
                    f"Form sound: {sound} · run /animagus again to transform "
                    f"for a minute"
                )
            )
        return embed

    async def send_form(
        self,
        interaction: discord.Interaction,
        member,
        rec: dict,
        *,
        fresh: bool = False,
        deferred: bool = False,
    ) -> None:
        embed = self.embed_for(member, rec, fresh=fresh)
        file = None
        path = resolve_portrait_path(member.id, rec)
        if path is not None:
            file = discord.File(path, filename="animagus.png")
            embed.set_image(url="attachment://animagus.png")
        kwargs = {"embed": embed}
        if file is not None:
            kwargs["file"] = file
        if deferred:
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)

    @app_commands.command(
        name="animagus",
        description="Reveal your Animagus form — or transform again (1 min of animal sounds).",
    )
    @app_commands.describe(member="Whose form to see (leave blank for your own)")
    async def animagus(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        existing = self.form_of(target.id)
        if existing:
            # Re-running on yourself restarts the sound window — first cast
            # was easy to miss (60s only, and re-views used to skip the echo).
            fresh = target.id == interaction.user.id
            if fresh:
                self.start_echo(interaction.user.id)
            await self.send_form(interaction, target, existing, fresh=fresh)
            return

        if member and member.id != interaction.user.id:
            await interaction.response.send_message(
                f"{member.display_name} hasn't found their Animagus form yet.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(AnimagusModal(self))

    async def animagusreset(self, interaction: discord.Interaction, member: discord.Member):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if self.release(member.id):
            await interaction.response.send_message(
                f"{member.display_name}'s Animagus form has been released. "
                "They can be found again with `/animagus`.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"{member.display_name} has no Animagus form to release.",
                ephemeral=True,
            )

    # -------------------------------------------------------------- echo

    def _purge_echo(self) -> None:
        now = time.time()
        stale = [uid for uid, until in self.state["echo_until"].items() if until <= now]
        for uid in stale:
            self.state["echo_until"].pop(uid, None)
        if stale:
            self.save()

    @tasks.loop(minutes=2)
    async def expiry_sweep(self):
        self._purge_echo()

    @expiry_sweep.before_loop
    async def _before_sweep(self):
        await self.bot.wait_until_ready()

    def _relay_home(
        self, channel: discord.abc.Messageable
    ) -> tuple[Optional[discord.TextChannel], Optional[discord.Thread]]:
        """Webhook host channel + optional thread to post into."""
        if isinstance(channel, discord.TextChannel):
            return channel, None
        if isinstance(channel, discord.Thread) and isinstance(
            channel.parent, discord.TextChannel
        ):
            return channel.parent, channel
        return None, None

    async def _relay_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        cached = self._webhooks.get(channel.id)
        if cached:
            return cached
        try:
            hooks = await channel.webhooks()
            hook = next((h for h in hooks if h.name == RELAY_WEBHOOK_NAME), None)
            if hook is None:
                hook = await channel.create_webhook(
                    name=RELAY_WEBHOOK_NAME, reason="Animagus form-sound relay"
                )
            self._webhooks[channel.id] = hook
            return hook
        except discord.DiscordException:
            log.exception(
                "Could not get/create the animagus relay webhook in #%s",
                getattr(channel, "name", channel.id),
            )
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        antispam = self.bot.get_cog("AntiSpam")
        if antispam is not None and antispam.should_block(message):
            return
        # Hex mangles take priority — don't fight that relay.
        hexes = self.bot.get_cog("Hexes")
        if hexes is not None and hexes.active_hex(message.author.id):
            return
        if not message.content or not message.content.strip():
            return

        sound = self.echo_active(message.author.id)
        if not sound:
            return

        body = message.content.rstrip()
        if body.endswith(sound):
            return
        cursed = f"{body} {sound}"
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
        home, thread = self._relay_home(message.channel)
        if home is None:
            return
        hook = await self._relay_webhook(home)
        if hook is None:
            # No Manage Webhooks here — still append the sound as a bot line
            # so the minute isn't a silent no-op for those channels.
            try:
                await message.channel.send(
                    f"**{message.author.display_name}:** {cursed}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.DiscordException:
                log.exception(
                    "Animagus echo fallback failed in #%s",
                    getattr(message.channel, "name", message.channel.id),
                )
            return

        try:
            await message.delete()
        except discord.DiscordException:
            log.exception(
                "Could not delete an animagus-echo message in #%s",
                getattr(message.channel, "name", message.channel.id),
            )
            return

        try:
            files = [await a.to_file() for a in message.attachments] if message.attachments else []
            send_kwargs = dict(
                content=cursed[:2000],
                username=message.author.display_name,
                avatar_url=message.author.display_avatar.url,
                files=files,
            )
            if thread is not None:
                send_kwargs["thread"] = thread
            await hook.send(**send_kwargs)
        except discord.DiscordException:
            log.exception(
                "Could not repost an animagus-echo message in #%s",
                getattr(message.channel, "name", message.channel.id),
            )
            self._webhooks.pop(home.id, None)
            try:
                await message.channel.send(
                    f"**{message.author.display_name}:** {cursed}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.DiscordException:
                log.exception("Animagus echo recovery send also failed.")


class AnimagusModal(discord.ui.Modal, title="The form finds you"):
    words = discord.ui.TextInput(
        label="Three words that feel like you",
        placeholder="Not a wish list. Whatever comes — still · stubborn · soft",
        max_length=120,
    )

    def __init__(self, cog: Animagus):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        text = str(self.words).strip()
        if not _words_of(text):
            await interaction.response.send_message(
                "The form needs words to listen to. Try again with `/animagus`.",
                ephemeral=True,
            )
            return

        if self.cog.form_of(interaction.user.id):
            self.cog.start_echo(interaction.user.id)
            await self.cog.send_form(
                interaction,
                interaction.user,
                self.cog.form_of(interaction.user.id),
                fresh=True,
            )
            return

        await interaction.response.defer(thinking=True)
        result = await self.cog.read_form(text, user_id=interaction.user.id)
        result["words"] = text
        result["art"] = pick_art_variant(interaction.user.id, result["animal"])
        self.cog.state["forms"][str(interaction.user.id)] = result
        self.cog.start_echo(interaction.user.id)
        await self.cog.send_form(
            interaction, interaction.user, result, fresh=True, deferred=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Animagus(bot))
