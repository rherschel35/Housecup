"""Descent cog: fights, loot, and staff restore tools."""

from pathlib import Path
from typing import Optional
import asyncio
import json
import os
import random
import time
from discord import app_commands
from discord.ext import commands
import discord

from cogs.descent.rules import (
    AP_REGEN_PER_TURN,
    ASSETS_DIR,
    ATK_PER_POINT,
    BASELINE_DMG_MULT,
    BASE_ATK,
    BASE_DEF,
    BASE_HP,
    BOSS_IMAGE,
    BOSS_ITEM,
    BOSS_NAMES,
    BOSS_SUMMONS,
    CAST_AP_COST,
    CLAIMS_PATH,
    DEFEND_AP_COST,
    DEF_PER_POINT,
    DESCENT_CHANNEL_IDS,
    DESCENT_ONESHOT_USER_ID,
    ELEMENT_EMOJI,
    FLOOR_CLEAR_GUARANTEED,
    FURY_BOSS_FLOORS,
    HEAL_AP_COST,
    HEAL_FRACTION,
    HP_PER_POINT,
    IDLE_TIMEOUT,
    LOCKOUT_SECONDS,
    MAX_FLOOR,
    MAX_LOSSES,
    MONSTERS_PER_FLOOR,
    MONSTER_DROP_CHANCE,
    MONSTER_IMAGE,
    MONSTER_NAMES,
    REST_DMG_MULT,
    SECONDARY_CHANCE,
    SECONDARY_ELEMENT,
    SHADOW_IMAGE,
    SPELL_NAME,
    SPIREHEART_PERK,
    SPIREHEART_STAT_CHANCE,
    SPIREHEART_STAT_FLOOR,
    STARTING_MAX_AP,
    STATE_DIR,
    STATE_PATH,
    STATUP_AT_MONSTER,
    STRIKE_MULT,
    WEAK_TO,
    WILD_THREAT_ASSETS,
    ZONE_ITEM,
    _descent_channel_hint,
    append_army_unit,
    army_floor_counts,
    blank_record,
    cleared_depth,
    clone_army_unit,
    combat_to_points,
    fury_hit_count,
    log,
    max_ap_for_depth,
    mitigate,
    monster_stats,
    pending_statup_picks,
    player_stats,
    practice_statup_chance,
    progress_backfill_plan,
    recruit_from_fight,
    stamp_owner,
    zone_for,
)

from cogs.descent.fight import Fight

from cogs.descent.views import (
    FightView,
    OneShotView,
    StatUpView,
)

