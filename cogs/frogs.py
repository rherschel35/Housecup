"""
Chocolate Frog Cards — daily collectibles.

    /frog open                 - unwrap today's frog (once / day)
    /frog album [member]       - your collection
    /frog show <card>          - show off a card (decorative)
    /frog give @member card    - gift a duplicate
    /frog trade @member …      - propose a 1:1 card swap

Staff:
    /staffgame frog prize      - award any card as a prize/reward

50 cards (spells, crests, beasts — no people yet). Mythic Lumen Aegis is extremely rare.
Every 10 unique cards → +10 house pts. First mythic → +25.
"""

from __future__ import annotations

import io
import json
import logging
import os
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cogs.frog_card_art import render_card_png as _compose_card_png
from cogs.world_engine import today

log = logging.getLogger("velmora.frogs")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
STATE_PATH = STATE_DIR / "frogs_state.json"

# Ultra-rare: ~1 in N opens (default 250). Env override for testing.
MYTHIC_ODDS = max(50, int(os.getenv("FROG_MYTHIC_ODDS", "250")))
MYTHIC_ID = "lumen_aegis"
DECADE_PTS = 10
MYTHIC_PTS = 25

RARITY_ORDER = ("common", "uncommon", "rare", "legendary", "mythic")
RARITY_WEIGHT = {
    "common": 55,
    "uncommon": 28,
    "rare": 12,
    "legendary": 5,
    # mythic is rolled separately via MYTHIC_ODDS — not in normal pool
}
RARITY_COLOR = {
    "common": 0x8B7355,
    "uncommon": 0x3D8B6E,
    "rare": 0x4A6FA5,
    "legendary": 0xC9A227,
    "mythic": 0xE8D48B,
}
RARITY_LABEL = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "legendary": "Legendary",
    "mythic": "✦ Mythic ✦",
}
# id -> card meta. Art files live in frog_art_assets/ (see frog_card_art.ART_FILES).
CARDS: dict[str, dict] = {
    "lumen_aegis": {
        "name": "Lumen Aegis",
        "blurb": "Mythic ward-light. Almost never found in a box.",
        "rarity": "mythic",
        "kind": "spell",
        "emoji": "✨",
    },
    "velmora_seal": {
        "name": "The Velmora Seal",
        "blurb": "Five houses. One crest. Wax that never cools.",
        "rarity": "legendary",
        "kind": "crest",
        "emoji": "🔏",
    },
    "chimera_saro": {
        "name": "Chimera Saro",
        "blurb": "Three heads. One argument. Zero patience.",
        "rarity": "legendary",
        "kind": "beast",
        "emoji": "🦁",
    },
    "kraken_lumi": {
        "name": "Kraken Lumi",
        "blurb": "Ink, depth, and a very long grudge.",
        "rarity": "legendary",
        "kind": "beast",
        "emoji": "🦑",
    },
    "griffin": {
        "name": "The Griffin",
        "blurb": "Wings optional. Pride mandatory.",
        "rarity": "legendary",
        "kind": "beast",
        "emoji": "🦅",
    },
    "sorting_net": {
        "name": "The Sorting Net",
        "blurb": "It does not shout. It quietly files you.",
        "rarity": "legendary",
        "kind": "spell",
        "emoji": "🧵",
    },
    "expecto": {
        "name": "Expecto Patronum",
        "blurb": "Silver light with teeth for the dark.",
        "rarity": "legendary",
        "kind": "spell",
        "emoji": "🦌",
    },
    "crest_oakmont": {
        "name": "Crest of Oakmont",
        "blurb": "Patient. Relentless. Purple.",
        "rarity": "rare",
        "kind": "crest",
        "emoji": "🐻",
    },
    "crest_asterwilde": {
        "name": "Crest of AsterWilde",
        "blurb": "Wild minds. Fierce hearts. Beautiful chaos.",
        "rarity": "rare",
        "kind": "crest",
        "emoji": "🐿️",
    },
    "crest_veyren": {
        "name": "Crest of Veyren",
        "blurb": "Some bonds need no words.",
        "rarity": "rare",
        "kind": "crest",
        "emoji": "🦌",
    },
    "crest_caldrin": {
        "name": "Crest of Caldrin",
        "blurb": "Measured, not meek.",
        "rarity": "rare",
        "kind": "crest",
        "emoji": "🐒",
    },
    "crest_thornmere": {
        "name": "Crest of Thornmere",
        "blurb": "Loyalty earned. Advantage taken.",
        "rarity": "rare",
        "kind": "crest",
        "emoji": "🐺",
    },
    "dementor": {
        "name": "Dementor",
        "blurb": "Cold that remembers your name.",
        "rarity": "rare",
        "kind": "beast",
        "emoji": "👻",
    },
    "shadow_wolf": {
        "name": "Shadow Wolf",
        "blurb": "Howls from the wrong side of the veil.",
        "rarity": "rare",
        "kind": "beast",
        "emoji": "🐺",
    },
    "incendio": {
        "name": "Incendio",
        "blurb": "Fire on command. Apologies optional.",
        "rarity": "rare",
        "kind": "spell",
        "emoji": "🔥",
    },
    "protego": {
        "name": "Protego",
        "blurb": "A wall of will. Hold it.",
        "rarity": "rare",
        "kind": "spell",
        "emoji": "🛡️",
    },
    "expelliarmus": {
        "name": "Expelliarmus",
        "blurb": "Your wand. My terms.",
        "rarity": "rare",
        "kind": "spell",
        "emoji": "🪄",
    },
    "stupefy": {
        "name": "Stupefy",
        "blurb": "Lights out. Soft landing not included.",
        "rarity": "uncommon",
        "kind": "spell",
        "emoji": "💥",
    },
    "bombarda": {
        "name": "Bombarda",
        "blurb": "Doors were a suggestion.",
        "rarity": "uncommon",
        "kind": "spell",
        "emoji": "💣",
    },
    "petrificus": {
        "name": "Petrificus Totalus",
        "blurb": "Full body bind. No stretching.",
        "rarity": "uncommon",
        "kind": "spell",
        "emoji": "🪨",
    },
    "obliviate": {
        "name": "Obliviate",
        "blurb": "You won't remember reading this.",
        "rarity": "uncommon",
        "kind": "spell",
        "emoji": "🌫️",
    },
    "stormcaller": {
        "name": "Stormcaller",
        "blurb": "Thunder with a destination.",
        "rarity": "uncommon",
        "kind": "beast",
        "emoji": "⛈️",
    },
    "ember_hound": {
        "name": "Ember Hound",
        "blurb": "Warm loyalty. Hot breath.",
        "rarity": "uncommon",
        "kind": "beast",
        "emoji": "🐶",
    },
    "volt_serpent": {
        "name": "Volt Serpent",
        "blurb": "Don't pet the lightning.",
        "rarity": "uncommon",
        "kind": "beast",
        "emoji": "🐍",
    },
    "niffler": {
        "name": "Niffler",
        "blurb": "Shiny things were never safe again.",
        "rarity": "uncommon",
        "kind": "beast",
        "emoji": "🦡",
    },
    "lanternback_stag": {
        "name": "Lanternback Stag",
        "blurb": "Lights the path. Judges the traveler.",
        "rarity": "uncommon",
        "kind": "beast",
        "emoji": "🦌",
    },
    "moonglass_owl": {
        "name": "Moonglass Owl",
        "blurb": "Sees through excuses.",
        "rarity": "uncommon",
        "kind": "beast",
        "emoji": "🦉",
    },
    "arresto": {
        "name": "Arresto Momentum",
        "blurb": "Fall later. Think now.",
        "rarity": "uncommon",
        "kind": "spell",
        "emoji": "⏳",
    },
    "glacius": {
        "name": "Glacius",
        "blurb": "Ice with opinions.",
        "rarity": "uncommon",
        "kind": "spell",
        "emoji": "❄️",
    },
    "lumos": {
        "name": "Lumos",
        "blurb": "A little light goes a long hallway.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "💡",
    },
    "accio": {
        "name": "Accio",
        "blurb": "Come here. Yes, you — the biscuit.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "📦",
    },
    "reparo": {
        "name": "Reparo",
        "blurb": "Almost as good as new. Almost.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "🔧",
    },
    "wingardium": {
        "name": "Wingardium Leviosa",
        "blurb": "It's levi-O-sa. The feather knows.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "🪶",
    },
    "alohomora": {
        "name": "Alohomora",
        "blurb": "Locks are social constructs.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "🔓",
    },
    "colloportus": {
        "name": "Colloportus",
        "blurb": "Stay closed. We're talking.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "🔒",
    },
    "finite": {
        "name": "Finite Incantatem",
        "blurb": "Spell's over. Sit down.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "⛔",
    },
    "riddikulus": {
        "name": "Riddikulus",
        "blurb": "Fear, but make it comedy.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "🎭",
    },
    "impedimenta": {
        "name": "Impedimenta",
        "blurb": "Slow down. That's an order.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "🌀",
    },
    "silencio": {
        "name": "Silencio",
        "blurb": "Quiet. Finally.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "🔇",
    },
    "episkey": {
        "name": "Episkey",
        "blurb": "Small hurts. Quick fix.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "💚",
    },
    "descendo": {
        "name": "Descendo",
        "blurb": "Gravity, but personal.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "⬇️",
    },
    "ventus": {
        "name": "Ventus",
        "blurb": "Wind with an agenda.",
        "rarity": "common",
        "kind": "spell",
        "emoji": "💨",
    },
    "hopsalot": {
        "name": "Sir Hopsalot",
        "blurb": "A prefect's toad. A legend. A menace.",
        "rarity": "common",
        "kind": "beast",
        "emoji": "🐸",
    },
    "wild_threat": {
        "name": "Wild Threat",
        "blurb": "It wandered in. Someone cast.",
        "rarity": "common",
        "kind": "beast",
        "emoji": "👻",
    },
    "field_cow": {
        "name": "Field Cow",
        "blurb": "Pastoral. Magical. Unbothered.",
        "rarity": "common",
        "kind": "beast",
        "emoji": "🐄",
    },
    "cistern_toad": {
        "name": "Cistern Toad",
        "blurb": "Lives in the pipes. Knows things.",
        "rarity": "common",
        "kind": "beast",
        "emoji": "🐸",
    },
    "button_magpie": {
        "name": "Button Magpie",
        "blurb": "Collects buttons. Judges fashion.",
        "rarity": "common",
        "kind": "beast",
        "emoji": "🐦",
    },
    "blackcap_squirrel": {
        "name": "Blackcap Squirrel",
        "blurb": "AsterWilde energy in rodent form.",
        "rarity": "common",
        "kind": "beast",
        "emoji": "🐿️",
    },
    "duskpup": {
        "name": "Duskpup",
        "blurb": "Sleeps through Charms. Dreams of snacks.",
        "rarity": "common",
        "kind": "beast",
        "emoji": "🐕",
    },
    "orbitcat": {
        "name": "Orbitcat",
        "blurb": "Gravity is a suggestion.",
        "rarity": "common",
        "kind": "beast",
        "emoji": "🐱",
    },
}

