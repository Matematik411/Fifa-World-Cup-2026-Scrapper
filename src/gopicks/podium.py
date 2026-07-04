"""GoPicks podium simulator (U9) — the ONE deliberately rank-aware module.

Everywhere else the project is pure best-EV by standing instruction. GoPicks is
the explicit exception (user decision 2026-07-02, re-opened 2026-07-04): it is
the only league with real prizes, they pay **top-3 only**, and late in the
tournament a trailing player's P(podium) is maximized by *decorrelating* from
the leaders — accepting a small EV cost for gap variance — IF the deficit and
the number of remaining matches make pure-EV catch-up unlikely.

This module quantifies that trade instead of hand-waving it:

  * Remaining KNOWN matches use the model's real scoreline matrices; remaining
    LATER rounds (teams unknown) are approximated by the tightest known
    matrices (later rounds = stronger, closer opponents).
  * Rivals are simulated as a mix of EV-pickers, favorite-modal "public"
    pickers (the public under-picks draws) and casual scatter pickers; the
    `overlap` parameter q is the share of pure EV-pickers. We don't know the
    real overlap, so every number is reported across a q sensitivity grid.
  * Strategies are per-match RULES (generalize to unknown future rounds):
      best-ev    — the shipped EV-optimal pick, every match
      goal-tilt  — keep the EV outcome, take 2nd-best goal counts (cheap
                   decorrelation of the exact-goal components)
      tilt2-XX   — flip to the best pick of the 2nd-most-likely outcome
                   (usually the 90' draw) on matches where the favorite's
                   win prob < XX%; EV pick elsewhere
      tilt2-all  — flip every match (variance ceiling, not a recommendation)
  * Output: expected final points, P(top-3), P(top-1) and expected rank per
    strategy × q, plus the concrete this-round pick changes each strategy
    implies, and a data-driven verdict.

Approximations (stated, deliberate): unknown leaderboard ranks are linearly
interpolated; only the nearest ~13 rivals are simulated (the far field can't
realistically both catch the user and the podium); final-total ties at the
3rd place split the podium probability (the real tiebreaker is total exact
goal picks, unknowable for rivals).
"""
from __future__ import annotations

import numpy as np

from .optimizer import expected_points_grid, _outcome  # noqa: F401  (shared scoring core)

# ---------------------------------------------------------------------------
# per-match building blocks
# ---------------------------------------------------------------------------


def _grid_and_outcomes(P: np.ndarray, scoring: dict, max_candidate: int = 6):
    """EV grid over candidate picks + per-outcome masks + outcome probs."""
    grid, exact = expected_points_grid(P, scoring, max_candidate)
    C = grid.shape[0]
    I, J = np.indices((C, C))
    sign = np.sign(I - J)  # +1 home, 0 draw, -1 away
    n = P.shape[0]
    ii, jj = np.indices((n, n))
    sa = np.sign(ii - jj)
    p_out = {oc: float(P[sa == oc].sum()) for oc in (-1, 0, 1)}
    return grid, exact, sign, p_out


def candidate_picks(P: np.ndarray, scoring: dict, max_candidate: int = 6) -> dict:
    """The named candidate picks a strategy can choose between for one match."""
    grid, exact, sign, p_out = _grid_and_outcomes(P, scoring, max_candidate)
    out = {}

    def best_in(mask) -> tuple[int, int]:
        g = np.where(mask, grid, -np.inf)
        a, b = divmod(int(np.argmax(g)), g.shape[1])
        return a, b

    ev_pick = divmod(int(np.argmax(grid)), grid.shape[1])
    out["ev"] = ev_pick
    ev_outcome = _outcome(*ev_pick)

    # 2nd-best pick that keeps the EV outcome (goal-count decorrelation)
    m = sign == ev_outcome
    g2 = np.where(m, grid, -np.inf).copy()
    g2[ev_pick] = -np.inf
    if np.isfinite(g2).any():
        out["ev_alt_goals"] = divmod(int(np.argmax(g2)), g2.shape[1])
    else:
        out["ev_alt_goals"] = ev_pick

    # best pick per outcome
    for oc, name in ((1, "H"), (0, "D"), (-1, "A")):
        out[f"best_{name}"] = best_in(sign == oc)

    # 2nd-most-likely outcome's best pick
    order = sorted(p_out, key=p_out.get, reverse=True)
    out["second_outcome"] = best_in(sign == order[1])
    out["p_out"] = p_out
    out["grid"] = grid
    return out


