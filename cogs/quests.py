"""
Shared challenges. The bot posts one question for everyone at once and the
first three members to answer correctly earn points for their house.

    /challenge <tier>     - staff, post one right now
    /challengeconfig      - staff, set the channel and posting time
    /challengestatus      - what's open at the moment

Three tiers, each on its own schedule:

    Daily   1 point   a lore question
    Trial   3 points  every 3 days - harder lore, or a scrambled word
    Rite    5 points  weekly - a pattern to work out, or a blank to fill

Everyone gets ONE attempt per challenge. With four buttons on screen,
unlimited retries would just be brute force, and there are only three
places to win.

Answers are given privately, so nobody can read the solution off someone
else's screen. Only the winners are announced.
"""

import datetime
import json
import logging
import os
import random
import re
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("velmora.quests")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
BANK_PATH = DATA_DIR / "quests.json"
PROGRESS_PATH = STATE_DIR / "challenges.json"

WINNERS_WANTED = 3

TIERS = {
    "daily": {"label": "Daily Challenge", "points": 1, "every": 1,
              "blurb": "A question about Velmora.", "color": 0x6C5CE7},
    "trial": {"label": "Trial", "points": 3, "every": 3,
              "blurb": "Every three days. Harder.", "color": 0x3FA9A0},
    "rite":  {"label": "Weekly Rite", "points": 5, "every": 7,
              "blurb": "Once a week. Hardest of the three.", "color": 0xD9A441},
}

# A tier is due when this much time has passed. Kept a little under the
# nominal period so a challenge never drifts an hour later each cycle.
def _due_after(tier: str) -> float:
    return TIERS[tier]["every"] * 24 * 3600 - 2 * 3600


