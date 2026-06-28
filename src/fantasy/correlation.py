"""U7 — player-level correlation for the captain ceiling and defensive-stack value.

Per-player fantasy EVs (src/fantasy/projections) are independent. That is CORRECT for
EV-optimal squad SELECTION — the ILP maximizes a sum of means, and correlation never
changes a sum of means (linearity of expectation). It is wrong for two distributional
questions the optimizer cannot answer:

  * the **captain ceiling** — the Maximum Captain chip doubles the round's single highest
    XI scorer, so its value is E[max_i pts_i] - E[pts_captain], which needs the JOINT
    distribution of the XI's scores; and
  * a defensive **stack** — a team's clean sheet is ONE event shared by its GK + defenders,
    so their points move together (raising the lineup's ceiling/variance, though not its
    mean).

This reuses the shared Dixon-Coles per-match scoreline matrices (one model for everything)
and Monte-Carlo samples correlated player points for the NEXT round: teammates share their
match's sampled scoreline, opponents are anti-correlated. Each player's MEAN is pinned to
his `exp_next`, so the squad's EV is untouched — only the spread is modeled. Used ONLY for
the captain / Max-Captain decision and ceiling reporting, never to bias squad selection
toward variance (the project's pure-best-EV rule).
"""
from __future__ import annotations

import numpy as np

from .projections import ASSIST_PTS, ASSIST_RATE, CS_PTS, GOAL_PTS, _expected_extra_conceded


def _sample_match_goals(P: np.ndarray, n: int, rng) -> tuple[np.ndarray, np.ndarray]:
    """n (home_goals, away_goals) draws from a scoreline matrix P."""
    cum = np.cumsum(P.ravel())
    cum[-1] = 1.0
    W = P.shape[1]
    flat = np.searchsorted(cum, rng.random(n))
    return flat // W, flat % W


def _player_samples(p, P, is_home, gh, ga, rng) -> np.ndarray:
    """[N] sampled next-match fantasy points for player p given his match's sampled
    scorelines. The mean is pinned to p.exp_next (the deterministic, scoreline-independent
    terms — MID chances/tackles, FWD shots, GK saves, cards, scout bonus — are folded into
    a constant offset); the spread comes from the goals / clean-sheet draws."""
    N = len(gh)
    pos = p.position
    G_for = (gh if is_home else ga).astype(int)
    G_against = (ga if is_home else gh).astype(int)
    mins = p.next_minutes or p.minutes_prob
    appear_p = min(0.98, mins + 0.06)
    p60 = mins * 0.92
    appeared = (rng.random(N) < appear_p).astype(float)
    # played-60 nested inside appearance, so played60 ⟹ appeared (and E[played60]=p60)
    played60 = appeared * (rng.random(N) < (p60 / max(appear_p, 1e-9))).astype(float)

    g_prob = 0.0 if pos == "GK" else (min(p.goal_share, 0.06) if pos == "DEF" else p.goal_share)
    g_prob = float(min(max(g_prob, 0.0), 1.0))
    a_prob = float(min(max(ASSIST_RATE * p.assist_share, 0.0), 1.0))
    goals = rng.binomial(G_for, g_prob).astype(float) if g_prob > 0 else np.zeros(N)
    assists = rng.binomial(G_for, a_prob).astype(float) if a_prob > 0 else np.zeros(N)
    cs_pts = CS_PTS.get(pos, 0)
    cs = cs_pts * (G_against == 0).astype(float) * played60
    conceded = -np.maximum(G_against - 1, 0) * played60 if pos in ("GK", "DEF") else 0.0
    var = appeared + goals * GOAL_PTS[pos] + assists * ASSIST_PTS + cs + conceded

    home_marg, away_marg = P.sum(axis=1), P.sum(axis=0)
    E_for = float((np.arange(len(home_marg)) * (home_marg if is_home else away_marg)).sum())
    cs_prob = float(P[:, 0].sum() if is_home else P[0, :].sum())
    opp_marg = away_marg if is_home else home_marg
    E_conc = -_expected_extra_conceded(opp_marg) * p60 if pos in ("GK", "DEF") else 0.0
    E_var = (appear_p + E_for * g_prob * GOAL_PTS[pos] + E_for * a_prob * ASSIST_PTS
             + cs_pts * cs_prob * p60 + E_conc)
    base = float(p.exp_next) - E_var
    return base + var


