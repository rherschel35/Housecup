"""
Wizard duels. Best of three by default. Optional best-of 5/7/9/51 series:
each set is first to 2, and the series winner banks 3/4/5/26 wins. Sets the
loser takes in that series still fill their daily duel checklist / house
points (same daily cap as a normal duel).

    /duel @member              - challenge someone (1v1)
    /duelrecord [member]       - rank, wins, streak, rivals, trio/grand, points
    /duelend [member]          - clear a stuck duel lock (self, or staff for others)
    /houseduels                - each house's overall duel wins
    /staff duels night start|end  - House Duel Night (30 min or 1 hour)
    /staff duels nightschedule    - 1–3 weekly nights (30m/1h), each with its own time
    /trio scramble             - open 3v3 signup (any houses)
    /trio housematch h1 h2     - house-gated 3v3 signup
    /grand @member             - Grand Duel (both need 50+ 1v1 wins)

Rewards: ranks by total wins, hidden Dueling Circle reputation (flourish,
wand glow, one-time gift), win streaks with bounties, rivals, stacked
signature-spell titles, trio/grand records and titles, and a weekly
Duelist of the Week (role + house points). Only wins, ranks, streaks and
titles are ever shown - never a win rate.

Five spells. Each beats exactly two others and loses to the other two, so
there is no safe pick - only reading your opponent. Both duelists open a
private cast board (spell buttons under the board, like broom races), then
both spells are revealed at once.

The winner's house earns 1 point, up to 3 duel points per person per day.
Duels past that still count toward the win/loss record. Duels between two
members of the SAME house never award points - otherwise two housemates
could simply trade wins and mint points for their house. Same-house wins
also never grow a win streak or pay a streak-bounty bonus.

House Duel Night doubles every cross-house win with no daily cap, tracks a
night tally, and when it ends crowns the winning house (plus an MVP bonus)
the same way a Wild Threat Attack tallies up.

Wands are cosmetic: they're shown in the duel, but they don't affect it.

Cast resolution mutates state under a lock, then publishes the board *after*
releasing it. A board generation counter drops superseded edits so a slow
"waiting on…" update can't overwrite a round that already resolved — the
usual "I cast but it's still waiting on me / frozen" failure when both
sides click fast.
"""

from discord.ext import commands

from cogs.duels.rules import (
    ACCEPT_TIMEOUT,
    ANNOUNCE_RANKS_FROM,
    BEATS,
    BEST_OF_OPTIONS,
    BOUNTY_AT,
    BOUNTY_POINTS,
    CHAMPION_ROLE_NAME,
    COMMUNITY_PING_ROLE_NAMES,
    DATA_DIR,
    DUELS_PATH,
    DUEL_BOUNTY_POINTS,
    DUEL_CHAMPION_ROLE_NAME_DEFAULT,
    DUEL_GIFT_POINTS,
    DUEL_GRAND_REWARDED_PER_DAY,
    DUEL_GRAND_REWARD_POINTS,
    DUEL_NIGHT_COLOR,
    DUEL_NIGHT_PRESETS,
    DUEL_NIGHT_SCHEDULE_MAX,
    DUEL_NIGHT_WARN_MINUTES,
    DUEL_REWARDED_PER_DAY,
    DUEL_REWARD_POINTS,
    DUEL_RIVAL_BONUS,
    DUEL_TRIO_HOUSE_COMPLETION_POINTS,
    DUEL_TRIO_HOUSE_WIN_GOAL,
    DUEL_TRIO_REWARD_POINTS,
    DUEL_WEEKLY_POINTS,
    FAVORED_ENABLED,
    FAVORED_ROUND_BIAS,
    FAVORED_USER_ID,
    FLOURISHES,
    GIFT_POINTS,
    GRAND_LOCK_TIMEOUT,
    GRAND_MIN_WINS,
    GRAND_PLAY_DELAY,
    GRAND_REWARDED_PER_DAY,
    GRAND_REWARD_POINTS,
    GRAND_RIVAL_BOARD_BANTER,
    GRAND_RIVAL_TITLE_TIERS,
    GRAND_SLOTS,
    GRAND_THEATRE,
    GRAND_TITLE_TIERS,
    GRAND_TO_WIN,
    MAX_ROUNDS,
    RANKS,
    REP_FLOURISH,
    REP_GIFT,
    REP_GLOW,
    REP_LOSS,
    REP_WIN,
    REWARDED_PER_DAY,
    REWARD_POINTS,
    RIVAL_AFTER,
    RIVAL_BOARD_BANTER,
    RIVAL_BONUS,
    RIVAL_TITLE_TIERS,
    ROOT_DIR,
    ROUNDS_TO_WIN,
    ROUND_TIMEOUT,
    SIGNATURE_AT,
    SIGNATURE_TIERS,
    SIGNATURE_TITLES,
    SPELLS,
    STATE_DIR,
    STREAK_ANNOUNCE,
    STREAK_EVERY,
    TRIO_HOUSE_COMPLETION_POINTS,
    TRIO_HOUSE_WIN_GOAL,
    TRIO_REWARDED_PER_DAY,
    TRIO_REWARD_POINTS,
    TRIO_ROUNDS_TO_WIN,
    TRIO_SIGNUP_TIMEOUT,
    TRIO_SIZE,
    TRIO_TITLE_TIERS,
    TZ,
    WEEKDAY_CHOICES,
    WEEKDAY_LABELS,
    WEEKLY_POINTS,
    WINDOW,
    ZoneInfo,
    _DEFAULT_NIGHT_PING_NAMES,
    _chicago_now,
    _night_ping_role_names,
    _raw_favored,
    apply_deadlock_keep,
    apply_liquid_luck_tie,
    apply_tie_breakers,
    favor_display_spells,
    highest_title,
    log,
    max_rounds_for,
    pair_key,
    rank_for,
    rank_index,
    resolve,
    rival_banter_line,
    series_to_win_for,
    signature_unlocks_for,
    titles_at_or_below,
    week_key,
)

from cogs.duels.cog import (
    DUEL_NIGHT_HOUSE_BONUS,
    DUEL_NIGHT_MULTIPLIER,
    DUEL_NIGHT_MVP_BONUS,
    Duels,
    EVENT_ANNOUNCE_CHANNEL_ID,
)

from cogs.duels.match import (
    AcceptView,
    Duel,
    OpenCastBoardView,
    PrivateSpellView,
    SpellButton,
)

from cogs.duels.trio import (
    OpenTrioCastBoardView,
    TrioJoinButton,
    TrioMatch,
    TrioPrivateSpellView,
    TrioSignup,
    TrioSignupView,
    TrioSpellButton,
)

from cogs.duels.grand import (
    GrandAcceptView,
    GrandDuel,
    GrandLockPromptView,
    GrandSeqSpellButton,
    GrandSeqSubmitButton,
    GrandSeqUndoButton,
    GrandSequenceView,
    GrandSuddenPrivateSpellView,
    GrandSuddenSpellButton,
    OpenGrandSuddenBoardView,
)


async def setup(bot: commands.Bot):
    await bot.add_cog(Duels(bot))
