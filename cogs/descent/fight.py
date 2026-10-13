"""One in-progress Descent encounter."""

import asyncio
import discord

from cogs.descent.rules import (
    DEFEND_DMG_MULT,
    ELEMENT_EMOJI,
    ENDGAME_FLOOR_START,
    IDLE_TIMEOUT,
    MONSTERS_PER_FLOOR,
    RAGE_DMG_MULT,
    RAGE_FLOOR_START,
    RAGE_SECOND_WIND_HEALS,
    SECOND_WIND_PCT,
    SHADOW_EMOJI,
    SHADOW_NAME,
    VAULT_ETERNAL_DMG_MULT,
    VAULT_ETERNAL_SECOND_WIND_HEALS,
    bar,
    stamp_owner,
)

class Fight:
    """One in-progress monster encounter. Purely in-memory - like duels,
    an interrupted fight (e.g. a bot restart) just needs restarting via
    /descend rather than surviving forever."""

    def __init__(self, user_id: int, floor: int, monster_index: int, is_boss: bool,
                 name: str, emoji: str, element: str, kind: str, weak: list[str],
                 m_hp: int, m_atk: int, m_def: int, p_hp: int, p_atk: int, p_def: int,
                 ap_max: int, is_practice: bool = False):
        self.user_id = user_id
        self.floor = floor
        self.monster_index = monster_index
        self.is_boss = is_boss
        # Endgame (90–100): shown as a shadow until the player attacks.
        self.true_name = name
        self.true_emoji = emoji
        self.shadowed = floor >= ENDGAME_FLOOR_START
        self.revealed = not self.shadowed
        if self.shadowed:
            self.name = SHADOW_NAME
            self.emoji = SHADOW_EMOJI
        else:
            self.name = name
            self.emoji = emoji
        self.element = element
        self.kind = kind
        self.weak = weak
        self.m_hp_max = self.m_hp = m_hp
        self.m_atk = m_atk
        self.m_def = m_def
        self.p_hp_max = self.p_hp = p_hp
        self.p_atk = p_atk
        self.p_def = p_def
        self.ap_max = self.ap = ap_max
        self.is_practice = is_practice
        self.round = 0
        self.log: list[str] = []
        # Regular floors 51+: first monster swing is Fury, then alternate.
        # Toggled by fury_hit_count; unused on floors without alternating Fury.
        self.fury_next = True
        # potion buffs - defaults are "no effect"; _apply_potion_mods and
        # _start_fight may override these right after construction
        self.heal_mult = 1.0
        self.defend_mult = DEFEND_DMG_MULT
        self.rest_no_penalty = False
        self.reveal_weakness = False
        self.boss_dmg_mult = 1.0
        self.revive_available = False
        # Floors 95+: outgoing damage spike + second-wind full heals at ≤20% HP.
        if floor >= RAGE_FLOOR_START and is_boss and floor == 100:
            self.monster_dmg_mult = VAULT_ETERNAL_DMG_MULT
            self.second_wind_left = VAULT_ETERNAL_SECOND_WIND_HEALS
        elif floor >= RAGE_FLOOR_START:
            self.monster_dmg_mult = RAGE_DMG_MULT
            self.second_wind_left = RAGE_SECOND_WIND_HEALS
        else:
            self.monster_dmg_mult = 1.0
            self.second_wind_left = 0
        self.message: Optional[discord.Message] = None
        self.oneshot_message: Optional[discord.Message] = None
        self.auto = False  # chain into the next monster after a win
        self._idle_task: Optional[asyncio.Task] = None
        self._idle_gen = 0

    def reveal_identity(self) -> str | None:
        """Peel the shadow veil after the first attack. Returns a log line or None."""
        if not self.shadowed or self.revealed:
            return None
        self.revealed = True
        self.name = self.true_name
        self.emoji = self.true_emoji
        return f"🌑 The shadow peels back — it's **{self.true_name}**!"

    def try_second_wind(self) -> str | None:
        """Full heal when brought to ≤20% HP, if any second-wind charges remain.

        Lethal overkill (HP hits 0) still wins the fight — this only fires while
        the monster is still standing at or under the threshold.
        """
        if self.second_wind_left <= 0 or self.m_hp <= 0:
            return None
        if self.m_hp > self.m_hp_max * SECOND_WIND_PCT:
            return None
        self.second_wind_left -= 1
        self.m_hp = self.m_hp_max
        left = self.second_wind_left
        charges = f"{left} left" if left else "spent"
        return (
            f"{self.emoji} {self.name} draws a **second wind** — HP restored to "
            f"**{self.m_hp_max}** ({charges})!"
        )

    def cancel_idle(self) -> None:
        self._idle_gen += 1
        task = self._idle_task
        self._idle_task = None
        if task is not None and not task.done():
            task.cancel()

    def arm_idle(self, cog: "Descent") -> None:
        """Restart the 3-minute no-press timer (counts as a loss when it fires)."""
        self.cancel_idle()
        gen = self._idle_gen
        self._idle_task = asyncio.create_task(
            cog._idle_timeout(self, gen),
            name=f"descent-idle-{self.user_id}",
        )

    def embed(self, member: discord.Member) -> discord.Embed:
        title = f"{self.emoji} Floor {self.floor} — {self.name}"
        if self.is_boss:
            title += " (Boss)"
        elif self.is_practice:
            title += " (Practice)"
        desc = (
            f"Monster {self.monster_index}/{MONSTERS_PER_FLOOR}"
            if not self.is_practice
            else "Practice fight — no floor progress at stake"
        )
        if self.auto:
            desc += "\n⚡ Auto-play on — next monster posts after a win."
        desc += f"\n⏱️ Idle **{IDLE_TIMEOUT // 60} min** with no button press = loss."
        e = discord.Embed(
            title=title,
            description=desc,
            color=0x8B5FBF if not self.is_boss else 0xE0A526,
        )
        stamp_owner(e, member)
        e.set_image(url="attachment://monster.png")
        e.add_field(
            name=f"{member.display_name}'s HP",
            value=(f"{bar(self.p_hp, self.p_hp_max)} {self.p_hp}/{self.p_hp_max}\n"
                   f"⚡ {self.ap}/{self.ap_max} AP"),
            inline=False,
        )
        e.add_field(name=self.name, value=f"{bar(self.m_hp, self.m_hp_max)} {self.m_hp}/{self.m_hp_max}",
                    inline=False)
        if self.reveal_weakness and self.weak:
            e.add_field(name="🦉 Owl's Eye", value=f"Weak to {' / '.join(ELEMENT_EMOJI[w] for w in self.weak)}",
                       inline=False)
        if self.log:
            e.add_field(name="Last round", value="\n".join(self.log[-2:]), inline=False)
        return e
