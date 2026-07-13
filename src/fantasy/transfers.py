"""Transfer planning and chip-timing advice for the upcoming round.

First run (no owned squad yet) → recommend the optimal initial 15.
Later runs → given the real owned squad + free transfers, recommend the best
in/out moves (taking a points hit only when the multi-match gain clears it),
plus when to play each chip.
"""
from __future__ import annotations

from dataclasses import dataclass

import pulp

from .optimizer import _solver
from .projections import PlayerProj


@dataclass
class TransferMove:
    out_pid: int
    in_pid: int
    out_name: str
    in_name: str
    position: str
    gain: float       # horizon-value gain
    price_delta: float


# valid starting-XI shapes (DEF, MID, FWD); 1 GK always
_FORMATIONS = [(3, 4, 3), (3, 5, 2), (4, 3, 3), (4, 4, 2), (4, 5, 1), (5, 3, 2), (5, 4, 1), (5, 2, 3)]


def _apply(pids: list[int], moves) -> list[int]:
    out = {m.out_pid for m in moves}
    return [p for p in pids if p not in out] + [m.in_pid for m in moves]


def _best_xi_exp(squad_projs) -> float:
    """Next-round EV of the best legal starting XI from a 15-man pool (by exp_next).
    Used to test whether a transfer actually improves what SCORES (the XI) rather than
    only deepening the bench."""
    pos: dict[str, list[float]] = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in squad_projs:
        if p.position in pos:
            pos[p.position].append(float(p.exp_next))
    for k in pos:
        pos[k].sort(reverse=True)
    if not pos["GK"]:
        return 0.0
    best = -1.0
    gk = pos["GK"][0]
    for d, m, f in _FORMATIONS:
        if len(pos["DEF"]) >= d and len(pos["MID"]) >= m and len(pos["FWD"]) >= f:
            best = max(best, gk + sum(pos["DEF"][:d]) + sum(pos["MID"][:m]) + sum(pos["FWD"][:f]))
    return best


def _max_freeable(owned, projs, owned_ids) -> float:
    """Upper bound on money the REST of a transfer package can free: for each owned
    player, the drop to the cheapest viable same-position replacement. Generous by
    construction — exact affordability is enforced by the packaging step; this only
    prunes candidates no package could ever pay for."""
    floor: dict[str, float] = {}
    for c in projs:
        if c.pid not in owned_ids and c.minutes_prob >= 0.4:
            if c.position not in floor or c.price < floor[c.position]:
                floor[c.position] = c.price
    return sum(max(0.0, o.price - floor[o.position]) for o in owned if o.position in floor)


def _package_ilp(candidates, by_pid, nation_count, nation_cap, bank, max_moves):
    """Best free-transfer package: maximize total horizon gain over ≤max_moves
    non-conflicting swaps whose SUMMED price delta fits the bank (so a swap that is
    only affordable net of the package's freeing sales is found — the static-bank
    greedy missed those). Returns None if no solver is available / solve fails."""
    if not candidates or max_moves <= 0:
        return []
    prob = pulp.LpProblem("transfer_package", pulp.LpMaximize)
    x = [pulp.LpVariable(f"m{i}", cat="Binary") for i in range(len(candidates))]
    # tiny per-move cost: equal-gain packages prefer fewer moves (deterministic output)
    prob += pulp.lpSum((c.gain - 1e-3) * x[i] for i, c in enumerate(candidates))
    prob += pulp.lpSum(x) <= max_moves
    prob += pulp.lpSum(c.price_delta * x[i] for i, c in enumerate(candidates)) <= bank + 1e-6
    outs: dict[int, list] = {}
    ins: dict[int, list] = {}
    for i, c in enumerate(candidates):
        outs.setdefault(c.out_pid, []).append(x[i])
        ins.setdefault(c.in_pid, []).append(x[i])
    for vs in outs.values():
        prob += pulp.lpSum(vs) <= 1
    for vs in ins.values():
        prob += pulp.lpSum(vs) <= 1
    nations = {by_pid[c.in_pid].nation for c in candidates} | {by_pid[c.out_pid].nation for c in candidates}
    for n in nations:
        delta = pulp.lpSum(
            [x[i] for i, c in enumerate(candidates) if by_pid[c.in_pid].nation == n]
            + [-x[i] for i, c in enumerate(candidates) if by_pid[c.out_pid].nation == n])
        prob += nation_count.get(n, 0) + delta <= nation_cap
    solver = _solver()
    try:
        prob.solve(solver) if solver else prob.solve()
    except Exception:
        return None
    if pulp.LpStatus[prob.status] != "Optimal":
        return None
    chosen = [c for i, c in enumerate(candidates) if (x[i].value() or 0) > 0.5]
    chosen.sort(key=lambda m: m.gain, reverse=True)
    return chosen


