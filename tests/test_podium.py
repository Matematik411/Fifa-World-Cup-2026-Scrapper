"""U9 — GoPicks podium simulator (the sanctioned rank-aware exception)."""
import numpy as np
import pytest

from src.gopicks.podium import (candidate_picks, podium_analysis, rival_pick_dist,
                                simulate_podium, strategy_pick)

SCORING = {"result": 3, "exact_home": 1, "exact_away": 1}


def poisson_matrix(lh, la, n=9):
    from math import exp, factorial
    ph = np.array([exp(-lh) * lh ** k / factorial(k) for k in range(n)])
    pa = np.array([exp(-la) * la ** k / factorial(k) for k in range(n)])
    P = np.outer(ph, pa)
    return P / P.sum()


def outcome_sign(pick):
    a, b = (int(x) for x in pick)
    return np.sign(a - b)


def test_candidate_picks_coherent():
    P = poisson_matrix(1.8, 0.7)
    c = candidate_picks(P, SCORING)
    # ev = argmax of the grid
    g = c["grid"]
    assert g[c["ev"]] == pytest.approx(g.max())
    # goal-tilt keeps the EV outcome but changes the pick
    assert outcome_sign(c["ev_alt_goals"]) == outcome_sign(c["ev"])
    assert c["ev_alt_goals"] != c["ev"]
    # second_outcome pick really is the 2nd-most-likely outcome
    order = sorted(c["p_out"], key=c["p_out"].get, reverse=True)
    assert outcome_sign(c["second_outcome"]) == order[1]
    # per-outcome bests have the right sign
    assert outcome_sign(c["best_H"]) == 1
    assert outcome_sign(c["best_D"]) == 0
    assert outcome_sign(c["best_A"]) == -1


def test_rival_dist_full_overlap_mirrors_ev():
    P = poisson_matrix(1.6, 1.0)
    picks, probs = rival_pick_dist(P, SCORING, overlap=1.0)
    assert probs.sum() == pytest.approx(1.0)
    c = candidate_picks(P, SCORING)
    assert picks[int(np.argmax(probs))] == c["ev"]
    assert probs.max() == pytest.approx(1.0)


def test_rival_dist_partial_overlap_spreads():
    P = poisson_matrix(1.6, 1.0)
    picks, probs = rival_pick_dist(P, SCORING, overlap=0.5)
    assert probs.sum() == pytest.approx(1.0)
    assert len(picks) > 3          # public + scatter components present
    assert probs.max() < 0.9


def _models(n_matches=8, tight=False):
    lams = (1.25, 1.05) if tight else (1.8, 0.7)
    return [{"num": i + 1, "home": f"H{i}", "away": f"A{i}",
             "P": poisson_matrix(*lams), "known": True}
            for i in range(n_matches)]


def test_frozen_gap_when_rivals_mirror_ev():
    """With overlap=1.0 rivals make OUR ev pick — identical picks freeze the
    gap, so a trailing player can NEVER catch up playing best-ev. This is the
    core reason a deficit forces decorrelation."""
    res = simulate_podium(_models(8), standing={"points": 100, "rank": 2,
                                                "leaderboard_ahead": [104]},
                          scoring=SCORING, n_sims=400, overlap_grid=(1.0,),
                          strategies=["best-ev", "tilt2-all"], rivals_behind=())
    assert res["strategies"]["best-ev"]["per_overlap"][1.0]["p_top3"] == pytest.approx(1.0)  # top-3 of 2 players is trivial
    # p_top1 is the real gap test: frozen gap -> never catches the leader
    assert res["strategies"]["best-ev"]["per_overlap"][1.0]["p_top1"] == 0.0
    assert res["strategies"]["tilt2-all"]["per_overlap"][1.0]["p_top1"] > 0.0


def test_best_ev_maximizes_expected_points():
    res = simulate_podium(_models(8, tight=True), standing={"points": 230, "rank": 4,
                                                            "leaderboard_ahead": [259, 244, 242]},
                          scoring=SCORING, n_sims=600, overlap_grid=(0.55,))
    evs = {k: v["ev_final"] for k, v in res["strategies"].items()}
    assert max(evs, key=evs.get) == "best-ev"
    # tilt2-all pays a real EV price for its decorrelation (NB: what it buys is
    # variance of the GAP vs correlated rivals — his own total's σ can even
    # shrink — so the σ claim is tested via p_top1/p_top3 above, not here)
    assert res["strategies"]["tilt2-all"]["ev_cost_round"] > 0
    assert res["strategies"]["tilt2-all"]["ev_final"] < res["strategies"]["best-ev"]["ev_final"]


