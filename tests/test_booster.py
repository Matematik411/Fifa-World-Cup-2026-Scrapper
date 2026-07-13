"""U4 — QB-aware selection (select_squad_xi / build_burner_squad) + the chip schedule."""
from collections import Counter

from src.fantasy.booster import chip_schedule, qb_advance_bonus, qb_ev_by_round
from src.fantasy.optimizer import build_burner_squad, select_squad_xi
from src.fantasy.projections import PlayerProj


def mk(pid, pos, nation, price, exp_next, mins=0.9):
    return PlayerProj(pid=pid, name=f"P{pid}", nation=nation, group="A", position=pos,
                      price=price, ownership=10.0, minutes_prob=mins, exp_next=exp_next,
                      exp_avg=exp_next, horizon=exp_next * 3, per_match={}, next_date="2026-06-28")


def _pool():
    pool, pid = [], 1
    for nat, pr, e in [("a", 4.5, 4.0), ("b", 4.5, 3.8), ("c", 4.0, 1.0)]:
        pool.append(mk(pid, "GK", nat, pr, e)); pid += 1
    for nat, pr, e in [("a", 5.0, 4.5), ("b", 5.5, 4.2), ("c", 4.5, 4.0), ("d", 6.0, 3.8),
                       ("e", 4.5, 3.5), ("f", 4.0, 1.2), ("d", 4.0, 1.0)]:
        pool.append(mk(pid, "DEF", nat, pr, e)); pid += 1
    for nat, pr, e in [("a", 8.0, 6.5), ("b", 7.5, 6.0), ("c", 6.5, 5.5), ("e", 6.0, 5.0),
                       ("f", 5.5, 4.5), ("d", 5.0, 2.0), ("e", 4.5, 1.5), ("f", 4.0, 1.2)]:
        pool.append(mk(pid, "MID", nat, pr, e)); pid += 1
    for nat, pr, e in [("a", 10.5, 7.5), ("b", 8.5, 6.5), ("c", 7.5, 5.5),
                       ("e", 6.0, 4.0), ("f", 5.0, 1.5), ("d", 4.5, 1.2)]:
        pool.append(mk(pid, "FWD", nat, pr, e)); pid += 1
    return pool


def test_qb_advance_bonus_is_2x_conditional_advance():
    adv = {"A": {"reach_R32": 1.0, "reach_R16": 0.7}, "B": {"reach_R32": 1.0, "reach_R16": 0.4}}
    b = qb_advance_bonus(adv, "R32", {"A", "B"})
    assert abs(b["A"] - 1.4) < 1e-9 and abs(b["B"] - 0.8) < 1e-9
    # P(advance | reached) capped at 1 (so bonus ≤ 2)
    assert qb_advance_bonus({"A": {"reach_R16": 0.5, "reach_QF": 0.9}}, "R16", {"A"})["A"] == 2.0
    # a team with no chance contributes 0
    assert qb_advance_bonus({"A": {"reach_R32": 0.0}}, "R32", {"A"})["A"] == 0.0


def test_select_squad_xi_is_legal_and_xi_focused():
    chosen, starters = select_squad_xi(_pool(), budget=100.0, nation_cap=3, value_attr="exp_next")
    assert len(chosen) == 15 and len(starters) == 11
    pos = Counter(p.position for p in chosen)
    assert (pos["GK"], pos["DEF"], pos["MID"], pos["FWD"]) == (2, 5, 5, 3)
    assert sum(p.price for p in chosen) <= 100.0 + 1e-6
    for nat, c in Counter(p.nation for p in chosen).items():
        assert c <= 3
    sp = Counter(next(pl.position for pl in chosen if pl.pid == s) for s in starters)
    assert sp["GK"] == 1 and 3 <= sp["DEF"] <= 5 and 3 <= sp["MID"] <= 5 and 1 <= sp["FWD"] <= 3
    # the burner starts the best XI; the cheap fillers (low exp_next) are benched
    bench = {p.pid for p in chosen} - set(starters)
    assert 3 in bench  # the worthless GK never starts