assert len(CARDS) == 50, f"expected 50 frog cards, got {len(CARDS)}"
assert MYTHIC_ID in CARDS and CARDS[MYTHIC_ID]["rarity"] == "mythic"


def _resolve_card(query: str) -> str | None:
    key = query.strip().lower().replace(" ", "_")
    if key in CARDS:
        return key
    needle = query.strip().lower()
    for cid, meta in CARDS.items():
        if meta["name"].lower() == needle:
            return cid
    for cid, meta in CARDS.items():
        if needle in meta["name"].lower() or needle in cid:
            return cid
    return None


# Stable album order for #NN/50 footers (mythic first, then by rarity).
def _index_key(cid: str) -> tuple:
    r = CARDS[cid]["rarity"]
    tier = 0 if r == "mythic" else 1 + RARITY_ORDER.index(r)
    return (tier, CARDS[cid]["name"])


CARD_INDEX = {cid: i + 1 for i, cid in enumerate(sorted(CARDS, key=_index_key))}


def render_card_png(card_id: str) -> bytes:
    """Decorative Velmora Chocolate Frog card PNG."""
    return _compose_card_png(
        card_id,
        CARDS[card_id],
        index=CARD_INDEX.get(card_id, 0),
        total=len(CARDS),
    )


def _card_file(card_id: str) -> discord.File:
    png = render_card_png(card_id)
    return discord.File(io.BytesIO(png), filename=f"frog_{card_id}.png")


