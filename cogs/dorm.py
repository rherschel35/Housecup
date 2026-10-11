"""
Your Own Room — personal dorm you decorate.

    /room [member]     - view a dorm
    /room find         - discover one decor item (once / day)
    /room place        - put an owned item into a slot
    /room clear        - clear a slot
    /room stash        - list decor you own

Daily finds fill your stash. Frog cards can sit on the shelf
(use card ids from /frog album). Purely cosmetic.
"""

from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.velmora_channels import channel_mentions, with_study_hall_and_duels_potions
from cogs.world_engine import today

log = logging.getLogger("velmora.dorm")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "dorm_state.json"

ROOM_CHANNEL_IDS = with_study_hall_and_duels_potions()
COZY = 0x6B4F3A

SLOTS = ("bed", "wall", "desk", "shelf", "window")
SLOT_LABEL = {
    "bed": "Bed",
    "wall": "Wall",
    "desk": "Desk",
    "shelf": "Shelf",
    "window": "Window",
}

# Decor catalog — found via /room find
DECOR = {
    "patchwork_quilt": {
        "name": "Patchwork Quilt",
        "slot": "bed",
        "blurb": "Every square is a different excuse for staying up late.",
        "emoji": "🛏️",
    },
    "star_mobile": {
        "name": "Star Mobile",
        "slot": "bed",
        "blurb": "Turns slowly. Judges your sleep schedule.",
        "emoji": "✨",
    },
    "house_banner": {
        "name": "House Banner",
        "slot": "wall",
        "blurb": "Crooked on purpose. Or by talent.",
        "emoji": "🏳️",
    },
    "moving_sketch": {
        "name": "Moving Sketch",
        "slot": "wall",
        "blurb": "The subject keeps changing hats.",
        "emoji": "🖼️",
    },
    "wanted_poster_joke": {
        "name": "Joke Wanted Poster",
        "slot": "wall",
        "blurb": "Reward: one biscuit. Last seen: everywhere.",
        "emoji": "📜",
    },
    "ink_stained_desk": {
        "name": "Ink-Stained Desk Set",
        "slot": "desk",
        "blurb": "The quill has opinions about your essays.",
        "emoji": "🖋️",
    },
    "tea_tray": {
        "name": "Perpetual Tea Tray",
        "slot": "desk",
        "blurb": "Still warm. Always. Suspicious.",
        "emoji": "🍵",
    },
    "crystal_paperweight": {
        "name": "Crystal Paperweight",
        "slot": "desk",
        "blurb": "Contains a tiny storm. Office-safe.",
        "emoji": "💎",
    },
    "shelf_of_oddities": {
        "name": "Shelf of Oddities",
        "slot": "shelf",
        "blurb": "None of these objects agree on a theme.",
        "emoji": "📦",
    },
    "plant_that_bites": {
        "name": "Plant That Bites",
        "slot": "shelf",
        "blurb": "Friendly. Mostly. Water from a distance.",
        "emoji": "🪴",
    },
    "window_charm_rain": {
        "name": "Rain Charm Pane",
        "slot": "window",
        "blurb": "Soft rain against the glass even in drought.",
        "emoji": "🌧️",
    },
    "window_charm_aurora": {
        "name": "Aurora Pane",
        "slot": "window",
        "blurb": "Northern lights on demand. Show-off setting included.",
        "emoji": "🌌",
    },
    "moon_catcher": {
        "name": "Moon Catcher",
        "slot": "window",
        "blurb": "Catches moonlight. Releases gossip.",
        "emoji": "🌙",
    },
    "cozy_rug": {
        "name": "Cozy Rug",
        "slot": "bed",
        "blurb": "Technically for the floor. Spiritually for the bed.",
        "emoji": "🧶",
    },
    "candle_cluster": {
        "name": "Candle Cluster",
        "slot": "desk",
        "blurb": "Never drip on homework. Will drip on pride.",
        "emoji": "🕯️",
    },
}


def _channel_hint() -> str:
    return f"Dorm rooms are in {channel_mentions(ROOM_CHANNEL_IDS)}."


