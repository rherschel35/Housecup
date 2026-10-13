"""Grand Duel sequences and sudden-death views."""

import asyncio
import random
import discord

from cogs.duels.rules import (
    ACCEPT_TIMEOUT,
    GRAND_LOCK_TIMEOUT,
    GRAND_PLAY_DELAY,
    GRAND_SLOTS,
    GRAND_THEATRE,
    GRAND_TO_WIN,
    ROUND_TIMEOUT,
    SPELLS,
    apply_tie_breakers,
    favor_display_spells,
    log,
    resolve,
)

from cogs.duels.cog import Duels

class GrandDuel:
    def __init__(self, cog: Duels, channel, challenger, opponent):
        self.cog = cog
        self.channel = channel
        self.a = challenger
        self.b = opponent
        self.message = None
        self.state = "pending"   # pending -> locking -> playing -> sudden -> done
        self.sequences: dict[int, list] = {challenger.id: [], opponent.id: []}
        self.locked: set[int] = set()
        self.score = {challenger.id: 0, opponent.id: 0}
        self.round = 0
        self.history: list[str] = []
        self.sudden_picks: dict[int, str] = {}
        self.lock = asyncio.Lock()
        self.publish_lock = asyncio.Lock()
        self._timer = None
        self._sequence_task: asyncio.Task | None = None
        self.board_gen = 0
        self.private_boards: dict[int, discord.Message] = {}
        self.opened: set[int] = set()
        self.rival_line = self.cog.grand_rivalry_board_line(challenger, opponent)
        self.luck_tie_used: dict[int, bool] = {}

    def challenge_embed(self) -> discord.Embed:
        desc = (
            f"**{self.a.display_name}** challenges **{self.b.display_name}** to a Grand Duel.\n\n"
            f"Both lock a blind sequence of **{GRAND_SLOTS}** spells. First to **{GRAND_TO_WIN}** "
            f"round wins. Same spell = neither scores. A tie after 10 goes to live sudden death."
        )
        if self.rival_line:
            desc += f"\n\n⚔️ {self.rival_line}"
        embed = discord.Embed(
            title="A Grand Duel is called",
            description=desc,
            color=0xD4A017,
        )
        embed.set_footer(text=f"{self.b.display_name} has {ACCEPT_TIMEOUT // 60} minutes to answer")
        return embed

    def board_embed(self, footer: str = None) -> discord.Embed:
        a_s, b_s = self.score[self.a.id], self.score[self.b.id]
        lines = [f"**{self.a.display_name}** {a_s} — {b_s} **{self.b.display_name}**"]
        if self.rival_line:
            lines.append(f"⚔️ {self.rival_line}")
        if self.state == "locking":
            for m in (self.a, self.b):
                status = "sequence locked ✓" if m.id in self.locked else "building their sequence…"
                lines.append(f"• {m.display_name}: {status}")
        if self.history:
            lines.append("")
            lines.extend(self.history[-6:])
        title = "Grand Duel" + (" — Sudden Death" if self.state == "sudden" else "")
        embed = discord.Embed(title=title, description="\n".join(lines), color=0xD4A017)
        embed.set_footer(text=footer or "The Circle is watching.")
        return embed

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
                                                "The Grand Duel is off.", color=0x7A7A7A),
                view=None,
            )
        except discord.DiscordException:
            pass

    async def _locking_timeout(self):
        await asyncio.sleep(GRAND_LOCK_TIMEOUT)
        async with self.lock:
            if self.state != "locking":
                return
            self.cog.release_match(self)
            snap = self._snapshot(
                view=None,
                footer="Sequences were never locked in time. The Grand Duel dissolves.",
            )
        await self._publish(*snap)

    def _snapshot(self, view=None, footer=None):
        self.board_gen += 1
        return self.board_embed(footer), view, self.board_gen, footer

    async def _publish(self, embed, view, gen, footer=None):
        if self.message is None and not self.private_boards:
            return
        async with self.publish_lock:
            async with self.lock:
                if gen != self.board_gen:
                    return
                state = self.state
                sudden = dict(self.sudden_picks)
                boards = dict(self.private_boards)
            if self.message is not None:
                try:
                    await self.message.edit(content=None, embed=embed, view=view)
                except discord.DiscordException:
                    log.exception("Could not update Grand Duel message.")
            # Private boards are only used in sudden death (sequence builder
            # is its own ephemeral opened from the lock prompt).
            if state == "sudden" or state == "done":
                for uid, msg in boards.items():
                    try:
                        priv_footer = footer
                        pick = sudden.get(uid)
                        if priv_footer is None and state == "sudden" and pick:
                            priv_footer = (
                                f"You cast {SPELLS[pick]['name']}. Waiting…"
                            )
                        elif priv_footer is None and state == "sudden":
                            priv_footer = "Sudden death — pick a spell below."
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
                        if state == "sudden" and uid not in sudden:
                            priv_view = GrandSuddenPrivateSpellView(self)
                        await msg.edit(embed=priv_embed, view=priv_view)
                    except discord.DiscordException:
                        log.exception(
                            "Could not update Grand sudden board for %s", uid
                        )

    async def _update(self, view=None, footer=None):
        async with self.lock:
            snap = self._snapshot(view=view, footer=footer)
        await self._publish(*snap)

    async def begin_locking(self):
        self.state = "locking"
        self._arm(self._locking_timeout())
        await self._update(
            view=GrandLockPromptView(self),
            footer=(
                f"Both duelists: Open my sequence board "
                f"({GRAND_LOCK_TIMEOUT // 60} min)."
            ),
        )

    async def open_sudden_board(self, interaction: discord.Interaction) -> None:
        """Send/reopen sudden-death board — dismiss is recoverable."""
        uid = interaction.user.id
        if uid not in self.score:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        async with self.lock:
            if self.state != "sudden":
                await interaction.response.send_message(
                    "Sudden death isn't open.", ephemeral=True
                )
                return
            self.opened.add(uid)
            embed = self.board_embed()
            if uid in self.sudden_picks:
                embed.set_footer(
                    text=(
                        f"You cast {SPELLS[self.sudden_picks[uid]]['name']}. "
                        "Waiting…"
                    )
                )
                view = None
            else:
                embed.set_footer(text="Sudden death — pick a spell below.")
                view = GrandSuddenPrivateSpellView(self)
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )
        try:
            self.private_boards[uid] = await interaction.original_response()
        except discord.HTTPException:
            pass

    def try_lock(self, user_id: int, sequence: list[str]) -> str:
        if self.state != "locking":
            return "Locking is closed."
        if user_id not in self.sequences:
            return "You're not in this Grand Duel."
        if user_id in self.locked:
            return "You've already locked your sequence."
        if len(sequence) != GRAND_SLOTS or any(s not in SPELLS for s in sequence):
            return f"You need exactly {GRAND_SLOTS} spells."
        self.sequences[user_id] = list(sequence)
        self.locked.add(user_id)
        return "locked"

    async def maybe_start_play(self):
        if len(self.locked) < 2:
            await self._update(view=GrandLockPromptView(self))
            return
        # Only one sequence playback — both submit callbacks can race here.
        if self.state == "playing" or (
            self._sequence_task is not None and not self._sequence_task.done()
        ):
            return
        self.state = "playing"
        if self._timer:
            self._timer.cancel()
            self._timer = None
        await self._update(view=None, footer="Sequences locked. The Circle begins…")
        # Keep a strong reference — the loop only weakly refs tasks; without
        # this, GC can cancel mid-playback and skip sudden death after a tie.
        self._sequence_task = asyncio.create_task(
            self._play_sequence(),
            name=f"grand-sequence-{self.a.id}-{self.b.id}",
        )

    async def _play_sequence(self):
        try:
            await self._play_sequence_inner()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Grand Duel sequence crashed (%s vs %s)",
                self.a.id, self.b.id,
            )
            try:
                async with self.lock:
                    if self.state not in ("done", "sudden"):
                        self.cog.release_match(self)
                        snap = self._snapshot(
                            view=None,
                            footer="The Grand sequence broke — duel dissolved. Call /grand again.",
                        )
                    else:
                        snap = None
                if snap:
                    await self._publish(*snap)
            except Exception:
                log.exception("Could not recover Grand Duel after sequence crash.")

    async def _play_sequence_inner(self):
        seq_a = self.sequences[self.a.id]
        seq_b = self.sequences[self.b.id]
        for i in range(GRAND_SLOTS):
            await asyncio.sleep(GRAND_PLAY_DELAY)
            async with self.lock:
                if self.state != "playing":
                    return
                self.round = i + 1
                sa, sb = seq_a[i], seq_b[i]
                disp_a, disp_b = favor_display_spells(self.a.id, self.b.id, sa, sb)
                line = random.choice(GRAND_THEATRE)
                result, detail = resolve(disp_a, disp_b)
                result, detail = apply_tie_breakers(
                    result, detail, self.a.id, self.b.id, self.cog.bot, self.luck_tie_used,
                )
                who = ""
                if result == 1:
                    self.score[self.a.id] += 1
                    who = f"**{self.a.display_name}** scores."
                elif result == 2:
                    self.score[self.b.id] += 1
                    who = f"**{self.b.display_name}** scores."
                else:
                    who = "Neither scores."
                self.history.append(
                    f"*{line}*\n"
                    f"**Step {self.round}:** {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                    f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {detail} {who}"
                )
                snap = self._snapshot(view=None)
            await self._publish(*snap)

        finish = None
        sudden = None
        arm_sudden = None
        async with self.lock:
            if self.state != "playing":
                return
            a_s, b_s = self.score[self.a.id], self.score[self.b.id]
            if a_s > b_s and a_s >= GRAND_TO_WIN:
                finish = (self.a, self.b)
            elif b_s > a_s and b_s >= GRAND_TO_WIN:
                finish = (self.b, self.a)
            elif a_s == b_s:
                self.state = "sudden"
                self.sudden_picks = {}
                self.history.append(
                    f"**{a_s}–{b_s}.** Sudden death. One spell each, until someone lands a hit."
                )
                # Fresh private boards for sudden death (sequence boards are done).
                self.private_boards = {}
                self.opened = set()
                sudden_view = OpenGrandSuddenBoardView(self)
                sudden = self._snapshot(
                    view=sudden_view,
                    footer="Sudden death — Open my cast board (again if dismissed).",
                )
                arm_sudden = self.round
            elif a_s > b_s:
                finish = (self.a, self.b)
            else:
                finish = (self.b, self.a)
        if finish:
            await self._finish(finish[0], finish[1])
            return
        if sudden:
            await self._publish(*sudden)
            # Belt-and-suspenders: if the board edit was dropped, post a fresh one.
            if self.state == "sudden" and self.channel is not None:
                embed, view, _, _ = sudden
                try:
                    if self.message is not None:
                        await self.message.edit(content=None, embed=embed, view=view)
                    else:
                        self.message = await self.channel.send(embed=embed, view=view)
                except discord.DiscordException:
                    log.exception("Grand sudden-death board edit failed; posting fallback.")
                    try:
                        self.message = await self.channel.send(embed=embed, view=view)
                    except discord.DiscordException:
                        log.exception("Could not post fallback Grand sudden-death board.")
                try:
                    await self.channel.send(
                        f"⚡ **Sudden death!** "
                        f"**{self.a.display_name}** vs **{self.b.display_name}** — "
                        f"open your cast board on the Grand Duel message."
                    )
                except discord.DiscordException:
                    log.exception("Could not announce Grand sudden death.")
            self._arm(self._sudden_timeout(arm_sudden))

    async def _sudden_timeout(self, marker: int):
        await asyncio.sleep(ROUND_TIMEOUT)
        finish = None
        dissolve = None
        async with self.lock:
            if self.state != "sudden" or self.round != marker:
                return
            cast = [m for m in (self.a, self.b) if m.id in self.sudden_picks]
            if len(cast) == 1:
                winner = cast[0]
                loser = self.b if winner is self.a else self.a
                self.history.append(f"*{loser.display_name} froze in sudden death.*")
                finish = (winner, loser)
            else:
                self.cog.release_match(self)
                dissolve = self._snapshot(
                    view=None, footer="Neither cast. The Grand Duel dissolves.")
        if finish:
            await self._finish(finish[0], finish[1])
        elif dissolve:
            await self._publish(*dissolve)

    async def sudden_cast(self, member, spell: str) -> str:
        publish = None
        finish = None
        arm_round = None
        async with self.lock:
            if self.state != "sudden":
                return "Sudden death isn't running."
            if member.id not in self.score:
                return "You're not in this Grand Duel."
            if member.id in self.sudden_picks:
                return f"You've already cast {SPELLS[self.sudden_picks[member.id]]['name']}."
            self.sudden_picks[member.id] = spell
            if len(self.sudden_picks) < 2:
                publish = self._snapshot(view=OpenGrandSuddenBoardView(self))
                reply = f"You cast **{SPELLS[spell]['name']}**. Waiting…"
            else:
                sa, sb = self.sudden_picks[self.a.id], self.sudden_picks[self.b.id]
                self.round += 1
                line = random.choice(GRAND_THEATRE)
                disp_a, disp_b = favor_display_spells(self.a.id, self.b.id, sa, sb)
                result, detail = resolve(disp_a, disp_b)
                result, detail = apply_tie_breakers(
                    result, detail, self.a.id, self.b.id, self.cog.bot, self.luck_tie_used,
                )
                self.sudden_picks = {}
                if result == 0:
                    self.history.append(
                        f"*{line}*\n"
                        f"**Sudden {self.round}:** {SPELLS[disp_a]['emoji']} vs "
                        f"{SPELLS[disp_b]['emoji']} — {detail} Again."
                    )
                    publish = self._snapshot(view=OpenGrandSuddenBoardView(self))
                    arm_round = self.round
                elif result == 1:
                    self.score[self.a.id] += 1
                    self.history.append(
                        f"*{line}*\n"
                        f"**Sudden {self.round}:** {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                        f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {detail}"
                    )
                    finish = (self.a, self.b)
                else:
                    self.score[self.b.id] += 1
                    self.history.append(
                        f"*{line}*\n"
                        f"**Sudden {self.round}:** {SPELLS[disp_a]['emoji']} {SPELLS[disp_a]['name']} vs "
                        f"{SPELLS[disp_b]['emoji']} {SPELLS[disp_b]['name']} — {detail}"
                    )
                    finish = (self.b, self.a)
                reply = f"You cast **{SPELLS[spell]['name']}**."

        if finish:
            await self._finish(finish[0], finish[1])
        elif publish:
            await self._publish(*publish)
            if arm_round is not None:
                self._arm(self._sudden_timeout(arm_round))
        return reply

    async def _finish(self, winner, loser):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self.cog.release_match(self)
        outcome = self.cog.settle_grand(winner, loser)
        from cogs.store import HOUSES
        if outcome["awarded"]:
            h = HOUSES[outcome["house"]]
            tail = f"+{outcome['awarded']} to {h['emoji']} {h['name']}"
            if outcome.get("night"):
                tail += " (Duel Night!)"
        elif outcome["reason"] == "same-house":
            tail = "Housemates — no points"
        elif outcome["reason"] == "daily-cap":
            tail = f"{winner.display_name} has taken today's Grand points already"
        else:
            tail = "No house to credit"
        async with self.lock:
            snap = self._snapshot(
                view=None, footer=f"{winner.display_name} wins the Grand Duel • {tail}")
        await self._publish(*snap)
        notes = outcome.get("notes") or []
        if notes and self.channel is not None:
            try:
                await self.channel.send("\n".join(notes))
            except discord.DiscordException:
                log.exception("Could not post Grand Duel announcements.")


