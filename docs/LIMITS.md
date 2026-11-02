# Limits

What the app can and cannot do. Read this before you rely on a number.

## The predictions

- A win probability is an estimate. A 60 percent pick should lose about 4 times in 10.
  The Results tab shows the season pick rate and the record by confidence tier; the
  Model tab shows how often each probability band actually won.
- The Results tab also compares the model with the betting market on games where the
  NHL score feed carried a line. The market has often been the better forecaster. The
  model is shown for what it is, not as an edge. A card tags a game where the market
  favors the other team, and since Nov 2, 2026 a 50/50 blend of model and market is
  under test (Model tab); it is used only if it proves better on 40 or more games.
- Early in the season the form factor leans on last season while each team has fewer
  than 5 games. The motivation factor (playoff race) stays off until 35 percent of the
  season has been played.
- The model changes its settings only after a new setting proves better on games it
  was not fit on. That takes many games, so most of the season runs on the same
  settings.

## Timing

- The data updates twice a day, at about 9:00 AM and 4:45 PM US Central time. On game
  days, from 9:30 AM Central until the last game starts, a check every 15 minutes
  rebuilds it when Daily Faceoff reports another starting goalie, a confirmation, a
  newer report, or (in the 90 minutes before a game) a changed lineup. So morning-skate
  and warmup news usually reaches the app within 20 minutes, as fast as Daily Faceoff
  relays it from the beat writers' posts. The check depends on the outside timer (with
  GitHub's own schedule as a slower backup) and on Daily Faceoff answering; news that
  Daily Faceoff has not picked up, or that comes after puck drop, is not in the app.
  Other injury news still waits for the next main update.
- A game card shows the final score and WIN or LOSS only for a game that was over at
  an update, which in practice means early afternoon games at the 4:45 PM update. A game
  still in its third period then, and every evening game, shows its result in the
  Results tab the next morning. Until then its card on Today and My Teams says that it
  started after the data was built (or was under way when it was built) and which update
  brings the final score.
- On My Teams, a game that is over at an update becomes the team's Last game at once,
  with its final score. MoneyPuck posts its shot values overnight, so that evening each
  shot is valued at MoneyPuck's season average for shots of its type from its distance
  (a note says so) and the goalie's goals saved above expected waits. Tested on 18 games
  of 2026-10-28 to 30, these Game Scores came within 0.16 of the final ones on average
  and put 92% of players on the same side of zero. The first update after MoneyPuck
  posts the game, usually the next morning, replaces them with the exact values. If the
  NHL has not published the shift charts either, only production is scored until it does.
- GitHub starts scheduled runs on a best-effort basis. A run can start late when
  GitHub is busy (one evening run started two and a half hours late), and GitHub can
  skip a run (the 14:05 run of 2026-10-24 never started). A timer outside GitHub starts
  the workflow at each update time with a `daily-data` dispatch event; if that timer
  is down, backup cron runs in the two hours after each update time build the file
  when the main run has not, and the line under
  each page title shows the next scheduled update in your time, says when an update is
  due or late, and an open copy of the app keeps checking for the new file while one is
  due.
- GitHub turns off scheduled workflows in a public repository after 60 days with no
  repository activity. If the data stops updating, open the repository's Actions tab,
  choose **Daily data**, and turn it back on.
- Times in the app show in your device's time zone. The slate date follows the
  league's US Central clock.

## Goalies

- A projected starter is a projection. Teams often confirm their goalie only in the
  late afternoon, so the morning data shows more PROJECTED and ROTATION starters than
  the evening data.
- ROTATION means no outside source named a starter, and the model picked the goalie
  from recent workload.
- THIN marks a goalie with fewer than 180 minutes in his recent-start window, so the
  goalie factor rests on a small sample.

## Lines

- Line combinations come from Daily Faceoff when its team page answers. GitHub's
  servers are shared, and the site may not always answer them.
- When it does not answer, the app keeps its last answer: a team's lines for up to three
  days, starting goalies for the same date only. On 2026-10-30 it answered the 9:00 AM
  update and refused the 4:45 PM one (HTTP 403); the evening data now keeps the morning's
  lines and goalies instead of falling back. A Data note and the note under each table
  say so.
- With no earlier answer to keep, the lines are rebuilt from the team's last game: the
  players who shared the most ice time are grouped into four lines and three pairs, and
  the forward lines are numbered by their points per game this season (ice time is no
  guide: a top line whose wingers take penalties plays less). That shows how the team
  played last game, not a projection of tonight, and the numbering may differ from the
  coach's. The note under each table says so.
- Player stats count the current regular season only. Preseason never counts. Early in
  the season "Last 10" covers fewer than 10 games.

## Injuries

- Missing players come from each team's last lineup plus the injury reports: ESPN's
  public feed first, then the injured lists on Daily Faceoff's team pages. Hockey-Reference
  is read only when ESPN cannot be reached, because it refuses GitHub's servers. A "Data
  note" on Today and Matchups appears when ESPN or every report is missing.
- ESPN's feed is public but undocumented, so it can change without notice. Its player
  ids differ from the NHL's, so players are matched by name.
- The injury reports and Daily Faceoff's projected lineup do not always agree. When a
  player appears in both, the app follows the lineup and treats him as playing.
- Either report can lag behind team announcements, and the two do not always list the
  same players.

## DFS projections

- Projections use each player's season rates, recent ice time and his team's win
  probability, scored with DraftKings points. They do not include salaries, ownership
  or late lineup news, and they do not build lineups.

## The data sources

- All sources are free public pages and feeds. Any of them can change its layout or
  stop answering without notice. The build writes a plain note in `problems` (and the
  app shows it) when something fails or falls back.
- If a source asks this project to stop reading it, the project will stop. The
  `--no-dfo` switch already runs the whole build without Daily Faceoff.

## The repository

- Each run commits the new data file, so the repository grows by roughly 100 KB a
  run, or about 50 MB over a full season. That is well inside GitHub's limits.
