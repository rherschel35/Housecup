"""
Private broom racing on the Quidditch pitch.

    /broomrace              - fly one of 100 courses (6 private stages)
    /broomnotes [course]    - permanent study notes you've unlocked

Higher Speed/Altitude means fewer button choices per stage (the broom
filters noise). Finish a course to unlock its study note forever; notes
warn you off one trap when you race that course again.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("velmora.broom_race")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_DIR = Path(os.getenv("STATE_DIR", str(DATA_DIR)))
COURSES_PATH = DATA_DIR / "broom_courses.json"
STATE_PATH = STATE_DIR / "broom_races.json"

# Same pitch as team Quidditch.
QUIDDITCH_CHANNEL_ID = 1553089438933065913

RACE_COLOR = 0x2E6B4F
STAGE_TIMEOUT = 90
STAGES = 6

# kind -> base time penalty (seconds of "race time")
KIND_PENALTY = {
    "clean": 0,
    "tech": 0,
    "bold": 4,
    "stall": 6,
    "trap": 12,
}


def _load_courses() -> list[dict]:
    with open(COURSES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    courses = data.get("courses") or []
    if len(courses) != 100:
        log.warning("Expected 100 broom courses, found %s", len(courses))
    return courses


def max_options_for_skill(skill: float) -> int:
    """Higher flight stats → fewer choices (broom filters the noise)."""
    if skill >= 9:
        return 2
    if skill >= 7:
        return 3
    if skill >= 5:
        return 4
    if skill >= 3:
        return 5
    return 6


def option_score(opt: dict, speed: int, altitude: int) -> int:
    """Lower is better when ranking which options to keep visible."""
    kind = opt.get("kind", "bold")
    if kind == "trap":
        return 50
    if kind == "stall":
        return 40
    if kind == "bold":
        return 30
    stat = opt.get("stat")
    thr = int(opt.get("threshold", 0) or 0)
    have = speed if stat == "speed" else altitude if stat == "altitude" else max(speed, altitude)
    gap = max(0, thr - have)
    base = 0 if kind == "clean" else 2
    return base + gap * 3


def pick_stage_options(
    stage: dict,
    speed: int,
    altitude: int,
    *,
    studied: bool = False,
    rng: random.Random,
) -> list[dict]:
    opts = list(stage.get("options") or [])
    if not opts:
        return []
    skill = (speed + altitude) / 2
    limit = min(max_options_for_skill(skill), len(opts))

    ranked = sorted(opts, key=lambda o: (option_score(o, speed, altitude), o.get("label", "")))
    # Always keep the best clean/tech if present.
    chosen: list[dict] = []
    for o in ranked:
        if o.get("kind") in ("clean", "tech"):
            chosen.append(o)
            break
    # Fill with a mix; prefer variety of kinds when skill is low.
    for o in ranked:
        if o in chosen:
            continue
        chosen.append(o)
        if len(chosen) >= limit:
            break

    # Studied courses: ensure one trap is visible (and marked) when skill still shows noise.
    if studied and limit >= 3:
        if not any(o.get("kind") == "trap" for o in chosen):
            trap = next((o for o in opts if o.get("kind") == "trap"), None)
            if trap and chosen:
                chosen[-1] = trap

    rng.shuffle(chosen)
    out = []
    for o in chosen:
        copy = dict(o)
        if studied and copy.get("kind") == "trap":
            copy["label"] = f"⚠ {copy['label']}"[:80]
            copy["warned"] = True
        out.append(copy)
    return out[:limit]


def resolve_choice(opt: dict, speed: int, altitude: int, rng: random.Random) -> tuple[int, str]:
    """Return (penalty_seconds, flavor line)."""
    kind = opt.get("kind", "bold")
    if opt.get("warned"):
        return 10, "You recognized the trap from your notes — still costly, but you clipped free."

    if kind in ("clean", "tech"):
        stat = opt.get("stat")
        thr = int(opt.get("threshold", 0) or 0)
        have = speed if stat == "speed" else altitude if stat == "altitude" else max(speed, altitude)
        if have >= thr:
            return 0, "Clean line. The markers blur past."
        gap = thr - have
        if gap <= 2 and rng.random() < 0.55:
            return 2, "A little shaky — you make it with scraped knuckles."
        return 3 + gap, "Your broom complains; you lose the racing line."

    if kind == "bold":
        if rng.random() < 0.45:
            return 1, "Audacity pays off. Barely."
        return KIND_PENALTY["bold"], "Style points, time lost."

    if kind == "stall":
        return KIND_PENALTY["stall"], "Safe. Slow. The pitch notices."

    # trap
    return KIND_PENALTY["trap"], "Wrong way — the course exacts a toll."


class StageButton(discord.ui.Button):
    def __init__(self, race: "RaceSession", opt: dict):
        style = discord.ButtonStyle.secondary
        if opt.get("warned"):
            style = discord.ButtonStyle.danger
        elif opt.get("kind") in ("clean", "tech"):
            style = discord.ButtonStyle.primary
        super().__init__(label=opt.get("label", "…")[:80], style=style)
        self.race = race
        self.opt = opt

    async def callback(self, interaction: discord.Interaction):
        await self.race.cog.on_stage_pick(interaction, self.race, self.opt)


class StageView(discord.ui.View):
    def __init__(self, race: "RaceSession", options: list[dict]):
        super().__init__(timeout=STAGE_TIMEOUT)
        self.race = race
        for opt in options[:6]:
            self.add_item(StageButton(race, opt))

    async def on_timeout(self):
        await self.race.cog.on_stage_timeout(self.race)


class RaceSession:
    def __init__(self, cog: "BroomRace", user_id: int, course: dict, speed: int, altitude: int,
                 studied: bool):
        self.cog = cog
        self.user_id = user_id
        self.course = course
        self.speed = speed
        self.altitude = altitude
        self.studied = studied
        self.stage_index = 0
        self.penalty = 0
        self.log_lines: list[str] = []
        self.rng = random.Random(f"{user_id}:{course['id']}:{int(time.time())}")
        self.message: discord.WebhookMessage | discord.Message | None = None
        self.view: StageView | None = None
        self.done = False


class BroomRace(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.courses = _load_courses()
        self.by_id = {c["id"]: c for c in self.courses}
        self.by_name = {c["name"].lower(): c for c in self.courses}
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()
        self.active: dict[int, RaceSession] = {}

    def _load_state(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        except (OSError, json.JSONDecodeError):
            log.exception("broom race state unreadable")
            data = {}
        data.setdefault("notes", {})   # uid -> [course_id, ...]
        data.setdefault("best", {})    # uid -> {course_id: {penalty, at}}
        return data

    def save(self) -> None:
        try:
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save broom race state")

    def notes_of(self, user_id: int) -> list[str]:
        return list(self.state["notes"].get(str(user_id), []))

    def unlock_note(self, user_id: int, course_id: str) -> bool:
        key = str(user_id)
        have = self.state["notes"].setdefault(key, [])
        if course_id in have:
            return False
        have.append(course_id)
        self.save()
        return True

    def record_best(self, user_id: int, course_id: str, penalty: int) -> bool:
        key = str(user_id)
        bag = self.state["best"].setdefault(key, {})
        prev = bag.get(course_id)
        if prev is not None and int(prev.get("penalty", 999)) <= penalty:
            return False
        bag[course_id] = {"penalty": penalty, "at": int(time.time())}
        self.save()
        return True

    def _flight_stats(self, user_id: int) -> tuple[int, int] | None:
        brooms = self.bot.get_cog("Brooms")
        if not brooms:
            return None
        broom = brooms.broom_of(user_id)
        if not broom:
            return None
        from cogs.brooms import effective_stats
        eff = effective_stats(broom)
        return int(eff.get("speed", 0)), int(eff.get("altitude", 0))

    def _pick_course(self, user_id: int) -> dict:
        """Prefer unstudied courses; else any."""
        notes = set(self.notes_of(user_id))
        fresh = [c for c in self.courses if c["id"] not in notes]
        pool = fresh or self.courses
        return random.choice(pool)

    def _stage_embed(self, race: RaceSession, options: list[dict]) -> discord.Embed:
        course = race.course
        n = race.stage_index + 1
        stage = course["stages"][race.stage_index]
        skill = (race.speed + race.altitude) / 2
        embed = discord.Embed(
            title=f"🧹 {course['name']} — stage {n}/{STAGES}",
            description=(
                f"*{course['blurb']}*\n\n"
                f"**{stage['prompt']}**\n\n"
                f"Your flight · Speed **{race.speed}/10** · Altitude **{race.altitude}/10**\n"
                f"Choices shown · **{len(options)}** "
                f"(skill {skill:.0f} filters the noise)\n"
                f"Time lost so far · **+{race.penalty}s**"
            ),
            color=RACE_COLOR,
        )
        if race.studied:
            embed.set_footer(text="Study notes active — traps you know are marked ⚠")
        else:
            embed.set_footer(text="Private race · finish to unlock this course's study note")
        return embed

    def _finish_embed(self, member: discord.Member, race: RaceSession, new_note: bool,
                      new_best: bool) -> discord.Embed:
        course = race.course
        lines = "\n".join(f"· {x}" for x in race.log_lines) or "· (no notes)"
        desc = (
            f"**{member.display_name}** finishes **{course['name']}**.\n"
            f"Time lost · **+{race.penalty}s** across {STAGES} stages.\n\n"
            f"{lines}"
        )
        if new_note:
            desc += f"\n\n📓 **Study note unlocked**\n*{course['study_note']}*"
        elif race.studied:
            desc += f"\n\n📓 Notes you already hold\n*{course['study_note']}*"
        if new_best:
            desc += "\n\n🏆 Personal best on this course."
        return discord.Embed(title="🏁 Race complete", description=desc, color=RACE_COLOR)

    @app_commands.command(
        name="broomrace",
        description="Private 6-stage broom race on a course (Quidditch channel). Higher stats → fewer choices.",
    )
    async def broomrace(self, interaction: discord.Interaction):
        if interaction.channel_id != QUIDDITCH_CHANNEL_ID:
            await interaction.response.send_message(
                f"Broom races run on the pitch — try <#{QUIDDITCH_CHANNEL_ID}>.",
                ephemeral=True,
            )
            return

        hexes = self.bot.get_cog("Hexes")
        if hexes and await hexes.deny_if_limp_wand(interaction):
            return

        if interaction.user.id in self.active:
            await interaction.response.send_message(
                "You're already mid-race. Finish your stages (or wait for timeout).",
                ephemeral=True,
            )
            return

        stats = self._flight_stats(interaction.user.id)
        if not stats:
            await interaction.response.send_message(
                "You need a claimed broom first — `/broom`.", ephemeral=True
            )
            return
        speed, altitude = stats
        course = self._pick_course(interaction.user.id)
        studied = course["id"] in self.notes_of(interaction.user.id)
        race = RaceSession(self, interaction.user.id, course, speed, altitude, studied)
        self.active[interaction.user.id] = race

        options = pick_stage_options(
            course["stages"][0],
            speed,
            altitude,
            studied=studied,
            rng=race.rng,
        )
        view = StageView(race, options)
        race.view = view

        # Public ping that someone is racing; picks stay private (ephemeral).
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🧹 Broom race",
                description=(
                    f"**{interaction.user.display_name}** takes **{course['name']}**.\n"
                    f"*{course['blurb']}*\n\n"
                    "Stages are private — only they see the choices."
                ),
                color=RACE_COLOR,
            )
        )
        race.message = await interaction.followup.send(
            embed=self._stage_embed(race, options), view=view, ephemeral=True, wait=True
        )

    async def on_stage_pick(self, interaction: discord.Interaction, race: RaceSession, opt: dict):
        if interaction.user.id != race.user_id:
            await interaction.response.send_message("Not your race.", ephemeral=True)
            return
        if race.done or self.active.get(race.user_id) is not race:
            await interaction.response.send_message("This race is over.", ephemeral=True)
            return
        if race.view:
            race.view.stop()

        penalty, flavor = resolve_choice(opt, race.speed, race.altitude, race.rng)
        race.penalty += penalty
        label = opt.get("label", "a choice")
        race.log_lines.append(
            f"Stage {race.stage_index + 1}: {label} → +{penalty}s — {flavor}"
        )

        race.stage_index += 1
        if race.stage_index >= STAGES:
            await self._finish(interaction, race)
            return

        options = pick_stage_options(
            race.course["stages"][race.stage_index],
            race.speed,
            race.altitude,
            studied=race.studied,
            rng=race.rng,
        )
        view = StageView(race, options)
        race.view = view
        await interaction.response.edit_message(
            embed=self._stage_embed(race, options), view=view
        )

    async def on_stage_timeout(self, race: RaceSession):
        if race.done or self.active.get(race.user_id) is not race:
            return
        # DNF remaining stages — still bank a study note so timeouts aren't a dead end.
        while race.stage_index < STAGES:
            race.penalty += 15
            race.log_lines.append(
                f"Stage {race.stage_index + 1}: timed out → +15s — the course moved on."
            )
            race.stage_index += 1
        race.done = True
        self.active.pop(race.user_id, None)
        new_note = self.unlock_note(race.user_id, race.course["id"])
        new_best = self.record_best(race.user_id, race.course["id"], race.penalty)
        if race.message is not None:
            try:
                await race.message.edit(
                    embed=discord.Embed(
                        title="🏁 Race timed out",
                        description=(
                            f"**{race.course['name']}** — stages left unflown.\n"
                            f"Time lost · **+{race.penalty}s**\n"
                            + (
                                f"\n📓 Study note unlocked\n*{race.course['study_note']}*"
                                if new_note
                                else ""
                            )
                            + ("\n\n🏆 Personal best (somehow)." if new_best else "")
                        ),
                        color=0x95A5A6,
                    ),
                    view=None,
                )
            except discord.DiscordException:
                log.exception("Could not edit timed-out broom race message")

    async def _finish(self, interaction: discord.Interaction, race: RaceSession):
        race.done = True
        if race.view:
            race.view.stop()
        self.active.pop(race.user_id, None)
        new_note = self.unlock_note(race.user_id, race.course["id"])
        new_best = self.record_best(race.user_id, race.course["id"], race.penalty)
        embed = self._finish_embed(interaction.user, race, new_note, new_best)
        await interaction.response.edit_message(embed=embed, view=None)
        # Public finish line.
        try:
            await interaction.channel.send(
                embed=discord.Embed(
                    title="🏁 Broom race finished",
                    description=(
                        f"**{interaction.user.display_name}** clears **{race.course['name']}** "
                        f"with **+{race.penalty}s** time lost."
                        + (" · study note unlocked" if new_note else "")
                    ),
                    color=RACE_COLOR,
                )
            )
        except discord.DiscordException:
            log.exception("Could not announce broom race finish")

    @app_commands.command(
        name="broomnotes",
        description="Permanent study notes from broom courses you've finished.",
    )
    @app_commands.describe(course="Optional course name to read in full")
    async def broomnotes(self, interaction: discord.Interaction, course: str | None = None):
        ids = self.notes_of(interaction.user.id)
        if not ids:
            await interaction.response.send_message(
                "No study notes yet. Finish a `/broomrace` to unlock one.",
                ephemeral=True,
            )
            return

        if course:
            c = self.by_name.get(course.lower()) or self.by_id.get(course)
            if not c:
                await interaction.response.send_message(
                    "That course isn't in the book.", ephemeral=True
                )
                return
            if c["id"] not in ids:
                await interaction.response.send_message(
                    f"You haven't unlocked notes for **{c['name']}** yet.",
                    ephemeral=True,
                )
                return
            best = self.state["best"].get(str(interaction.user.id), {}).get(c["id"])
            best_line = (
                f"\nPersonal best · **+{best['penalty']}s** time lost"
                if best else ""
            )
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=f"📓 {c['name']}",
                    description=f"*{c['blurb']}*\n\n{c['study_note']}{best_line}",
                    color=RACE_COLOR,
                ),
                ephemeral=True,
            )
            return

        lines = []
        for cid in ids[:25]:
            c = self.by_id.get(cid)
            if not c:
                continue
            lines.append(f"· **{c['name']}** — {c['study_note'][:90]}")
        more = len(ids) - len(lines)
        footer = f"{len(ids)} / 100 courses studied"
        if more > 0:
            footer += f" · showing 25 · pass course: for one"
        await interaction.response.send_message(
            embed=discord.Embed(
                title="📓 Broom study notes",
                description="\n".join(lines),
                color=RACE_COLOR,
            ).set_footer(text=footer),
            ephemeral=True,
        )

    @broomnotes.autocomplete("course")
    async def _notes_ac(self, interaction: discord.Interaction, current: str):
        q = (current or "").lower()
        out = []
        for cid in self.notes_of(interaction.user.id):
            c = self.by_id.get(cid)
            if not c:
                continue
            if q and q not in c["name"].lower() and q not in cid:
                continue
            out.append(app_commands.Choice(name=c["name"][:100], value=c["name"]))
            if len(out) >= 25:
                break
        return out


async def setup(bot: commands.Bot):
    await bot.add_cog(BroomRace(bot))