class GrandAcceptView(discord.ui.View):
    def __init__(self, gd: GrandDuel):
        super().__init__(timeout=ACCEPT_TIMEOUT + 5)
        self.gd = gd

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.gd
        if interaction.user.id != d.b.id:
            await interaction.response.send_message("This challenge isn't yours to answer.",
                                                    ephemeral=True)
            return
        async with d.lock:
            if d.state != "pending":
                await interaction.response.send_message("Too late for that.", ephemeral=True)
                return
            d.state = "locking"
        await interaction.response.defer()
        await d.begin_locking()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.gd
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
                                            "No Grand Duel today.", color=0x7A7A7A),
            view=None,
        )


class GrandLockPromptView(discord.ui.View):
    def __init__(self, gd: GrandDuel):
        super().__init__(timeout=GRAND_LOCK_TIMEOUT + 5)
        self.gd = gd

    @discord.ui.button(
        label="Open my sequence board",
        style=discord.ButtonStyle.primary,
        emoji="📜",
    )
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        d = self.gd
        if interaction.user.id not in d.sequences:
            await interaction.response.send_message("You're watching, not duelling.", ephemeral=True)
            return
        if interaction.user.id in d.locked:
            await interaction.response.send_message("You've already locked your sequence.", ephemeral=True)
            return
        if d.state != "locking":
            await interaction.response.send_message("Locking is closed.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Build your {GRAND_SLOTS}-spell sequence. Only you can see this.\n"
            f"**Slots filled: 0/{GRAND_SLOTS}**",
            view=GrandSequenceView(d, interaction.user.id),
            ephemeral=True,
        )


