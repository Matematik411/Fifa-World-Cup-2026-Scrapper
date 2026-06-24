"""Forecast-evaluation metrics (pure functions — no I/O, fully unit-tested).

Everything operates on the 90-minute result. Outcomes are the ordered triple
(Home win, Draw, Away win); RPS treats them as ordinal (a draw sits "between"
the two wins in goal-difference space), which is the standard football metric.
"""
from __future__ import annotations

from math import log

import numpy as np

from ..model import dixon_coles as dc

_OUT_ONEHOT = {"H": (1.0, 0.0, 0.0), "D": (0.0, 1.0, 0.0), "A": (0.0, 0.0, 1.0)}


def outcome_of(home_goals: int, away_goals: int) -> str:
    return "H" if home_goals > away_goals else ("A" if away_goals > home_goals else "D")


def rps_1x2(p_home: float, p_draw: float, p_away: float, outcome: str) -> float:
    """Ranked Probability Score for the ordered 1X2 triple. Range [0, 1]; lower better."""
    p = (p_home, p_draw, p_away)
    o = _OUT_ONEHOT[outcome]
    cum_p = cum_o = s = 0.0
    for i in range(2):                       # r-1 = 2 cumulative steps
        cum_p += p[i]
        cum_o += o[i]
        s += (cum_p - cum_o) ** 2
    return s / 2.0


def brier_1x2(probs: tuple[float, float, float], outcome: str) -> float:
    """Multiclass Brier score = Σ (p_k − o_k)². Range [0, 2]; lower better."""
    o = _OUT_ONEHOT[outcome]
    return float(sum((p - oo) ** 2 for p, oo in zip(probs, o)))


def log_loss_1x2(probs: tuple[float, float, float], outcome: str, eps: float = 1e-12) -> float:
    """Negative log-likelihood of the realized outcome. Lower better."""
    idx = {"H": 0, "D": 1, "A": 2}[outcome]
    return float(-log(max(probs[idx], eps)))


def cs_probs_from_lambdas(lam: float, mu: float, rho: float = -0.12,
                          max_goals: int = 8) -> tuple[float, float]:
    """(P(home clean sheet), P(away clean sheet)) from expected goals.

    A team keeps a clean sheet iff its OPPONENT scores 0, so the home CS
    probability is P(away == 0) and vice-versa.
    """
    P = dc.score_matrix(lam, mu, rho, max_goals)
    p_home_cs = float(P[:, 0].sum())   # away scores 0
    p_away_cs = float(P[0, :].sum())   # home scores 0
    return p_home_cs, p_away_cs


def reliability(pairs: list[tuple[float, int]], n_bins: int = 5) -> list[dict]:
    """Reliability table over (predicted_prob, occurred 0/1) pairs.

    Pool H/D/A across all matches into one curve. Each bin reports mean predicted
    prob vs empirical frequency; a well-calibrated model has pred ≈ emp per bin.
    """
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, hit in pairs:
        b = min(n_bins - 1, max(0, int(p * n_bins)))
        bins[b].append((p, hit))
    rows = []
    for i, bn in enumerate(bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if bn:
            pred = sum(p for p, _ in bn) / len(bn)
            emp = sum(h for _, h in bn) / len(bn)
            rows.append({"lo": lo, "hi": hi, "n": len(bn),
                         "pred": round(pred, 3), "emp": round(emp, 3)})
        else:
            rows.append({"lo": lo, "hi": hi, "n": 0, "pred": None, "emp": None})
    return rows


def reliability_pairs(p_home: float, p_draw: float, p_away: float,
                      outcome: str) -> list[tuple[float, int]]:
    """The three (prob, occurred) pairs a single match contributes to the curve."""
    return [(p_home, 1 if outcome == "H" else 0),
            (p_draw, 1 if outcome == "D" else 0),
            (p_away, 1 if outcome == "A" else 0)]


def summarize(values: list[float]) -> float | None:
    """Mean of a metric list (None if empty), rounded for display."""
    return round(float(np.mean(values)), 4) if values else None