def test_qb_bonus_tips_a_borderline_starter_into_the_xi():
    pool = _pool()
    base_chosen, base_xi = select_squad_xi(pool, 100.0, 3, value_attr="exp_next")
    # pid 24 (FWD 'e', exp 4.0) is normally the bench FWD; a big advancement bonus only for
    # HIS pid must pull him into the starting XI (QB synergy favours likely-advancers).
    qb = {p.pid: 0.0 for p in pool}
    qb[24] = 5.0
    _, xi2 = select_squad_xi(pool, 100.0, 3, value_attr="exp_next", qb_bonus=qb)
    assert 24 in xi2 and 24 not in base_xi


def test_build_burner_squad_freezes_its_xi():
    sq = build_burner_squad(_pool(), budget=100.0, nation_cap=3)
    assert len(sq.starters) == 11 and len(sq.players) == 15
    assert sq.captain in sq.starters
    d, m, f = (int(x) for x in sq.formation.split("-"))
    assert (d + m + f) == 10


def test_chip_schedule_holds_wildcard_past_r16():
    chips = ["Wildcard", "Qualification Booster", "Maximum Captain", "12th Man", "Mystery Booster"]
    qb_by_round = {"R32": 17.0, "R16": 12.0, "QF": 7.0, "SF": 4.0}
    sched = chip_schedule("R32", chips, qb_by_round)   # no squad data → WC defaults off R16
    by_round = {e["round"]: e["chip"] for e in sched["schedule"]}
    assert by_round.get("R32") == "Qualification Booster"   # QB best where most are alive
    assert by_round.get("R16") != "Wildcard"                # Wildcard is NOT pinned to R16 anymore
    wc = [e["round"] for e in sched["schedule"] if e["chip"] == "Wildcard"]
    assert wc and wc[0] == "QF"                             # fallback (no squad) = QF, not R16
    assert sched["this_round"]["chip"] == "Qualification Booster"
    assert sched["this_round"]["status"] == "PLAY"
    used = [e["chip"] for e in sched["schedule"]]
    assert len(used) == len(set(used))
    assert "Mystery Booster" not in used
    assert any("Mystery" in n for n in sched["notes"])


def test_chip_schedule_places_all_five_one_per_round_with_clean_sheet_shield():
    chips = ["Wildcard", "Qualification Booster", "Maximum Captain", "12th Man", "Mystery Booster"]
    qb = {"R32": 20.0, "R16": 16.0, "QF": 14.0, "SF": 12.0}
    sched = chip_schedule("R32", chips, qb,
                          mystery={"known": True, "clean_sheet": True, "name": "Clean Sheet Shield",
                                   "best_round": "SF", "effect": "one-goal CS buffer"})
    by_round = {e["round"]: e["chip"] for e in sched["schedule"]}
    # 5 chips, 5 rounds, one each — QB@R32, 12th@R16 (most games), WC@QF (held off R16), CSS@SF, MaxCap@Final
    assert by_round == {"R32": "Qualification Booster", "R16": "12th Man", "QF": "Wildcard",
                        "SF": "Clean Sheet Shield", "final": "Maximum Captain"}
    assert len(sched["schedule"]) == 5
    assert sched["this_round"]["chip"] == "Qualification Booster"


def test_wildcard_goes_to_max_squad_breakage_not_r16():
    from src.fantasy.optimizer import Squad
    # favourites squad: survives the R32 (reach_R16 ~0.9) but thins out by QF
    adv = {n: {"reach_R16": 0.9, "reach_QF": 0.5, "reach_SF": 0.25, "reach_final": 0.12}
           for n in ("a", "b")}
    players = [mk(i, "MID", ("a" if i % 2 else "b"), 6.0, 5.0) for i in range(1, 12)] \
        + [mk(i, "DEF", "a", 5.0, 4.0) for i in range(12, 16)]
    sq = Squad(players=players, starters=[p.pid for p in players[:11]], captain=1, vice=2,
               bench=[p.pid for p in players[11:]], formation="3-5-2", cost=0.0, bank=0.0,
               budget=105.0, xi_exp=0.0, squad_horizon=0.0)
    sched = chip_schedule("R32",
                          ["Wildcard", "Qualification Booster", "12th Man", "Maximum Captain", "Mystery Booster"],
                          {"R32": 18.0, "R16": 14.0, "QF": 10.0, "SF": 7.0},
                          squad=sq, advancement=adv, ft_by_round={"R16": 4, "QF": 4, "SF": 5, "final": 6},
                          mystery={"known": True, "clean_sheet": True, "name": "Clean Sheet Shield", "best_round": "SF"})
    wc = [e["round"] for e in sched["schedule"] if e["chip"] == "Wildcard"][0]
    assert wc != "R16"                  # the squad survives R32 → R16's free transfers cover it
    assert wc in ("QF", "SF")           # held for the deeper break (here QF: breakage peaks there)