class GrandSequenceView(discord.ui.View):
    def __init__(self, gd: GrandDuel, user_id: int):
        super().__init__(timeout=ACCEPT_TIMEOUT)
        self.gd = gd
        self.user_id = user_id
        self.seq: list[str] = []
        for key, spell in SPELLS.items():
            self.add_item(GrandSeqSpellButton(key, spell))
        self.add_item(GrandSeqUndoButton())
        self.add_item(GrandSeqSubmitButton())

    def status_text(self) -> str:
        filled = " → ".join(SPELLS[s]["emoji"] + SPELLS[s]["name"] for s in self.seq) or "—"
        return (f"Build your {GRAND_SLOTS}-spell sequence. Only you can see this.\n"
                f"**Slots filled: {len(self.seq)}/{GRAND_SLOTS}**\n{filled}")


class GrandSeqSpellButton(discord.ui.Button):
    def __init__(self, key: str, spell: dict):
        super().__init__(label=spell["name"], emoji=spell["emoji"],
                         style=discord.ButtonStyle.secondary, row=0 if key in ("hex", "ward", "disarm") else 1)
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        v: GrandSequenceView = self.view
        if interaction.user.id != v.user_id:
            await interaction.response.send_message("Not your sequence.", ephemeral=True)
            return
        if len(v.seq) >= GRAND_SLOTS:
            await interaction.response.send_message("Sequence full — hit Submit.", ephemeral=True)
            return
        v.seq.append(self.key)
        await interaction.response.edit_message(content=v.status_text(), view=v)


class GrandSeqUndoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Undo", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        v: GrandSequenceView = self.view
        if interaction.user.id != v.user_id:
            await interaction.response.send_message("Not your sequence.", ephemeral=True)
            return
        if v.seq:
            v.seq.pop()
        await interaction.response.edit_message(content=v.status_text(), view=v)


class GrandSeqSubmitButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Submit sequence", style=discord.ButtonStyle.success, row=2)

    async def callback(self, interaction: discord.Interaction):
        v: GrandSequenceView = self.view
        if interaction.user.id != v.user_id:
            await interaction.response.send_message("Not your sequence.", ephemeral=True)
            return
        if len(v.seq) != GRAND_SLOTS:
            await interaction.response.send_message(
                f"Need {GRAND_SLOTS} spells — you have {len(v.seq)}.", ephemeral=True)
            return
        result = v.gd.try_lock(v.user_id, v.seq)
        if result != "locked":
            await interaction.response.send_message(result, ephemeral=True)
            return
        await interaction.response.edit_message(
            content="Sequence locked. Waiting on your opponent…", view=None)
        await v.gd.maybe_start_play()


class OpenGrandSuddenBoardView(discord.ui.View):
    def __init__(self, gd: GrandDuel):
        super().__init__(timeout=ROUND_TIMEOUT * 6 + 60)
        self.gd = gd

    @discord.ui.button(
        label="Open my cast board",
        style=discord.ButtonStyle.danger,
        emoji="\U0001FA84",
    )
    async def open_board(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.gd.open_sudden_board(interaction)


class GrandSuddenPrivateSpellView(discord.ui.View):
    def __init__(self, gd: GrandDuel):
        super().__init__(timeout=ROUND_TIMEOUT + 5)
        self.gd = gd
        for i, (key, spell) in enumerate(SPELLS.items()):
            self.add_item(GrandSuddenSpellButton(key, spell, row=0 if i < 3 else 1))


class GrandSuddenSpellButton(discord.ui.Button):
    def __init__(self, key: str, spell: dict, row: int = 0):
        super().__init__(
            label=spell["name"],
            emoji=spell["emoji"],
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        d = self.view.gd
        if interaction.user.id not in d.score:
            await interaction.response.send_message(
                "You're watching, not duelling.", ephemeral=True
            )
            return
        await interaction.response.defer()
        before = d.board_gen
        reply = await d.sudden_cast(interaction.user, self.key)
        if d.board_gen == before:
            try:
                await interaction.followup.send(reply, ephemeral=True)
            except discord.DiscordException:
                pass
