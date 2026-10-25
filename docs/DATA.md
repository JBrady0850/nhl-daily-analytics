# The data file

The workflow writes one file a day, `app/data/latest.json`, and publishes it with the
app at `https://jbrady0850.github.io/nhl-daily-analytics/data/latest.json`. Anyone may
read it. `app/data/latest.js` holds the same data as `window.NDA_DATA = {...};` so the
app also works when opened as a plain file.

Every past version is in the repository history (`git log -- app/data/latest.json`).

`python -m nhlda check <file>` validates a file: the format name, and that it carries
no wager, account or chat wording.

## Top level

| Key | Type | Meaning |
| --- | --- | --- |
| `format` | text | Always `nhl-daily-analytics/1` for this layout. A breaking change gets a new number. |
| `data_version` | number | Version of the fields inside the format. |
| `generated_utc` | text | When the build finished, UTC, `YYYY-MM-DDTHH:MM:SSZ`. |
| `date` | text | The slate date, `YYYY-MM-DD`, on the league's US Central clock. |
| `season` | text | NHL season id, for example `20262027`. |
| `model_version` | text | Version of the prediction model. |
| `teams` | object | All 32 teams by abbreviation: `name`, `conference`, `division`, `record` (W-L-OTL), `pts`, `games_played`. |
| `standings` | object | League standings from the NHL standings feed. See below. |
| `slate` | object | Today's games. See below. |
| `lines` | object | Lines and pairs for all 32 teams. See below. |
| `results` | object | Settled picks. See below. |
| `model` | object | Settings, learning and calibration. See below. |
| `injuries` | object | Every injured player the injury reports name, league-wide. See below. |
| `dfs` | object | DraftKings projections: `skaters` (top 30 by projection) and `goalies` (every probable starter, highest team win probability first, ties by projection). |
| `sources` | object | What loaded: goalie and injury counts, `injuries.sources` (the reports read: `espn`, `hockey_reference`, `daily_faceoff`) and `injuries.source` (the same joined with `+`, or `none`), market games, lines by source, request counts, build seconds. |
| `problems` | list | Plain-text notes about anything that failed or fell back. Empty on a clean run. |

## `slate`

| Key | Meaning |
| --- | --- |
| `game_count`, `game_type` | Number of games and `REGULAR` or `PLAYOFF`. |
| `gate` | `floor`, `dynamic_gate` (rating points) and `cleared` (games that reached it). Information only. |
| `next` | The next game day after today: `date`, `game_count` and `matchups`. The app shows it when there are no games today. |
| `games` | One entry per game, highest win probability first. |

Each game:

| Key | Meaning |
| --- | --- |
| `game_id`, `matchup`, `away`, `home`, `start_utc`, `venue`, `state` | The NHL game id, `AWY @ HOM`, team abbreviations, start time in UTC, arena, game state. |
| `projected`, `win_prob`, `rank` | The projected winner, its win probability (0 to 1), and its rank on the slate. |
| `home_win_prob`, `away_win_prob` | Both sides' probabilities. |
| `tier`, `confidence_pct` | `HIGH` (58.5 percent or more), `MODERATE` (54.0 to 58.4) or `LOW`. |
| `delta`, `gate_cleared` | The rating gap between the teams and whether it reached the day's gate. |
| `market` | `home_prob`, `away_prob` and `books`: the sportsbook consensus from the NHL score feed, margin removed. Comparison only. `null` when the feed has no lines. |
| `home_side`, `away_side` | `raa` (team score), `pillars` (`p1_form`, `p2_absence`, `p3_motivation`, `p4_gvi`, `p5_friction`, `adj_raa`), `form`, `goalie`, `absences`. |

`goalie` holds `name`, `player_id`, `source` (`CONFIRMED`, `PROJECTED` or `ROTATION`),
`confirmed`, `last_start`, `days_rest`, `window_starts`, `window_toi_min`,
`season_sv_pct`, `gsax`, `hdsv_pct`, `gaa`, `games_played`, `reported_by` and
`source_url`.

`form` holds shot shares (`l5_shot_share`, `l3_shot_share`, `ssn_shot_share`,
`venue_shot_share`), records (`l5_record`, `l3_record`), `ssn_goal_diff_pg`,
`ssn_xgf_pct`, `rest_days`, `venue`, `venue_games` and `l3_vs_l5_momentum`.

Each entry in `absences` holds `player_id`, `name`, `position`, `toi_share`,
`points_share`, `source` and an `injury_note` when the injury report has one. `source`
is `LINEUP_DIFF` (did not dress in the last game), `ESPN_INJURY_REPORT`,
`DFO_INJURY_REPORT` or `HR_INJURY_REPORT` (listed by ESPN, Daily Faceoff or
Hockey-Reference), or the lineup and a report joined, for example
`LINEUP_DIFF+ESPN_INJURY`.

Every `player_id` and `nhl_id` is the NHL's own player id. The player's official page is
`https://www.nhl.com/player/<id>`.

## `lines`

One object per team abbreviation:

