"""
Private broom racing on the Quidditch pitch.

    /broomrace                     - solo fly one of 100 courses (6 private stages)
    /broomrace opponent:@member    - challenge them on the same track
    /broomnotes [course]           - permanent study notes you've unlocked

Higher Speed/Altitude means fewer button choices per stage (the broom
filters noise). Top brooms (skill 9–10) still see three: the right line,
a pick that adds time, and a trap that doubles time lost. Finish a course
to unlock its study note forever; notes warn you off traps on that course
again. Challenges share one course; lower time-lost wins.

Daily caps (UTC):
    - Solo: 5 learning races/day (hard stop — those unlock study notes)
    - Challenge: always allowed; first 5 wins pay 3 house points each
"""

from __future__ import annotations

import datetime
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
ACCEPT_TIMEOUT = 60
STAGES = 6

SOLO_DAILY_CAP = 5
CHALLENGE_POINT_CAP = 5
CHALLENGE_POINTS = 3


def today_str() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

# kind -> base time penalty (seconds of "race time")
# Trap is "double time" vs a normal wrong pick (bold).
KIND_PENALTY = {
    "clean": 0,
    "tech": 0,
    "bold": 4,
    "stall": 6,
    "trap": 8,  # 2× bold — overwritten for cumulative double in resolve_choice
}


