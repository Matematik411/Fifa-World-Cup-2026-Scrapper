"""Unit tests for the fantasy ILP squad optimizer on a tiny hand-checked pool."""
from collections import Counter

from src.fantasy.optimizer import build_squad, pick_xi, select_squad
from src.fantasy.projections import PlayerProj


def mk(pid, pos, nation, price, horizon, exp_next=None, mins=0.9, next_date=""):
    return PlayerProj(pid=pid, name=f"P{pid}", nation=nation, group="A", position=pos,
                      price=price, ownership=10.0, minutes_prob=mins,
                      exp_next=exp_next if exp_next is not None else horizon / 5.0,
                      exp_avg=horizon / 5.0, horizon=horizon, per_match={},
                      next_date=next_date, tags=[], why="")


def _balanced_pool():
    pool = []
    pid = 1
    # GK
    for nat, pr, h in [("a", 4.5, 12), ("b", 4.5, 11), ("c", 4.0, 3)]:
        pool.append(mk(pid, "GK", nat, pr, h)); pid += 1
    # DEF (7 across nations)
    for nat, pr, h in [("a", 5.0, 14), ("b", 5.5, 13), ("c", 4.5, 12), ("d", 6.0, 11),
                       ("e", 4.5, 10), ("f", 4.0, 4), ("d", 4.0, 3)]:
        pool.append(mk(pid, "DEF", nat, pr, h)); pid += 1
    # MID (8)
    for nat, pr, h in [("a", 8.0, 22), ("b", 7.5, 20), ("c", 6.5, 18), ("e", 6.0, 16),
                       ("f", 5.5, 15), ("d", 5.0, 9), ("e", 4.5, 5), ("f", 4.0, 4)]:
        pool.append(mk(pid, "MID", nat, pr, h)); pid += 1
    # FWD (6)
    for nat, pr, h in [("a", 10.5, 30), ("b", 8.5, 24), ("c", 7.5, 20),
                       ("e", 6.0, 12), ("f", 5.0, 6), ("d", 4.5, 4)]:
        pool.append(mk(pid, "FWD", nat, pr, h)); pid += 1
    return pool


def test_squad_satisfies_all_constraints():
    pool = _balanced_pool()
    chosen = select_squad(pool, budget=100.0, nation_cap=3)
    assert len(chosen) == 15
    pos = Counter(p.position for p in chosen)
    assert (pos["GK"], pos["DEF"], pos["MID"], pos["FWD"]) == (2, 5, 5, 3)
    assert sum(p.price for p in chosen) <= 100.0 + 1e-6
    for nat, c in Counter(p.nation for p in chosen).items():
        assert c <= 3, f"nation cap violated for {nat}"
    # the worthless GK (pid 3, horizon 3) must be excluded in favour of the two best GKs
    assert 3 not in {p.pid for p in chosen}


def test_nation_cap_binds():
    # one nation 'z' has 6 elite cheap players; cap must hold them to <=3
    pool = _balanced_pool()
    pid = 100
    for pos in ["DEF", "DEF", "MID", "MID", "FWD", "FWD"]:
        pool.append(mk(pid, pos, "z", 4.0, 50)); pid += 1   # absurdly valuable & cheap
    chosen = select_squad(pool, budget=100.0, nation_cap=3)
    assert Counter(p.nation for p in chosen)["z"] == 3      # exactly the cap, not more


def test_xi_is_valid_formation():
    pool = _balanced_pool()
    squad = build_squad(pool, cfg=None, budget=100.0, nation_cap=3)
    assert len(squad.starters) == 11
    d, m, f = (int(x) for x in squad.formation.split("-"))
    assert 3 <= d <= 5 and 3 <= m <= 5 and 1 <= f <= 3 and (d + m + f) == 10
    assert squad.captain in squad.starters
    assert len(squad.players) == 15 and len(squad.bench) == 4


def test_forced_in_player_is_selected():
    pool = _balanced_pool()
    # force the cheap weak GK (pid 3) in; it should appear despite low value
    chosen = select_squad(pool, budget=100.0, nation_cap=3, forced_in={3})
    assert 3 in {p.pid for p in chosen}


def test_captain_prefers_earlier_kickoff_among_near_equals():
    pool = _balanced_pool()
    for p in pool:
        p.next_date = "2026-06-15"
    # pid 19 (FWD, exp 6.0) is the top scorer but plays late; pid 20 (exp 5.8,
    # within the 0.4 relay window) plays first -> armband starts on 20
    by_pid = {p.pid: p for p in pool}
    by_pid[19].next_date = "2026-06-16"
    by_pid[20].next_date = "2026-06-12"
    by_pid[20].exp_next = 5.8
    squad = build_squad(pool, cfg=None, budget=100.0, nation_cap=3)
    if 19 in squad.starters and 20 in squad.starters:   # both premiums start
        assert squad.captain == 20
        assert squad.vice == 19


