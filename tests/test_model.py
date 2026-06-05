"""Unit tests for the Dixon-Coles scoreline model and market inversion."""
import numpy as np

from src.model.dixon_coles import (outcome_probs, score_matrix,
                                    solve_lambdas_from_market, summarize)


def test_matrix_normalizes():
    for lam, mu in [(1.3, 1.1), (2.5, 0.4), (0.5, 0.5), (3.0, 2.0)]:
        P = score_matrix(lam, mu, rho=-0.12, max_goals=8)
        assert abs(P.sum() - 1.0) < 1e-9
        ph, pd, pa = outcome_probs(P)
        assert abs(ph + pd + pa - 1.0) < 1e-9


def test_expected_goals_recovered():
    P = score_matrix(1.8, 1.0, rho=-0.1, max_goals=10)
    s = summarize(P)
    assert abs(s.exp_home - 1.8) < 0.05
    assert abs(s.exp_away - 1.0) < 0.05


def test_market_inversion_directionally_correct():
    # home-favoured market -> lam_home > lam_away
    lam, mu = solve_lambdas_from_market(0.60, 0.25, 0.15, total_hint=2.6)
    assert lam > mu
    P = score_matrix(lam, mu)
    ph, pd, pa = outcome_probs(P)
    assert abs(ph - 0.60) < 0.05 and abs(pd - 0.25) < 0.05 and abs(pa - 0.15) < 0.05

    # symmetric market -> roughly equal lambdas
    lam2, mu2 = solve_lambdas_from_market(0.38, 0.24, 0.38, total_hint=2.6)
    assert abs(lam2 - mu2) < 0.15


def test_higher_total_more_goals():
    lo_h, lo_a = solve_lambdas_from_market(0.45, 0.30, 0.25, total_hint=1.8)
    hi_h, hi_a = solve_lambdas_from_market(0.45, 0.30, 0.25, total_hint=3.6)
    assert (hi_h + hi_a) > (lo_h + lo_a)
