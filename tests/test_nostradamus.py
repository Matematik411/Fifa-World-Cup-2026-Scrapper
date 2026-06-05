"""Unit tests for the Nostradamus expected-points optimizer on hand-checked inputs."""
import numpy as np

from src.model.forecast import MatchForecast
from src.model.dixon_coles import summarize
from src.nostradamus.optimizer import expected_points_grid, optimize_match

SCORING = {"exact": 3, "outcome_plus_one": 2, "outcome_only": 1, "wrong": 0, "ko_multiplier": 2}


def _mf(P, rnd="group"):
    s = summarize(P)
    return MatchForecast(num=1, round=rnd, group="A", home="H", away="A",
                         lam_home=1.0, lam_away=1.0, p_home=s.p_home, p_draw=s.p_draw,
                         p_away=s.p_away, exp_home=s.exp_home, exp_away=s.exp_away,
                         source="test", P=P)


def test_points_tiers_match_rules():
    # All probability mass on actual 2-1 (home win). Check each tier directly.
    P = np.zeros((9, 9)); P[2, 1] = 1.0
    g = expected_points_grid(P, SCORING, max_candidate=6)
    assert abs(g[2, 1] - 3.0) < 1e-9          # exact -> 3
    assert abs(g[2, 0] - 2.0) < 1e-9          # home win + home goals right -> 2
    assert abs(g[3, 1] - 2.0) < 1e-9          # home win + away goals right -> 2
    assert abs(g[1, 0] - 1.0) < 1e-9          # home win only -> 1
    assert abs(g[0, 1] - 0.0) < 1e-9          # away win predicted, actual home win -> 0
    assert abs(g[1, 1] - 0.0) < 1e-9          # draw predicted, actual home win -> 0


def test_argmax_is_exact_when_concentrated():
    P = np.zeros((9, 9)); P[1, 0] = 1.0
    pred = optimize_match(_mf(P), SCORING)
    assert (pred.pred_home, pred.pred_away) == (1, 0)
    assert abs(pred.ev - 3.0) < 1e-9


def test_ko_doubling_scales_ev_not_argmax():
    P = np.zeros((9, 9)); P[1, 0] = 1.0
    group = optimize_match(_mf(P, "group"), SCORING)
    ko = optimize_match(_mf(P, "R32"), SCORING)
    assert (group.pred_home, group.pred_away) == (ko.pred_home, ko.pred_away)  # same argmax
    assert group.multiplier == 1 and ko.multiplier == 2
    assert abs(ko.ev_applied - 2 * ko.ev) < 1e-9


def test_ev_optimal_can_differ_from_modal():
    # Home win is likely but spread across many scorelines; modal cell is a draw 0-0.
    P = np.zeros((9, 9))
    P[0, 0] = 0.22                      # modal scoreline is a draw
    P[1, 0] = 0.16; P[2, 0] = 0.14; P[2, 1] = 0.12; P[3, 1] = 0.10  # home-win mass 0.52
    P[0, 1] = 0.13; P[1, 2] = 0.13     # some away mass
    P /= P.sum()
    pred = optimize_match(_mf(P), SCORING)
    # modal is the draw, but the EV-optimal pick should back the (likely) home win
    assert pred.modal_home == pred.modal_away          # modal is a draw
    assert pred.pred_home > pred.pred_away             # EV pick is a home win
    assert pred.ev > 0.8


def test_grid_matches_bruteforce():
    rng = np.random.default_rng(0)
    P = rng.random((9, 9)); P /= P.sum()
    g = expected_points_grid(P, SCORING, max_candidate=6)

    def brute(a, b):
        tot = 0.0
        for i in range(9):
            for j in range(9):
                if (i, j) == (a, b):
                    pts = 3
                elif np.sign(a - b) == np.sign(i - j) and (i == a or j == b):
                    pts = 2
                elif np.sign(a - b) == np.sign(i - j):
                    pts = 1
                else:
                    pts = 0
                tot += pts * P[i, j]
        return tot

    for a in range(7):
        for b in range(7):
            assert abs(g[a, b] - brute(a, b)) < 1e-9