def _package_greedy(candidates, by_pid, nation_count, nation_cap, bank, max_moves):
    """Sequential fallback (pre-2026-07-13 behaviour, capped at max_moves): pick
    non-conflicting swaps by gain, respecting a running bank & the nation cap."""
    picked: list[TransferMove] = []
    used_out, used_in = set(), set()
    run_bank = bank
    nc = dict(nation_count)
    for m in candidates:
        if len(picked) >= max_moves:
            break
        if m.out_pid in used_out or m.in_pid in used_in:
            continue
        if m.price_delta > run_bank + 1e-9:
            continue
        if nc.get(by_pid[m.in_pid].nation, 0) + 1 > nation_cap:
            continue
        picked.append(m)
        used_out.add(m.out_pid); used_in.add(m.in_pid)
        run_bank -= m.price_delta
        nc[by_pid[m.out_pid].nation] -= 1
        nc[by_pid[m.in_pid].nation] = nc.get(by_pid[m.in_pid].nation, 0) + 1
    return picked


def plan_transfers(current_pids: list[int], projs: list[PlayerProj], budget: float,
                   nation_cap: int, free_transfers: int, bank: float,
                   extra_penalty: float = -3.0, hit_threshold: float = 4.0):
    by_pid = {p.pid: p for p in projs}
    owned = [by_pid[pid] for pid in current_pids if pid in by_pid]
    owned_ids = set(current_pids)
    nation_count: dict[str, int] = {}
    for p in owned:
        nation_count[p.nation] = nation_count.get(p.nation, 0) + 1

    # candidate single swaps, by position. Affordability is judged against the bank
    # PLUS what other sales could free (package-affordable), not the static bank —
    # the packaging step below enforces the exact budget over the chosen set.
    headroom = _max_freeable(owned, projs, owned_ids)
    candidates: list[TransferMove] = []
    for o in owned:
        for c in projs:
            if c.pid in owned_ids or c.position != o.position or c.minutes_prob < 0.4:
                continue
            price_delta = c.price - o.price
            if price_delta > bank + headroom + 1e-9:
                continue
            # nation cap after swap
            nc = dict(nation_count)
            nc[o.nation] = nc.get(o.nation, 0) - 1
            nc[c.nation] = nc.get(c.nation, 0) + 1
            if nc[c.nation] > nation_cap:
                continue
            gain = c.horizon - o.horizon
            if gain > 0:
                candidates.append(TransferMove(o.pid, c.pid, o.name, c.name, o.position,
                                               round(gain, 2), round(price_delta, 1)))
    candidates.sort(key=lambda m: m.gain, reverse=True)

    # best free package: joint ILP (running-bank-aware); greedy only as solver fallback
    free_moves = _package_ilp(candidates, by_pid, nation_count, nation_cap, bank, free_transfers)
    if free_moves is None:
        free_moves = _package_greedy(candidates, by_pid, nation_count, nation_cap, bank, free_transfers)
    # Paid transfers (beyond the free allowance) each cost extra_penalty, so only take one
    # when it (a) clears the horizon threshold AND (b) actually improves the STARTING XI's
    # next-round EV. Swapping a player who'd sit on the bench for another bench player (e.g.
    # upgrading 5th-choice cover) raises squad horizon on paper but scores ~nothing for the
    # user — it must never justify a -3 hit. Free transfers stay unconstrained (squad health).
    extra_moves: list[TransferMove] = []
    cur_ids = _apply(current_pids, free_moves)
    base_xi = _best_xi_exp([by_pid[p] for p in cur_ids if p in by_pid])
    used_out = {m.out_pid for m in free_moves}
    used_in = {m.in_pid for m in free_moves}
    run_bank = bank - sum(m.price_delta for m in free_moves)
    nc = dict(nation_count)
    for m in free_moves:
        nc[by_pid[m.out_pid].nation] -= 1
        nc[by_pid[m.in_pid].nation] = nc.get(by_pid[m.in_pid].nation, 0) + 1
    for m in candidates:
        if m.gain < hit_threshold:
            continue
        if m.out_pid in used_out or m.in_pid in used_in:
            continue
        if m.price_delta > run_bank + 1e-9:
            continue
        if nc.get(by_pid[m.in_pid].nation, 0) + 1 > nation_cap:
            continue
        trial_ids = _apply(cur_ids, [m])
        trial_xi = _best_xi_exp([by_pid[p] for p in trial_ids if p in by_pid])
        if trial_xi > base_xi + 1e-6:
            extra_moves.append(m)
            cur_ids, base_xi = trial_ids, trial_xi
            used_out.add(m.out_pid); used_in.add(m.in_pid)
            run_bank -= m.price_delta
            nc[by_pid[m.out_pid].nation] -= 1
            nc[by_pid[m.in_pid].nation] = nc.get(by_pid[m.in_pid].nation, 0) + 1
    moves = free_moves + extra_moves
    n_hits = len(extra_moves)
    total_gain = sum(m.gain for m in moves)
    net_gain = total_gain + n_hits * extra_penalty
    return {
        "moves": moves,
        "free_used": len(free_moves),
        "hits": n_hits,
        "hit_cost": round(n_hits * extra_penalty, 1),
        "total_gain": round(total_gain, 2),
        "net_gain": round(net_gain, 2),
        "all_candidates": candidates[:12],
    }