class TradeView(discord.ui.View):
    def __init__(self, cog: "Frogs", trade_id: str):
        super().__init__(timeout=600)
        self.cog = cog
        self.trade_id = trade_id

    async def _finish(self, interaction: discord.Interaction, accepted: bool):
        trade = self.cog.state.get("trades", {}).get(self.trade_id)
        if not trade or trade.get("status") != "open":
            await interaction.response.send_message(
                "That trade is no longer open.", ephemeral=True
            )
            return
        if interaction.user.id != int(trade["to_id"]):
            await interaction.response.send_message(
                "Only the other collector can accept or decline.", ephemeral=True
            )
            return
        if not accepted:
            trade["status"] = "declined"
            self.cog.save()
            self.stop()
            await interaction.response.edit_message(
                content=(
                    f"Trade declined — "
                    f"{CARDS[trade['offer']]['emoji']} **{CARDS[trade['offer']]['name']}** "
                    f"⇄ {CARDS[trade['want']]['emoji']} **{CARDS[trade['want']]['name']}**"
                ),
                embed=None,
                view=None,
                attachments=[],
            )
            return

        from_id = int(trade["from_id"])
        to_id = int(trade["to_id"])
        offer = trade["offer"]
        want = trade["want"]
        if self.cog.counts(from_id).get(offer, 0) < 1:
            trade["status"] = "failed"
            self.cog.save()
            await interaction.response.send_message(
                "Trade failed — they no longer have the offered card.", ephemeral=True
            )
            return
        if self.cog.counts(to_id).get(want, 0) < 1:
            trade["status"] = "failed"
            self.cog.save()
            await interaction.response.send_message(
                "Trade failed — you no longer have the card they want.", ephemeral=True
            )
            return

        self.cog._transfer(from_id, to_id, offer)
        self.cog._transfer(to_id, from_id, want)
        notes_a = self.cog._apply_rewards(from_id)
        notes_b = self.cog._apply_rewards(to_id)
        dorm = self.cog.bot.get_cog("Dorm")
        if dorm:
            notes_a.extend(dorm.pop_unlock_notes(from_id))
            notes_b.extend(dorm.pop_unlock_notes(to_id))
        trade["status"] = "done"
        self.cog.save()
        self.stop()
        extras = ""
        if notes_a or notes_b:
            extras = "\n" + "\n".join(notes_a + notes_b)
        await interaction.response.edit_message(
            content=(
                f"✅ Trade complete!\n"
                f"<@{from_id}> → {CARDS[offer]['emoji']} **{CARDS[offer]['name']}**\n"
                f"<@{to_id}> → {CARDS[want]['emoji']} **{CARDS[want]['name']}**"
                f"{extras}"
            ),
            embed=None,
            view=None,
            attachments=[],
        )

    @discord.ui.button(label="Accept trade", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, True)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, False)


