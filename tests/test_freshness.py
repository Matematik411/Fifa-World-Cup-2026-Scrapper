"""U8 — data freshness / confidence statuses (src/pipeline._freshness_rows)."""
from src.config import load_config
from src.pipeline import _freshness_rows


class _FakeBundle:
    def __init__(self, freshness):
        self.freshness = freshness
        self.warnings = []


def _rows(freshness, run_date="2026-06-24"):
    rows = _freshness_rows(_FakeBundle(freshness), run_date, load_config())
    return {r["input"]: r for r in rows}


def test_player_stats_pretournament_is_baseline_not_stale():
    r = _rows({"player_stats": {"as_of": "2026-06-05", "confidence": "Med"}})
    assert r["player_stats"]["status"] == "baseline"     # club-season prior, expected — not a warning


def test_old_daily_input_is_stale():
    r = _rows({"lineups": {"as_of": "2026-06-20", "confidence": "Med"}})
    assert r["lineups"]["status"] == "stale" and r["lineups"]["age_days"] == 4


def test_seed_confidence_is_low_confidence():
    r = _rows({"wc_form": {"as_of": "2026-06-24", "confidence": "Low-seed"}})
    assert r["wc_form"]["status"] == "low-confidence"    # fresh date, but seed/estimated values


def test_fresh_high_confidence_is_ok():
    r = _rows({"fixtures": {"as_of": "2026-06-24", "confidence": "High"}})
    assert r["fixtures"]["status"] == "ok"
