"""
Beasts of Velmora. 70 creatures, 14 in each place, to find and befriend.

    /approach                 - befriend the beast that's here, if you have what it wants
    /bestiary [member]        - every beast you've befriended, and the ones still out there
    /summon <beast>           - call one of your beasts to do something cute or cool (just for show)
    /beastadmin spawn [beast] - (staff) make a beast appear right now
    /beastadmin channel #ch   - (staff) where beasts appear
    /beastadmin status        - (staff) what's out there, and when the next one comes

About 5-6 times a day (for the whole server, not per place) a beast wanders
into the explore channel and says exactly what it wants. The first person
to /approach with those items in their satchel befriends it - the items
are used up. It slips away after 10 minutes if nobody does.

Rarer beasts turn up less often, and a few only appear after dark
(8 PM - 6 AM, Velmora time). Befriending pays house points by rarity -
only the first time you befriend that particular beast - up to 5 beast
points per person per day. /summon is purely for fun: no points, no
advantage.

Collector ranks rise with how many different beasts you've befriended, and
titles are earned for finishing a place, taming a legendary, or taming all
five legendaries. Both show on /bestiary and /profile.
"""

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.world_engine import is_night

log = logging.getLogger("velmora.beasts")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "beasts_state.json"
BEASTS_PATH = DATA_DIR / "beasts.json"

DEFAULT_CHANNEL_ID = int(os.getenv("BEAST_CHANNEL_ID", "1552403888890576916") or 0)  # #explore-velmora

# Timing: 5-6 sightings a day across the whole server.
GAP_MIN_HOURS = 2.5
GAP_MAX_HOURS = 6.0          # average gap ~4.25h -> ~5.6 sightings a day
STAY_MINUTES = 10

RARITY_WEIGHTS = {"common": 50, "uncommon": 30, "rare": 15, "legendary": 5}
RARITY_POINTS = {"common": 1, "uncommon": 2, "rare": 3, "legendary": 5}
RARITY_COLORS = {"common": 0x7FA66A, "uncommon": 0x4F8FC0, "rare": 0x9B59B6, "legendary": 0xE0A526}
SIGHTING_COLOR = 0x2E8B44  # every "X appears!" alert, regardless of rarity, so it never blends in
RARITY_LABEL = {"common": "Common", "uncommon": "Uncommon", "rare": "Rare", "legendary": "Legendary"}
DAILY_POINT_CAP = 5
WINDOW = 24 * 3600
SUMMON_COOLDOWN = 60
LINGER_GRACE = 120           # Lingering Charm: wearers can still approach this long after a beast leaves

PLACES = {
    "garden": "The Garden",
    "library": "The Library",
    "dungeons": "The Dungeons",
    "forbidden_woods": "The Forbidden Woods",
    "observatory": "The Observatory",
}
PLACE_EMOJI = {"garden": "🌿", "library": "📚", "dungeons": "🕯️", "forbidden_woods": "🌲", "observatory": "🔭"}
PLACE_SHORT = {"garden": "Garden", "library": "Library", "dungeons": "Dungeons",
               "forbidden_woods": "Woods", "observatory": "Observatory"}

# (distinct beasts befriended, rank) - lowest first
RANKS = [
    (0, "Unacquainted"),
    (1, "Beast-Spotter"),
    (10, "Tracker"),
    (20, "Handler"),
    (35, "Beastkeeper"),
    (50, "Warden of Wild Things"),
    (70, "Beastmaster"),
]


