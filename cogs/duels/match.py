"""One 1v1 duel and its cast-board views."""

import asyncio
import discord

from cogs.duels.rules import (
    ACCEPT_TIMEOUT,
    BEST_OF_OPTIONS,
    REWARDED_PER_DAY,
    ROUNDS_TO_WIN,
    ROUND_TIMEOUT,
    SPELLS,
    apply_tie_breakers,
    favor_display_spells,
    log,
    max_rounds_for,
    resolve,
    series_to_win_for,
)

from cogs.duels.cog import Duels

class Duel:
    """One duel, held in memory. Duels last a couple of minutes, so they
    aren't saved - a redeploy mid-duel simply ends it."""

    def __init__(self, cog: Duels, channel, challenger, opponent, *, best_of: int = 3):
        self.cog = cog
        self.channel = channel
        self.a = challenger
        self.b = opponent
        self.message = None
        self.best_of = int(best_of) if best_of in BEST_OF_OPTIONS else 3
        # Series of first-to-2 sets. Bo3 = one set (1 win); Bo5/7/9/51 = first to 3/4/5/26.
        self.series_to_win = series_to_win_for(self.best_of)
        self.match_wins = self.series_to_win  # record wins for series champion
        self.max_rounds = max_rounds_for(self.best_of)
        self.set_score = {challenger.id: 0, opponent.id: 0}
        self.score = {challenger.id: 0, opponent.id: 0}  # round score within current set
        self.set_no = 1
        self.picks: dict[int, str] = {}
        self.round = 0  # round within the current set
        self.total_rounds = 0  # across the whole series (tie / timeout budget)
        self.history: list[str] = []
        self.state = "pending"     # pending -> active -> done
        self.lock = asyncio.Lock()
        self.publish_lock = asyncio.Lock()  # one board edit at a time
        self._timer = None
        self.board_gen = 0        # bumps on every planned board publish
        # Private cast boards (broom-race style): spell buttons live under
        # each duelist's ephemeral board, which edits in place each round.
        self.private_boards: dict[int, discord.Message] = {}
        self.opened: set[int] = set()
        # Fixed for this duel so board edits don't reshuffle the joke.
        self.rival_line = self.cog.rivalry_board_line(challenger, opponent)
        # Felix Felicis: one won spell-tie per drinker for this whole match.
        self.luck_tie_used: dict[int, bool] = {}

    # -------------------------------------------------------------- display

    def challenge_embed(self) -> discord.Embed:
        if self.series_to_win <= 1:
            length = (
                "**Best of three** (first to 2). Counts as **1 win**."
            )
        else:
            length = (
                f"**Best of {self.best_of}** — first to **{self.series_to_win}** "
                f"duel wins (each duel is first to 2). Series winner banks "
                f"**{self.match_wins} wins**."
            )
        desc = (
            f"{self.cog._duelist_label(self.a)} challenges "
            f"{self.cog._duelist_label(self.b)}.\n\n"
            f"{length} Spells are chosen in secret and revealed together."
        )
        if self.rival_line:
            desc += f"\n\n⚔️ {self.rival_line}"
        embed = discord.Embed(
            title="A duel is called",
            description=desc,
            color=0xB8434F,
        )
        embed.set_footer(text=f"{self.b.display_name} has {ACCEPT_TIMEOUT // 60} minutes to answer")
        return embed

    def board_embed(self, footer: str = None) -> discord.Embed:
        a_r, b_r = self.score[self.a.id], self.score[self.b.id]
        a_s, b_s = self.set_score[self.a.id], self.set_score[self.b.id]
        if self.series_to_win <= 1:
            lines = [
                f"{self.cog._duelist_label(self.a)} **{a_r}** — "
                f"**{b_r}** {self.cog._duelist_label(self.b)}",
            ]
        else:
            lines = [
                f"Best of {self.best_of} · first to {self.series_to_win} "
                f"(each set first to {ROUNDS_TO_WIN})",
                f"Series: {self.cog._duelist_label(self.a)} **{a_s}** — "
                f"**{b_s}** {self.cog._duelist_label(self.b)}",
                f"This set: **{a_r}** — **{b_r}**",
            ]
        if self.rival_line:
            lines.append(f"⚔️ {self.rival_line}")
        if self.history:
            lines.append("")
            lines.extend(self.history[-4:])
        if self.state == "active":
            waiting = [m.display_name for m in (self.a, self.b) if m.id not in self.picks]
            lines.append("")
            if self.series_to_win > 1:
                round_label = f"**Set {self.set_no} · Round {self.round}**"
            else:
                round_label = f"**Round {self.round}**"
            lines.append(
                f"{round_label} — "
                + ("waiting on " + " and ".join(waiting) if waiting else "revealing…")
            )
        embed = discord.Embed(title="The duel", description="\n".join(lines), color=0xB8434F)
        embed.set_footer(text=footer or (
            f"Both duelists: Open my cast board (again if dismissed) • {ROUND_TIMEOUT}s per round"
            if self.state == "active"
            else f"{ROUND_TIMEOUT}s per round"
        ))
        return embed

    def private_embed(self, user_id: int, footer: str = None) -> discord.Embed:
        """Same scoreboard as public, with a footer aimed at this caster."""
        embed = self.board_embed(footer)
        if footer is not None:
            return embed
        if self.state == "active" and user_id in self.picks:
            spell = SPELLS[self.picks[user_id]]["name"]
            embed.set_footer(text=f"You cast {spell}. Waiting on your opponent…")
        elif self.state == "active":
            embed.set_footer(
                text=f"Pick a spell below • Open again if dismissed • {ROUND_TIMEOUT}s per round"
            )
        return embed

    def _public_view(self):
        if self.state == "active":
            return OpenCastBoardView(self)
        return None

    def _private_view_for(self, user_id: int):
        if self.state != "active":
            return None
        if user_id in self.picks:
            return None
        return PrivateSpellView(self)

    def _snapshot(self, view=None, footer=None):
        """Bump board gen and capture embed+view. Call while holding the lock
        (or when no concurrent publisher can race)."""
        self.board_gen += 1
        if view is None and self.state == "active":
            view = self._public_view()
        return self.board_embed(footer), view, self.board_gen, footer

    async def _publish(self, embed, view, gen, footer=None):
        """Edit the public duel message and any private cast boards if current.

        publish_lock serializes Discord edits so a slow stale write can't land
        *after* a newer board without the newer one getting to write last.
        """
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
                    log.exception("Could not update the duel message.")
            for uid, msg in boards.items():
                try:
                    priv_footer = footer
                    if priv_footer is None and state == "active" and uid in picks:
                        priv_footer = (
                            f"You cast {SPELLS[picks[uid]]['name']}. "
                            "Waiting on your opponent…"
                        )
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
                        priv_view = PrivateSpellView(self, round_no=round_no)
                    await msg.edit(embed=priv_embed, view=priv_view)
                except discord.DiscordException:
                    log.exception("Could not update private cast board for %s", uid)

    async def _update(self, view=None, footer=None):
        """Convenience: snapshot under the lock, then publish unlocked."""
        async with self.lock:
            snap = self._snapshot(view=view, footer=footer)
        await self._publish(*snap)

    # --------------------------------------------------------------- timing

    def _arm(self, coro):
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.create_task(coro)

    def start_accept_timer(self):
        self._arm(self._accept_timeout())

    async def _accept_timeout(self):
        await asyncio.sleep(ACCEPT_TIMEOUT)
        async with self.lock:
            if self.state != "pending":
                return
            self.cog.release_match(self)
        try:
            await self.message.edit(
                content=None,
                embed=discord.Embed(description=f"{self.b.display_name} never answered. "
                                                "The duel is off.", color=0x7A7A7A),
                view=None,
            )
        except discord.DiscordException:
            pass

    async def _round_timeout(self, round_no: int):
        await asyncio.sleep(ROUND_TIMEOUT)
        finish = None
        fizzle = None
        async with self.lock:
            if self.state != "active" or self.round != round_no:
                return
            cast = [m for m in (self.a, self.b) if m.id in self.picks]
            if len(cast) == 1:
                winner = cast[0]
                loser = self.b if winner is self.a else self.a
                self.history.append(f"*{loser.display_name} froze and never cast.*")
                finish = (winner, loser)
            else:
                self.cog.release_match(self)
                fizzle = self._snapshot(
                    view=None, footer="Neither duelist cast. The duel fizzles out.")
        if finish:
            await self._finish(finish[0], finish[1], forfeit=True)
        elif fizzle:
            await self._publish(*fizzle)

    # ------------------------------------------------------------ the rounds

    async def _next_round(self):
        arm_round = None
        async with self.lock:
            self.round += 1
            self.total_rounds += 1
            self.picks = {}
            if self.total_rounds > self.max_rounds:
                self.cog.release_match(self)
                snap = self._snapshot(view=None, footer="Too evenly matched — declared a draw.")
            else:
                snap = self._snapshot(view=OpenCastBoardView(self))
                arm_round = self.round
        await self._publish(*snap)
        if arm_round is not None:
            self._arm(self._round_timeout(arm_round))

    async def open_cast_board(self, interaction: discord.Interaction) -> None:
        """Send a private board with spells under it.

        Always allowed to open again — dismissing an ephemeral used to
        leave fat-fingered duelists stuck until they forfeited the round.
        """
        uid = interaction.user.id
        if uid not in self.score:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        async with self.lock:
            if self.state != "active":
                await interaction.response.send_message(
                    "This duel is over.", ephemeral=True
                )
                return
            self.opened.add(uid)
            embed = self.private_embed(uid)
            view = self._private_view_for(uid)
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )
        try:
            self.private_boards[uid] = await interaction.original_response()
        except discord.HTTPException:
            # Keep opened so publish still knows they participate; they
            # can press Open again for a fresh board.
            pass

    async def cast(self, member, spell: str, round_no: int = None) -> str:
        """Lock in a spell. Returns a message for the caster.

        State changes happen under the lock; Discord edits happen after release
        so a slow board update can't freeze the opponent's cast.
        """
        publish = None
        finish = None
        arm_round = None
        async with self.lock:
            if self.state != "active":
                return "This duel is over."
            if round_no is not None and round_no != self.round:
                return "That round is already over — pick again on your cast board."
            if member.id not in self.score:
                return "You're not in this duel."
            if member.id in self.picks:
                return f"You've already cast {SPELLS[self.picks[member.id]]['name']} this round."
            self.picks[member.id] = spell

            if len(self.picks) < 2:
                publish = self._snapshot(view=OpenCastBoardView(self))
                reply = f"You cast **{SPELLS[spell]['name']}**. Waiting on your opponent…"
            else:
                a_spell, b_spell = self.picks[self.a.id], self.picks[self.b.id]
                disp_a, disp_b = favor_display_spells(self.a.id, self.b.id, a_spell, b_spell)
                result, line = resolve(disp_a, disp_b)
                result, line = apply_tie_breakers(
                    result, line, self.a.id, self.b.id, self.cog.bot, self.luck_tie_used,
                )
                reveal = (f"S{self.set_no}R{self.round}: {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                          f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {line}"
                          if self.series_to_win > 1 else
                          f"R{self.round}: {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                          f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {line}")
                if result == 1:
                    self.score[self.a.id] += 1
                    self.cog.note_round_win(self.a.id, disp_a)
                elif result == 2:
                    self.score[self.b.id] += 1
                    self.cog.note_round_win(self.b.id, disp_b)
                self.history.append(reveal)

                set_winner = None
                if self.score[self.a.id] >= ROUNDS_TO_WIN:
                    set_winner = self.a
                elif self.score[self.b.id] >= ROUNDS_TO_WIN:
                    set_winner = self.b

                if set_winner is not None:
                    self.set_score[set_winner.id] += 1
                    set_loser = self.b if set_winner.id == self.a.id else self.a
                    if self.series_to_win > 1:
                        self.history.append(
                            f"✅ **{set_winner.display_name}** takes set {self.set_no} "
                            f"({self.set_score[self.a.id]}–{self.set_score[self.b.id]})"
                        )
                    if self.set_score[set_winner.id] >= self.series_to_win:
                        finish = (set_winner, set_loser, False)
                    else:
                        # Next set: reset round score, keep going.
                        self.set_no += 1
                        self.score = {self.a.id: 0, self.b.id: 0}
                        self.round = 1
                        self.total_rounds += 1
                        self.picks = {}
                        if self.total_rounds > self.max_rounds:
                            self.cog.release_match(self)
                            publish = self._snapshot(
                                view=None, footer="Too evenly matched — declared a draw.")
                        else:
                            publish = self._snapshot(view=OpenCastBoardView(self))
                            arm_round = self.round
                else:
                    self.round += 1
                    self.total_rounds += 1
                    self.picks = {}
                    if self.total_rounds > self.max_rounds:
                        self.cog.release_match(self)
                        publish = self._snapshot(
                            view=None, footer="Too evenly matched — declared a draw.")
                    else:
                        publish = self._snapshot(view=OpenCastBoardView(self))
                        arm_round = self.round
                reply = f"You cast **{SPELLS[spell]['name']}**."

        if finish:
            await self._finish(finish[0], finish[1], forfeit=finish[2])
        elif publish:
            await self._publish(*publish)
            if arm_round is not None:
                self._arm(self._round_timeout(arm_round))
        return reply

    async def _finish(self, winner, loser, forfeit: bool = False):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self.cog.release_match(self)
        winner_sets = int(self.set_score.get(winner.id, 0) or 0)
        loser_sets = int(self.set_score.get(loser.id, 0) or 0)
        # Normal first-to-2: one set played → 1–0. Series: use the real set tally
        # so a 4–1 best-of-7 banks +4W/+1L for the champ and +1W/+4L for the other.
        if self.series_to_win <= 1:
            winner_sets = max(1, winner_sets)
            loser_sets = 0
        outcome = self.cog.settle(
            winner, loser,
            match_wins=self.match_wins,
            winner_sets=winner_sets,
            loser_sets=loser_sets,
        )

        from cogs.store import HOUSES
        if outcome.get("flourish"):
            self.history.append(f"*{outcome['flourish']}*")

        def _points_bit(member, awarded, reason, house_key) -> str:
            if awarded and house_key and house_key in HOUSES:
                h = HOUSES[house_key]
                bit = f"+{awarded} to {h['emoji']} {h['name']}"
                if outcome.get("night"):
                    bit += " (Duel Night!)"
                return bit
            if reason == "same-house":
                return "Housemates — no points change hands"
            if reason == "daily-cap":
                return (
                    f"{member.display_name} has taken today's "
                    f"{REWARDED_PER_DAY} duel points already"
                )
            if reason == "no-house":
                return f"{member.display_name}: no house to credit"
            return ""

        bits = [
            _points_bit(
                winner,
                outcome.get("awarded") or 0,
                outcome.get("reason"),
                outcome.get("house"),
            )
        ]
        # Best-of series: mention the loser's set-win payout / cap separately.
        if (outcome.get("loser_sets") or 0) > 0 and self.match_wins > 1:
            loser_bit = _points_bit(
                loser,
                outcome.get("loser_awarded") or 0,
                outcome.get("loser_reason"),
                outcome.get("loser_house"),
            )
            if loser_bit and loser_bit != bits[0]:
                # Same-house already said once; otherwise append set credit.
                if outcome.get("loser_reason") != "same-house":
                    if outcome.get("loser_awarded"):
                        loser_bit = (
                            f"{loser.display_name}'s sets: {loser_bit}"
                        )
                    bits.append(loser_bit)
        bits = [b for b in bits if b]
        tail = " • ".join(bits) if bits else "No house to credit"
        if forfeit:
            verb = "wins by forfeit"
        elif self.match_wins > 1:
            w_sets = outcome.get("winner_sets", self.match_wins)
            verb = f"wins the series (+{w_sets} wins)"
        else:
            verb = "wins the duel"
        async with self.lock:
            snap = self._snapshot(view=None, footer=f"{winner.display_name} {verb} • {tail}")
        await self._publish(*snap)
        notes = outcome.get("notes") or []
        if notes and self.channel is not None:
            try:
                await self.channel.send("\n".join(notes))
            except discord.DiscordException:
                log.exception("Could not post duel announcements.")