def test_qb_ev_by_round_proxy_monotone_helper():
    # qb_ev_by_round just maps each remaining round through squad_qb_ev; smoke-test shape
    class _Sq:
        starters = []
        def by_pid(self):
            return {}
    out = qb_ev_by_round(_Sq(), {}, "R32")
    assert set(out) == {"R32", "R16", "QF", "SF"}


def test_css_ev_counts_concede_exactly_one(monkeypatch):
    """U12 — CSS EV = CS_pts × P(concede exactly 1) × played-60, GK/DEF/MID only."""
    import numpy as np
    from types import SimpleNamespace
    from src.fantasy.booster import css_ev
    from src.fantasy.optimizer import Squad

    # home team: P(concede exactly 1) = column-1 mass = 0.3
    P = np.array([[0.2, 0.1, 0.0],
                  [0.3, 0.1, 0.1],
                  [0.1, 0.1, 0.0]])
    players = [mk(1, "GK", "a", 5.0, 4.0), mk(2, "DEF", "a", 5.0, 4.0),
               mk(3, "MID", "a", 6.0, 5.0), mk(4, "FWD", "a", 7.0, 5.0)]
    for p in players:
        p.next_num, p.next_is_home, p.next_minutes = 55, True, 1.0
    sq = Squad(players=players, starters=[1, 2, 3, 4], captain=3, vice=4, bench=[],
               formation="1-1-1", cost=0.0, bank=0.0, budget=105.0, xi_exp=0.0,
               squad_horizon=0.0)
    fc = SimpleNamespace(match_forecasts={55: SimpleNamespace(P=P)})
    out = css_ev(sq, fc)
    # P(exactly 1 conceded) = 0.1+0.1+0.1 (row of away=1... home perspective: P[:,1])
    p_c1 = float(P[:, 1].sum())
    want = (5 + 5 + 1) * p_c1 * 0.92          # GK5 + DEF5 + MID1; FWD contributes 0
    assert abs(out["ev"] - round(want, 1)) < 0.06
    assert list(out["by_team"]) == ["a"] and len(out["by_team"]["a"]["players"]) == 3


def test_css_ev_feeds_chip_schedule_reason():
    import numpy as np
    from types import SimpleNamespace
    from src.fantasy.booster import css_ev
    from src.fantasy.optimizer import Squad
    P = np.array([[0.4, 0.2], [0.2, 0.2]])
    players = [mk(1, "GK", "a", 5.0, 4.0)]
    players[0].next_num, players[0].next_is_home, players[0].next_minutes = 7, False, 1.0
    sq = Squad(players=players, starters=[1], captain=1, vice=1, bench=[], formation="",
               cost=0.0, bank=0.0, budget=105.0, xi_exp=0.0, squad_horizon=0.0)
    fc = SimpleNamespace(match_forecasts={7: SimpleNamespace(P=P)})
    css = css_ev(sq, fc)
    sched = chip_schedule("SF", ["Mystery Booster"], {},
                          mystery={"known": True, "clean_sheet": True,
                                   "name": "Clean Sheet Shield", "best_round": "SF"},
                          css=css)
    entry = [e for e in sched["schedule"] if e["chip"] == "Clean Sheet Shield"][0]
    assert entry["ev"] == css["ev"] and "U12" in entry["reason"]
