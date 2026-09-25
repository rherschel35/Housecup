"""
Adornments: design your wizard, collect gear, and see it all in the Mirror.

    /wizard                    - design how your wizard looks (menus, with a live preview)
    /mirror [member]           - someone's wizard as a trading card: look, gear, wand, title, stats
    /jewelbox [member]         - what you own, what you're wearing, what you could craft
    /craft <piece>             - make a piece from materials in your satchel
    /wear <piece>              - put on a piece you own
    /remove <slot>             - take off whatever's in a slot
    /title [title]             - choose which earned title shows under your name
    /cheer                     - (House Cup Bracelet) a celebration for your house
    /whistle                   - (Beastcaller's Whistle) call the next beast now, once a week
    /secrets                   - (Keeper's Talisman) how many secrets you haven't found yet
    /nightwatch on|off         - (Nightwatch Pendant) night-beast heads-up on or off
    /adornadmin give|take|channel|status   - staff

40 pieces over four slots (necklace, bracelet, ring, talisman). 26 are
crafted from satchel materials; 14 are earned automatically from
achievements and announced. Perks only come on earned pieces, only work
while worn, and never touch duels or house points.
"""

from __future__ import annotations

import asyncio
import io
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

from cogs import mirror_art as art
from cogs.gear_data import GEAR, PLACE_OF, RARITY_LABEL, RARITY_ORDER, SLOTS, by_slot, crafted, earned

log = logging.getLogger("velmora.adornments")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "adornments_state.json"

GOLD = 0xD6A847
RARITY_COLOR = {"common": 0x8FA38A, "uncommon": 0x4F8FC0, "rare": 0x9B59B6, "legendary": 0xE0A526}
RARITY_DOT = {"common": "⚪", "uncommon": "🔵", "rare": "🟣", "legendary": "🟡"}

LINGER_SECONDS = 120              # Lingering Charm grace after a beast leaves
WHISTLE_COOLDOWN = 7 * 24 * 3600  # Beastcaller's Whistle: once a week
CHEER_COOLDOWN = 10 * 60
SEEKER_CHANCE = 0.35              # Seeker's Ring: chance of a find when /explore turns up nothing
DOUBLE_FIND_CHANCE = 0.25         # Magpie's Eye: chance of a second copy of what you found
KINDRED_BONUS = 1                 # Kindred Bracelet: extra friendship per feed/pet/play
BONUS_RARITIES = ("common", "uncommon", "rare")   # perk finds never include point-paying rarities
DROP_WEIGHT = {"common": 60, "uncommon": 28, "rare": 10}

# Titles and how impressive they are - /title "Automatic" shows the highest.
TITLE_SCORE = {
    "Mythkeeper": 100, "Legend": 96, "Tri-Wizard Champion": 94, "House Cup Champion": 92,
    "Beastmaster": 90, "Master of the Circle": 86, "Champion of the Circle": 82,
    "Warden of Wild Things": 78, "Legend-Tamer": 74, "Spellblade": 62, "Beastkeeper": 60,
    "Handler": 46, "Duelist": 42, "Tracker": 36, "Apprentice": 26, "Beast-Spotter": 16, "Novice": 10,
}
KEEPER_SCORE, SIGNATURE_SCORE = 66, 52

BELL_LINES = {
    "fox": ["The bell on your ring chimes and {fam} does a full zoomie lap of the room before settling.",
            "{fam} hears the bell, sits up very straight, and offers you a paw. Where did it learn that?"],
    "raven": ["At the sound of the bell, {fam} bows. Deeply. It has clearly been practising.",
              "{fam} rings the bell back at you with its beak, twice, like a password."],
    "salamander": ["The bell chimes and {fam} glows a warm, happy orange from nose to tail.",
                   "{fam} curls around your wrist at the sound of the bell, warm as a mug of cocoa."],
}

CHEER_LINES = [
    "raises the House Cup Bracelet high and the whole common room roars!",
    "starts a chant for their house. It catches. It spreads. The portraits join in.",
    "throws a fistful of gold sparks into the air. They spell out the house name before they fall.",
    "bangs a goblet on the table until everyone is cheering. Nobody is sure why. Nobody minds.",
]


def _read(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError):
        log.exception("Adornments state unreadable - starting fresh.")
        return default


def gear_line(key: str, with_slot: bool = False) -> str:
    g = GEAR[key]
    slot = f"{SLOTS[g['slot']][1]} " if with_slot else ""
    return f"{slot}{RARITY_DOT[g['rarity']]} **{g['name']}**"