def load_beasts() -> dict:
    with open(BEASTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def rank_for(count: int) -> str:
    title = RANKS[0][1]
    for threshold, name in RANKS:
        if count >= threshold:
            title = name
    return title


def rank_index(count: int) -> int:
    idx = 0
    for i, (threshold, _) in enumerate(RANKS):
        if count >= threshold:
            idx = i
    return idx


class Beasts(commands.Cog):
    admin = app_commands.Group(name="beastadmin", description="(staff) Run the beast sightings.")

    def __init__(self, bot: commands.Bot, rng: Optional[random.Random] = None):
        self.bot = bot
        self.rng = rng or random.Random()
        self.beasts = load_beasts()
        self.state = self._load()
        self.lock = asyncio.Lock()
        self._summoned: dict[int, float] = {}

    async def cog_load(self):
        self.tick.start()

    async def cog_unload(self):
        self.tick.cancel()

    # ------------------------------------------------------------- storage

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        except (OSError, json.JSONDecodeError):
            log.exception("Beast state unreadable - starting fresh.")
            state = {}
        for key in ("collections", "paid", "rank_seen", "titles_seen"):
            state.setdefault(key, {})
        state.setdefault("sighting", None)
        state.setdefault("channel_id", None)
        state.setdefault("next_at", time.time() + self._gap())
        return state

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save beast state.")

    def _gap(self) -> float:
        return self.rng.uniform(GAP_MIN_HOURS, GAP_MAX_HOURS) * 3600

    # ---------------------------------------------------------- collection

    def collection(self, user_id: int) -> dict:
        return self.state["collections"].get(str(user_id), {})

    def count_of(self, user_id: int) -> int:
        return len(self.collection(user_id))

    def titles_of(self, user_id: int) -> list[str]:
        have = set(self.collection(user_id))
        titles = []
        for place, name in PLACES.items():
            keys = {k for k, b in self.beasts.items() if b["place"] == place}
            if keys and keys <= have:
                titles.append(f"Keeper of {name.replace('The ', 'the ')}")
        legends = {k for k, b in self.beasts.items() if b["rarity"] == "legendary"}
        if have & legends:
            titles.append("Legend-Tamer")
        if legends and legends <= have:
            titles.append("Mythkeeper")
        return titles

    def points_today(self, user_id: int, now: float) -> int:
        rows = [r for r in self.state["paid"].get(str(user_id), []) if now - r[0] < WINDOW]
        self.state["paid"][str(user_id)] = rows
        return sum(r[1] for r in rows)

    def items_line(self, wants: list[str]) -> str:
        world = self.bot.get_cog("World")
        if world:
            return " + ".join(world.world.item_line(i) for i in wants)
        return " + ".join(wants)

    def channel_id(self) -> int:
        return self.state.get("channel_id") or DEFAULT_CHANNEL_ID

    def _boss_trophies(self, user_id: int) -> dict:
        """Descent boss trophies (see cogs/descent.py) - a separate, cosmetic-only
        collection that plugs into /summon and /bestiary without touching the
        real beast collection, its counts, or its ranks."""
        descent = self.bot.get_cog("Descent")
        return descent.boss_trophies(user_id) if descent else {}

    # ------------------------------------------------------------ spawning

    def pick_beast(self, now: float) -> str:
        night = is_night(now)
        eligible = {k: b for k, b in self.beasts.items() if night or not b.get("night")}
        rarities = [r for r in RARITY_WEIGHTS if any(b["rarity"] == r for b in eligible.values())]
        rarity = self.rng.choices(rarities, weights=[RARITY_WEIGHTS[r] for r in rarities], k=1)[0]
        pool = sorted(k for k, b in eligible.items() if b["rarity"] == rarity)
        return self.rng.choice(pool)

    def sighting_embed(self, key: str, expires: float) -> discord.Embed:
        b = self.beasts[key]
        n = len(b["wants"])
        desc = (f"{b['sighting']}\n\n"
                f"**It wants:** {self.items_line(b['wants'])}\n\n"
                f"First to `/approach` with {'it' if n == 1 else 'them'} in their satchel befriends it. "
                f"It'll slip away <t:{int(expires)}:R>.")
        embed = discord.Embed(title=f"{b['emoji']} A {b['name']} appears!", description=desc,
                              color=SIGHTING_COLOR)
        embed.set_footer(text=f"{RARITY_LABEL[b['rarity']]} • from {PLACES[b['place']]}"
                              + (" • only seen after dark" if b.get("night") else ""))
        return embed

    async def spawn(self, key: Optional[str] = None, channel=None, now: Optional[float] = None) -> Optional[str]:
        """Put a beast in the channel. Returns its key, or None if it couldn't."""
        now = now if now is not None else time.time()
        channel = channel or self.bot.get_channel(self.channel_id())
        if channel is None:
            log.warning("Beast channel %s not found - skipping this sighting", self.channel_id())
            self.state["next_at"] = now + self._gap()
            self.save()
            return None
        key = key or self.pick_beast(now)
        expires = now + STAY_MINUTES * 60
        try:
            msg = await channel.send(embed=self.sighting_embed(key, expires))
        except discord.DiscordException:
            log.exception("Could not post a beast sighting")
            self.state["next_at"] = now + self._gap()
            self.save()
            return None
        self.state["sighting"] = {"beast": key, "channel_id": channel.id, "message_id": msg.id,
                                  "at": now, "expires": expires}
        self.state["next_at"] = now + self._gap()
        self.save()
        log.info("Beast sighting: %s in %s", key, channel.id)
        adorn = self.bot.get_cog("Adornments")
        if adorn and self.beasts[key].get("night"):
            asyncio.ensure_future(adorn.notify_nightwatch(self.beasts[key], channel.id))
        return key

    async def _edit_sighting(self, sighting: dict, embed: discord.Embed):
        channel = self.bot.get_channel(sighting["channel_id"])
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(sighting["message_id"])
            await msg.edit(embed=embed)
        except discord.DiscordException:
            log.info("Could not update an old beast sighting message")

    @tasks.loop(seconds=30)
    async def tick(self):
        try:
            await self._tick(time.time())
        except Exception:
            log.exception("Beast tick failed")

    async def _tick(self, now: float):
        s = self.state.get("sighting")
        if s and now >= s["expires"] and not s.get("left"):
            # it's gone - but anyone wearing a Lingering Charm gets a short grace
            s["left"] = True
            self.save()
            b = self.beasts.get(s["beast"])
            if b:
                await self._edit_sighting(s, discord.Embed(
                    description=f"{b['emoji']} The {b['name']} slipped away. Maybe next time.",
                    color=0x7A7A7A))
            return
        if s and now >= s["expires"] + LINGER_GRACE:
            self.state["sighting"] = None
            self.save()
            return
        if (not s or s.get("left")) and now >= self.state.get("next_at", 0):
            await self.spawn(now=now)

    @tick.before_loop
    async def _before_tick(self):
        await self.bot.wait_until_ready()

    # ----------------------------------------------------------- befriending

    def befriend(self, member, key: str, now: float) -> dict:
        """Record the friendship, pay points if earned, and work out any
        rank-up or new titles. Returns what happened."""
        uid = str(member.id)
        b = self.beasts[key]
        col = self.state["collections"].setdefault(uid, {})
        first = key not in col
        entry = col.setdefault(key, {"n": 0, "first": now})
        entry["n"] += 1

        out = {"first": first, "points": 0, "house": None, "capped": False, "notes": []}
        store = self.bot.get_cog("Store")
        house = store.member_house(member) if store else None
        out["house"] = house
        if first and store and house:
            want = RARITY_POINTS[b["rarity"]]
            room = max(0, DAILY_POINT_CAP - self.points_today(member.id, now))
            pts = min(want, room)
            out["capped"] = pts < want
            if pts:
                store.record(house=house, delta=pts,
                             actor_id=self.bot.user.id if self.bot.user else 0,
                             target_id=member.id, reason=f"Befriended a {b['name']}")
                self.state["paid"].setdefault(uid, []).append([now, pts])
                out["points"] = pts

        count = len(col)
        idx = rank_index(count)
        if idx > self.state["rank_seen"].get(uid, 0):
            self.state["rank_seen"][uid] = idx
            out["notes"].append(f"⬆️ **{member.display_name}** is now a **{rank_for(count)}** "
                                f"({count} of {len(self.beasts)} beasts befriended).")
        seen = set(self.state["titles_seen"].get(uid, []))
        for title in self.titles_of(member.id):
            if title not in seen:
                seen.add(title)
                out["notes"].append(f"🏅 **{member.display_name}** has earned the title **{title}**!")
        self.state["titles_seen"][uid] = sorted(seen)
        return out

    @app_commands.command(name="approach", description="Try to befriend the beast that's here.")
    async def approach(self, interaction: discord.Interaction):
        now = time.time()
        async with self.lock:
            s = self.state.get("sighting")
            adorn = self.bot.get_cog("Adornments")
            grace = adorn.linger_seconds(interaction.user.id) if adorn else 0
            if not s or now >= s["expires"] + min(grace, LINGER_GRACE):
                await interaction.response.send_message(
                    "There's no beast here right now. Keep an eye on the explore channel.", ephemeral=True)
                return
            lingered = now >= s["expires"]
            if interaction.channel_id != s["channel_id"]:
                await interaction.response.send_message(
                    f"The beast is in <#{s['channel_id']}>.", ephemeral=True)
                return
            key = s["beast"]
            b = self.beasts[key]
            world = self.bot.get_cog("World")
            if world is None:
                await interaction.response.send_message("The satchels are out of reach right now.",
                                                        ephemeral=True)
                return
            async with world.lock:
                student = world.student(interaction.user)
                have = student.get("items", {})
                missing = [i for i in b["wants"] if have.get(i, 0) < 1]
                if missing:
                    await interaction.response.send_message(
                        f"The {b['name']} sniffs at you and waits. It wants {self.items_line(b['wants'])} - "
                        f"you're missing {self.items_line(missing)}.", ephemeral=True)
                    return
                for i in b["wants"]:
                    world.world.take(student, i)
                world.save()
            out = self.befriend(interaction.user, key, now)
            self.state["sighting"] = None
            self.save()

        from cogs.store import HOUSES
        lines = [f"{b['emoji']} **{interaction.user.display_name}** offers {self.items_line(b['wants'])} "
                 f"and befriends the **{b['name']}**!", f"*{b['desc']}*"]
        if lingered:
            lines.insert(1, "⏳ *It had nearly gone, but the Lingering Charm made it look back.*")
        if out["first"]:
            if out["points"]:
                h = HOUSES.get(out["house"], {})
                lines.append(f"+{out['points']} to {h.get('emoji', '')} {h.get('name', out['house'])}"
                             + (" (daily beast points reached)" if out["capped"] else ""))
            elif out["capped"]:
                lines.append("*(They've already earned today's beast points.)*")
        else:
            lines.append(f"*They've befriended a {b['name']} before - it's added to their bestiary again. "
                         "(Points only come the first time.)*")
        if b["rarity"] in ("rare", "legendary") and out["first"]:
            lines.append(f"🌟 **A {RARITY_LABEL[b['rarity']].lower()} beast!** The whole castle is talking about it.")
        lines += out["notes"]
        await interaction.response.send_message(embed=discord.Embed(
            description="\n".join(lines), color=RARITY_COLORS[b["rarity"]]))
        await self._edit_sighting(s, discord.Embed(
            description=f"{b['emoji']} The {b['name']} went home with **{interaction.user.display_name}**.",
            color=RARITY_COLORS[b["rarity"]]))
        if adorn:
            try:
                await adorn.check_member(interaction.user)
            except Exception:
                log.exception("Gear check after befriending failed")

    # ------------------------------------------------------------- bestiary

    def bestiary_embed(self, member) -> discord.Embed:
        col = self.collection(member.id)
        count = len(col)
        titles = self.titles_of(member.id)
        desc = [f"**{rank_for(count)}** • {count} of {len(self.beasts)} beasts befriended"]
        if titles:
            desc.append("🏅 " + " • ".join(titles))
        embed = discord.Embed(title=f"{member.display_name}'s Bestiary", description="\n".join(desc),
                              color=0x5B8C5A)
        order = {"common": 0, "uncommon": 1, "rare": 2, "legendary": 3}
        for place, name in PLACES.items():
            keys = sorted((k for k, b in self.beasts.items() if b["place"] == place),
                          key=lambda k: (order[self.beasts[k]["rarity"]], self.beasts[k]["name"]))
            got = sum(1 for k in keys if k in col)
            lines = []
            for k in keys:
                b = self.beasts[k]
                if k in col:
                    n = col[k]["n"]
                    lines.append(f"{b['emoji']} {b['name']}" + (f" ×{n}" if n > 1 else ""))
                else:
                    lines.append(f"❔ *??? ({RARITY_LABEL[b['rarity']].lower()})*")
            embed.add_field(name=f"{PLACE_EMOJI[place]} {name} — {got}/{len(keys)}",
                            value="\n".join(lines), inline=True)
        trophies = self._boss_trophies(member.id)
        if trophies:
            lines = [f"{t['emoji']} {t['name']}" for t in trophies.values()]
            embed.add_field(name="👑 Descent Trophies", value="\n".join(lines), inline=False)
        return embed

    @app_commands.command(name="bestiary", description="Every beast you've befriended - or anyone's.")
    @app_commands.describe(member="Whose bestiary (leave blank for your own)")
    async def bestiary(self, interaction: discord.Interaction, member: discord.Member = None):
        embed = self.bestiary_embed(member or interaction.user)
        adorn = self.bot.get_cog("Adornments")
        if (member is None or member.id == interaction.user.id) and adorn and adorn.has_perk(interaction.user.id, "tracker"):
            s = self.state.get("sighting")
            if s and time.time() < s["expires"]:
                hint = f"one is out right now in <#{s['channel_id']}>!"
            else:
                # rounded to the nearest quarter hour - a hum, not a clock
                at = int(round(self.state.get("next_at", 0) / 900) * 900)
                hint = f"the next beast should turn up around <t:{at}:t> (<t:{at}:R>)."
            embed.description += f"\n💍 Your Tracker's Band hums: {hint}"
        await interaction.response.send_message(embed=embed)

    # --------------------------------------------------------------- summon

    def _entry(self, user_id: int, key: str):
        """Look up something summonable: a real befriended beast, or a Descent
        boss trophy. Returns (data, is_boss_trophy), or (None, False)."""
        if key.startswith("boss:"):
            trophy = self._boss_trophies(user_id).get(key)
            return (trophy, True) if trophy else (None, False)
        col = self.collection(user_id)
        if key in col and key in self.beasts:
            return self.beasts[key], False
        return None, False

    @app_commands.command(name="summon", description="Call one of your beasts to show off. Just for fun.")
    @app_commands.describe(beast="Which of your beasts (or a Descent boss trophy)",
                           second="(Beastmaster's Totem) a second beast to call at the same time")
    async def summon(self, interaction: discord.Interaction, beast: str, second: str = None):
        b, b_is_boss = self._entry(interaction.user.id, beast)
        if b is None:
            await interaction.response.send_message(
                "You haven't befriended that beast yet. `/bestiary` shows the ones you have.", ephemeral=True)
            return
        adorn = self.bot.get_cog("Adornments")
        b2 = b2_is_boss = None
        if second is not None:
            if not (adorn and adorn.has_perk(interaction.user.id, "totem")):
                await interaction.response.send_message(
                    "Only someone wearing the **Beastmaster's Totem** can call two beasts at once.", ephemeral=True)
                return
            if second == beast:
                await interaction.response.send_message(
                    "Pick a different beast you've befriended for the second one.", ephemeral=True)
                return
            b2, b2_is_boss = self._entry(interaction.user.id, second)
            if b2 is None:
                await interaction.response.send_message(
                    "Pick a different beast you've befriended for the second one.", ephemeral=True)
                return
        now = time.time()
        last = self._summoned.get(interaction.user.id, 0)
        if now - last < SUMMON_COOLDOWN:
            await interaction.response.send_message(
                f"Your beasts need a moment. Try again in {int(SUMMON_COOLDOWN - (now - last))} seconds.",
                ephemeral=True)
            return
        self._summoned[interaction.user.id] = now
        moment = self.rng.choice(b["summons"]).format(owner=interaction.user.display_name)
        title = f"{b['emoji']} {interaction.user.display_name} summons their {b['name']}!"
        if b2 is not None:
            moment += "\n\n" + self.rng.choice(b2["summons"]).format(owner=interaction.user.display_name)
            title = f"{b['emoji']}{b2['emoji']} {interaction.user.display_name} summons their {b['name']} and {b2['name']}!"
        flourish = adorn.summon_flourish(interaction.user.id) if adorn else None
        if flourish:
            moment += f"\n\n{flourish}"
        color = 0xE0A526 if (b_is_boss or b2_is_boss) else RARITY_COLORS[b["rarity"]]
        embed = discord.Embed(title=title[:256], description=moment, color=color)

        # A Descent boss trophy shows its real portrait art - the thing that
        # makes it feel like a genuine trophy rather than just another entry.
        image_path = (b.get("image_path") if b_is_boss else None) or \
                     (b2.get("image_path") if b2_is_boss else None)
        file = None
        if image_path and image_path.exists():
            file = discord.File(image_path, filename=image_path.name)
            embed.set_image(url=f"attachment://{image_path.name}")

        if file:
            await interaction.response.send_message(embed=embed, file=file)
        else:
            await interaction.response.send_message(embed=embed)

    @summon.autocomplete("second")
    @summon.autocomplete("beast")
    async def _your_beasts(self, interaction: discord.Interaction, current: str):
        col = self.collection(interaction.user.id)
        out = []
        for k in col:
            b = self.beasts.get(k)
            if b and current.lower() in b["name"].lower():
                out.append(app_commands.Choice(name=f"{b['name']} ({PLACE_SHORT[b['place']]})", value=k))
        for k, t in self._boss_trophies(interaction.user.id).items():
            if current.lower() in t["name"].lower():
                out.append(app_commands.Choice(name=f"{t['name']} (Descent Boss)", value=k))
        return sorted(out, key=lambda c: c.name)[:25]

    # ---------------------------------------------------------------- staff

    async def _staff(self, interaction) -> bool:
        store = self.bot.get_cog("Store")
        if store and store.is_staff(interaction.user):
            return True
        await interaction.response.send_message("That's for staff.", ephemeral=True)
        return False

    @admin.command(name="spawn", description="(staff) Make a beast appear right now.")
    @app_commands.describe(beast="Which beast (leave blank for a random one)")
    async def admin_spawn(self, interaction: discord.Interaction, beast: str = None):
        if not await self._staff(interaction):
            return
        if beast and beast not in self.beasts:
            await interaction.response.send_message("I don't know that beast.", ephemeral=True)
            return
        s = self.state.get("sighting")
        if s and time.time() < s["expires"]:
            await interaction.response.send_message(
                f"A {self.beasts[s['beast']]['name']} is already out in <#{s['channel_id']}>.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        key = await self.spawn(key=beast)
        await interaction.followup.send(
            f"A {self.beasts[key]['name']} has appeared in <#{self.channel_id()}>." if key
            else "Couldn't post it - check the beast channel is set and I can talk there.", ephemeral=True)

    @admin_spawn.autocomplete("beast")
    async def _all_beasts(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=f"{b['name']} ({RARITY_LABEL[b['rarity']]}, {PLACE_SHORT[b['place']]})",
                                    value=k)
                for k, b in sorted(self.beasts.items(), key=lambda kv: kv[1]["name"])
                if current.lower() in b["name"].lower()][:25]

    @admin.command(name="channel", description="(staff) Set which channel beasts appear in.")
    async def admin_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await self._staff(interaction):
            return
        self.state["channel_id"] = channel.id
        self.save()
        await interaction.response.send_message(f"Beasts will now appear in {channel.mention}.", ephemeral=True)

    @admin.command(name="status", description="(staff) What's out there, and when the next beast comes.")
    async def admin_status(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        s = self.state.get("sighting")
        lines = [f"Beasts appear in <#{self.channel_id()}>."]
        if s and time.time() < s["expires"]:
            lines.append(f"Out now: **{self.beasts[s['beast']]['name']}** (leaves <t:{int(s['expires'])}:R>).")
        else:
            lines.append(f"Next sighting: <t:{int(self.state.get('next_at', 0))}:R>.")
        befriended = sum(len(c) for c in self.state["collections"].values())
        lines.append(f"{len(self.state['collections'])} students have befriended {befriended} beasts between them.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Beasts(bot))
