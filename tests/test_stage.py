"""Stage-engine, rule-window and KO-conditioning tests (real fixtures + rules files)."""
import json
from pathlib import Path

from src.config import load_config
from src.model.bracket import forced_ko_winners
from src.pipeline import _budget, _free_transfers, _nation_cap, _stage

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = json.loads((ROOT / "data" / "manual" / "fixtures.json").read_text())
CFG = load_config()


def _group_results(n):
    """1-0 results for the first n group matches in chronological order."""
    g = sorted((m for m in FIXTURES["matches"] if m["round"] == "group"),
               key=lambda m: (m["date"], m["num"]))
    return {m["num"]: (1, 0) for m in g[:n]}


def test_stage_pre_tournament():
    assert _stage(CFG, "2026-06-10", FIXTURES, {})[:2] == ("pre", "MD1")
    # opener day, nothing played yet -> still pre-lock (initial squad freely editable)
    assert _stage(CFG, "2026-06-11", FIXTURES, {})[:2] == ("pre", "MD1")


def test_stage_md1_live_targets_md2():
    stage, target, _ = _stage(CFG, "2026-06-12", FIXTURES, _group_results(4))
    assert (stage, target) == ("MD1", "MD2")


def test_stage_uses_dates_when_results_lag():
    # no results entered but the opener's date has passed -> MD1 counts as locked
    stage, target, _ = _stage(CFG, "2026-06-12", FIXTURES, {})
    assert (stage, target) == ("MD1", "MD2")


def test_stage_md3_live_targets_r32():
    stage, target, _ = _stage(CFG, "2026-06-25", FIXTURES, _group_results(50))
    assert (stage, target) == ("MD3", "R32")


def test_stage_groups_done():
    stage, target, _ = _stage(CFG, "2026-06-28", FIXTURES, _group_results(72))
    assert (stage, target) == ("R32", "R32")


def test_rule_windows_by_target_round():
    # verified FIFA fantasy windows from data/manual/fantasy_rules.json
    assert _free_transfers(CFG, "MD2") == 2
    assert _free_transfers(CFG, "MD3") == 2
    assert _free_transfers(CFG, "R32") == "unlimited"
    assert _free_transfers(CFG, "R16") == 4
    assert _free_transfers(CFG, "QF") == 4
    assert _free_transfers(CFG, "SF") == 5
    assert _free_transfers(CFG, "final") == 6
    assert _budget(CFG, "MD2") == 100.0
    assert _budget(CFG, "R32") == 105.0
    assert _budget(CFG, "final") == 105.0
    assert _nation_cap(CFG, "MD3") == 3
    assert _nation_cap(CFG, "R32") == 3
    assert _nation_cap(CFG, "R16") == 4
    assert _nation_cap(CFG, "QF") == 5
    assert _nation_cap(CFG, "SF") == 6
    assert _nation_cap(CFG, "final") == 8


def test_forced_ko_winners():
    fixtures = {"matches": [
        {"num": 73, "round": "R32", "home": "Spain", "away": "Norway"},
        {"num": 74, "round": "R32", "home": "France", "away": "Senegal"},
        {"num": 1, "round": "group", "home": "Mexico", "away": "South Africa"},
    ]}
    idx = {"Spain": 0, "Norway": 1, "France": 2, "Senegal": 3, "Mexico": 4, "South Africa": 5}
    # decisive 90' KO result forces the winner; group results are not "forced"
    assert forced_ko_winners({73: (2, 0), 1: (1, 0)}, {}, fixtures, idx) == {73: 0}
    assert forced_ko_winners({73: (0, 1)}, {}, fixtures, idx) == {73: 1}
    # a 90' draw is undecidable without an explicit advancer (ET/pens)
    assert forced_ko_winners({74: (1, 1)}, {}, fixtures, idx) == {}
    assert forced_ko_winners({74: (1, 1)}, {74: "Senegal"}, fixtures, idx) == {74: 3}
    # explicit advancer wins over the 90' score
    assert forced_ko_winners({74: (2, 0)}, {74: "Senegal"}, fixtures, idx) == {74: 3}
