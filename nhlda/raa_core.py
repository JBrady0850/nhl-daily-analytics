# =============================================================================
# NHL RAA CORE (model V34.7, public build)
# Five-pillar Relative Advantage Assessment on a single normalized scale.
#
# SCALE CONTRACT
#   Every pillar returns a delta in RAA POINTS from a neutral baseline of 50.0.
#   adj_raa = RAA_BASELINE + W1*P1 + W2*P2 + W3*P3 + W4*P4 + W5*P5  (W = 1.0 at baseline; V34.5 learning)
#   The scale is fixed by P1_FORM_GAIN = 200. P1_FORM_GAIN and WIN_PROB_K are
#   multiplicatively confounded, so only their product is identified by the
#   likelihood; the scale is pinned for interpretability and K is fit to it.
#   Observed 2025-26 matchup deltas: p25=4.5 p50=9.2 p75=14.9 p90=21.3 max=42.2.
#
#   V32 defect this replaces: P1 evaluated to roughly 0.35 while P3 evaluated
#   to 5.0, so the pillar the model spec weights at 70 percent contributed about
#   3 percent of the score. All gains below are fit on 2025-26 outcomes.
# =============================================================================

import math

RAA_BASELINE = 50.0

# --- Pillar gains (calibrated: see calibration_report.json) -------------------
P1_FORM_GAIN      = 200.0   # RAA points per unit of shot-share above 0.500
P1_L5_WEIGHT      = 0.50    # model spec Section 11: L5 weighted 0.50
P1_SSN_WEIGHT     = 0.20    # model spec Section 11: season weighted 0.20
P1_GDIFF_GAIN     = 2.122   # RAA points per goal of season goal differential
P1_VENUE_WEIGHT   = 0.12    # weight of the venue-relevant split inside the form blend (see calibration_report.json)
P1_VENUE_SHRINK_K = 8       # venue-split shrinkage half-life in games (nhl_data.py)
P1_L3_GAIN        = -1.10   # RAA points per unit of L3-vs-L5 shot-share momentum (see calibration_report.json)

P2_ABSENCE_FLOOR  = -7.5    # model spec Section 11: STACKED_PENALTY_CEILING
P2_APEX_RECOVERY  = 2.5     # model spec Section 11: Next-Man-Up surge offset

P3_MOTIV_GAIN     = 5.0     # model spec Section 11: +5.0 inside the wildcard cut
P3_MIN_PROGRESS   = 0.35    # motivation is inert before 35 percent of the season

P4_GVI_GAIN       = 120.0   # RAA points per unit of save pct above league mean
P4_LEAGUE_SV      = 0.9015  # 2025-26 league average all-situations save pct
P4_TOI_FLOOR_MIN  = 180.0   # model spec Section 11: 180-minute GVI window
P4_RUST_PENALTY   = -3.5    # applied on genuine layoff, not on a win streak
P4_RUST_DAYS      = 10      # days without a start that counts as rust
P4_NO_SAMPLE      = -5.0    # model spec Section 11: sub-180-minute penalty

P5_REST_GAIN      = 0.555   # RAA points per day of rest advantage
P5_B2B_PENALTY    = -2.286  # second half of a back-to-back

# --- Win probability (calibrated) --------------------------------------------
HOME_ADVANTAGE    = 1.071   # RAA points of home ice
WIN_PROB_K        = 0.0261  # logistic slope on the RAA delta

# --- Delta gate (calibrated to the 2025-26 operating curve) -------------------
GATE_APEX_RATIO   = 0.70    # model spec Section 11: SLATE_APEX_DELTA * 0.70
GATE_FLOOR        = 13.0    # calibrated: 32% of slate authorized at 64.2% accuracy
GATE_MAX_AUTH     = 0.40    # never authorize more than 40 percent of a slate

