"""3v3 trio signup and the live trio match."""

import asyncio
import random
import discord

from cogs.duels.rules import (
    MAX_ROUNDS,
    ROUND_TIMEOUT,
    SPELLS,
    TRIO_HOUSE_WIN_GOAL,
    TRIO_ROUNDS_TO_WIN,
    TRIO_SIGNUP_TIMEOUT,
    TRIO_SIZE,
    apply_tie_breakers,
    favor_display_spells,
    log,
    resolve,
)

from cogs.duels.cog import Duels

class TrioSignup:
    def __init__(self, cog: Duels, is_house: bool, house_a: str = None, house_b: str = None):
        self.cog = cog
        self.is_house = is_house
        self.house_a = house_a
        self.house_b = house_b
        self.team_a: list = []   # members
        self.team_b: list = []
        self.started = False
        self.cancelled = False
        self.message = None
        self.timer_task = None

    def _side_name(self, side: str) -> str:
        from cogs.store import HOUSES
        if self.is_house:
            key = self.house_a if side == "a" else self.house_b
            return HOUSES[key]["name"]
        return "Team A" if side == "a" else "Team B"

    def embed(self) -> discord.Embed:
        from cogs.store import HOUSES
        if self.is_house:
            title = (f"⚔️ Trio: {HOUSES[self.house_a]['emoji']} {HOUSES[self.house_a]['name']} vs "
                     f"{HOUSES[self.house_b]['emoji']} {HOUSES[self.house_b]['name']}")
        else:
            title = "⚔️ Trio Scramble (3v3)"
        e = discord.Embed(title=title, description="Join a side. Best of three rounds once both are full.",
                          color=0xB8434F)
        for side, team in (("a", self.team_a), ("b", self.team_b)):
            e.add_field(
                name=f"{self._side_name(side)} ({len(team)}/{TRIO_SIZE})",
                value="\n".join(m.display_name for m in team) or "—",
                inline=True,
            )
        e.set_footer(text=f"Signup closes in {TRIO_SIGNUP_TIMEOUT}s")
        return e

    async def _timeout(self):
        try:
            await asyncio.sleep(TRIO_SIGNUP_TIMEOUT)
        except asyncio.CancelledError:
            return
        if self.started or self.cancelled or self.message is None:
            return
        self.cancelled = True
        for m in self.team_a + self.team_b:
            self.cog.busy.discard(m.id)
        try:
            await self.message.edit(
                embed=discord.Embed(
                    title="Trio signup expired",
                    description="Not enough duelists joined in time.",
                    color=0x7A7A7A,
                ),
                view=None,
            )
        except discord.DiscordException:
            pass

    async def join(self, interaction: discord.Interaction, side: str):
        if self.cancelled or self.started:
            await interaction.response.send_message("This signup is closed.", ephemeral=True)
            return
        uid = interaction.user.id
        already = any(m.id == uid for m in self.team_a + self.team_b)
        if uid in self.cog.busy and not already:
            await interaction.response.send_message("You're already in a duel.", ephemeral=True)
            return
        if already:
            await interaction.response.send_message("You're already signed up.", ephemeral=True)
            return
        target = self.team_a if side == "a" else self.team_b
        if len(target) >= TRIO_SIZE:
            await interaction.response.send_message("That side is full.", ephemeral=True)
            return
        if self.is_house:
            store = self.cog.bot.get_cog("Store")
            required = self.house_a if side == "a" else self.house_b
            member_house = store.member_house(interaction.user) if store else None
            if member_house != required:
                from cogs.store import HOUSES
                await interaction.response.send_message(
                    f"Only members of House {HOUSES[required]['name']} can join this side.",
                    ephemeral=True,
                )
                return
        target.append(interaction.user)
        self.cog.busy.add(uid)

        if len(self.team_a) >= TRIO_SIZE and len(self.team_b) >= TRIO_SIZE:
            self.started = True
            if self.timer_task:
                self.timer_task.cancel()
            match = TrioMatch(self)
            match.message = interaction.message
            match.round = 1
            await interaction.response.edit_message(
                embed=match.board_embed(), view=OpenTrioCastBoardView(match)
            )
            match._arm(match._round_timeout(1))
        else:
            await interaction.response.edit_message(embed=self.embed(), view=TrioSignupView(self))


class TrioJoinButton(discord.ui.Button):
    def __init__(self, side: str, label: str):
        super().__init__(label=label,
                         style=discord.ButtonStyle.success if side == "a" else discord.ButtonStyle.primary)
        self.side = side

    async def callback(self, interaction: discord.Interaction):
        await self.view.signup.join(interaction, self.side)