def rival_pick_dist(P: np.ndarray, scoring: dict, overlap: float,
                    max_candidate: int = 6) -> tuple[list[tuple[int, int]], np.ndarray]:
    """A rival's per-match pick distribution.

    overlap q = share of rivals-behavior that exactly mirrors our EV pick.
    The rest: 70% "public" (modal-ish scorelines *within the favorite outcome*
    — the public backs favorites and under-picks draws), 30% scatter
    (∝ the scoreline probability itself).
    """
    cands = candidate_picks(P, scoring, max_candidate)
    grid = cands["grid"]
    C = grid.shape[0]
    n = P.shape[0]
    Pc = P[:C, :C].copy()  # candidate-space scoreline probs

    probs: dict[tuple[int, int], float] = {}

    def add(pick, w):
        probs[pick] = probs.get(pick, 0.0) + w

    add(cands["ev"], overlap)

    # public: favorite-outcome cells, sharpened toward the modal scoreline
    fav_oc = max(cands["p_out"], key=cands["p_out"].get)
    I, J = np.indices((C, C))
    sign = np.sign(I - J)
    pub = np.where(sign == fav_oc, Pc, 0.0) ** 1.5
    if pub.sum() > 0:
        pub = pub / pub.sum() * (1.0 - overlap) * 0.7
        for a in range(C):
            for b in range(C):
                if pub[a, b] > 1e-9:
                    add((a, b), float(pub[a, b]))

    # scatter: any plausible scoreline
    sc = Pc / max(Pc.sum(), 1e-12) * (1.0 - overlap) * 0.3
    for a in range(C):
        for b in range(C):
            if sc[a, b] > 1e-9:
                add((a, b), float(sc[a, b]))

    picks = list(probs)
    p = np.array([probs[k] for k in picks])
    p = p / p.sum()
    _ = n  # (full-P dims only matter for the points table, built elsewhere)
    return picks, p


def _points_table(picks: list[tuple[int, int]], n: int, scoring: dict) -> np.ndarray:
    """points[pick_idx, actual_cell] for every actual (h, a) in the n×n grid."""
    pts_result = float(scoring.get("result", 3))
    pts_h = float(scoring.get("exact_home", 1))
    pts_a = float(scoring.get("exact_away", 1))
    ii, jj = np.indices((n, n))
    sa = np.sign(ii - jj).ravel()
    T = np.zeros((len(picks), n * n))
    for k, (a, b) in enumerate(picks):
        t = pts_result * (np.sign(a - b) == sa).astype(float)
        t += pts_h * (ii.ravel() == a)
        t += pts_a * (jj.ravel() == b)
        T[k] = t
    return T


# ---------------------------------------------------------------------------
# strategies (per-match rules — must generalize to unknown later rounds)
# ---------------------------------------------------------------------------

def _fav_prob(p_out: dict) -> float:
    return max(p_out[1], p_out[-1])


def strategy_pick(name: str, cands: dict) -> tuple[int, int]:
    if name == "best-ev":
        return cands["ev"]
    if name == "goal-tilt":
        return cands["ev_alt_goals"]
    if name.startswith("tilt2-"):
        arg = name.split("-", 1)[1]
        if arg == "all":
            return cands["second_outcome"]
        thr = float(arg) / 100.0
        if _fav_prob(cands["p_out"]) < thr:
            return cands["second_outcome"]
        return cands["ev"]
    raise ValueError(f"unknown strategy {name}")


DEFAULT_STRATEGIES = ["best-ev", "goal-tilt", "tilt2-45", "tilt2-55", "tilt2-65", "tilt2-all"]


# ---------------------------------------------------------------------------
# the simulator
# ---------------------------------------------------------------------------

def _interp_leaderboard(ahead_known: list[float], my_points: float, my_rank: int) -> list[float]:
    """Fill unknown ranks between the last known leader score and the user."""
    n_ahead = my_rank - 1
    known = list(ahead_known)[:n_ahead]
    missing = n_ahead - len(known)
    if missing > 0:
        lo = known[-1] if known else my_points + 10
        fill = np.linspace(lo, my_points, missing + 2)[1:-1]
        known += [float(round(x)) for x in fill]
    return known


