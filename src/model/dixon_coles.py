"""Dixon-Coles bivariate-Poisson scoreline model.

Builds a P(home=i, away=j) matrix from expected goals (lambda_home, lambda_away)
with the low-score correlation correction, and can invert de-vigged market 1X2
(+ optional total) into the (lambda_home, lambda_away) that reproduces it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson


def dc_tau(rho: float, lam: float, mu: float, max_goals: int) -> np.ndarray:
    """Dixon-Coles low-score correction matrix tau[i, j]."""
    tau = np.ones((max_goals + 1, max_goals + 1))
    tau[0, 0] = 1.0 - lam * mu * rho
    tau[0, 1] = 1.0 + lam * rho
    tau[1, 0] = 1.0 + mu * rho
    tau[1, 1] = 1.0 - rho
    return tau


def score_matrix(lam: float, mu: float, rho: float = -0.12, max_goals: int = 8) -> np.ndarray:
    """Normalized scoreline probability matrix P[i, j] (home i goals, away j goals)."""
    lam = max(float(lam), 1e-4)
    mu = max(float(mu), 1e-4)
    i = np.arange(max_goals + 1)
    home = poisson.pmf(i, lam)
    away = poisson.pmf(i, mu)
    P = np.outer(home, away)
    P *= dc_tau(rho, lam, mu, max_goals)
    P = np.clip(P, 0.0, None)
    s = P.sum()
    if s <= 0:
        # Degenerate fallback: plain independent Poisson.
        P = np.outer(home, away)
        s = P.sum()
    return P / s


@dataclass
class MatrixSummary:
    p_home: float
    p_draw: float
    p_away: float
    exp_home: float
    exp_away: float
    exp_total: float


def summarize(P: np.ndarray) -> MatrixSummary:
    n = P.shape[0]
    idx = np.arange(n)
    p_home = float(np.tril(P, -1).sum())   # i > j
    p_away = float(np.triu(P, 1).sum())    # j > i
    p_draw = float(np.trace(P))
    exp_home = float((P.sum(axis=1) * idx).sum())
    exp_away = float((P.sum(axis=0) * idx).sum())
    return MatrixSummary(p_home, p_draw, p_away, exp_home, exp_away, exp_home + exp_away)


def outcome_probs(P: np.ndarray) -> tuple[float, float, float]:
    s = summarize(P)
    return s.p_home, s.p_draw, s.p_away


def solve_lambdas_from_market(
    p_home: float,
    p_draw: float,
    p_away: float,
    total_hint: float | None = None,
    rho: float = -0.12,
    max_goals: int = 8,
) -> tuple[float, float]:
    """Find (lambda_home, lambda_away) whose DC matrix matches the de-vigged 1X2.

    If total_hint is given it is used as a soft anchor and for initialization.
    """
    p = np.array([p_home, p_draw, p_away], dtype=float)
    p = p / p.sum()
    T = total_hint if total_hint and total_hint > 0.3 else 2.6

    # Initial guess: split the total by a supremacy proxy from the win-prob gap.
    sup = 0.6 * (p[0] - p[2])  # rough goal-supremacy seed
    lam0 = max(0.2, T / 2 + sup / 2)
    mu0 = max(0.2, T / 2 - sup / 2)

    def objective(x):
        lam, mu = np.exp(x[0]), np.exp(x[1])
        P = score_matrix(lam, mu, rho, max_goals)
        ph, pd, pa = outcome_probs(P)
        err = (ph - p[0]) ** 2 + (pd - p[1]) ** 2 + (pa - p[2]) ** 2
        if total_hint and total_hint > 0.3:
            err += 0.05 * ((lam + mu) - total_hint) ** 2 / max(total_hint, 1.0)
        return err

    x0 = np.log([lam0, mu0])
    res = minimize(objective, x0, method="Nelder-Mead",
                   options={"xatol": 1e-4, "fatol": 1e-8, "maxiter": 400})
    lam, mu = float(np.exp(res.x[0])), float(np.exp(res.x[1]))
    return max(lam, 0.05), max(mu, 0.05)


def blend_matrices(mats: list[np.ndarray], weights: list[float]) -> np.ndarray:
    """Weighted average of scoreline matrices (renormalized)."""
    w = np.array(weights, dtype=float)
    w = w / w.sum()
    P = np.zeros_like(mats[0])
    for m, wi in zip(mats, w):
        P += wi * m
    return P / P.sum()