# V34.5.0: learned settings. Each pillar value is multiplied by its weight in
# the adjusted RAA; the learning cycle (below) tunes these weights, home ice
# and the win-probability slope from settled games. 1.0 everywhere is the
# 2025-26 calibration, the baseline the learning starts from.
PILLAR_WEIGHTS    = [1.0, 1.0, 1.0, 1.0, 1.0]
LEARN_BASELINE    = {"k": WIN_PROB_K, "h": HOME_ADVANTAGE, "w": [1.0, 1.0, 1.0, 1.0, 1.0]}
LEARN_BATCH       = 10      # a cycle runs each time 10 more settled games are on the ledger
LEARN_PRIOR_GAMES = 200     # the baseline counts as this many games of evidence
LEARN_STEP_W      = 0.10    # most a pillar weight may move in one cycle
LEARN_STEP_H      = 0.50    # most home ice may move in one cycle, RAA points
LEARN_STEP_K      = 0.10    # most the slope may move in one cycle, relative
LEARN_BOUNDS      = {"w": (0.25, 2.0), "h": (-1.0, 4.0), "k": (0.5, 2.0)}   # k relative to baseline


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# =============================================================================
# PILLAR 1 - FORM AND PROCESS  (model spec weight 70 percent)
# V32 defect: l5_xgf and season_xgf were assigned from the same MoneyPuck
# field, and that field was read off the 5on4 power-play row rather than the
# all-situations row, so every team scored 0.85-0.92 and the pillar had no
# discriminating power at all.
#
# V34.1 addition: venue_shot_share carries this team's shot share in games at
# the SAME venue as tonight's game (home split if this team is home, road
# split if this team is away), shrunk toward its season rate by sample size
# (nhl_data.py TeamGameLog.venue_shot_share). l3_shot_share is a 3-game
# recency window; it enters only as a momentum term against L5, not as a
# level, because a rolling-origin backtest on 1,259 2025-26 games (see
# venue_l3_experiment.py, calibration_report.json) showed a raw L3-for-L5
# swap costs both accuracy and log loss, and L3-vs-L5 momentum fits a small
# NEGATIVE coefficient out of sample: a team that outshot its L5 rate over
# its last 3 games skews very slightly toward mean reversion, not continued
# form, on this sample. P1_L3_GAIN is shipped at its honestly-fit sign and
# magnitude rather than zeroed out or flipped positive to match intuition.
# =============================================================================
def pillar_1(l5_shot_share, ssn_shot_share, ssn_goal_diff_pg, l5_xgf=None, ssn_xgf=None,
             venue_shot_share=None, l3_shot_share=None):
    l5  = l5_shot_share if l5_xgf is None else (0.6 * l5_shot_share + 0.4 * l5_xgf)
    ssn = ssn_shot_share if ssn_xgf is None else (0.6 * ssn_shot_share + 0.4 * ssn_xgf)

    weighted = [(P1_L5_WEIGHT, l5), (P1_SSN_WEIGHT, ssn)]
    if venue_shot_share is not None:
        weighted.append((P1_VENUE_WEIGHT, venue_shot_share))
    wsum = sum(w for w, _ in weighted)
    form = sum(w * v for w, v in weighted) / wsum

    pts  = P1_FORM_GAIN * (form - 0.500)
    pts += P1_GDIFF_GAIN * ssn_goal_diff_pg
    if l3_shot_share is not None:
        pts += P1_L3_GAIN * (l3_shot_share - l5_shot_share)
    return _clamp(pts, -20.0, 20.0)


# =============================================================================
# PILLAR 2 - AVAILABILITY / ABSENCE
# V32 defect: absence_val was hardcoded 0.0 and is_apex hardcoded False, so
# this pillar was inert on every team on every slate.
# Absence is now derived from roster-versus-recent-lineup, weighted by each
# missing player's share of team ice time and scoring.
# =============================================================================
def pillar_2(absence_val=0.0, is_apex=False):
    if is_apex:
        return max(absence_val, P2_ABSENCE_FLOOR) + P2_APEX_RECOVERY
    return max(absence_val, P2_ABSENCE_FLOOR * 1.6)


