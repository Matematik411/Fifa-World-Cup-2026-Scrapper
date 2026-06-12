"""Unit tests for the GoPicks expected-points optimizer on hand-checked inputs."""
import numpy as np

from src.model.forecast import MatchForecast
from src.model.dixon_coles import summarize
from src.gopicks.optimizer import expected_points_grid, optimize_match, score_prediction

SCORING = {"result": 3, "exact_home": 1, "exact_away": 1}


def _mf(P, rnd="group"):
    s = summarize(P)
    return MatchForecast(num=1, round=rnd, group="A", home="H", away="A",
                         lam_home=1.0, lam_away=1.0, p_home=s.p_home, p_draw=s.p_draw,
                         p_away=s.p_away, exp_home=s.exp_home, exp_away=s.exp_away,
                         source="test", P=P)


def test_points_components_stack():
    # actual 2-1 (home win): the three components are independent and additive
    assert score_prediction(2, 1, 2, 1, SCORING) == (5, 2)   # exact -> 3+1+1
    assert score_prediction(2, 0, 2, 1, SCORING) == (4, 1)   # result + home goals
    assert score_prediction(3, 1, 2, 1, SCORING) == (4, 1)   # result + away goals
    assert score_prediction(1, 0, 2, 1, SCORING) == (3, 0)   # result only
    assert score_prediction(2, 2, 2, 1, SCORING) == (1, 1)   # WRONG result, home goals exact
    assert score_prediction(0, 1, 2, 1, SCORING) == (1, 1)   # wrong result, away goals exact
    assert score_prediction(0, 3, 2, 1, SCORING) == (0, 0)   # nothing right


def test_argmax_is_exact_when_concentrated():
    P = np.zeros((9, 9)); P[1, 0] = 1.0
    pred = optimize_match(_mf(P), SCORING)
    assert (pred.pred_home, pred.pred_away) == (1, 0)
    assert abs(pred.ev - 5.0) < 1e-9
    assert abs(pred.exact_ev - 2.0) < 1e-9


def test_no_ko_doubling():
    P = np.zeros((9, 9)); P[1, 0] = 1.0
    group = optimize_match(_mf(P, "group"), SCORING)
    ko = optimize_match(_mf(P, "R32"), SCORING)
    assert (group.pred_home, group.pred_away) == (ko.pred_home, ko.pred_away)
    assert abs(group.ev - ko.ev) < 1e-9          # same value in every round


def test_goal_counts_follow_marginals_not_joint():
    # Home win certain; home goals split 1/2, away goals split 0/1 with 1 likelier.
    # Joint modal scoreline is 2-0 (0.35), but marginals favor away=1 (0.55).
    P = np.zeros((9, 9))
    P[1, 0] = 0.10; P[1, 1] = 0.35
    P[2, 0] = 0.35; P[2, 1] = 0.20
    pred = optimize_match(_mf(P), SCORING)
    # marginals: H=1 -> .45, H=2 -> .55; A=0 -> .45, A=1 -> .55  => pick 2-1
    assert (pred.pred_home, pred.pred_away) == (2, 1)
    assert (pred.modal_home, pred.modal_away) in ((1, 1), (2, 0))
    assert abs(pred.ev - (3 * (P[1, 0] + P[2, 0] + P[2, 1]) + 0.55 + 0.55)) < 1e-9


def test_grid_matches_bruteforce():
    rng = np.random.default_rng(0)
    P = rng.random((9, 9)); P /= P.sum()
    grid, exact = expected_points_grid(P, SCORING, max_candidate=6)

    def brute(a, b):
        tot = 0.0
        for i in range(9):
            for j in range(9):
                pts = 0
                if np.sign(a - b) == np.sign(i - j):
                    pts += 3
                if i == a:
                    pts += 1
                if j == b:
                    pts += 1
                tot += pts * P[i, j]
        return tot

    for a in range(7):
        for b in range(7):
            assert abs(grid[a, b] - brute(a, b)) < 1e-9
            assert abs(exact[a, b] - (P[a, :].sum() + P[:, b].sum())) < 1e-9


def test_tie_breaks_on_expected_exact_goals():
    # Two candidates with identical EV but different exact-goal expectation:
    # outcome mass entirely on home win spread evenly; marginals make 1-0 and 2-0
    # outcome-equal, but craft so EV ties and tiebreaker decides.
    P = np.zeros((9, 9))
    P[1, 0] = 0.25; P[2, 0] = 0.25; P[3, 2] = 0.25; P[4, 3] = 0.25
    grid, exact = expected_points_grid(P, SCORING, max_candidate=6)
    # candidates (1,0) and (2,0): same outcome term (home win = 1.0 -> 3.0),
    # same home marginal (0.25 each), same away marginal (col 0 = 0.5) -> EV ties
    assert abs(grid[1, 0] - grid[2, 0]) < 1e-9
    pred = optimize_match(_mf(P), SCORING)
    best = (pred.pred_home, pred.pred_away)
    assert grid[best] >= grid.max() - 1e-9
    assert exact[best] >= max(exact[1, 0], exact[2, 0]) - 1e-9