def test_deficit_prefers_decorrelation_in_tight_slate():
    """Trailing by 12 with 16 tight matches left: some tilt strategy should
    give a materially better P(top-3) than pure EV when rivals are highly
    correlated with us."""
    models = _models(16, tight=True)
    res = simulate_podium(models, standing={"points": 230, "rank": 9,
                                            "leaderboard_ahead": [259, 244, 242]},
                          scoring=SCORING, n_sims=1500, overlap_grid=(0.7,))
    base = res["strategies"]["best-ev"]["per_overlap"][0.7]["p_top3"]
    best_tilt = max(v["per_overlap"][0.7]["p_top3"]
                    for k, v in res["strategies"].items() if k != "best-ev")
    assert best_tilt > base


def test_strategy_pick_rules():
    P = poisson_matrix(1.3, 1.1)   # tight match
    c = candidate_picks(P, SCORING)
    fav = max(c["p_out"][1], c["p_out"][-1])
    assert fav < 0.55
    assert strategy_pick("tilt2-55", c) == c["second_outcome"]
    assert strategy_pick("tilt2-45", c) == (c["second_outcome"] if fav < 0.45 else c["ev"])
    P2 = poisson_matrix(2.4, 0.5)  # heavy favorite: no flip at 55
    c2 = candidate_picks(P2, SCORING)
    assert strategy_pick("tilt2-55", c2) == c2["ev"]


def test_podium_analysis_skips_without_standing():
    class FakeForecast:
        match_forecasts = {}
    msgs = []
    out = podium_analysis(FakeForecast(), {"matches": []}, {}, SCORING,
                          {"points_official": 230}, log=msgs.append)
    assert out is None
    assert any("skipped" in m for m in msgs)


def _podium_result(flips):
    """Minimal podium dict: flips = {num: (pick, ev_pick)}."""
    picks = [{"num": n, "home": f"H{n}", "away": f"A{n}", "pick": p, "ev_pick": e,
              "flipped": True, "ev_cost": 0.2, "pick_ev": 1.5, "pick_exact_ev": 0.6,
              "fav_prob": 0.4} for n, (p, e) in flips.items()]
    return {"verdict": {"switch": True, "to": "tilt2-45"},
            "strategies": {"tilt2-45": {"picks": picks}}}


def test_apply_podium_flips_records_and_state():
    from src.pipeline import _apply_podium_flips
    state = {"gopicks": {}}
    recs = [{"num": 92, "gp_home": 0, "gp_away": 1, "gp_ev": 1.9, "gp_exact_ev": 0.8},
            {"num": 93, "gp_home": 0, "gp_away": 1, "gp_ev": 2.2, "gp_exact_ev": 0.8}]
    pod = _podium_result({92: ("1-1", "0-1")})
    _apply_podium_flips(state, recs, pod, results={}, log=lambda *_: None)
    assert (recs[0]["gp_home"], recs[0]["gp_away"]) == (1, 1)
    assert recs[0]["gp_tilted"] == "tilt2-45"
    assert recs[0]["gp_ev"] == 1.5 and recs[0]["gp_exact_ev"] == 0.6
    assert recs[1]["gp_home"] == 0                       # non-flipped untouched
    assert state["gopicks"]["predictions_entered"]["92"] == "1-1"
    assert state["gopicks"]["auto_flips"]["92"] == "1-1"


def test_apply_podium_flips_respects_user_and_retires_stale():
    from src.pipeline import _apply_podium_flips
    # user-stated deviation is never stomped
    state = {"gopicks": {"predictions_entered": {"92": "2-2"}}}
    recs = [{"num": 92, "gp_home": 0, "gp_away": 1, "gp_ev": 1.9, "gp_exact_ev": 0.8}]
    _apply_podium_flips(state, recs, _podium_result({92: ("1-1", "0-1")}), {}, log=lambda *_: None)
    assert state["gopicks"]["predictions_entered"]["92"] == "2-2"
    assert recs[0]["gp_home"] == 0

    # a stale unplayed auto-entry is withdrawn when the verdict stops flipping it
    state = {"gopicks": {"predictions_entered": {"96": "1-1"}, "auto_flips": {"96": "1-1"}}}
    _apply_podium_flips(state, [], {"verdict": {"switch": False}}, {}, log=lambda *_: None)
    assert "96" not in state["gopicks"]["predictions_entered"]

    # ...but a PLAYED match's auto-entry is frozen as history
    state = {"gopicks": {"predictions_entered": {"96": "1-1"}, "auto_flips": {"96": "1-1"}}}
    _apply_podium_flips(state, [], {"verdict": {"switch": False}}, {96: (1, 1)}, log=lambda *_: None)
    assert state["gopicks"]["predictions_entered"]["96"] == "1-1"
    assert "96" not in state["gopicks"]["auto_flips"]
