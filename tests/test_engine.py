"""Offline tests for the public engine and the data file. No network."""
import csv
import hashlib
import json
import os
import re
import subprocess

import pytest

from nhlda import core as C
from nhlda.build import check_public, standings_from, FORMAT
from nhlda.lines import group_by_shared_ice, _overlap

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLE = os.path.join(HERE, "fixtures", "sample_latest.json")


def test_overlap_counts_shared_seconds():
    assert _overlap([(0, 50), (100, 150)], [(25, 125)]) == 25 + 25
    assert _overlap([(0, 10)], [(20, 30)]) == 0


def test_shift_grouping_finds_lines_and_orders_by_ice_time():
    # three trios that each always share the ice; trio B plays the most
    shifts = {}
    for i, (start, n) in enumerate(((0, 4), (1000, 6), (2000, 3))):
        for p in range(3):
            shifts[f"{i}-{p}"] = [(start + k * 120, start + k * 120 + 45) for k in range(n)]
    groups = group_by_shared_ice(list(shifts), shifts, 3, 4)
    assert len(groups) == 3
    assert sorted(groups[0]) == ["1-0", "1-1", "1-2"]          # most ice first
    assert all(len({p.split("-")[0] for p in g}) == 1 for g in groups)


# ---- Daily Faceoff refused the 4:45 PM update (2026-10-30): keep its earlier answer ----
def _dfo_lines_page(groups):
    """A Daily Faceoff line-combinations page with only what the parser reads."""
    players = []
    for gid, names in groups.items():
        pos = {"f": ["lw", "c", "rw"], "d": ["ld", "rd"], "p": ["lw", "c", "rw", "ld", "rd"]}[gid[0]]
        for n, (name, pid) in enumerate(names):
            players.append({"name": name, "playerId": pid, "groupIdentifier": gid, "positionIdentifier": pos[n],
                            "injuryStatus": None, "gameTimeDecision": False,
                            "season": {"gamesPlayed": 12, "goals": 1}, "last10": {"gamesPlayed": 10}})
    data = {"props": {"pageProps": {"combinations": {"sourceName": "David Dwork", "source": "https://x.com/example/1",
                                                     "updatedAt": "2026-10-30T13:38:43Z", "players": players}}}}
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script></html>"


FLA_DFO = {"f1": [("Brady Tkachuk", 1), ("Sam Bennett", 2), ("Matthew Tkachuk", 3)],
           "f2": [("Carter Verhaeghe", 4), ("Sam Reinhart", 5), ("Sandis Vilmanis", 6)],
           "f3": [("Eetu Luostarinen", 7), ("Anton Lundell", 8), ("Lars Eller", 9)],
           "f4": [("Bokondji Imama", 10), ("Cole Schwindt", 11), ("Garnet Hathaway", 12)],
           "d1": [("Gustav Forsling", 13), ("Aaron Ekblad", 14)], "d2": [("Niko Mikkola", 15), ("Seth Jones", 16)],
           "d3": [("Dmitry Kulikov", 17), ("Radko Gudas", 18)],
           "pp1": [("Brady Tkachuk", 1), ("Sam Reinhart", 5), ("Matthew Tkachuk", 3), ("Carter Verhaeghe", 4), ("Seth Jones", 16)]}


class _DfoFetch:
    """Answers Daily Faceoff only when ok; otherwise as a 403 does (None)."""
    def __init__(self, ok, goalie_page=None):
        self.ok, self.goalie_page = ok, goalie_page

    async def get(self, url, as_text=False, **kw):
        if not self.ok or "dailyfaceoff.com" not in url:
            return None
        if "line-combinations" in url:
            return _dfo_lines_page(FLA_DFO)
        return self.goalie_page


def test_daily_faceoff_lines_are_kept_when_a_later_update_is_refused(tmp_path):
    """John, 2026-10-30: the Panthers lines were wrong. Daily Faceoff answered GitHub at
    9:53 AM and refused it (HTTP 403) at 4:45 PM, so every team's lines fell back to a
    shift-chart rebuild numbered by ice time (Florida's top line showed as F3). Its
    last answer is now kept for up to three days and used when it refuses."""
    import asyncio
    from nhlda.lines import dfo_lines_with_cache
    got, info = asyncio.run(dfo_lines_with_cache(_DfoFetch(True), ["FLA"], str(tmp_path), now_utc="2026-10-30T14:53:00Z"))
    names = lambda dl: [[r["name"] for r in ln["players"]] for ln in dl.lines]
    first = names(got["FLA"])
    assert first[0] == ["Brady Tkachuk", "Sam Bennett", "Matthew Tkachuk"] and not info["refused"]
    got, info = asyncio.run(dfo_lines_with_cache(_DfoFetch(False), ["FLA"], str(tmp_path), now_utc="2026-10-30T21:45:00Z"))
    dl = got["FLA"]
    assert not dl.error and names(dl) == first and dl.cached_utc == "2026-10-30T14:53:00Z"
    assert [[r["name"] for r in p["players"]] for p in dl.pairs][0] == ["Gustav Forsling", "Aaron Ekblad"]
    assert next(r for r in dl.lines[0]["players"] if r["name"] == "Brady Tkachuk")["pp"] == "PP1"
    assert info["refused"] == ["FLA"] and info["cached"] == {"FLA": "2026-10-30T14:53:00Z"}
    # four days on, the saved answer is too old: the shift-chart rebuild takes over
    got, info = asyncio.run(dfo_lines_with_cache(_DfoFetch(False), ["FLA"], str(tmp_path), now_utc="2026-11-03T21:45:00Z"))
    assert got["FLA"].error and not info["cached"]


def test_daily_faceoff_goalies_are_kept_for_the_same_day(tmp_path):
    import asyncio
    from nhlda.lines import dfo_goalies_with_cache
    page = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"data": [
        {"homeTeamName": "Florida Panthers", "awayTeamName": "Winnipeg Jets", "homeGoalieName": "Akira Schmid",
         "awayGoalieName": "Nico Daws", "homeNewsStrengthName": "Confirmed", "awayNewsStrengthName": "Likely"}]}}}) + "</script>")
    g = asyncio.run(dfo_goalies_with_cache(_DfoFetch(True, page), "2026-10-30", str(tmp_path), now_utc="2026-10-30T14:53:00Z"))
    assert g.loaded and g.get("WPG")["goalie_name"] == "Nico Daws" and not g.cached_utc
    g = asyncio.run(dfo_goalies_with_cache(_DfoFetch(False), "2026-10-30", str(tmp_path), now_utc="2026-10-30T21:45:00Z"))
    assert g.loaded and g.refused and g.cached_utc == "2026-10-30T14:53:00Z" and g.get("WPG")["goalie_name"] == "Nico Daws"
    assert g.get("FLA")["confirmed"] is True
    # another day's answer is never used
    g = asyncio.run(dfo_goalies_with_cache(_DfoFetch(False), "2026-10-31", str(tmp_path), now_utc="2026-10-31T14:00:00Z"))
    assert not g.loaded and g.refused and not g.cached_utc


def test_daily_faceoff_note_says_what_the_app_used_instead():
    from nhlda.lines import dfo_note
    assert dfo_note(False, False, {}, 0) is None
    t = dfo_note(True, True, {"FLA": "2026-10-30T14:53:00Z"}, 0, goalies_cached="2026-10-30T14:53:00Z")
    assert "Daily Faceoff did not answer this update" in t and "2026-10-30 14:53 UTC" in t and "rotation" not in t
    t = dfo_note(True, True, {}, 32, goalies_cached=None)
    assert "rotation guess" in t and "rebuilt from each team's last game" in t


def test_rebuilt_lines_are_numbered_by_points_with_the_center_in_the_middle():
    """The shift chart cannot know the coach's line numbers; ice time put Florida's top
    line third (its wingers took penalties). Rebuilt lines are numbered by the line's
    points per game this season, and the center sits between his wingers."""
    from nhlda.lines import order_rebuilt_lines, center_middle
    tot = lambda p: {"last10": {"p_pg": p}, "season": {"p_pg": p}}
    ln = [{"line": 1, "players": [{"name": "Vilmanis"}], "totals": tot(1.78)},
          {"line": 2, "players": [{"name": "Luostarinen"}], "totals": tot(0.85)},
          {"line": 3, "players": [{"name": "Tkachuk"}], "totals": tot(3.15)},
          {"line": 4, "players": [{"name": "Imama"}], "totals": tot(0.0)}]
    out = order_rebuilt_lines(ln)
    assert [x["players"][0]["name"] for x in out] == ["Tkachuk", "Vilmanis", "Luostarinen", "Imama"]
    assert [x["line"] for x in out] == [1, 2, 3, 4]
    rows = [{"name": "M. Tkachuk", "pos": "LW"}, {"name": "B. Tkachuk", "pos": "LW"}, {"name": "Bennett", "pos": "C"}]
    assert [r["name"] for r in center_middle(rows)] == ["M. Tkachuk", "Bennett", "B. Tkachuk"]
    rows = [{"name": "a", "pos": "RW"}, {"name": "b", "pos": "LW"}, {"name": "c", "pos": "C"}]
    assert [r["name"] for r in center_middle(rows)] == ["b", "c", "a"]


def test_check_public_rejects_stakes_mail_and_chat():
    check_public({"lines": {"FLA": {"leaders": {"points": True}}}, "teams": {"FLA": {"pts": 10}}})
    for bad in ({"allocation": {}}, {"x": {"base_net": 1}}, {"x": [{"funded": True}]},
                {"note": "!bet 300 FLA"}, {"to": "someone@gmail.com"}, {"discord": []}):
        with pytest.raises(ValueError):
            check_public(bad)


def test_sample_data_file_is_public_and_complete():
    d = json.load(open(SAMPLE, encoding="utf-8"))
    assert d["format"] == FORMAT
    check_public(d)
    for k in ("date", "generated_utc", "teams", "slate", "lines", "results", "model", "dfs", "sources"):
        assert k in d
    assert len(d["teams"]) == 32 and len(d["lines"]) == 32
    gk = d["dfs"]["goalies"]
    starters = sum(1 for g in d["slate"]["games"] for side in ("home_side", "away_side") if (g[side].get("goalie") or {}).get("name"))
    assert len(gk) == starters, "every probable starter is on the DFS goalie board"
    assert [x["win_prob"] for x in gk] == sorted((x["win_prob"] for x in gk), reverse=True), "DFS goalies by win %"
    st = d["standings"]["teams"]
    assert sorted(r["team"] for r in st) == sorted(d["teams"])
    assert [r["league_rank"] for r in st] == list(range(1, 33))
    assert {r["conference"] for r in st} == {"Eastern", "Western"} and len({r["division"] for r in st}) == 4
    for g in d["slate"]["games"]:
        assert 0 <= g["home_win_prob"] <= 1 and abs(g["home_win_prob"] + g["away_win_prob"] - 1) < 0.002
        assert g["projected"] in (g["home"], g["away"])
        for side in ("home_side", "away_side"):
            assert set(g[side]["pillars"]) >= {"p1_form", "p2_absence", "p3_motivation", "p4_gvi", "p5_friction"}