def _load_courses() -> list[dict]:
    with open(COURSES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    courses = data.get("courses") or []
    if len(courses) != 100:
        log.warning("Expected 100 broom courses, found %s", len(courses))
    return courses


def max_options_for_skill(skill: float) -> int:
    """Higher flight stats → fewer choices (broom filters the noise).

    Top brooms (skill 9–10) still see three: the right line, a time-adder,
    and a trap that doubles your time lost.
    """
    if skill >= 9:
        return 3
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


def _meets_line(opt: dict, speed: int, altitude: int) -> bool:
    if opt.get("kind") not in ("clean", "tech"):
        return False
    stat = opt.get("stat")
    thr = int(opt.get("threshold", 0) or 0)
    have = speed if stat == "speed" else altitude if stat == "altitude" else max(speed, altitude)
    return have >= thr


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
    chosen: list[dict] = []

    # Top brooms (3 options): always offer right line + time-adder + trap.
    if limit == 3 and skill >= 9:
        right = next((o for o in ranked if _meets_line(o, speed, altitude)), None)
        if right is None:
            right = next((o for o in ranked if o.get("kind") in ("clean", "tech")), None)
        adder = next((o for o in opts if o.get("kind") == "bold"), None)
        if adder is None:
            adder = next((o for o in opts if o.get("kind") == "stall"), None)
        trap = next((o for o in opts if o.get("kind") == "trap"), None)
        for o in (right, adder, trap):
            if o is not None and all(o is not c for c in chosen):
                chosen.append(o)
        # Only if a role was missing from the course data, fill from ranked.
        for o in ranked:
            if len(chosen) >= limit:
                break
            if all(o is not c for c in chosen):
                chosen.append(o)
    else:
        # Always keep the best clean/tech if present.
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
        # Top brooms always see traps unmarked unless studied; studied marks ⚠.
        if studied and copy.get("kind") == "trap":
            copy["label"] = f"⚠ {copy['label']}"[:80]
            copy["warned"] = True
        out.append(copy)
    return out[:limit]


def resolve_choice(
    opt: dict,
    speed: int,
    altitude: int,
    rng: random.Random,
    *,
    time_lost: int = 0,
) -> tuple[int, str]:
    """Return (penalty_seconds, flavor line).

    Wrong picks add time. Traps double your time lost so far (with a floor),
    so a late mistake hurts more than an early one.
    """
    kind = opt.get("kind", "bold")
    if opt.get("warned"):
        # Knew the trap — still costs, but not a full double.
        hit = max(KIND_PENALTY["bold"], time_lost // 2)
        return hit, "You recognized the trap from your notes — still costly, but you clipped free."

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
        return KIND_PENALTY["bold"], "Wrong line — time added."

    if kind == "stall":
        return KIND_PENALTY["stall"], "Wrong line — more time added."

    # trap — double time lost so far (minimum 2× a bold mistake)
    floor = KIND_PENALTY["bold"] * 2
    doubled = max(floor, time_lost)  # add at least as much as you've already lost
    return doubled, "Trap — your time lost doubles."


class StageButton(discord.ui.Button):
    def __init__(self, race: "RaceSession", opt: dict):
        style = discord.ButtonStyle.secondary
        skill = (race.speed + race.altitude) / 2
        if opt.get("warned"):
            # Studied trap — still marked, but only via ⚠ label + red.
            style = discord.ButtonStyle.danger
        elif skill < 9 and opt.get("kind") in ("clean", "tech"):
            # Lower-skill races may hint the racing line; top brooms don't —
            # their three picks are shuffled and styled the same.
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


class ChallengeAcceptView(discord.ui.View):
    def __init__(self, match: "ChallengeMatch"):
        super().__init__(timeout=ACCEPT_TIMEOUT + 5)
        self.match = match

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        m = self.match
        if interaction.user.id != m.opponent.id:
            await interaction.response.send_message(
                "This challenge isn't yours to answer.", ephemeral=True
            )
            return
        if m.state != "pending":
            await interaction.response.send_message("Too late for that.", ephemeral=True)
            return
        await m.cog.start_challenge(interaction, m)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        m = self.match
        if interaction.user.id not in (m.challenger.id, m.opponent.id):
            await interaction.response.send_message(
                "This isn't your race challenge.", ephemeral=True
            )
            return
        if m.state != "pending":
            await interaction.response.send_message("Too late for that.", ephemeral=True)
            return
        m.state = "cancelled"
        m.cog.clear_pending(m)
        who = "withdraws" if interaction.user.id == m.challenger.id else "declines"
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(
                description=(
                    f"{interaction.user.display_name} {who}. No broom race today."
                ),
                color=0x7A7A7A,
            ),
            view=None,
        )

    async def on_timeout(self):
        m = self.match
        if m.state != "pending":
            return
        m.state = "cancelled"
        m.cog.clear_pending(m)
        if m.message is not None:
            try:
                await m.message.edit(
                    content=None,
                    embed=discord.Embed(
                        title="🧹 Challenge expired",
                        description=(
                            f"**{m.opponent.display_name}** didn't answer in time. "
                            "Challenge cancelled."
                        ),
                        color=0x95A5A6,
                    ),
                    view=None,
                )
            except discord.DiscordException:
                log.exception("Could not expire broom race challenge")


class OpenBoardButton(discord.ui.Button):
    def __init__(self, match: "ChallengeMatch"):
        super().__init__(
            label="Open my race board",
            style=discord.ButtonStyle.primary,
            emoji="🧹",
        )
        self.match = match

    async def callback(self, interaction: discord.Interaction):
        await self.match.cog.open_challenge_board(interaction, self.match)


class OpenBoardView(discord.ui.View):
    def __init__(self, match: "ChallengeMatch"):
        super().__init__(timeout=STAGE_TIMEOUT * STAGES + 60)
        self.match = match
        self.add_item(OpenBoardButton(match))

    async def on_timeout(self):
        # If someone never opened, force-finish them so the match can resolve.
        await self.match.cog.force_unopened_challengers(self.match)


class RaceSession:
    def __init__(
        self,
        cog: "BroomRace",
        user_id: int,
        course: dict,
        speed: int,
        altitude: int,
        studied: bool,
        match: "ChallengeMatch | None" = None,
    ):
        self.cog = cog
        self.user_id = user_id
        self.course = course
        self.speed = speed
        self.altitude = altitude
        self.studied = studied
        self.match = match
        self.stage_index = 0
        self.penalty = 0
        self.log_lines: list[str] = []
        self.rng = random.Random(f"{user_id}:{course['id']}:{int(time.time())}")
        self.message: discord.WebhookMessage | discord.Message | None = None
        self.view: StageView | None = None
        self.done = False
        self.timed_out = False


class ChallengeMatch:
    def __init__(
        self,
        cog: "BroomRace",
        channel: discord.abc.Messageable,
        challenger: discord.Member,
        opponent: discord.Member,
        course: dict,
    ):
        self.cog = cog
        self.channel = channel
        self.challenger = challenger
        self.opponent = opponent
        self.course = course
        self.state = "pending"  # pending | racing | done | cancelled
        self.message: discord.Message | None = None
        self.results: dict[int, int] = {}  # uid -> penalty
        self.opened: set[int] = set()
        self.announced = False

    @property
    def racer_ids(self) -> tuple[int, int]:
        return (self.challenger.id, self.opponent.id)

    def display_for(self, user_id: int) -> str:
        if user_id == self.challenger.id:
            return self.challenger.display_name
        if user_id == self.opponent.id:
            return self.opponent.display_name
        return str(user_id)


class BroomRace(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.courses = _load_courses()
        self.by_id = {c["id"]: c for c in self.courses}
        self.by_name = {c["name"].lower(): c for c in self.courses}
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()
        self.active: dict[int, RaceSession] = {}
        self.pending: dict[int, ChallengeMatch] = {}  # uid -> match (both sides)

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
        data.setdefault("versus", {})  # uid -> {wins, losses, ties}
        data.setdefault("daily", {})   # uid -> {date, solo, challenge_pts}
        return data

    def save(self) -> None:
        try:
            tmp = STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_PATH)
        except OSError:
            log.exception("Could not save broom race state")

    def daily_rec(self, user_id: int) -> dict:
        key = str(user_id)
        bag = self.state.setdefault("daily", {})
        rec = bag.get(key) or {}
        if rec.get("date") != today_str():
            rec = {"date": today_str(), "solo": 0, "challenge_pts": 0}
            bag[key] = rec
        rec.setdefault("solo", 0)
        rec.setdefault("challenge_pts", 0)
        return rec

    def solo_used(self, user_id: int) -> int:
        return int(self.daily_rec(user_id).get("solo", 0))

    def solo_left(self, user_id: int) -> int:
        return max(0, SOLO_DAILY_CAP - self.solo_used(user_id))

    def challenge_pts_used(self, user_id: int) -> int:
        return int(self.daily_rec(user_id).get("challenge_pts", 0))

    def challenge_pts_left(self, user_id: int) -> int:
        return max(0, CHALLENGE_POINT_CAP - self.challenge_pts_used(user_id))

    def consume_solo_slot(self, user_id: int) -> bool:
        """Spend one of today's solo learning races. False if already at cap."""
        rec = self.daily_rec(user_id)
        if int(rec["solo"]) >= SOLO_DAILY_CAP:
            return False
        rec["solo"] = int(rec["solo"]) + 1
        self.save()
        return True

    def try_award_challenge_points(self, member: discord.Member) -> str:
        """Award CHALLENGE_POINTS for a challenge win if under daily cap.

        Returns: "awarded" | "capped" | "no_house" | "no_store"
        """
        rec = self.daily_rec(member.id)
        if int(rec["challenge_pts"]) >= CHALLENGE_POINT_CAP:
            return "capped"
        store = self.bot.get_cog("Store")
        if not store:
            return "no_store"
        house = store.member_house(member)
        if not house:
            return "no_house"
        store.record(
            house=house,
            delta=CHALLENGE_POINTS,
            actor_id=self.bot.user.id if self.bot.user else 0,
            target_id=member.id,
            reason="Broom race challenge win",
        )
        rec["challenge_pts"] = int(rec["challenge_pts"]) + 1
        self.save()
        return "awarded"

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

    def _versus_bump(self, winner_id: int | None, a_id: int, b_id: int) -> None:
        bag = self.state.setdefault("versus", {})
        for uid in (a_id, b_id):
            bag.setdefault(str(uid), {"wins": 0, "losses": 0, "ties": 0})
        if winner_id is None:
            bag[str(a_id)]["ties"] += 1
            bag[str(b_id)]["ties"] += 1
        else:
            loser = b_id if winner_id == a_id else a_id
            bag[str(winner_id)]["wins"] += 1
            bag[str(loser)]["losses"] += 1
        self.save()

    def clear_pending(self, match: ChallengeMatch) -> None:
        for uid in match.racer_ids:
            if self.pending.get(uid) is match:
                self.pending.pop(uid, None)

    def _busy(self, user_id: int) -> str | None:
        if user_id in self.active:
            return "already mid-race"
        if user_id in self.pending:
            return "already has a broom race challenge pending"
        return None

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

    def _pick_course(self, *user_ids: int) -> dict:
        """Prefer courses none of the racers have studied yet."""
        note_sets = [set(self.notes_of(uid)) for uid in user_ids]
        fresh_all = [
            c for c in self.courses
            if all(c["id"] not in notes for notes in note_sets)
        ]
        if fresh_all:
            return random.choice(fresh_all)
        fresh_any = [
            c for c in self.courses
            if any(c["id"] not in notes for notes in note_sets)
        ]
        return random.choice(fresh_any or self.courses)

    def _stage_embed(self, race: RaceSession, options: list[dict]) -> discord.Embed:
        course = race.course
        n = race.stage_index + 1
        stage = course["stages"][race.stage_index]
        skill = (race.speed + race.altitude) / 2
        versus = ""
        if race.match:
            rival_id = (
                race.match.opponent.id
                if race.user_id == race.match.challenger.id
                else race.match.challenger.id
            )
            versus = f"\nHead-to-head vs **{race.match.display_for(rival_id)}**"
        embed = discord.Embed(
            title=f"🧹 {course['name']} — stage {n}/{STAGES}",
            description=(
                f"*{course['blurb']}*{versus}\n\n"
                f"**{stage['prompt']}**\n\n"
                f"Your flight · Speed **{race.speed}/10** · Altitude **{race.altitude}/10**\n"
                f"Choices shown · **{len(options)}** "
                f"(skill {skill:.0f} filters the noise"
                + ("; wrong pick adds time, trap doubles it" if skill >= 9 else "")
                + ")\n"
                f"Time lost so far · **+{race.penalty}s**"
            ),
            color=RACE_COLOR,
        )
        if race.studied:
            embed.set_footer(text="Study notes active — traps you know are marked ⚠")
        else:
            embed.set_footer(text="Private race · finish to unlock this course's study note")
        return embed

    def _finish_embed(
        self,
        member: discord.Member,
        race: RaceSession,
        new_note: bool,
        new_best: bool,
        waiting: bool = False,
    ) -> discord.Embed:
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
        if waiting:
            desc += "\n\n⏳ Waiting for your opponent to finish…"
        return discord.Embed(title="🏁 Race complete", description=desc, color=RACE_COLOR)

    def _begin_session(
        self,
        user_id: int,
        course: dict,
        match: ChallengeMatch | None = None,
    ) -> RaceSession | None:
        stats = self._flight_stats(user_id)
        if not stats:
            return None
        speed, altitude = stats
        studied = course["id"] in self.notes_of(user_id)
        race = RaceSession(
            self, user_id, course, speed, altitude, studied, match=match
        )
        self.active[user_id] = race
        return race

    async def _send_stage_board(
        self,
        interaction: discord.Interaction,
        race: RaceSession,
        *,
        via_followup: bool = False,
    ) -> None:
        options = pick_stage_options(
            race.course["stages"][0],
            race.speed,
            race.altitude,
            studied=race.studied,
            rng=race.rng,
        )
        view = StageView(race, options)
        race.view = view
        embed = self._stage_embed(race, options)
        if via_followup:
            race.message = await interaction.followup.send(
                embed=embed, view=view, ephemeral=True, wait=True
            )
        else:
            await interaction.response.send_message(
                embed=embed, view=view, ephemeral=True
            )
            try:
                race.message = await interaction.original_response()
            except discord.HTTPException:
                race.message = None

    # ================================================================ solo / challenge

    @app_commands.command(
        name="broomrace",
        description="Solo (5 learning races/day) or challenge someone on the same track (Quidditch channel).",
    )
    @app_commands.describe(
        opponent="Challenge this member to the same course (always allowed; wins pay pts up to 5/day)",
    )
    async def broomrace(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member | None = None,
    ):
        if interaction.channel_id != QUIDDITCH_CHANNEL_ID:
            await interaction.response.send_message(
                f"Broom races run on the pitch — try <#{QUIDDITCH_CHANNEL_ID}>.",
                ephemeral=True,
            )
            return

        hexes = self.bot.get_cog("Hexes")
        if hexes and await hexes.deny_if_limp_wand(interaction):
            return

        if opponent is None:
            await self._start_solo(interaction)
            return
        await self._offer_challenge(interaction, opponent)

    async def _start_solo(self, interaction: discord.Interaction):
        why = self._busy(interaction.user.id)
        if why:
            await interaction.response.send_message(
                f"You're {why}.", ephemeral=True
            )
            return

        if not self._flight_stats(interaction.user.id):
            await interaction.response.send_message(
                "You need a claimed broom first — `/broom`.", ephemeral=True
            )
            return

        if self.solo_left(interaction.user.id) <= 0:
            await interaction.response.send_message(
                f"You've used all **{SOLO_DAILY_CAP}** solo learning races today. "
                "Challenge someone with `/broomrace opponent:` anytime — "
                "those don't use the solo cap.",
                ephemeral=True,
            )
            return

        course = self._pick_course(interaction.user.id)
        if not self.consume_solo_slot(interaction.user.id):
            await interaction.response.send_message(
                f"You've used all **{SOLO_DAILY_CAP}** solo learning races today.",
                ephemeral=True,
            )
            return

        race = self._begin_session(interaction.user.id, course)
        if not race:
            # Refund the solo slot if we somehow failed to start.
            rec = self.daily_rec(interaction.user.id)
            rec["solo"] = max(0, int(rec["solo"]) - 1)
            self.save()
            await interaction.response.send_message(
                "You need a claimed broom first — `/broom`.", ephemeral=True
            )
            return

        left = self.solo_left(interaction.user.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🧹 Broom race",
                description=(
                    f"**{interaction.user.display_name}** takes **{course['name']}**.\n"
                    f"*{course['blurb']}*\n\n"
                    "Stages are private — only they see the choices.\n"
                    f"Solo learning races left today · **{left}/{SOLO_DAILY_CAP}**"
                ),
                color=RACE_COLOR,
            )
        )
        await self._send_stage_board(interaction, race, via_followup=True)

    async def _offer_challenge(
        self, interaction: discord.Interaction, opponent: discord.Member
    ):
        me = interaction.user
        if opponent.id == me.id:
            await interaction.response.send_message(
                "Challenge someone else — solo is just `/broomrace`.", ephemeral=True
            )
            return
        if opponent.bot:
            await interaction.response.send_message(
                "Brooms don't answer to bots.", ephemeral=True
            )
            return

        for who, label in ((me, "You're"), (opponent, f"{opponent.display_name} is")):
            why = self._busy(who.id)
            if why:
                await interaction.response.send_message(
                    f"{label} {why}.", ephemeral=True
                )
                return

        if not self._flight_stats(me.id):
            await interaction.response.send_message(
                "You need a claimed broom first — `/broom`.", ephemeral=True
            )
            return
        if not self._flight_stats(opponent.id):
            await interaction.response.send_message(
                f"{opponent.display_name} hasn't claimed a broom yet.",
                ephemeral=True,
            )
            return

        course = self._pick_course(me.id, opponent.id)
        match = ChallengeMatch(self, interaction.channel, me, opponent, course)
        self.pending[me.id] = match
        self.pending[opponent.id] = match

        my_pts_left = self.challenge_pts_left(me.id)
        their_pts_left = self.challenge_pts_left(opponent.id)
        view = ChallengeAcceptView(match)
        await interaction.response.send_message(
            content=opponent.mention,
            embed=discord.Embed(
                title="🧹 Broom race challenge",
                description=(
                    f"**{me.display_name}** challenges **{opponent.display_name}** "
                    f"on the same track.\n\n"
                    f"**Course · {course['name']}**\n*{course['blurb']}*\n\n"
                    "Accept to race head-to-head — private stages, shared course, "
                    f"lowest time lost wins. Winner earns **{CHALLENGE_POINTS}** "
                    f"house points (up to **{CHALLENGE_POINT_CAP}**/day; challenges "
                    "always allowed past that).\n\n"
                    f"Point wins left today · "
                    f"**{me.display_name}** {my_pts_left}/{CHALLENGE_POINT_CAP} · "
                    f"**{opponent.display_name}** {their_pts_left}/{CHALLENGE_POINT_CAP}"
                ),
                color=RACE_COLOR,
            ).set_footer(text=f"Expires in {ACCEPT_TIMEOUT}s"),
            view=view,
        )
        match.message = await interaction.original_response()

    async def start_challenge(
        self, interaction: discord.Interaction, match: ChallengeMatch
    ):
        if match.state != "pending":
            await interaction.response.send_message("Too late for that.", ephemeral=True)
            return
        # Re-check busy (solo race could have started? pending should block)
        for uid in match.racer_ids:
            if uid in self.active:
                await interaction.response.send_message(
                    "Someone started another race — challenge cancelled.",
                    ephemeral=True,
                )
                match.state = "cancelled"
                self.clear_pending(match)
                return

        match.state = "racing"
        self.clear_pending(match)

        view = OpenBoardView(match)
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(
                title="🧹 Race is on — same track",
                description=(
                    f"**{match.challenger.display_name}** vs "
                    f"**{match.opponent.display_name}**\n\n"
                    f"**Course · {match.course['name']}**\n"
                    f"*{match.course['blurb']}*\n\n"
                    "Both racers: press **Open my race board** for your private "
                    "6 stages. Your own Speed/Altitude still shapes how many "
                    "choices you see. Lowest time lost wins."
                ),
                color=RACE_COLOR,
            ),
            view=view,
        )
        match.message = await interaction.original_response()

    async def open_challenge_board(
        self, interaction: discord.Interaction, match: ChallengeMatch
    ):
        if match.state != "racing":
            await interaction.response.send_message(
                "This race isn't open.", ephemeral=True
            )
            return
        if interaction.user.id not in match.racer_ids:
            await interaction.response.send_message(
                "You're not in this race.", ephemeral=True
            )
            return
        if interaction.user.id in match.opened or interaction.user.id in self.active:
            await interaction.response.send_message(
                "Your board is already open (check your ephemeral messages).",
                ephemeral=True,
            )
            return
        if interaction.user.id in match.results:
            await interaction.response.send_message(
                "You've already finished this race.", ephemeral=True
            )
            return

        race = self._begin_session(interaction.user.id, match.course, match=match)
        if not race:
            await interaction.response.send_message(
                "You need a claimed broom first — `/broom`.", ephemeral=True
            )
            return
        match.opened.add(interaction.user.id)
        await self._send_stage_board(interaction, race, via_followup=False)

    async def force_unopened_challengers(self, match: ChallengeMatch):
        if match.state != "racing" or match.announced:
            return
        for uid in match.racer_ids:
            if uid in match.results:
                continue
            if uid in self.active:
                continue
            # Never opened — count as a heavy DNF so the match can resolve.
            match.results[uid] = 15 * STAGES
            self.unlock_note(uid, match.course["id"])
        await self._maybe_announce_challenge(match)

    # ================================================================ stage loop

    async def on_stage_pick(self, interaction: discord.Interaction, race: RaceSession, opt: dict):
        if interaction.user.id != race.user_id:
            await interaction.response.send_message("Not your race.", ephemeral=True)
            return
        if race.done or self.active.get(race.user_id) is not race:
            await interaction.response.send_message("This race is over.", ephemeral=True)
            return
        if race.view:
            race.view.stop()

        penalty, flavor = resolve_choice(
            opt, race.speed, race.altitude, race.rng, time_lost=race.penalty
        )
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
        while race.stage_index < STAGES:
            race.penalty += 15
            race.log_lines.append(
                f"Stage {race.stage_index + 1}: timed out → +15s — the course moved on."
            )
            race.stage_index += 1
        race.done = True
        race.timed_out = True
        self.active.pop(race.user_id, None)
        new_note = self.unlock_note(race.user_id, race.course["id"])
        new_best = self.record_best(race.user_id, race.course["id"], race.penalty)

        if race.match:
            race.match.results[race.user_id] = race.penalty
            if race.message is not None:
                try:
                    wait = (
                        "\n\n⏳ Waiting for your opponent…"
                        if len(race.match.results) < 2
                        else ""
                    )
                    note = (
                        f"\n📓 Study note unlocked\n*{race.course['study_note']}*"
                        if new_note
                        else ""
                    )
                    await race.message.edit(
                        embed=discord.Embed(
                            title="🏁 Race timed out",
                            description=(
                                f"**{race.course['name']}** — stages left unflown.\n"
                                f"Time lost · **+{race.penalty}s**\n"
                                f"{note}{wait}"
                            ),
                            color=0x95A5A6,
                        ),
                        view=None,
                    )
                except discord.DiscordException:
                    log.exception("Could not edit timed-out challenge race message")
            await self._maybe_announce_challenge(race.match)
            return

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

        if race.match:
            race.match.results[race.user_id] = race.penalty
            waiting = len(race.match.results) < 2
            embed = self._finish_embed(
                interaction.user, race, new_note, new_best, waiting=waiting
            )
            await interaction.response.edit_message(embed=embed, view=None)
            await self._maybe_announce_challenge(race.match)
            return

        embed = self._finish_embed(interaction.user, race, new_note, new_best)
        await interaction.response.edit_message(embed=embed, view=None)
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

    async def _maybe_announce_challenge(self, match: ChallengeMatch):
        if match.announced or match.state == "cancelled":
            return
        if len(match.results) < 2:
            return
        match.announced = True
        match.state = "done"

        a, b = match.challenger, match.opponent
        pa, pb = match.results[a.id], match.results[b.id]
        if pa < pb:
            winner, winner_id = a, a.id
            line = (
                f"**{a.display_name}** wins with **+{pa}s** "
                f"(vs **{b.display_name}** at **+{pb}s**)."
            )
        elif pb < pa:
            winner, winner_id = b, b.id
            line = (
                f"**{b.display_name}** wins with **+{pb}s** "
                f"(vs **{a.display_name}** at **+{pa}s**)."
            )
        else:
            winner_id = None
            line = (
                f"Dead heat — both **{a.display_name}** and **{b.display_name}** "
                f"at **+{pa}s**."
            )

        self._versus_bump(winner_id, a.id, b.id)

        pts_line = ""
        if winner_id is not None:
            winner = a if winner_id == a.id else b
            outcome = self.try_award_challenge_points(winner)
            if outcome == "awarded":
                left = self.challenge_pts_left(winner.id)
                pts_line = (
                    f"\n\n+**{CHALLENGE_POINTS}** house points to "
                    f"**{winner.display_name}** "
                    f"(point wins left today · **{left}/{CHALLENGE_POINT_CAP}**)."
                )
            elif outcome == "capped":
                pts_line = (
                    f"\n\n**{winner.display_name}** is at today's challenge "
                    f"point cap (**{CHALLENGE_POINT_CAP}** wins) — race counted, "
                    "no points."
                )
            elif outcome == "no_house":
                pts_line = (
                    f"\n\n**{winner.display_name}** needs a house before "
                    "challenge wins can pay points."
                )

        embed = discord.Embed(
            title="🏁 Head-to-head result",
            description=(
                f"**{match.course['name']}**\n*{match.course['blurb']}*\n\n"
                f"{line}{pts_line}"
            ),
            color=RACE_COLOR,
        )
        try:
            if match.message is not None:
                await match.message.edit(embed=embed, view=None)
            else:
                await match.channel.send(embed=embed)
        except discord.DiscordException:
            try:
                await match.channel.send(embed=embed)
            except discord.DiscordException:
                log.exception("Could not announce broom race challenge result")

    # ================================================================ notes

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
        versus = self.state.get("versus", {}).get(str(interaction.user.id))
        if versus:
            footer += (
                f" · H2H {versus.get('wins', 0)}-{versus.get('losses', 0)}"
                f"-{versus.get('ties', 0)}"
            )
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