def absence_impact(missing_players):
    """missing_players: [{'toi_share': 0.0-1.0, 'points_share': 0.0-1.0}, ...]"""
    total = 0.0
    for p in missing_players:
        weight = (0.55 * p.get("toi_share", 0.0)) + (0.45 * p.get("points_share", 0.0))
        total += weight * 42.0
    return -total


# =============================================================================
# PILLAR 3 - MOTIVATION DELTA
# V32 defect: the wildcard cutline was taken as index 7 of a points-sorted
# conference list, so every team at or above the line received gap 0 and the
# full +5.0. Sixteen-plus teams got the same modifier every night. It also
# fired in October when the standings carry no information.
# =============================================================================
def pillar_3(wildcard_gap, is_division_leader_locked, games_played, season_length=84):
    progress = _clamp(games_played / float(season_length), 0.0, 1.0)
    if progress < P3_MIN_PROGRESS:
        return 0.0
    if is_division_leader_locked:
        return 0.0
    if wildcard_gap is None or wildcard_gap > 4:
        return 0.0
    urgency = 1.0 - (abs(wildcard_gap) / 5.0)
    ramp = (progress - P3_MIN_PROGRESS) / (1.0 - P3_MIN_PROGRESS)
    return P3_MOTIV_GAIN * urgency * ramp


# =============================================================================
# PILLAR 4 - GOALTENDER VOLATILITY INDEX
# V32 defects: every MoneyPuck goalie column it requested
# (savedShotsOnGoal, goalsAgainst, shutouts, highDangerSavePercentage,
# goals_saved_above_expected) is absent from goalies.csv, so all of them
# resolved to 0.0. The 180-minute window was checked against a 3-game history
# that tops out at 180 minutes, so nearly every goalie tripped the floor. And
# three consecutive wins applied a -3.5 "rust" penalty, penalizing a hot
# goaltender. Rust now means an actual layoff.
# =============================================================================
def pillar_4(goalie_history, starter_confirmed, days_since_last_start=3, is_recall=False):
    if not starter_confirmed:
        return None, {"g_confirm_gate": "BLOCKED", "rust_flag": False, "rust_penalty": False}

    total_toi = sum(e[0] for e in goalie_history)
    flags = {"g_confirm_gate": "CLEARED", "rust_flag": False, "rust_penalty": False,
             "window_toi_min": round(total_toi, 1)}

    if total_toi < P4_TOI_FLOOR_MIN:
        pts = P4_NO_SAMPLE * (1.0 - (total_toi / P4_TOI_FLOOR_MIN))
        flags["rust_flag"] = True
    else:
        w_sv = sum(e[0] * e[1] for e in goalie_history) / total_toi
        pts = P4_GVI_GAIN * (w_sv - P4_LEAGUE_SV)
        flags["weighted_sv_pct"] = round(w_sv, 4)

    if days_since_last_start >= P4_RUST_DAYS or is_recall:
        pts += P4_RUST_PENALTY
        flags["rust_flag"] = True
        flags["rust_penalty"] = True

    return _clamp(pts, -12.0, 12.0), flags


# =============================================================================
# PILLAR 5 - SITUATIONAL FRICTION
# V32 defect: is_road_fav was set to `not is_home`, so it meant "is the away
# team" rather than "is the road favorite", and the term was subtracted from
# the total, inverting its sign. Friction now carries rest and travel load,
# which the backtest shows is the single strongest situational signal on the
# board (a rested team against a back-to-back opponent is worth roughly
# 4 RAA points).
# =============================================================================
def pillar_5(rest_days, opp_rest_days, is_back_to_back=None, opp_back_to_back=None):
    rest = _clamp(rest_days, 0, 5)
    opp = _clamp(opp_rest_days, 0, 5)
    pts = P5_REST_GAIN * (rest - opp)
    b2b = is_back_to_back if is_back_to_back is not None else (rest <= 1)
    ob2b = opp_back_to_back if opp_back_to_back is not None else (opp <= 1)
    if b2b:
        pts += P5_B2B_PENALTY
    if ob2b:
        pts -= P5_B2B_PENALTY
    return _clamp(pts, -8.0, 8.0)


