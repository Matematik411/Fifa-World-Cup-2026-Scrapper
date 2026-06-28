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


def select_squad_xi(projs: list[PlayerProj], budget: float, nation_cap: int, *,
                    value_attr: str = "exp_next", qb_bonus: dict[int, float] | None = None,
                    bench_value_frac: float = 0.1,
                    forced_in: set[int] | None = None, forced_out: set[int] | None = None
                    ) -> tuple[list[PlayerProj], list[int]]:
    """Joint 15-man squad + starting-XI ILP that maximizes the STARTING XI's value
    (optionally plus a per-starter Qualification-Booster advancement bonus), with the
    bench only lightly weighted (U4).

    This is the right objective for a single-round "burner" build — the R32 free
    rebuild when the Wildcard is queued for R16 — where squad depth/longevity (the
    `horizon` objective of select_squad) is wasted, because the R16 Wildcard
    re-optimizes the whole squad anyway. Maximizing the XI's next-round points (+ the
    QB advancement bonus, which only pays for STARTERS) instead spends the budget on
    the best XI and lets the bench be cheap.

    value_attr: PlayerProj attribute used as the per-player starting value (e.g. "exp_next").
    qb_bonus:   {pid: bonus} added to a player's value IFF he starts (QB's +2*P(advance)).
    Returns (chosen 15, starter pids).
    """
    forced_in = forced_in or set()
    forced_out = forced_out or set()
    qb_bonus = qb_bonus or {}
    pool = [p for p in projs if p.pid not in forced_out and p.minutes_prob > 0.05]
    pool_ids = {p.pid for p in pool}
    for p in projs:
        if p.pid in forced_in and p.pid not in pool_ids:
            pool.append(p)

    prob = pulp.LpProblem("squad_xi", pulp.LpMaximize)
    x = {p.pid: pulp.LpVariable(f"x_{p.pid}", cat="Binary") for p in pool}   # in the 15
    y = {p.pid: pulp.LpVariable(f"y_{p.pid}", cat="Binary") for p in pool}   # in the XI
    val = {p.pid: float(getattr(p, value_attr)) for p in pool}
    start_val = {p.pid: val[p.pid] + float(qb_bonus.get(p.pid, 0.0)) for p in pool}
    prob += pulp.lpSum(start_val[p.pid] * y[p.pid]
                       + bench_value_frac * val[p.pid] * (x[p.pid] - y[p.pid]) for p in pool)
    prob += pulp.lpSum(x.values()) == 15
    prob += pulp.lpSum(y.values()) == 11
    for p in pool:
        prob += y[p.pid] <= x[p.pid]
    for pos, need in POS_NEED.items():
        prob += pulp.lpSum(x[p.pid] for p in pool if p.position == pos) == need
        cnt_y = pulp.lpSum(y[p.pid] for p in pool if p.position == pos)
        prob += cnt_y >= XI_MIN[pos]
        prob += cnt_y <= XI_MAX[pos]
    prob += pulp.lpSum(p.price * x[p.pid] for p in pool) <= budget
    for nat in {p.nation for p in pool}:
        prob += pulp.lpSum(x[p.pid] for p in pool if p.nation == nat) <= nation_cap
    for pid in forced_in:
        if pid in x:
            prob += x[pid] == 1

    status = prob.solve(_solver())
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Squad+XI ILP not optimal: {pulp.LpStatus[status]}")
    chosen = [p for p in pool if x[p.pid].value() and x[p.pid].value() > 0.5]
    starters = [p.pid for p in pool if y[p.pid].value() and y[p.pid].value() > 0.5]
    return chosen, starters


def build_burner_squad(projs: list[PlayerProj], budget: float, nation_cap: int, *,
                       qb_bonus: dict[int, float] | None = None,
                       forced_in: set[int] | None = None) -> Squad:
    """A single-round 'burner' Squad: best starting XI (+ optional QB advancement bonus)
    with a cheap bench. Used for the R32 rebuild when the Wildcard is reserved for R16."""
    chosen, starters = select_squad_xi(projs, budget, nation_cap, value_attr="exp_next",
                                       qb_bonus=qb_bonus, forced_in=forced_in)
    return assemble_squad(chosen, budget, lineup={"starters": starters})


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


def squad_from_pids(projs: list[PlayerProj], pids: list[int], budget: float,
                    lineup: dict | None = None) -> Squad:
    """Squad object (XI/captain/bench) for a FIXED 15 — the user's reachable team
    (owned ± this round's transfers), as opposed to the unconstrained optimum.
    Pass `lineup` (a stored starters/bench/captain) to freeze the XI for a round that
    is already locked, instead of re-deriving the best XI from current projections."""
    by_pid = {p.pid: p for p in projs}
    missing = [pid for pid in pids if pid not in by_pid]
    if missing or len(pids) != 15:
        raise ValueError(f"Cannot assemble squad: {len(pids)} pids, missing from pool: {missing}")
    return assemble_squad([by_pid[pid] for pid in pids], budget, lineup=lineup)


def _xi_is_legal(starters: list[int], by_pid: dict) -> bool:
    if len(starters) != 11:
        return False
    nb = {pos: 0 for pos in ("GK", "DEF", "MID", "FWD")}
    for pid in starters:
        nb[by_pid[pid].position] += 1
    return all(XI_MIN[pos] <= nb[pos] <= XI_MAX[pos] for pos in nb)


def assemble_squad(chosen: list[PlayerProj], budget: float, lineup: dict | None = None) -> Squad:
    by_pid = {p.pid: p for p in chosen}
    # A locked round freezes the XI/captain set at its deadline. If a valid `lineup`
    # is supplied and every starter is still in the squad, honour it as-is; otherwise
    # (initial build, unlimited-window rebuild, post-transfer team) optimise the XI fresh.
    fixed = None
    if lineup:
        ls = [pid for pid in (lineup.get("starters") or []) if pid in by_pid]
        if _xi_is_legal(ls, by_pid):
            fixed = ls
    if fixed is not None:
        starters = fixed
        nb = {pos: sum(1 for pid in starters if by_pid[pid].position == pos)
              for pos in ("GK", "DEF", "MID", "FWD")}
        formation = f"{nb['DEF']}-{nb['MID']}-{nb['FWD']}"
        xi_exp = sum(by_pid[pid].exp_next for pid in starters)
    else:
        starters, formation, xi_exp = pick_xi(chosen)
    # Captain/vice from outfield attackers/mids only — the x2 multiplier wants ceiling,
    # and GK/DEF expected points are clean-sheet-driven (low ceiling, high variance).
    attack_starters = sorted(
        [pid for pid in starters if by_pid[pid].position in ("MID", "FWD")],
        key=lambda pid: by_pid[pid].exp_next, reverse=True)
    cap_pool = attack_starters or sorted(starters, key=lambda pid: by_pid[pid].exp_next, reverse=True)
    locked_cap = (lineup or {}).get("captain")
    if fixed is not None and locked_cap in starters:
        # Honour the captain you locked in for this round (the relay is handled separately).
        captain = locked_cap
    else:
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
    locked_bench = [pid for pid in ((lineup or {}).get("bench") or []) if pid in set(bench_ids)]
    if fixed is not None and len(locked_bench) == len(bench_ids):
        bench = locked_bench  # preserve the locked auto-sub order
    else:
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
