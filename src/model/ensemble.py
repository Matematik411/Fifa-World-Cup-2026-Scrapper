"""Ensemble team-strength estimation.

Blends several signals into one unified per-team strength (z-scored):
  * de-vigged outright title odds
  * eloratings.net Elo
  * public model/supercomputer title probabilities
  * FIFA ranking points (minor)

Then calibrates the strength->goals mapping (beta, baseline, host bump) against
the per-match market odds we do have, so that rating-derived expected goals are
consistent with the market on average.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import log

import numpy as np

from .dixon_coles import solve_lambdas_from_market
from .teams import normalize_team


def devig_multiplicative(odds: dict[str, float]) -> dict[str, float]:
    """Normalize 1/decimal-odds to sum 1 (proportional de-vig)."""
    inv = {k: 1.0 / v for k, v in odds.items() if v and v > 1.0}
    s = sum(inv.values())
    if s <= 0:
        return {}
    return {k: v / s for k, v in inv.items()}


def _zscore(d: dict[str, float]) -> dict[str, float]:
    if not d:
        return {}
    vals = np.array(list(d.values()), dtype=float)
    mu, sd = vals.mean(), vals.std()
    if sd < 1e-9:
        return {k: 0.0 for k in d}
    return {k: (v - mu) / sd for k, v in d.items()}


@dataclass
class Strengths:
    teams: list[str]
    s: dict[str, float]
    beta: float
    mu_base: float
    host_log: float
    altitude_log: float
    title_prob_devig: dict[str, float] = field(default_factory=dict)
    signals_used: dict[str, int] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


HOSTS = {"USA", "Mexico", "Canada"}


def build_strengths(teams: list[str], ratings_odds: dict, cfg) -> Strengths:
    notes: list[str] = []
    weights = cfg.get("model.ensemble_weights", {})

    # --- gather signals, normalized to canonical names ---
    def norm_map(d: dict | None) -> dict[str, float]:
        out: dict[str, float] = {}
        if not d:
            return out
        for k, v in d.items():
            try:
                out[normalize_team(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out

    elo = norm_map(ratings_odds.get("elo"))
    fifa_pts = norm_map(ratings_odds.get("fifa_rank_points"))
    title_odds = norm_map(ratings_odds.get("title_odds_decimal"))
    title_devig = devig_multiplicative(title_odds) if title_odds else {}

    model_title = {}
    for _, mp in (ratings_odds.get("model_probs") or {}).items():
        if isinstance(mp, dict) and "title" in mp:
            for k, v in mp["title"].items():
                try:
                    model_title[normalize_team(k)] = model_title.get(normalize_team(k), 0.0) + float(v)
                except (TypeError, ValueError):
                    continue

    # Transform each signal so "bigger = stronger", then z-score.
    z_elo = _zscore(elo)
    z_fifa = _zscore(fifa_pts)
    z_title = _zscore({k: log(max(v, 1e-6)) for k, v in title_devig.items()})
    z_model = _zscore({k: log(max(v, 1e-6)) for k, v in model_title.items()})

    w_elo = float(weights.get("elo", 0.25))
    w_title = float(weights.get("market_title", 0.30))
    w_model = float(weights.get("model_probs", 0.15))
    w_fifa = 0.08

    s: dict[str, float] = {}
    signals_used: dict[str, int] = {}
    for t in teams:
        parts, wsum = 0.0, 0.0
        n = 0
        for z, w in ((z_elo, w_elo), (z_title, w_title), (z_model, w_model), (z_fifa, w_fifa)):
            if t in z:
                parts += w * z[t]
                wsum += w
                n += 1
        s[t] = parts / wsum if wsum > 0 else 0.0
        signals_used[t] = n

    # Re-z-score the blended strength so it's a clean ~N(0,1).
    s = _zscore(s)
    missing = [t for t in teams if signals_used.get(t, 0) == 0]
    if missing:
        notes.append(f"No strength signal for {len(missing)} team(s): {', '.join(missing[:8])}"
                     + ("..." if len(missing) > 8 else "") + " — treated as average.")

    # --- calibrate strength -> goals (beta, mu_base, host bump) from match odds ---
    mu_base = log(max(cfg.get("model.base_total_goals", 2.65), 0.5) / 2.0)
    beta = 0.55
    host_log = log(1.18)

    market_rows = []
    for mo in (ratings_odds.get("match_odds") or []):
        try:
            h, a = normalize_team(mo["home"]), normalize_team(mo["away"])
            o = {"H": float(mo["home_dec"]), "D": float(mo["draw_dec"]), "A": float(mo["away_dec"])}
            dv = devig_multiplicative(o)
            total = None
            if mo.get("total_line") and (mo.get("over_dec") or mo.get("under_dec")):
                total = float(mo["total_line"])  # use the line as a total anchor
            lam, mu = solve_lambdas_from_market(dv["H"], dv["D"], dv["A"], total_hint=total)
            if h in s and a in s:
                market_rows.append((h, a, lam, mu))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue

    if len(market_rows) >= 6:
        # Fit log(lam_home)=c0 + beta*(s_h - s_a) + host_log*host_h ; log(lam_away)=c0 + beta*(s_a - s_h) + host_log*host_a
        X, y = [], []
        for h, a, lam, mu in market_rows:
            ds = s[h] - s[a]
            X.append([1.0, ds, 1.0 if h in HOSTS else 0.0, 0.0]); y.append(log(max(lam, 0.05)))
            X.append([1.0, -ds, 0.0, 1.0 if a in HOSTS else 0.0]); y.append(log(max(mu, 0.05)))
        X = np.array(X); y = np.array(y)
        try:
            coef, *_ = np.linalg.lstsq(X, y, rcond=None)
            # Adopt the market-calibrated supremacy slope and host bump, but keep the
            # neutral baseline at the config prior (the regression intercept is biased
            # low by the asymmetry of favourite-vs-minnow first-round fixtures).
            beta = float(np.clip(coef[1], 0.15, 1.2))
            host_log = float(np.clip((coef[2] + coef[3]) / 2.0, 0.0, log(1.6)))
            notes.append(f"Calibrated beta={beta:.3f} (supremacy slope), host bump={np.exp(host_log):.2f}x "
                         f"from {len(market_rows)} market matches; neutral baseline pinned to "
                         f"{np.exp(mu_base) * 2:.2f} total goals.")
        except np.linalg.LinAlgError:
            notes.append("Market calibration failed; using default beta/host values.")
    else:
        notes.append(f"Only {len(market_rows)} market matches; using default beta={beta}, host bump.")

    altitude_log = log(float(cfg.get("model.altitude_factor", 1.05)))
    return Strengths(
        teams=teams, s=s, beta=beta, mu_base=mu_base, host_log=host_log,
        altitude_log=altitude_log, title_prob_devig=title_devig,
        signals_used=signals_used, sources=ratings_odds.get("sources", []), notes=notes,
    )


def apply_team_form(strengths: Strengths, wc_form: dict | None, cfg, log=None) -> Strengths:
    """U2 — nudge team strength by opponent-adjusted in-tournament xG.

    Applied AFTER market calibration (so the supremacy slope `beta` is NOT re-fit on
    form — avoids the double-count) and kept deliberately SMALL: the signal is the
    opponent-credited xG-difference per game, centred on neutral (0 = even), scaled,
    matches-shrunk, and capped. `model.team_form_weight`=0 disables it entirely.

    NB (market-override): because curated per-match odds override strength for priced
    fixtures, this mostly moves the KO advance-prob matrix, odds-less matches, and the
    bracket-derived quantities (title/deep-run probs, fantasy horizon, QB EV) — not the
    next-match scoreline where odds already exist.
    """
    weight = float(cfg.get("model.team_form_weight", 0.0))
    teams_form = (wc_form or {}).get("teams") or {}
    if weight <= 0 or not teams_form:
        return strengths
    base = float(cfg.get("model.base_total_goals", 2.65)) / 2.0   # neutral per-team xG/game
    k = float(cfg.get("model.team_form_shrink_matches", 3.0))
    opp_coef = float(cfg.get("model.team_form_opp_coef", 0.5))
    scale = float(cfg.get("model.team_form_scale", 1.5))
    s = strengths.s
    moved = []
    for nation, info in teams_form.items():
        if not isinstance(info, dict) or info.get("wc_xg_for") is None:
            continue
        m = float(info.get("matches") or 0)
        if m < 1:
            continue
        t = normalize_team(nation)
        xgf = float(info["wc_xg_for"]) / m
        xga = float(info.get("wc_xg_against") or 0.0) / m
        opps = [normalize_team(o) for o in (info.get("opponents") or [])]
        opp_s = float(np.mean([s.get(o, 0.0) for o in opps])) if opps else 0.0
        raw = (xgf - xga) + opp_coef * opp_s          # xG-diff per game, credited for schedule
        off = weight * (m / (m + k)) * float(np.clip(raw / scale, -1.5, 1.5))
        s[t] = s.get(t, 0.0) + off
        moved.append((t, off))
    if moved and log:
        top = sorted(moved, key=lambda x: -abs(x[1]))[:5]
        strengths.notes.append(
            "Team form (U2, post-calibration): "
            + ", ".join(f"{t} {o:+.2f}" for t, o in top)
            + f" (weight {weight}, {len(moved)} teams w/ WC xG).")
    return strengths