# =============================================================================
# COMPOSITE
# =============================================================================
def compute_raa(inputs):
    p1 = pillar_1(
        inputs.get("l5_shot_share", 0.5),
        inputs.get("ssn_shot_share", 0.5),
        inputs.get("ssn_goal_diff_pg", 0.0),
        inputs.get("l5_xgf"),
        inputs.get("ssn_xgf"),
        inputs.get("venue_shot_share"),
        inputs.get("l3_shot_share"),
    )
    p2 = pillar_2(inputs.get("absence_val", 0.0), inputs.get("is_apex", False))
    p3 = pillar_3(
        inputs.get("wildcard_gap"),
        inputs.get("is_division_leader_locked", False),
        inputs.get("games_played", 0),
        inputs.get("season_length", 84),
    )
    p4, gflags = pillar_4(
        inputs.get("goalie_history", []),
        inputs.get("starter_confirmed", False),
        inputs.get("days_since_last_start", 3),
        inputs.get("is_recall", False),
    )
    p5 = pillar_5(
        inputs.get("rest_days", 3),
        inputs.get("opp_rest_days", 3),
        inputs.get("is_back_to_back"),
        inputs.get("opp_back_to_back"),
    )

    blocked = p4 is None
    w = PILLAR_WEIGHTS
    adj = None if blocked else (RAA_BASELINE + w[0] * p1 + w[1] * p2 + w[2] * p3
                                + w[3] * p4 + w[4] * p5)

    pillars = {
        "p1_form":       round(p1, 3),
        "p2_absence":    round(p2, 3),
        "p3_motivation": round(p3, 3),
        "p4_gvi":        "BLOCKED" if blocked else round(p4, 3),
        "p5_friction":   round(p5, 3),
        "adj_raa":       "BLOCKED" if blocked else round(adj, 3),
        "g_confirm_gate": gflags["g_confirm_gate"],
        "pricing_blocked": blocked,
        "rust_flag":     gflags.get("rust_flag", False),
        "rust_penalty":  gflags.get("rust_penalty", False),
    }
    recovery = {
        "base_raa":  round(RAA_BASELINE + p1 + p2 + p3, 3),
        "hdsv_pct":  inputs.get("hdsv_pct", 0.0),
        "gsax":      inputs.get("gsax", 0.0),
        "hdsv_priority_flag": inputs.get("hdsv_pct", 0.0) > 0.0,
        "gsax_positive_flag": inputs.get("gsax", 0.0) > 0.0,
        "goalie_window": gflags,
    }
    return pillars, recovery


def compute_win_probability(home_adj_raa, away_adj_raa):
    delta = (home_adj_raa + HOME_ADVANTAGE) - away_adj_raa
    p_home = 1.0 / (1.0 + math.exp(-_clamp(WIN_PROB_K * delta, -30, 30)))
    conf = abs(p_home - 0.5) * 200
    # Tiers set from the calibrated delta curve: HIGH corresponds to a delta at
    # or above the 13.0 gate floor (64.2 percent observed accuracy).
    tier = "HIGH" if conf >= 17 else ("MODERATE" if conf >= 8 else "LOW")
    return {
        "home_win_prob": round(p_home, 4),
        "away_win_prob": round(1.0 - p_home, 4),
        "raw_delta": round(delta, 3),
        "confidence_pct": round(conf, 1),
        "confidence_tier": tier,
        "home_advantage_applied": HOME_ADVANTAGE,
    }