class Frogs(commands.Cog):
    frog = app_commands.Group(
        name="frog",
        description="Chocolate Frog Cards — daily collectibles.",
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
            return {"players": {}, "trades": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Frogs state unreadable — starting empty.")
            return {"players": {}, "trades": {}}
        data.setdefault("players", {})
        data.setdefault("trades", {})
        return data

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            # prune old trades
            trades = self.state.get("trades", {})
            if len(trades) > 80:
                closed = [
                    tid
                    for tid, t in trades.items()
                    if t.get("status") != "open"
                    or time.time() - float(t.get("at", 0)) > 86400
                ]
                for tid in closed[:40]:
                    trades.pop(tid, None)
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save frogs state.")

    def record(self, user_id: int) -> dict:
        rec = self.state.setdefault("players", {}).setdefault(str(user_id), {})
        rec.setdefault("cards", {})
        rec.setdefault("opened_on", "")
        rec.setdefault("decade_paid", [])  # [10, 20, ...]
        rec.setdefault("mythic_paid", False)
        return rec

    def counts(self, user_id: int) -> dict[str, int]:
        return {
            k: int(v)
            for k, v in (self.record(user_id).get("cards") or {}).items()
            if int(v) > 0 and k in CARDS
        }

    def owned_total(self, user_id: int) -> tuple[int, int]:
        counts = self.counts(user_id)
        return len(counts), sum(counts.values())

    def roll(self) -> str:
        # Extremely rare mythic gate, then normal rarity pool (no mythic).
        if self.rng.randrange(MYTHIC_ODDS) == 0:
            return MYTHIC_ID
        pool, weights = [], []
        for cid, meta in CARDS.items():
            if meta["rarity"] == "mythic":
                continue
            pool.append(cid)
            weights.append(RARITY_WEIGHT.get(meta["rarity"], 10))
        return self.rng.choices(pool, weights=weights, k=1)[0]

    def add_card(self, user_id: int, card_id: str, *, save: bool = True) -> tuple[int, bool]:
        rec = self.record(user_id)
        cards = rec.setdefault("cards", {})
        before = int(cards.get(card_id, 0))
        cards[card_id] = before + 1
        # First time pulling a card unlocks its matching room decor
        if before == 0:
            dorm = self.bot.get_cog("Dorm")
            if dorm:
                dorm.unlock_from_frog(user_id, card_id, save=True)
        if save:
            self.save()
        return cards[card_id], before == 0

    def _transfer(self, from_id: int, to_id: int, card_id: str) -> None:
        fr = self.record(from_id)
        cards = fr.setdefault("cards", {})
        n = int(cards.get(card_id, 0))
        if n < 1:
            return
        cards[card_id] = n - 1
        if cards[card_id] <= 0:
            cards.pop(card_id, None)
        self.add_card(to_id, card_id, save=False)

    def _award_house_pts(self, user_id: int, pts: int, reason: str) -> str | None:
        store = self.bot.get_cog("Store")
        if not store or not store.season_active():
            return None
        member = None
        for g in self.bot.guilds:
            m = g.get_member(user_id)
            if m:
                member = m
                break
        if member is None:
            return None
        house = store.member_house(member)
        if not house:
            return None
        store.record(
            house=house,
            delta=pts,
            actor_id=self.bot.user.id if self.bot.user else 0,
            target_id=user_id,
            reason=reason,
        )
        return f"+{pts} to their house — {reason}"

    def _apply_rewards(self, user_id: int) -> list[str]:
        """Pay decade + mythic bonuses if newly earned. Returns public notes."""
        rec = self.record(user_id)
        notes: list[str] = []
        unique, _ = self.owned_total(user_id)
        paid = {int(x) for x in rec.get("decade_paid") or []}
        for threshold in (10, 20, 30, 40, 50):
            if unique >= threshold and threshold not in paid:
                msg = self._award_house_pts(
                    user_id,
                    DECADE_PTS,
                    f"Chocolate Frog album — {threshold} unique",
                )
                paid.add(threshold)
                if msg:
                    notes.append(f"🏅 <@{user_id}> {msg}")
                else:
                    notes.append(
                        f"🏅 <@{user_id}> hit **{threshold}** unique frogs "
                        f"(+{DECADE_PTS} when a Cup season is live)."
                    )
        rec["decade_paid"] = sorted(paid)

        if MYTHIC_ID in self.counts(user_id) and not rec.get("mythic_paid"):
            msg = self._award_house_pts(
                user_id,
                MYTHIC_PTS,
                "Chocolate Frog — Lumen Aegis (mythic)",
            )
            rec["mythic_paid"] = True
            if msg:
                notes.append(f"✦ <@{user_id}> {msg}")
            else:
                notes.append(
                    f"✦ <@{user_id}> collected **Lumen Aegis** "
                    f"(+{MYTHIC_PTS} when a Cup season is live)."
                )
        self.save()
        return notes

    def embed_card(
        self,
        card_id: str,
        *,
        title_prefix: str = "🍫 Chocolate Frog",
        flag: str = "",
        footer: str = "",
        album_line: str = "",
    ) -> discord.Embed:
        meta = CARDS[card_id]
        rarity = meta["rarity"]
        color = RARITY_COLOR.get(rarity, 0xD4A84A)
        desc = f"*{meta['blurb']}*\n\n**{RARITY_LABEL.get(rarity, rarity.title())}**"
        if flag:
            desc += f" · {flag}"
        if album_line:
            desc += f"\n{album_line}"
        embed = discord.Embed(
            title=f"{title_prefix} — {meta['emoji']} {meta['name']}",
            description=desc,
            color=color,
        )
        embed.set_image(url=f"attachment://frog_{card_id}.png")
        if footer:
            embed.set_footer(text=footer)
        return embed

    @frog.command(name="open", description="Unwrap today's Chocolate Frog (once per day).")
    async def frog_open(self, interaction: discord.Interaction):
        rec = self.record(interaction.user.id)
        day = today()
        if rec.get("opened_on") == day:
            await interaction.response.send_message(
                "You've already opened today's frog. Come back tomorrow — "
                "or show off with `/frog show` / `/frog album`.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        card_id = self.roll()
        count, is_new = self.add_card(interaction.user.id, card_id, save=False)
        rec["opened_on"] = day
        self.save()
        reward_notes = self._apply_rewards(interaction.user.id)
        dorm = self.bot.get_cog("Dorm")
        if dorm:
            reward_notes.extend(dorm.pop_unlock_notes(interaction.user.id))
        unique, copies = self.owned_total(interaction.user.id)
        flag = "**NEW!**" if is_new else f"Duplicate ×{count}"
        embed = self.embed_card(
            card_id,
            flag=flag,
            album_line=f"Album: **{unique}/{len(CARDS)}** unique · **{copies}** cards",
            footer="1 frog / day · unlocks room decor · /frog trade · /room",
        )
        file = _card_file(card_id)
        content = "\n".join(reward_notes) if reward_notes else None
        await interaction.followup.send(content=content, embed=embed, file=file)

    @frog.command(name="album", description="Your Chocolate Frog album (or someone else's).")
    @app_commands.describe(member="Whose album to view")
    async def frog_album(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ):
        target = member or interaction.user
        counts = self.counts(target.id)
        unique, copies = self.owned_total(target.id)
        if not counts:
            await interaction.response.send_message(
                f"{target.display_name} hasn't opened a frog yet. `/frog open` once a day.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title=f"🃏 {target.display_name}'s Frog Album",
            description=(
                f"**{unique}/{len(CARDS)}** unique · **{copies}** cards\n"
                f"Spells · Crests · Beasts — people cards later\n"
                f"Milestones: +{DECADE_PTS} pts every 10 unique · "
                f"mythic **Lumen Aegis** +{MYTHIC_PTS}"
            ),
            color=RARITY_COLOR["legendary"],
        )
        kind_order = ("crest", "spell", "beast")
        kind_label = {"crest": "Crests", "spell": "Spells", "beast": "Beasts"}
        for kind in kind_order:
            chunk = [
                f"{CARDS[cid]['emoji']} **{CARDS[cid]['name']}** "
                f"· {RARITY_LABEL.get(CARDS[cid]['rarity'], CARDS[cid]['rarity'])} ×{counts[cid]}"
                for cid in sorted(counts, key=lambda c: CARDS[c]["name"])
                if CARDS[cid].get("kind") == kind
            ]
            if chunk:
                embed.add_field(
                    name=kind_label[kind],
                    value="\n".join(chunk),
                    inline=False,
                )
        embed.set_footer(text=f"{len(CARDS) - unique} still missing · /frog show <card>")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @frog.command(name="show", description="Show off a Chocolate Frog card from your album.")
    @app_commands.describe(card="Card name or id from your album")
    async def frog_show(self, interaction: discord.Interaction, card: str):
        key = _resolve_card(card)
        if not key:
            await interaction.response.send_message(
                "Unknown card. Check `/frog album`.", ephemeral=True
            )
            return
        if self.counts(interaction.user.id).get(key, 0) < 1:
            await interaction.response.send_message(
                "You don't have that card yet.", ephemeral=True
            )
            return
        await interaction.response.defer()
        embed = self.embed_card(
            key,
            title_prefix=f"🃏 {interaction.user.display_name} shows",
            footer="Collect · Trade · Display",
        )
        await interaction.followup.send(embed=embed, file=_card_file(key))

    @frog_show.autocomplete("card")
    async def show_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return self._owned_autocomplete(interaction.user.id, current, min_count=1)

    @frog.command(name="give", description="Gift a duplicate Chocolate Frog card to someone.")
    @app_commands.describe(member="Who receives the card", card="Card to gift (must have a spare)")
    async def frog_give(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        card: str,
    ):
        if member.id == interaction.user.id:
            await interaction.response.send_message(
                "Gifting yourself a frog is just holding it differently.",
                ephemeral=True,
            )
            return
        if member.bot:
            await interaction.response.send_message(
                "Bots don't collect frogs.", ephemeral=True
            )
            return
        key = _resolve_card(card)
        if not key:
            await interaction.response.send_message(
                "Unknown card. Check `/frog album` for names.", ephemeral=True
            )
            return
        mine = self.counts(interaction.user.id)
        if mine.get(key, 0) < 2:
            await interaction.response.send_message(
                f"You need a **duplicate** of {CARDS[key]['name']} to gift one "
                "(keep at least one for your album). To swap singles, use `/frog trade`.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        rec = self.record(interaction.user.id)
        rec["cards"][key] = int(rec["cards"][key]) - 1
        if rec["cards"][key] <= 0:
            rec["cards"].pop(key, None)
        self.add_card(member.id, key, save=False)
        self.save()
        notes = self._apply_rewards(member.id)
        dorm = self.bot.get_cog("Dorm")
        if dorm:
            notes.extend(dorm.pop_unlock_notes(member.id))
        embed = self.embed_card(
            key,
            title_prefix="🎁 Gifted Chocolate Frog",
            flag=f"from {interaction.user.display_name}",
            footer="Duplicates only · use /frog trade to swap",
        )
        content = (
            f"{interaction.user.mention} gifted "
            f"{CARDS[key]['emoji']} **{CARDS[key]['name']}** to {member.mention}!"
        )
        if notes:
            content += "\n" + "\n".join(notes)
        await interaction.followup.send(content=content, embed=embed, file=_card_file(key))

    @frog_give.autocomplete("card")
    async def give_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return self._owned_autocomplete(interaction.user.id, current, min_count=2)

    @frog.command(
        name="trade",
        description="Propose a 1:1 Chocolate Frog card trade with someone.",
    )
    @app_commands.describe(
        member="Who you're trading with",
        offer="Card you give them",
        want="Card you want from them",
    )
    async def frog_trade(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        offer: str,
        want: str,
    ):
        if member.id == interaction.user.id or member.bot:
            await interaction.response.send_message(
                "Pick another collector.", ephemeral=True
            )
            return
        offer_id = _resolve_card(offer)
        want_id = _resolve_card(want)
        if not offer_id or not want_id:
            await interaction.response.send_message(
                "Unknown card name. Check `/frog album`.", ephemeral=True
            )
            return
        if offer_id == want_id:
            await interaction.response.send_message(
                "Trading the same card for itself is just a handshake.",
                ephemeral=True,
            )
            return
        if self.counts(interaction.user.id).get(offer_id, 0) < 1:
            await interaction.response.send_message(
                f"You don't have **{CARDS[offer_id]['name']}**.", ephemeral=True
            )
            return
        if self.counts(member.id).get(want_id, 0) < 1:
            await interaction.response.send_message(
                f"{member.display_name} doesn't have **{CARDS[want_id]['name']}**.",
                ephemeral=True,
            )
            return

        trade_id = f"{interaction.user.id}-{member.id}-{int(time.time())}"
        self.state.setdefault("trades", {})[trade_id] = {
            "from_id": interaction.user.id,
            "to_id": member.id,
            "offer": offer_id,
            "want": want_id,
            "status": "open",
            "at": time.time(),
        }
        self.save()
        view = TradeView(self, trade_id)
        embed = discord.Embed(
            title="🐸 Frog trade proposed",
            description=(
                f"{interaction.user.mention} offers "
                f"{CARDS[offer_id]['emoji']} **{CARDS[offer_id]['name']}**\n"
                f"and wants "
                f"{CARDS[want_id]['emoji']} **{CARDS[want_id]['name']}** "
                f"from {member.mention}.\n\n"
                f"{member.mention} — Accept or Decline."
            ),
            color=RARITY_COLOR["rare"],
        )
        await interaction.response.send_message(
            content=member.mention, embed=embed, view=view
        )

    @frog_trade.autocomplete("offer")
    async def trade_offer_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return self._owned_autocomplete(interaction.user.id, current, min_count=1)

    @frog_trade.autocomplete("want")
    async def trade_want_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        # Can't easily know the other member here reliably; list all cards.
        needle = (current or "").lower()
        out = []
        for cid, meta in sorted(CARDS.items(), key=lambda kv: kv[1]["name"]):
            if needle and needle not in meta["name"].lower() and needle not in cid:
                continue
            out.append(app_commands.Choice(name=meta["name"], value=cid))
            if len(out) >= 25:
                break
        return out

    def _owned_autocomplete(
        self, user_id: int, current: str, *, min_count: int
    ) -> list[app_commands.Choice[str]]:
        counts = self.counts(user_id)
        needle = (current or "").lower()
        out = []
        for cid, n in sorted(counts.items(), key=lambda kv: CARDS[kv[0]]["name"]):
            if n < min_count:
                continue
            name = CARDS[cid]["name"]
            if needle and needle not in name.lower() and needle not in cid:
                continue
            label = f"{name} (×{n})" if n > 1 else name
            out.append(app_commands.Choice(name=label[:100], value=cid))
            if len(out) >= 25:
                break
        return out

    async def staff_prize(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        card: str,
    ) -> None:
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        key = _resolve_card(card)
        if not key:
            await interaction.response.send_message(
                "Unknown card.", ephemeral=True
            )
            return
        if member.bot:
            await interaction.response.send_message(
                "Bots don't collect frogs.", ephemeral=True
            )
            return
        await interaction.response.defer()
        count, is_new = self.add_card(member.id, key, save=False)
        self.save()
        notes = self._apply_rewards(member.id)
        dorm = self.bot.get_cog("Dorm")
        if dorm:
            notes.extend(dorm.pop_unlock_notes(member.id))
        flag = "**NEW!**" if is_new else f"now ×{count}"
        embed = self.embed_card(
            key,
            title_prefix="🏆 Prize Chocolate Frog",
            flag=flag,
            footer=f"Awarded by {interaction.user.display_name}",
        )
        content = (
            f"{interaction.user.mention} awarded "
            f"{CARDS[key]['emoji']} **{CARDS[key]['name']}** to {member.mention}!"
        )
        if notes:
            content += "\n" + "\n".join(notes)
        await interaction.followup.send(content=content, embed=embed, file=_card_file(key))

    async def staff_prize_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        needle = (current or "").lower()
        out = []
        for cid, meta in sorted(CARDS.items(), key=lambda kv: kv[1]["name"]):
            label = f"{meta['name']} ({meta['rarity']})"
            if needle and needle not in label.lower() and needle not in cid:
                continue
            out.append(app_commands.Choice(name=label[:100], value=cid))
            if len(out) >= 25:
                break
        return out


async def setup(bot: commands.Bot):
    await bot.add_cog(Frogs(bot))