class Descent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self.fights: dict[int, Fight] = {}
        # In-memory auto-play: after a win, post the next monster without /descend.
        self.auto_play: set[int] = set()
        self.auto_practice_floor: dict[int, int] = {}

    async def cog_load(self):
        self.bot.add_view(StatUpView(self))

    async def cog_unload(self):
        for fight in list(self.fights.values()):
            fight.cancel_idle()

    def is_auto(self, user_id: int) -> bool:
        return user_id in self.auto_play

    def set_auto(self, user_id: int, enabled: bool) -> None:
        if enabled:
            self.auto_play.add(user_id)
        else:
            self.stop_auto(user_id)

    def stop_auto(self, user_id: int) -> None:
        self.auto_play.discard(user_id)
        self.auto_practice_floor.pop(user_id, None)

    async def _idle_timeout(self, fight: Fight, gen: int) -> None:
        try:
            await asyncio.sleep(IDLE_TIMEOUT)
        except asyncio.CancelledError:
            return
        if fight._idle_gen != gen:
            return
        if self.fights.get(fight.user_id) is not fight:
            return
        await self._forfeit_fight(fight, reason="idle")

    async def _forfeit_fight(self, fight: Fight, *, reason: str) -> dict:
        """End an open fight as a loss (idle timeout or /descendend).

        Practice fights still take no penalty. Returns a small result dict
        for command replies: ``practice``, ``locked``, ``losses``.
        """
        if self.fights.get(fight.user_id) is fight:
            del self.fights[fight.user_id]
        fight.cancel_idle()
        await self._dismiss_oneshot(fight)
        was_auto = self.is_auto(fight.user_id)
        self.stop_auto(fight.user_id)

        if reason == "idle":
            why = (
                f"No button pressed for **{IDLE_TIMEOUT // 60} minutes** — "
                f"**{fight.name}** claims the fight."
            )
        else:
            why = f"**{fight.name}** encounter forfeited."

        if fight.is_practice:
            tip = (
                "Use `/descend floor:` again"
                if not was_auto
                else "Auto stopped — `/descend floor:… auto:True` to grind again"
            )
            embed = discord.Embed(
                title="Timed out" if reason == "idle" else "Forfeited",
                description=(
                    f"{why} It was only practice — no penalty, no lockout. {tip}."
                ),
                color=0x95A5A6,
            )
            await self._edit_fight_message(fight, embed)
            return {"practice": True, "locked": False, "losses": 0}

        rec = self.record(fight.user_id)
        rec["losses"] += 1
        locked = rec["losses"] >= MAX_LOSSES
        if locked:
            rec["locked_until"] = time.time() + LOCKOUT_SECONDS
            rec["losses"] = 0
            rec["monster_index"] = 1
        self.save()

        if locked:
            desc = (
                f"{why} That's 3 losses on floor {fight.floor} — "
                f"you're locked out for 24 hours. When you're back, floor "
                f"{fight.floor} restarts at monster 1."
            )
        else:
            tip = "Use `/descend` to try that monster again."
            if was_auto:
                tip = "Auto stopped — `/descend auto:True` to keep chaining after a win."
            desc = f"{why} {tip}"
        embed = discord.Embed(
            title="Timed out" if reason == "idle" else "Forfeited",
            description=desc,
            color=0xC0392B,
        )
        await self._edit_fight_message(fight, embed)
        return {
            "practice": False,
            "locked": locked,
            "losses": 0 if locked else int(rec["losses"]),
        }

    async def _edit_fight_message(
        self, fight: Fight, embed: discord.Embed
    ) -> None:
        if fight.message is None:
            return
        try:
            await fight.message.edit(embed=embed, view=None, attachments=[])
        except discord.DiscordException:
            log.exception("Could not edit Descent fight message after forfeit")

    def _parse_state_file(self, path: Path) -> dict | None:
        """Return parsed Descent state, or None if missing/empty/invalid."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            log.exception("Could not read Descent state at %s", path)
            return None
        if not raw.strip():
            log.error("Descent state at %s is empty (0 bytes / whitespace).", path)
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.exception("Descent state at %s is corrupt JSON.", path)
            return None
        if not isinstance(data, dict):
            log.error("Descent state at %s is not a JSON object.", path)
            return None
        players = data.get("players")
        if not isinstance(players, dict):
            data["players"] = {}
        return data

    def _candidate_state_paths(self) -> list[Path]:
        """Main file first, then tmp / recent backups (crash recovery)."""
        paths = [STATE_PATH, STATE_PATH.with_suffix(".tmp")]
        try:
            backups = sorted(
                STATE_DIR.glob("descent_state.corrupt.*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            paths.extend(backups[:5])
        except OSError:
            pass
        return paths

    def _quarantine_corrupt(self, path: Path) -> Path | None:
        if not path.exists():
            return None
        try:
            backup = STATE_DIR / f"descent_state.corrupt.{int(time.time())}.json"
            os.replace(path, backup)
            log.error("Moved unreadable Descent state %s → %s", path, backup)
            return backup
        except OSError:
            log.exception("Could not quarantine corrupt Descent state at %s", path)
            return None

    def _load(self) -> dict:
        """Load Descent state, recovering from .tmp / corrupt backups when needed.

        An empty file (JSONDecodeError char 0) usually means a non-atomic write
        was interrupted mid-redeploy. We never silently treat that as 'no players'
        without checking recovery copies first.
        """
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        primary_bad = False
        if STATE_PATH.exists():
            data = self._parse_state_file(STATE_PATH)
            if data is not None and data.get("players"):
                log.info(
                    "Loaded Descent state: %d players from %s",
                    len(data["players"]),
                    STATE_PATH,
                )
                return data
            if data is not None and not data.get("players"):
                log.warning("Descent state at %s has 0 players.", STATE_PATH)
                # Still prefer an empty-but-valid file over inventing one — but
                # keep looking for a richer backup below.
                empty_valid = data
            else:
                empty_valid = None
                primary_bad = True
                self._quarantine_corrupt(STATE_PATH)
        else:
            empty_valid = None

        for path in self._candidate_state_paths():
            if path == STATE_PATH:
                continue
            data = self._parse_state_file(path)
            if data is None or not data.get("players"):
                continue
            log.error(
                "Recovered Descent state with %d players from %s "
                "(primary was %s).",
                len(data["players"]),
                path,
                "corrupt/empty" if primary_bad else "missing/empty",
            )
            # Restore recovered copy to the canonical path.
            try:
                tmp = STATE_PATH.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                os.replace(tmp, STATE_PATH)
            except OSError:
                log.exception("Could not restore recovered Descent state to %s", STATE_PATH)
            return data

        if empty_valid is not None:
            return empty_valid

        log.warning(
            "No usable Descent state under %s — starting empty. "
            "Check for descent_state.corrupt.*.json on the volume.",
            STATE_DIR,
        )
        return {"players": {}}

    def save(self):
        """Atomic write. Never clobber a populated file with an empty ledger."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        incoming = self.state.get("players") if isinstance(self.state, dict) else None
        if not incoming and STATE_PATH.exists():
            try:
                existing = self._parse_state_file(STATE_PATH)
                prior = (existing or {}).get("players") if existing else None
                if prior:
                    log.error(
                        "Refusing to save empty Descent state over %d players at %s",
                        len(prior),
                        STATE_PATH,
                    )
                    return
            except Exception:
                log.exception("Could not verify existing Descent state before save")
        tmp = STATE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_PATH)

    def record(self, user_id: int) -> dict:
        rec = self.state["players"].setdefault(str(user_id), blank_record())
        rec.setdefault("max_ap", STARTING_MAX_AP)  # back-compat for records saved before AP existed
        rec.setdefault("bosses_bound", [])          # back-compat for records saved before boss trophies existed
        rec.setdefault("army", [])                  # back-compat for records saved before army tracking
        rec.setdefault("army_seq", len(rec.get("army") or []))
        return rec

    def boss_trophies(self, user_id: int) -> dict:
        """Descent boss trophies this player has bound, keyed for /summon and
        /bestiary in the Beasts cog. Read-only - never creates a record."""
        rec = self.state["players"].get(str(user_id))
        if not rec:
            return {}
        out = {}
        for floor in rec.get("bosses_bound", []):
            name, emoji, _ = BOSS_NAMES.get(floor, (f"Floor {floor} Boss", "👑", None))
            image = BOSS_IMAGE.get(floor)
            out[f"boss:{floor}"] = {
                "name": name,
                "emoji": emoji,
                "floor": floor,
                "image_path": (ASSETS_DIR / image) if image else None,
                "summons": BOSS_SUMMONS.get(floor, [f"{{owner}}'s {name} answers the call."]),
            }
        return out

    # -------------------------------------------------------- fight setup

    def _make_monster(self, floor: int, monster_index: int):
        zone = zone_for(floor)
        primary = zone["element"]
        is_boss = (floor % 10 == 0 and monster_index == MONSTERS_PER_FLOOR)
        if is_boss:
            element = primary
            name, emoji, kind = BOSS_NAMES[floor]
            weak = [WEAK_TO[element], WEAK_TO[WEAK_TO[element]]]
        else:
            secondary = SECONDARY_ELEMENT[primary]
            element = secondary if random.random() < SECONDARY_CHANCE else primary
            name, emoji, kind = random.choice(MONSTER_NAMES[element])
            weak = [WEAK_TO[element]]
        m_hp, m_atk, m_def = monster_stats(floor, is_boss)
        return name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def

    async def _monster_file(self, fight: "Fight") -> discord.File:
        # Endgame veiled fights use a generic shadow portrait until the player attacks.
        if getattr(fight, "shadowed", False) and not getattr(fight, "revealed", True):
            path = WILD_THREAT_ASSETS / SHADOW_IMAGE
            return discord.File(path, filename="monster.png")
        name_key = getattr(fight, "true_name", None) or fight.name
        filename = BOSS_IMAGE[fight.floor] if fight.is_boss else MONSTER_IMAGE[name_key]
        path = ASSETS_DIR / filename
        return discord.File(path, filename="monster.png")

    def _apply_potion_mods(self, fight: "Fight", user_id: int, is_boss: bool):
        """Pull any active potion buffs for a real fight and fold them
        into this Fight - a no-op if the Potions cog isn't loaded."""
        potions = self.bot.get_cog("Potions")
        if not potions:
            return
        mods = potions.consume_for_fight(user_id, is_boss)
        if not mods:
            return
        if "atk_mult" in mods:
            fight.p_atk = round(fight.p_atk * mods["atk_mult"])
        if "def_mult" in mods:
            fight.p_def = round(fight.p_def * mods["def_mult"])
        if "ap_bonus" in mods:
            fight.ap_max += mods["ap_bonus"]
            fight.ap = fight.ap_max
        if "hp_bonus" in mods:
            fight.p_hp_max += mods["hp_bonus"]
            fight.p_hp += mods["hp_bonus"]
        if "heal_mult" in mods:
            fight.heal_mult = mods["heal_mult"]
        if "defend_mult" in mods:
            fight.defend_mult = mods["defend_mult"]
        if "rest_no_penalty" in mods:
            fight.rest_no_penalty = True
        if "reveal_weakness" in mods:
            fight.reveal_weakness = True
        if "boss_dmg_mult" in mods:
            fight.boss_dmg_mult = mods["boss_dmg_mult"]
        if potions.has_revive(user_id):
            fight.revive_available = True
            potions.consume_revive(user_id)

    async def _publish_board(
        self,
        interaction: discord.Interaction,
        fight: Fight,
        embed: discord.Embed,
        view: Optional[discord.ui.View],
    ) -> None:
        """Edit the public fight post (needed when One-shot is ephemeral)."""
        owner = interaction.user if interaction.user.id == fight.user_id else None
        if owner is None and interaction.guild is not None:
            owner = interaction.guild.get_member(fight.user_id)
        if owner is not None:
            stamp_owner(embed, owner)
        if fight.message is not None:
            try:
                await fight.message.edit(embed=embed, view=view, attachments=[])
                return
            except discord.DiscordException:
                log.exception("Could not update Descent fight board.")
        await interaction.edit_original_response(embed=embed, view=view)

    async def _dismiss_oneshot(
        self,
        fight: Fight,
        interaction: Optional[discord.Interaction] = None,
        *,
        used: bool = False,
    ) -> None:
        text = "⚡ One-shot — done." if used else "Fight over."
        if interaction is not None and interaction.message is not None:
            oneshot = fight.oneshot_message
            if oneshot is not None and interaction.message.id == oneshot.id:
                try:
                    await interaction.edit_original_response(
                        content=text, embed=None, view=None
                    )
                except discord.DiscordException:
                    pass
                fight.oneshot_message = None
                return
        if fight.oneshot_message is not None:
            try:
                await fight.oneshot_message.edit(content=text, embed=None, view=None)
            except discord.DiscordException:
                pass
            fight.oneshot_message = None

    async def _offer_oneshot(
        self, interaction: discord.Interaction, fight: Fight
    ) -> None:
        if (
            DESCENT_ONESHOT_USER_ID is None
            or fight.user_id != DESCENT_ONESHOT_USER_ID
        ):
            return
        try:
            msg = await interaction.followup.send(
                "⚡ Private finisher — only you can see this.",
                view=OneShotView(self, fight),
                ephemeral=True,
            )
            fight.oneshot_message = msg
        except discord.DiscordException:
            log.exception("Could not send Descent one-shot panel.")

    async def _post_fight_board(
        self,
        interaction: discord.Interaction,
        fight: Fight,
        *,
        followup: bool = False,
    ) -> None:
        file = await self._monster_file(fight)
        embed = fight.embed(interaction.user)
        view = FightView(self, fight)
        use_followup = followup or interaction.response.is_done()
        if use_followup:
            try:
                msg = await interaction.followup.send(embed=embed, view=view, file=file)
                fight.message = msg
            except discord.DiscordException:
                log.exception("Could not post Descent fight board via followup.")
                fight.message = None
        else:
            await interaction.response.send_message(embed=embed, view=view, file=file)
            try:
                fight.message = await interaction.original_response()
            except discord.HTTPException:
                fight.message = None
        await self._offer_oneshot(interaction, fight)

    def _apply_prelude_bastion(self, fight: Fight, user_id: int) -> None:
        """Prelude Bastion: Descent monsters start at half attack."""
        castles = self.bot.get_cog("Castles")
        if castles and castles.prelude_halves_monster_atk(user_id):
            fight.m_atk = max(1, int(fight.m_atk) // 2)

    def _arm_fight(self, fight: Fight) -> None:
        fight.auto = self.is_auto(fight.user_id)
        self.fights[fight.user_id] = fight
        fight.arm_idle(self)

    async def _start_fight(
        self,
        interaction: discord.Interaction,
        rec: dict,
        *,
        followup: bool = False,
    ):
        floor, idx = rec["floor"], rec["monster_index"]
        name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def = self._make_monster(floor, idx)
        p_hp, p_atk, p_def = player_stats(rec)
        fight = Fight(interaction.user.id, floor, idx, is_boss, name, emoji, element, kind, weak,
                      m_hp, m_atk, m_def, p_hp, p_atk, p_def, ap_max=rec["max_ap"])
        self._apply_potion_mods(fight, interaction.user.id, is_boss)
        self._apply_prelude_bastion(fight, interaction.user.id)
        # Real runs clear any practice auto target.
        self.auto_practice_floor.pop(interaction.user.id, None)
        self._arm_fight(fight)
        await self._post_fight_board(interaction, fight, followup=followup)

    async def _start_practice_fight(
        self,
        interaction: discord.Interaction,
        rec: dict,
        floor: int,
        *,
        followup: bool = False,
    ):
        name, emoji, element, kind, weak, is_boss, m_hp, m_atk, m_def = self._make_monster(floor, 1)
        p_hp, p_atk, p_def = player_stats(rec)
        fight = Fight(interaction.user.id, floor, 0, False, name, emoji, element, kind, weak,
                      m_hp, m_atk, m_def, p_hp, p_atk, p_def, ap_max=rec["max_ap"], is_practice=True)
        self._apply_prelude_bastion(fight, interaction.user.id)
        if self.is_auto(interaction.user.id):
            self.auto_practice_floor[interaction.user.id] = floor
        self._arm_fight(fight)
        await self._post_fight_board(interaction, fight, followup=followup)

    async def _try_auto_continue(self, interaction: discord.Interaction) -> bool:
        """If auto-play is on, start the next fight. Returns True when a fight was posted.

        Practice auto ignores the real-floor 24h lockout (same as `/descend floor:`).
        """
        uid = interaction.user.id
        if not self.is_auto(uid) or uid in self.fights:
            return False
        rec = self.record(uid)
        if pending_statup_picks(rec) > 0:
            return False
        practice_floor = self.auto_practice_floor.get(uid)
        # Real-floor lockout only blocks real auto — practice on cleared floors still runs.
        if practice_floor is None and rec["locked_until"] > time.time():
            self.stop_auto(uid)
            try:
                await interaction.followup.send(
                    "⏹️ Auto-play stopped — floor lockout is active. "
                    "Practice a cleared floor with `/descend floor:n auto:True` while you wait.",
                    ephemeral=True,
                )
            except discord.DiscordException:
                pass
            return False
        try:
            if practice_floor is not None:
                await self._start_practice_fight(
                    interaction, rec, practice_floor, followup=True,
                )
                return True
            if rec["floor"] > MAX_FLOOR:
                self.stop_auto(uid)
                await interaction.followup.send(
                    "⏹️ Auto-play stopped — the Descent is complete.",
                    ephemeral=True,
                )
                return False
            await self._start_fight(interaction, rec, followup=True)
            return True
        except discord.DiscordException:
            log.exception("Descent auto-continue failed for %s", uid)
            return False

    # ------------------------------------------------------------ combat

    async def take_action(self, interaction: discord.Interaction, action: str, element: Optional[str] = None):
        fight = self.fights.get(interaction.user.id)
        if not fight:
            await interaction.followup.send("That fight isn't active anymore - use `/descend` to start again.",
                                            ephemeral=True)
            return
        # Any button press resets the idle clock.
        fight.arm_idle(self)
        fight.round += 1
        dmg_dealt = 0
        counter_mult = BASELINE_DMG_MULT
        lines: list[str] = []

        if action == "cast":
            if fight.ap < CAST_AP_COST:
                await interaction.followup.send("Not enough AP for that spell.", ephemeral=True)
                return
            fight.ap -= CAST_AP_COST
            if element == fight.element:
                mult, note = 0.5, "resisted"
            elif element in fight.weak:
                mult, note = 2.0, "super effective"
            else:
                mult, note = 1.0, None
            dmg_dealt = mitigate(fight.p_atk, mult, fight.m_def)
            lines.append(f"{ELEMENT_EMOJI[element]} You cast **{SPELL_NAME[element]}** for **{dmg_dealt}**"
                        + (f" ({note})" if note else "") + f" (-{CAST_AP_COST} AP)")
            reveal = fight.reveal_identity()
            if reveal:
                lines.append(reveal)
        elif action == "strike":
            dmg_dealt = mitigate(fight.p_atk, STRIKE_MULT, fight.m_def)
            lines.append(f"🗡️ You strike for **{dmg_dealt}** (no AP used)")
            reveal = fight.reveal_identity()
            if reveal:
                lines.append(reveal)
        elif action == "heal":
            if fight.ap < HEAL_AP_COST:
                await interaction.followup.send("Not enough AP to heal.", ephemeral=True)
                return
            fight.ap -= HEAL_AP_COST
            missing = fight.p_hp_max - fight.p_hp
            healed = round(missing * HEAL_FRACTION * fight.heal_mult)
            fight.p_hp = min(fight.p_hp_max, fight.p_hp + healed)
            lines.append(f"💚 You heal for **{healed}** (-{HEAL_AP_COST} AP, you're exposed)")
        elif action == "defend":
            if fight.ap < DEFEND_AP_COST:
                await interaction.followup.send("Not enough AP to defend.", ephemeral=True)
                return
            fight.ap -= DEFEND_AP_COST
            counter_mult = fight.defend_mult
            lines.append("🛡️ You brace to defend (free action)" if DEFEND_AP_COST == 0
                         else f"🛡️ You brace to defend (-{DEFEND_AP_COST} AP)")
        elif action == "rest":
            fight.ap = fight.ap_max
            counter_mult = 1.0 if fight.rest_no_penalty else REST_DMG_MULT
            lines.append("😮‍💨 You catch your breath - AP fully restored"
                        + (" and unbothered" if fight.rest_no_penalty else " (unguarded)"))
        elif action == "oneshot":
            if (
                DESCENT_ONESHOT_USER_ID is None
                or interaction.user.id != DESCENT_ONESHOT_USER_ID
            ):
                await interaction.followup.send(
                    "That button isn't for you.", ephemeral=True
                )
                return
            dmg_dealt = max(fight.m_hp, 1)
            fight.reveal_identity()
            lines.append(
                f"⚡ You end it in one motion — **{fight.name}** never gets a turn."
            )
        else:
            return

        if dmg_dealt:
            fight.m_hp -= dmg_dealt
        fight.log.extend(lines)

        second_wind = fight.try_second_wind()
        if second_wind:
            fight.log.append(second_wind)

        if fight.m_hp <= 0:
            fight.reveal_identity()  # army / win text always use the true identity
            await self._on_win(interaction, fight, from_oneshot=(action == "oneshot"))
            return

        counter_mult *= fight.boss_dmg_mult if fight.is_boss else 1.0
        counter_mult *= getattr(fight, "monster_dmg_mult", 1.0)
        hits = fury_hit_count(fight)
        hit_rolls: list[int] = []
        for _ in range(hits):
            roll = mitigate(fight.m_atk, counter_mult, fight.p_def)
            fight.p_hp -= roll
            hit_rolls.append(roll)
        tag = " (reduced)" if counter_mult < 1 else (" (extra!)" if counter_mult > 1 else "")
        if hits > 1:
            rolls_txt = ", ".join(f"**{r}**" for r in hit_rolls)
            fight.log.append(
                f"{fight.emoji} {fight.name} strikes in a **Fury** ({hits} hits: {rolls_txt})"
                f" — **{sum(hit_rolls)}** total{tag}"
            )
        else:
            fight.log.append(f"{fight.emoji} {fight.name} hits back for **{hit_rolls[0]}**{tag}")

        if fight.p_hp <= 0:
            if fight.revive_available:
                fight.revive_available = False
                fight.p_hp = 1
                fight.log.append("🪽 Phoenix Tears flares - you're pulled back from the brink at **1 HP**.")
            else:
                await self._on_loss(interaction, fight)
                return

        fight.ap = min(fight.ap_max, fight.ap + AP_REGEN_PER_TURN)
        await self._publish_board(
            interaction,
            fight,
            fight.embed(interaction.user),
            FightView(self, fight),
        )

    async def _drop_loot(self, member: discord.Member, element: str, n: int = 1) -> Optional[str]:
        world_cog = self.bot.get_cog("World")
        if not world_cog:
            return None
        item_id = ZONE_ITEM[element]
        item = world_cog.world.items.get(item_id)
        if not item:
            return None
        async with world_cog.lock:
            student = world_cog.student(member)
            world_cog.world.give(student, item_id, n)
            world_cog.save()
        qty = f" x{n}" if n > 1 else ""
        return f"{item.get('emoji', '')} **{item['name']}**{qty}".strip()

    async def _drop_boss_item(self, member: discord.Member) -> Optional[str]:
        world_cog = self.bot.get_cog("World")
        item = world_cog.world.items.get(BOSS_ITEM) if world_cog else None
        if not world_cog or not item:
            return None
        async with world_cog.lock:
            student = world_cog.student(member)
            world_cog.world.give(student, BOSS_ITEM, 1)
            world_cog.save()
        return f"{item.get('emoji', '')} **{item['name']}**".strip()

    def spireheart_worn(self, member: discord.Member) -> bool:
        adorn = self.bot.get_cog("Adornments")
        return bool(adorn and adorn.has_perk(member.id, SPIREHEART_PERK))

    def owns_spireheart(self, member: discord.Member) -> bool:
        adorn = self.bot.get_cog("Adornments")
        if not adorn:
            return False
        return "spireheart_necklace" in (adorn.peek(member.id).get("owned") or {})

    def try_spireheart_statup(
        self, member: discord.Member, rec: dict, floor: int, *, announce_miss: bool = False,
    ) -> str | None:
        """Worn Spireheart Necklace: 20% free Descent stat pick on floors 45+.

        When ``announce_miss`` is set (practice fights), always return a line so
        players can see the necklace rolled — silent misses looked like the perk
        was broken next to the separate practice-grind %.
        """
        if floor < SPIREHEART_STAT_FLOOR:
            return None
        if not self.spireheart_worn(member):
            return None
        if random.random() < SPIREHEART_STAT_CHANCE:
            rec["pending_statup"] = pending_statup_picks(rec) + 1
            return (
                "📿 **Spireheart Necklace** flares — you've earned a free "
                "HP / Attack / Defense pick!"
            )
        if announce_miss:
            return "📿 Spireheart Necklace stays quiet (**20%** chance)."
        return None

    async def _on_win(
        self,
        interaction: discord.Interaction,
        fight: Fight,
        *,
        from_oneshot: bool = False,
    ):
        del self.fights[interaction.user.id]
        fight.cancel_idle()
        rec = self.record(interaction.user.id)
        member = interaction.user
        await self._dismiss_oneshot(fight, interaction, used=from_oneshot)
        castles = self.bot.get_cog("Castles")
        allowed = castles.recruit_allowed(member.id, 1) if castles else 1
        unit = None
        bind_pts = 0
        if allowed > 0:
            unit = recruit_from_fight(rec, fight)
            if castles:
                castles.note_recruits(member.id, 1)
                bind_pts += castles.note_bind40(
                    member, floor=fight.floor, n=1
                )
                bonus = castles.fifth_muster_bonus(member.id)
                if bonus and castles.recruit_allowed(member.id, bonus) > 0:
                    clone_army_unit(rec, unit)
                    castles.note_recruits(member.id, bonus)
                    bind_pts += castles.note_bind40(
                        member, floor=fight.floor, n=bonus
                    )
        elif castles:
            # Still tick Fifth Muster progress even when the daily cap blocks a bind.
            castles.fifth_muster_bonus(member.id)
        self.save()

        bind_line = ""
        if bind_pts:
            from cogs.castles import BIND_BONUS_GOAL, BIND_BONUS_MIN_FLOOR
            bind_line = (
                f"\n🏰 **+{bind_pts}** house points — "
                f"**{BIND_BONUS_GOAL}** floor-{BIND_BONUS_MIN_FLOOR}+ "
                "monsters bound today!"
            )

        if fight.is_practice:
            await self._on_practice_win(
                interaction, fight, rec, member, bind_line=bind_line
            )
            return

        drop_text = None
        if fight.is_boss:
            drop_text = await self._drop_boss_item(member)
        elif random.random() < MONSTER_DROP_CHANCE:
            drop_text = await self._drop_loot(member, fight.element)

        cleared_index = fight.monster_index
        if cleared_index >= MONSTERS_PER_FLOOR:
            # floor cleared
            floor = rec["floor"]
            rec["highest_cleared"] = max(rec["highest_cleared"], floor)
            rec["floor"] = min(floor + 1, MAX_FLOOR)
            rec["monster_index"] = 1
            rec["losses"] = 0
            # +1 Max AP every 10 floors cleared (10, 20, … 100).
            target_ap = max_ap_for_depth(rec["highest_cleared"])
            ap_increased = int(rec.get("max_ap") or STARTING_MAX_AP) < target_ap
            if ap_increased:
                rec["max_ap"] = target_ap
            clear_drop = await self._drop_loot(member, zone_for(floor)["element"], FLOOR_CLEAR_GUARANTEED)
            # Fury bosses (60/70/80/90/100) bank three free picks; everything else one.
            clear_picks = 3 if fight.is_boss and floor in FURY_BOSS_FLOORS else 1
            rec["pending_statup"] = clear_picks
            spire_line = self.try_spireheart_statup(member, rec, floor)
            self.save()

            potions = self.bot.get_cog("Potions")
            fortune_drop = None
            if potions:
                bonus_n = potions.consume_loot_boost(member.id)
                if bonus_n:
                    fortune_drop = await self._drop_loot(member, zone_for(floor)["element"], bonus_n)

            desc = f"**Floor {floor} cleared!**"
            if ap_increased:
                desc += f" Max AP is now **{rec['max_ap']}**."
            if drop_text:
                desc += f"\n📦 The kill dropped {drop_text}."
            if clear_drop:
                desc += f"\n📦 Clearing the floor also dropped {clear_drop}."
            if fortune_drop:
                desc += f"\n🍀 Draught of Fortune paid off: {fortune_drop}."
            if fight.is_boss:
                desc += f"\n🏆 You defeated **{fight.name}**!"
                if floor not in rec["bosses_bound"]:
                    rec["bosses_bound"].append(floor)
                    self.save()
                desc += (f"\n🐲 **{fight.name}** now answers to you. See it with `/bestiary`, "
                        f"call it with `/summon`.")
                if floor == 90:
                    adorn = self.bot.get_cog("Adornments")
                    if adorn:
                        try:
                            awarded = await adorn.check_member(member)
                            if "spireheart_necklace" in awarded:
                                desc += (
                                    "\n📿 You claimed the **Spireheart Necklace** — wear it for "
                                    "a 20% free stat pick on floors 45+."
                                )
                        except Exception:
                            log.exception("Spireheart award failed for %s", member.id)
            if floor >= MAX_FLOOR:
                desc += "\n\n👑 **The Descent is complete.** There is nothing further down."
                self.stop_auto(member.id)
            if clear_picks > 1:
                desc += (f"\n\nYou've earned **{clear_picks}** stat points for clearing this Fury boss "
                         f"— pick where each one goes.")
            else:
                desc += "\n\nYou've earned a stat point for clearing the floor - pick where it goes."
            if spire_line:
                desc += f"\n{spire_line}"
            if bind_line:
                desc += bind_line
            if self.is_auto(member.id) and floor < MAX_FLOOR:
                desc += "\n⚡ Auto paused for your stat pick — next monster follows when you're done."
            embed = discord.Embed(title=f"{fight.emoji} Victory!", description=desc, color=0x2ECC71)
            await self._publish_board(interaction, fight, embed, StatUpView(self))
            return

        rec["monster_index"] = cleared_index + 1
        mid_stat = False
        if cleared_index == STATUP_AT_MONSTER:
            rec["pending_statup"] = pending_statup_picks(rec) + 1
            mid_stat = True
        spire_line = self.try_spireheart_statup(member, rec, fight.floor)
        self.save()

        desc = f"**{fight.name}** falls. On to monster {rec['monster_index']}/{MONSTERS_PER_FLOOR}."
        if drop_text:
            desc += f"\n📦 It dropped {drop_text}."
        if mid_stat:
            desc += "\n\nYou've earned a stat point - pick where it goes."
        if spire_line:
            desc += f"\n{spire_line}"
        if bind_line:
            desc += bind_line
        embed = discord.Embed(
            title=f"{fight.emoji} Victory!",
            description=desc,
            color=0x2ECC71,
        )
        if pending_statup_picks(rec) > 0:
            if self.is_auto(member.id):
                embed.description += "\n⚡ Auto paused for your stat pick — next monster follows when you're done."
            await self._publish_board(interaction, fight, embed, StatUpView(self))
        elif self.is_auto(member.id):
            embed.description += "\n⚡ Auto — next monster incoming…"
            await self._publish_board(interaction, fight, embed, None)
            await self._try_auto_continue(interaction)
        else:
            embed.description += "\nUse `/descend` to keep going."
            await self._publish_board(interaction, fight, embed, None)

    async def _on_practice_win(
        self,
        interaction: discord.Interaction,
        fight: Fight,
        rec: dict,
        member: discord.Member,
        *,
        bind_line: str = "",
    ):
        got_loot = random.random() < MONSTER_DROP_CHANCE
        if got_loot:
            await self._drop_loot(member, fight.element)

        desc = f"**{fight.name}** falls. This was a practice fight - your floor progress hasn't changed."
        desc += "\nA material dropped!" if got_loot else "\nNo material dropped this time."
        if bind_line:
            desc += bind_line

        chance = practice_statup_chance(rec["floor"], fight.floor)
        pct = max(1, int(round(chance * 100)))
        pending_before = pending_statup_picks(rec)
        spire_active = (
            fight.floor >= SPIREHEART_STAT_FLOOR and self.spireheart_worn(member)
        )

        if pending_before > 0:
            # Should be rare (practice is gated on spend), but keep the picker
            # attached so a stuck pick can still be cleared from a win message.
            desc += (f"\n\nYou still have **{pending_before}** unspent stat pick"
                     f"{'s' if pending_before != 1 else ''} — spend "
                     f"{'them' if pending_before != 1 else 'it'} before practice can sharpen you again.")
        elif spire_active:
            # Spireheart is the headline 20% while worn on 45+ — always say
            # hit or miss. Practice-grind % stays a quiet bonus on top.
            spire_line = self.try_spireheart_statup(
                member, rec, fight.floor, announce_miss=True,
            )
            if spire_line:
                desc += f"\n{spire_line}"
            if random.random() < chance:
                rec["pending_statup"] = pending_statup_picks(rec) + 1
                desc += "\nPractice grind also paid off — another pick!"
        else:
            practice_hit = False
            if random.random() < chance:
                rec["pending_statup"] = pending_statup_picks(rec) + 1
                practice_hit = True
                desc += "\n\nThis grind sharpened you - pick a stat to raise."
            elif (
                fight.floor >= SPIREHEART_STAT_FLOOR
                and self.owns_spireheart(member)
                and not self.spireheart_worn(member)
            ):
                desc += (
                    "\n📿 You own **Spireheart Necklace** — `/wear` it "
                    "for a **20%** free-stat chance on floors 45+."
                )
            else:
                desc += f"\n\nNo free stat this time ({pct}% practice chance on this floor)."

        pending = pending_statup_picks(rec)
        self.save()
        embed = discord.Embed(title=f"{fight.emoji} Practice victory!", description=desc, color=0x2ECC71)
        if pending > 0:
            if self.is_auto(member.id):
                desc += "\n⚡ Auto paused for your stat pick — practice continues when you're done."
                embed.description = desc
            await self._publish_board(interaction, fight, embed, StatUpView(self))
        elif self.is_auto(member.id):
            embed.description = desc + "\n⚡ Auto — another practice incoming…"
            await self._publish_board(interaction, fight, embed, None)
            await self._try_auto_continue(interaction)
        else:
            await self._publish_board(interaction, fight, embed, None)

    async def _on_loss(self, interaction: discord.Interaction, fight: Fight):
        del self.fights[interaction.user.id]
        fight.cancel_idle()
        await self._dismiss_oneshot(fight, interaction)
        was_auto = self.is_auto(interaction.user.id)
        # Loss breaks the chain; re-enable with `/descend auto:True`.
        self.stop_auto(interaction.user.id)

        if fight.is_practice:
            tip = (
                "Use `/descend floor:` again"
                if not was_auto
                else "Auto stopped — `/descend floor:… auto:True` to grind again"
            )
            embed = discord.Embed(
                title="Defeated",
                description=f"**{fight.name}** gets the better of you. It was only practice - no penalty, "
                            f"no lockout. {tip}.",
                color=0xC0392B,
            )
            await self._publish_board(interaction, fight, embed, None)
            return

        rec = self.record(interaction.user.id)
        rec["losses"] += 1
        locked = rec["losses"] >= MAX_LOSSES
        if locked:
            rec["locked_until"] = time.time() + LOCKOUT_SECONDS
            rec["losses"] = 0
            rec["monster_index"] = 1
        self.save()

        if locked:
            desc = (f"**{fight.name}** finishes you off. That's 3 losses on floor {fight.floor} - "
                     f"you're locked out for 24 hours. When you're back, floor {fight.floor} restarts "
                     f"at monster 1.")
        else:
            tip = "Use `/descend` to try that monster again."
            if was_auto:
                tip = "Auto stopped — `/descend auto:True` to keep chaining after a win."
            desc = f"**{fight.name}** finishes you off. {tip}"
        embed = discord.Embed(title="Defeated", description=desc, color=0xC0392B)
        await self._publish_board(interaction, fight, embed, None)

    async def pick_stat(self, interaction: discord.Interaction, stat: str):
        rec = self.record(interaction.user.id)
        remaining = pending_statup_picks(rec)
        if remaining <= 0:
            await interaction.response.send_message("Nothing to spend right now.", ephemeral=True)
            return
        rec["stat_points"][stat] += 1
        remaining -= 1
        rec["pending_statup"] = remaining
        self.save()
        label = {"hp": "Max HP", "atk": "Attack", "def": "Defense"}[stat]
        if remaining > 0:
            embed = discord.Embed(
                title="Stat raised",
                description=(f"**+1 {label}**. {remaining} pick"
                             f"{'s' if remaining != 1 else ''} left — choose another."),
                color=0x6C5CE7,
            )
            stamp_owner(embed, interaction.user)
            await interaction.response.edit_message(embed=embed, view=StatUpView(self))
            return
        if self.is_auto(interaction.user.id):
            embed = discord.Embed(
                title="Stat raised",
                description=f"**+1 {label}**. ⚡ Auto — next monster incoming…",
                color=0x6C5CE7,
            )
            stamp_owner(embed, interaction.user)
            await interaction.response.edit_message(embed=embed, view=None)
            await self._try_auto_continue(interaction)
            return
        embed = discord.Embed(
            title="Stat raised",
            description=f"**+1 {label}**. Use `/descend` to keep going.",
            color=0x6C5CE7,
        )
        stamp_owner(embed, interaction.user)
        await interaction.response.edit_message(embed=embed, view=None)

    async def _prompt_statup(self, interaction: discord.Interaction, rec: dict) -> bool:
        """If the player still owes a pick, show the picker and return True."""
        left = pending_statup_picks(rec)
        if left <= 0:
            return False
        tip = "Pick your remaining stat points first." if left > 1 else "Pick your stat point first."
        desc = f"{left} picks remaining." if left > 1 else None
        embed = discord.Embed(title="Choose a stat to raise", description=desc, color=0x6C5CE7)
        stamp_owner(embed, interaction.user)
        await interaction.response.send_message(
            tip,
            embed=embed,
            view=StatUpView(self),
        )
        return True

    # ----------------------------------------------------------- commands

    @app_commands.command(name="descend", description="Fight the next monster on your current Descent floor.")
    @app_commands.describe(
        floor="Replay a floor you've already cleared, for practice/loot (not boss floors).",
        auto="After each win, post the next monster automatically (Stop Auto on the board to cancel).",
    )
    async def descend(
        self,
        interaction: discord.Interaction,
        floor: Optional[int] = None,
        auto: Optional[bool] = None,
    ):
        if interaction.channel_id not in DESCENT_CHANNEL_IDS:
            await interaction.response.send_message(_descent_channel_hint(), ephemeral=True)
            return

        if auto is True:
            self.set_auto(interaction.user.id, True)
        elif auto is False:
            self.set_auto(interaction.user.id, False)

        rec = self.record(interaction.user.id)

        if floor is not None:
            if interaction.user.id in self.fights:
                await interaction.response.send_message(
                    "Finish your current fight first — `/descendend` forfeits it "
                    f"(counts as a loss). Idle {IDLE_TIMEOUT // 60} min also counts.",
                    ephemeral=True,
                )
                return
            if floor < 1 or floor > rec["highest_cleared"]:
                await interaction.response.send_message(
                    f"You can only replay a floor you've already cleared (up to floor {rec['highest_cleared']}).",
                    ephemeral=True)
                return
            if floor % 10 == 0:
                await interaction.response.send_message("Boss floors can't be replayed for practice.",
                                                         ephemeral=True)
                return
            # Same gate as a real descend: an unspent pick silently blocked
            # practice +1 rolls forever, which looked like the grind was broken.
            if await self._prompt_statup(interaction, rec):
                return
            await self._start_practice_fight(interaction, rec, floor)
            return

        now = time.time()
        if rec["locked_until"] > now:
            left = int(rec["locked_until"] - now)
            h, m = left // 3600, (left % 3600) // 60
            await interaction.response.send_message(
                f"Floor {rec['floor']} is locked after 3 losses. Try again in {h}h {m}m.", ephemeral=True)
            return
        if await self._prompt_statup(interaction, rec):
            return
        if interaction.user.id in self.fights:
            fight = self.fights[interaction.user.id]
            # Re-arm auto on an open board if they toggled it with this command.
            if auto is not None:
                fight.auto = self.is_auto(interaction.user.id)
            fight.arm_idle(self)
            await self._post_fight_board(interaction, fight)
            return
        if rec["floor"] > MAX_FLOOR:
            await interaction.response.send_message("You've already conquered the Descent.", ephemeral=True)
            return
        await self._start_fight(interaction, rec)

    @app_commands.command(
        name="descendend",
        description="Forfeit your Descent fight (counts as a loss). Staff can forfeit others.",
    )
    @app_commands.describe(member="Whose fight to forfeit (staff only; leave blank for yourself)")
    async def descendend(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        if interaction.channel_id not in DESCENT_CHANNEL_IDS:
            await interaction.response.send_message(_descent_channel_hint(), ephemeral=True)
            return

        target = member or interaction.user
        if target.id != interaction.user.id:
            store = self.bot.get_cog("Store")
            if not (store and store.is_staff(interaction.user)):
                await interaction.response.send_message(
                    "Only staff can forfeit someone else's fight. "
                    "Use `/descendend` with no one tagged for yourself.",
                    ephemeral=True,
                )
                return

        fight = self.fights.get(target.id)
        if fight is None:
            # Also clear a dangling auto flag with nothing to forfeit.
            if self.is_auto(target.id):
                self.stop_auto(target.id)
                who = "Your" if target.id == interaction.user.id else f"{target.display_name}'s"
                await interaction.response.send_message(
                    f"{who} auto-play was stopped (no open fight).",
                    ephemeral=True,
                )
                return
            who = (
                "You aren't"
                if target.id == interaction.user.id
                else f"{target.display_name} isn't"
            )
            await interaction.response.send_message(
                f"{who} in an active Descent fight right now.",
                ephemeral=True,
            )
            return

        result = await self._forfeit_fight(fight, reason="forfeit")
        who = "Your" if target.id == interaction.user.id else f"{target.display_name}'s"
        if result["practice"]:
            detail = "practice — no loss counted"
        elif result["locked"]:
            detail = "3rd loss — 24h lockout"
        else:
            detail = f"loss recorded ({result['losses']}/{MAX_LOSSES})"
        await interaction.response.send_message(
            f"{who} Descent fight is forfeited ({detail}). "
            f"{'You' if target.id == interaction.user.id else 'They'} can `/descend` again.",
            ephemeral=True,
        )

    @app_commands.command(name="descentstatus", description="Your Descent progress: floor, stats, and lockout.")
    async def descentstatus(self, interaction: discord.Interaction):
        if interaction.channel_id not in DESCENT_CHANNEL_IDS:
            await interaction.response.send_message(_descent_channel_hint(), ephemeral=True)
            return
        rec = self.record(interaction.user.id)
        p_hp, p_atk, p_def = player_stats(rec)
        zone = zone_for(rec["floor"])
        now = time.time()

        desc = [f"**Floor {rec['floor']}** — {zone['name']}",
                f"Monster {rec['monster_index']}/{MONSTERS_PER_FLOOR} • {rec['losses']}/{MAX_LOSSES} losses"]
        if rec["locked_until"] > now:
            left = int(rec["locked_until"] - now)
            h, m = left // 3600, (left % 3600) // 60
            desc.append(f"🔒 Locked for {h}h {m}m")
        if rec["highest_cleared"]:
            desc.append(f"Deepest floor cleared: **{rec['highest_cleared']}**")

        army = rec.get("army") or []
        castles = self.bot.get_cog("Castles")
        owned = castles.army_owned_count(interaction.user.id) if castles else len(army)
        if owned:
            counts = army_floor_counts(army)
            floors = sorted(counts)
            band = ", ".join(
                f"F{fl}×{counts[fl]}" for fl in floors[-8:]  # newest-depth slice
            )
            if len(floors) > 8:
                band = "… " + band
            if castles and castles.is_pvp_live():
                from cogs.castles import ARMY_CAP
                size = f"**{owned}/{ARMY_CAP}**"
            else:
                size = f"**{owned}**"
            desc.append(
                f"⚔️ Army: {size} bound"
                + (f" ({band})" if band else "")
            )

        embed = discord.Embed(title=f"{interaction.user.display_name}'s Descent", description="\n".join(desc),
                              color=0x6C5CE7)
        embed.add_field(name="❤️ Max HP", value=str(p_hp))
        embed.add_field(name="⚔️ Attack", value=str(p_atk))
        embed.add_field(name="🛡️ Defense", value=str(p_def))
        embed.add_field(name="⚡ Max AP", value=str(rec["max_ap"]))
        if army:
            last = army[-1]
            embed.add_field(
                name="Last bound",
                value=(
                    f"{last.get('emoji', '')} **{last.get('name', '?')}** "
                    f"(floor {last.get('floor', '?')}) — "
                    f"HP {last.get('hp', '?')} · ATK {last.get('atk', '?')} · "
                    f"DEF {last.get('def', '?')}"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    async def descentreset(self, interaction: discord.Interaction, member: discord.Member = None):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        target = member or interaction.user
        old = self.fights.pop(target.id, None)
        if old is not None:
            old.cancel_idle()
        self.state["players"][str(target.id)] = blank_record()
        self.save()
        who = "Your" if target.id == interaction.user.id else f"{target.display_name}'s"
        await interaction.response.send_message(
            f"{who} Descent progress has been wiped - back to floor 1, monster 1.", ephemeral=True)

    async def descentunlock(self, interaction: discord.Interaction, member: discord.Member = None):
        """Lift the 24h floor lockout and reset the loss counter.

        Keeps floor, monster progress, stats, Max AP, and highest cleared.
        """
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        target = member or interaction.user
        rec = self.record(target.id)
        was_locked = rec.get("locked_until", 0) > time.time()
        prior_losses = int(rec.get("losses", 0) or 0)
        rec["locked_until"] = 0.0
        rec["losses"] = 0
        # Drop a stuck fight so the next /descend starts clean on this floor.
        old = self.fights.pop(target.id, None)
        if old is not None:
            old.cancel_idle()
        self.save()

        who = "Your" if target.id == interaction.user.id else f"{target.display_name}'s"
        if was_locked:
            detail = "24h lockout lifted"
        elif prior_losses:
            detail = f"loss counter cleared ({prior_losses}/{MAX_LOSSES})"
        else:
            detail = "no lockout or losses were active"
        await interaction.response.send_message(
            f"{who} Descent is unlocked — {detail}. "
            f"Still on floor **{rec['floor']}**, monster "
            f"**{rec['monster_index']}/{MONSTERS_PER_FLOOR}**. "
            f"Stats and Max AP untouched.",
            ephemeral=True,
        )

    async def descentboost(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        hp: int = 0,
        attack: int = 0,
        defense: int = 0,
    ):
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if hp == 0 and attack == 0 and defense == 0:
            await interaction.response.send_message(
                "Give at least one of `hp`, `attack`, or `defense` (can be negative to remove).",
                ephemeral=True,
            )
            return

        rec = self.record(member.id)
        pts = rec["stat_points"]
        pts["hp"] = max(0, pts.get("hp", 0) + hp)
        pts["atk"] = max(0, pts.get("atk", 0) + attack)
        pts["def"] = max(0, pts.get("def", 0) + defense)
        # Active fight keeps old stats; drop it so the next /descend uses the boost.
        old = self.fights.pop(member.id, None)
        if old is not None:
            old.cancel_idle()
        self.save()

        p_hp, p_atk, p_def = player_stats(rec)
        await interaction.response.send_message(
            f"Boosted **{member.display_name}**'s Descent stats "
            f"(+{hp} HP pts, +{attack} ATK pts, +{defense} DEF pts).\n"
            f"Now: ❤️ **{p_hp}** · ⚔️ **{p_atk}** · 🛡️ **{p_def}** "
            f"(points: hp={pts['hp']}, atk={pts['atk']}, def={pts['def']}).",
            ephemeral=True,
        )

    async def descentset(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        hp: int,
        attack: int,
        defense: int,
    ):
        """Staff: set combat HP / Attack / Defense to absolute totals in one go."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if hp < BASE_HP or attack < BASE_ATK or defense < BASE_DEF:
            await interaction.response.send_message(
                f"Combat totals can't be below the base "
                f"(❤️ {BASE_HP} · ⚔️ {BASE_ATK} · 🛡️ {BASE_DEF}).",
                ephemeral=True,
            )
            return

        rec = self.record(member.id)
        before = player_stats(rec)
        pts = combat_to_points(hp, attack, defense)
        rec["stat_points"] = pts
        old = self.fights.pop(member.id, None)
        if old is not None:
            old.cancel_idle()
        self.save()

        p_hp, p_atk, p_def = player_stats(rec)
        note = ""
        if (p_hp, p_atk, p_def) != (hp, attack, defense):
            note = (
                f"\n_(Rounded to the nearest achievable totals from point steps: "
                f"+{HP_PER_POINT} HP / +{ATK_PER_POINT} ATK / +{DEF_PER_POINT} DEF.)_"
            )
        await interaction.response.send_message(
            f"Set **{member.display_name}**'s Descent combat stats.\n"
            f"Was: ❤️ **{before[0]}** · ⚔️ **{before[1]}** · 🛡️ **{before[2]}**\n"
            f"Now: ❤️ **{p_hp}** · ⚔️ **{p_atk}** · 🛡️ **{p_def}**"
            f"{note}",
            ephemeral=True,
        )

    def apply_progress_backfill(self, user_id: int) -> dict:
        """Add missing boss trophies + one-clear army credit. Never wipes army."""
        rec = self.record(user_id)
        plan = progress_backfill_plan(rec)
        depth = plan["depth"]
        if depth < 1:
            return {**plan, "bosses_added": 0, "units_added": 0, "army_after": plan["army_before"]}

        bosses_added = 0
        bound = rec.setdefault("bosses_bound", [])
        for fl in plan["bosses_to_add"]:
            if fl not in bound:
                bound.append(fl)
                bosses_added += 1

        units_added = 0
        counts = army_floor_counts(rec.get("army") or [])
        for fl, need in plan["floors_needed"].items():
            have = counts.get(fl, 0)
            for i in range(need):
                monster_index = have + i + 1
                name, emoji, element, kind, _weak, is_boss, m_hp, m_atk, m_def = (
                    self._make_monster(fl, monster_index)
                )
                append_army_unit(
                    rec,
                    name=name,
                    emoji=emoji,
                    element=element,
                    kind=kind,
                    floor=fl,
                    is_boss=is_boss,
                    m_hp=m_hp,
                    m_atk=m_atk,
                    m_def=m_def,
                    backfill=True,
                )
                units_added += 1
            counts[fl] = have + need

        # Keep highest_cleared at least as deep as the backfill credit.
        rec["highest_cleared"] = max(int(rec.get("highest_cleared") or 0), depth)
        self.save()
        return {
            **plan,
            "bosses_added": bosses_added,
            "units_added": units_added,
            "army_after": len(rec.get("army") or []),
            "bosses_after": len(rec.get("bosses_bound") or []),
        }

    @staticmethod
    def estimate_restore_points(depth: int) -> dict[str, int]:
        """Fair stat-point credit for having cleared `depth` floors once.

        Per floor cleared: +1 from monster #5, +1 from floor clear
        (+2 more on Fury boss floors 60/70/80/90/100 where clear pays 3).
        Split evenly across HP / Attack / Defense (remainder → HP).
        """
        depth = max(0, min(MAX_FLOOR, int(depth)))
        if depth <= 0:
            return {"hp": 0, "atk": 0, "def": 0}
        fury = sum(1 for fl in FURY_BOSS_FLOORS if fl <= depth)
        total = depth * 2 + fury * 2
        base, rem = divmod(total, 3)
        return {"hp": base + rem, "atk": base, "def": base}

    def apply_progress_restore(
        self,
        user_id: int,
        depth: int,
        *,
        backfill_army: bool = True,
        force: bool = False,
        set_stats: bool = True,
        set_max_ap: bool = True,
    ) -> dict:
        """Rebuild wiped progress to a cleared depth. Never lowers an existing depth.

        set_stats=False keeps existing combat stat points (or base zeros) so staff
        can assign HP/ATK/DEF with /staffgame descent set.
        set_max_ap=True grants Max AP as 5 + (floors cleared // 10).
        """
        depth = max(0, min(MAX_FLOOR, int(depth)))
        prior = self.state["players"].get(str(user_id))
        prior_depth = 0
        if isinstance(prior, dict):
            prior_depth = max(
                int(prior.get("highest_cleared") or 0),
                max(0, int(prior.get("floor") or 1) - 1),
            )
        if not force and prior_depth >= depth:
            rec = prior if isinstance(prior, dict) else blank_record()
            p_hp, p_atk, p_def = player_stats(rec)
            return {
                "depth": prior_depth,
                "floor": int(rec.get("floor") or 1),
                "max_ap": int(rec.get("max_ap") or STARTING_MAX_AP),
                "stat_points": dict(rec.get("stat_points") or {}),
                "hp": p_hp,
                "atk": p_atk,
                "def": p_def,
                "army": len(rec.get("army") or []),
                "bosses": len(rec.get("bosses_bound") or []),
                "backfill": {"bosses_added": 0, "units_added": 0},
                "skipped": True,
                "reason": f"already at cleared floor {prior_depth}",
            }

        old = self.fights.pop(user_id, None)
        if old is not None:
            old.cancel_idle()
        self.stop_auto(user_id)

        rec = blank_record()
        rec["highest_cleared"] = depth
        rec["floor"] = min(MAX_FLOOR, depth + 1) if depth < MAX_FLOOR else MAX_FLOOR
        rec["monster_index"] = 1
        rec["losses"] = 0
        rec["locked_until"] = 0.0
        if set_max_ap:
            rec["max_ap"] = max_ap_for_depth(depth)
        elif isinstance(prior, dict) and prior.get("max_ap") is not None:
            rec["max_ap"] = max(STARTING_MAX_AP, int(prior.get("max_ap") or STARTING_MAX_AP))
        # else blank_record already has STARTING_MAX_AP
        if set_stats:
            rec["stat_points"] = self.estimate_restore_points(depth)
        elif isinstance(prior, dict) and isinstance(prior.get("stat_points"), dict):
            pts = prior["stat_points"]
            rec["stat_points"] = {
                "hp": max(0, int(pts.get("hp") or 0)),
                "atk": max(0, int(pts.get("atk") or 0)),
                "def": max(0, int(pts.get("def") or 0)),
            }
        rec["pending_statup"] = 0
        if isinstance(prior, dict) and prior.get("army"):
            rec["army"] = list(prior["army"])
            rec["army_seq"] = int(prior.get("army_seq") or len(rec["army"]))
            rec["bosses_bound"] = list(prior.get("bosses_bound") or [])
        self.state["players"][str(user_id)] = rec

        backfill = {"bosses_added": 0, "units_added": 0}
        if backfill_army and depth >= 1:
            backfill = self.apply_progress_backfill(user_id)
        else:
            self.save()

        p_hp, p_atk, p_def = player_stats(rec)
        return {
            "depth": depth,
            "floor": rec["floor"],
            "max_ap": rec["max_ap"],
            "stat_points": dict(rec["stat_points"]),
            "hp": p_hp,
            "atk": p_atk,
            "def": p_def,
            "army": len(rec.get("army") or []),
            "bosses": len(rec.get("bosses_bound") or []),
            "backfill": backfill,
            "skipped": False,
        }
    async def descentrestore(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        floor: int,
        backfill_army: bool = True,
    ):
        """Staff: rebuild wiped Descent progress to a cleared floor depth."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        if floor < 1 or floor > MAX_FLOOR:
            await interaction.response.send_message(
                f"`floor` must be 1–{MAX_FLOOR} (deepest floor they had cleared).",
                ephemeral=True,
            )
            return
        result = self.apply_progress_restore(
            member.id, floor, backfill_army=backfill_army
        )
        pts = result["stat_points"]
        extra = ""
        if backfill_army:
            extra = (
                f" Backfill +{result['backfill'].get('units_added', 0)} units, "
                f"+{result['backfill'].get('bosses_added', 0)} bosses."
            )
        await interaction.response.send_message(
            f"Restored **{member.display_name}** to cleared floor **{result['depth']}** "
            f"(now on floor **{result['floor']}**).\n"
            f"⚡ Max AP **{result['max_ap']}** · "
            f"❤️ **{result['hp']}** · ⚔️ **{result['atk']}** · 🛡️ **{result['def']}** "
            f"(points hp={pts['hp']}, atk={pts['atk']}, def={pts['def']}).\n"
            f"Army **{result['army']}** · bosses bound **{result['bosses']}**."
            f"{extra}",
            ephemeral=True,
        )

    async def descentbackfill(self, interaction: discord.Interaction, member: discord.Member):
        """Staff: add pre-tracking army/boss credit from cleared depth (additive)."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        rec = self.record(member.id)
        plan = progress_backfill_plan(rec)
        if plan["depth"] < 1:
            await interaction.followup.send(
                f"**{member.display_name}** has no cleared Descent depth to backfill "
                f"(highest cleared / floor progress is empty).",
                ephemeral=True,
            )
            return

        result = self.apply_progress_backfill(member.id)

        # Spireheart etc. if boss 90 was just granted.
        adorn_note = ""
        adorn = self.bot.get_cog("Adornments")
        if adorn and result.get("bosses_added"):
            try:
                awarded = await adorn.check_member(member)
                if awarded:
                    from cogs.gear_data import GEAR
                    names = ", ".join(
                        f"**{GEAR[k]['name']}**" for k in awarded if k in GEAR
                    )
                    if names:
                        adorn_note = f"\n🎁 Gear check: {names}"
            except Exception:
                log.exception("Adornments check after Descent backfill failed for %s", member.id)

        await interaction.followup.send(
            f"📜 Backfilled **{member.display_name}** from cleared depth "
            f"**{result['depth']}** — **added** to their army (nothing removed).\n"
            f"• Boss trophies added: **{result['bosses_added']}** "
            f"(now {result['bosses_after']})\n"
            f"• Army units added: **{result['units_added']}** "
            f"(was {result['army_before']} → now {result['army_after']})\n"
            f"Each cleared floor is topped up to **{MONSTERS_PER_FLOOR}** binds "
            f"(one clear's worth); floors already at that count were left alone."
            f"{adorn_note}",
            ephemeral=True,
        )

    def scan_volume_recovery(self) -> list[dict]:
        """Report every Descent state candidate on the volume (path, size, players)."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        seen: set[Path] = set()
        rows: list[dict] = []
        for path in self._candidate_state_paths():
            if path in seen:
                continue
            seen.add(path)
            try:
                exists = path.exists()
            except OSError:
                continue
            if not exists:
                rows.append(
                    {
                        "path": str(path),
                        "exists": False,
                        "bytes": 0,
                        "players": None,
                        "ok": False,
                        "note": "missing",
                    }
                )
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = -1
            data = self._parse_state_file(path)
            if data is None:
                rows.append(
                    {
                        "path": str(path),
                        "exists": True,
                        "bytes": size,
                        "players": None,
                        "ok": False,
                        "note": "empty/corrupt",
                    }
                )
                continue
            n = len(data.get("players") or {})
            rows.append(
                {
                    "path": str(path),
                    "exists": True,
                    "bytes": size,
                    "players": n,
                    "ok": n > 0,
                    "note": "usable" if n > 0 else "0 players",
                    "data": data if n > 0 else None,
                }
            )
        # Also list any other descent_state* we might have missed
        try:
            for path in sorted(STATE_DIR.glob("descent_state*")):
                if path in seen:
                    continue
                seen.add(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    size = -1
                data = self._parse_state_file(path)
                n = len((data or {}).get("players") or {}) if data else None
                rows.append(
                    {
                        "path": str(path),
                        "exists": True,
                        "bytes": size,
                        "players": n,
                        "ok": bool(data and n),
                        "note": "extra" if data and n else "empty/corrupt",
                        "data": data if data and n else None,
                    }
                )
        except OSError:
            pass
        return rows

    def apply_volume_recovery(self) -> dict | None:
        """Reload the richest usable backup into live state. Returns summary or None."""
        rows = self.scan_volume_recovery()
        best = None
        for row in rows:
            if not row.get("ok") or not row.get("data"):
                continue
            n = int(row["players"] or 0)
            if best is None or n > best["players"]:
                best = row
        if best is None:
            return None
        self.state = best["data"]
        self.save()
        return {
            "path": best["path"],
            "players": best["players"],
            "bytes": best["bytes"],
        }

    @staticmethod
    def parse_restore_bulk_text(text: str) -> tuple[list[tuple[int, int]], list[str]]:
        """Parse lines of `user_id floor` or `<@id> floor`. Returns (ok, errors)."""
        import re

        ok: list[tuple[int, int]] = []
        errors: list[str] = []
        text = (text or "").strip()
        if not text:
            return ok, ["No lines provided."]
        # JSON object {"123": 47, ...} or [[id, floor], ...]
        if text[0] in "{[":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as e:
                return ok, [f"Invalid JSON: {e}"]
            if isinstance(payload, dict):
                pairs = list(payload.items())
            elif isinstance(payload, list):
                pairs = []
                for row in payload:
                    if isinstance(row, (list, tuple)) and len(row) >= 2:
                        pairs.append((row[0], row[1]))
                    elif isinstance(row, dict) and "id" in row and "floor" in row:
                        pairs.append((row["id"], row["floor"]))
                    else:
                        errors.append(f"Bad JSON row: {row!r}")
            else:
                return ok, ["JSON must be an object or list."]
            for uid, floor in pairs:
                try:
                    uid_i = int(str(uid).strip())
                    floor_i = int(floor)
                except (TypeError, ValueError):
                    errors.append(f"Bad entry {uid!r} → {floor!r}")
                    continue
                if floor_i < 1 or floor_i > MAX_FLOOR:
                    errors.append(f"{uid_i}: floor {floor_i} out of range")
                    continue
                ok.append((uid_i, floor_i))
            return ok, errors

        mention = re.compile(r"<@!?(\d+)>")
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.replace(",", " ").replace(":", " ")
            m = mention.search(line)
            if m:
                uid_s = m.group(1)
                rest = mention.sub(" ", line).strip()
                parts = rest.split()
                floor_s = parts[0] if parts else ""
            else:
                parts = line.split()
                if len(parts) < 2:
                    errors.append(f"Line {i}: need `user_id floor` — got {line!r}")
                    continue
                uid_s, floor_s = parts[0], parts[1]
            try:
                uid_i = int(uid_s)
                floor_i = int(floor_s)
            except ValueError:
                errors.append(f"Line {i}: bad numbers — {line!r}")
                continue
            if floor_i < 1 or floor_i > MAX_FLOOR:
                errors.append(f"Line {i}: floor {floor_i} out of range")
                continue
            ok.append((uid_i, floor_i))
        return ok, errors

    def _player_snapshot_lines(self, limit: int = 12) -> list[str]:
        players = (self.state or {}).get("players") or {}
        rows = []
        for uid, rec in players.items():
            if not isinstance(rec, dict):
                continue
            cleared = int(rec.get("highest_cleared") or 0)
            floor = int(rec.get("floor") or 1)
            rows.append((cleared, floor, uid))
        rows.sort(reverse=True)
        lines = []
        for cleared, floor, uid in rows[:limit]:
            lines.append(f"• <@{uid}> cleared **{cleared}** (on floor {floor})")
        if len(rows) > limit:
            lines.append(f"• …and {len(rows) - limit} more")
        return lines

    def load_claims(self) -> dict:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if not CLAIMS_PATH.exists():
            return {"claims": {}}
        try:
            data = json.loads(CLAIMS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read Descent restore claims")
            return {"claims": {}}
        if not isinstance(data, dict):
            return {"claims": {}}
        data.setdefault("claims", {})
        if not isinstance(data["claims"], dict):
            data["claims"] = {}
        return data

    def save_claims(self, data: dict) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CLAIMS_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CLAIMS_PATH)

    async def descentrecover(self, interaction: discord.Interaction, apply: bool = True):
        """Staff: scan volume for Descent backups; optionally reload the best one."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        rows = self.scan_volume_recovery()
        live_n = len((self.state or {}).get("players") or {})
        lines = [
            f"**STATE_DIR** `{STATE_DIR}`",
            f"Live memory: **{live_n}** players",
            "",
            "**Files on volume:**",
        ]
        for row in rows:
            if not row["exists"]:
                lines.append(f"• `{Path(row['path']).name}` — missing")
                continue
            players = row["players"]
            ptxt = "corrupt/empty" if players is None else f"{players} players"
            mark = "✅" if row["ok"] else "⚠️"
            lines.append(
                f"• {mark} `{Path(row['path']).name}` — {row['bytes']} bytes, {ptxt}"
            )

        applied = None
        if apply:
            applied = self.apply_volume_recovery()
            if applied:
                self.fights.clear()
                lines.append("")
                lines.append(
                    f"**Recovered** **{applied['players']}** players from "
                    f"`{Path(applied['path']).name}` into live state."
                )
                if applied["players"] < 20:
                    lines.append(
                        "That’s only what’s left on the volume after the wipe — "
                        "not a full historical backup. Everyone else needs "
                        "`/descentclaim` → `/staffgame descent approveclaims`, "
                        "or `/staffgame descent restorebulk`."
                    )
            else:
                lines.append("")
                lines.append(
                    "**No usable backup found.** Exact stats are gone. "
                    "Have players `/descentclaim floor:<n>`, then "
                    "`/staffgame descent approveclaims`."
                )
        else:
            lines.append("")
            lines.append("Scan only (apply=False). Re-run with apply=True to reload.")

        snap = self._player_snapshot_lines()
        if snap:
            lines.append("")
            lines.append("**Who’s in the ledger now:**")
            lines.extend(snap)

        msg = "\n".join(lines)
        if len(msg) > 1900:
            msg = msg[:1900] + "\n…"
        await interaction.followup.send(msg, ephemeral=True)
    async def descentrestorebulk(
        self,
        interaction: discord.Interaction,
        mapping: str,
        backfill_army: bool = True,
    ):
        """Staff: restore many players from a paste of user_id + floor."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        entries, errors = self.parse_restore_bulk_text(mapping)
        if not entries and errors:
            await interaction.followup.send(
                "Could not parse mapping:\n" + "\n".join(f"• {e}" for e in errors[:15]),
                ephemeral=True,
            )
            return

        restored = 0
        skipped = 0
        for uid, floor in entries:
            result = self.apply_progress_restore(uid, floor, backfill_army=backfill_army)
            if result.get("skipped"):
                skipped += 1
            else:
                restored += 1

        summary = [
            f"Restored **{restored}** players "
            f"(fair Max AP + stats"
            f"{' + army/boss backfill' if backfill_army else ''})"
            + (f"; skipped **{skipped}** already at/above claim." if skipped else ".")
        ]
        if errors:
            summary.append(f"Skipped/errors ({len(errors)}):")
            summary.extend(f"• {e}" for e in errors[:12])
            if len(errors) > 12:
                summary.append(f"• …and {len(errors) - 12} more")
        sample = ", ".join(f"`{u}`→F{f}" for u, f in entries[:8])
        if sample:
            summary.append(f"Sample: {sample}" + ("…" if len(entries) > 8 else ""))
        await interaction.followup.send("\n".join(summary), ephemeral=True)

    @app_commands.command(
        name="descentclaim",
        description="After a wipe: report the deepest Descent floor you had cleared.",
    )
    @app_commands.describe(floor="Deepest floor you had cleared (1–100)")
    async def descentclaim(
        self,
        interaction: discord.Interaction,
        floor: app_commands.Range[int, 1, 100],
    ):
        data = self.load_claims()
        uid = str(interaction.user.id)
        data["claims"][uid] = {
            "floor": int(floor),
            "name": interaction.user.display_name,
            "at": time.time(),
        }
        self.save_claims(data)
        await interaction.response.send_message(
            f"Claimed deepest cleared floor **{floor}**. "
            f"Staff will approve with `/staffgame descent approveclaims` "
            f"(restores floor, army, and Max AP at +1 per 10 floors — "
            f"combat stats are set by staff separately).",
            ephemeral=True,
        )

    async def descentclaims(self, interaction: discord.Interaction):
        """Staff: list pending wipe-restore claims."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        data = self.load_claims()
        claims = data.get("claims") or {}
        if not claims:
            await interaction.response.send_message(
                "No pending `/descentclaim` entries.",
                ephemeral=True,
            )
            return
        rows = sorted(
            claims.items(),
            key=lambda kv: int((kv[1] or {}).get("floor") or 0),
            reverse=True,
        )
        lines = [f"**{len(rows)} pending claims:**"]
        for uid, info in rows[:40]:
            fl = int((info or {}).get("floor") or 0)
            name = (info or {}).get("name") or "?"
            lines.append(f"• <@{uid}> ({name}) → floor **{fl}**")
        if len(rows) > 40:
            lines.append(f"• …and {len(rows) - 40} more")
        lines.append("Approve all with `/staffgame descent approveclaims`.")
        msg = "\n".join(lines)
        if len(msg) > 1900:
            msg = msg[:1900] + "\n…"
        await interaction.response.send_message(msg, ephemeral=True)

    def apply_fix_all_max_ap(self, *, only_wrong: bool = True) -> dict:
        """Set every player's Max AP from cleared depth (5 + floors//10)."""
        players = (self.state or {}).get("players") or {}
        changed = 0
        checked = 0
        samples: list[str] = []
        for uid, rec in players.items():
            if not isinstance(rec, dict):
                continue
            checked += 1
            depth = cleared_depth(rec)
            target = max_ap_for_depth(depth)
            current = int(rec.get("max_ap") or STARTING_MAX_AP)
            if only_wrong and current == target:
                continue
            rec["max_ap"] = target
            rec.setdefault("highest_cleared", 0)
            # Keep highest_cleared at least as deep as we used for the formula.
            rec["highest_cleared"] = max(int(rec.get("highest_cleared") or 0), depth)
            changed += 1
            if len(samples) < 8:
                samples.append(f"<@{uid}> F{depth}: {current}→{target}")
        if changed:
            self.save()
        return {
            "checked": checked,
            "changed": changed,
            "samples": samples,
        }

    async def descentfixap(
        self,
        interaction: discord.Interaction,
        only_wrong: bool = True,
    ):
        """Staff: correct Max AP for everyone from cleared floors (+1 per 10)."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = self.apply_fix_all_max_ap(only_wrong=only_wrong)
        lines = [
            f"Fixed Max AP for **{result['changed']}** / {result['checked']} players "
            f"(rule: **5 + floors÷10**)."
        ]
        if result["samples"]:
            lines.append("Sample:")
            lines.extend(f"• {s}" for s in result["samples"])
            if result["changed"] > len(result["samples"]):
                lines.append(f"• …and {result['changed'] - len(result['samples'])} more")
        if result["changed"] == 0:
            lines.append("Everyone already matched the rule.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    async def descentapproveclaims(
        self,
        interaction: discord.Interaction,
        backfill_army: bool = True,
        clear: bool = True,
    ):
        """Staff: restore floors/army + Max AP (+1/10 floors); leave combat stats alone."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        data = self.load_claims()
        claims = data.get("claims") or {}
        if not claims:
            await interaction.followup.send("No pending claims.", ephemeral=True)
            return
        restored = skipped = 0
        for uid, info in claims.items():
            try:
                uid_i = int(uid)
                floor = int((info or {}).get("floor") or 0)
            except (TypeError, ValueError):
                continue
            if floor < 1:
                continue
            result = self.apply_progress_restore(
                uid_i,
                floor,
                backfill_army=backfill_army,
                set_stats=False,
                set_max_ap=True,
            )
            if result.get("skipped"):
                skipped += 1
            else:
                restored += 1
        if clear:
            data["claims"] = {}
            self.save_claims(data)
        await interaction.followup.send(
            f"Approved claims: restored **{restored}** "
            f"(floor / army / Max AP at +1 per 10 floors — **combat stats untouched**), "
            f"skipped **{skipped}** already at/above their claim"
            + (" (claim list cleared)." if clear else " (claims kept).")
            + "\nSet combat stats with `/staffgame descent set`.",
            ephemeral=True,
        )

    async def descentrestorespire(
        self,
        interaction: discord.Interaction,
        floor: int = 90,
        backfill_army: bool = True,
    ):
        """Staff: restore everyone who owns Spireheart to at least `floor`."""
        store = self.bot.get_cog("Store")
        if not (store and store.is_staff(interaction.user)):
            await interaction.response.send_message("That's for staff.", ephemeral=True)
            return
        adorn = self.bot.get_cog("Adornments")
        if not adorn:
            await interaction.response.send_message(
                "Adornments isn't loaded — can't see Spireheart owners.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        floor = max(1, min(MAX_FLOOR, int(floor)))
        owners = []
        for uid, rec in (adorn.state.get("members") or {}).items():
            owned = (rec or {}).get("owned") or {}
            if "spireheart_necklace" in owned:
                try:
                    owners.append(int(uid))
                except ValueError:
                    continue
        if not owners:
            await interaction.followup.send(
                "Nobody in adornments owns Spireheart.",
                ephemeral=True,
            )
            return
        restored = skipped = 0
        for uid in owners:
            result = self.apply_progress_restore(
                uid, floor, backfill_army=backfill_army
            )
            if result.get("skipped"):
                skipped += 1
            else:
                restored += 1
        await interaction.followup.send(
            f"Spireheart owners: restored **{restored}** to floor **{floor}**, "
            f"skipped **{skipped}** already at/above that depth "
            f"({len(owners)} owners total).",
            ephemeral=True,
        )
