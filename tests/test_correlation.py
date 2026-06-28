"""U7 — joint-scoreline captain ceiling + defensive-stack detection."""
import numpy as np

from src.fantasy.correlation import captain_ceiling
from src.fantasy.optimizer import Squad
from src.fantasy.projections import PlayerProj
from src.model.dixon_coles import score_matrix, summarize
from src.model.forecast import MatchForecast


def mk(pid, pos, nation, exp_next, *, mins=0.95, num=73, is_home=True, gshare=0.0, ashare=0.0):
    return PlayerProj(pid=pid, name=f"P{pid}", nation=nation, group="A", position=pos,
                      price=5.0, ownership=10.0, minutes_prob=mins, exp_next=exp_next,
                      exp_avg=exp_next, horizon=exp_next * 3, per_match={}, next_date="2026-06-28",
                      next_minutes=mins, next_num=num, next_is_home=is_home,
                      goal_share=gshare, assist_share=ashare)


def _mf(num, home, away, lam_h, lam_a):
    P = score_matrix(lam_h, lam_a)
    s = summarize(P)
    return MatchForecast(num=num, round="R32", group=None, home=home, away=away,
                         lam_home=lam_h, lam_away=lam_a, p_home=s.p_home, p_draw=s.p_draw,
                         p_away=s.p_away, exp_home=s.exp_home, exp_away=s.exp_away,
                         source="t", P=P, date="2026-06-28")


class _FC:
    def __init__(self, mfs):
        self.match_forecasts = mfs


def _squad(players, starters, captain):
    bench = [p.pid for p in players if p.pid not in starters]
    return Squad(players=players, starters=starters, captain=captain, vice=starters[1],
                 bench=bench, formation="3-4-3", cost=0.0, bank=0.0, budget=100.0,
                 xi_exp=0.0, squad_horizon=0.0)


def _setup():
    # match 73: esp (strong, low-conceding) vs minnow; match 74: arg vs minnow
    fc = _FC({73: _mf(73, "esp", "min1", 2.1, 0.5), 74: _mf(74, "arg", "min2", 1.9, 0.7)})
    players = [
        mk(1, "GK", "esp", 3.5, num=73),
        mk(2, "DEF", "esp", 3.4, num=73),          # esp GK+DEF+DEF -> defensive stack
        mk(3, "DEF", "esp", 3.2, num=73),
        mk(4, "DEF", "arg", 3.0, num=74),
        mk(5, "MID", "esp", 5.5, num=73, gshare=0.18, ashare=0.20),
        mk(6, "MID", "arg", 5.0, num=74, gshare=0.16, ashare=0.18),
        mk(7, "MID", "esp", 4.5, num=73, gshare=0.12, ashare=0.22),
        mk(8, "MID", "arg", 4.0, num=74, gshare=0.10, ashare=0.15),
        mk(9, "FWD", "esp", 7.0, num=73, gshare=0.40, ashare=0.10),    # captain candidate
        mk(10, "FWD", "arg", 6.5, num=74, gshare=0.38, ashare=0.12),
        mk(11, "FWD", "esp", 4.5, num=73, gshare=0.22, ashare=0.10),
        mk(12, "GK", "min1", 1.0, num=73), mk(13, "DEF", "min2", 1.0, num=74),
        mk(14, "MID", "min1", 1.0, num=73), mk(15, "FWD", "min2", 1.0, num=74),
    ]
    starters = list(range(1, 12))
    return _squad(players, starters, captain=9), fc


def test_captain_ceiling_mean_matches_sum_of_exp_next():
    squad, fc = _setup()
    res = captain_ceiling(squad, fc, cfg=None, n_sims=12000)
    assert res is not None
    expected = sum(p.exp_next for p in squad.players if p.pid in squad.starters)
    # each player's sampled mean is pinned to exp_next -> XI mean ≈ the independent sum
    assert abs(res["xi_mean"] - expected) < 1.0
    # ceiling above mean above floor
    assert res["xi_p10"] < res["xi_mean"] < res["xi_p90"]


def test_max_cap_gain_nonneg_and_doubles_top_scorer():
    squad, fc = _setup()
    res = captain_ceiling(squad, fc, cfg=None, n_sims=12000)
    # Max Captain doubles whoever turns out best -> never worse than a fixed armband
    assert res["max_cap_gain"] >= -0.05
    assert res["captain"]["name"] == "P9"
    assert res["captain"]["doubled_p90"] >= res["captain"]["doubled_mean"]


def test_defensive_stack_detected():
    squad, fc = _setup()
    res = captain_ceiling(squad, fc, cfg=None, n_sims=4000)
    esp = [s for s in res["stacks"] if s["nation"] == "esp"]
    assert esp and esp[0]["n"] == 3          # GK + 2 DEF from esp
    assert 0.0 < esp[0]["cs_prob"] <= 1.0    # shares a real clean-sheet probability


def test_returns_none_when_next_match_unknown():
    squad, fc = _setup()
    # a starter whose next match isn't in the forecast (KO opponent TBD) -> no ceiling view
    squad.by_pid()[5].next_num = 999
    assert captain_ceiling(squad, fc, cfg=None, n_sims=500) is None