# ------------------------------------------------------------------ the views

class AcceptView(discord.ui.View):
    def __init__(self, duel: Duel):
        super().__init__(timeout=ACCEPT_TIMEOUT + 5)
        self.duel = duel

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.duel
        if interaction.user.id != d.b.id:
            await interaction.response.send_message("This challenge isn't yours to answer.",
                                                    ephemeral=True)
            return
        async with d.lock:
            if d.state != "pending":
                await interaction.response.send_message("Too late for that.", ephemeral=True)
                return
            d.state = "active"
        await interaction.response.defer()
        d.round = 0
        await d._next_round()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.duel
        if interaction.user.id not in (d.a.id, d.b.id):
            await interaction.response.send_message("This isn't your duel.", ephemeral=True)
            return
        async with d.lock:
            if d.state != "pending":
                await interaction.response.send_message("Too late for that.", ephemeral=True)
                return
            d.cog.release_match(d)
        who = "withdraws" if interaction.user.id == d.a.id else "declines"
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(description=f"{interaction.user.display_name} {who}. "
                                            "No duel today.", color=0x7A7A7A),
            view=None,
        )


class OpenCastBoardView(discord.ui.View):
    """Public prompt — same idea as broom race's Open my race board."""

    def __init__(self, duel: Duel):
        super().__init__(timeout=ROUND_TIMEOUT * duel.max_rounds + 60)
        self.duel = duel

    @discord.ui.button(
        label="Open my cast board",
        style=discord.ButtonStyle.primary,
        emoji="\U0001FA84",
    )
    async def open_board(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.duel.open_cast_board(interaction)


class PrivateSpellView(discord.ui.View):
    """Spell buttons attached under the private cast board (edits in place)."""

    def __init__(self, duel: Duel, round_no: int | None = None):
        super().__init__(timeout=ROUND_TIMEOUT + 5)
        self.duel = duel
        self.round_no = duel.round if round_no is None else round_no
        for i, (key, spell) in enumerate(SPELLS.items()):
            self.add_item(SpellButton(key, spell, row=0 if i < 3 else 1))


class SpellButton(discord.ui.Button):
    def __init__(self, key: str, spell: dict, row: int = 0):
        super().__init__(
            label=spell["name"],
            emoji=spell["emoji"],
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        # Discord allows 3 seconds to answer a click; resolving a round also
        # edits boards, so acknowledge first. Success refreshes this private
        # board in place (options stay under the duel); errors get a followup.
        d = self.view.duel
        if interaction.user.id not in d.score:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        await interaction.response.defer()
        before = d.board_gen
        reply = await d.cast(interaction.user, self.key, self.view.round_no)
        if d.board_gen == before:
            try:
                await interaction.followup.send(reply, ephemeral=True)
            except discord.DiscordException:
                pass


# ==================================================================== Trio 3v3