| Key | Meaning |
| --- | --- |
| `source` | `daily_faceoff`, `shift_chart` (rebuilt from the last game) or `none`. |
| `url`, `source_name`, `source_url`, `updated_at` | Where the combinations came from and when that source last changed them. |
| `error` | Why lines are missing, or `null`. |
| `lines` | Four forward lines: `line`, `players`, `totals`, `leaders`. |
| `pairs` | Three defense pairs: `pair`, `players`, `totals`. |

Each player has `name`, `pos`, `nhl_id`, `pp` (power-play unit), `gtd`, `injury`, and
two stat windows, `last10` and `season`: `gp`, `g`, `a`, `p`, `sog`, `ppp`, `pim`,
`toi_pg` (minutes) and `toi_sec_pg`. Stats come from the NHL's per-game logs for the
current regular season only.

`totals` adds the same fields per window, plus `p_pg` and `sog_pg` (the sum of each
player's own per-game rate). `leaders` says, per window, whether the line leads its
team in points and shots per game, and whether that lead is tied.

## `results`

| Key | Meaning |
| --- | --- |
| `last_date`, `last_games` | The last settled day and its games: `matchup`, `projected`, `win_prob`, `tier`, `actual`, scores, `last_period` (`REG`, `OT` or `SO`), `correct`, `market_prob`. |
| `season` | `games`, `correct`, `accuracy`, `gate_cleared`, `gate_correct`, `gate_accuracy`, `brier`, `log_loss`, `by_tier`, `market` (model and market log loss on games with a captured line, and how often they agreed), `calibration`, `first_date`, `last_date`. |

The full game-by-game record is `state/results_ledger.csv` in the repository.

## `standings`

| Key | Meaning |
| --- | --- |
| `as_of_utc` | When the NHL last updated its standings, UTC. |
| `wildcard_format` | `true` while the league uses the wild-card playoff format. |
| `teams` | All 32 teams in league order (see below). |

Each team: `team` (abbreviation), `conference`, `division`, `gp` (games played), `w`
(wins), `l` (regulation losses), `otl` (overtime and shootout losses), `pts`, `p_pct`
(points percentage, 0 to 1), `rw` (regulation wins), `row` (regulation plus overtime
wins), `gf`, `ga`, `diff` (goals for, against and the difference), `l10` (last 10 games
as W-L-OTL), `streak` (for example `W3`, `L1`, `OT2`, or `null`), `div_rank`,
`conf_rank`, `league_rank`, `wc_rank` (the NHL's own order within the division,
conference, league and wild-card race; `wc_rank` is `0` for the top three in each
division) and `clinch` (`x` playoff spot, `y` division, `z` conference, `p` Presidents'
Trophy, `e` eliminated, or `null`).

## `model`

| Key | Meaning |
| --- | --- |
| `settings_in_use`, `baseline` | `h` (home ice in rating points), `k` (logistic slope) and `w` (the five pillar weights). |
| `learning` | `games`, `next_at`, `cycles`, `accepted_cycles`, `forward` (the candidate's score on unseen games: `games`, `mean_gain`, `t`, `passed`), `min_forward`, `forward_t`, `last`. |
| `calibration` | Probability bands: `lo`, `hi`, `games`, `correct`, `hit_rate`, `mean_prob`. |

## `injuries`

`sources`: the reports read, in order of preference (`espn`, `hockey_reference`,
`daily_faceoff`). `players`: one row per injured player, team by team, shortest
absence first. The first report that names a player supplies his row.

| Field | Meaning |
| --- | --- |
| `team` | Team abbreviation. |
| `name` | Player name as the report spells it. |
| `player_id` | NHL player id (text), from the NHL roster list or the NHL's player search; `null` when not found. |
| `position` | `C`, `L`/`LW`, `R`/`RW`, `D` or `G`; `null` when unknown. |
| `status` | `GTD` game-time decision, `DTD` day to day, `OUT`, `IR` injured reserve, `LTIR` long-term injured reserve, `IR-NR` injured non-roster, `SUSP` suspended. |
| `injury` | Body part, with ESPN's detail in brackets (`Hip (surgery)`); `Undisclosed`; or `null`. |
| `reported` | Date of the latest report, `YYYY-MM-DD`, or `null`. |
| `return_est` | ESPN's estimated return date (today or later), or `null`. |
| `note` | The report's short comment, or `null`. |
| `source` | `ESPN`, `HR` (Hockey-Reference) or `DFO` (Daily Faceoff). |
| `in_lineup` | Present and `true` when Daily Faceoff has him in today's lineup anyway. |

Ids the roster list lacks (players on long-term or non-roster injured reserve) come
from the NHL's public player search and are kept in `state/injury_ids.json` between
runs, so each name is looked up once.

## `dfs`

Skaters: `player_id`, `name`, `team`, `opp`, `position`, `avg_toi_min`, `proj_goals`,
`proj_assists`, `proj_points`, `proj_sog`, `proj_blocks`, `proj_hits`, `dk_proj`.

Goalies: `player_id`, `name`, `team`, `opp`, `win_prob`, `save_pct`, `gsax`, `dk_proj`.
