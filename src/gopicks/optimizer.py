"""GoPicks expected-points-maximizing scoreline optimizer (gopicks.app —
the Sentora-partners score prediction league).

Scoring (per match, 90-minute result only; same values in every round — no KO
doubling), the three components are awarded independently and stack:
  correct result (home win / draw / away win) -> 3
  exact home goals                            -> 1
  exact away goals                            -> 1
so an exact scoreline is worth 5, and (unlike Nostradamus) a WRONG outcome can
still score 1-2 points if one or both goal counts happen to match.

Because the components are independent, the EV is separable:
  E[points](a, b) = result * P(outcome == sign(a-b)) + P(H == a) + P(A == b)
with P(H == a) / P(A == b) the scoreline-matrix marginals. The optimum backs
the most valuable outcome and the most likely individual goal counts within
it — which need not be the modal scoreline, nor the Nostradamus pick.

Leaderboard tiebreaker: total number of exact goal picks. Among (near-)EV-ties
we therefore prefer the candidate with the higher expected exact-goal count.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _outcome(a: int, b: int) -> int:
    return 1 if a > b else (-1 if a < b else 0)


def score_prediction(pred_h: int, pred_a: int, act_h: int, act_a: int,
                     scoring: dict) -> tuple[int, int]:
    """(points, exact-goal-pick count) for a prediction vs an actual 90' result."""
    pts = 0
    if _outcome(pred_h, pred_a) == _outcome(act_h, act_a):
        pts += int(scoring.get("result", 3))
    n_exact = 0
    if pred_h == act_h:
        pts += int(scoring.get("exact_home", 1))
        n_exact += 1
    if pred_a == act_a:
        pts += int(scoring.get("exact_away", 1))
        n_exact += 1
    return pts, n_exact


@dataclass
class Prediction:
    num: int
    round: str
    home: str
    away: str
    pred_home: int
    pred_away: int
    ev: float            # expected points (no multipliers exist in this game)
    exact_ev: float      # expected exact-goal picks = the leaderboard tiebreaker
    runner_home: int
    runner_away: int
    runner_ev: float
    p_home: float
    p_draw: float
    p_away: float
    modal_home: int
    modal_away: int
    confidence: str
    rationale: str

    def to_record(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def expected_points_grid(P: np.ndarray, scoring: dict,
                         max_candidate: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """(E[points], E[exact goal picks]) for every candidate prediction (a, b)."""
    n = P.shape[0]
    I, J = np.indices((n, n))
    sign_actual = np.sign(I - J)
    p_out = {oc: P[sign_actual == oc].sum() for oc in (-1, 0, 1)}
    marg_h = P.sum(axis=1)
    marg_a = P.sum(axis=0)
    pts_result = float(scoring.get("result", 3))
    pts_h = float(scoring.get("exact_home", 1))
    pts_a = float(scoring.get("exact_away", 1))

    C = max_candidate + 1
    grid = np.zeros((C, C))
    exact = np.zeros((C, C))
    for a in range(C):
        for b in range(C):
            ph = marg_h[a] if a < n else 0.0
            pa = marg_a[b] if b < n else 0.0
            grid[a, b] = pts_result * p_out[_outcome(a, b)] + pts_h * ph + pts_a * pa
            exact[a, b] = ph + pa
    return grid, exact


def _confidence(p_outcome_max: float) -> str:
    """Same thresholds as Nostradamus: 3 of the 5 points ride on the outcome,
    so outcome certainty is still where the reliable points are."""
    if p_outcome_max >= 0.55:
        return "High"
    if p_outcome_max >= 0.40:
        return "Med"
    return "Low"


def _rationale(mf, pred, modal, is_ko: bool) -> str:
    a, b = pred
    ma, mb = modal
    outs = {"home": mf.p_home, "draw": mf.p_draw, "away": mf.p_away}
    fav = max(outs, key=outs.get)
    fav_label = {"home": mf.home, "draw": "draw", "away": mf.away}[fav]
    base = (f"GoPicks pays 3 for the result + 1 per exact goal count (no cross-outcome partial "
            f"credit): {a}–{b} backs the {fav_label} with the most likely goal counts inside it.")
    if (a, b) != (ma, mb):
        base += f" (Modal scoreline is {ma}–{mb}.)"
    if is_ko:
        base += " Knockout: 90-min result only — a draw is a valid pick; values are NOT doubled here."
    return base


KO_ROUNDS = {"R32", "R16", "QF", "SF", "final", "third-place"}


def optimize_match(mf, scoring: dict, max_candidate: int = 6) -> Prediction:
    P = mf.P
    grid, exact = expected_points_grid(P, scoring, max_candidate)
    C = grid.shape[0]
    flat_order = np.argsort(-grid.ravel())
    best_ev = grid[divmod(int(flat_order[0]), C)]
    # tie-break toward the tiebreaker the leaderboard uses (expected exact goals),
    # then toward the higher-probability exact scoreline
    ties = [divmod(int(k), C) for k in flat_order if abs(grid[divmod(int(k), C)] - best_ev) < 1e-9]
    best = max(ties, key=lambda ab: (exact[ab], P[ab[0], ab[1]] if (ab[0] < P.shape[0] and ab[1] < P.shape[0]) else 0))
    runner = next(divmod(int(k), C) for k in flat_order if divmod(int(k), C) != best)

    modal_flat = int(np.argmax(P))
    modal = divmod(modal_flat, P.shape[0])
    p_outcome_max = max(mf.p_home, mf.p_draw, mf.p_away)

    return Prediction(
        num=mf.num, round=mf.round, home=mf.home, away=mf.away,
        pred_home=best[0], pred_away=best[1],
        ev=float(grid[best]), exact_ev=float(exact[best]),
        runner_home=runner[0], runner_away=runner[1], runner_ev=float(grid[runner]),
        p_home=mf.p_home, p_draw=mf.p_draw, p_away=mf.p_away,
        modal_home=modal[0], modal_away=modal[1],
        confidence=_confidence(p_outcome_max),
        rationale=_rationale(mf, best, modal, mf.round in KO_ROUNDS),
    )


def optimize_all(forecast, scoring: dict, max_candidate: int = 6) -> list[Prediction]:
    """Generate a prediction for every match whose teams are currently known."""
    return [optimize_match(forecast.match_forecasts[num], scoring, max_candidate)
            for num in sorted(forecast.match_forecasts)]
