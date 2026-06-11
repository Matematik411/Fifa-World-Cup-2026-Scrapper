"""Constrained squad selection (ILP) + starting XI / captain / bench.

Squad ILP: maximize horizon-weighted value subject to budget, 2/5/5/3 squad
composition, per-nation cap, and 15 players. Starting XI: a second ILP picks a
valid formation maximizing the upcoming round's expected points.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from .projections import PlayerProj

POS_NEED = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
# starting-XI position ranges that enumerate exactly the valid formations
XI_MIN = {"GK": 1, "DEF": 3, "MID": 3, "FWD": 1}
XI_MAX = {"GK": 1, "DEF": 5, "MID": 5, "FWD": 3}


def _solver():
    import shutil
    # Prefer system CBC (e.g. nix `cbc`) via the non-deprecated COIN_CMD; fall back to bundled.
    cbc = shutil.which("cbc")
    if cbc:
        try:
            s = pulp.COIN_CMD(path=cbc, msg=0)
            if s.available():
                return s
        except Exception:
            pass
    for name in ("PULP_CBC_CMD", "COIN_CMD"):
        try:
            s = getattr(pulp, name)(msg=0)
            if s.available():
                return s
        except Exception:
            continue
    return None


@dataclass
class Squad:
    players: list[PlayerProj]
    starters: list[int]          # pids
    captain: int
    vice: int
    bench: list[int]             # pids, ordered (outfield by EP, backup GK last)
    formation: str
    cost: float
    bank: float
    budget: float
    xi_exp: float
    squad_horizon: float
    nation_counts: dict = field(default_factory=dict)

    def by_pid(self) -> dict:
        return {p.pid: p for p in self.players}


def select_squad(projs: list[PlayerProj], budget: float, nation_cap: int,
                 forced_in: set[int] | None = None, forced_out: set[int] | None = None) -> list[PlayerProj]:
    forced_in = forced_in or set()
    forced_out = forced_out or set()
    pool = [p for p in projs if p.pid not in forced_out and p.minutes_prob > 0.05]
    # always allow forced_in even if low minutes
    pool_ids = {p.pid for p in pool}
    for p in projs:
        if p.pid in forced_in and p.pid not in pool_ids:
            pool.append(p)

    prob = pulp.LpProblem("squad", pulp.LpMaximize)
    x = {p.pid: pulp.LpVariable(f"x_{p.pid}", cat="Binary") for p in pool}
    prob += pulp.lpSum(p.horizon * x[p.pid] for p in pool)
    prob += pulp.lpSum(x.values()) == 15
    for pos, need in POS_NEED.items():
        prob += pulp.lpSum(x[p.pid] for p in pool if p.position == pos) == need
    prob += pulp.lpSum(p.price * x[p.pid] for p in pool) <= budget
    nations = {p.nation for p in pool}
    for nat in nations:
        prob += pulp.lpSum(x[p.pid] for p in pool if p.nation == nat) <= nation_cap
    for pid in forced_in:
        if pid in x:
            prob += x[pid] == 1

    status = prob.solve(_solver())
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Squad ILP not optimal: {pulp.LpStatus[status]}")
    chosen = [p for p in pool if x[p.pid].value() and x[p.pid].value() > 0.5]
    return chosen


def pick_xi(squad_players: list[PlayerProj]) -> tuple[list[int], str, float]:
    prob = pulp.LpProblem("xi", pulp.LpMaximize)
    y = {p.pid: pulp.LpVariable(f"y_{p.pid}", cat="Binary") for p in squad_players}
    prob += pulp.lpSum(p.exp_next * y[p.pid] for p in squad_players)
    prob += pulp.lpSum(y.values()) == 11
    for pos in ("GK", "DEF", "MID", "FWD"):
        cnt = pulp.lpSum(y[p.pid] for p in squad_players if p.position == pos)
        prob += cnt >= XI_MIN[pos]
        prob += cnt <= XI_MAX[pos]
    prob.solve(_solver())
    starters = [p.pid for p in squad_players if y[p.pid].value() and y[p.pid].value() > 0.5]
    nb = {pos: sum(1 for p in squad_players if p.pid in starters and p.position == pos)
          for pos in ("GK", "DEF", "MID", "FWD")}
    formation = f"{nb['DEF']}-{nb['MID']}-{nb['FWD']}"
    xi_exp = sum(p.exp_next for p in squad_players if p.pid in starters)
    return starters, formation, xi_exp


def build_squad(projs: list[PlayerProj], cfg, budget: float, nation_cap: int,
                forced_in: set[int] | None = None) -> Squad:
    chosen = select_squad(projs, budget, nation_cap, forced_in=forced_in)
    return assemble_squad(chosen, budget)


def squad_from_pids(projs: list[PlayerProj], pids: list[int], budget: float) -> Squad:
    """Squad object (XI/captain/bench) for a FIXED 15 — the user's reachable team
    (owned ± this round's transfers), as opposed to the unconstrained optimum."""
    by_pid = {p.pid: p for p in projs}
    missing = [pid for pid in pids if pid not in by_pid]
    if missing or len(pids) != 15:
        raise ValueError(f"Cannot assemble squad: {len(pids)} pids, missing from pool: {missing}")
    return assemble_squad([by_pid[pid] for pid in pids], budget)


def assemble_squad(chosen: list[PlayerProj], budget: float) -> Squad:
    starters, formation, xi_exp = pick_xi(chosen)
    by_pid = {p.pid: p for p in chosen}
    # Captain/vice from outfield attackers/mids only — the x2 multiplier wants ceiling,
    # and GK/DEF expected points are clean-sheet-driven (low ceiling, high variance).
    attack_starters = sorted(
        [pid for pid in starters if by_pid[pid].position in ("MID", "FWD")],
        key=lambda pid: by_pid[pid].exp_next, reverse=True)
    cap_pool = attack_starters or sorted(starters, key=lambda pid: by_pid[pid].exp_next, reverse=True)
    # Near-equal candidates: prefer the EARLIEST kickoff. The armband can be moved
    # mid-round to a not-yet-played starter once the captain's match ends, so an
    # early captain keeps the relay option alive at almost no EV cost.
    best_ep = by_pid[cap_pool[0]].exp_next
    near = [pid for pid in cap_pool if by_pid[pid].exp_next >= best_ep - 0.4]
    captain = min(near, key=lambda pid: (by_pid[pid].next_date or "9999-99-99",
                                         -by_pid[pid].exp_next))
    vice_pool = [pid for pid in cap_pool if pid != captain]
    vice = vice_pool[0] if vice_pool else captain
    bench_ids = [p.pid for p in chosen if p.pid not in starters]
    # bench order: outfield by exp_next desc, backup GK last
    bench_out = sorted([pid for pid in bench_ids if by_pid[pid].position != "GK"],
                       key=lambda pid: by_pid[pid].exp_next, reverse=True)
    bench_gk = [pid for pid in bench_ids if by_pid[pid].position == "GK"]
    bench = bench_out + bench_gk
    cost = sum(p.price for p in chosen)
    nation_counts: dict[str, int] = {}
    for p in chosen:
        nation_counts[p.nation] = nation_counts.get(p.nation, 0) + 1
    return Squad(
        players=chosen, starters=starters, captain=captain, vice=vice, bench=bench,
        formation=formation, cost=round(cost, 1), bank=round(budget - cost, 1), budget=budget,
        xi_exp=round(xi_exp, 2), squad_horizon=round(sum(p.horizon for p in chosen), 1),
        nation_counts=nation_counts,
    )
