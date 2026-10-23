"""Offline tests for the public engine and the data file. No network."""
import csv
import json
import os
import re
import subprocess

import pytest

from nhlda import core as C
from nhlda.build import check_public, FORMAT
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
    for g in d["slate"]["games"]:
        assert 0 <= g["home_win_prob"] <= 1 and abs(g["home_win_prob"] + g["away_win_prob"] - 1) < 0.002
        assert g["projected"] in (g["home"], g["away"])
        for side in ("home_side", "away_side"):
            assert set(g[side]["pillars"]) >= {"p1_form", "p2_absence", "p3_motivation", "p4_gvi", "p5_friction"}


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


PRIVATE = [r"redacted", r"redacted", r"redacted", r"@redacted\.", r"redacted", r"C:\\\\Users",
           r"sk-ant-", r"AIza", r"ghp_[A-Za-z0-9]{20}", r"github_pat_", r"redacted"]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_OK = ("users.noreply.github.com", "example.com", "example.org")
SKIP_DIRS = (".git", "__pycache__", ".pytest_cache", "node_modules")


def _repo_files():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    if files:
        return files
    return [os.path.relpath(os.path.join(dp, f), ROOT) for dp, dirs, fs in os.walk(ROOT)
            if not any(part in SKIP_DIRS for part in dp.split(os.sep)) for f in fs]


def test_repository_carries_no_personal_data_or_keys():
    hits = []
    for f in _repo_files():
        if f.endswith((".png", ".npy")) or f.replace(os.sep, "/") == "tests/test_engine.py":
            continue
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for pat in PRIVATE:
            if re.search(pat, text):
                hits.append(f"{f}: {pat}")
        for m in EMAIL.finditer(text):
            if not m.group(0).lower().endswith(EMAIL_OK):
                hits.append(f"{f}: email address {m.group(0)}")
    assert not hits, hits
