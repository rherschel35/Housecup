"""
Exploring Velmora - the places students can go, in Discord.

Everyone shares one student record (satchel, reputation with each place,
discoveries, flags) stored in STATE_DIR/world_state.json, so something
found in one place can matter in another.

Players:  /explore  /forage  /satchel  /use  /offer  /places
Staff:    /world eventstart | eventend | eventstatus | academy | rep | give | channel | reload

Secret interactions are NOT commands: they're typed as plain messages in a
place's channel. Wrong guesses get silence. That's deliberate - Discord
shows every slash command to everyone, and secrets have to stay secret.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.world_engine import (HOUSE_NAMES, RARITY_LABEL, RARITY_ORDER, Ctx, Place, World,
                               blank_student, tone_for)

log = logging.getLogger("velmora.world")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
WORLD_DIR = DATA_DIR / "world"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "world_state.json"

# Places that exist. Add a line here when a new place is written.
PLACES = {
    "garden": "garden.json",
    "library": "library.json",
    "dungeons": "dungeons.json",
    "observatory": "observatory.json",
    "forbidden_woods": "forbidden_woods.json",
}
PLACE_CHOICES = [
    app_commands.Choice(name="🌿 The Garden", value="garden"),
    app_commands.Choice(name="📚 The Library", value="library"),
    app_commands.Choice(name="🕯️ The Dungeons", value="dungeons"),
    app_commands.Choice(name="🔭 The Observatory", value="observatory"),
    app_commands.Choice(name="🌲 The Forbidden Woods", value="forbidden_woods"),
]

# Places where searching around risks a Wild Threat showing up right there.
DANGEROUS_PLACES = {"dungeons", "forbidden_woods"}
MONSTER_CHANCE = 0.20

DEFAULT_LIMITS = {"explore": 5, "forage": 3}
HERE_MINUTES = 60          # how long you count as "in" a place after exploring it
GREEN, NIGHT_COLOR = 0x3E8E5A, 0x2B3A2E
EVENT_CHOICES = [
    app_commands.Choice(name="🚪 Door behind the roses", value="door"),
    app_commands.Choice(name="🥀 Flowers turn black", value="black_flowers"),
    app_commands.Choice(name="🐸 Golden frog trades", value="frog"),
    app_commands.Choice(name="💧 A name from the Pool", value="whisper"),
    app_commands.Choice(name="🌳 The Ancient Tree glows", value="tree_glow"),
    app_commands.Choice(name="🦋 Moon moths", value="moths"),
    app_commands.Choice(name="🗝️ Key beneath the bench", value="key"),
    app_commands.Choice(name="🌿 An unexplored path", value="path"),
    app_commands.Choice(name="🏡 The Garden takes an interest in a House", value="strange_house"),
]
HOUSE_CHOICES = [app_commands.Choice(name=n, value=k) for k, n in HOUSE_NAMES.items()]


def _read(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError):
        log.exception("Could not read %s", path)
        if path == STATE_PATH:
            try:
                path.rename(path.with_suffix(f".corrupt-{int(time.time())}"))
            except OSError:
                pass
        return default


def load_world(rng=None) -> World:
    items = _read(WORLD_DIR / "items.json", {})
    places = {k: Place(k, _read(WORLD_DIR / f, {})) for k, f in PLACES.items()}
    return World(places, items, rng)


class ChoiceView(discord.ui.View):
    """The buttons under an encounter - only the student who had it can press them."""

    def __init__(self, cog, user_id: int, place: str, subject: dict, choices: dict):
        super().__init__(timeout=180)
        self.cog, self.user_id, self.place, self.subject = cog, user_id, place, subject
        self.message = None
        for key, meta in choices.items():
            danger = key in ("steal", "uproot")
            btn = discord.ui.Button(label=meta["label"], emoji=meta["emoji"],
                                    style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.secondary)
            btn.callback = self._cb(key)
            self.add_item(btn)

    def _cb(self, key):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("*This isn't your encounter.*", ephemeral=True)
                return
            for child in self.children:
                child.disabled = True
            self.stop()
            await self.cog.handle_choice(interaction, self.place, key, self.subject, self)
        return cb

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class WorldCog(commands.Cog, name="World"):
    world_group = app_commands.Group(name="world", description="(staff) Run Velmora's places and events.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.world = load_world()
        self.state = _read(STATE_PATH, {})
        for k, v in (("students", {}), ("places", {}), ("channels", {}), ("academy", []), ("server_found", [])):
            self.state.setdefault(k, v)
        for key in self.world.places:
            self.state["places"].setdefault(key, {"event": None, "last_event_end": 0})
        env_ch = os.getenv("GARDEN_CHANNEL_ID")
        if env_ch and env_ch.isdigit():
            self.state["channels"].setdefault("garden", int(env_ch))
        self.lock = asyncio.Lock()

    async def cog_load(self):
        self.tick.start()

    async def cog_unload(self):
        self.tick.cancel()

    # ------------------------------------------------------------ state

    def save(self):
        tmp = STATE_PATH.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Failed to save world state")

    def student(self, user) -> dict:
        s = self.state["students"].setdefault(str(user.id), blank_student(getattr(user, "display_name", "")))
        s["name"] = getattr(user, "display_name", s.get("name", ""))
        return s

    def store(self):
        return self.bot.get_cog("Store")

    def house_of(self, member) -> Optional[str]:
        store = self.store()
        try:
            return store.member_house(member) if store else None
        except Exception:
            return None

    def event(self, place: str) -> Optional[dict]:
        return self.state["places"].get(place, {}).get("event")

    def ctx(self, user, place: str) -> Ctx:
        ev = self.event(place)
        return Ctx(student=self.student(user), place=place, house=self.house_of(user),
                   event=ev["key"] if ev else None, academy=self.state.get("academy", []))

    def is_staff(self, member) -> bool:
        store = self.store()
        if store:
            return store.is_staff(member)
        perms = getattr(member, "guild_permissions", None)
        return bool(perms and perms.manage_guild)

    def limit(self, place: str, what: str) -> int:
        return self.world.places[place].c.get("limits", {}).get(what, DEFAULT_LIMITS[what])

    def embed(self, text, title=None, night=False) -> discord.Embed:
        e = discord.Embed(description=text[:4000], color=NIGHT_COLOR if night else GREEN)
        if title:
            e.set_author(name=title)
        return e

    async def in_place_channel(self, interaction, place) -> bool:
        cid = self.state["channels"].get(place)
        if cid and interaction.channel_id != cid:
            await interaction.response.send_message(
                f"{self.world.places[place].name.capitalize()} is found in <#{cid}>.", ephemeral=True)
            return False
        return True

    def where(self, student) -> Optional[str]:
        here = student.get("here") or {}
        if here.get("place") in self.world.places and time.time() - here.get("at", 0) < HERE_MINUTES * 60:
            return here["place"]
        return None

    # ------------------------------------------------------------ house points

    async def points(self, student, member, delta: int, reason: str) -> str:
        delta = self.world.allow_points(student, delta)
        if not delta:
            return ""
        store = self.store()
        house = self.house_of(member)
        if not (store and house):
            self.world.refund_points(student, delta)
            return ""
        try:
            store.record(house=house, delta=delta, actor_id=self.bot.user.id if self.bot.user else 0,
                         target_id=member.id, reason=reason)
        except Exception:
            log.exception("Couldn't record exploration points")
            self.world.refund_points(student, delta)
            return ""
        from cogs.store import HOUSES
        h = HOUSES[house]
        if delta > 0:
            return f"\n\n✨ **+{delta}** for {h['emoji']} {h['name']}."
        return f"\n\n🥀 **{delta}** from {h['emoji']} {h['name']}. It was noticed."

    async def maybe_spawn_monster(self, place: str, channel_id: int) -> bool:
        """Searching somewhere dangerous risks a Wild Threat turning up right
        there. Quietly does nothing if that place isn't dangerous, the roll
        misses, the Dementors cog isn't loaded, or something's already loose
        (never steals an encounter out from under another channel)."""
        if place not in DANGEROUS_PLACES or self.world.rng.random() >= MONSTER_CHANCE:
            return False
        dementors = self.bot.get_cog("Dementors")
        if not dementors:
            return False
        return await dementors.try_ambient_spawn(channel_id)

    # ------------------------------------------------------------ player commands

    @app_commands.command(name="explore", description="Go somewhere in Velmora and see what you find.")
    @app_commands.describe(place="Where to go")
    @app_commands.choices(place=PLACE_CHOICES)
    async def explore(self, interaction: discord.Interaction, place: str):
        if place not in self.world.places or not await self.in_place_channel(interaction, place):
            return
        P = self.world.places[place]
        async with self.lock:
            ctx = self.ctx(interaction.user, place)
            key = f"{place}_explore"
            if self.world.count(ctx.student, key) >= self.limit(place, "explore"):
                return await interaction.response.send_message(
                    "🌙 The way in won't open for you again today. Come back tomorrow.", ephemeral=True)
            self.world.bump(ctx.student, key)
            pe = self.event(place)
            enc = P.explore(ctx, strange_house=pe.get("house") if pe and pe["key"] == "strange_house" else None)
            reveal = self.world.reveal_if_new(ctx.student, place)
            left = self.limit(place, "explore") - self.world.count(ctx.student, key)
            self.save()
        text = enc["text"]
        if enc["reward"]:
            text += f"\n\n🎁 You receive: {self.world.item_line(enc['reward'])}"
        text += await self.points(ctx.student, interaction.user, self.world.reward_points(enc["reward"]),
                                  f"🌿 found something rare in {P.name}")
        if reveal:
            text += f"\n\n{reveal}"
        monster = await self.maybe_spawn_monster(place, interaction.channel_id)
        if monster:
            text += "\n\n⚠️ Something else was already here. Better have a spell ready."
        embed = self.embed(text, f"{interaction.user.display_name} explores {P.name}"
                           + (" by moonlight" if enc["night"] else ""), enc["night"])
        embed.set_footer(text=f"Visits left today: {left}")
        view = None
        if enc["creature"]:
            view = ChoiceView(self, interaction.user.id, place, enc["creature"], P.c["choices"])
        elif enc["plant"]:
            view = ChoiceView(self, interaction.user.id, place, enc["discovery"], P.c["plant_choices"])
        if view:
            await interaction.response.send_message(embed=embed, view=view)
            try:
                view.message = await interaction.original_response()
            except discord.HTTPException:
                pass
        else:
            await interaction.response.send_message(embed=embed)

    async def handle_choice(self, interaction, place, key, subject, view):
        P = self.world.places[place]
        async with self.lock:
            ctx = self.ctx(interaction.user, place)
            res = P.resolve_choice(ctx, key, subject)
            reveal = self.world.reveal_if_new(ctx.student, place)
            self.save()
        text = res["text"]
        if res["item"]:
            text += f"\n\n🎁 You receive: {self.world.item_line(res['item'])}"
        text += await self.points(ctx.student, interaction.user, res["points"], f"🥀 misbehaved in {P.name}")
        if reveal:
            text += f"\n\n{reveal}"
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(embed=self.embed(text))

    @app_commands.command(name="forage", description="Search somewhere for ingredients and strange things.")
    @app_commands.describe(place="Where to search")
    @app_commands.choices(place=PLACE_CHOICES)
    async def forage(self, interaction: discord.Interaction, place: str):
        if place not in self.world.places or not await self.in_place_channel(interaction, place):
            return
        P = self.world.places[place]
        async with self.lock:
            ctx = self.ctx(interaction.user, place)
            key = f"{place}_forage"
            if self.world.count(ctx.student, key) >= self.limit(place, "forage"):
                return await interaction.response.send_message(
                    "🍂 You've searched it bare for today. It will grow back. Probably.", ephemeral=True)
            self.world.bump(ctx.student, key)
            res = P.forage(ctx)
            left = self.limit(place, "forage") - self.world.count(ctx.student, key)
            self.save()
        text = res["text"]
        if res["item"]:
            it = self.world.items[res["item"]]
            text += f"\n*{RARITY_LABEL[it['rarity']]}* - {it['desc']}"
        text += await self.points(ctx.student, interaction.user, self.world.reward_points(res["item"]),
                                  f"🌱 foraged something rare in {P.name}")
        monster = await self.maybe_spawn_monster(place, interaction.channel_id)
        if monster:
            text += "\n\n⚠️ Something else was already here. Better have a spell ready."
        embed = self.embed(text, f"{interaction.user.display_name} forages in {P.name}")
        embed.set_footer(text=f"Searches left today: {left}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="satchel", description="Look inside your satchel. Only you can see it.")
    async def satchel(self, interaction: discord.Interaction):
        s = self.student(interaction.user)
        items = s.get("items", {})
        if not items:
            return await interaction.response.send_message(
                "🎒 Your satchel is empty. Go and explore somewhere.", ephemeral=True)
        lines = []
        for rarity in RARITY_ORDER:
            group = sorted((i, n) for i, n in items.items() if self.world.items.get(i, {}).get("rarity") == rarity)
            if group:
                lines.append(f"**{RARITY_LABEL[rarity]}**")
                lines += [f"{self.world.item_line(i, n)} - *{self.world.items[i]['desc']}*" for i, n in group]
        embed = self.embed("\n".join(lines), f"🎒 {interaction.user.display_name}'s satchel")
        embed.set_footer(text=f"{sum(items.values())} things carried · /use or /offer them where you are.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _satchel_choices(self, interaction: discord.Interaction, current: str):
        s = self.state["students"].get(str(interaction.user.id), {})
        out = []
        for i, n in s.get("items", {}).items():
            it = self.world.items.get(i)
            if it and current.lower() in it["name"].lower():
                out.append(app_commands.Choice(name=f"{it['name']} (×{n})", value=i))
        return out[:25]

    @app_commands.command(name="use", description="Try using something from your satchel, wherever you are.")
    @app_commands.describe(item="Something you're carrying")
    async def use(self, interaction: discord.Interaction, item: str):
        async with self.lock:
            s = self.student(interaction.user)
            if s.get("items", {}).get(item, 0) < 1:
                return await interaction.response.send_message("You're not carrying that.", ephemeral=True)
            place = self.where(s)
            if not place:
                return await interaction.response.send_message(
                    "You turn it over in your hands. Maybe try it somewhere - go and `/explore` first.", ephemeral=True)
            P = self.world.places[place]
            ctx = self.ctx(interaction.user, place)
            res = P.use(ctx, item)
            if res:
                self.save()
        if not res:
            return await interaction.response.send_message(
                f"You try the {self.world.items[item]['name']} here. Nothing happens. Not here, anyway.", ephemeral=True)
        text = res["text"]
        if res["item"]:
            text += f"\n\n🎁 You receive: {self.world.item_line(res['item'])}"
        text += await self.points(ctx.student, interaction.user, res["points"], f"🗝️ discovered something in {P.name}")
        await interaction.response.send_message(embed=self.embed(text, f"{interaction.user.display_name}, in {P.name}"))

    use.autocomplete("item")(_satchel_choices)

    @app_commands.command(name="offer", description="Leave something as an offering, wherever you are.")
    @app_commands.describe(item="Something you're carrying")
    async def offer(self, interaction: discord.Interaction, item: str):
        async with self.lock:
            s = self.student(interaction.user)
            place = self.where(s)
            if not place:
                return await interaction.response.send_message(
                    "An offering needs somewhere to leave it. Go and `/explore` first.", ephemeral=True)
            if s.get("items", {}).get(item, 0) < 1:
                return await interaction.response.send_message("You're not carrying that.", ephemeral=True)
            P = self.world.places[place]
            ctx = self.ctx(interaction.user, place)
            res = P.offer(ctx, item)
            reveal = self.world.reveal_if_new(ctx.student, place)
            self.save()
        text = f"You leave {self.world.item_line(item)} in {P.name}.\n\n{res['text']}"
        if reveal:
            text += f"\n\n{reveal}"
        await interaction.response.send_message(embed=self.embed(text, f"An offering from {interaction.user.display_name}"))

    offer.autocomplete("item")(_satchel_choices)

    @app_commands.command(name="places", description="Where in Velmora you can go.")
    async def places(self, interaction: discord.Interaction):
        lines = []
        for key, P in self.world.places.items():
            cid = self.state["channels"].get(key)
            ev = self.event(key)
            bit = f" · in <#{cid}>" if cid else ""
            if ev:
                bit += " · *something unusual is happening*"
            lines.append(f"**{P.c.get('title', P.name)}**{bit}\n{P.c.get('blurb', '')}")
        lines.append("\n`/explore` · `/forage` · `/satchel` · `/use` · `/offer`\n"
                     "*Not everything you can do is on this list. Some things you'll only learn from classes, "
                     "books, riddles, rumours - or other students. If you discover something, you decide whether to share it.*")
        await interaction.response.send_message(embed=self.embed("\n\n".join(lines), "Velmora"))

    # ------------------------------------------------------------ secrets, typed in a place's channel

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or len(message.content or "") > 300:
            return
        place = next((k for k, cid in self.state["channels"].items() if cid == message.channel.id), None)
        if not place or place not in self.world.places:
            return
        P = self.world.places[place]
        async with self.lock:
            ctx = self.ctx(message.author, place)
            ev = self.event(place)
            sec = P.match_secret(message.content, ctx, self.state, ev)
            if not sec:
                return
            if sec["response"] == "FROG_TRADE":
                res = P.frog_trade(ctx, ev)
                self.save()
                await message.reply(embed=self.embed(res["text"]), mention_author=False)
                return
            if sec["response"] == "POOL_ANSWER":
                if ev.get("user_id") != message.author.id:
                    return   # the Pool wasn't calling you
                sec = dict(sec, response=(
                    "💧 You kneel and answer. The whispering stops. Then, very softly, the Pool says something "
                    "only you can hear - and a single bright drop rises out of the water into your hand."),
                    reward="dew_diamond", rep=2, points=2)
            res = P.claim_secret(sec, ctx, self.state, ev)
            ctx.student["here"] = {"place": place, "at": time.time()}
            reveal = self.world.reveal_if_new(ctx.student, place)
            self.save()
        text = res["text"]
        if res["item"]:
            text += f"\n\n🎁 You receive: {self.world.item_line(res['item'])}"
        text += await self.points(ctx.student, message.author, res.get("points", 0), f"🗝️ discovered a secret in {P.name}")
        if reveal:
            text += f"\n\n{reveal}"
        self.save()
        await message.reply(embed=self.embed(text), mention_author=False)
        log.info("%s found secret %s:%s", message.author, place, sec["id"])

    # ------------------------------------------------------------ events

    def whisper_candidates(self, place):
        cutoff = time.time() - 7 * 86400
        return [(int(uid), s.get("name") or "someone") for uid, s in self.state["students"].items()
                if s.get("last_seen", {}).get(place, 0) >= cutoff]

    async def announce(self, place, text):
        cid = self.state["channels"].get(place)
        ch = self.bot.get_channel(cid) if cid else None
        if ch:
            try:
                await ch.send(embed=self.embed(text, night=True))
            except discord.HTTPException:
                log.exception("Couldn't announce in %s", place)

    @tasks.loop(minutes=5)
    async def tick(self):
        now = time.time()
        for key, P in self.world.places.items():
            if not self.state["channels"].get(key):
                continue   # a place with nowhere to announce stays quiet
            async with self.lock:
                ps = self.state["places"][key]
                ended = P.end_event_if_due(ps, now)
                started = P.maybe_start_event(ps, now, self.whisper_candidates(key))
                if ended or started:
                    self.save()
            if ended:
                await self.announce(key, ended)
            if started:
                await self.announce(key, started["announce"])
                log.info("Event started in %s: %s", key, started["key"])

    @tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ staff

    async def _staff(self, interaction) -> bool:
        if self.is_staff(interaction.user):
            return True
        await interaction.response.send_message("That's for staff.", ephemeral=True)
        return False

    @world_group.command(name="eventstart", description="(staff) Start an event somewhere now.")
    @app_commands.describe(event="Which event", minutes="How long (empty = random)", house="For the House event")
    @app_commands.choices(place=PLACE_CHOICES, event=EVENT_CHOICES, house=HOUSE_CHOICES)
    async def eventstart(self, interaction: discord.Interaction, place: str, event: str,
                         minutes: Optional[app_commands.Range[int, 5, 10080]] = None,
                         house: Optional[str] = None):
        if not await self._staff(interaction):
            return
        P = self.world.places[place]
        async with self.lock:
            ps = self.state["places"][place]
            ended = P.end_event_if_due(ps, time.time(), force=True)
            ev = P.start_event(ps, event, time.time(), self.whisper_candidates(place), minutes=minutes, house=house)
            self.save()
        if not ev:
            return await interaction.response.send_message(
                "That event couldn't start here (the Pool needs someone who visited in the last week).", ephemeral=True)
        await interaction.response.send_message(f"Started - ends <t:{int(ev['ends'])}:f>.", ephemeral=True)
        if ended:
            await self.announce(place, ended)
        await self.announce(place, ev["announce"])

    @world_group.command(name="eventend", description="(staff) End the event happening somewhere.")
    @app_commands.choices(place=PLACE_CHOICES)
    async def eventend(self, interaction: discord.Interaction, place: str):
        if not await self._staff(interaction):
            return
        async with self.lock:
            ended = self.world.places[place].end_event_if_due(self.state["places"][place], time.time(), force=True)
            self.save()
        await interaction.response.send_message("Ended." if ended else "Nothing is happening there.", ephemeral=True)
        if ended:
            await self.announce(place, ended)

    @world_group.command(name="eventstatus", description="(staff) What's happening around Velmora?")
    async def eventstatus(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        lines = []
        for key, P in self.world.places.items():
            ev = self.event(key)
            if ev:
                extra = f" (whispering {ev.get('name')})" if ev["key"] == "whisper" else ""
                extra += f" (House {HOUSE_NAMES[ev['house']]})" if ev["key"] == "strange_house" else ""
                lines.append(f"**{P.name}**: {ev['key']}{extra} - ends <t:{int(ev['ends'])}:R>")
            else:
                lines.append(f"**{P.name}**: quiet")
        academy = ", ".join(self.state.get("academy", [])) or "none"
        lines.append(f"Academy events on: {academy}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @world_group.command(name="academy", description="(staff) Switch an academy-wide event on or off (e.g. tournament).")
    @app_commands.describe(name="Short name, e.g. tournament", on="On or off")
    async def academy(self, interaction: discord.Interaction, name: str, on: bool):
        if not await self._staff(interaction):
            return
        name = name.strip().lower().replace(" ", "_")
        async with self.lock:
            ac = self.state.setdefault("academy", [])
            if on and name not in ac:
                ac.append(name)
            if not on and name in ac:
                ac.remove(name)
            self.save()
        await interaction.response.send_message(
            f"Academy event **{name}** is now **{'on' if on else 'off'}**. Now on: {', '.join(ac) or 'none'}.",
            ephemeral=True)

    @world_group.command(name="rep", description="(staff) How a place feels about someone.")
    async def rep(self, interaction: discord.Interaction, member: discord.Member):
        if not await self._staff(interaction):
            return
        s = self.state["students"].get(str(member.id))
        if not s:
            return await interaction.response.send_message(f"{member.display_name} hasn't explored yet.", ephemeral=True)
        reps = ", ".join(f"{self.world.places[k].name}: {v} ({tone_for(v)})" for k, v in s.get("rep", {}).items()
                         if k in self.world.places) or "no opinions yet"
        secrets_found = [f for f in s.get("flags", []) if ":" in f and "@" not in f]
        await interaction.response.send_message(
            f"**{member.display_name}** - {reps}\n{sum(s.get('items', {}).values())} items · "
            f"secrets: {', '.join(secrets_found) or 'none'}", ephemeral=True)

    @world_group.command(name="give", description="(staff) Put an item in someone's satchel.")
    async def give(self, interaction: discord.Interaction, member: discord.Member, item: str,
                   count: app_commands.Range[int, 1, 20] = 1):
        if not await self._staff(interaction):
            return
        if item not in self.world.items:
            return await interaction.response.send_message("No such item.", ephemeral=True)
        async with self.lock:
            self.world.give(self.student(member), item, count)
            self.save()
        await interaction.response.send_message(f"Gave {self.world.item_line(item, count)} to {member.display_name}.",
                                                ephemeral=True)

    @give.autocomplete("item")
    async def _all_items(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=f"{v['name']} ({RARITY_LABEL[v['rarity']]})", value=k)
                for k, v in self.world.items.items() if current.lower() in v["name"].lower()][:25]

    @world_group.command(name="channel", description="(staff) Set which channel a place lives in.")
    @app_commands.choices(place=PLACE_CHOICES)
    async def channel(self, interaction: discord.Interaction, place: str, channel: discord.TextChannel):
        if not await self._staff(interaction):
            return
        self.state["channels"][place] = channel.id
        self.save()
        await interaction.response.send_message(f"{self.world.places[place].name.capitalize()} now lives in {channel.mention}.",
                                                ephemeral=True)

    @world_group.command(name="reload", description="(staff) Reload the places' writing without restarting.")
    async def reload(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        fresh = load_world(self.world.rng)
        if not fresh.items or not all(p.c.get("areas") for p in fresh.places.values()):
            return await interaction.response.send_message("Couldn't read the world files - nothing changed.", ephemeral=True)
        self.world = fresh
        await interaction.response.send_message("🌿 Reloaded.", ephemeral=True)

    async def cog_app_command_error(self, interaction, error):
        log.exception("World command failed", exc_info=error)
        msg = "Something tangled. Try again in a moment."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(WorldCog(bot))