class Dorm(commands.Cog):
    room = app_commands.Group(
        name="room",
        description="Your dorm — find decor, place it, show it off.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.rng = random.Random()

    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"players": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Dorm state unreadable — starting empty.")
            return {"players": {}}
        data.setdefault("players", {})
        return data

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save dorm state.")

    async def _require_channel(self, interaction: discord.Interaction) -> bool:
        if interaction.channel_id in ROOM_CHANNEL_IDS:
            return True
        await interaction.response.send_message(_channel_hint(), ephemeral=True)
        return False

    def record(self, user_id: int) -> dict:
        rec = self.state.setdefault("players", {}).setdefault(str(user_id), {})
        rec.setdefault("stash", {})  # decor_id -> count
        rec.setdefault("slots", {})  # slot -> {"kind":"decor"|"frog", "id":...}
        rec.setdefault("found_on", "")
        return rec

    def _frog_meta(self, card_id: str) -> dict | None:
        frogs = self.bot.get_cog("Frogs")
        if not frogs:
            return None
        from cogs.frogs import CARDS
        return CARDS.get(card_id)

    def embed_room(self, member: discord.abc.User, rec: dict) -> discord.Embed:
        lines = []
        for slot in SLOTS:
            placed = (rec.get("slots") or {}).get(slot)
            label = SLOT_LABEL[slot]
            if not placed:
                lines.append(f"**{label}:** _empty_")
                continue
            kind = placed.get("kind")
            pid = placed.get("id")
            if kind == "decor" and pid in DECOR:
                d = DECOR[pid]
                lines.append(f"**{label}:** {d['emoji']} {d['name']} — *{d['blurb']}*")
            elif kind == "frog":
                meta = self._frog_meta(pid)
                if meta:
                    lines.append(
                        f"**{label}:** {meta['emoji']} Frog card — **{meta['name']}**"
                    )
                else:
                    lines.append(f"**{label}:** 🃏 Frog card (`{pid}`)")
            else:
                lines.append(f"**{label}:** _something odd_")
        stash_n = sum(int(v) for v in (rec.get("stash") or {}).values())
        embed = discord.Embed(
            title=f"🏠 {member.display_name}'s Room",
            description="\n".join(lines),
            color=COZY,
        )
        embed.set_footer(
            text=f"Stash: {stash_n} decor · /room find · /room place · shelf holds frog cards too"
        )
        return embed

    @room.command(name="view", description="Look at a dorm room.")
    @app_commands.describe(member="Whose room (default: yours)")
    async def room_view(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ):
        if not await self._require_channel(interaction):
            return
        target = member or interaction.user
        rec = self.record(target.id)
        await interaction.response.send_message(
            embed=self.embed_room(target, rec),
            ephemeral=True,
        )

    # Also allow bare /room as alias via same group default? Discord needs subcommand.
    # Add a top-level convenience: users asked for /room — add both group view and
    # a top-level command named room_show? Keep group only: /room view

    @room.command(name="find", description="Find one decor item for your stash (once per day).")
    async def room_find(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        rec = self.record(interaction.user.id)
        day = today()
        if rec.get("found_on") == day:
            await interaction.response.send_message(
                "You've already found something for your room today. "
                "Try `/room place` or `/room view`.",
                ephemeral=True,
            )
            return
        item_id = self.rng.choice(list(DECOR))
        stash = rec.setdefault("stash", {})
        stash[item_id] = int(stash.get(item_id, 0)) + 1
        rec["found_on"] = day
        self.save()
        d = DECOR[item_id]
        embed = discord.Embed(
            title=f"Found: {d['emoji']} {d['name']}",
            description=(
                f"*{d['blurb']}*\n\n"
                f"Best on the **{SLOT_LABEL[d['slot']]}**. "
                f"Place it with `/room place`."
            ),
            color=COZY,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @room.command(name="stash", description="Decor waiting in your stash.")
    async def room_stash(self, interaction: discord.Interaction):
        if not await self._require_channel(interaction):
            return
        rec = self.record(interaction.user.id)
        stash = {
            k: int(v)
            for k, v in (rec.get("stash") or {}).items()
            if int(v) > 0 and k in DECOR
        }
        if not stash:
            await interaction.response.send_message(
                "Stash is empty. `/room find` once a day.", ephemeral=True
            )
            return
        lines = [
            f"{DECOR[i]['emoji']} **{DECOR[i]['name']}** ×{n} "
            f"({SLOT_LABEL[DECOR[i]['slot']]})"
            for i, n in sorted(stash.items(), key=lambda kv: DECOR[kv[0]]["name"])
        ]
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Room stash",
                description="\n".join(lines),
                color=COZY,
            ),
            ephemeral=True,
        )

    @room.command(name="place", description="Place decor (or a frog card on the shelf).")
    @app_commands.describe(
        item="Decor from your stash, or a frog card id/name for the shelf",
        slot="Where to put it (frog cards → shelf)",
    )
    @app_commands.choices(
        slot=[app_commands.Choice(name=SLOT_LABEL[s], value=s) for s in SLOTS]
    )
    async def room_place(
        self,
        interaction: discord.Interaction,
        item: str,
        slot: app_commands.Choice[str],
    ):
        if not await self._require_channel(interaction):
            return
        rec = self.record(interaction.user.id)
        slot_key = slot.value
        key = item.strip().lower().replace(" ", "_")

        # Decor path
        if key in DECOR or any(
            DECOR[d]["name"].lower() == item.strip().lower() for d in DECOR
        ):
            if key not in DECOR:
                key = next(
                    d
                    for d in DECOR
                    if DECOR[d]["name"].lower() == item.strip().lower()
                )
            d = DECOR[key]
            if d["slot"] != slot_key:
                await interaction.response.send_message(
                    f"**{d['name']}** belongs on the **{SLOT_LABEL[d['slot']]}**, "
                    f"not the {SLOT_LABEL[slot_key]}.",
                    ephemeral=True,
                )
                return
            stash = rec.setdefault("stash", {})
            if int(stash.get(key, 0)) < 1:
                await interaction.response.send_message(
                    "You don't have that in your stash. `/room find` or check `/room stash`.",
                    ephemeral=True,
                )
                return
            # return previous decor to stash if any
            prev = (rec.get("slots") or {}).get(slot_key)
            if prev and prev.get("kind") == "decor" and prev.get("id") in DECOR:
                pid = prev["id"]
                stash[pid] = int(stash.get(pid, 0)) + 1
            stash[key] = int(stash[key]) - 1
            if stash[key] <= 0:
                stash.pop(key, None)
            rec.setdefault("slots", {})[slot_key] = {"kind": "decor", "id": key}
            self.save()
            await interaction.response.send_message(
                embed=self.embed_room(interaction.user, rec),
                ephemeral=True,
            )
            return

        # Frog card path — shelf only
        from cogs.frogs import CARDS

        frog_id = key if key in CARDS else None
        if not frog_id:
            for cid, meta in CARDS.items():
                if meta["name"].lower() == item.strip().lower():
                    frog_id = cid
                    break
        if not frog_id:
            await interaction.response.send_message(
                "Unknown item. Use a decor name from `/room stash`, "
                "or a frog card from `/frog album` for the shelf.",
                ephemeral=True,
            )
            return
        if slot_key != "shelf":
            await interaction.response.send_message(
                "Frog cards only display on the **Shelf**.", ephemeral=True
            )
            return
        frogs = self.bot.get_cog("Frogs")
        if not frogs or frogs.counts(interaction.user.id).get(frog_id, 0) < 1:
            await interaction.response.send_message(
                "You don't have that frog card.", ephemeral=True
            )
            return
        # previous shelf decor returns to stash; previous frog just swaps (cards stay in album)
        prev = (rec.get("slots") or {}).get("shelf")
        if prev and prev.get("kind") == "decor" and prev.get("id") in DECOR:
            pid = prev["id"]
            stash = rec.setdefault("stash", {})
            stash[pid] = int(stash.get(pid, 0)) + 1
        rec.setdefault("slots", {})["shelf"] = {"kind": "frog", "id": frog_id}
        self.save()
        await interaction.response.send_message(
            embed=self.embed_room(interaction.user, rec),
            ephemeral=True,
        )

    @room_place.autocomplete("item")
    async def place_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        rec = self.record(interaction.user.id)
        needle = (current or "").lower()
        out: list[app_commands.Choice[str]] = []
        for did, n in (rec.get("stash") or {}).items():
            if int(n) < 1 or did not in DECOR:
                continue
            name = DECOR[did]["name"]
            if needle and needle not in name.lower():
                continue
            out.append(app_commands.Choice(name=f"{name} (decor)", value=did))
        frogs = self.bot.get_cog("Frogs")
        if frogs:
            from cogs.frogs import CARDS
            for cid, n in frogs.counts(interaction.user.id).items():
                if n < 1:
                    continue
                name = CARDS[cid]["name"]
                if needle and needle not in name.lower():
                    continue
                out.append(app_commands.Choice(name=f"{name} (frog → shelf)", value=cid))
        return out[:25]

    @room.command(name="clear", description="Clear a furniture slot (decor returns to stash).")
    @app_commands.choices(
        slot=[app_commands.Choice(name=SLOT_LABEL[s], value=s) for s in SLOTS]
    )
    async def room_clear(
        self, interaction: discord.Interaction, slot: app_commands.Choice[str]
    ):
        if not await self._require_channel(interaction):
            return
        rec = self.record(interaction.user.id)
        slot_key = slot.value
        prev = (rec.get("slots") or {}).pop(slot_key, None)
        if not prev:
            await interaction.response.send_message(
                f"The {SLOT_LABEL[slot_key]} is already empty.", ephemeral=True
            )
            return
        if prev.get("kind") == "decor" and prev.get("id") in DECOR:
            stash = rec.setdefault("stash", {})
            pid = prev["id"]
            stash[pid] = int(stash.get(pid, 0)) + 1
        self.save()
        await interaction.response.send_message(
            embed=self.embed_room(interaction.user, rec),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Dorm(bot))