class Adornments(commands.Cog):
    admin = app_commands.Group(name="adornadmin", description="(staff) Gear and the Mirror.")

    def __init__(self, bot: commands.Bot, rng: Optional[random.Random] = None):
        self.bot = bot
        self.rng = rng or random.Random()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = _read(STATE_PATH, {})
        self.state.setdefault("members", {})
        self.state.setdefault("backfilled", False)
        self.state.setdefault("channel_id", None)
        self._cheered: dict[int, float] = {}

    async def cog_load(self):
        self.award_loop.start()

    async def cog_unload(self):
        self.award_loop.cancel()

    # ================================================================ storage

    def save(self) -> None:
        try:
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save adornments state.")

    def rec(self, user_id: int) -> dict:
        r = self.state["members"].setdefault(str(user_id), {})
        r.setdefault("look", None)
        r.setdefault("owned", {})
        r.setdefault("worn", {})
        r.setdefault("title", None)
        r.setdefault("nightwatch", True)
        r.setdefault("whistle_at", 0)
        return r

    def peek(self, user_id: int) -> dict:
        return self.state["members"].get(str(user_id), {})

    def owns(self, user_id: int, key: str) -> bool:
        return key in self.peek(user_id).get("owned", {})

    def worn(self, user_id: int) -> dict:
        return {s: k for s, k in self.peek(user_id).get("worn", {}).items() if k in GEAR}

    def look_of(self, user_id: int) -> dict:
        return art.clean_look(self.peek(user_id).get("look"), user_id)

    # ================================================================ perks (asked by other cogs)

    def has_perk(self, user_id: int, perk: str) -> bool:
        return any(GEAR[k].get("perk") == perk for k in self.worn(user_id).values())

    def linger_seconds(self, user_id: int) -> int:
        return LINGER_SECONDS if self.has_perk(user_id, "linger") else 0

    def friendship_bonus(self, user_id: int) -> int:
        return KINDRED_BONUS if self.has_perk(user_id, "kindred") else 0

    def bell_line(self, user_id: int, species: str, fam: str) -> Optional[str]:
        if not self.has_perk(user_id, "bell") or species not in BELL_LINES:
            return None
        return "🔔 " + self.rng.choice(BELL_LINES[species]).format(fam=fam)

    def summon_flourish(self, user_id: int) -> Optional[str]:
        if self.has_perk(user_id, "legend_aura"):
            return "✨ *A ring of golden light flares around them — the Legend's Tooth remembers.*"
        return None

    def bonus_finds(self, user_id: int, student: dict, world, place: str,
                    found: Optional[str], exploring: bool) -> list[tuple[str, str]]:
        """Called by /explore and /forage inside the World lock. Gives any
        perk finds straight into the satchel and returns (item, why) pairs
        for the message. Never a points-paying rarity."""
        out = []
        items = world.items
        if found and self.has_perk(user_id, "double_find") and items.get(found, {}).get("rarity") in BONUS_RARITIES:
            if self.rng.random() < DOUBLE_FIND_CHANCE:
                world.give(student, found)
                out.append((found, "Your Magpie's Eye spots a second one"))
        if exploring and not found and self.has_perk(user_id, "seeker"):
            if self.rng.random() < SEEKER_CHANCE:
                P = world.places.get(place)
                pool = [i for i in (P.c.get("item_ids", []) if P else [])
                        if items.get(i, {}).get("rarity") in BONUS_RARITIES]
                if pool:
                    pick = self.rng.choices(pool, weights=[DROP_WEIGHT[items[i]["rarity"]] for i in pool], k=1)[0]
                    world.give(student, pick)
                    out.append((pick, "Your Seeker's Ring warms, and you find"))
        return out

    async def notify_nightwatch(self, beast: dict, channel_id: int):
        """DM everyone wearing the Nightwatch Pendant (who hasn't switched it
        off) that a night beast has appeared."""
        wearers = [int(uid) for uid, r in self.state["members"].items()
                   if r.get("nightwatch", True) and self.has_perk(int(uid), "nightwatch")]
        for uid in wearers:
            user = self.bot.get_user(uid)
            if user is None:
                continue
            try:
                await user.send(f"🌙 Your Nightwatch Pendant stirs. A **{beast['name']}** {beast['emoji']} "
                                f"has come out in <#{channel_id}>. It won't stay long.\n"
                                f"-# Turn these off with /nightwatch off.")
            except discord.DiscordException:
                pass

    # ================================================================ achievements

    def _cogs(self):
        g = self.bot.get_cog
        return g("Beasts"), g("Familiars"), g("Duels"), g("World"), g("Store")

    @staticmethod
    def findable_secrets(world, place: str, house: Optional[str]) -> set[str]:
        """The secrets in a place that this person can find for good: one-off
        secrets, not tied to an event, not another house's, and not the
        apology you only get after upsetting a place."""
        out = set()
        P = world.places.get(place)
        for sec in (P.c.get("secrets", []) if P else []):
            if sec.get("once", "user") != "user" or sec.get("event") or sec["id"] == "apology":
                continue
            houses = (sec.get("requires") or {}).get("houses")
            if houses and house not in houses:
                continue
            out.add(f"{place}:{sec['id']}")
        return out

    def secrets_found(self, user_id: int) -> set[str]:
        world_cog = self.bot.get_cog("World")
        if not world_cog:
            return set()
        s = world_cog.state["students"].get(str(user_id), {})
        valid = set()
        for key, P in world_cog.world.places.items():
            for sec in P.c.get("secrets", []):
                valid.add(f"{key}:{sec['id']}")
        return {f.split("@")[0] for f in s.get("flags", []) if f.split("@")[0] in valid}

    def _house(self, user_id: int, member=None) -> Optional[str]:
        store = self.bot.get_cog("Store")
        if not store:
            return None
        member = member or self._member(user_id)
        if member is None:
            return None
        try:
            return store.member_house(member)
        except Exception:
            return None

    def earned_now(self, user_id: int, member=None) -> set[str]:
        """Every earned piece this person qualifies for right now."""
        beasts, fams, duels, world_cog, store = self._cogs()
        got = set()
        if beasts:
            col = set(beasts.collection(user_id))
            allb = beasts.beasts
            n = len(col)
            if any(allb.get(k, {}).get("rarity") == "legendary" for k in col):
                got.add("legendary_beast")
            if sum(1 for k in col if allb.get(k, {}).get("night")) >= 3:
                got.add("night_beasts")
            nibblers = {k for k, b in allb.items() if "nibbler" in b["name"].lower()}
            if nibblers and nibblers <= col:
                got.add("all_nibblers")
            for need, key in ((10, "beasts_10"), (20, "beasts_20"), (35, "beasts_35")):
                if n >= need:
                    got.add(key)
            if allb and set(allb) <= col:
                got.add("beasts_all")
        if fams:
            f = fams.familiar_of(user_id)
            if f:
                if f.get("friendship", 0) >= 50:
                    got.add("familiar_devoted")
                if f.get("friendship", 0) >= 80:
                    got.add("familiar_inseparable")
        if duels:
            if duels.is_champion(user_id):
                got.add("duelist_of_week")
            if duels.wins_of(user_id) >= 60:
                got.add("master_duelist")
        if store:
            h = store.honours(user_id)
            if h.get("cups") or h.get("champion_of"):
                got.add("house_cup")
        if world_cog:
            found = self.secrets_found(user_id)
            if len(found) >= 10:
                got.add("secrets_10")
            house = self._house(user_id, member)
            for place in world_cog.world.places:
                need = self.findable_secrets(world_cog.world, place, house)
                if need and need <= found:
                    got.add("place_secrets")
                    break
        return {k for k in earned() if GEAR[k]["earn"] in got}

    def _member(self, user_id: int):
        for guild in self.bot.guilds:
            m = guild.get_member(user_id)
            if m:
                return m
        return None

    def award(self, user_id: int, key: str) -> bool:
        r = self.rec(user_id)
        if key in r["owned"]:
            return False
        r["owned"][key] = time.time()
        slot = GEAR[key]["slot"]
        if not r["worn"].get(slot):
            r["worn"][slot] = key
        return True

    def announce_channel(self):
        cid = self.state.get("channel_id")
        if not cid:
            beasts = self.bot.get_cog("Beasts")
            cid = beasts.channel_id() if beasts else None
        return self.bot.get_channel(cid) if cid else None

    async def check_member(self, member, announce: bool = True) -> list[str]:
        """Award anything newly earned. Returns the keys awarded."""
        uid = member.id if hasattr(member, "id") else int(member)
        m = member if hasattr(member, "display_name") else self._member(uid)
        new = [k for k in sorted(self.earned_now(uid, m)) if self.award(uid, k)]
        if not new:
            return []
        self.save()
        if announce:
            channel = self.announce_channel()
            if channel is not None:
                name = m.mention if m else f"<@{uid}>"
                for k in new:
                    g = GEAR[k]
                    embed = discord.Embed(
                        title=f"🎁 {g['name']}",
                        description=(f"{name} has earned the **{g['name']}**!\n*{g['earn_text']}*\n\n"
                                     f"{g['desc']}"
                                     + (f"\n\n**Perk:** {g['perk_text']}" if g.get("perk") else "")
                                     + f"\n\nSee it on them with `/mirror`."),
                        color=RARITY_COLOR[g["rarity"]])
                    try:
                        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=False))
                    except discord.DiscordException:
                        log.info("Couldn't announce an earned piece")
        return new

    def _candidate_ids(self) -> set[int]:
        beasts, fams, duels, world_cog, store = self._cogs()
        ids = set()
        if beasts:
            ids |= {int(u) for u in beasts.state.get("collections", {})}
        if fams:
            ids |= {int(u) for u in fams.state.get("members", {})}
        if duels:
            ids |= {int(u) for u in duels.state.get("records", {})}
            c = duels.state.get("champion")
            if c:
                ids.add(int(c["user_id"]))
        if world_cog:
            ids |= {int(u) for u in world_cog.state.get("students", {}) if str(u).isdigit()}
        if store:
            for record in store.state.get("archive", []):
                ids |= {int(u) for u in record.get("winning_members", {}) if str(u).isdigit()}
                ids |= {int(c["id"]) for c in record.get("champions", [])}
        return ids

    async def sweep(self, announce: bool = True) -> int:
        n = 0
        for uid in sorted(self._candidate_ids()):
            try:
                n += len(await self.check_member(self._member(uid) or uid, announce=announce))
            except Exception:
                log.exception("Award check failed for %s", uid)
        return n

    @tasks.loop(minutes=10)
    async def award_loop(self):
        try:
            if not self.state.get("backfilled"):
                n = await self.sweep(announce=False)
                self.state["backfilled"] = True
                self.save()
                log.info("Adornments backfilled: %d earned pieces handed out quietly", n)
            else:
                await self.sweep(announce=True)
        except Exception:
            log.exception("Adornments award loop failed")

    @award_loop.before_loop
    async def _before_loop(self):
        await self.bot.wait_until_ready()

    # ================================================================ titles

    def titles_available(self, member) -> list[str]:
        uid = member.id
        beasts, fams, duels, world_cog, store = self._cogs()
        out = []
        if duels:
            from cogs.duels import rank_for as duel_rank
            w = duels.wins_of(uid)
            if w > 0:
                out.append(duel_rank(w))
            sig = duels.signature_of(uid)
            if sig:
                out.append(sig[1])
            if duels.is_champion(uid):
                out.append("Champion of the Circle")
        if beasts:
            from cogs.beasts import rank_for as beast_rank
            c = beasts.count_of(uid)
            if c > 0:
                out.append(beast_rank(c))
            out += beasts.titles_of(uid)
        if store:
            if store.honours(uid).get("champion_of"):
                out.append("House Cup Champion")
        tw = self.bot.get_cog("TriWizard")
        if tw and tw.titles_of(uid):
            out.append("Tri-Wizard Champion")
        seen, uniq = set(), []
        for t in out:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        return sorted(uniq, key=lambda t: -self._title_score(t))

    @staticmethod
    def _title_score(t: str) -> int:
        if t in TITLE_SCORE:
            return TITLE_SCORE[t]
        if t.startswith("Keeper of"):
            return KEEPER_SCORE
        if t.startswith("the "):
            return SIGNATURE_SCORE
        return 0

    def title_of(self, member) -> Optional[str]:
        avail = self.titles_available(member)
        chosen = self.peek(member.id).get("title")
        if chosen == "none":
            return None
        if chosen and chosen in avail:
            return chosen
        return avail[0] if avail else None

    # ================================================================ the Mirror

    def scene_for(self, member) -> dict:
        from cogs.store import HOUSES
        uid = member.id
        house = self._house(uid, member)
        meta = HOUSES.get(house) if house else None
        worn = self.worn(uid)
        gear = {}
        for slot, key in worn.items():
            v = dict(GEAR[key]["visual"])
            if v.get("color") == "house":
                v["color"] = art.hexint(meta["color"]) if meta else "#D6A847"
            gear[slot] = v
        wands = self.bot.get_cog("Wands")
        beasts = self.bot.get_cog("Beasts")
        beast_emoji = None
        if beasts and self.has_perk(uid, "totem"):
            col = beasts.collection(uid)
            order = {"common": 0, "uncommon": 1, "rare": 2, "legendary": 3}
            if col:
                fav = max(col, key=lambda k: (col[k].get("n", 1),
                                              order.get(beasts.beasts.get(k, {}).get("rarity"), 0)))
                beast_emoji = beasts.beasts.get(fav, {}).get("emoji")
        duels = self.bot.get_cog("Duels")
        owned = self.peek(uid).get("owned", {})
        n_earned = sum(1 for k in owned if "earn" in GEAR.get(k, {}))
        stars = 1 if n_earned == 0 else 2 if n_earned <= 2 else 3 if n_earned <= 6 else 4
        stats = [("Duel wins", duels.wins_of(uid) if duels else 0),
                 ("Beasts", beasts.count_of(uid) if beasts else 0),
                 ("Gear", f"{len(owned)}/{len(GEAR)}")]
        return dict(
            look=self.look_of(uid), user_id=uid, name=member.display_name, title=self.title_of(member),
            house_color=meta["color"] if meta else 0x6C5CE7, house_emoji=meta["emoji"] if meta else "✨",
            gear=gear, wand=wands.wand_of(uid) if wands else None,
            beast_emoji=beast_emoji, stats=stats, stars=stars,
            motto=meta["motto"] if meta else "Not sorted yet. Anything could happen.",
            aura=self.has_perk(uid, "legend_aura"),
            gold_trim=self.has_perk(uid, "cheer"),
            wand_sparks=any(k == "duelists_signet" for k in worn.values()),
        )

    async def render_for(self, member, look_override: Optional[dict] = None) -> bytes:
        scene = self.scene_for(member)
        if look_override:
            scene["look"] = art.clean_look(look_override, member.id)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: art.render(**scene))

    async def mirror_file(self, member, name="mirror.png", look_override=None) -> discord.File:
        png = await self.render_for(member, look_override)
        return discord.File(io.BytesIO(png), filename=name)

    def profile_line(self, user_id: int) -> str:
        owned = len(self.peek(user_id).get("owned", {}))
        if not owned:
            return f"Nothing collected yet\n`/jewelbox` · `/craft`"
        return f"{owned}/{len(GEAR)} collected"

    # ================================================================ /mirror

    @app_commands.command(name="mirror", description="See someone's wizard trading card - look, gear, wand, title and stats.")
    @app_commands.describe(member="Whose reflection (leave blank for your own)")
    async def mirror(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        if getattr(member, "bot", False):
            await interaction.response.send_message("The Mirror shows nothing. Ghosts don't reflect.", ephemeral=True)
            return
        await interaction.response.defer()
        await self.check_member(member)
        try:
            file = await self.mirror_file(member)
        except Exception:
            log.exception("Mirror render failed")
            await interaction.followup.send("The Mirror fogged over. Try again in a moment.")
            return
        embed = discord.Embed(color=GOLD)
        embed.set_image(url="attachment://mirror.png")
        mine = member.id == interaction.user.id
        embed.set_footer(text="Change your look with /wizard • gear with /jewelbox" if mine
                         else f"{member.display_name}'s reflection")
        await interaction.followup.send(embed=embed, file=file)

    # ================================================================ /wizard

    @app_commands.command(name="wizard", description="Design how your wizard looks in the Mirror.")
    async def wizard(self, interaction: discord.Interaction):
        look = self.look_of(interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        view = WizardView(self, interaction.user, look)
        file = await self.mirror_file(interaction.user, look_override=look)
        await interaction.followup.send(content=view.header(), file=file, view=view, ephemeral=True)

    # ================================================================ /jewelbox

    def jewelbox_embed(self, member, viewer_is_owner: bool) -> discord.Embed:
        uid = member.id
        r = self.peek(uid)
        owned = r.get("owned", {})
        worn = self.worn(uid)
        embed = discord.Embed(title=f"💎 {member.display_name}'s Jewel Box",
                              description=f"**{len(owned)} of {len(GEAR)}** pieces collected",
                              color=GOLD)
        world_cog = self.bot.get_cog("World")
        have = {}
        if viewer_is_owner and world_cog:
            have = world_cog.state["students"].get(str(uid), {}).get("items", {})
        for slot, (label, emoji) in SLOTS.items():
            lines = []
            for k in by_slot(slot):
                g = GEAR[k]
                if k in owned:
                    mark = " ← *wearing*" if worn.get(slot) == k else ""
                    lines.append(f"{gear_line(k)}{mark}")
                elif viewer_is_owner and "recipe" in g and all(have.get(i, 0) >= n for i, n in g["recipe"].items()):
                    lines.append(f"🔨 {g['name']} — *ready to craft*")
            hidden = sum(1 for k in by_slot(slot) if k not in owned)
            if hidden:
                lines.append(f"-# {hidden} more to find")
            embed.add_field(name=f"{emoji} {label}s", value="\n".join(lines) or "—", inline=True)
        if viewer_is_owner:
            embed.set_footer(text="/craft to make one • /wear to put it on • /mirror to see it")
        return embed

    @app_commands.command(name="jewelbox", description="Your gear: what you own, what you're wearing, what you can craft.")
    @app_commands.describe(member="Whose jewel box (leave blank for your own)")
    async def jewelbox(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        mine = target.id == interaction.user.id
        if mine:
            await self.check_member(target)
        await interaction.response.send_message(embed=self.jewelbox_embed(target, mine), ephemeral=mine)

    # ================================================================ /craft

    def recipe_text(self, key: str, have: dict | None = None) -> str:
        world_cog = self.bot.get_cog("World")
        parts = []
        for i, n in GEAR[key]["recipe"].items():
            label = world_cog.world.item_line(i, n) if world_cog and i in world_cog.world.items else f"{i} ×{n}"
            if have is not None and have.get(i, 0) < n:
                label += " *(missing)*"
            parts.append(label)
        return " + ".join(parts)

    @app_commands.command(name="craft", description="Make a piece of gear from materials in your satchel.")
    @app_commands.describe(piece="What to make")
    async def craft(self, interaction: discord.Interaction, piece: str):
        g = GEAR.get(piece)
        if not g:
            await interaction.response.send_message("There's no such piece. Pick one from the list.", ephemeral=True)
            return
        if "recipe" not in g:
            await interaction.response.send_message(
                f"The **{g['name']}** can't be crafted. It's earned: *{g['earn_text']}*", ephemeral=True)
            return
        uid = interaction.user.id
        if self.owns(uid, piece):
            await interaction.response.send_message(f"You already have the **{g['name']}**.", ephemeral=True)
            return
        world_cog = self.bot.get_cog("World")
        if not world_cog:
            await interaction.response.send_message("Your satchel is out of reach right now.", ephemeral=True)
            return
        async with world_cog.lock:
            student = world_cog.student(interaction.user)
            have = student.get("items", {})
            if any(have.get(i, 0) < n for i, n in g["recipe"].items()):
                await interaction.response.send_message(
                    f"To make the **{g['name']}** you need {self.recipe_text(piece, have)}.", ephemeral=True)
                return
            for i, n in g["recipe"].items():
                world_cog.world.take(student, i, n)
            world_cog.save()
        r = self.rec(uid)
        r["owned"][piece] = time.time()
        wearing = False
        if not r["worn"].get(g["slot"]):
            r["worn"][g["slot"]] = piece
            wearing = True
        self.save()
        embed = discord.Embed(
            title=f"🔨 {interaction.user.display_name} crafts the {g['name']}",
            description=(f"{g['desc']}\n\n*Used:* {self.recipe_text(piece)}\n"
                         + ("It's on — see it with `/mirror`." if wearing
                            else f"It's in your jewel box. `/wear` it to swap it in.")),
            color=RARITY_COLOR[g["rarity"]])
        embed.set_footer(text=f"{RARITY_LABEL[g['rarity']]} {SLOTS[g['slot']][0].lower()} • {PLACE_OF[g['place']]}")
        await interaction.response.send_message(embed=embed)

    @craft.autocomplete("piece")
    async def _craftable(self, interaction: discord.Interaction, current: str):
        uid = interaction.user.id
        world_cog = self.bot.get_cog("World")
        have = world_cog.state["students"].get(str(uid), {}).get("items", {}) if world_cog else {}
        rows = []
        for k in crafted():
            g = GEAR[k]
            if self.owns(uid, k) or current.lower() not in g["name"].lower():
                continue
            ready = all(have.get(i, 0) >= n for i, n in g["recipe"].items())
            rows.append((0 if ready else 1, g["name"], k,
                         f"{'✅ ' if ready else ''}{g['name']} ({SLOTS[g['slot']][0]}, {PLACE_OF[g['place']][2:].strip()})"))
        rows.sort()
        return [app_commands.Choice(name=label[:100], value=k) for _, _, k, label in rows[:25]]

    # ================================================================ /wear /remove

    @app_commands.command(name="wear", description="Put on a piece of gear you own.")
    @app_commands.describe(piece="Which piece")
    async def wear(self, interaction: discord.Interaction, piece: str):
        uid = interaction.user.id
        g = GEAR.get(piece)
        if not g or not self.owns(uid, piece):
            await interaction.response.send_message("You don't own that piece. `/jewelbox` shows what you have.",
                                                    ephemeral=True)
            return
        r = self.rec(uid)
        old = r["worn"].get(g["slot"])
        r["worn"][g["slot"]] = piece
        self.save()
        swap = f" (swapped out the {GEAR[old]['name']})" if old and old != piece and old in GEAR else ""
        perk = f"\n**Perk active:** {g['perk_text']}" if g.get("perk") else ""
        await interaction.response.send_message(f"You put on the **{g['name']}**{swap}.{perk}", ephemeral=True)

    @wear.autocomplete("piece")
    async def _owned(self, interaction: discord.Interaction, current: str):
        uid = interaction.user.id
        worn = set(self.worn(uid).values())
        out = []
        for k in self.peek(uid).get("owned", {}):
            g = GEAR.get(k)
            if g and current.lower() in g["name"].lower():
                tag = " (wearing)" if k in worn else ""
                out.append(app_commands.Choice(name=f"{g['name']} — {SLOTS[g['slot']][0]}{tag}"[:100], value=k))
        return sorted(out, key=lambda c: c.name)[:25]

    @app_commands.command(name="remove", description="Take off whatever you're wearing in a slot.")
    @app_commands.choices(slot=[app_commands.Choice(name=f"{e} {n}", value=k) for k, (n, e) in SLOTS.items()])
    async def remove(self, interaction: discord.Interaction, slot: str):
        r = self.rec(interaction.user.id)
        old = r["worn"].pop(slot, None)
        self.save()
        if old in GEAR:
            await interaction.response.send_message(f"You take off the **{GEAR[old]['name']}**.", ephemeral=True)
        else:
            await interaction.response.send_message(f"You aren't wearing a {SLOTS[slot][0].lower()}.", ephemeral=True)

    # ================================================================ /title

    @app_commands.command(name="title", description="Choose which of your earned titles shows under your name.")
    @app_commands.describe(title="Pick one, 'Automatic' for your best, or 'None' to show no title")
    async def title(self, interaction: discord.Interaction, title: str = None):
        avail = self.titles_available(interaction.user)
        r = self.rec(interaction.user.id)
        if title is None:
            current = self.title_of(interaction.user)
            lines = [f"Showing: **{current or 'no title'}**"
                     + (" (automatic)" if not r.get("title") else ""),
                     "Yours to choose from: " + (", ".join(f"*{t}*" for t in avail) if avail
                                                 else "none yet — win duels or befriend beasts to earn some.")]
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return
        if title == "auto":
            r["title"] = None
            msg = f"Your title is automatic again: **{self.title_of(interaction.user) or 'none yet'}**."
        elif title == "none":
            r["title"] = "none"
            msg = "No title shown. Humble. Mysterious."
        elif title in avail:
            r["title"] = title
            msg = f"You're now known as **{interaction.user.display_name}, {title}**." if not title.startswith("the ") \
                else f"You're now known as **{interaction.user.display_name} {title}**."
        else:
            await interaction.response.send_message("You haven't earned that title (yet).", ephemeral=True)
            return
        self.save()
        await interaction.response.send_message(msg, ephemeral=True)

    @title.autocomplete("title")
    async def _titles(self, interaction: discord.Interaction, current: str):
        opts = [app_commands.Choice(name="Automatic (your best)", value="auto"),
                app_commands.Choice(name="None", value="none")]
        opts += [app_commands.Choice(name=t, value=t) for t in self.titles_available(interaction.user)]
        return [o for o in opts if current.lower() in o.name.lower()][:25]

    # ================================================================ perk commands

    async def _needs(self, interaction, perk: str, piece: str) -> bool:
        if self.has_perk(interaction.user.id, perk):
            return True
        g = GEAR[piece]
        how = ("Put it on with `/wear`." if self.owns(interaction.user.id, piece) else f"*{g['earn_text']}*")
        await interaction.response.send_message(f"That needs the **{g['name']}** worn. {how}", ephemeral=True)
        return False

    @app_commands.command(name="cheer", description="(House Cup Bracelet) Start a celebration for your house.")
    async def cheer(self, interaction: discord.Interaction):
        if not await self._needs(interaction, "cheer", "house_cup_bracelet"):
            return
        now = time.time()
        last = self._cheered.get(interaction.user.id, 0)
        if now - last < CHEER_COOLDOWN:
            await interaction.response.send_message(
                f"Your voice needs a rest. Try again in {int((CHEER_COOLDOWN - (now - last)) // 60) + 1} minutes.",
                ephemeral=True)
            return
        self._cheered[interaction.user.id] = now
        from cogs.store import HOUSES
        house = self._house(interaction.user.id, interaction.user)
        h = HOUSES.get(house, {"name": "their house", "emoji": "✨", "color": GOLD})
        await interaction.response.send_message(embed=discord.Embed(
            title=f"{h['emoji']} {h['name'].upper()}! {h['emoji']}",
            description=f"**{interaction.user.display_name}** {self.rng.choice(CHEER_LINES)}",
            color=h["color"]))

    @app_commands.command(name="whistle", description="(Beastcaller's Whistle) Call the next beast right now. Once a week.")
    async def whistle(self, interaction: discord.Interaction):
        if not await self._needs(interaction, "whistle", "beastcallers_whistle"):
            return
        beasts = self.bot.get_cog("Beasts")
        if not beasts:
            await interaction.response.send_message("No beasts are listening right now.", ephemeral=True)
            return
        r = self.rec(interaction.user.id)
        now = time.time()
        if now - r.get("whistle_at", 0) < WHISTLE_COOLDOWN:
            await interaction.response.send_message(
                f"The whistle needs to rest. It'll work again <t:{int(r['whistle_at'] + WHISTLE_COOLDOWN)}:R>.",
                ephemeral=True)
            return
        s = beasts.state.get("sighting")
        if s and now < s["expires"]:
            await interaction.response.send_message(
                f"A beast is already out in <#{s['channel_id']}>. Save your breath.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        channel = beasts.bot.get_channel(beasts.channel_id())
        if channel is not None:
            try:
                await channel.send(f"🎶 **{interaction.user.display_name}** blows the Beastcaller's Whistle. "
                                   "Nobody hears a thing. Something in the grounds does…")
            except discord.DiscordException:
                pass
        key = await beasts.spawn()
        if not key:
            await interaction.followup.send("Nothing answered. The whistle's still yours to use.", ephemeral=True)
            return
        r["whistle_at"] = now
        self.save()
        await interaction.followup.send(f"Something answered. Go and look in <#{beasts.channel_id()}> — "
                                        "quickly, anyone can befriend it.", ephemeral=True)

    @app_commands.command(name="secrets", description="(Keeper's Talisman) How many secrets you haven't found yet.")
    async def secrets(self, interaction: discord.Interaction):
        if not await self._needs(interaction, "secret_count", "keepers_talisman"):
            return
        world_cog = self.bot.get_cog("World")
        if not world_cog:
            await interaction.response.send_message("The talisman is quiet.", ephemeral=True)
            return
        found = self.secrets_found(interaction.user.id)
        house = self._house(interaction.user.id, interaction.user)
        lines = []
        for place, P in world_cog.world.places.items():
            need = self.findable_secrets(world_cog.world, place, house)
            left = len(need - found)
            lines.append(f"{PLACE_OF.get(place, place)} — " + (f"**{left}** still hidden" if left else "✅ all found"))
        await interaction.response.send_message(
            "🗝️ The Keeper's Talisman turns in your hand…\n\n" + "\n".join(lines)
            + "\n\n-# Counts the secrets you can find for good. Events and one-time surprises aren't included.",
            ephemeral=True)

    @app_commands.command(name="nightwatch", description="(Nightwatch Pendant) Turn the night-beast heads-up on or off.")
    @app_commands.choices(setting=[app_commands.Choice(name="On", value="on"), app_commands.Choice(name="Off", value="off")])
    async def nightwatch(self, interaction: discord.Interaction, setting: str):
        r = self.rec(interaction.user.id)
        r["nightwatch"] = setting == "on"
        self.save()
        extra = "" if self.has_perk(interaction.user.id, "nightwatch") else \
            " (You'll need the **Nightwatch Pendant** worn for it to do anything.)"
        await interaction.response.send_message(f"Night-beast heads-up: **{setting}**.{extra}", ephemeral=True)

    # ================================================================ staff

    async def _staff(self, interaction) -> bool:
        store = self.bot.get_cog("Store")
        if store and store.is_staff(interaction.user):
            return True
        await interaction.response.send_message("That's for staff.", ephemeral=True)
        return False

    @admin.command(name="give", description="(staff) Give someone a piece of gear.")
    async def admin_give(self, interaction: discord.Interaction, member: discord.Member, piece: str):
        if not await self._staff(interaction):
            return
        if piece not in GEAR:
            await interaction.response.send_message("No such piece.", ephemeral=True)
            return
        ok = self.award(member.id, piece)
        self.save()
        await interaction.response.send_message(
            f"Gave {member.display_name} the **{GEAR[piece]['name']}**." if ok else "They already have it.",
            ephemeral=True)

    @admin.command(name="take", description="(staff) Take a piece of gear away from someone.")
    async def admin_take(self, interaction: discord.Interaction, member: discord.Member, piece: str):
        if not await self._staff(interaction):
            return
        r = self.rec(member.id)
        if r["owned"].pop(piece, None) is None:
            await interaction.response.send_message("They don't have that.", ephemeral=True)
            return
        for slot, k in list(r["worn"].items()):
            if k == piece:
                del r["worn"][slot]
        self.save()
        await interaction.response.send_message(
            f"Took the **{GEAR[piece]['name']}** from {member.display_name}. "
            "(If they still qualify for an earned piece, it'll come back on the next check.)", ephemeral=True)

    @admin_give.autocomplete("piece")
    @admin_take.autocomplete("piece")
    async def _all_pieces(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=f"{g['name']} ({SLOTS[g['slot']][0]})", value=k)
                for k, g in sorted(GEAR.items(), key=lambda kv: kv[1]["name"])
                if current.lower() in g["name"].lower()][:25]

    @admin.command(name="channel", description="(staff) Where earned gear gets announced.")
    async def admin_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await self._staff(interaction):
            return
        self.state["channel_id"] = channel.id
        self.save()
        await interaction.response.send_message(f"Earned gear will be announced in {channel.mention}.", ephemeral=True)

    @admin.command(name="status", description="(staff) How gear is spread around the server.")
    async def admin_status(self, interaction: discord.Interaction):
        if not await self._staff(interaction):
            return
        members = self.state["members"]
        owned = [k for r in members.values() for k in r.get("owned", {})]
        designed = sum(1 for r in members.values() if r.get("look"))
        ch = self.announce_channel()
        lines = [f"Announcements go to {ch.mention if ch else '*(no channel found)*'}.",
                 f"{designed} people have designed their wizard.",
                 f"{len(owned)} pieces owned in total ({sum(1 for k in owned if 'earn' in GEAR.get(k, {}))} earned)."]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


# ==================================================================== /wizard menus

PAGES = [
    ("Body & hair", ["body", "skin", "hair", "hair_color"]),
    ("Face", ["eyes", "eye_color", "brows", "expression"]),
    ("Extras", ["extras", "facial_hair"]),
]
FIELD_LABEL = {"body": "Build", "skin": "Skin tone", "hair": "Hair style", "hair_color": "Hair colour",
               "eyes": "Eye shape", "eye_color": "Eye colour", "brows": "Brows", "expression": "Expression",
               "extras": "Freckles & glasses", "facial_hair": "Facial hair"}


class LookSelect(discord.ui.Select):
    def __init__(self, wizard_view: "WizardView", field: str):
        self.wv = wizard_view
        self.field = field
        opts = []
        for key in art.LOOK_FIELDS[field]:
            opts.append(discord.SelectOption(label=art.option_label(field, key), value=key,
                                             default=(wizard_view.look.get(field) == key)))
        # a fixed id for the life of this /wizard window, so a click that lands
        # while the preview is redrawing still finds its menu
        super().__init__(placeholder=FIELD_LABEL[field], options=opts[:25], min_values=1, max_values=1,
                         custom_id=f"wiz:{wizard_view.nonce}:{field}")

    async def callback(self, interaction: discord.Interaction):
        self.wv.look[self.field] = self.values[0]
        await self.wv.refresh(interaction)


class WizardView(discord.ui.View):
    def __init__(self, cog: Adornments, user, look: dict, page: int = 0):
        super().__init__(timeout=900)
        self.cog = cog
        self.user = user
        self.look = dict(look)
        self.page = page
        self.nonce = f"{user.id}-{int(time.time() * 1000) % 10**9}"
        self.build()

    def header(self) -> str:
        name, _ = PAGES[self.page]
        return (f"🪞 **Design your wizard** — page {self.page + 1} of {len(PAGES)}: *{name}*\n"
                "Pick from the menus and the Mirror updates. Changes save as you go.")

    def build(self):
        self.clear_items()
        for field in PAGES[self.page][1]:
            self.add_item(LookSelect(self, field))
        prev_b = discord.ui.Button(label="◂ Back", style=discord.ButtonStyle.secondary, row=4,
                                   disabled=self.page == 0, custom_id=f"wiz:{self.nonce}:back")
        next_b = discord.ui.Button(label="Next ▸", style=discord.ButtonStyle.secondary, row=4,
                                   disabled=self.page == len(PAGES) - 1, custom_id=f"wiz:{self.nonce}:next")
        rand_b = discord.ui.Button(label="🎲 Surprise me", style=discord.ButtonStyle.primary, row=4,
                                   custom_id=f"wiz:{self.nonce}:random")
        prev_b.callback = self._prev
        next_b.callback = self._next
        rand_b.callback = self._random
        for b in (prev_b, next_b, rand_b):
            self.add_item(b)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user.id

    async def _prev(self, interaction):
        self.page = max(0, self.page - 1)
        self.build()
        await interaction.response.edit_message(content=self.header(), view=self)

    async def _next(self, interaction):
        self.page = min(len(PAGES) - 1, self.page + 1)
        self.build()
        await interaction.response.edit_message(content=self.header(), view=self)

    async def _random(self, interaction):
        self.look = {f: self.cog.rng.choice(sorted(opts)) for f, opts in art.LOOK_FIELDS.items()}
        await self.refresh(interaction)

    async def refresh(self, interaction: discord.Interaction):
        r = self.cog.rec(self.user.id)
        r["look"] = dict(self.look)
        self.cog.save()
        self.build()
        await interaction.response.defer()
        try:
            file = await self.cog.mirror_file(self.user, look_override=self.look)
            await interaction.edit_original_response(content=self.header(), attachments=[file], view=self)
        except Exception:
            log.exception("Wizard preview failed")


async def setup(bot: commands.Bot):
    await bot.add_cog(Adornments(bot))