def simulate_podium(match_models: list[dict], standing: dict, scoring: dict,
                    n_sims: int = 20000, overlap_grid: tuple = (0.4, 0.55, 0.7),
                    strategies: list[str] | None = None,
                    rivals_behind: tuple = (229, 228, 227, 226, 225),
                    seed: int = 20260704) -> dict:
    """Monte-Carlo the endgame. match_models: [{num, home, away, P, known}, ...]
    (known=False marks later-round proxy matches). standing: {points, rank,
    leaderboard_ahead: [...]}. Returns per-strategy × per-overlap stats."""
    strategies = strategies or list(DEFAULT_STRATEGIES)
    my_pts = float(standing["points"])
    ahead = _interp_leaderboard(standing.get("leaderboard_ahead") or [], my_pts,
                                int(standing.get("rank", len(standing.get("leaderboard_ahead") or [])+1)))
    rival_starts = np.array(list(ahead) + list(rivals_behind), dtype=float)
    n_riv = len(rival_starts)

    # per-match precomputation
    for mm in match_models:
        P = mm["P"]
        mm["cands"] = candidate_picks(P, scoring)
        mm["cells_p"] = (P / P.sum()).ravel()
        mm["n"] = P.shape[0]

    rng = np.random.default_rng(seed)
    # sample actual outcomes ONCE — shared across strategies and overlaps so
    # comparisons are paired (much lower comparison variance)
    actual_cells = [rng.choice(mm["n"] * mm["n"], size=n_sims, p=mm["cells_p"])
                    for mm in match_models]

    out: dict = {"strategies": {}, "overlap_grid": list(overlap_grid),
                 "n_sims": n_sims, "n_rivals": n_riv,
                 "rival_starts": [float(x) for x in rival_starts],
                 "my_points": my_pts}

    # rival totals per overlap (shared across strategies)
    rival_totals_by_q: dict[float, np.ndarray] = {}
    for q in overlap_grid:
        totals = np.tile(rival_starts, (n_sims, 1))  # (sims, rivals)
        for mi, mm in enumerate(match_models):
            picks, pp = rival_pick_dist(mm["P"], scoring, q)
            T = _points_table(picks, mm["n"], scoring)
            pick_idx = rng.choice(len(picks), size=(n_sims, n_riv), p=pp)
            totals += T[pick_idx, actual_cells[mi][:, None]]
        rival_totals_by_q[q] = totals

    for strat in strategies:
        srec = {"per_overlap": {}, "picks": [], "ev_cost_round": 0.0}
        my_add = np.zeros(n_sims)
        for mi, mm in enumerate(match_models):
            pick = strategy_pick(strat, mm["cands"])
            T = _points_table([pick], mm["n"], scoring)
            my_add += T[0, actual_cells[mi]]
            ev_pick = mm["cands"]["ev"]
            g = mm["cands"]["grid"]
            cost = float(g[ev_pick] - g[pick])
            if mm.get("known"):
                srec["picks"].append({
                    "num": mm["num"], "home": mm["home"], "away": mm["away"],
                    "pick": f"{pick[0]}-{pick[1]}", "ev_pick": f"{ev_pick[0]}-{ev_pick[1]}",
                    "flipped": pick != ev_pick, "ev_cost": round(cost, 3),
                    "fav_prob": round(_fav_prob(mm["cands"]["p_out"]), 3),
                })
                srec["ev_cost_round"] += cost
        my_totals = my_pts + my_add

        for q in overlap_grid:
            rt = rival_totals_by_q[q]
            n_above = (rt > my_totals[:, None]).sum(axis=1)
            n_tie = (rt == my_totals[:, None]).sum(axis=1)
            top3 = np.where(n_above >= 3, 0.0, np.where(n_above + n_tie <= 2, 1.0, 0.5))
            top1 = np.where(n_above >= 1, 0.0, np.where(n_tie == 0, 1.0, 0.5))
            srec["per_overlap"][q] = {
                "p_top3": float(top3.mean()),
                "p_top1": float(top1.mean()),
                "exp_rank": float((n_above + 1 + n_tie * 0.5).mean()),
            }
        srec["ev_final"] = float(my_totals.mean())
        srec["sd_final"] = float(my_totals.std())
        srec["ev_cost_round"] = round(srec["ev_cost_round"], 3)
        srec["p_top3_mean"] = float(np.mean([v["p_top3"] for v in srec["per_overlap"].values()]))
        out["strategies"][strat] = srec

    # data-driven verdict: does the best decorrelation rule beat pure EV by
    # enough to be worth the points it burns?
    base = out["strategies"].get("best-ev")
    alts = {k: v for k, v in out["strategies"].items() if k not in ("best-ev", "tilt2-all")}
    best_alt = max(alts, key=lambda k: alts[k]["p_top3_mean"]) if alts else None
    verdict = {"switch": False, "to": None, "reason": ""}
    if base and best_alt:
        b, a = base["p_top3_mean"], alts[best_alt]["p_top3_mean"]
        if a > b * 1.3 and (a - b) > 0.015:
            verdict = {"switch": True, "to": best_alt,
                       "reason": (f"P(top-3) {b:.1%} → {a:.1%} across the rival-overlap grid for "
                                  f"{out['strategies'][best_alt]['ev_cost_round']:.1f} EV pts this round")}
        else:
            verdict["reason"] = (f"best alternative ({best_alt}) lifts P(top-3) only "
                                 f"{b:.1%} → {a:.1%} — not worth decorrelating yet")
    out["verdict"] = verdict
    return out


