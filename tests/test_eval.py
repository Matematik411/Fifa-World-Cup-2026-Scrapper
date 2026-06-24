"""Tests for the backtest/calibration harness (src/eval).

Metric tests use deterministic known inputs. The walk-forward tests assert
structural INVARIANTS over whatever data is in the repo (every played match has a
leak-free pick-of-record; totals are internally consistent; scores are legal) — no
tournament-state numbers are hardcoded, so they stay green as results accrue.
"""
import json

from src.config import ROOT, load_config
from src.eval import backtest as B
from src.eval import metrics as M


def test_rps_extremes_and_uniform():
    assert M.rps_1x2(1, 0, 0, "H") == 0.0
    assert abs(M.rps_1x2(0, 0, 1, "H") - 1.0) < 1e-9          # worst possible
    assert abs(M.rps_1x2(1 / 3, 1 / 3, 1 / 3, "H") - 5 / 18) < 1e-9


def test_brier_and_log_loss():
    assert M.brier_1x2((1, 0, 0), "H") == 0.0
    assert abs(M.brier_1x2((0, 0, 1), "H") - 2.0) < 1e-9
    assert abs(M.log_loss_1x2((1.0, 0.0, 0.0), "H")) < 1e-9
    assert abs(M.log_loss_1x2((0.5, 0.25, 0.25), "H") - 0.6931) < 1e-3


def test_outcome_of():
    assert M.outcome_of(2, 0) == "H"
    assert M.outcome_of(1, 1) == "D"
    assert M.outcome_of(0, 2) == "A"


def test_clean_sheet_probs_direction():
    # strong home, weak away -> home clean sheet (away scores 0) far likelier
    p_home_cs, p_away_cs = M.cs_probs_from_lambdas(2.2, 0.3)
    assert 0 < p_away_cs < p_home_cs < 1


def test_reliability_binning():
    rows = M.reliability([(0.1, 0), (0.1, 0), (0.9, 1), (0.9, 1)], n_bins=5)
    assert rows[0]["n"] == 2 and rows[0]["emp"] == 0.0
    assert rows[4]["n"] == 2 and rows[4]["emp"] == 1.0
    assert rows[2]["n"] == 0 and rows[2]["pred"] is None


def _wf():
    cfg = load_config()
    runs = B._run_dates()
    results = B._load_results()
    ror = B.records_of_record(runs)
    return cfg, runs, results, ror


def test_pick_of_record_covers_all_played_matches():
    _, runs, results, ror = _wf()
    assert runs and results, "expected persisted runs + results in the repo"
    missing = [n for n in results if n not in ror]
    assert not missing, f"played matches with no pre-result pick-of-record: {missing}"


def test_realized_scoring_is_internally_consistent():
    cfg, _, results, ror = _wf()
    state = json.loads((ROOT / "state.json").read_text())
    rz = B.realized_scoring(results, ror, state, cfg)
    assert rz["nostradamus_scored"] == len(results)
    assert rz["nostradamus_total"] == sum(r["nostra_pts"] for r in rz["rows"])
    assert rz["gopicks_total"] == sum(r["gp_pts"] for r in rz["rows"])
    for r in rz["rows"]:
        assert r["nostra_pts"] in (0, 1, 2, 3, 4, 6)      # single or KO-doubled tiers
        assert 0 <= r["gp_pts"] <= 5                       # 3 result + up to 2 exact goals
        assert 0 <= r["gp_exact"] <= 2


def test_calibration_outputs_are_sane():
    cfg, _, results, ror = _wf()
    cal = B.calibration_walkforward(results, ror, cfg)
    assert cal["n"] > 0
    assert 0.0 < cal["rps"] < 1.0
    assert cal["log_loss"] > 0.0
    assert cal["cs_expected"] > 0 and cal["cs_actual"] >= 0
    assert sum(b["n"] for b in cal["reliability"]) == 3 * cal["n"]   # H/D/A pooled
