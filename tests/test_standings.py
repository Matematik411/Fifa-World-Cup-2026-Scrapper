"""Dead-rubber detection (src/model/standings) + the forecast goal-intensity wiring."""
from math import log

from src.config import load_config
from src.model.ensemble import Strengths
from src.model.forecast import Forecast
from src.model.standings import dead_rubber_flags, group_table

# 1 group, 4 teams, 6 matches; MD1=1,2 MD2=3,4 MD3=5,6 (each team plays 3).
FIX = {
    "groups": {"A": ["T1", "T2", "T3", "T4"]},
    "matches": [
        {"num": 1, "round": "group", "group": "A", "home": "T1", "away": "T2"},
        {"num": 2, "round": "group", "group": "A", "home": "T3", "away": "T4"},
        {"num": 3, "round": "group", "group": "A", "home": "T1", "away": "T3"},
        {"num": 4, "round": "group", "group": "A", "home": "T2", "away": "T4"},
        {"num": 5, "round": "group", "group": "A", "home": "T1", "away": "T4"},
        {"num": 6, "round": "group", "group": "A", "home": "T2", "away": "T3"},
    ],
}
# T1 wins both (6 pts, clinched); T4 loses both (0 pts, eliminated); T2/T3 1W-1L (live).
RES = {1: (2, 0), 2: (1, 0), 3: (1, 0), 4: (1, 0)}


def test_group_table_points():
    t = group_table(FIX, RES)["A"]
    assert t["T1"]["pts"] == 6 and t["T1"]["played"] == 2
    assert t["T4"]["pts"] == 0 and t["T4"]["played"] == 2
    assert t["T2"]["pts"] == 3 and t["T3"]["pts"] == 3


def test_dead_rubber_flags_clinched_vs_eliminated():
    flags = dead_rubber_flags(FIX, RES)
    # m5 = T1(clinched) v T4(eliminated) -> both settled; m6 = T2 v T3 -> both live, absent
    assert 5 in flags and 6 not in flags
    assert flags[5]["home_state"] == "clinched"
    assert flags[5]["away_state"] == "eliminated"
    assert flags[5]["both_settled"] is True


def test_no_flags_before_last_round():
    assert dead_rubber_flags(FIX, {}) == {}            # nothing played -> no stakes resolvable
    assert dead_rubber_flags(FIX, {1: (2, 0)}) == {}   # only one game in -> still live


def test_dead_rubber_intensity_lowers_goals():
    cfg = load_config()
    st = Strengths(teams=["A", "B"], s={"A": 0.4, "B": -0.4}, beta=0.5,
                   mu_base=log(1.3), host_log=0.0, altitude_log=0.0)
    fx = {"groups": {"A": ["A", "B"]},
          "matches": [{"num": 1, "round": "group", "group": "A", "home": "A", "away": "B",
                       "city": "", "date": "2026-06-20"}]}
    base = Forecast(["A", "B"], st, cfg, {}, fx)
    damped = Forecast(["A", "B"], st, cfg, {}, fx, stakes={1: {"both_settled": True}})
    b, d = base.match_forecasts[1], damped.match_forecasts[1]
    assert d.exp_home + d.exp_away < b.exp_home + b.exp_away
    assert "deadrubber" in d.source