class TrioSignupView(discord.ui.View):
    def __init__(self, signup: TrioSignup):
        super().__init__(timeout=TRIO_SIGNUP_TIMEOUT + 10)
        self.signup = signup
        from cogs.store import HOUSES
        if signup.is_house:
            a = f"Join {HOUSES[signup.house_a]['name']}"
            b = f"Join {HOUSES[signup.house_b]['name']}"
        else:
            a, b = "Join Team A", "Join Team B"
        self.add_item(TrioJoinButton("a", a))
        self.add_item(TrioJoinButton("b", b))


class TrioMatch:
    def __init__(self, signup: TrioSignup):
        self.cog = signup.cog
        self.is_house = signup.is_house
        self.house_a = signup.house_a
        self.house_b = signup.house_b
        self.team_a = list(signup.team_a)
        self.team_b = list(signup.team_b)
        # Fixed random pairings for the whole match.
        a = list(self.team_a)
        b = list(self.team_b)
        random.shuffle(a)
        random.shuffle(b)
        self.pairings = list(zip(a, b))
        self.score_a = 0
        self.score_b = 0
        self.round = 0
        self.picks: dict[int, str] = {}
        self.history: list[str] = []
        self.state = "active"
        self.lock = asyncio.Lock()
        self.publish_lock = asyncio.Lock()
        self.message = None
        self._timer = None
        self.board_gen = 0
        self.channel = signup.message.channel if signup.message else None
        self.private_boards: dict[int, discord.Message] = {}
        self.opened: set[int] = set()
        self.luck_tie_used: dict[int, bool] = {}

    def all_players(self):
        return self.team_a + self.team_b

    def side_name(self, side: str) -> str:
        from cogs.store import HOUSES
        if self.is_house:
            key = self.house_a if side == "a" else self.house_b
            return HOUSES[key]["name"]
        return "Team A" if side == "a" else "Team B"

    def board_embed(self, footer: str = None) -> discord.Embed:
        lines = [f"**{self.side_name('a')}** {self.score_a} — {self.score_b} **{self.side_name('b')}**"]
        lines.append("Pairings: " + ", ".join(
            f"{a.display_name} vs {b.display_name}" for a, b in self.pairings
        ))
        if self.history:
            lines.append("")
            lines.extend(self.history[-5:])
        if self.state == "active":
            waiting = [m.display_name for m in self.all_players() if m.id not in self.picks]
            lines.append("")
            lines.append(f"**Round {self.round}** — "
                         + ("waiting on " + ", ".join(waiting) if waiting else "revealing…"))
        embed = discord.Embed(title="Trio Duel", description="\n".join(lines), color=0xB8434F)
        embed.set_footer(text=footer or (
            f"Duelists: Open my cast board (again if dismissed) • {ROUND_TIMEOUT}s per round"
            if self.state == "active"
            else f"{ROUND_TIMEOUT}s per round"
        ))
        return embed

    def private_embed(self, user_id: int, footer: str = None) -> discord.Embed:
        embed = self.board_embed(footer)
        if footer is not None:
            return embed
        pick = self.picks.get(user_id)
        if self.state == "active" and pick:
            embed.set_footer(text=f"You cast {SPELLS[pick]['name']}. Waiting…")
        elif self.state == "active":
            embed.set_footer(
                text=f"Pick a spell below • Open again if dismissed • {ROUND_TIMEOUT}s per round"
            )
        return embed

    def start_round(self):
        self.round += 1
        self.picks = {}
        self._arm(self._round_timeout(self.round))

    def _arm(self, coro):
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.create_task(coro)

    def _snapshot(self, view=None, footer=None):
        self.board_gen += 1
        if view is None and self.state == "active":
            view = OpenTrioCastBoardView(self)
        return self.board_embed(footer), view, self.board_gen, footer

    async def _publish(self, embed, view, gen, footer=None):
        if self.message is None and not self.private_boards:
            return
        async with self.publish_lock:
            async with self.lock:
                if gen != self.board_gen:
                    return
                picks = dict(self.picks)
                state = self.state
                boards = dict(self.private_boards)
                round_no = self.round
            if self.message is not None:
                try:
                    await self.message.edit(content=None, embed=embed, view=view)
                except discord.DiscordException:
                    log.exception("Could not update trio message.")
            for uid, msg in boards.items():
                try:
                    priv_footer = footer
                    pick = picks.get(uid)
                    if priv_footer is None and state == "active" and pick:
                        priv_footer = f"You cast {SPELLS[pick]['name']}. Waiting…"
                    elif priv_footer is None and state == "active":
                        priv_footer = (
                            f"Pick a spell below • Open again if dismissed "
                            f"• {ROUND_TIMEOUT}s per round"
                        )
                    priv_embed = discord.Embed(
                        title=embed.title,
                        description=embed.description,
                        color=embed.color,
                    )
                    if priv_footer:
                        priv_embed.set_footer(text=priv_footer)
                    elif embed.footer and embed.footer.text:
                        priv_embed.set_footer(text=embed.footer.text)
                    priv_view = None
                    if state == "active" and uid not in picks:
                        priv_view = TrioPrivateSpellView(self, round_no=round_no)
                    await msg.edit(embed=priv_embed, view=priv_view)
                except discord.DiscordException:
                    log.exception("Could not update trio private cast board for %s", uid)

    async def _update(self, view=None, footer=None):
        async with self.lock:
            snap = self._snapshot(view=view, footer=footer)
        await self._publish(*snap)

    async def open_cast_board(self, interaction: discord.Interaction) -> None:
        """Send/reopen private board — dismiss is recoverable (no forfeit trap)."""
        uid = interaction.user.id
        ids = {p.id for p in self.all_players()}
        if uid not in ids:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        async with self.lock:
            if self.state != "active":
                await interaction.response.send_message(
                    "This trio is over.", ephemeral=True
                )
                return
            self.opened.add(uid)
            embed = self.private_embed(uid)
            view = (
                None
                if uid in self.picks
                else TrioPrivateSpellView(self, round_no=self.round)
            )
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )
        try:
            self.private_boards[uid] = await interaction.original_response()
        except discord.HTTPException:
            pass

    async def _round_timeout(self, round_no: int):
        await asyncio.sleep(ROUND_TIMEOUT)
        resolve = False
        fizzle = None
        async with self.lock:
            if self.state != "active" or self.round != round_no:
                return
            # Non-casters forfeit their pairing.
            for a, b in self.pairings:
                if a.id not in self.picks and b.id in self.picks:
                    self.picks[a.id] = None  # marker: auto-loss
                elif b.id not in self.picks and a.id in self.picks:
                    self.picks[b.id] = None
            if len([m for m in self.all_players() if m.id in self.picks]) < 2:
                self.state = "done"
                for m in self.all_players():
                    self.cog.busy.discard(m.id)
                fizzle = self._snapshot(view=None, footer="Too few casts — the trio fizzles out.")
            else:
                resolve = True
                follow = self._resolve_round_locked()
        if fizzle:
            await self._publish(*fizzle)
            return
        if resolve:
            await self._apply_trio_followup(follow)

    async def cast(self, member, spell: str, round_no: int = None) -> str:
        publish = None
        follow = None
        async with self.lock:
            if self.state != "active":
                return "This trio is over."
            if round_no is not None and round_no != self.round:
                return "That round is already over — pick again on your cast board."
            ids = {m.id for m in self.all_players()}
            if member.id not in ids:
                return "You're not in this trio."
            if member.id in self.picks:
                prev = self.picks[member.id]
                if prev:
                    return f"You've already cast {SPELLS[prev]['name']} this round."
                return "You've already cast this round."
            self.picks[member.id] = spell
            if len(self.picks) < len(self.all_players()):
                publish = self._snapshot(view=OpenTrioCastBoardView(self))
            else:
                follow = self._resolve_round_locked()
            reply = (
                f"You cast **{SPELLS[spell]['name']}**. Waiting on your team…"
                if publish else f"You cast **{SPELLS[spell]['name']}**."
            )
        if publish:
            await self._publish(*publish)
        elif follow:
            await self._apply_trio_followup(follow)
        return reply

    def _resolve_round_locked(self) -> dict:
        """Score the round under the lock; return Discord work for outside."""
        if self._timer:
            self._timer.cancel()
        a_pair_wins = 0
        b_pair_wins = 0
        bits = []
        for pa, pb in self.pairings:
            sa, sb = self.picks.get(pa.id), self.picks.get(pb.id)
            if sa is None and sb is None:
                bits.append(f"{pa.display_name} & {pb.display_name} both froze.")
                continue
            if sa is None:
                b_pair_wins += 1
                bits.append(f"{pa.display_name} froze — point to {pb.display_name}.")
                continue
            if sb is None:
                a_pair_wins += 1
                bits.append(f"{pb.display_name} froze — point to {pa.display_name}.")
                continue
            disp_a, disp_b = favor_display_spells(pa.id, pb.id, sa, sb)
            result, line = resolve(disp_a, disp_b)
            result, line = apply_tie_breakers(
                result, line, pa.id, pb.id, self.cog.bot, self.luck_tie_used,
            )
            bits.append(
                f"{SPELLS[disp_a]['emoji']} {pa.display_name} vs {SPELLS[disp_b]['emoji']} "
                f"{pb.display_name} — {line}"
            )
            if result == 1:
                a_pair_wins += 1
            elif result == 2:
                b_pair_wins += 1
        self.history.append(f"**R{self.round}:** " + " | ".join(bits))
        if a_pair_wins > b_pair_wins:
            self.score_a += 1
            self.history.append(f"→ **{self.side_name('a')}** takes the round ({a_pair_wins}-{b_pair_wins}).")
        elif b_pair_wins > a_pair_wins:
            self.score_b += 1
            self.history.append(f"→ **{self.side_name('b')}** takes the round ({b_pair_wins}-{a_pair_wins}).")
        else:
            self.history.append(f"→ Round tied ({a_pair_wins}-{b_pair_wins}). Replaying.")

        if self.score_a >= TRIO_ROUNDS_TO_WIN:
            return {"finish": (self.team_a, self.team_b)}
        if self.score_b >= TRIO_ROUNDS_TO_WIN:
            return {"finish": (self.team_b, self.team_a)}
        if self.round >= MAX_ROUNDS:
            self.state = "done"
            for m in self.all_players():
                self.cog.busy.discard(m.id)
            return {
                "publish": self._snapshot(view=None, footer="Too evenly matched — declared a draw."),
            }
        self.round += 1
        self.picks = {}
        arm = self.round
        return {
            "publish": self._snapshot(view=OpenTrioCastBoardView(self)),
            "arm": arm,
        }

    async def _apply_trio_followup(self, follow: dict):
        if follow.get("finish"):
            await self._finish(follow["finish"][0], follow["finish"][1])
            return
        if follow.get("publish"):
            await self._publish(*follow["publish"])
        if follow.get("arm") is not None:
            self._arm(self._round_timeout(follow["arm"]))

    async def _finish(self, winners, losers):
        self.state = "done"
        if self._timer:
            self._timer.cancel()
        for m in self.all_players():
            self.cog.busy.discard(m.id)
        outcome = self.cog.settle_trio(winners, losers)
        from cogs.store import HOUSES
        win_ids = {m.id for m in winners}
        win_side = "a" if win_ids == {m.id for m in self.team_a} else "b"
        h = HOUSES.get(outcome.get("house") or "", {})
        h_label = f"{h.get('emoji', '')} {h.get('name', '')}".strip() or "the house"
        hw = int(outcome.get("house_wins") or 0)
        goal = int(outcome.get("house_goal") or TRIO_HOUSE_WIN_GOAL)
        if outcome["awarded"]:
            tail = (
                f"+{outcome['awarded']} to {h_label} — "
                f"house trio set complete ({goal}/{goal})"
            )
        elif outcome.get("same_house"):
            tail = "Same-house teams — no points"
        elif outcome.get("house"):
            if outcome.get("house_bonus"):
                tail = f"{h_label} trio set already claimed today ({goal}/{goal})"
            else:
                tail = f"{h_label} trio wins today · {hw}/{goal} (+5 at {goal})"
        else:
            tail = "No unanimous house — no house points"
        async with self.lock:
            snap = self._snapshot(
                view=None,
                footer=f"{self.side_name(win_side)} wins the trio • {tail}",
            )
        await self._publish(*snap)
        notes = outcome.get("notes") or []
        if notes and self.channel is not None:
            try:
                await self.channel.send("\n".join(notes))
            except discord.DiscordException:
                log.exception("Could not post trio announcements.")


