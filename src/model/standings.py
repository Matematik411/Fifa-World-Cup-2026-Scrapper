"""Lightweight group standings + dead-rubber detection from entered results.

Sim-independent (uses only `results`), so it can feed BOTH the forecast (stakes →
goal intensity) and the player projections (rotation → per-match minutes) without
an ordering cycle with the Monte-Carlo bracket.

Deliberately LIGHT (UPGRADES.md U3, decided 2026-06-24): only the UNAMBIGUOUS
end-of-group cases fire, so it can never misfire on a borderline group —
  * clinched   = a team that has already secured top-2 (≥6 pts after 2 games), and
  * eliminated = a team that cannot realistically reach the best-thirds cut
                 (0 pts after 2 games),
both evaluated ONLY for a team's third (last) group game. The manual dead-rubber
handling remains the fallback for the in-between cases.
"""
from __future__ import annotations

from .teams import normalize_team


def group_table(fixtures: dict, results: dict) -> dict[str, dict[str, dict]]:
    """letter -> {team: {'pts','gd','gf','played'}} from entered group results."""
    table = {g: {normalize_team(t): {"pts": 0, "gd": 0, "gf": 0, "played": 0} for t in ts}
             for g, ts in fixtures["groups"].items()}
    for m in fixtures["matches"]:
        if m["round"] != "group" or m["num"] not in results:
            continue
        g = m.get("group")
        h, a = normalize_team(m.get("home", "")), normalize_team(m.get("away", ""))
        if g not in table or h not in table[g] or a not in table[g]:
            continue
        gh, ga = results[m["num"]]
        H, A = table[g][h], table[g][a]
        H["gf"] += gh; A["gf"] += ga
        H["gd"] += gh - ga; A["gd"] += ga - gh
        H["played"] += 1; A["played"] += 1
        if gh > ga:
            H["pts"] += 3
        elif ga > gh:
            A["pts"] += 3
        else:
            H["pts"] += 1; A["pts"] += 1
    return table


def _team_state(row: dict) -> str:
    """clinched / eliminated / live for a team going into its LAST group game.
    Only the unambiguous cases (light); everything else is 'live'."""
    if row["played"] < 2:
        return "live"                      # not yet at the final group game
    if row["pts"] >= 6:
        return "clinched"                  # two wins -> top-2 in virtually every 4-team group
    if row["pts"] == 0:
        return "eliminated"                # two losses -> can't realistically make the thirds cut
    return "live"


def dead_rubber_flags(fixtures: dict, results: dict) -> dict[int, dict]:
    """num -> {'home_state','away_state','both_settled'} for each UNPLAYED group
    match that is a team's third group game with a settled team. Empty when no MD3
    stakes are resolvable yet (the common pre-MD3 case → zero downstream effect)."""
    table = group_table(fixtures, results)
    out: dict[int, dict] = {}
    for m in fixtures["matches"]:
        if m["round"] != "group" or m["num"] in results:
            continue
        g = m.get("group")
        h, a = normalize_team(m.get("home", "")), normalize_team(m.get("away", ""))
        if g not in table or h not in table[g] or a not in table[g]:
            continue
        hs, as_ = _team_state(table[g][h]), _team_state(table[g][a])
        if hs == "live" and as_ == "live":
            continue
        out[m["num"]] = {"home_state": hs, "away_state": as_,
                         "both_settled": hs != "live" and as_ != "live"}
    return out
