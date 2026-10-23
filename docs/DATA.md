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
| `slate` | object | Today's games. See below. |
| `lines` | object | Lines and pairs for all 32 teams. See below. |
| `results` | object | Settled picks. See below. |
| `model` | object | Settings, learning and calibration. See below. |
| `dfs` | object | DraftKings projections: `skaters` (top 30) and `goalies`. |
| `sources` | object | What loaded: goalie and injury counts, market games, lines by source, request counts, build seconds. |
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

Each entry in `absences` holds `name`, `position`, `toi_share`, `points_share`,
`source` and an `injury_note` when the injury report has one.

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

## `model`

| Key | Meaning |
| --- | --- |
| `settings_in_use`, `baseline` | `h` (home ice in rating points), `k` (logistic slope) and `w` (the five pillar weights). |
| `learning` | `games`, `next_at`, `cycles`, `accepted_cycles`, `forward` (the candidate's score on unseen games: `games`, `mean_gain`, `t`, `passed`), `min_forward`, `forward_t`, `last`. |
| `calibration` | Probability bands: `lo`, `hi`, `games`, `correct`, `hit_rate`, `mean_prob`. |

## `dfs`

Skaters: `name`, `team`, `opp`, `position`, `avg_toi_min`, `proj_goals`,
`proj_assists`, `proj_points`, `proj_sog`, `proj_blocks`, `proj_hits`, `dk_proj`.

Goalies: `name`, `team`, `opp`, `win_prob`, `save_pct`, `gsax`, `dk_proj`.