def compute_delta_gate(matchup_deltas):
    """matchup_deltas: {matchup_key: abs RAA delta}. Slate-wide, per the model spec
    Section 11. V32 computed SLATE_APEX_DELTA inside a single two-team map, so
    the 'slate' apex was always just that one game's own delta."""
    vals = [v for v in matchup_deltas.values() if isinstance(v, (int, float))]
    if not vals:
        return {"error": "INSUFFICIENT_VALID_RAA", "matchups": {}}
    apex = max(vals)
    gate = max(apex * GATE_APEX_RATIO, GATE_FLOOR)

    ranked = sorted(matchup_deltas.items(),
                    key=lambda kv: kv[1] if isinstance(kv[1], (int, float)) else -1,
                    reverse=True)
    cap = max(1, int(math.floor(len(ranked) * GATE_MAX_AUTH)))
    out = {"slate_apex_delta": round(apex, 3), "dynamic_gate": round(gate, 3),
           "authorization_cap": cap, "matchups": {}}
    authorized = 0
    for key, d in ranked:
        if not isinstance(d, (int, float)):
            out["matchups"][key] = {"verdict": "NO_PLAY_GATE_BLOCKED"}
            continue
        ok = d >= gate and authorized < cap
        if ok:
            authorized += 1
        out["matchups"][key] = {
            "delta": round(d, 3),
            "authorized": ok,
            "verdict": "PLAY_AUTHORIZED" if ok else (
                "NO_PLAY_CONCENTRATION_CAP" if d >= gate else "NO_PLAY_VOLATILITY_RISK"),
        }
    out["authorized_count"] = authorized
    return out


# =============================================================================
# LEARNING CYCLE (V34.5.0)
# Design request (2026-09-30): "A learning function needs to be added that is run every
# 10 games based on the results of the Results Last Settled Slate to improve
# the metrics and prediction." Chosen: calibration plus pillar weights,
# applied automatically only when the new settings test better.
#
# Model: logit P(home win) = K * (sum_i w_i * (home_p_i - away_p_i) + h), the
# engine's own formula with the five raw pillar differences, pillar weights
# w_i, home ice h and slope K.
#
# Each cycle fits these settings on every settled game this season by
# penalized maximum likelihood, the penalty pulling toward the 2025-26
# baseline as if the baseline were LEARN_PRIOR_GAMES games of evidence. The
# fit is stepped toward from the settings in use no further than the
# per-cycle limits (LEARN_STEP_*) and kept inside LEARN_BOUNDS: that is the
# CANDIDATE. A candidate is not put into use on the games it was fit on. It
# is tested forward: every game that settles after it was proposed is
# scored twice (candidate and settings in use, log loss), and the candidate
# goes into use only when it has beaten the settings in use on at least
# LEARN_MIN_FORWARD such unseen games by LEARN_FORWARD_T standard errors.
#
# Why forward testing and not cross-validation (the first V34.5 draft): a
# season simulation (1312 games per season, 12 to 16 seasons per scenario)
# showed that NHL outcomes are so noisy that accepting on cross-validated log
# loss moved the settings on noise. Against the fixed baseline it predicted
# the next games worse in three of four realistic scenarios (by 0.3 to 1.0
# thousandths of log loss per game) and better in one (by 0.17). The forward
# test at two standard errors cost at most 0.15 thousandths in any simulated
# season and gained on average in every scenario where the baseline was off.
# =============================================================================
LEARN_MIN_FORWARD = 40      # unseen games a candidate must be scored on before it can go into use
LEARN_FORWARD_T   = -2.0    # it must beat the settings in use by two standard errors
LEARN_FORWARD_MAX = 300     # the forward record keeps the latest this many games


def set_learned(k=None, home=None, weights=None):
    """Put learned settings into use for this run."""
    global WIN_PROB_K, HOME_ADVANTAGE, PILLAR_WEIGHTS
    if k is not None:
        WIN_PROB_K = float(k)
    if home is not None:
        HOME_ADVANTAGE = float(home)
    if weights is not None and len(weights) == 5:
        PILLAR_WEIGHTS = [float(x) for x in weights]


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-_clamp(z, -30.0, 30.0)))


def _solve(a, b):
    """Gaussian elimination with partial pivoting for a small dense system."""
    n = len(b)
    m = [list(a[i]) + [b[i]] for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-12:
            raise ValueError("singular system")
        m[c], m[piv] = m[piv], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for j in range(c, n + 1):
                m[r][j] -= f * m[c][j]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][j] * x[j] for j in range(r + 1, n))) / m[r][r]
    return x


