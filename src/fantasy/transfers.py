"""Transfer planning and chip-timing advice for the upcoming round.

First run (no owned squad yet) → recommend the optimal initial 15.
Later runs → given the real owned squad + free transfers, recommend the best
in/out moves (taking a points hit only when the multi-match gain clears it),
plus when to play each chip.
"""
from __future__ import annotations

from dataclasses import dataclass

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


def plan_transfers(current_pids: list[int], projs: list[PlayerProj], budget: float,
                   nation_cap: int, free_transfers: int, bank: float,
                   extra_penalty: float = -3.0, hit_threshold: float = 4.0):
    by_pid = {p.pid: p for p in projs}
    owned = [by_pid[pid] for pid in current_pids if pid in by_pid]
    owned_ids = set(current_pids)
    nation_count: dict[str, int] = {}
    for p in owned:
        nation_count[p.nation] = nation_count.get(p.nation, 0) + 1

    # candidate single swaps, by position
    candidates: list[TransferMove] = []
    for o in owned:
        for c in projs:
            if c.pid in owned_ids or c.position != o.position or c.minutes_prob < 0.4:
                continue
            price_delta = c.price - o.price
            if price_delta > bank + 1e-9:
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

    # greedily pick non-conflicting swaps (no player in/out twice), respecting running bank & nation cap
    picked: list[TransferMove] = []
    used_out, used_in = set(), set()
    run_bank = bank
    nc = dict(nation_count)
    for m in candidates:
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

    free_moves = picked[:free_transfers]
    extra_moves = [m for m in picked[free_transfers:] if m.gain >= hit_threshold]
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


def chip_advice(stage: str, chips_remaining: list[str], squad=None, transfer_plan=None) -> list[dict]:
    """Stage-aware chip recommendations. Returns list of {chip, action, reason}."""
    out = []
    remaining = set(chips_remaining or [])
    is_group = stage in ("pre", "MD1", "MD2", "MD3", "group")

    if "Wildcard" in remaining:
        if is_group and stage in ("pre", "MD1"):
            out.append({"chip": "Wildcard", "action": "HOLD",
                        "reason": "Cannot be used on Matchday 1 or the Round of 32. Save it for a congested "
                                  "transfer round (e.g. when several starters are eliminated at once after the groups)."})
        elif transfer_plan and len(transfer_plan.get("moves", [])) >= 4:
            out.append({"chip": "Wildcard", "action": "CONSIDER",
                        "reason": f"{len(transfer_plan['moves'])} beneficial moves available this round — a Wildcard "
                                  f"makes them all free (saves the {abs(transfer_plan.get('hit_cost',0))}-pt hits)."})
        else:
            out.append({"chip": "Wildcard", "action": "HOLD",
                        "reason": "Not enough beneficial moves to justify it yet; keep it for a bigger reshuffle."})

    if "Qualification Booster" in remaining:
        if stage in ("pre", "MD1", "MD2", "MD3", "group"):
            out.append({"chip": "Qualification Booster", "action": "HOLD",
                        "reason": "Knockout-only (R32+). Plan to use it in a round where most of your XI are heavy "
                                  "favourites to progress."})
        else:
            out.append({"chip": "Qualification Booster", "action": "CONSIDER",
                        "reason": "Use when your XI is stacked with strong favourites to win their tie (+2 each who progress)."})

    if "Maximum Captain" in remaining:
        out.append({"chip": "Maximum Captain", "action": "HOLD",
                    "reason": "Save for a round where your premiums have great fixtures; it auto-doubles your top scorer "
                              "so it shines when several of your players could explode."})
    if "12th Man" in remaining:
        out.append({"chip": "12th Man", "action": "HOLD",
                    "reason": "Best in a round where you can name a strong 12th attacker with a soft fixture; hold until then."})
    if "Mystery Booster" in remaining:
        out.append({"chip": "Mystery Booster", "action": "WAIT",
                    "reason": "Effect is revealed at the Round of 32 — re-evaluate then."})
    return out
