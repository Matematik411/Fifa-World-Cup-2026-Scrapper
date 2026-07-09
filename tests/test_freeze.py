"""U11 — auto-freeze of imminent picks-of-record (drift-proof scoring)."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.pipeline import _freeze_imminent_picks

CET = ZoneInfo("Europe/Ljubljana")


def _forecast(mfs: dict):
    return SimpleNamespace(match_forecasts={
        num: SimpleNamespace(date=d, kickoff_local=k, tz=tz)
        for num, (d, k, tz) in mfs.items()
    })


def _rec(num, ph, pa, gh=None, ga=None):
    r = {"num": num, "pred_home": ph, "pred_away": pa}
    if gh is not None:
        r["gp_home"], r["gp_away"] = gh, ga
    return r


# A run on the afternoon of 2026-07-09 (CEST): M97 kicks off 22:00 CEST tonight,
# M98 tomorrow 21:00 CEST (past the 06:00 cutoff), M96 already played.
GEN = datetime(2026, 7, 9, 15, 0, tzinfo=CET)
MFS = {
    96: ("2026-07-07", "13:00", "America/Vancouver"),
    97: ("2026-07-09", "16:00", "America/New_York"),   # 22:00 CEST tonight
    98: ("2026-07-10", "12:00", "America/Los_Angeles"),  # 21:00 CEST tomorrow
    99: (None, None, None),                             # TBD slot
}


def test_freezes_tonights_match_in_both_leagues():
    state = {}
    recs = [_rec(97, 2, 0, 1, 0)]
    _freeze_imminent_picks(state, recs, {}, _forecast(MFS), GEN, log=lambda *a: None)
    assert state["predictions_entered"]["97"] == "2-0"
    assert state["gopicks"]["predictions_entered"]["97"] == "1-0"


def test_tomorrow_evening_match_not_frozen():
    state = {}
    recs = [_rec(98, 1, 1, 1, 1)]
    _freeze_imminent_picks(state, recs, {}, _forecast(MFS), GEN, log=lambda *a: None)
    assert "98" not in state.get("predictions_entered", {})
    assert "98" not in (state.get("gopicks") or {}).get("predictions_entered", {})


def test_user_deviation_and_missed_survive():
    state = {"predictions_entered": {"97": "0-3"},
             "gopicks": {"predictions_entered": {"97": "missed"}}}
    recs = [_rec(97, 2, 0, 1, 0)]
    _freeze_imminent_picks(state, recs, {}, _forecast(MFS), GEN, log=lambda *a: None)
    assert state["predictions_entered"]["97"] == "0-3"
    assert state["gopicks"]["predictions_entered"]["97"] == "missed"


def test_played_and_kicked_off_and_tbd_matches_skipped():
    state = {}
    # 96 played; 97 "kicked off" (gen after KO); 99 has no kickoff data
    gen_late = datetime(2026, 7, 9, 23, 0, tzinfo=CET)
    recs = [_rec(96, 1, 0, 1, 0), _rec(97, 2, 0, 1, 0), _rec(99, 1, 0, 1, 0)]
    _freeze_imminent_picks(state, recs, {96: (0, 1)}, _forecast(MFS), gen_late,
                           log=lambda *a: None)
    assert state.get("predictions_entered", {}) == {}


def test_refreeze_same_day_keeps_first_pick():
    state = {}
    recs1 = [_rec(97, 1, 0, 1, 0)]
    _freeze_imminent_picks(state, recs1, {}, _forecast(MFS), GEN, log=lambda *a: None)
    # a later same-day re-run flips the recommendation — the frozen entry wins
    recs2 = [_rec(97, 2, 0, 2, 0)]
    _freeze_imminent_picks(state, recs2, {}, _forecast(MFS),
                           datetime(2026, 7, 9, 18, 0, tzinfo=CET), log=lambda *a: None)
    assert state["predictions_entered"]["97"] == "1-0"
    assert state["gopicks"]["predictions_entered"]["97"] == "1-0"