def _to_b(p):
    return [p["k"] * p["h"]] + [p["k"] * w for w in p["w"]]


def _from_b(b):
    k = sum(b[1:]) / 5.0
    if k <= 0:
        return None
    return {"k": k, "h": b[0] / k, "w": [x / k for x in b[1:]]}


def _fit_map(x, y, b0, lam, iters=30):
    b = list(b0)
    n = len(b)
    for _ in range(iters):
        g = [lam[j] * (b[j] - b0[j]) for j in range(n)]
        h = [[lam[j] if j == i else 0.0 for j in range(n)] for i in range(n)]
        for xi, yi in zip(x, y):
            p = _sigmoid(sum(bj * xj for bj, xj in zip(b, xi)))
            r, wgt = p - yi, p * (1.0 - p)
            for i in range(n):
                g[i] += r * xi[i]
                for j in range(n):
                    h[i][j] += wgt * xi[i] * xi[j]
        step = _solve(h, g)
        b = [bj - sj for bj, sj in zip(b, step)]
        if max(abs(sj) for sj in step) < 1e-10:
            break
    return b


def _game_losses(rows, p):
    """Per-game log loss of settings p on rows [(diffs[5], home_won)]."""
    b = _to_b(p)
    out = []
    for d, won in rows:
        q = _clamp(_sigmoid(b[0] + sum(bj * float(v) for bj, v in zip(b[1:], d))), 1e-6, 1.0 - 1e-6)
        out.append(-math.log(q) if won else -math.log(1.0 - q))
    return out


def _scores(rows, p):
    """(log loss, Brier, accuracy) of settings p on the rows."""
    if not rows:
        return None
    b = _to_b(p)
    ll = br = hit = 0.0
    for d, won in rows:
        q = _clamp(_sigmoid(b[0] + sum(bj * float(v) for bj, v in zip(b[1:], d))), 1e-6, 1.0 - 1e-6)
        y = 1 if won else 0
        ll += -(y * math.log(q) + (1 - y) * math.log(1 - q))
        br += (q - y) ** 2
        hit += 1.0 if (q >= 0.5) == (y == 1) else 0.0
    n = float(len(rows))
    return {"log_loss": round(ll / n, 4), "brier": round(br / n, 4), "accuracy": round(hit / n, 4)}


def _within_steps(c, cur):
    return (abs(c["k"] - cur["k"]) <= cur["k"] * LEARN_STEP_K + 1e-12
            and abs(c["h"] - cur["h"]) <= LEARN_STEP_H + 1e-12
            and all(abs(a - b) <= LEARN_STEP_W + 1e-12 for a, b in zip(c["w"], cur["w"])))


def _bounded(c, base):
    wl, wh = LEARN_BOUNDS["w"]
    hl, hh = LEARN_BOUNDS["h"]
    kl, kh = LEARN_BOUNDS["k"]
    return {"k": round(_clamp(c["k"], base["k"] * kl, base["k"] * kh), 6),
            "h": round(_clamp(c["h"], hl, hh), 4),
            "w": [round(_clamp(w, wl, wh), 4) for w in c["w"]]}


def propose_settings(rows, current, baseline=None):
    """The candidate: the penalized fit on rows, reached from the settings in
    use along a straight line in coefficient space no further than the
    per-cycle step limits allow. Returns (candidate, raw_fit); candidate is
    None when the fit is unusable."""
    base = baseline or LEARN_BASELINE
    if not rows:
        return None, None
    x = [[1.0] + [float(v) for v in d] for d, _ in rows]
    y = [1 if won else 0 for _, won in rows]
    n = len(y)
    m2 = [1.0] + [max(1.0, sum(xi[j] ** 2 for xi in x) / n) for j in range(1, 6)]
    lam = [LEARN_PRIOR_GAMES * 0.25 * m for m in m2]
    try:
        b_fit = _fit_map(x, y, _to_b(base), lam)
    except (ValueError, OverflowError):
        return None, None
    raw = _from_b(b_fit)
    b_cur = _to_b(current)
    if raw is not None and _within_steps(raw, current):
        return _bounded(raw, base), raw
    lo, hi = 0.0, 1.0
    for _ in range(40):
        t = (lo + hi) / 2.0
        c = _from_b([bc + t * (bf - bc) for bc, bf in zip(b_cur, b_fit)])
        if c is not None and _within_steps(c, current):
            lo = t
        else:
            hi = t
    c = _from_b([bc + lo * (bf - bc) for bc, bf in zip(b_cur, b_fit)])
    return (_bounded(c, base) if c is not None else None), raw