def _normalize(text: str) -> str:
    """Compare answers forgivingly: case, spacing and punctuation don't count."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _scramble(word: str, rng: random.Random) -> str:
    letters = list(word)
    for _ in range(12):
        rng.shuffle(letters)
        if "".join(letters) != word:
            break
    return " ".join(letters)


def _number_pattern(rng: random.Random) -> dict:
    """Build a number sequence and the rule behind it. Generated fresh every
    time, so this tier never repeats itself and needs no written content."""
    family = rng.choice(["affine", "fib", "square", "interleave", "geometric"])

    if family == "affine":
        start, r, c = rng.randint(1, 6), rng.randint(2, 3), rng.randint(1, 6)
        terms = [start]
        for _ in range(4):
            terms.append(terms[-1] * r + c)
        rule = f"each term is the one before it times {r}, plus {c}"

    elif family == "fib":
        a, b = rng.randint(1, 7), rng.randint(2, 9)
        terms = [a, b]
        for _ in range(4):
            terms.append(terms[-1] + terms[-2])
        rule = "each term is the sum of the two before it"

    elif family == "square":
        offset, start_n = rng.randint(0, 5), rng.randint(1, 4)
        terms = [(n * n) + offset for n in range(start_n, start_n + 5)]
        rule = f"consecutive squares plus {offset}" if offset else "consecutive squares"

    elif family == "geometric":
        start, r = rng.randint(2, 6), rng.randint(2, 4)
        terms = [start * (r ** i) for i in range(5)]
        rule = f"each term is the one before it times {r}"

    else:
        # The two runs are kept in separate ranges on purpose: if they can
        # collide the sequence stops having one defensible answer.
        a0, ad = rng.randint(1, 9), rng.randint(2, 7)
        b0, bd = rng.randint(40, 60), rng.randint(2, 9)
        terms = []
        for i in range(3):
            terms.append(a0 + ad * i)
            terms.append(b0 + bd * i)
        rule = f"two sequences alternating - one rising by {ad}, the other by {bd}"

    return {
        "kind": "text",
        "prompt": "Maynard left a sequence in his journal. What comes next?\n\n**"
                  + ", ".join(str(t) for t in terms[:-1]) + ", ?**",
        "answer": str(terms[-1]),
        "explain": f"The rule: {rule}.",
    }


def _symbol_pattern(rng: random.Random, emojis: list) -> dict | None:
    """A repeating run of house emblems with the next one missing."""
    if len(emojis) < 4:
        return None

    style = rng.choice(["cycle", "doubled"])
    period = rng.randint(3, 4)
    cycle = rng.sample(emojis, period)
    full = cycle * 4 if style == "cycle" else [e for e in cycle for _ in (0, 1)] * 3
    run = full[:rng.choice([7, 8]) if style == "cycle" else rng.choice([7, 9])]
    answer = full[len(run)]

    options = list(dict.fromkeys(cycle))
    for e in emojis:
        if len(options) >= 4:
            break
        if e not in options:
            options.append(e)
    rng.shuffle(options)

    return {
        "kind": "choice",
        "prompt": "The emblems repeat. Which comes next?\n\n" + " ".join(run) + "  **?**",
        "options": options,
        "correct_index": options.index(answer),
        "explain": f"The run repeats every {period if style == 'cycle' else period * 2}.",
    }


def _load_bank() -> dict:
    try:
        with open(BANK_PATH, "r", encoding="utf-8") as f:
            bank = json.load(f)
    except (OSError, json.JSONDecodeError):
        log.exception("Could not load %s - challenges will be unavailable.", BANK_PATH)
        bank = {}
    for key in ("easy", "hard", "scrambles", "fill_blanks"):
        bank.setdefault(key, [])
    return bank


class Quests(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bank = _load_bank()
        self.state = self._load_state()
        self.scheduler.start()
        log.info(
            "Challenge bank: %d easy, %d hard, %d scrambles, %d blanks",
            len(self.bank["easy"]), len(self.bank["hard"]),
            len(self.bank["scrambles"]), len(self.bank["fill_blanks"]),
        )

    def cog_unload(self):
        self.scheduler.cancel()

    # ------------------------------------------------------------- storage

    def _blank(self) -> dict:
        return {
            "settings": {"channel_id": None, "hour": 18, "weekday": 6},
            "last_posted": {},
            "active": {},
            "seen": {},
        }

    def _load_state(self) -> dict:
        try:
            with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            for key, value in self._blank().items():
                state.setdefault(key, value)
            for key, value in self._blank()["settings"].items():
                state["settings"].setdefault(key, value)
            return state
        except FileNotFoundError:
            return self._blank()
        except (OSError, json.JSONDecodeError):
            log.exception("Challenge state unreadable - starting fresh.")
            return self._blank()

    def save(self) -> None:
        try:
            PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = PROGRESS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, PROGRESS_PATH)
        except OSError:
            log.exception("Could not save challenge state.")

    @property
    def settings(self) -> dict:
        return self.state["settings"]

    def _is_staff(self, member) -> bool:
        store = self.bot.get_cog("Store")
        if store is not None:
            return store.is_staff(member)
        perms = getattr(member, "guild_permissions", None)
        return bool(perms and (getattr(perms, "manage_guild", False)
                               or getattr(perms, "administrator", False)))

    # --------------------------------------------------------- task making

    def _pick(self, pool_name: str, pool: list, rng: random.Random):
        """Pick something the server hasn't seen recently. Once the pool is
        exhausted the history resets and it starts over."""
        if not pool:
            return None
        seen = set(self.state["seen"].get(pool_name, []))
        choices = [i for i in range(len(pool)) if i not in seen]
        if not choices:
            self.state["seen"][pool_name] = []
            choices = list(range(len(pool)))
        index = rng.choice(choices)
        self.state["seen"].setdefault(pool_name, []).append(index)
        return pool[index]

    def build_task(self, tier: str, rng: random.Random = None) -> dict | None:
        rng = rng or random.Random()

        if tier == "daily":
            kind = "lore_easy"
        elif tier == "trial":
            kind = rng.choice(["lore_hard", "scramble"])
        else:
            kind = rng.choice(["pattern", "blank", "lore_hard"])

        if kind == "pattern":
            from cogs.store import HOUSES
            if rng.random() < 0.5:
                return _number_pattern(rng)
            return _symbol_pattern(rng, [h["emoji"] for h in HOUSES.values()]) \
                or _number_pattern(rng)

        if kind in ("lore_easy", "lore_hard"):
            pool_name = "easy" if kind == "lore_easy" else "hard"
            q = self._pick(pool_name, self.bank[pool_name], rng)
            if q is None:
                return None
            # The bank stores the right answer first, so shuffle or every
            # question would be "pick the top button".
            correct = q["options"][q["answer"]]
            options = list(q["options"])
            rng.shuffle(options)
            return {
                "kind": "choice",
                "prompt": q["q"],
                "options": options,
                "correct_index": options.index(correct),
            }

        if kind == "scramble":
            s = self._pick("scrambles", self.bank["scrambles"], rng)
            if s is None:
                return None
            return {
                "kind": "text",
                "prompt": f"Unscramble it:\n\n**{_scramble(s['word'], rng)}**",
                "answer": s["word"],
                "hint": s.get("hint"),
            }

        b = self._pick("fill_blanks", self.bank["fill_blanks"], rng)
        if b is None:
            return None
        return {
            "kind": "text",
            "prompt": f"Fill in the blank:\n\n**{b['text']}**",
            "answer": b["answer"],
            "hint": b.get("hint"),
        }

    # ------------------------------------------------------------ the round

    def check(self, task: dict, given) -> bool:
        if task["kind"] == "choice":
            return given == task["correct_index"]
        return _normalize(given) == _normalize(task["answer"])

    def public_embed(self, tier: str) -> discord.Embed:
        """What everyone sees. Never contains the answer while it's open."""
        round_ = self.state["active"][tier]
        meta = TIERS[tier]
        task = round_["task"]

        embed = discord.Embed(
            title=meta["label"],
            description=task["prompt"],
            color=meta["color"],
        )
        if task.get("hint"):
            embed.add_field(name="Hint", value=task["hint"], inline=False)

        winners = round_["winners"]
        if winners:
            from cogs.store import HOUSES
            lines = []
            for i, w in enumerate(winners, start=1):
                house = w.get("house")
                emblem = HOUSES[house]["emoji"] + " " if house in HOUSES else ""
                lines.append(f"`{i}.` {emblem}<@{w['id']}>")
            embed.add_field(
                name=f"Solved by ({len(winners)}/{WINNERS_WANTED})",
                value="\n".join(lines),
                inline=False,
            )

        if round_.get("closed"):
            shown = (task["answer"] if task["kind"] == "text"
                     else task["options"][task["correct_index"]])
            explain = f" {task['explain']}" if task.get("explain") else ""
            embed.add_field(name="Answer", value=f"**{shown}**{explain}", inline=False)
            embed.set_footer(text="Closed. The next one comes around soon.")
        else:
            remaining = WINNERS_WANTED - len(winners)
            embed.set_footer(
                text=f"{meta['points']} point{'s' if meta['points'] != 1 else ''} each to the "
                     f"first {WINNERS_WANTED} correct • {remaining} place"
                     f"{'s' if remaining != 1 else ''} left • one attempt each"
            )
        return embed

    async def post_challenge(self, tier: str, channel) -> bool:
        """Close whatever was running for this tier and post a fresh one."""
        old = self.state["active"].get(tier)
        if old and not old.get("closed"):
            old["closed"] = True
            await self._refresh_message(tier, old)

        task = self.build_task(tier)
        if task is None:
            log.warning("No material available for tier %s", tier)
            return False

        self.state["active"][tier] = {
            "task": task,
            "winners": [],
            "attempted": [],
            "channel_id": channel.id,
            "message_id": None,
            "posted_at": time.time(),
            "closed": False,
        }

        view = ChallengeView(self, tier)
        try:
            message = await channel.send(embed=self.public_embed(tier), view=view)
        except discord.DiscordException:
            log.exception("Could not post the %s challenge.", tier)
            self.state["active"].pop(tier, None)
            return False

        self.state["active"][tier]["message_id"] = message.id
        self.state["last_posted"][tier] = time.time()
        self.save()
        return True

    async def _refresh_message(self, tier: str, round_: dict = None):
        """Re-render the public message after someone places or it closes."""
        round_ = round_ or self.state["active"].get(tier)
        if not round_ or not round_.get("message_id"):
            return
        channel = self.bot.get_channel(round_["channel_id"])
        if channel is None:
            return
        try:
            message = await channel.fetch_message(round_["message_id"])
            view = None if round_.get("closed") else ChallengeView(self, tier)
            await message.edit(embed=self.public_embed(tier), view=view)
        except discord.DiscordException:
            log.exception("Could not refresh the %s challenge message.", tier)

    # ----------------------------------------------------------- scheduling

    @tasks.loop(minutes=5)
    async def scheduler(self):
        channel_id = self.settings.get("channel_id")
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        if now.hour != self.settings.get("hour", 18):
            return

        for tier in ("daily", "trial", "rite"):
            last = self.state["last_posted"].get(tier, 0)
            if time.time() - last < _due_after(tier):
                continue
            if tier == "rite" and now.weekday() != self.settings.get("weekday", 6):
                continue
            await self.post_challenge(tier, channel)

    @scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()
        # Buttons stop working across a restart unless the views are
        # re-registered, and this bot redeploys often.
        for tier, round_ in self.state.get("active", {}).items():
            if not round_.get("closed"):
                try:
                    self.bot.add_view(ChallengeView(self, tier),
                                      message_id=round_.get("message_id"))
                except Exception:
                    log.exception("Could not restore the %s challenge view.", tier)

    # ------------------------------------------------------------ commands

    @app_commands.command(name="challenge", description="Post a challenge to the channel right now.")
    @app_commands.describe(tier="Which challenge to post")
    @app_commands.choices(tier=[
        app_commands.Choice(name="Daily (1 point)", value="daily"),
        app_commands.Choice(name="Trial (3 points)", value="trial"),
        app_commands.Choice(name="Weekly Rite (5 points)", value="rite"),
    ])
    async def challenge(self, interaction: discord.Interaction, tier: app_commands.Choice[str]):
        if not self._is_staff(interaction.user):
            await interaction.response.send_message(
                "Only staff can post a challenge.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Posting the {TIERS[tier.value]['label'].lower()}.", ephemeral=True
        )
        posted = await self.post_challenge(tier.value, interaction.channel)
        if not posted:
            await interaction.followup.send(
                "That didn't post — check the logs.", ephemeral=True
            )

    @app_commands.command(name="challengeconfig",
                          description="Set where and when challenges post automatically.")
    @app_commands.describe(channel="Where challenges should appear",
                           hour="Hour in UTC, 0-23", weekday="Which day the weekly Rite lands on")
    @app_commands.choices(weekday=[
        app_commands.Choice(name=d, value=i) for i, d in enumerate(
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"))
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def challengeconfig(self, interaction: discord.Interaction,
                              channel: discord.TextChannel, hour: int = 18,
                              weekday: app_commands.Choice[int] = None):
        if not 0 <= hour <= 23:
            await interaction.response.send_message("Hour has to be 0-23.", ephemeral=True)
            return

        self.settings["channel_id"] = channel.id
        self.settings["hour"] = hour
        if weekday is not None:
            self.settings["weekday"] = weekday.value
        self.save()

        day = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
               "Sunday")[self.settings["weekday"]]
        await interaction.response.send_message(
            f"Challenges will post in {channel.mention} at {hour:02d}:00 UTC — "
            f"daily every day, the Trial every 3 days, and the Rite on {day}s.",
            ephemeral=True,
        )

    @app_commands.command(name="challengestatus", description="What's open right now.")
    async def challengestatus(self, interaction: discord.Interaction):
        lines = []
        for tier in ("daily", "trial", "rite"):
            meta = TIERS[tier]
            round_ = self.state["active"].get(tier)
            if round_ and not round_.get("closed"):
                left = WINNERS_WANTED - len(round_["winners"])
                lines.append(f"**{meta['label']}** — open, {left} place"
                             f"{'s' if left != 1 else ''} left")
            else:
                last = self.state["last_posted"].get(tier)
                nxt = (last + _due_after(tier)) if last else None
                lines.append(f"**{meta['label']}** — "
                             + (f"next <t:{int(nxt)}:R>" if nxt else "not scheduled yet"))

        if not self.settings.get("channel_id"):
            lines.append("\n*No channel set — staff can set one with `/challengeconfig`.*")

        await interaction.response.send_message(
            embed=discord.Embed(title="Challenges", description="\n".join(lines),
                                color=0x3FA9A0),
            ephemeral=True,
        )


# --------------------------------------------------------------- the views

class ChallengeView(discord.ui.View):
    """Persistent view - survives a redeploy, because these rounds can be
    open for hours and this bot restarts often."""

    def __init__(self, cog: Quests, tier: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.tier = tier

        round_ = cog.state["active"].get(tier) or {}
        task = round_.get("task", {})

        if task.get("kind") == "choice":
            for i, option in enumerate(task.get("options", [])):
                self.add_item(ChoiceButton(tier, i, option))
        else:
            self.add_item(AnswerButton(tier))

    async def submit(self, interaction: discord.Interaction, given):
        cog, tier = self.cog, self.tier
        round_ = cog.state["active"].get(tier)

        if not round_ or round_.get("closed"):
            await interaction.response.send_message(
                "That one's already closed.", ephemeral=True
            )
            return

        user = interaction.user
        if any(w["id"] == user.id for w in round_["winners"]):
            await interaction.response.send_message(
                "You've already placed in this one.", ephemeral=True
            )
            return
        if user.id in round_["attempted"]:
            await interaction.response.send_message(
                "You've had your attempt at this one. Next challenge comes around soon.",
                ephemeral=True,
            )
            return

        store = cog.bot.get_cog("Store")
        house = store.member_house(user) if store else None
        if not house:
            # Not counted as an attempt - they haven't actually played yet.
            await interaction.response.send_message(
                "You need a house before you can win points for one. Ask staff for your "
                "house role, then come back — your attempt is still unused.",
                ephemeral=True,
            )
            return

        round_["attempted"].append(user.id)

        if not cog.check(round_["task"], given):
            cog.save()
            await interaction.response.send_message(
                "Not this time. The answer goes up when the challenge closes.",
                ephemeral=True,
            )
            return

        round_["winners"].append({"id": user.id, "name": user.display_name, "house": house})
        place = len(round_["winners"])

        points = TIERS[tier]["points"]
        if store is not None:
            store.record(
                house=house,
                delta=points,
                actor_id=cog.bot.user.id if cog.bot.user else 0,
                target_id=user.id,
                reason=TIERS[tier]["label"],
            )
        if place >= WINNERS_WANTED:
            round_["closed"] = True
        cog.save()

        from cogs.store import house_display
        ordinal = {1: "First", 2: "Second", 3: "Third"}.get(place, f"#{place}")
        await interaction.response.send_message(
            f"Correct — **{ordinal}**. **+{points}** to {house_display(house)}.",
            ephemeral=True,
        )
        await cog._refresh_message(tier)


class ChoiceButton(discord.ui.Button):
    def __init__(self, tier: str, index: int, label: str):
        # Discord caps button labels at 80 characters.
        super().__init__(label=label[:80], style=discord.ButtonStyle.secondary,
                         custom_id=f"velmora:challenge:{tier}:{index}")
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        await self.view.submit(interaction, self.index)


class AnswerButton(discord.ui.Button):
    def __init__(self, tier: str):
        super().__init__(label="Answer", style=discord.ButtonStyle.primary,
                         custom_id=f"velmora:challenge:{tier}:text")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AnswerModal(self.view))


class AnswerModal(discord.ui.Modal, title="Your answer"):
    response = discord.ui.TextInput(label="Answer", max_length=100)

    def __init__(self, view: ChallengeView):
        super().__init__()
        self.challenge_view = view

    async def on_submit(self, interaction: discord.Interaction):
        await self.challenge_view.submit(interaction, str(self.response))


async def setup(bot: commands.Bot):
    await bot.add_cog(Quests(bot))
