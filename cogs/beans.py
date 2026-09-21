"""
Mystery beans. Spend a point of your house's, eat a bean, find out.

    /bean   - costs 1 point; three a day

Most beans are nothing. Some pay back double, triple, even five. A few are
dreadful and cost you more than the bean did. The odds lean very slightly
against the eater on purpose - roughly -0.12 points per bean on average -
so beans are a thrill and a points sink, never a way to print points.

Results are public. People like showing off their disasters.
"""

import json
import logging
import os
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.beans")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
BEANS_PATH = STATE_DIR / "beans.json"

COST = 1
BEANS_PER_DAY = 3
WINDOW = 24 * 3600

# (weight, net change including the bean's cost, verdict, flavours)
OUTCOMES = [
    (50, -1, "Nothing.", [
        "wet parchment", "chalk dust", "cold tea", "library dust", "boiled cabbage",
        "candle wax", "pencil shavings", "plain water, somehow", "an old envelope",
        "the inside of a boot", "lukewarm porridge", "a rainy Tuesday",
    ]),
    (25, +1, "Not bad at all.", [
        "toasted marshmallow", "honey", "warm bread", "cinnamon", "butterscotch",
        "fresh mint", "apple crumble", "Vashara's herbal tonic",
    ]),
    (12, +2, "Delicious.", [
        "hot chocolate", "birthday cake", "strawberries and cream",
        "the first snow of winter", "a Veyren kitchen on a Sunday",
    ]),
    (4, +4, "Extraordinary.", [
        "pure invention - Maynard would be proud", "golden syrup and starlight",
        "the first day of summer", "winning",
    ]),
    (9, -3, "Dreadful.", [
        "troll sweat", "maze mud", "earwax", "dragon's breath", "Mordy's socks",
        "Caldrin lab fumes", "a Moonveil prank - it exploded", "regret",
    ]),
]


def expected_value() -> float:
    total = sum(w for w, *_ in OUTCOMES)
    return sum(w * net for w, net, *_ in OUTCOMES) / total


def roll(rng: random.Random) -> tuple[int, str, str]:
    """Return (net points, verdict, flavour)."""
    weights = [w for w, *_ in OUTCOMES]
    _, net, verdict, flavours = rng.choices(OUTCOMES, weights=weights, k=1)[0]
    return net, verdict, rng.choice(flavours)


class Beans(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load()
        self.rng = random.Random()

    def _load(self) -> dict:
        try:
            with open(BEANS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {"eaten": {}}
        except (OSError, json.JSONDecodeError):
            log.exception("Bean records unreadable - starting fresh.")
            return {"eaten": {}}

    def save(self) -> None:
        try:
            BEANS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = BEANS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, BEANS_PATH)
        except OSError:
            log.exception("Could not save bean records.")

    def recent(self, user_id: int, now: float = None) -> list[float]:
        now = now if now is not None else time.time()
        stamps = [t for t in self.state["eaten"].get(str(user_id), []) if now - t < WINDOW]
        self.state["eaten"][str(user_id)] = stamps
        return stamps

    def eat(self, member, now: float = None, rng: random.Random = None) -> dict:
        """Everything short of talking to Discord, so it can be tested."""
        now = now if now is not None else time.time()
        store = self.bot.get_cog("Store")
        if store is None:
            return {"error": "The ledger isn't loaded."}

        house = store.member_house(member)
        if not house:
            return {"error": "You'll need a house before you can spend its points."}

        if store.member_points(member.id, "season") < COST:
            return {"error": f"Beans cost {COST} point, and you haven't earned any this "
                             "season yet. Win a challenge or a duel first."}

        eaten = self.recent(member.id, now)
        if len(eaten) >= BEANS_PER_DAY:
            next_at = min(eaten) + WINDOW
            return {"error": f"That's your {BEANS_PER_DAY} for today. "
                             f"Another bean <t:{int(next_at)}:R>."}

        net, verdict, flavour = roll(rng or self.rng)
        store.record(
            house=house,
            delta=net,
            actor_id=self.bot.user.id if self.bot.user else 0,
            target_id=member.id,
            reason=f"Bean: {flavour}",
        )
        eaten.append(now)
        self.state["eaten"][str(member.id)] = eaten
        self.save()
        return {
            "net": net,
            "verdict": verdict,
            "flavour": flavour,
            "house": house,
            "left_today": BEANS_PER_DAY - len(eaten),
        }

    @app_commands.command(name="bean", description="Spend a point on a mystery bean. Three a day.")
    async def bean(self, interaction: discord.Interaction):
        result = self.eat(interaction.user)
        if "error" in result:
            await interaction.response.send_message(result["error"], ephemeral=True)
            return

        from cogs.store import HOUSES
        net = result["net"]
        meta = HOUSES[result["house"]]
        if net > 0:
            line, color = f"**+{net}** to {meta['emoji']} {meta['name']}.", meta["color"]
        elif net == -1:
            line, color = "The bean is gone, and so is the point.", 0x7A7A7A
        else:
            line, color = f"**{net}** for {meta['emoji']} {meta['name']}. Brutal.", 0x9E4A4A

        embed = discord.Embed(
            title=f"{interaction.user.display_name} eats a bean…",
            description=f"It tastes of **{result['flavour']}**.\n\n*{result['verdict']}* {line}",
            color=color,
        )
        left = result["left_today"]
        embed.set_footer(text=f"{left} bean{'s' if left != 1 else ''} left today"
                         if left else "No beans left today")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Beans(bot))
