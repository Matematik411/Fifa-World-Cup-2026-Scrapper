"""Nostradamus expected-points-maximizing scoreline optimizer.

Scoring (per match, on the 90' result), single values in the group stage and
doubled from the Round of 32 onward:
  exact score                              -> 3 (KO 6)
  correct outcome + one team's exact goals -> 2 (KO 4)
  correct outcome only                     -> 1 (KO 2)
  wrong outcome                            -> 0

The EV-optimal prediction is the argmax over all candidate scorelines of
E[points] = sum_{i,j} pts(a,b,i,j) * P(i,j). It is NOT generally the modal
scoreline nor the modal outcome — partial credit for the outcome reshapes it.
The KO doubling is a per-match constant, so it does not change the argmax, but
we report the doubled EV so the user sees which matches matter most.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KO_ROUNDS = {"R32", "R16", "QF", "SF", "final", "third-place"}


def _outcome(a: int, b: int) -> int:
    return 1 if a > b else (-1 if a < b else 0)


def score_prediction(pred_h: int, pred_a: int, act_h: int, act_a: int,
                     is_ko: bool, scoring: dict) -> int:
    """Points scored by a prediction against an actual 90-minute result."""
    if pred_h == act_h and pred_a == act_a:
        base = scoring.get("exact", 3)
    elif _outcome(pred_h, pred_a) == _outcome(act_h, act_a):
        base = scoring.get("outcome_plus_one", 2) if (pred_h == act_h or pred_a == act_a) else scoring.get("outcome_only", 1)
    else:
        base = scoring.get("wrong", 0)
    mult = int(scoring.get("ko_multiplier", 2)) if is_ko else 1
    return int(base) * mult


@dataclass
class Prediction:
    num: int
    round: str
    home: str
    away: str
    pred_home: int
    pred_away: int
    ev: float            # expected points at face value (single)
    ev_applied: float    # expected points actually scored (doubled in KO)
    multiplier: int
    runner_home: int
    runner_away: int
    runner_ev: float
    p_home: float
    p_draw: float
    p_away: float
    exp_home: float
    exp_away: float
    modal_home: int
    modal_away: int
    confidence: str
    rationale: str

    def to_record(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def expected_points_grid(P: np.ndarray, scoring: dict, max_candidate: int = 6) -> np.ndarray:
    """E[points] (single/face value) for every candidate prediction (a, b)."""
    n = P.shape[0]
    I, J = np.indices((n, n))
    sign_actual = np.sign(I - J)               # outcome of actual scoreline
    pts_exact = float(scoring.get("exact", 3))
    pts_one = float(scoring.get("outcome_plus_one", 2))
    pts_only = float(scoring.get("outcome_only", 1))

    C = max_candidate + 1
    grid = np.zeros((C, C))
    for a in range(C):
        for b in range(C):
            oc = _outcome(a, b)
            outcome_match = (sign_actual == oc)
            one = outcome_match & ((I == a) | (J == b))
            exact = np.zeros_like(P, dtype=bool)
            if a < n and b < n:
                exact[a, b] = True
            one = one & ~exact
            only = outcome_match & ~one & ~exact
            ev = pts_exact * (P[a, b] if (a < n and b < n) else 0.0)
            ev += pts_one * P[one].sum()
            ev += pts_only * P[only].sum()
            grid[a, b] = ev
    return grid


def _confidence(p_outcome_max: float) -> str:
    """Confidence tracks outcome certainty — that's where the reliable points are
    (partial credit for the correct 1/X/2). Exact scorelines are inherently noisy."""
    if p_outcome_max >= 0.55:
        return "High"
    if p_outcome_max >= 0.40:
        return "Med"
    return "Low"


def _rationale(mf, pred, modal, p_sorted, is_ko: bool) -> str:
    a, b = pred
    ma, mb = modal
    outs = {"home": mf.p_home, "draw": mf.p_draw, "away": mf.p_away}
    fav = max(outs, key=outs.get)
    fav_label = {"home": mf.home, "draw": "draw", "away": mf.away}[fav]
    base = (f"Model: {mf.home} {mf.p_home:.0%} / draw {mf.p_draw:.0%} / {mf.away} {mf.p_away:.0%}, "
            f"xG {mf.exp_home:.1f}–{mf.exp_away:.1f}.")
    if (a, b) != (ma, mb):
        base += (f" Modal score is {ma}–{mb}, but {a}–{b} maximizes expected points by hedging "
                 f"toward the {fav_label} outcome's partial credit.")
    else:
        base += f" {a}–{b} is both the modal and EV-optimal scoreline."
    if is_ko:
        base += " Knockout: 90-min result only — a draw is a valid pick and points are doubled."
    return base


def optimize_match(mf, scoring: dict, max_candidate: int = 6) -> Prediction:
    P = mf.P
    grid = expected_points_grid(P, scoring, max_candidate)
    flat_order = np.argsort(-grid.ravel())
    C = grid.shape[0]
    best = divmod(int(flat_order[0]), C)
    runner = divmod(int(flat_order[1]), C)
    # tie-break best toward higher-probability exact score
    best_ev = grid[best]
    ties = [divmod(int(k), C) for k in flat_order if abs(grid[divmod(int(k), C)] - best_ev) < 1e-9]
    if len(ties) > 1:
        best = max(ties, key=lambda ab: P[ab[0], ab[1]] if (ab[0] < P.shape[0] and ab[1] < P.shape[0]) else 0)

    is_ko = mf.round in KO_ROUNDS
    mult = int(scoring.get("ko_multiplier", 2)) if is_ko else 1
    modal_flat = int(np.argmax(P))
    modal = divmod(modal_flat, P.shape[0])
    p_outcome_max = max(mf.p_home, mf.p_draw, mf.p_away)

    return Prediction(
        num=mf.num, round=mf.round, home=mf.home, away=mf.away,
        pred_home=best[0], pred_away=best[1],
        ev=float(best_ev), ev_applied=float(best_ev * mult), multiplier=mult,
        runner_home=runner[0], runner_away=runner[1], runner_ev=float(grid[runner]),
        p_home=mf.p_home, p_draw=mf.p_draw, p_away=mf.p_away,
        exp_home=mf.exp_home, exp_away=mf.exp_away,
        modal_home=modal[0], modal_away=modal[1],
        confidence=_confidence(p_outcome_max),
        rationale=_rationale(mf, best, modal, None, is_ko),
    )


def optimize_all(forecast, scoring: dict, max_candidate: int = 6) -> list[Prediction]:
    """Generate a prediction for every match whose teams are currently known."""
    preds = []
    for num in sorted(forecast.match_forecasts):
        preds.append(optimize_match(forecast.match_forecasts[num], scoring, max_candidate))
    return preds
