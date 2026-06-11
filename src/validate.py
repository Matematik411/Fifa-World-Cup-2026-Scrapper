"""Post-run validation. Returns a list of human-readable problems (empty = OK).

Fail loudly rather than emitting a silently-wrong report.
"""
from __future__ import annotations


def validate_run(result: dict) -> list[str]:
    problems: list[str] = []

    # --- predictions ---
    preds = result.get("predictions", [])
    if not preds:
        if result.get("stage") in ("R32", "R16", "QF", "SF", "final"):
            problems.append("No upcoming predictions — fill the resolved KO teams into "
                            "fixtures.json home/away (RUNBOOK §2) so the next ties unlock.")
        else:
            problems.append("No predictions generated.")
    for r in preds:
        s = r["p_home"] + r["p_draw"] + r["p_away"]
        if abs(s - 1.0) > 0.02:
            problems.append(f"Match {r['num']} outcome probs sum to {s:.3f} (expected ~1).")
        for k in ("pred_home", "pred_away"):
            if not (0 <= r[k] <= 8):
                problems.append(f"Match {r['num']} {k}={r[k]} out of range.")

    # every known group match should have exactly one prediction
    nums = [r["num"] for r in preds]
    if len(nums) != len(set(nums)):
        problems.append("Duplicate predictions for some matches.")

    # --- fantasy squad ---
    fan = result.get("fantasy")
    if fan:
        squad = fan["squad"]
        allp = squad["all"]
        if len(allp) != 15:
            problems.append(f"Squad has {len(allp)} players (expected 15).")
        from collections import Counter
        pos = Counter(p["position"] for p in allp)
        for need_pos, need_n in (("GK", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)):
            if pos.get(need_pos, 0) != need_n:
                problems.append(f"Squad has {pos.get(need_pos, 0)} {need_pos} (expected {need_n}).")
        cost = sum(p["price"] for p in allp)
        if cost > squad["budget"] + 1e-6:
            problems.append(f"Squad cost {cost:.1f} exceeds budget {squad['budget']}.")
        cap = fan.get("nation_cap", 3)
        from collections import Counter as C2
        natc = C2(p["nation"] for p in allp)
        for nat, c in natc.items():
            if c > cap:
                problems.append(f"Squad has {c} players from {nat} (cap {cap}).")
        if len(squad["starters"]) != 11:
            problems.append(f"Starting XI has {len(squad['starters'])} players (expected 11).")
        if any(p.get("price") is None or not p.get("position") for p in allp):
            problems.append("A squad player has a missing price or position.")
        cap_pid = squad["captain"]["pid"]
        if cap_pid not in [p["pid"] for p in squad["starters"]]:
            problems.append("Captain is not in the starting XI.")

    return problems