def test_captain_ladder_orders_by_date_and_thresholds():
    from src.pipeline import _captain_ladder
    pool = _balanced_pool()
    by_pid = {p.pid: p for p in pool}
    dates = {19: "2026-06-12", 20: "2026-06-14", 21: "2026-06-16",
             11: "2026-06-13", 12: "2026-06-15"}
    for p in pool:
        p.next_date = dates.get(p.pid, "2026-06-17")
    squad = build_squad(pool, cfg=None, budget=100.0, nation_cap=3)
    ladder = _captain_ladder(squad, lambda p: None)   # no live feed -> no banked column
    assert ladder[0]["name"] == by_pid[squad.captain].name
    assert all("banked" not in r for r in ladder)
    # rungs strictly later in the round, each with a switch rule except the last
    ds = [r["date"] for r in ladder]
    assert ds == sorted(ds) and len(set(ds)) == len(ds)
    for r in ladder[:-1]:
        assert "next_name" in r and r["switch_if"] >= 0
    assert "next_name" not in ladder[-1]


def test_playbook_live_verdict_from_feed_points():
    from src.pipeline import _playbook
    pool = _balanced_pool()
    dates = {19: "2026-06-12", 20: "2026-06-14", 21: "2026-06-16",
             11: "2026-06-13", 12: "2026-06-15"}
    for p in pool:
        p.next_date = dates.get(p.pid, "2026-06-17")
    squad = build_squad(pool, cfg=None, budget=100.0, nation_cap=3)
    by_pid = squad.by_pid()
    cap = by_pid[squad.captain]
    cap.round_points = {"1": 2.0}

    # captain's match has NOT ended -> no live verdict, no banked column
    pb = _playbook(squad, "1", ended=frozenset())
    assert pb["live"] is None and pb["xi_live"] is None

    # captain's match ended on 2 pts -> concrete verdict vs the next rung's E
    pb = _playbook(squad, "1", ended=frozenset({cap.nation}))
    assert pb["ladder"][0]["banked"] == 2
    expected = "SWITCH" if 2 < pb["ladder"][1]["exp"] else "HOLD"
    assert pb["live"]["verdict"] == expected
    # XI tally: captain double counted; same-nation teammates banked 0 (no feed entry)
    n_done = sum(1 for pid in squad.starters if by_pid[pid].nation == cap.nation)
    assert pb["xi_live"] == {"points": 4, "done": n_done, "captain_doubled": True}


def test_captain_relay_survives_played_captain_date_rollover():
    """Regression: once the captain has played his round game, the live feed advances
    his next_date to a LATER round. The relay ladder must still surface not-yet-played
    teammates (anchored at his played game, not his rolled-forward date) and resolve to
    SWITCH — otherwise it collapses to a false HOLD with the armband stuck on a spent
    captain. This is the common case, since captains are picked to kick off early."""
    from src.pipeline import _playbook
    pool = _balanced_pool()
    squad = build_squad(pool, cfg=None, budget=100.0, nation_cap=3)
    by_pid = squad.by_pid()
    cap = by_pid[squad.captain]
    # captain played early, banked little; his NEXT fixture has rolled to a later round
    cap.round_points = {"1": 2.0}
    cap.next_date = "2026-06-25"
    # other MID/FWD starters still have their current-round game ahead (all < 06-25).
    # The EARLIEST kickoff gets the LOWEST projection and the latest the highest, so an
    # EV-greedy chain would (wrongly) jump straight to the late name; the earliest-first
    # relay must take the soonest game first (it can relay forward after seeing it).
    earlier = sorted(pid for pid in squad.starters
                     if pid != cap.pid and by_pid[pid].position in ("MID", "FWD"))
    for i, pid in enumerate(earlier):
        by_pid[pid].next_date = f"2026-06-{14 + i:02d}"   # 06-14, 06-15, 06-16, ...
        by_pid[pid].exp_next = 4.0 + i                     # ascending: latest == highest EV

    pb = _playbook(squad, "1", ended=frozenset({cap.nation}))
    assert pb["ladder"][0]["banked"] == 2
    assert len(pb["ladder"]) >= 2, "relay collapsed to the spent captain only"
    assert pb["live"]["verdict"] == "SWITCH"
    assert pb["live"]["to"] is not None and pb["live"]["to"] != cap.name
    # earliest-first: the first relay rung is the soonest-kicking VALID teammate, and
    # the published ladder is strictly date-ascending. Teammates sharing the captain's
    # nation have already played too (their match is in `ended`) so they are not relay
    # targets — exclude them when computing the expected earliest date.
    cand_dates = sorted(by_pid[pid].next_date for pid in earlier
                        if by_pid[pid].nation != cap.nation and by_pid[pid].exp_next >= 3.0)
    assert pb["ladder"][1]["date"] == cand_dates[0]
    rung_dates = [r["date"] for r in pb["ladder"][1:]]
    assert rung_dates == sorted(rung_dates) and len(set(rung_dates)) == len(rung_dates)


