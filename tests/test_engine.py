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
    assert names == ["Juraj Slafkovsky", "Sam Montembeault"]       # Xhekaj is also in tonight's lineup
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
    assert both.match_player("MTL", "Florian Xhekaj") is None            # in tonight's lineup: playing
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


def test_install_identity_is_this_app_only():
    """Browsers resolve a manifest id against the site's origin, not the app's folder.
    "./" claimed all of the shared github.io site, so a second app installed from it
    was reported as already installed and the install failed."""
    with open(os.path.join(ROOT, "app", "manifest.webmanifest"), encoding="utf-8") as f:
        m = json.load(f)
    assert m["id"] == "/nhl-daily-analytics/" and m["scope"] == "./"