REACH_KEY = {"R32": ("reach_R32", "reach_R16"), "R16": ("reach_R16", "reach_QF"),
             "QF": ("reach_QF", "reach_SF"), "SF": ("reach_SF", "reach_final")}


def qual_booster_ev(squad, advancement: dict, target_round: str) -> float | None:
    """Expected points of the Qualification Booster (+2 per starter whose team
    advances) if played in target_round, given the current XI."""
    if not squad or not advancement or target_round not in REACH_KEY:
        return None
    cur_key, nxt_key = REACH_KEY[target_round]
    by_pid = squad.by_pid()
    ev = 0.0
    for pid in squad.starters:
        adv = advancement.get(by_pid[pid].nation, {})
        cur, nxt = float(adv.get(cur_key, 0.0)), float(adv.get(nxt_key, 0.0))
        if cur > 1e-9:
            ev += 2.0 * min(1.0, nxt / cur)   # P(advance this round | playing it)
    return round(ev, 1)


def chip_advice(stage: str, target_round: str, chips_remaining: list[str], squad=None,
                transfer_plan=None, advancement: dict | None = None,
                optimal_gap: float | None = None, twelfth: dict | None = None) -> list[dict]:
    """Chip recommendations for the round being planned (target_round).

    target_round is the round any action taken now applies to: MD1..MD3 (group),
    then R32/R16/QF/SF/final. Returns a list of {chip, action, reason}.
    """
    out = []
    remaining = set(chips_remaining or [])
    ko = target_round in ("R32", "R16", "QF", "SF", "final")

    if "Wildcard" in remaining:
        n_moves = len(transfer_plan.get("moves", [])) if transfer_plan else 0
        hit_cost = abs(transfer_plan.get("hit_cost", 0)) if transfer_plan else 0
        if target_round == "MD1":
            out.append({"chip": "Wildcard", "action": "HOLD",
                        "reason": "Not usable on Matchday 1. Hold — the natural spot is MD3 (lock in "
                                  "qualified teams) or a knockout round where your squad breaks."})
        elif target_round == "R32":
            out.append({"chip": "Wildcard", "action": "HOLD",
                        "reason": "Not usable in the Round of 32 — and unnecessary: transfers before the "
                                  "R32 are unlimited anyway. Save it for R16 or later."})
        elif n_moves >= 4 or (optimal_gap or 0) >= 15:
            gap_txt = f" The unconstrained optimal squad is {optimal_gap:+.0f} horizon pts away." if optimal_gap else ""
            out.append({"chip": "Wildcard", "action": "CONSIDER",
                        "reason": f"{n_moves} beneficial moves this round (saves {hit_cost} pts in hits).{gap_txt}"})
        else:
            out.append({"chip": "Wildcard", "action": "HOLD",
                        "reason": "Your squad is close to optimal — not enough beneficial moves to burn it. "
                                  "Keep it for a round where several starters are eliminated at once."})

    if "Qualification Booster" in remaining:
        ev = qual_booster_ev(squad, advancement, target_round)
        if not ko:
            out.append({"chip": "Qualification Booster", "action": "HOLD",
                        "reason": "Knockout-only (R32+). Best round: usually the R32, when all 11 starters are "
                                  "still alive and mostly heavy favourites (+2 per starter who advances)."})
        elif ev is not None:
            action = "PLAY" if ev >= 16.0 else "CONSIDER"
            out.append({"chip": "Qualification Booster", "action": action,
                        "reason": f"Played this round it is worth ≈{ev:.0f} pts (+2 per starter whose team advances). "
                                  f"Rule of thumb: play at ≥16 — later rounds have fewer of your players alive."})
        else:
            out.append({"chip": "Qualification Booster", "action": "CONSIDER",
                        "reason": "Use in the KO round where the most of your XI are strong favourites to advance."})

    if "Maximum Captain" in remaining:
        out.append({"chip": "Maximum Captain", "action": "HOLD" if not ko else "CONSIDER",
                    "reason": "Auto-doubles your top scorer, so it beats a normal armband most in a round with "
                              "several explosive fixtures. Best saved for R32/R16 when your premiums face "
                              "soft opposition and captain risk is highest."})
    if "12th Man" in remaining:
        extra = (f" Best candidate outside your 15 right now: {twelfth['name']} "
                 f"(E {twelfth['ev']:.1f} this round) — play it when that number spikes well above ~6."
                 if twelfth else "")
        out.append({"chip": "12th Man", "action": "HOLD",
                    "reason": "Adds one extra scorer for a round (no budget/nation limits). Play it when a clear "
                              "points machine sits outside your 15 — typically a KO round with a standout "
                              f"fixture.{extra}"})
    if "Mystery Booster" in remaining:
        out.append({"chip": "Mystery Booster", "action": "WAIT",
                    "reason": "Effect is revealed at the Round of 32 — re-evaluate the moment it's known."})
    return out
