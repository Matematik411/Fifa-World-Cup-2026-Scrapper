"""Unit tests for the fantasy ILP squad optimizer on a tiny hand-checked pool."""
from collections import Counter

from src.fantasy.optimizer import build_squad, pick_xi, select_squad
from src.fantasy.projections import PlayerProj


def mk(pid, pos, nation, price, horizon, exp_next=None, mins=0.9):
    return PlayerProj(pid=pid, name=f"P{pid}", nation=nation, group="A", position=pos,
                      price=price, ownership=10.0, minutes_prob=mins,
                      exp_next=exp_next if exp_next is not None else horizon / 5.0,
                      exp_avg=horizon / 5.0, horizon=horizon, per_match={}, tags=[], why="")


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
