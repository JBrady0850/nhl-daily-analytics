# Limits

What the app can and cannot do. Read this before you rely on a number.

## The predictions

- A win probability is an estimate. A 60 percent pick should lose about 4 times in 10.
  The Results tab shows the season pick rate and the record by confidence tier; the
  Model tab shows how often each probability band actually won.
- The Results tab also compares the model with the betting market on games where the
  NHL score feed carried a line. The market has often been the better forecaster. The
  model is shown for what it is, not as an edge.
- Early in the season the form factor leans on last season while each team has fewer
  than 5 games. The motivation factor (playoff race) stays off until 35 percent of the
  season has been played.
- The model changes its settings only after a new setting proves better on games it
  was not fit on. That takes many games, so most of the season runs on the same
  settings.

## Timing

- The data updates twice a day, at about 14:05 and 22:20 UTC. News after the evening
  run (a late scratch, a goalie change at warmups) is not in the app until the next
  morning.
- GitHub starts scheduled runs on a best-effort basis. A run can start late when
  GitHub is busy.
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
- When it does not, the lines are rebuilt from the team's last game: the players who
  shared the most ice time are grouped into four lines and three pairs. That shows how
  the team played last game, not a projection of tonight. The note under each table
  says which source was used.
- Player stats count the current regular season only. Preseason never counts. Early in
  the season "Last 10" covers fewer than 10 games.

## Injuries

- Missing players come from each team's last lineup plus an injury report. The report
  is Hockey-Reference's league page when it can be reached. Hockey-Reference refuses
  GitHub's servers, so the scheduled runs usually use the injured lists on Daily
  Faceoff's team pages instead; a "Data note" on Today and Matchups says so.
- Daily Faceoff's injured list and its projected lineup do not always agree. When a
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
