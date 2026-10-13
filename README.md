# Housecup

Housecup is the Velmora House Cup Discord bot. It keeps the house-points ledger and runs the school's games: duels, Descent, wild threats, the marketplace, familiars, bingo, and the rest of the slash-command tree. `bot.py` loads the cog modules and syncs slash commands with Discord. Railway deploys whatever is on `main`.

## Layout

- `bot.py` - process entry, cog load order, and slash-command sync.
- `cogs/` - one module per feature (points, duels, world, dementors, and so on), plus shared helpers such as `staff_groups.py`.
- `data/` - static writing shipped in git (places, quests, fonts, course lists). Runtime state defaults to this directory when `STATE_DIR` is unset.
- `scripts/` - local checks: module import, the 100-command slash budget, and each command's JSON payload size.
- `compendium_site/` - the separate compendium site. It is not part of the bot process.

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`python-dotenv` loads a `.env` file from the repo root if one is present. The process will not start without `DISCORD_TOKEN`.

```bash
python bot.py
```

Leave `STATE_DIR` unset for a local trial and the bot writes state under `data/`. Those JSON files are runtime data. Do not commit them.

## Environment variables

Names only. Set them in the shell, a local `.env` that stays untracked, or the Railway service variables. Never commit values.

| Name | Role |
| --- | --- |
| `DISCORD_TOKEN` | Bot token. Required to run. |
| `DEV_GUILD_ID` | Optional guild id for instant command sync while testing. |
| `ALLOWED_GUILD_IDS` | Optional comma-separated guild ids. The bot leaves any other server. |
| `FORCE_COMMAND_SYNC` | Set to `1` to push slash commands even when the tree is unchanged. |
| `COMMAND_SYNC_TIMEOUT` | Seconds to wait on a Discord sync call. |
| `STATE_DIR` | Directory for runtime JSON and downloaded wizard packs. |
| `ANTHROPIC_API_KEY` | Optional. Wand, broom, patronus, and Animagus text fall back without it. |
| `WAND_MODEL` | Model name for wand text. |
| `BROOM_MODEL` | Model name for broom text. |
| `PATRONUS_MODEL` | Model name for patronus text. |
| `ANIMAGUS_MODEL` | Model name for Animagus text. |
| `BROOM_RESERVED` | Optional reserved broom names. |
| `WIZARD_PACK_FEMALE` | Optional URL for the feminine sprite pack. |
| `WIZARD_PACK_FEMALE_UPDATE` | Optional follow-up URL for that pack. |
| `WIZARD_PACK_FEMALE_FULL` | Optional URL for the full-body feminine pack. |
| `WIZARD_PACK_FEMALE_FULL_UPDATE` | Optional follow-up URL for that pack. |
| `WIZARD_PACK_MALE` | Optional URL for the masculine sprite pack. |
| `WIZARD_PACK_MALE_UPDATE` | Optional follow-up URL for that pack. |
| `WIZARD_ASSETS_DIR` | Override for where sprite packs are unpacked. |
| `HOUSE_CRESTS_DIR` | Override for house crest art. Crests otherwise ship in the repo. |
| `GARDEN_CHANNEL_ID` | Optional channel id for the Garden. |
| `RUMOR_CHANNEL_ID` | Channel id for rumors. |
| `RUMOR_INTERVAL_HOURS` | Hours between rumors. |
| `EVENT_ANNOUNCE_CHANNEL_ID` | Channel id for event announcements. |
| `EVENT_BOARD_CHANNEL_ID` | Channel id for the living event board. |
| `DUEL_CHANNEL_ID` | Channel id for the duel arena. |
| `DUEL_CHAMPION_ROLE_NAME` | Role name for the duel champion. |
| `DUEL_NIGHT_PING_ROLE_NAMES` | Comma-separated role names pinged for Duel Night. |
| `ATTACK_PING_ROLE_NAMES` | Comma-separated role names pinged for Attack on Velmora. |
| `BEAST_CHANNEL_ID` | Channel id for beast sightings. |
| `BROKE_REPORT_CHANNEL_ID` | Channel id for broke reports. |
| `FIRE_STOKE_THRESHOLD` | Stoke count for the house fire. |
| `FIRE_WINNER_PTS` | Points awarded for the house fire. |
| `FROG_MYTHIC_ODDS` | Denominator for the mythic Chocolate Frog card. |
| `CAST_AUTO_USER_ID` | Optional user whose `/cast` skips the spell buttons. |
| `DESCENT_ONESHOT_USER_ID` | Optional user who gets the Descent one-shot button. |
| `ANIMAGUS_FORCE_USER_ID` | Optional Animagus override user. |
| `ANIMAGUS_FORCE_ANIMAL` | Optional forced Animagus animal. |
| `ANIMAGUS_FORCE_ART` | Optional forced Animagus art filename. |
| `DUEL_FAVORED_USER_ID` | Optional duel setting. |
| `DUEL_FAVORED_BIAS` | Optional duel setting. |

## Railway

Railway builds this repo and deploys on every push to `main`. The service start command is `python bot.py`.

Put `DISCORD_TOKEN` and any other variables above in the Railway service variables, not in git.

Attach a volume and set `STATE_DIR` to that volume's mount path. The points ledger, game progress, and command-sync fingerprint live there. A redeploy without a volume (or with `STATE_DIR` left at the default `data/` directory inside the container) starts from empty state.

Slash-command sync talks to Discord only when the command tree actually changed. Restarts from a new deploy skip the upload when the fingerprint matches, so they do not burn Discord's daily command-create budget. `FORCE_COMMAND_SYNC=1` forces a sync anyway.

On boot, if the wizard sprite packs are missing from the volume, the bot can download them into `STATE_DIR/wizard_assets/` using the `WIZARD_PACK_*` URLs. Those packs are not in git.

## Data storage

Runtime files are written under `STATE_DIR`. The bot creates a file when that feature first saves. A missing file starts empty.

- `points_store.json` - house-points ledger and totals
- `command_sync.json` - slash-command sync fingerprint
- `command_usage.json` - slash-command usage counts
- `world_state.json` - satchels, place reputation, academy flags
- `dementors.json` - wild threats and Attack schedule
- `duels.json` - duel records and Duel Night
- `descent_state.json` - Descent progress
- `descent_restore_claims.json` - pending Descent restore claims
- `descent_state.corrupt.*.json` - backups kept when a Descent save could not be read
- `castles_state.json` - castle ownership and sieges
- `threeraid_state.json` - 3Raid
- `quidditch_state.json` - Quidditch
- `marketplace.json` - marketplace prices and sell caps
- `hexes_state.json` - active hexes
- `bingo_state.json` - bingo rounds
- `frogs_state.json` - Chocolate Frog albums
- `familiars.json` - familiars
- `wands.json`, `brooms.json`, `patronus.json`, `animagus.json` - identity
- `potions_state.json`, `beans.json`, `beasts_state.json`, `adornments_state.json`
- `house_fire_state.json`, `event_board.json`, `rumor_state.json`
- `challenges.json`, `countdown.json`, `checklist_state.json`, `triwizard.json`
- `reaction_roles.json`, `antispam.json`
- `broom_races.json`
- `wizard_assets/` - sprite packs unpacked on the volume (not in git)

Place writing, quests, and fonts stay in the git `data/` tree and are read from the image, not from the volume.