class OpenTrioCastBoardView(discord.ui.View):
    def __init__(self, match: TrioMatch):
        super().__init__(timeout=ROUND_TIMEOUT * MAX_ROUNDS + 60)
        self.match = match

    @discord.ui.button(
        label="Open my cast board",
        style=discord.ButtonStyle.primary,
        emoji="\U0001FA84",
    )
    async def open_board(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.match.open_cast_board(interaction)


class TrioPrivateSpellView(discord.ui.View):
    def __init__(self, match: TrioMatch, round_no: int | None = None):
        super().__init__(timeout=ROUND_TIMEOUT + 5)
        self.match = match
        self.round_no = match.round if round_no is None else round_no
        for i, (key, spell) in enumerate(SPELLS.items()):
            self.add_item(TrioSpellButton(key, spell, row=0 if i < 3 else 1))


class TrioSpellButton(discord.ui.Button):
    def __init__(self, key: str, spell: dict, row: int = 0):
        super().__init__(
            label=spell["name"],
            emoji=spell["emoji"],
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        m = self.view.match
        ids = {p.id for p in m.all_players()}
        if interaction.user.id not in ids:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        await interaction.response.defer()
        before = m.board_gen
        reply = await m.cast(interaction.user, self.key, self.view.round_no)
        if m.board_gen == before:
            try:
                await interaction.followup.send(reply, ephemeral=True)
            except discord.DiscordException:
                pass


# ==================================================================== Grand Duel