def _team(ab, conf, div, seq, **kw):
    t = {"teamAbbrev": {"default": ab}, "teamName": {"default": ab + " Club"}, "conferenceName": conf,
         "divisionName": div, "gamesPlayed": 10, "wins": 6, "losses": 3, "otLosses": 1, "points": 13,
         "pointPctg": 0.65432, "regulationWins": 5, "regulationPlusOtWins": 6, "goalFor": 30, "goalAgainst": 25,
         "goalDifferential": 5, "l10Wins": 6, "l10Losses": 3, "l10OtLosses": 1, "streakCode": "W", "streakCount": 2,
         "divisionSequence": seq, "conferenceSequence": seq, "leagueSequence": seq, "wildcardSequence": 0}
    t.update(kw)
    return t


def test_standings_from_reads_the_nhl_feed():
    feed = {"standingsDateTimeUtc": "2026-10-23T21:00:00Z", "wildCardIndicator": True, "standings": [
        _team("BBB", "Western", "Central", 2, points=12, streakCode="OT", streakCount=1, clinchIndicator="x"),
        _team("AAA", "Eastern", "Atlantic", 1),
        {"teamName": {"default": "No abbreviation"}},
        _team("CCC", "Eastern", "Metropolitan", 3, pointPctg=None, wildcardSequence=1, streakCode=None, streakCount=None)]}
    names, block = standings_from(feed)
    assert set(names) == {"AAA", "BBB", "CCC"} and names["AAA"]["record"] == "6-3-1"
    # official logos stay on the NHL's image server: the primary mark for light and dark backgrounds
    assert names["AAA"]["logo"] == "https://assets.nhle.com/logos/nhl/svg/AAA_light.svg"
    assert names["AAA"]["logo_dark"] == "https://assets.nhle.com/logos/nhl/svg/AAA_dark.svg"
    assert block["as_of_utc"] == "2026-10-23T21:00:00Z" and block["wildcard_format"] is True
    rows = block["teams"]
    assert [r["team"] for r in rows] == ["AAA", "BBB", "CCC"]
    a, b, c = rows
    assert a["l10"] == "6-3-1" and a["streak"] == "W2" and a["p_pct"] == 0.6543 and a["rw"] == 5 and a["row"] == 6
    assert b["streak"] == "OT1" and b["clinch"] == "x" and b["pts"] == 12 and b["conference"] == "Western"
    assert c["streak"] is None and c["p_pct"] is None and c["wc_rank"] == 1 and c["clinch"] is None
    check_public({"standings": block})
    assert standings_from({}) == ({}, {"as_of_utc": None, "wildcard_format": True, "teams": []})
    assert standings_from(None)[1]["teams"] == []


def _ledger(tmp_path, rows):
    p = tmp_path / C.LEDGER_NAME
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=C.LEDGER_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in C.LEDGER_FIELDS})
    return str(tmp_path)


def test_season_record_and_learning_rows(tmp_path):
    rows = [{"date": "2026-10-01", "matchup": "AAA @ BBB", "projected": "BBB", "win_prob": 0.62, "tier": "HIGH",
             "gate_cleared": 1, "actual": "BBB", "correct": 1, "brier": 0.14, "dp1": 5, "dp2": 0, "dp3": 0, "dp4": 1, "dp5": 0},
            {"date": "2026-10-01", "matchup": "CCC @ DDD", "projected": "CCC", "win_prob": 0.53, "tier": "LOW",
             "gate_cleared": 0, "actual": "DDD", "correct": 0, "brier": 0.28, "dp1": -2, "dp2": 0, "dp3": 0, "dp4": 0, "dp5": 1}]
    d = _ledger(tmp_path, rows)
    s = C.season_to_date(C.read_ledger(d))
    assert s["games"] == 2 and s["correct"] == 1 and s["gate_accuracy"] == 1.0
    assert s["by_tier"]["HIGH"] == {"games": 1, "correct": 1}
    lr, skipped = C._learning_rows(d)
    assert skipped == 0 and lr == [([5.0, 0.0, 0.0, 1.0, 0.0], 1), ([-2.0, 0.0, 0.0, 0.0, 1.0], 1)]
    assert [b["games"] for b in C.calibration_bins(C.read_ledger(d))][:3] == [1, 0, 1]