def test_squad_from_pids_assembles_exact_15():
    import pytest

    from src.fantasy.optimizer import squad_from_pids
    pool = _balanced_pool()
    # a legal fixed 15: GKs 1-2, DEFs 4-8, MIDs 11-15, FWDs 19-21
    pids = [1, 2, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 19, 20, 21]
    squad = squad_from_pids(pool, pids, budget=100.0)
    assert {p.pid for p in squad.players} == set(pids)   # exactly the given 15, no re-optimization
    assert len(squad.starters) == 11 and squad.captain in squad.starters
    assert abs(squad.cost + squad.bank - 100.0) < 1e-6
    # an unknown pid must fail loudly (caller falls back + warns)
    with pytest.raises(ValueError):
        squad_from_pids(pool, pids[:-1] + [9999], budget=100.0)


def _mk_squad(players, starters, bench, captain):
    from src.fantasy.optimizer import Squad
    return Squad(players=players, starters=starters, captain=captain,
                 vice=starters[0], bench=bench, formation="3-5-2", cost=0.0,
                 bank=0.0, budget=100.0, xi_exp=0.0, squad_horizon=0.0)


def test_lineup_fixes_flags_non_playing_starters():
    """A locked XI with a back-up GK and an injured DEF should yield one
    same-position swap each, to a bench player who'll actually play; a nailed
    starter is never flagged."""
    from src.pipeline import _lineup_fixes
    gk_out = mk(1, "GK", "a", 4.5, 0.5, exp_next=0.1, mins=0.03, next_date="2026-06-16")
    gk_in = mk(2, "GK", "b", 5.0, 20, exp_next=4.0, mins=0.96, next_date="2026-06-16")
    def_out = mk(3, "DEF", "c", 4.3, 0.3, exp_next=0.05, mins=0.02, next_date="2026-06-17")
    def_in = mk(4, "DEF", "d", 5.0, 15, exp_next=3.0, mins=0.90, next_date="2026-06-16")
    def_ok = mk(5, "DEF", "e", 5.5, 18, exp_next=3.5, mins=0.95, next_date="2026-06-16")
    squad = _mk_squad([gk_out, gk_in, def_out, def_in, def_ok],
                      starters=[1, 3, 5], bench=[2, 4], captain=5)
    fixes = _lineup_fixes(squad, banked_of=lambda p: None)
    pairs = {(f["out"], f["in"]) for f in fixes}
    assert ("P1", "P2") in pairs                      # back-up GK -> playing GK
    assert ("P3", "P4") in pairs                      # injured DEF -> playing DEF
    assert all(f["out"] != "P5" for f in fixes)       # a nailed starter is never flagged
    assert all(f["gain"] > 0 for f in fixes)


def test_lineup_fixes_skips_locked_and_unreplaceable():
    from src.pipeline import _lineup_fixes
    def_out = mk(3, "DEF", "c", 4.3, 0.3, exp_next=0.05, mins=0.02, next_date="2026-06-17")
    def_in = mk(4, "DEF", "d", 5.0, 15, exp_next=3.0, mins=0.90, next_date="2026-06-16")
    squad = _mk_squad([def_out, def_in], starters=[3], bench=[4], captain=3)
    # his match already FINISHED (banked != None) -> result locked, nothing to do
    assert _lineup_fixes(squad, banked_of=lambda p: 0.0) == []
    # only bench option is the wrong position -> no legal same-position swap
    fwd_in = mk(5, "FWD", "d", 5.0, 15, exp_next=3.0, mins=0.90)
    squad2 = _mk_squad([def_out, fwd_in], starters=[3], bench=[5], captain=3)
    assert _lineup_fixes(squad2, banked_of=lambda p: None) == []