def forward_test(deltas):
    """deltas: per-game (candidate log loss - settings-in-use log loss) on
    games the candidate was not fit on. Passed when there are at least
    LEARN_MIN_FORWARD of them and their mean is LEARN_FORWARD_T standard
    errors below zero."""
    n = len(deltas)
    out = {"games": n, "mean_gain": None, "t": None, "passed": False}
    if n < 2:
        return out
    mean = sum(deltas) / n
    sd = math.sqrt(sum((d - mean) ** 2 for d in deltas) / (n - 1))
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    out.update({"mean_gain": round(-mean, 5), "t": round(t, 2),
                "passed": n >= LEARN_MIN_FORWARD and t < LEARN_FORWARD_T})
    return out


def learning_cycle(rows, current, baseline=None, candidate=None, since=0, forward=None):
    """One cycle. rows: every usable settled game this season, in settlement
    order, [(home_minus_away_pillars[5], home_won)]. candidate: the candidate
    proposed at the previous cycle when rows[:since] had settled; forward:
    its forward record so far. Scores the candidate on rows[since:], puts it
    into use if it passes the forward test, then proposes the next candidate
    from all rows. Returns the cycle report."""
    base = baseline or LEARN_BASELINE
    n = len(rows)
    record = list(forward or [])
    new_games = 0
    if candidate and 0 <= since <= n:
        new = rows[since:]
        new_games = len(new)
        record += [c - u for c, u in zip(_game_losses(new, candidate), _game_losses(new, current))]
        record = record[-LEARN_FORWARD_MAX:]
    elif candidate:
        record = []                       # the ledger shrank; the record no longer lines up
    test = forward_test(record)
    accepted = bool(candidate) and test["passed"]
    in_use = candidate if accepted else current
    if accepted:
        record = []
    nxt, raw = propose_settings(rows, in_use, base)
    report = {"games": n, "new_games_scored": new_games, "accepted": accepted,
              "forward": test, "forward_record": [round(v, 6) for v in record],
              "before": current, "current": in_use, "tested": candidate,
              "candidate": nxt, "raw_fit": ({"k": round(raw["k"], 6), "h": round(raw["h"], 4),
                                             "w": [round(w, 4) for w in raw["w"]]} if raw else None),
              "scores": {"in_use": _scores(rows, in_use), "candidate": _scores(rows, nxt) if nxt else None,
                         "baseline": _scores(rows, base)}}
    if not candidate:
        report["reason"] = "first candidate proposed; it is tested on the games that settle from now on"
    elif accepted:
        report["reason"] = ("candidate beat the settings in use on %d unseen games (log loss %.4f better per "
                            "game, %.1f standard errors); now in use" % (test["games"], test["mean_gain"], -test["t"]))
    elif test["games"] < LEARN_MIN_FORWARD:
        report["reason"] = ("candidate scored on %d of the %d unseen games required; kept the settings in use"
                            % (test["games"], LEARN_MIN_FORWARD))
    else:
        report["reason"] = ("candidate %s the settings in use on %d unseen games (%.1f standard errors, %.1f "
                            "required); kept the settings in use"
                            % ("ahead of" if (test["mean_gain"] or 0) > 0 else "behind",
                               test["games"], abs(test["t"] or 0.0), -LEARN_FORWARD_T))
    return report