def captain_ceiling(squad, forecast, cfg=None, n_sims: int = 6000) -> dict | None:
    """Joint next-round simulation of the XI → lineup ceiling + the Maximum-Captain value.

    Returns None when the next-round matches aren't all known yet (e.g. KO opponents TBD),
    so callers simply omit the captain-ceiling view for that round.
    """
    by_pid = squad.by_pid()
    starters = [by_pid[pid] for pid in squad.starters]
    playing = [p for p in starters if p.next_num]
    if not playing or any(p.next_num not in forecast.match_forecasts for p in playing):
        return None
    seed = (int(cfg.get("model.rng_seed", 20260605)) if cfg else 20260605) + 7
    rng = np.random.default_rng(seed)

    match_draw = {num: _sample_match_goals(forecast.match_forecasts[num].P, n_sims, rng)
                  for num in {p.next_num for p in playing}}
    samp = {}
    for p in starters:
        if not p.next_num:
            samp[p.pid] = np.full(n_sims, float(p.exp_next))
            continue
        gh, ga = match_draw[p.next_num]
        samp[p.pid] = _player_samples(p, forecast.match_forecasts[p.next_num].P,
                                      p.next_is_home, gh, ga, rng)

    M = np.vstack([samp[p.pid] for p in starters])      # [11, N]
    xi_total = M.sum(axis=0)
    top_scorer = M.max(axis=0)                          # Max Captain doubles this one
    cap = by_pid[squad.captain]
    cap_s = samp[cap.pid]

    cand_rows = []
    for p in sorted((q for q in starters if q.position in ("MID", "FWD")),
                    key=lambda x: -float(samp[x.pid].mean())):
        s = samp[p.pid]
        cand_rows.append({"name": p.name, "nation": p.nation, "mean": round(float(s.mean()), 2),
                          "p90": round(float(np.percentile(s, 90)), 1),
                          "is_captain": p.pid == squad.captain})

    # defensive stacks: ≥2 GK/DEF from one team in the XI share that team's clean sheet
    teamdef: dict[str, list[str]] = {}
    for p in starters:
        if p.position in ("GK", "DEF"):
            teamdef.setdefault(p.nation, []).append(p.name)
    stacks = []
    for nat, names in teamdef.items():
        if len(names) >= 2:
            pl = next((p for p in playing if p.nation == nat), None)
            cs = None
            if pl is not None:
                P = forecast.match_forecasts[pl.next_num].P
                cs = round(float(P[:, 0].sum() if pl.next_is_home else P[0, :].sum()), 2)
            stacks.append({"nation": nat, "players": names, "n": len(names), "cs_prob": cs})
    stacks.sort(key=lambda s: (-s["n"], -(s["cs_prob"] or 0)))

    return {
        "xi_mean": round(float(xi_total.mean()), 1),
        "xi_p90": round(float(np.percentile(xi_total, 90)), 1),
        "xi_p10": round(float(np.percentile(xi_total, 10)), 1),
        "captain": {"name": cap.name, "nation": cap.nation,
                    "doubled_mean": round(float(2 * cap_s.mean()), 1),
                    "doubled_p90": round(float(np.percentile(2 * cap_s, 90)), 1)},
        # EV of the Maximum Captain chip over a fixed armband = E[top scorer] - E[captain]
        "max_cap_gain": round(float(top_scorer.mean() - cap_s.mean()), 1),
        "candidates": cand_rows[:6],
        "stacks": stacks,
        "n_sims": n_sims,
    }
