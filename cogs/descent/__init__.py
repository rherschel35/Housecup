"""
The Descent - a 100-floor solo dungeon crawl.

    /descend             - fight the next monster on your current Descent floor
    /descend auto:True   - keep posting the next monster after each win
    /descend floor:<n>   - replay a floor you've already cleared, for practice/loot
    /descendend [member] - forfeit (counts as a loss) so you can /descend again;
                           idle fights also auto-forfeit after 3 minutes
    /descentstatus        - your floor, stats, AP, and lockout status
    /staff descent unlock - clear the 3-loss lockout without wiping progress
    /staffgame descent backfill - add missing army + bosses from cleared depth
      (additive — never replaces an existing army)
    /staffgame descent restore - rebuild wiped progress to a cleared floor
      (depth, Max AP, fair stat points, optional army/boss backfill)
    /staffgame descent recover - scan the volume for descent_state backups
      and reload everyone if a good copy still exists
    /staffgame descent restorebulk - paste many "user_id floor" lines at once
    /descentclaim floor:<n> - players self-report deepest cleared floor after a wipe
    /staffgame descent claims - list pending claims
    /staffgame descent approveclaims - restore floors/army + Max AP (+1 per 10 floors); stats untouched
    /staffgame descent fixap - set every player's Max AP from cleared floors (+1 per 10)
    /staffgame descent restorespire - restore Spireheart owners to at least floor 90
    /staffgame descent set - set a player's combat HP / Attack / Defense in one go

Ten zones of ten floors each, one primary element per zone plus a second
element mixed in (about 30% of non-boss monsters), so a floor is never a
single-element gimme. Every floor is a gauntlet of 10 monsters fought in
order:

    - Win all 10 -> advance a floor and level up (level = deepest floor
      cleared): a stat point (HP/Attack/Defense) at monster #5, and your
      max AP goes up by 1 every 10 floors cleared (floors 10, 20, … 100).
    - Lose a fight -> refight that same monster; you don't lose progress
      on the floor for a single loss.
    - Lose 3 times on the same floor -> locked out of it for 24 hours.
      When the lockout clears you restart that floor at monster #1.
    - Every 10th floor (10, 20, ... 100) ends in a boss: tougher, weak to
      two elements instead of one, and worth a floor-clear bonus.
    - Floors 90–100: every monster strike is a Fury (6 hits; floor-100 boss
      is 10), every foe has 8k+ HP (95–100 pin to 13k; Vault Eternal is 20k),
      and each fight starts as a Shadow Remnant until you attack and peel
      the veil. Floors 95+: monsters deal 3× damage (Vault Eternal 5×) and
      fully heal once at ≤20% HP (Vault Eternal twice).

Combat runs on an AP (action point) economy, not just "pick a spell every
round": you start each fight with your current max AP (and full HP),
gaining 1 AP back automatically each round. Actions:

    - Cast a named spell (3 AP) - each tied to an element (the emoji on
      the button says which). A monster's home element resists that same
      element (half damage) and is weak to one other (double damage) -
      neither is shown up front, so you learn it by testing spells.
    - Strike (free) - a guaranteed, unglamorous hit for resisted-tier
      damage. Always available even at 0 AP.
    - Heal (3 AP) - restore half your missing HP, but you're not
      defending yourself that round.
    - Defend (1 AP) - deal no damage, but halve the monster's hit back.
    - Rest (free) - fully refill your AP, but you take 10% extra damage
      that round for being unguarded.
    - One-shot (Headmaster only) - ephemeral private button that instantly
      wins with full loot / floor / trophy rewards. Normal actions stay on
      the public board; spectators never see One-shot.

Floor replay: once you've cleared a floor, `/descend floor:<n>` lets you
refight a single monster from it any time (not boss floors) - for loot,
or to grind. Loot always has a chance to drop. Stat-point odds on
practice clears scale with how far behind you are: close floors are more
likely to sharpen you, deep-behind floors still have a small chance.
Practice fights ignore the floor-clear path and never count toward lockouts.

Every monster beaten (real or practice) is bound into your army ledger with
its floor and combat stats — foundation for army PVP.
"""

from discord.ext import commands

from cogs.descent.rules import (
    AP_PER_TEN_FLOORS,
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
    DAMAGE_VARIANCE,
    DATA_DIR,
    DEFEND_AP_COST,
    DEFEND_DMG_MULT,
    DEF_MITIGATION_K,
    DEF_PER_POINT,
    DESCENT_CHANNEL_IDS,
    DESCENT_HOME_CHANNEL_IDS,
    DESCENT_ONESHOT_USER_ID,
    ELEMENTS,
    ELEMENT_EMOJI,
    ELEMENT_NAME,
    ENDGAME_F100_BOSS_FURY,
    ENDGAME_F100_BOSS_HP,
    ENDGAME_FLOOR_START,
    ENDGAME_FURY_HITS,
    ENDGAME_MIN_HP,
    FLOOR_CLEAR_GUARANTEED,
    FURY_BOSS_FLOORS,
    FURY_BOSS_HITS,
    FURY_REGULAR_HITS,
    FURY_START_FLOOR,
    HEADMASTER_USER_ID_DEFAULT,
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
    PRACTICE_STATUP_CLOSE,
    PRACTICE_STATUP_FAR,
    PRACTICE_STATUP_MID,
    RAGE_DMG_MULT,
    RAGE_FLOOR_START,
    RAGE_PIN_HP,
    RAGE_SECOND_WIND_HEALS,
    REST_DMG_MULT,
    ROOT_DIR,
    SECONDARY_CHANCE,
    SECONDARY_ELEMENT,
    SECOND_WIND_PCT,
    SHADOW_EMOJI,
    SHADOW_IMAGE,
    SHADOW_NAME,
    SPELL_NAME,
    SPIREHEART_PERK,
    SPIREHEART_STAT_CHANCE,
    SPIREHEART_STAT_FLOOR,
    STARTING_MAX_AP,
    STATE_DIR,
    STATE_PATH,
    STATUP_AT_MONSTER,
    STRIKE_MULT,
    VAULT_ETERNAL_DMG_MULT,
    VAULT_ETERNAL_SECOND_WIND_HEALS,
    WEAK_TO,
    WILD_THREAT_ASSETS,
    ZONES,
    ZONE_ITEM,
    _descent_channel_hint,
    _raw_oneshot,
    append_army_unit,
    army_floor_counts,
    bar,
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
    zone_index,
)

from cogs.descent.fight import Fight

from cogs.descent.views import (
    DefendButton,
    ElementButton,
    FightView,
    HealButton,
    OneShotButton,
    OneShotView,
    RestButton,
    StatButton,
    StatUpView,
    StopAutoButton,
    StrikeButton,
)

from cogs.descent.cog import Descent


async def setup(bot: commands.Bot):
    await bot.add_cog(Descent(bot))
