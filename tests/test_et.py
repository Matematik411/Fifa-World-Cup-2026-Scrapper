"""U10 — knockout extra-time adjustment for fantasy projections.

FIFA Fantasy scores extra time ("not including shootouts" — verified rule);
the projections were built purely from the 90' matrix. KO entries now get an
attack uplift and a clean-sheet discount, both weighted by P(draw@90').
Nostradamus/GoPicks are untouched (their scoring resolves at 90')."""
import numpy as np

from src.fantasy.projections import _et_adjust, _team_match_env


class _Cfg:
    def __init__(self, factor=0.28):
        self.factor = factor

    def get(self, key, default=None):
        if key == "model.ko_et_goal_factor":
            return self.factor
        return default


class _MF:
    def __init__(self, num, rnd, home, away, lh, la, n=8):
        from math import exp, factorial
        self.num, self.round, self.home, self.away = num, rnd, home, away
        self.lam_home, self.lam_away = lh, la
        ph = np.array([exp(-lh) * lh ** k / factorial(k) for k in range(n)])
        pa = np.array([exp(-la) * la ** k / factorial(k) for k in range(n)])
        self.P = np.outer(ph, pa)
        self.P /= self.P.sum()
        ii, jj = np.indices((n, n))
        self.p_draw = float(self.P[ii == jj].sum())
        self.date = "2026-07-04"


class _FC:
    def __init__(self, mfs, factor=0.28):
        self.match_forecasts = {m.num: m for m in mfs}
        self.cfg = _Cfg(factor)


def test_ko_entry_gets_uplift_and_cs_discount():
    ko = _MF(93, "R16", "Portugal", "Spain", 1.15, 1.35)
    grp = _MF(37, "MD2", "Spain", "Saudi Arabia", 2.4, 0.4)
    env_ko = _team_match_env(_FC([ko]), {})
    env_gr = _team_match_env(_FC([grp]), {})

    spain_ko = env_ko["Spain"][0]
    assert spain_ko["lam_for"] > ko.lam_away                      # ET attack uplift
    cs90 = float(ko.P[0, :].sum())                                # away CS: home scored 0
    assert spain_ko["cs_prob"] < cs90                             # ET kills some 90' CS

    spain_gr = env_gr["Spain"][0]
    assert spain_gr["lam_for"] == grp.lam_home                    # group game untouched
    assert spain_gr["cs_prob"] == float(grp.P[:, 0].sum())        # home CS: away scored 0


def test_uplift_scales_with_draw_prob():
    tight = _MF(94, "R16", "USA", "Belgium", 1.2, 1.25)
    lopsided = _MF(95, "R16", "Argentina", "Egypt", 2.2, 0.6)
    env = _team_match_env(_FC([tight, lopsided]), {})
    rel_tight = env["USA"][0]["lam_for"] / tight.lam_home
    rel_lop = env["Argentina"][0]["lam_for"] / lopsided.lam_home
    assert rel_tight > rel_lop > 1.0


def test_factor_zero_is_noop():
    ko = _MF(93, "R16", "Portugal", "Spain", 1.15, 1.35)
    env = _team_match_env(_FC([ko], factor=0.0), {})
    assert env["Portugal"][0]["lam_for"] == ko.lam_home
    assert env["Portugal"][0]["cs_prob"] == float(ko.P[:, 0].sum())  # home CS side


def test_conceded_marginal_mixes_up_not_down():
    ko = _MF(94, "R16", "USA", "Belgium", 1.2, 1.25)
    env = _team_match_env(_FC([ko]), {})
    marg_adj = env["USA"][0]["opp_goal_marg"]
    raw = ko.P.sum(axis=0)
    exp_raw = float((np.arange(len(raw)) * raw).sum())
    exp_adj = float((np.arange(len(marg_adj)) * marg_adj).sum())
    assert exp_adj > exp_raw                                      # more expected concessions
    assert abs(marg_adj.sum() - 1.0) < 1e-9                       # still a distribution


def test_et_adjust_preserves_mass_and_signs():
    marg = np.array([0.5, 0.3, 0.15, 0.05])
    e = {"lam_for": 1.4, "cs_prob": 0.5, "opp_goal_marg": marg}
    _et_adjust(e, p00=0.1, p_draw=0.3, lam_opp=1.1, et_factor=0.28)
    assert 0 < e["cs_prob"] < 0.5
    assert e["lam_for"] > 1.4
    assert abs(e["opp_goal_marg"].sum() - 1.0) < 1e-9
