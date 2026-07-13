"""plan_transfers packaging: swaps affordable only NET OF THE PACKAGE must be found.

Regression tests for the 2026-07-09 QF bug: candidate generation pre-filtered with
`price_delta > bank` against the STATIC starting bank, so Barcola→Yamal (+2.0, bank 1.4)
was never generated even though the package's freeing sales paid for it.
"""
from src.fantasy.projections import PlayerProj
from src.fantasy.transfers import plan_transfers


def mk(pid, pos, nation, price, horizon, exp_next=None, mins=0.9):
    return PlayerProj(pid=pid, name=f"P{pid}", nation=nation, group="A", position=pos,
                      price=price, ownership=10.0, minutes_prob=mins,
                      exp_next=exp_next if exp_next is not None else horizon / 5.0,
                      exp_avg=horizon / 5.0, horizon=horizon, per_match={})


def _moves_set(plan):
    return {(m.out_pid, m.in_pid) for m in plan["moves"]}


def test_package_affordable_swap_found():
    """The big upgrade (+2.0) exceeds the static bank (0.5) but a same-package
    downgrade (−2.5) pays for it — both must be picked."""
    owned = [
        mk(1, "GK", "a", 5.0, 10),
        mk(2, "DEF", "b", 5.0, 10), mk(3, "DEF", "c", 5.0, 10), mk(4, "DEF", "d", 5.0, 10),
        mk(5, "DEF", "e", 5.0, 10), mk(6, "DEF", "f", 5.0, 10),
        mk(7, "MID", "g", 7.5, 8),          # weak mid, sell → frees 2.5
        mk(8, "MID", "h", 5.0, 10), mk(9, "MID", "i", 5.0, 10),
        mk(10, "MID", "j", 5.0, 10), mk(11, "MID", "k", 5.0, 10),
        mk(12, "FWD", "l", 9.0, 12),        # weak fwd for its price
        mk(13, "FWD", "m", 5.0, 10), mk(14, "FWD", "n", 5.0, 10),
    ]
    pool = owned + [
        mk(20, "MID", "o", 5.0, 9.5),       # cheap mid: 7→20 frees 2.5, gain +1.5
        mk(21, "FWD", "p", 11.0, 20),       # premium fwd: 12→21 costs +2.0, gain +8 — the prize
    ]
    plan = plan_transfers([p.pid for p in owned], pool, budget=100.0, nation_cap=3,
                          free_transfers=2, bank=0.5)
    assert (12, 21) in _moves_set(plan), "package-affordable premium upgrade was missed"
    assert (7, 20) in _moves_set(plan), "the freeing downgrade must ride along"
    spent = sum(m.price_delta for m in plan["moves"])
    assert spent <= 0.5 + 1e-6


def test_package_never_overspends_bank():
    owned = [mk(1, "GK", "a", 5.0, 5), mk(2, "DEF", "b", 5.0, 5), mk(3, "MID", "c", 5.0, 5),
             mk(4, "FWD", "d", 5.0, 5)]
    pool = owned + [
        mk(10, "DEF", "e", 6.0, 9),   # +1.0
        mk(11, "MID", "f", 6.0, 9),   # +1.0
        mk(12, "FWD", "g", 6.0, 9),   # +1.0
    ]
    plan = plan_transfers([p.pid for p in owned], pool, budget=100.0, nation_cap=3,
                          free_transfers=3, bank=1.0)
    # only one of the three +1.0 upgrades fits the €1.0 bank
    assert sum(m.price_delta for m in plan["moves"]) <= 1.0 + 1e-6
    assert len(plan["moves"]) >= 1


def test_package_respects_nation_cap():
    owned = [mk(1, "GK", "a", 5.0, 5), mk(2, "DEF", "x", 5.0, 5), mk(3, "MID", "x", 5.0, 5),
             mk(4, "FWD", "b", 5.0, 5)]
    pool = owned + [
        mk(10, "DEF", "x", 5.0, 9),   # in-nation x: would make 3 x's — over a cap of 2
        mk(11, "FWD", "x", 5.0, 12),  # also nation x, bigger gain
    ]
    plan = plan_transfers([p.pid for p in owned], pool, budget=100.0, nation_cap=2,
                          free_transfers=2, bank=5.0)
    # owned already has 2 of nation x; only swaps NOT raising the x-count are legal.
    # 2->10 (x out, x in) keeps 2; 4->11 (b out, x in) makes 3 = illegal alongside it.
    ins = {m.in_pid for m in plan["moves"]}
    assert not {10, 11} <= ins, "both x-nation ins would breach the cap"


def test_free_transfer_count_respected():
    owned = [mk(1, "GK", "a", 5.0, 5), mk(2, "DEF", "b", 5.0, 5), mk(3, "MID", "c", 5.0, 5),
             mk(4, "FWD", "d", 5.0, 5)]
    pool = owned + [mk(10, "DEF", "e", 5.0, 7), mk(11, "MID", "f", 5.0, 7),
                    mk(12, "FWD", "g", 5.0, 7)]
    plan = plan_transfers([p.pid for p in owned], pool, budget=100.0, nation_cap=3,
                          free_transfers=2, bank=0.0)
    # gains (+2.0 each) are below the hit threshold (4.0) → no paid extras
    assert plan["free_used"] <= 2 and plan["hits"] == 0
    assert len(plan["moves"]) <= 2


def test_qf_regression_shape_weakest_seller_funds_premium():
    """Shape of the real 2026-07-09 case: bank 1.4; premium upgrade +2.0 only affordable
    net of a cheap sale; the CORRECT package sells the WEAKEST same-position player,
    keeps the stronger one, and still lands the premium."""
    owned = [
        mk(1, "GK", "a", 5.0, 10),
        mk(2, "DEF", "b", 5.0, 10), mk(3, "DEF", "c", 5.0, 10), mk(4, "DEF", "d", 5.0, 10),
        mk(5, "MID", "fra", 8.6, 25),       # "Dembélé" — strong, keep
        mk(6, "MID", "fra", 7.9, 15),       # "Barcola" — weakest premium mid, sell
        mk(7, "MID", "e", 5.0, 8.5), mk(8, "MID", "f", 5.0, 10),
        mk(9, "FWD", "g", 9.0, 18), mk(10, "FWD", "h", 5.0, 10),
    ]
    pool = owned + [
        mk(20, "MID", "esp", 9.9, 24),      # "Yamal": +2.0 over Barcola — gain +9
        mk(21, "MID", "i", 4.0, 9.5),       # "Baena": cheaper AND better — frees 1.0, gain +1
    ]
    plan = plan_transfers([p.pid for p in owned], pool, budget=100.0, nation_cap=3,
                          free_transfers=4, bank=1.4)
    ms = _moves_set(plan)
    assert (6, 20) in ms, "should sell the WEAKEST mid (Barcola) to fund Yamal"
    assert all(out != 5 for out, _ in ms), "must NOT sell the stronger mid (Dembélé)"