def _dfo_page(players):
    return ('<html><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"combinations": {"players": players}}}}) + "</script></html>")


def test_daily_faceoff_injuries_read_the_injured_list():
    from nhlda.intel import DailyFaceoffInjuries
    page = _dfo_page([
        {"name": "Juraj Slafkovsky", "playerId": 1, "groupIdentifier": "ir", "injuryStatus": "ir",
         "latestNews": {"details": "Out two weeks (lower-body).", "createdAt": "2026-10-20T10:00:00"}},
        {"name": "Kirby Dach", "playerId": 2, "groupIdentifier": "f4", "injuryStatus": None},
        {"name": "Florian Xhekaj", "playerId": 3, "groupIdentifier": "f4"},
        {"name": "Florian Xhekaj", "playerId": 3, "groupIdentifier": "ir", "injuryStatus": "ir"},
        {"name": "Sam Montembeault", "playerId": 4, "groupIdentifier": "ir", "injuryStatus": "dtd"}])
    d = DailyFaceoffInjuries()
    assert d.add_page("mtl", page) is True
    names = [e["player_name"] for e in d.get("MTL")]
    assert names == ["Juraj Slafkovsky", "Sam Montembeault"]       # Xhekaj is also in today's lineup
    hit = d.match_player("MTL", "Juraj Slafkovský")                  # accents differ between sources
    assert hit and hit["injury_type"] == "IR" and hit["injury_note"].startswith("Out two weeks")
    assert hit["date_injury"] == "2026-10-20"
    assert d.match_player("MTL", "Sam Montembeault")["injury_note"] == "Listed DTD by Daily Faceoff."
    assert d.add_page("MTL", "<html>no data</html>") is False


def test_absences_name_the_injury_source():
    import asyncio
    from nhlda.intel import DailyFaceoffInjuries

    class FakeMP:
        def skater(self, pid):
            return {"icetime": "1000", "I_F_points": "10"}

        def goalie(self, pid):
            return None
    inj = DailyFaceoffInjuries()
    inj.add_page("MTL", _dfo_page([{"name": "Nick Suzuki", "playerId": 9, "groupIdentifier": "ir",
                                    "injuryStatus": "out", "latestNews": {"details": "Out 2-4 weeks."}}]))
    roster = [{"player_id": str(i), "name": n, "position": "C"} for i, n in enumerate(
        ["Nick Suzuki", "Cole Caufield", "Kirby Dach", "Jake Evans", "Josh Anderson"], start=1)]
    missing, impact = asyncio.run(C.detect_absences(None, "MTL", roster, FakeMP(), None, hr_injuries=inj))
    assert [m["name"] for m in missing] == ["Nick Suzuki"]
    assert missing[0]["source"] == "DFO_INJURY_REPORT" and missing[0]["injury_note"] == "Out 2-4 weeks."
    assert impact != 0
    from nhlda.intel import CombinedInjuries, EspnInjuries
    mixed = CombinedInjuries([EspnInjuries().read(ESPN_SAMPLE, as_of="2026-10-23"), inj])
    missing, _ = asyncio.run(C.detect_absences(None, "MTL", roster, FakeMP(), None, hr_injuries=mixed))
    assert missing[0]["source"] == "ESPN_INJURY_REPORT"


ESPN_SAMPLE = {"injuries": [
    {"displayName": "Montreal Canadiens", "injuries": [
        {"status": "Out", "date": "2026-10-22T15:30Z", "shortComment": "out",
         "details": {"type": "Upper Body", "returnDate": "2026-11-18"}, "athlete": {"displayName": "Nick Suzuki"}},
        {"status": "Injured Reserve", "date": "2026-10-17T17:08Z",
         "shortComment": "Xhekaj (lower body) was placed on injured reserve Saturday.",
         "details": {"type": "Lower Body", "returnDate": "2026-10-31"}, "athlete": {"displayName": "Florian Xhekaj"}},
        {"status": "Day-To-Day", "shortComment": "day-to-day",
         "details": {"type": "Undisclosed", "returnDate": "2026-10-23"}, "athlete": {"displayName": "Juraj Slafkovský"}}]},
    {"displayName": "Nowhere Skaters", "injuries": [{"status": "Out", "athlete": {"displayName": "Nobody"}}]}]}


def test_espn_injuries_read_status_note_and_return():
    from nhlda.intel import EspnInjuries
    e = EspnInjuries().read(ESPN_SAMPLE, as_of="2026-10-23")
    assert e.loaded and list(e.by_team) == ["MTL"]                       # unknown team names are skipped
    suz = e.match_player("MTL", "Nick Suzuki")
    assert suz["injury_type"] == "OUT" and suz["injury_note"] == "Out (upper body), estimated return Nov 18."
    assert e.match_player("MTL", "Florian Xhekaj")["injury_note"].startswith("Xhekaj (lower body) was placed")
    slaf = e.match_player("MTL", "Juraj Slafkovsky")                    # accents differ between sources
    assert slaf["injury_type"] == "DTD" and slaf["injury_note"] == "Day-To-Day."
    assert slaf["tag"] == "ESPN"


def test_combined_injuries_prefer_espn_and_respect_the_lineup():
    from nhlda.intel import CombinedInjuries, DailyFaceoffInjuries, EspnInjuries
    espn = EspnInjuries().read(ESPN_SAMPLE, as_of="2026-10-23")
    dfo = DailyFaceoffInjuries()
    dfo.add_page("MTL", _dfo_page([
        {"name": "Florian Xhekaj", "playerId": 3, "groupIdentifier": "f4"},
        {"name": "Nick Suzuki", "playerId": 9, "groupIdentifier": "ir", "injuryStatus": "out",
         "latestNews": {"details": "Suzuki will miss two to four weeks."}},
        {"name": "Mike Matheson", "playerId": 8, "groupIdentifier": "ir", "injuryStatus": "dtd",
         "latestNews": {"details": "Matheson is day to day."}}]))
    both = CombinedInjuries([espn, None, dfo], lineup=dfo.lineup)
    assert both.loaded
    assert both.match_player("MTL", "Nick Suzuki")["tag"] == "ESPN"      # ESPN wins when both name him
    assert both.match_player("MTL", "Mike Matheson")["tag"] == "DFO"     # Daily Faceoff fills the gap
    assert both.match_player("MTL", "Florian Xhekaj") is None            # in today's lineup: playing
    assert {e["player_name"] for e in both.get("MTL")} >= {"Nick Suzuki", "Mike Matheson"}
    assert not CombinedInjuries([None, EspnInjuries()]).loaded           # nothing reached


def test_check_public_accepts_the_injury_notes():
    check_public({"problems": ["Hockey-Reference's injury report could not be reached this run, so injuries "
                               "come from the injured lists on Daily Faceoff's team pages."],
                  "sources": {"injuries": {"loaded": True, "source": "daily_faceoff", "rows": 11}}})


# Personal words this public repository must never contain. Only their SHA-256
# digests (of the lowercased word, or of two adjacent words joined by a space) are
# stored here, so the list itself gives nothing away. The scan hashes every word
# and every pair of adjacent words in every file.
PRIVATE_DIGESTS = {
    "b17653146bd38027d1e916c896e1ed07eb07d601f663adf7a46a3775397aaabb",
    "fb0d668decb3b9bb0d7a118cc41657721e4de080eb2b1f4cb76c9fae0d157850",
    "53008a4021d501893a3fc103c1dd05c23342327d230db16793a6302775dd07b1",
    "55a484f502103aba8b0693abc683c96ea96773fabf228acb6384403934249b5e",
    "36d5d2ab47d7d2195003de72df65c4612793f6d6f89f98ef084c80e4f65b5644",
    "8e3f5e915594cdb4ef687b3c65e4706ffa0abefadc91934f0add9ce4570e4700",
}
# key formats need their long tails, so these patterns do not match themselves
KEY_PATTERNS = [r"sk-ant-[A-Za-z0-9_-]{20}", r"AIza[0-9A-Za-z_-]{35}", r"ghp_[A-Za-z0-9]{20}",
                r"github_pat_[A-Za-z0-9_]{20}", r"C:\\{1,2}Users"]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_OK = ("users.noreply.github.com", "example.com", "example.org")
SKIP_DIRS = (".git", "__pycache__", ".pytest_cache", "node_modules")


def _repo_files():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    if files:
        return files
    return [os.path.relpath(os.path.join(dp, f), ROOT) for dp, dirs, fs in os.walk(ROOT)
            if not any(part in SKIP_DIRS for part in dp.split(os.sep)) for f in fs]


def _private_words(text, digests=PRIVATE_DIGESTS):
    words = re.findall(r"[a-z0-9]+", text.lower())
    grams = set(words) | {f"{a} {b}" for a, b in zip(words, words[1:])}
    return sorted(g for g in grams if hashlib.sha256(g.encode()).hexdigest() in digests)


def test_private_word_check_works():
    probe = {hashlib.sha256(b"quokka harbor").hexdigest(), hashlib.sha256(b"zamboni42").hexdigest()}
    assert _private_words("nothing to see here", probe) == []
    assert _private_words("A Quokka  Harbor note from ZAMBONI42.", probe) == ["quokka harbor", "zamboni42"]


def test_repository_carries_no_personal_data_or_keys():
    hits = []
    for f in _repo_files():
        if f.endswith((".png", ".npy")):
            continue
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        this_test = f.replace(os.sep, "/") == "tests/test_engine.py"
        if _private_words(text):
            hits.append(f"{f}: a listed personal word")
        for pat in KEY_PATTERNS:
            if re.search(pat, text):
                hits.append(f"{f}: {pat}")
        if not this_test:                           # the stake test uses a fake mail address
            for m in EMAIL.finditer(text):
                if not m.group(0).lower().endswith(EMAIL_OK):
                    hits.append(f"{f}: email address {m.group(0)}")
    assert not hits, hits


def test_team_colors_cover_every_club():
    """Every club has a primary and a secondary color in TEAM_COLORS, as six-digit hex,
    and the 2026-10-23 corrections stay in place."""
    src = open(os.path.join(ROOT, "app", "js", "app.js"), encoding="utf-8").read()
    block = src[src.index("const TEAM_COLORS"):src.index("};", src.index("const TEAM_COLORS"))]
    colors = dict((k, (a.upper(), b.upper())) for k, a, b in re.findall(r'([A-Z]{3}): \["(#[0-9A-Fa-f]{6})", "(#[0-9A-Fa-f]{6})"\]', block))
    d = json.load(open(SAMPLE, encoding="utf-8"))
    assert sorted(colors) == sorted(d["teams"]) and len(colors) == 32
    assert all(a != b for a, b in colors.values())
    assert colors["ANA"] == ("#FC4C02", "#000000") and colors["UTA"] == ("#000000", "#6CACE3")


def test_install_identity_is_this_app_only():
    """Browsers resolve a manifest id against the site's origin, not the app's folder.
    "./" claimed all of the shared github.io site, so a second app installed from it
    was reported as already installed and the install failed."""
    with open(os.path.join(ROOT, "app", "manifest.webmanifest"), encoding="utf-8") as f:
        m = json.load(f)
    assert m["id"] == "/nhl-daily-analytics/" and m["scope"] == "./"


def test_schedule_builds_only_when_the_main_update_is_missing():
    """An automatic run builds when the data predates the latest main update time
    (9:00 AM or 4:45 PM US Central, all year) and stops otherwise, so extra triggers
    only fill in for a main run that GitHub started late or skipped."""
    import datetime as dt
    from nhlda import schedule as S
    t = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    # daylight time (CDT, UTC-5): 9:00 AM is 14:00Z and 4:45 PM is 21:45Z
    assert S.last_main(t("2026-10-25T13:59:00Z")) == t("2026-10-24T21:45:00Z")
    assert S.last_main(t("2026-10-25T14:00:00Z")) == t("2026-10-25T14:00:00Z")
    assert S.last_main(t("2026-10-25T23:44:00Z")) == t("2026-10-25T21:45:00Z")
    assert S.last_main(t("2026-10-26T03:30:00Z")) == t("2026-10-25T21:45:00Z")   # 10:30 PM Central
    # standard time (CST, UTC-6) from 2026-11-01: 9:00 AM is 15:00Z and 4:45 PM is 22:45Z
    assert S.last_main(t("2026-11-02T14:30:00Z")) == t("2026-11-01T22:45:00Z")
    assert S.last_main(t("2026-11-02T15:00:00Z")) == t("2026-11-02T15:00:00Z")
    assert S.last_main(t("2026-11-02T23:00:00Z")) == t("2026-11-02T22:45:00Z")
    # the day daylight time ends: Saturday's evening update was 21:45Z, Sunday's morning is 15:00Z
    assert S.last_main(t("2026-11-01T14:59:00Z")) == t("2026-10-31T21:45:00Z")
    assert S.last_main(t("2026-11-01T15:00:00Z")) == t("2026-11-01T15:00:00Z")
    # Saturday evening's file, then the morning run, then an extra trigger
    assert S.due("2026-10-24T22:35:29Z", t("2026-10-25T13:58:12Z"))[0] is False      # 108 s early
    assert S.due("2026-10-24T22:35:29Z", t("2026-10-25T14:01:00Z"))[0] is True
    assert S.due("2026-10-25T14:02:00Z", t("2026-10-25T14:47:00Z"))[0] is False
    # a cron fallback that GitHub starts very late still serves the next main time
    assert S.due("2026-10-25T14:02:00Z", t("2026-10-25T22:31:00Z"))[0] is True
    assert "(04:45 PM CDT)" in S.due("2026-10-25T14:02:00Z", t("2026-10-25T22:31:00Z"))[1]
    # no file or no build time: build
    assert S.due(None, t("2026-10-25T15:00:00Z"))[0] is True
    assert S.due("not a time", t("2026-10-25T15:00:00Z"))[0] is True


def test_schedule_command_prints_a_workflow_output(tmp_path):
    p = tmp_path / "latest.json"
    p.write_text(json.dumps({"generated_utc": "2000-01-01T00:00:00Z"}), encoding="utf-8")
    r = subprocess.run(["python", "-m", "nhlda.schedule", str(p)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "build=true" and "before the" in r.stderr
    r = subprocess.run(["python", "-m", "nhlda.schedule", str(tmp_path / "missing.json")], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "build=true"


def test_workflow_app_and_guard_share_the_main_times():
    """The guard (MAIN_CENTRAL) and the app's status line (RUNS_CENTRAL) list the same
    US Central main times; the cron fallback has a line within 5 minutes after each
    main time in both daylight time (UTC-5) and standard time (UTC-6); every cron line
    falls within three hours after a main time (before the app calls an update late)
    and avoids the top and bottom of the hour. Automatic runs (the outside timer's
    daily-data dispatch and the cron fallback) pass through the guard; a manual run
    always builds."""
    from nhlda import schedule as S
    wf = open(os.path.join(ROOT, ".github", "workflows", "daily.yml"), encoding="utf-8").read()
    crons = [int(h) * 60 + int(m) for m, h in re.findall(r'cron: "(\d+) (\d+) \* \* \*"', wf)]
    mains = [((h + off) * 60 + m) % 1440 for h, m in S.MAIN_CENTRAL for off in (5, 6)]
    for mt in mains:
        assert any(0 <= (c - mt) % 1440 <= 5 for c in crons), f"no cron line just after {mt // 60:02d}:{mt % 60:02d} UTC"
    for c in crons:
        assert min((c - mt) % 1440 for mt in mains) < 180, f"cron {c // 60:02d}:{c % 60:02d} is not within 3 hours after a main time"
        assert c % 60 not in (0, 30), "GitHub is busiest at the top and bottom of the hour"
    app = open(os.path.join(ROOT, "app", "js", "app.js"), encoding="utf-8").read()
    runs = re.search(r"const RUNS_CENTRAL = (\[\[.*?\]\]);", app).group(1)
    assert [tuple(x) for x in json.loads(runs)] == list(S.MAIN_CENTRAL)
    assert 'const CENTRAL = "America/Chicago"' in app
    assert "python3 -m nhlda.watch app/data/latest.json state" in wf and "needs.when.outputs.build == 'true'" in wf
    assert 'github.event_name }}" = "workflow_dispatch"' in wf
    assert re.search(r"repository_dispatch:\s*\n\s*types: \[daily-data, lineup-watch\]", wf), \
        "the outside timer's events must start the workflow"
    # 1.17.0: the game-day watch backup runs twice an hour, off the busy minutes, from
    # the US Central morning (14 UTC in daylight time) to the late games (05 UTC)
    watch = re.findall(r'cron: "([\d,]+) ([\d-]+) \* \* \*"', wf)
    watch = [(m, h) for m, h in watch if "," in m]
    assert watch == [("7,37", "14-23"), ("7,37", "0-5")]


ESPN_LISTS = {"injuries": [
    {"displayName": "Anaheim Ducks", "injuries": [
        {"status": "Injured Reserve", "date": "2026-09-30T18:00Z",
         "shortComment": "Terry (hip) was moved from the non-roster list to long-term injured reserve Wednesday.",
         "details": {"fantasyStatus": {"description": "IR-LT"}, "type": "Hip", "detail": "Surgery", "returnDate": "2026-11-21"},
         "athlete": {"displayName": "Troy Terry", "position": {"abbreviation": "RW"}}},
        {"status": "Injured Reserve", "date": "2026-10-20T18:00Z", "shortComment": "ir-nr",
         "details": {"fantasyStatus": {"description": "IR-NR"}, "type": "Undisclosed", "returnDate": "2026-10-01"},
         "athlete": {"displayName": "Someone Else", "position": {"abbreviation": "D"}}},
        {"status": "Day-To-Day", "date": "2026-10-24T12:00Z", "shortComment": "day-to-day",
         "details": {"fantasyStatus": {"description": "Day-To-Day"}, "type": "Upper Body", "returnDate": "2026-10-27"},
         "athlete": {"displayName": "Leo Carlsson", "position": {"abbreviation": "C"}}},
        {"status": "Out", "date": "2026-10-24T12:00Z", "shortComment": "He was seen on a discord stream saying he is out tonight.",
         "details": {"type": "Face"}, "athlete": {"displayName": "Mason McTavish", "position": {"abbreviation": "C"}}}]}]}


def test_espn_keeps_the_injury_list_each_player_is_on():
    """ESPN's fantasyStatus tells long-term and non-roster injured reserve apart;
    the app shows LTIR and IR-NR, the body part and ESPN's estimated return."""
    from nhlda.intel import EspnInjuries, injury_status_code
    e = EspnInjuries().read(ESPN_LISTS, as_of="2026-10-24")
    terry, other, leo, mason = e.get("ANA")
    assert (terry["status"], terry["body"], terry["return_est"], terry["position"]) == ("LTIR", "Hip (surgery)", "2026-11-21", "RW")
    assert terry["injury_type"] == "IR"                       # the engine's own code is unchanged
    assert (other["status"], other["body"], other["return_est"], other["comment"]) == ("IR-NR", "Undisclosed", "", "")
    assert (leo["status"], leo["body"]) == ("DTD", "Upper body") and mason["status"] == "OUT"
    assert [injury_status_code(x) for x in ("Injured Reserve", "IR-LT", "ltir", "dtd", "Suspension", "")] == \
        ["IR", "LTIR", "LTIR", "DTD", "SUSP", "OUT"]


def test_injury_list_merges_reports_and_finds_ids():
    """One row per player, league-wide: ESPN's row wins, Daily Faceoff adds who ESPN
    lacks, ids and positions come from the player master list, a player Daily
    Faceoff has in today's lineup is marked, and a note holding forbidden text is
    dropped rather than failing the public check. Rows run team by team, shortest
    absence first."""
    from nhlda.intel import DailyFaceoffInjuries, EspnInjuries, injury_list
    from nhlda.build import FORBIDDEN_TEXT
    espn = EspnInjuries().read(ESPN_LISTS, as_of="2026-10-24")
    dfo = DailyFaceoffInjuries()
    dfo.add_page("ANA", _dfo_page([
        {"name": "Leo Carlsson", "playerId": 1, "groupIdentifier": "f1"},
        {"name": "Troy Terry", "playerId": 2, "groupIdentifier": "ir", "injuryStatus": "ir"},
        {"name": "Radko Gudas", "playerId": 3, "groupIdentifier": "ir", "injuryStatus": "dtd",
         "latestNews": {"details": "Gudas is day to day.", "createdAt": "2026-10-23T10:00:00"}}]))
    master = [{"team": "ANA", "name": "Leo Carlsson", "player_id": "8484153", "position": "C"},
              {"team": "ANA", "name": "Radko Gudas", "player_id": "8475462", "position": "D"}]
    rows = injury_list([espn, None, dfo], master, lineup=dfo.lineup, forbidden=FORBIDDEN_TEXT)
    by = {r["name"]: r for r in rows}
    assert len(rows) == 5 and by["Troy Terry"]["status"] == "LTIR" and by["Troy Terry"]["source"] == "ESPN"
    assert by["Radko Gudas"]["source"] == "DFO" and by["Radko Gudas"]["player_id"] == "8475462" and by["Radko Gudas"]["note"] == "Gudas is day to day."
    assert by["Leo Carlsson"]["in_lineup"] is True and by["Leo Carlsson"]["player_id"] == "8484153"
    assert by["Mason McTavish"]["note"] is None                 # "discord" would fail check_public
    assert [r["status"] for r in rows] == ["DTD", "DTD", "OUT", "LTIR", "IR-NR"]
    check_public({"injuries": {"players": rows}})


def test_injury_ids_come_from_the_nhl_search_and_are_cached(tmp_path):
    import asyncio
    from nhlda.intel import resolve_injury_ids

    class FakeFetcher:
        def __init__(self):
            self.calls = []

        async def get(self, url, params=None, **kw):
            self.calls.append(params["q"])
            return [{"playerId": "8478873", "name": "Troy Terry", "positionCode": "R", "teamAbbrev": "ANA"},
                    {"playerId": "1", "name": "Troy Terry", "teamAbbrev": "DAL"}]
    cache = tmp_path / "injury_ids.json"
    rows = [{"team": "ANA", "name": "Troy Terry", "player_id": None, "position": None},
            {"team": "ANA", "name": "Leo Carlsson", "player_id": "8484153", "position": "C"}]
    f = FakeFetcher()
    asyncio.run(resolve_injury_ids(f, rows, str(cache)))
    assert f.calls == ["Troy Terry"] and rows[0]["player_id"] == "8478873" and rows[0]["position"] == "R"
    again = [{"team": "ANA", "name": "Troy Terry", "player_id": None, "position": None}]
    asyncio.run(resolve_injury_ids(f, again, str(cache)))
    assert f.calls == ["Troy Terry"] and again[0]["player_id"] == "8478873"     # second run: from the cache


def test_sample_teams_name_their_logo_on_the_nhl_server():
    d = json.load(open(SAMPLE, encoding="utf-8"))
    for ab, t in d["teams"].items():
        assert t["logo"] == f"https://assets.nhle.com/logos/nhl/svg/{ab}_light.svg"
        assert t["logo_dark"] == f"https://assets.nhle.com/logos/nhl/svg/{ab}_dark.svg"


def test_no_logo_files_are_copied_into_the_repository():
    # the app shows the NHL's logos from the NHL's server; the repository holds none
    found = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in (".git", "node_modules", "__pycache__")]
        for f in files:
            low = f.lower()
            if low.endswith(".svg") and ("_light" in low or "_dark" in low or "logo" in low):
                found.append(os.path.join(base, f))
    assert found == []


def test_sample_injury_list_is_complete():
    d = json.load(open(SAMPLE, encoding="utf-8"))
    rows = d["injuries"]["players"]
    known = {"GTD", "DTD", "OUT", "IR", "LTIR", "IR-NR", "SUSP"}
    assert len(rows) > 50 and all(r["team"] in d["teams"] and r["status"] in known and r["name"] for r in rows)
    assert {"LTIR", "IR-NR", "DTD", "OUT", "IR"} <= {r["status"] for r in rows}
    assert sum(1 for r in rows if r.get("player_id")) > 0.9 * len(rows)
    assert any(r.get("in_lineup") for r in rows)


def _game(gid, away, home, state, hwp):
    proj = home if hwp >= 0.5 else away
    return {"game_id": gid, "matchup": f"{away} @ {home}", "away": away, "home": home, "state": state,
            "home_win_prob": hwp, "away_win_prob": round(1 - hwp, 4), "projected": proj,
            "win_prob": hwp if hwp >= 0.5 else round(1 - hwp, 4), "tier": "HIGH", "rank": 9}


def test_started_games_keep_the_pick_from_before_puck_drop():
    from nhlda.build import settle_started
    prev = {"1": _game(1, "FLA", "CHI", "FUT", 0.34), "2": _game(2, "MIN", "VAN", "FUT", 0.30),
            "3": _game(3, "NYI", "DAL", "FUT", 0.57)}
    now = [_game(1, "FLA", "CHI", "LIVE", 0.61),       # under way: the new run would flip the pick
           _game(2, "MIN", "VAN", "FUT", 0.55),        # not started: the new run's pick stands
           _game(3, "NYI", "DAL", "PRE", 0.56),        # warm-ups: still not started
           _game(4, "COL", "NSH", "CRIT", 0.40)]       # no earlier pick to keep
    out = {g["game_id"]: g for g in settle_started(now, prev, {})}
    assert out[1]["projected"] == "FLA" and out[1]["home_win_prob"] == 0.34 and out[1]["state"] == "LIVE"
    assert out[1]["pick_locked"] is True and "rank" not in out[1] and "result" not in out[1]
    assert out[2]["projected"] == "VAN" and "pick_locked" not in out[2]
    assert out[3]["home_win_prob"] == 0.56 and "pick_locked" not in out[3]
    assert out[4]["projected"] == "COL" and "pick_locked" not in out[4]


def test_finished_games_show_the_final_score_and_the_picks_result():
    from nhlda.build import settle_started
    prev = {"1": _game(1, "FLA", "CHI", "FUT", 0.34), "2": _game(2, "VGK", "CBJ", "FUT", 0.455)}
    now = [_game(1, "FLA", "CHI", "OFF", 0.70), _game(2, "VGK", "CBJ", "FINAL", 0.455),
           _game(3, "TOR", "EDM", "LIVE", 0.46), _game(4, "BUF", "WSH", "FUT", 0.47)]
    finals = {"FLA_vs_CHI": {"away_score": 4, "home_score": 2, "winner": "FLA", "last_period": "REG"},
              "VGK_vs_CBJ": {"away_score": 1, "home_score": 2, "winner": "CBJ", "last_period": "OT"}}
    out = {g["game_id"]: g for g in settle_started(now, prev, finals)}
    assert out[1]["result"] == {"away_score": 4, "home_score": 2, "last_period": "REG", "winner": "FLA", "pick": "win"}
    assert out[1]["home_win_prob"] == 0.34, "graded against the pick made before puck drop"
    assert out[2]["result"]["pick"] == "loss" and out[2]["result"]["last_period"] == "OT"
    assert "result" not in out[3] and "result" not in out[4]
    check_public({"slate": {"games": list(out.values())}})


def test_previous_games_only_reads_the_same_day(tmp_path):
    from nhlda.build import previous_games
    assert previous_games(str(tmp_path), "2026-10-25") == {}
    body = {"date": "2026-10-25", "slate": {"games": [_game(7, "FLA", "CHI", "FUT", 0.34), {"game_id": 8, "matchup": "A @ B"}]}}
    (tmp_path / "latest.json").write_text(json.dumps(body))
    assert list(previous_games(str(tmp_path), "2026-10-25")) == ["7"], "only games with a pick"
    assert previous_games(str(tmp_path), "2026-10-26") == {}


def test_sample_shows_finished_games_with_a_result():
    d = json.load(open(SAMPLE, encoding="utf-8"))
    done = [g for g in d["slate"]["games"] if g.get("result")]
    assert {g["result"]["pick"] for g in done} == {"win", "loss"}
    for g in done:
        r = g["result"]
        assert g["state"] in ("OFF", "FINAL") and r["winner"] in (g["home"], g["away"])
        assert r["pick"] == ("win" if r["winner"] == g["projected"] else "loss")
        assert (r["home_score"] > r["away_score"]) == (r["winner"] == g["home"])


# ---------- Game Score breakdown (My Teams, last game) ----------
GAME = os.path.join(HERE, "fixtures", "game_2026020180.json")


def _csv_text(rows):
    import io as _io
    buf = _io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _game_inputs():
    from nhlda import gamescore as G
    fx = json.load(open(GAME, encoding="utf-8"))
    rates = G.league_rates(_csv_text(fx["mp_teams"]))
    return fx, G.shot_values(fx["shots"]), rates, G.player_quality(_csv_text(fx["mp_skaters"]), rates)


class _GoalieFetch:
    """Game logs for two Devils goalies: Jake Allen has started this season; Connor
    Hellebuyck (traded in from Winnipeg) has not played yet, but has last season's
    starts."""
    LOGS = {
        ("8474596", "20262027"): [{"gameDate": "2026-10-24", "toi": "60:00", "savePctg": 0.9}],
        ("8476945", "20262027"): [],
        ("8476945", "20252026"): [{"gameDate": f"2026-04-{d:02d}", "toi": "60:00", "savePctg": 0.91} for d in (13, 11, 9, 6, 4)],
    }

    async def get(self, url, **kw):
        m = re.search(r"player/(\d+)/game-log/(\d+)/", url)
        return {"gameLog": self.LOGS.get((m.group(1), m.group(2)), [])} if m else None


class _GoalieMP:
    def goalie(self, pid):
        return {"games_played": "7", "ongoal": "176", "goals": "20", "xGoals": "16.5", "icetime": "25200"} if str(pid) == "8474596" else {}


def test_daily_faceoffs_starter_is_used_even_before_his_first_game_this_season():
    """John, 2026-10-27: Hellebuyck is a New Jersey Devil and was missing from the
    lineup. Daily Faceoff confirmed him for his Devils debut, but the engine looked
    for Daily Faceoff's goalie only among goalies who had already played this season,
    so it showed Jake Allen (the rotation guess) with the reporter's link beside him."""
    import asyncio
    goalies = [{"player_id": "8474596", "name": "Jake Allen"}, {"player_id": "8476945", "name": "Connor Hellebuyck"}]
    dfo = {"NJD": {"goalie_name": "Connor Hellebuyck", "confirmed": True, "source_name": "Peter Baugh"}}
    starter, hist, days, conf, src, intel = asyncio.run(C.resolve_goalie(
        _GoalieFetch(), "NJD", goalies, _GoalieMP(), "20262027", 2, "2026-10-27", dfo=dfo, season_open="2026-10-07"))
    assert starter["player"]["name"] == "Connor Hellebuyck" and src == "DFO_CONFIRMED" and conf
    assert len(hist) == 5 and starter["prior_starts_used"] == 5     # last season's starts fill his window
    assert intel["source_name"] == "Peter Baugh"
    # a goalie Daily Faceoff names who is not on the roster: the rotation guess, and the
    # report is not attached to a goalie it was not about
    dfo = {"NJD": {"goalie_name": "Somebody Else", "confirmed": True, "source_name": "A Reporter"}}
    starter, hist, days, conf, src, intel = asyncio.run(C.resolve_goalie(
        _GoalieFetch(), "NJD", goalies, _GoalieMP(), "20262027", 2, "2026-10-27", dfo=dfo, season_open="2026-10-07"))
    assert starter["player"]["name"] == "Jake Allen" and src == "PROBABLE_ROTATION" and intel is None
    # names match without accents or extra spaces
    dfo = {"NJD": {"goalie_name": " connor  HELLEBUYCK ", "confirmed": False}}
    starter = asyncio.run(C.resolve_goalie(_GoalieFetch(), "NJD", goalies, _GoalieMP(), "20262027", 2, "2026-10-27", dfo=dfo))[0]
    assert starter["player"]["name"] == "Connor Hellebuyck"


def test_a_goalie_without_games_this_season_shows_no_season_numbers():
    g = C.project_goalie({"player_id": "8476945", "name": "Connor Hellebuyck", "team": "NJD"}, {}, 0.55, 2,
                         prior_row={"games_played": "57", "ongoal": "1600", "goals": "163", "xGoals": "160", "icetime": "205200"})
    assert g["save_pct"] is None and g["gsax"] is None and g["hdsv_pct"] is None
    assert g["dk_proj"] > 0.55 * 6           # the projection still uses last season's rates


def test_each_moneypuck_shot_value_is_used_once():
    """A saved shot at 3:42 and a missed shot one second later by the same player:
    MoneyPuck's 0.51 must not be counted for both (game 2026020186 counted it twice)."""
    from nhlda.gamescore import match_shots
    ev = lambda k, t: {"typeDescKey": k, "periodDescriptor": {"number": 1}, "timeInPeriod": t, "details": {"shootingPlayerId": 9}}
    plays = [ev("missed-shot", "03:41"), ev("shot-on-goal", "03:42"), ev("missed-shot", "03:43")]
    # the exact second wins first, so the 3:41 miss does not take the 3:42 shot's value
    assert match_shots(plays, {(1, 222, 9): 0.51}) == {1: 0.51}
    assert match_shots(plays, {(1, 222, 9): 0.51, (1, 224, 9): 0.02}) == {1: 0.51, 2: 0.02}


def test_learning_block_names_the_candidate(tmp_path):
    """John, 2026-10-30: the Model tab needs a column for the candidate. The learning
    block carries the candidate's settings, the game count it was proposed at and the
    last cycle's scores for the settings in use, the candidate and the baseline."""
    cand = {"k": 0.026182, "h": 1.571, "w": [1.0014, 0.9519, 0.9969, 1.0102, 1.0396]}
    scores = {"in_use": {"log_loss": 0.6631, "brier": 0.2358, "accuracy": 0.5636},
              "candidate": {"log_loss": 0.6618, "brier": 0.2352, "accuracy": 0.5576},
              "baseline": {"log_loss": 0.6631, "brier": 0.2358, "accuracy": 0.5636}}
    base = {"k": 0.0261, "h": 1.071, "w": [1.0] * 5}
    st = {"baseline": base, "current": base, "candidate": cand, "candidate_from": 165, "forward": [0.001, -0.002],
          "next_at": 170, "history": [{"cycle": 14, "utc": "2026-10-23 14:00", "games": 165, "accepted": False,
                                        "reason": "kept", "candidate": cand, "scores": scores}]}
    (tmp_path / "learning.json").write_text(json.dumps(st), encoding="utf-8")
    out = C.run_learning(str(tmp_path))
    assert out["candidate"] == cand and out["candidate_from"] == 165 and out["scores"] == scores
    # the forward record keeps the latest LEARN_FORWARD_MAX games; the app uses it to say
    # what it would take for the candidate to go into use
    assert out["forward_max"] == 300


def test_sample_model_block_carries_the_candidate():
    m = json.load(open(SAMPLE, encoding="utf-8"))["model"]
    c = m["candidate"]
    assert isinstance(c["k"], float) and isinstance(c["h"], float) and len(c["w"]) == 5
    L = m["learning"]
    assert L["candidate_from"] <= L["games"] and set(L["scores"]) == {"in_use", "candidate", "baseline"}
    assert L["forward_max"] == 300


def test_strength_reads_the_nhl_situation_code():
    from nhlda.gamescore import _strength
    assert _strength("1551", True) == "5v5" and _strength("1551", False) == "5v5"
    assert _strength("1451", True) == "PP" and _strength("1451", False) == "PK"     # away team short-handed
    assert _strength("1541", True) == "PK" and _strength("1441", True) == "EV"
    assert _strength("0651", True) == "EN" and _strength("1560", False) == "EN"


def test_game_score_of_a_real_game():
    from nhlda.gamescore import breakdown
    fx, xg, rates, q = _game_inputs()
    assert 2.4 < rates["xg"] < 2.7 and 6 < rates["pp"] < 9
    assert len(xg) == 92
    res = breakdown(fx["pbp"], fx["shifts"], xg, rates, q)
    assert res["inputs"] == "full" and set(res["teams"]) == {"FLA", "CHI"}
    fla = res["teams"]["FLA"]
    assert fla["opp"] == "CHI" and fla["home"] is False
    sk = {p["name"]: p for p in fla["skaters"]}
    assert len(sk) == 18 and all(p["toi"] > 0 for p in sk.values())
    for p in sk.values():
        assert abs(p["gs"] - sum(p["o"]) - sum(p["d"])) < 0.02
        b = p["box"]
        assert abs(p["d"][0] - (0.05 * b["blk"] - 0.15 * b["pt"])) < 0.006, p["name"]
    # Verhaeghe: second assist, three shots on goal, and he drove 5-on-5 play
    v = sk["Carter Verhaeghe"]
    assert v["box"]["a2"] == 1 and v["box"]["sog"] == 3 and v["o"][1] > 0.5
    # the order follows the game: Jones leads the defense, Gudas trails it
    dmen = [p["name"] for p in fla["skaters"] if p["pos"] == "D"]
    assert dmen[0] == "Seth Jones" and dmen[-1] == "Radko Gudas"
    fwd = [p["gs"] for p in fla["skaters"] if p["pos"] != "D"]
    assert fwd == sorted(fwd, reverse=True), "forwards first, best first"
    g = fla["goalies"][0]
    assert g["name"] == "Akira Schmid" and g["shots"] == 21 and g["ga"] == 2
    assert abs(g["gsax"] - (g["xga"] - g["ga"])) < 0.011 and g["gsax"] > 1
    assert res["teams"]["CHI"]["goalies"][0]["ga"] == 3


OFFICIAL_2026020180 = {  # NHL boxscore, read 2026-10-27: goals, assists, blocks, TOI seconds
    8480801: (0, 1, 1, 888), 8477935: (0, 0, 0, 1156), 8477933: (0, 1, 0, 1206), 8478147: (0, 0, 1, 293),
    8482113: (0, 0, 0, 963), 8479314: (1, 0, 1, 1107), 8474189: (1, 0, 1, 980), 8477903: (0, 0, 1, 537),
    8477409: (0, 1, 1, 999), 8480185: (0, 0, 1, 874), 8481655: (0, 0, 0, 267), 8483771: (1, 0, 0, 784),
    8477495: (0, 1, 1, 1495), 8477932: (0, 1, 3, 1351), 8475462: (0, 0, 0, 750), 8475179: (0, 0, 0, 711),
    8478055: (0, 0, 2, 1392), 8478859: (0, 0, 2, 1292), 8477987: (0, 0, 1, 756), 8478413: (0, 0, 0, 810),
    8485391: (0, 0, 4, 1007), 8483450: (1, 0, 0, 880), 8482062: (0, 0, 0, 586), 8478233: (0, 1, 0, 526),
    8477479: (0, 0, 0, 1342), 8484390: (1, 0, 0, 1272), 8476882: (0, 0, 0, 1137), 8474141: (0, 1, 0, 1221),
    8483493: (0, 0, 0, 931), 8484144: (0, 1, 2, 1389), 8483506: (0, 0, 0, 907), 8483466: (0, 1, 0, 761),
    8474013: (0, 0, 0, 925), 8482176: (0, 0, 0, 936), 8484783: (0, 0, 0, 1218), 8481568: (0, 0, 2, 1053),
}


def test_game_score_counts_match_the_nhl_boxscore():
    """John asked for every player and stat to be checked (2026-10-27). Against the
    NHL's own boxscore: the same 36 skaters, and the same goals, assists, blocks and
    time on ice. Two bugs failed this: a shot that hits the shooter's own teammate
    was counted as a block, and a shift the NHL's chart lists twice was counted twice."""
    from nhlda.gamescore import breakdown
    fx, xg, rates, q = _game_inputs()
    res = breakdown(fx["pbp"], fx["shifts"], xg, rates, q)
    ours = {p["id"]: p for t in res["teams"].values() for p in t["skaters"]}
    assert set(ours) == set(OFFICIAL_2026020180)
    for pid, (g, a, blk, toi) in OFFICIAL_2026020180.items():
        p = ours[pid]
        assert (p["box"]["g"], p["box"]["a1"] + p["box"]["a2"], p["box"]["blk"]) == (g, a, blk), p["name"]
        assert abs(p["toi"] - toi / 60) < 0.051, (p["name"], p["toi"], toi)

def test_the_nhl_boxscore_wins_where_the_play_by_play_disagrees():
    """After this game the NHL credited Verhaeghe with a fourth shot and Knight with a
    34th shot against in its boxscore; the play-by-play read the morning after had 3
    and 33. With the boxscore, every skater's shots, blocks, goals, assists and time on
    ice, and both goalies' shots and goals against, are the NHL's official numbers."""
    from nhlda.gamescore import breakdown, official_counts
    fx, xg, rates, q = _game_inputs()
    res = breakdown(fx["pbp"], fx["shifts"], xg, rates, q, box=fx["box"])
    off = official_counts(fx["box"])
    for t in res["teams"].values():
        for p in t["skaters"]:
            o = off[p["id"]]
            assert (p["box"]["sog"], p["box"]["blk"], p["box"]["g"], p["box"]["a1"] + p["box"]["a2"]) == \
                (o["sog"], o["blk"], o["g"], o["a"]), p["name"]
            assert p["toi"] == round(o["toi"] / 60, 1), p["name"]
        for g in t["goalies"]:
            assert (g["shots"], g["ga"]) == (off[g["id"]]["shots"], off[g["id"]]["ga"]), g["name"]
    v = next(p for p in res["teams"]["FLA"]["skaters"] if p["name"] == "Carter Verhaeghe")
    assert v["box"]["sog"] == 4 and res["teams"]["CHI"]["goalies"][0]["shots"] == 34


def test_goalie_expected_goals_are_moneypucks_own():
    """The goalie's expected goals against are MoneyPuck's own figure for him (3.21
    for Schmid in this game), not a sum over the shots this app could match."""
    from nhlda.gamescore import breakdown, goalie_values
    fx, xg, rates, q = _game_inputs()
    gx = goalie_values(fx["shots"])
    assert abs(gx[8481033] - 3.21) < 0.006
    res = breakdown(fx["pbp"], fx["shifts"], xg, rates, q, box=fx["box"], goalie_xg=gx)
    g = res["teams"]["FLA"]["goalies"][0]
    assert g["name"] == "Akira Schmid" and g["xga"] == 3.21 and g["gsax"] == 1.21


def test_game_score_without_shift_charts_is_production_only():
    from nhlda.gamescore import breakdown
    fx, xg, rates, q = _game_inputs()
    res = breakdown(fx["pbp"], [], xg, rates, q)
    assert res["inputs"] == "production"
    for p in res["teams"]["FLA"]["skaters"]:
        assert p["o"][1:] == [0, 0, 0] and p["d"][1:] == [0, 0, 0] and p["toi"] is None
    assert res["teams"]["FLA"]["goalies"][0]["gsax"] is None


class _FakeFetch:
    def __init__(self, fx, day):
        self.fx, self.day = fx, day

    async def get(self, url, params=None, as_text=False, **kw):
        if "/score/" in url:
            g = {"id": 2026020180, "gameDate": "2026-10-25", "gameState": "OFF", "gameType": 2}
            return {"games": [g] if url.endswith("2026-10-25") else []}
        if "play-by-play" in url:
            return self.fx["pbp"]
        if "shiftcharts" in url:
            return {"data": self.fx["shifts"]}
        if url.endswith("teams.csv"):
            return _csv_text(self.fx["mp_teams"])
        if url.endswith("skaters.csv"):
            return _csv_text(self.fx["mp_skaters"])
        return None


def _run_update(tmp_path, fx, day, shots):
    import asyncio
    from nhlda import gamescore as G

    async def fake_rows(session, year, want, table=None):
        if table is not None and shots:
            table.update(G.shot_table(shots))
        return {20180: shots} if shots and 20180 in want else {}
    orig = G._shot_rows
    G._shot_rows = fake_rows
    try:
        return asyncio.run(G.update(_FakeFetch(fx, day), None, str(tmp_path), day, 2026))
    finally:
        G._shot_rows = orig


def test_last_game_shows_at_once_then_takes_the_full_score(tmp_path):
    """1.15.0 (John, 2026-10-31: My Teams did not show Tampa Bay's finished afternoon
    game and its result at the evening update): a final game appears on the same day,
    marked pending until MoneyPuck's shot values arrive. 1.15.1 (John: "The helped hurt
    graph for the Tampa Bay Lightning was not updated correctly"): with the shift charts
    published, every part is scored on stand-in shot values, not production alone."""
    fx = json.load(open(GAME, encoding="utf-8"))
    # the evening of the game: final, shift charts out, no shot values yet
    out = _run_update(tmp_path, fx, "2026-10-25", [])
    t = out["teams"]["FLA"]
    assert (t["game_id"], t["date"], t["opp"], t["for"], t["against"], t["result"], t["inputs"], t["pending"]) == \
        (2026020180, "2026-10-25", "CHI", 3, 2, "W", "estimated", True)
    assert out["teams"]["CHI"]["result"] == "L" and out["teams"]["CHI"]["pending"]
    sk = t["skaters"] + out["teams"]["CHI"]["skaters"]
    assert all(isinstance(p["toi"], float) and p["toi"] > 0 for p in sk)                # time on ice
    assert sum(1 for p in sk if p["d"][1] < 0) >= 5                                      # on the ice for goals and chances against
    assert any(p["o"][2] or p["d"][2] for p in sk) and any(p["o"][3] or p["d"][3] for p in sk)
    assert all(g["gsax"] is None and g["xga"] is None for g in t["goalies"])             # MoneyPuck's own values only
    # the shot values arrive: both teams get the game, scored in full, and it is cached
    out = _run_update(tmp_path, fx, "2026-10-26", fx["shots"])
    assert set(out["teams"]) == {"FLA", "CHI"} and "MoneyPuck.com" in out["credit"]
    t = out["teams"]["FLA"]
    assert (t["game_id"], t["date"], t["opp"], t["for"], t["against"], t["result"], t["inputs"], t["pending"]) == \
        (2026020180, "2026-10-25", "CHI", 3, 2, "W", "full", False)
    assert out["teams"]["CHI"]["result"] == "L"
    cache = json.load(open(tmp_path / "gamescore.json", encoding="utf-8"))
    assert list(cache["games"]) == ["2026020180"] and cache["games"]["2026020180"]["complete"]
    check_public(out)


def test_recent_games_are_scored_again_with_the_nhls_corrections(tmp_path):
    """The NHL corrects its game sheets after the game (here it later credited
    Verhaeghe with a fourth shot), so a game inside the look-back window is scored
    again on every run; a run without the shot values keeps the full score."""
    import copy
    fx = json.load(open(GAME, encoding="utf-8"))
    out = _run_update(tmp_path, fx, "2026-10-26", fx["shots"])
    v = next(p for p in out["teams"]["FLA"]["skaters"] if p["name"] == "Carter Verhaeghe")
    assert v["box"]["sog"] == 3
    fixed = copy.deepcopy(fx)
    shot = copy.deepcopy(next(e for e in fixed["pbp"]["plays"] if e["typeDescKey"] == "shot-on-goal"
                              and e["details"].get("shootingPlayerId") != 8477409))
    shot["details"]["shootingPlayerId"] = 8477409
    shot["eventId"] = 99999
    fixed["pbp"]["plays"].append(shot)
    out = _run_update(tmp_path, fixed, "2026-10-26", fx["shots"])
    v2 = next(p for p in out["teams"]["FLA"]["skaters"] if p["name"] == "Carter Verhaeghe")
    assert v2["box"]["sog"] == 4 and abs(v2["o"][0] - v["o"][0] - 0.075) < 0.006
    # two days on, MoneyPuck's file cannot be read: the full score stays
    out = _run_update(tmp_path, fixed, "2026-10-27", [])
    assert out["teams"]["FLA"]["inputs"] == "full"


def test_last_game_without_shift_charts_is_production_and_final_after_two_days(tmp_path):
    fx = json.load(open(GAME, encoding="utf-8"))
    bare = dict(fx, shifts=[])
    t = _run_update(tmp_path, bare, "2026-10-26", [])["teams"]["FLA"]
    assert t["inputs"] == "production" and t["pending"]
    assert all(p["o"][1:] == [0, 0, 0] for p in t["skaters"])
    # the shift charts arrive: estimated replaces production
    t = _run_update(tmp_path, fx, "2026-10-26", [])["teams"]["FLA"]
    assert t["inputs"] == "estimated" and t["pending"]
    # a later run without them keeps the estimated score, and two days on it is final
    t = _run_update(tmp_path, bare, "2026-10-27", [])["teams"]["FLA"]
    assert t["inputs"] == "estimated" and not t["pending"]


def test_stand_in_shot_values_follow_distance_and_shot_type():
    from nhlda import gamescore as G
    fx, xg, rates, q = _game_inputs()
    plays = [e for e in fx["pbp"]["plays"] if e.get("typeDescKey") in ("goal", "shot-on-goal", "missed-shot")
             and (e.get("periodDescriptor") or {}).get("periodType") != "SO"]
    flat = G.estimate_shots(fx["pbp"], {}, 0.064)
    assert len(flat) == len({(e["periodDescriptor"]["number"], G._secs(e["timeInPeriod"]),
                              (e["details"].get("scoringPlayerId") or e["details"].get("shootingPlayerId"))) for e in plays})
    assert set(flat.values()) == {0.064}
    # MoneyPuck-style rows: close shots are worth more, tips more than slap shots up close
    rows = []
    for dist, base in ((5, 0.30), (12, 0.15), (22, 0.08), (33, 0.04), (55, 0.015)):
        for typ, mult in (("WRIST", 1.0), ("TIP", 1.3), ("SLAP", 0.8)):
            rows += [{"event": "SHOT", "shotDistance": str(dist), "shotType": typ, "xGoal": str(base * mult)}] * 40
    rows += [{"event": "BLOCK", "shotDistance": "5", "shotType": "WRIST", "xGoal": "0.9"}] * 40   # not unblocked: left out
    table = G.shot_table(rows)
    assert abs(table[(G._dist_bin(5), "WRIST")][0] - 0.30) < 1e-9 and table[(G._dist_bin(5), "")][1] == 120
    assert table[(G._dist_bin(5), "")][0] > table[(G._dist_bin(55), "")][0]
    # NHL play-by-play: rink coordinates in feet, nets at x = +89 and -89
    def play(kind, x, y, zone, typ, sec, per=1, ptype="REG"):
        return {"typeDescKey": kind, "timeInPeriod": f"00:{sec:02d}",
                "periodDescriptor": {"number": per, "periodType": ptype},
                "details": {"xCoord": x, "yCoord": y, "zoneCode": zone, "shotType": typ, "shootingPlayerId": 7}}
    pbp = {"plays": [play("shot-on-goal", 84, 0, "O", "tip-in", 1),        # 5 ft, a tip
                     play("shot-on-goal", -84, 0, "O", "slap", 2),          # 5 ft at the other net, a slap shot
                     play("missed-shot", 34, 0, "O", "wrist", 3),           # 55 ft
                     play("shot-on-goal", -30, 0, "D", "wrist", 4),         # from his own zone: 119 ft
                     play("shot-on-goal", 77, 0, "O", "bat", 5),            # 12 ft, a type MoneyPuck does not list
                     play("blocked-shot", 80, 0, "O", "wrist", 6),          # blocked: not valued
                     play("shot-on-goal", 84, 0, "O", "wrist", 1, 5, "SO")]}  # shootout: left out
    est = G.estimate_shots(pbp, table, 0.064)
    assert len(est) == 5
    assert abs(est[(1, 1, 7)] - 0.39) < 1e-9 and abs(est[(1, 2, 7)] - 0.24) < 1e-9
    assert abs(est[(1, 3, 7)] - 0.015) < 1e-9
    assert est[(1, 4, 7)] == 0.064                                         # no band that far: the league average
    assert abs(est[(1, 5, 7)] - 0.15 * (1.0 + 1.3 + 0.8) / 3) < 1e-9       # the distance band alone
    # a cell with too few shots falls back to its distance band, then to the league average
    thin = {k: (v[0], 5) if k[1] else v for k, v in table.items()}
    assert abs(G.estimate_shots(pbp, thin, 0.064)[(1, 1, 7)] - table[(G._dist_bin(5), "")][0]) < 1e-9
    assert set(G.estimate_shots(pbp, {}, 0.064).values()) == {0.064}


def test_estimated_game_score_tracks_the_full_score():
    """The stand-in values keep each player's Game Score close to MoneyPuck's (here with
    the league average alone; the build also uses distance and shot type, which on 18
    games of 2026-10-28 to 30 gave a correlation of 0.97)."""
    import statistics
    from nhlda import gamescore as G
    fx, xg, rates, q = _game_inputs()
    rows = [r for r in fx["shifts"] if r.get("typeCode") == 517]
    full = G.breakdown(fx["pbp"], rows, xg, rates, q, box=fx.get("box"))
    est = G.breakdown(fx["pbp"], rows, G.estimate_shots(fx["pbp"], {}, rates["shot"]), rates, q,
                      box=fx.get("box"), estimated=True)
    prod = G.breakdown(fx["pbp"], [], {}, rates, q, box=fx.get("box"))
    assert (full["inputs"], est["inputs"], prod["inputs"]) == ("full", "estimated", "production")
    assert all(p["toi"] for t in prod["teams"].values() for p in t["skaters"])        # the boxscore's TOI

    def pairs(alt):
        out = []
        for ab in full["teams"]:
            F = {p["id"]: p["gs"] for p in full["teams"][ab]["skaters"]}
            out += [(F[p["id"]], p["gs"]) for p in alt["teams"][ab]["skaters"] if p["id"] in F]
        return out
    pe, pp = pairs(est), pairs(prod)
    corr = lambda ps: statistics.correlation([a for a, _ in ps], [b for _, b in ps])
    assert corr(pe) > 0.9 and corr(pe) > corr(pp)
    assert sum((a >= 0) == (b >= 0) for a, b in pe) > sum((a >= 0) == (b >= 0) for a, b in pp)


def test_sample_carries_a_last_game_for_every_team():
    d = json.load(open(SAMPLE, encoding="utf-8"))
    gs = d["gamescore"]["teams"]
    assert sorted(gs) == sorted(d["teams"])
    for ab, t in gs.items():
        assert t["date"] < d["date"] and t["result"] in ("W", "L", "OTL") and t["opp"] in d["teams"]
        assert t["skaters"] and all(len(p["o"]) == 4 and len(p["d"]) == 4 for p in t["skaters"])


# ---------- 1.17.0: the game-day watch (John, 2026-11-02) ----------
def _watch_case():
    """Detroit at Florida, 7:00 PM Eastern on 2026-11-02 (00:00 UTC), as built at the
    9:00 AM Central update: Daily Faceoff named Akira Schmid with no report then."""
    data = {"date": "2026-11-02", "generated_utc": "2026-11-02T15:01:20Z",
            "slate": {"games": [
                {"away": "DET", "home": "FLA", "start_utc": "2026-11-03T00:00:00Z", "state": "FUT"},
                {"away": "CHI", "home": "BOS", "start_utc": "2026-11-02T18:00:00Z", "state": "LIVE"}]}}
    entry = lambda name, strength=None, by=None, at=None: {
        "goalie_name": name, "strength": strength, "confirmed": strength == "Confirmed",
        "source_name": by, "source_url": None, "reported_at": at}
    morning = {"DET": entry("Daniil Tarasov"), "FLA": entry("Akira Schmid"),
               "CHI": entry("Spencer Knight"), "BOS": entry("Jeremy Swayman")}
    lines = {"players": [{"name": "Brady Tkachuk", "groupIdentifier": "f1", "positionIdentifier": "lw"},
                         {"name": "Sam Bennett", "groupIdentifier": "f1", "positionIdentifier": "c"},
                         {"name": "Seth Jones", "groupIdentifier": "d2", "positionIdentifier": "rd",
                          "season": {"points": 17}}]}
    cache = {"goalies": {"date": "2026-11-02", "by_team": morning},
             "lines": {"FLA": {"combinations": lines}, "DET": {"combinations": {"players": []}}}}
    return data, cache, morning, entry, lines


def test_watch_builds_when_daily_faceoff_names_another_goalie():
    import copy
    import datetime as dt
    from nhlda import watch as W
    data, cache, morning, entry, _ = _watch_case()
    at = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    now = at("2026-11-02T16:00:00Z")                       # 10:00 AM Central
    same = copy.deepcopy(morning)
    assert W.decide(data, cache, now, same, {}) == []      # nothing changed: no build
    news = copy.deepcopy(morning)
    news["FLA"] = entry("Jacob Markstrom", "Confirmed", "Jordan McPherson", "2026-11-02T15:51:50.102Z")
    why = W.decide(data, cache, now, news, {})
    assert len(why) == 1 and why[0].startswith("FLA goalie: Akira Schmid (no report) -> Jacob Markstrom (Confirmed)")
    assert "Jordan McPherson" in why[0]
    # the same goalie, now confirmed, or a newer report about him, also builds
    sure = copy.deepcopy(morning)
    sure["DET"] = entry("Daniil Tarasov", "Likely", "Max Bultman", "2026-11-02T16:20:00Z")
    assert [w[:3] for w in W.decide(data, cache, now, sure, {})] == ["DET"]
    # a game under way is left alone (its pick locked at puck drop)
    live = copy.deepcopy(morning)
    live["BOS"] = entry("Joonas Korpisalo", "Confirmed", "Someone", "2026-11-02T17:00:00Z")
    assert W.decide(data, cache, now, live, {}) == []
    # Daily Faceoff did not answer: no way to tell, no build
    assert W.decide(data, cache, now, None, {}) == []
    # no answer saved for today (the last build read another day): build to read it
    old = copy.deepcopy(cache)
    old["goalies"]["date"] = "2026-11-01"
    assert len(W.decide(data, old, now, same, {})) == 2


def test_watch_window_and_lineups_near_puck_drop():
    import copy
    import datetime as dt
    from nhlda import watch as W
    data, cache, morning, _, lines = _watch_case()
    at = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    # before 9:30 AM Central, or on a file for another day, the watch never builds
    assert W.upcoming(data, at("2026-11-02T15:20:00Z")) == []                  # 9:20 AM CST
    assert [g["home"] for g in W.upcoming(data, at("2026-11-02T15:35:00Z"))] == ["FLA"]
    assert W.upcoming(dict(data, date="2026-11-01"), at("2026-11-02T16:00:00Z")) == []
    assert W.upcoming(data, at("2026-11-03T00:05:00Z")) == []                  # after puck drop
    # lineups are read only within 90 minutes of puck drop
    assert W.lines_due(W.upcoming(data, at("2026-11-02T22:00:00Z")), at("2026-11-02T22:00:00Z")) == []
    near = at("2026-11-02T23:00:00Z")
    assert W.lines_due(W.upcoming(data, near), near) == ["DET", "FLA"]
    # the same lineup with fresher stats is no change; a scratch is
    fresh = copy.deepcopy(lines)
    fresh["players"][2]["season"] = {"points": 18}
    fresh["updatedAt"] = "2026-11-02T22:55:00Z"
    assert W.decide(data, cache, near, morning, {"FLA": fresh}) == []
    scratch = copy.deepcopy(lines)
    scratch["players"][1]["name"] = "Cole Schwindt"
    scratch["sourceName"] = "George Richards"
    why = W.decide(data, cache, near, morning, {"FLA": scratch, "DET": None})
    assert why == ["FLA lineup changed on Daily Faceoff (George Richards)"]


def test_watch_check_builds_a_due_main_update_without_reading_daily_faceoff(tmp_path, monkeypatch):
    import datetime as dt
    from nhlda import watch as W
    data, cache, morning, entry, _ = _watch_case()
    p = tmp_path / "latest.json"
    p.write_text(json.dumps(data))
    (tmp_path / "dfo_cache.json").write_text(json.dumps(cache))
    calls = []
    monkeypatch.setattr(W, "read_goalies", lambda d: calls.append(d) or dict(morning, FLA=entry("Jacob Markstrom", "Confirmed", "Jordan McPherson", "2026-11-02T15:51:50Z")))
    monkeypatch.setattr(W, "read_lines", lambda t: calls.append(t) or None)
    # 4:50 PM Central: the 4:45 PM main update is due, so it builds without asking
    build, why = W.check(str(p), str(tmp_path), dt.datetime(2026, 11, 2, 22, 50, tzinfo=dt.timezone.utc))
    assert build and "before the 22:45Z" in why[0] and calls == []
    # 10:30 AM Central: the morning update is done; Daily Faceoff's news builds
    build, why = W.check(str(p), str(tmp_path), dt.datetime(2026, 11, 2, 16, 30, tzinfo=dt.timezone.utc))
    assert build and why[0].startswith("FLA goalie:") and calls == ["2026-11-02"]


def test_watch_runs_on_the_standard_library_alone():
    """The workflow's guard job runs it before pip installs anything."""
    import ast
    import sys
    for mod in ("watch", "dfo", "schedule"):
        tree = ast.parse(open(os.path.join(ROOT, "nhlda", mod + ".py"), encoding="utf-8").read())
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and not n.level}
        assert names <= set(sys.stdlib_module_names), (mod, names - set(sys.stdlib_module_names))


def test_goalie_report_time_and_strength_reach_the_data_file():
    from nhlda.build import _goalie
    g = _goalie({"name": "Jacob Markstrom", "source": "DFO_CONFIRMED",
                 "intel": {"reported_by": "Jordan McPherson", "source_url": "https://x.com/J_McPherson1126/status/1",
                           "reported_at": "2026-11-02T15:51:50.102Z", "strength": "Confirmed", "detail": "x"}})
    assert (g["reported_by"], g["reported_at"], g["report_strength"]) == ("Jordan McPherson", "2026-11-02T15:51:50.102Z", "Confirmed")
    g = _goalie({"name": "Daniil Tarasov", "source": "DFO_PROJECTED", "intel": {"reported_by": None, "reported_at": None}})
    assert g["reported_at"] is None and g["report_strength"] is None


# ---------- 1.18.0: the market blend test (John, 2026-11-02) ----------
def _blend_ledger(tmp_path, rows):
    from nhlda import core as C
    C._append_ledger(str(tmp_path), [dict({k: "" for k in C.LEDGER_FIELDS}, **r) for r in rows])


def test_market_blend_counts_only_games_with_a_line_from_its_start_date(tmp_path):
    from nhlda import core as C
    _blend_ledger(tmp_path, [
        # before the blend was chosen: never counted
        {"date": "2026-11-01", "matchup": "UTA @ PHI", "projected": "UTA", "win_prob": 0.59, "market_prob": 0.59, "correct": 0},
        # Detroit at Florida: the model 53% on Detroit, the market 37.9%; Florida wins
        {"date": "2026-11-02", "matchup": "DET @ FLA", "projected": "DET", "win_prob": 0.5303, "market_prob": 0.3787, "correct": 0},
        {"date": "2026-11-02", "matchup": "VGK @ BOS", "projected": "BOS", "win_prob": 0.60, "market_prob": "", "correct": 1}])
    d = C.blend_deltas(C.read_ledger(str(tmp_path)))
    import math
    want = -math.log(1 - (0.5 * 0.5303 + 0.5 * 0.3787)) + math.log(1 - 0.5303)
    assert len(d) == 1 and abs(d[0] - want) < 1e-9 and d[0] < 0      # the blend did better on it
    b = C.market_blend(str(tmp_path))
    assert (b["games"], b["in_use"], b["weight"], b["since"], b["min_games"], b["required_t"]) == (1, False, 0.5, "2026-11-02", 40, -2.0)


def test_market_blend_goes_into_use_only_after_a_clear_lead_and_stays(tmp_path):
    from nhlda import core as C
    rows = []
    for i in range(45):          # the market is right far more often than the model's 56% picks
        won = i % 4 == 0         # 12 of 45
        rows.append({"date": "2026-11-%02d" % (3 + i // 10), "matchup": f"G{i}", "projected": "X",
                     "win_prob": 0.56, "market_prob": 0.40, "correct": int(won)})
    _blend_ledger(tmp_path, rows[:30])
    b = C.market_blend(str(tmp_path))
    assert not b["in_use"] and b["games"] == 30                      # 40 games are required
    _blend_ledger(tmp_path, rows[30:])
    b = C.market_blend(str(tmp_path), now_utc="2026-11-08 15:01")
    assert b["in_use"] and b["adopted_utc"] == "2026-11-08 15:01" and b["games"] == 45 and b["t"] < -2.0
    # once in use the test stands as it passed, whatever settles later
    _blend_ledger(tmp_path, [dict(rows[0], matchup="later", correct=1)] * 30)
    again = C.market_blend(str(tmp_path))
    assert again["in_use"] and again["games"] == 45 and again["t"] == b["t"]
    assert json.load(open(tmp_path / "learning.json"))["market_blend"]["adopted_utc"] == "2026-11-08 15:01"


def test_blend_game_publishes_the_average_only_when_in_use_and_a_line_exists():
    from nhlda import core as C, raa_core as R
    off, on = {"in_use": False, "weight": 0.5}, {"in_use": True, "weight": 0.5}
    mk = {"home_prob": 0.6213, "away_prob": 0.3787}
    assert C.blend_game(0.4697, mk, off) == (0.4697, None)
    assert C.blend_game(0.4697, None, on) == (0.4697, None)
    p, own = C.blend_game(0.4697, mk, on)
    assert own == 0.4697 and abs(p - 0.5455) < 1e-9              # Florida 54.6% instead of 47.0%
    assert R.tier_for(p) == (9.1, "MODERATE") and R.tier_for(0.53) == (6.0, "LOW") and R.tier_for(0.6) == (20.0, "HIGH")
    # the tiers match the model's own
    w = R.compute_win_probability(60.0, 55.0)
    assert R.tier_for(w["home_win_prob"]) == (w["confidence_pct"], w["confidence_tier"])


# ---------- 1.19.0: each player's trend over his team's last ten games ----------
# John, 2026-11-05: "for the graph for my teams also include a trend line for the
# performance help or hurt to identify players that are dragging down the team"

def test_trend_average_slope_and_dragging_flag():
    from nhlda import gamescore as G
    assert G.series_stats([None, None]) == (0, None, None)
    n, avg, slope = G.series_stats([0.5, None, 0.3, 0.1, -0.1, -0.3, None, -0.5])
    assert n == 6 and abs(avg - 0.0) < 1e-9 and slope < 0
    n, avg, slope = G.series_stats([1.0, 2.0, 3.0])
    assert abs(slope - 1.0) < 1e-9
    # below zero and falling, five games or more: dragging
    assert G.dragging(*G.series_stats([0.1, -0.1, -0.2, -0.3, -0.4]))
    # below zero but climbing out of it: not flagged
    assert not G.dragging(*G.series_stats([-0.6, -0.4, -0.2, -0.1, 0.0]))
    # too few games, or above zero: not flagged
    assert not G.dragging(*G.series_stats([None] * 6 + [-0.5, -0.6, -0.7, -0.8]))
    assert not G.dragging(*G.series_stats([0.4, 0.3, 0.2, 0.1, 0.05]))


def test_history_keeps_each_teams_last_ten_games_and_replaces_a_rescore():
    from nhlda import gamescore as G
    hist = {"season": 2026, "teams": {}}
    def game(day, p_gs, opp="CHI"):
        return {"date": f"2026-10-{day:02d}", "inputs": "full",
                "teams": {"FLA": {"opp": opp, "home": True, "result": "W",
                                  "skaters": [{"id": 1, "gs": p_gs}, {"id": 2, "gs": -p_gs}]}}}
    for day in range(1, 13):
        G.hist_add(hist, 2026020000 + day, game(day, day / 10))
    rows = hist["teams"]["FLA"]
    assert [r["date"] for r in rows] == [f"2026-10-{d:02d}" for d in range(3, 13)]
    G.hist_add(hist, 2026020012, game(12, 2.0))                    # the NHL's correction, scored again
    assert len(hist["teams"]["FLA"]) == 10 and hist["teams"]["FLA"][-1]["gs"] == {"1": 2.0, "2": -2.0}
    G.hist_add(hist, 2026020001, game(1, 9.9))                     # older than the last ten: dropped
    assert hist["teams"]["FLA"][0]["date"] == "2026-10-03"
    latest = {"FLA": {"skaters": [{"id": 1, "gs": 2.0}, {"id": 2, "gs": -2.0}, {"id": 3, "gs": 0.1}]}}
    G.add_trends(latest, hist)
    t = latest["FLA"]
    assert len(t["trend_games"]) == 10 and t["trend_games"][-1] == {"game": 2026020012, "date": "2026-10-12",
                                                                     "opp": "CHI", "home": True, "result": "W"}
    p1, p2, p3 = t["skaters"]
    assert p1["trend"]["n"] == 10 and p1["trend"]["slope"] > 0 and not p1["trend"]["drag"]
    assert p2["trend"]["avg"] < 0 and p2["trend"]["slope"] < 0 and p2["trend"]["drag"]
    assert p3["trend"] == {"g": [None] * 10, "n": 0, "avg": None, "slope": None, "drag": False}
    check_public({"gamescore": {"teams": latest}})


TOI_HTML = """<table>
<tr><td class="playerHeading + border" colspan="8">13 BARKOV, ALEKSANDER</td></tr>
<tr><td>Shift #</td><td>Per</td><td>Start of Shift</td><td>End of Shift</td><td>Duration</td></tr>
<tr class="evenColor"><td>1</td><td>1</td><td>0:00 / 20:00</td><td>0:45 / 19:15</td><td>00:45</td><td>&nbsp;</td></tr>
<tr class="oddColor"><td>2</td><td>OT</td><td>1:10 / 3:50</td><td>1:52 / 3:08</td><td>00:42</td><td>G</td></tr>
<tr><td>TOT</td><td>2</td><td>1:27</td></tr>
<tr><td class="playerHeading + border" colspan="8">99 NOBODY, NOT ROSTERED</td></tr>
<tr class="evenColor"><td>1</td><td>2</td><td>5:00 / 15:00</td><td>5:30 / 14:30</td><td>00:30</td><td>&nbsp;</td></tr>
<tr><td class="playerHeading + border" colspan="8">5 EKBLAD, AARON</td></tr>
<tr class="evenColor"><td>1</td><td>3</td><td>10:05 / 9:55</td><td>11:00 / 9:00</td><td>00:55</td><td>&nbsp;</td></tr>
</table>"""


def test_shifts_from_the_nhls_time_on_ice_report():
    """The NHL's shift chart feed later empties for many games (2026-10-13 to 10-28 on
    2026-11-05), so the official TH and TV reports supply the shifts."""
    from nhlda import gamescore as G
    pbp = {"rosterSpots": [{"teamId": 13, "playerId": 8477493, "sweaterNumber": 13},
                           {"teamId": 13, "playerId": 8477932, "sweaterNumber": 5},
                           {"teamId": 16, "playerId": 1, "sweaterNumber": 99}]}
    rows = G.toi_report_rows(TOI_HTML, 13, pbp)
    assert rows == [
        {"typeCode": 517, "playerId": 8477493, "period": 1, "startTime": "0:00", "endTime": "0:45"},
        {"typeCode": 517, "playerId": 8477493, "period": 4, "startTime": "1:10", "endTime": "1:52"},
        {"typeCode": 517, "playerId": 8477932, "period": 3, "startTime": "10:05", "endTime": "11:00"}]


def test_update_scores_missing_older_games_from_the_schedule(tmp_path):
    """The first run scores the older games of each team's last ten (the season's
    history), and the latest game carries every skater's trend."""
    import asyncio
    from nhlda import gamescore as G
    fx = json.load(open(GAME, encoding="utf-8"))

    class Sched(_FakeFetch):
        async def get(self, url, params=None, as_text=False, **kw):
            if "/schedule/" in url:
                g = {"id": 2026020180, "gameType": 2, "gameState": "OFF",
                     "homeTeam": {"abbrev": "CHI"}, "awayTeam": {"abbrev": "FLA"}}
                return {"gameWeek": [{"date": "2026-10-25", "games": [g]}], "previousStartDate": None}
            return await super().get(url, params, as_text, **kw)

    async def fake_rows(session, year, want, table=None):
        return {20180: fx["shots"]} if 20180 in want else {}
    orig = G._shot_rows
    G._shot_rows = fake_rows
    try:
        # ten days on, outside the look-back window: only the history walk finds the game
        out = asyncio.run(G.update(Sched(fx, "2026-11-04"), None, str(tmp_path), "2026-11-04", 2026))
    finally:
        G._shot_rows = orig
    hist = json.load(open(tmp_path / "gamescore_history.json", encoding="utf-8"))
    assert hist["season"] == 2026 and [r["game"] for r in hist["teams"]["FLA"]] == [2026020180]
    t = out["teams"]["FLA"]
    assert t["game_id"] == 2026020180 and t["trend_games"][0]["opp"] == "CHI"
    assert all(p["trend"]["g"] == [p["gs"]] and p["trend"]["n"] == 1 for p in t["skaters"])