def podium_analysis(forecast, fixtures: dict, results: dict, scoring: dict,
                    gp_state: dict, cfg=None, log=print) -> dict | None:
    """Pipeline entry: build match models for the remaining tournament and run
    the simulator. Returns None (and logs why) when prerequisites are missing."""
    pts = gp_state.get("points_official")
    ahead = gp_state.get("leaderboard_ahead") or []
    rank = gp_state.get("rank")
    if pts is None or not ahead or not rank:
        log("  [podium] skipped: need gopicks.points_official + rank + leaderboard_ahead in state.json")
        return None

    remaining = [m for m in fixtures["matches"] if m["num"] not in results]
    known = [m for m in remaining if m["num"] in forecast.match_forecasts]
    n_later = len(remaining) - len(known)
    if not remaining or len(remaining) > 24:
        log(f"  [podium] skipped: {len(remaining)} matches remain — rank-aware play is an endgame tool (≤24).")
        return None

    models = []
    for m in known:
        mf = forecast.match_forecasts[m["num"]]
        models.append({"num": mf.num, "home": mf.home, "away": mf.away, "P": mf.P, "known": True})

    # later rounds: teams unknown → proxy with the tightest known matrices
    if n_later and models:
        def tightness(md):
            P = md["P"]
            n = P.shape[0]
            ii, jj = np.indices((n, n))
            sa = np.sign(ii - jj)
            ph, pa = float(P[sa == 1].sum()), float(P[sa == -1].sum())
            return abs(ph - pa)
        pool = sorted(models, key=tightness)[:max(3, len(models) // 2)]
        for i in range(n_later):
            src = pool[i % len(pool)]
            models.append({"num": -(i + 1), "home": "later", "away": "later",
                           "P": src["P"], "known": False})

    pod_cfg = (cfg.get("gopicks.podium", {}) if cfg else {}) or {}
    res = simulate_podium(
        models,
        standing={"points": pts, "rank": rank, "leaderboard_ahead": ahead},
        scoring=scoring,
        n_sims=int(pod_cfg.get("n_sims", 20000)),
        overlap_grid=tuple(pod_cfg.get("overlap_grid", (0.4, 0.55, 0.7))),
        rivals_behind=tuple(pod_cfg.get("rivals_behind", (229, 228, 227, 226, 225))),
        seed=int(pod_cfg.get("seed", 20260704)),
    )
    res["n_known"] = len(known)
    res["n_later"] = n_later
    v = res["verdict"]
    log(f"  [podium] P(top-3): best-ev {res['strategies']['best-ev']['p_top3_mean']:.1%}"
        + (f" → SWITCH to {v['to']} ({v['reason']})" if v["switch"]
           else f" — stay best-EV ({v['reason']})"))
    return res
