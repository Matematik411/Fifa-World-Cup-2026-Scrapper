"""Full-run orchestration: fetch -> model -> optimize -> persist -> render.

Idempotent and timestamped. Reconciles state.json (the user's REAL team) at the
start, regenerates everything, writes a timestamped processed run, updates the
`latest` pointer, and produces a changelog diff vs the prior run.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import io_utils
from .config import MANUAL, OUTPUT, ROOT, ensure_dirs, load_config
from .fantasy import optimizer as fopt
from .fantasy import transfers as ftr
from .fantasy.projections import build_projections
from .model.bracket import BracketSimulator
from .model.ensemble import build_strengths
from .model.forecast import Forecast
from .gopicks import optimizer as gopt
from .nostradamus import optimizer as nopt
from .sources.loader import load_bundle
from .timeutil import countdown_str, fmt_cet, fmt_local, kickoff_datetimes, now_cet

ALL_CHIPS = ["Wildcard", "12th Man", "Maximum Captain", "Qualification Booster", "Mystery Booster"]

KO_ROUNDS = ("R32", "R16", "QF", "SF", "final")
STAGE_SEQ = ["pre", "MD1", "MD2", "MD3", "R32", "R16", "QF", "SF", "final"]
STAGE_LABEL = {
    "pre": "Pre-tournament", "MD1": "Group stage — Matchday 1", "MD2": "Group stage — Matchday 2",
    "MD3": "Group stage — Matchday 3", "R32": "Round of 32", "R16": "Round of 16",
    "QF": "Quarter-finals", "SF": "Semi-finals", "final": "Final round",
}


def _group_matchdays(fixtures: dict) -> dict[int, int]:
    """Group match num -> matchday 1/2/3 (chronological thirds of the 72 games)."""
    g = sorted((m for m in fixtures["matches"] if m["round"] == "group"),
               key=lambda m: (m.get("date") or "", m["num"]))
    chunk = max(1, (len(g) + 2) // 3)
    return {m["num"]: min(3, 1 + i // chunk) for i, m in enumerate(g)}


def _round_of(m: dict, md_of: dict[int, int]) -> str:
    if m["round"] == "group":
        return f"MD{md_of[m['num']]}"
    return "final" if m["round"] == "third-place" else m["round"]


def _stage(cfg, run_date: str, fixtures: dict, results: dict) -> tuple[str, str, str]:
    """Detect (stage, target_round, label) from fixtures + entered results + date.

    stage         = the round whose matches are up next ("pre" before first kickoff).
    target_round  = the round any transfers made now apply to: the current round
                    while it hasn't locked yet, otherwise the next one (transfers
                    made during a live round take effect from the next round).
    """
    start = cfg.get("tournament.start_date", "2026-06-11")
    if run_date < start or (not results and run_date <= start):
        return "pre", "MD1", f"Pre-tournament — squad & Matchday-1 predictions lock at first kickoff ({start})"
    md_of = _group_matchdays(fixtures)
    unplayed = [m for m in fixtures["matches"] if m["num"] not in results]
    if not unplayed:
        return "final", "final", "Tournament complete"
    nxt = min(unplayed, key=lambda m: (m.get("date") or "9999-99-99", m["num"]))
    stage = _round_of(nxt, md_of)
    in_round = [m for m in fixtures["matches"] if _round_of(m, md_of) == stage]
    locked = any(m["num"] in results for m in in_round) or \
        any((m.get("date") or "9999") < run_date for m in in_round)
    if locked and stage != "final":
        target = STAGE_SEQ[STAGE_SEQ.index(stage) + 1]
    else:
        target = stage
    n_in = sum(1 for m in fixtures["matches"] if m["round"] == "group" and m["num"] in results)
    suffix = f" ({n_in}/72 group results in)" if stage.startswith("MD") else ""
    return stage, target, f"{STAGE_LABEL[stage]}{suffix} — transfers apply to {STAGE_LABEL[target]}"


def _load_state() -> dict:
    path = ROOT / "state.json"
    if path.exists():
        return io_utils.load_json(path)
    return {}


def _save_state(state: dict) -> None:
    io_utils.save_json(ROOT / "state.json", state)


def _load_results() -> tuple[dict, dict]:
    """data/manual/results.json -> ({match_num: (home, away)} 90-minute results,
    {match_num: team} KO advancers for ties that needed ET/pens)."""
    path = MANUAL / "results.json"
    if not path.exists():
        return {}, {}
    raw = io_utils.load_json(path)
    rows = raw.get("results", raw) if isinstance(raw, dict) else {}
    out = {}
    for k, v in rows.items():
        try:
            out[int(k)] = (int(v[0]), int(v[1]))
        except (ValueError, TypeError, IndexError):
            continue
    ko_advancers = {}
    if isinstance(raw, dict):
        for k, v in (raw.get("ko_advancers") or {}).items():
            try:
                ko_advancers[int(k)] = str(v)
            except (ValueError, TypeError):
                continue
    return out, ko_advancers


def _score_predictions(results: dict, pred_records: list, state: dict, scoring: dict) -> dict:
    """Score our predictions-of-record against actual results (idempotent recompute).

    Uses what the user actually entered (state.predictions_entered) when recorded,
    else the model's recommended scoreline for that match.
    """
    entered = state.get("predictions_entered") or {}
    ko = {"R32", "R16", "QF", "SF", "final", "third-place"}
    total, rows = 0, []
    for r in pred_records:
        num = r["num"]
        if num in results:
            ah, aa = results[num]
            es = entered.get(str(num))
            if es and "-" in str(es):
                ph, pa = (int(x) for x in str(es).split("-"))
            else:
                ph, pa = r["pred_home"], r["pred_away"]
            pts = nopt.score_prediction(ph, pa, ah, aa, r["round"] in ko, scoring)
            r["played"] = True
            r["actual"] = f"{ah}-{aa}"
            r["entered_pred"] = f"{ph}-{pa}"
            r["points"] = pts
            total += pts
            rows.append({"num": num, "home": r["home"], "away": r["away"],
                         "pred": f"{ph}-{pa}", "actual": f"{ah}-{aa}", "points": pts})
        else:
            r["played"] = False
    return {"total": total, "rows": rows, "n": len(rows)}


def _score_gopicks(results: dict, pred_records: list, state: dict, scoring: dict) -> dict:
    """Score our GoPicks predictions-of-record (idempotent recompute).

    Same assume-followed default as Nostradamus, with one extra state: the user
    joined the league late, so any played match dated before state.gopicks.joined
    (or explicitly entered as "missed") scores 0 with no pick on record.
    """
    gp_state = state.get("gopicks") or {}
    entered = gp_state.get("predictions_entered") or {}
    joined = gp_state.get("joined") or ""
    total, exact_total, missed, rows = 0, 0, 0, []
    for r in pred_records:
        num = r["num"]
        if num not in results or "gp_home" not in r:
            continue
        ah, aa = results[num]
        es = entered.get(str(num))
        if es == "missed" or (es is None and joined and (r.get("date") or "") < joined):
            r["gp_missed"] = True
            r["gp_points"] = 0
            missed += 1
            rows.append({"num": num, "home": r["home"], "away": r["away"],
                         "pred": "—", "actual": f"{ah}-{aa}", "points": 0,
                         "exact": 0, "missed": True})
            continue
        if es and "-" in str(es):
            ph, pa = (int(x) for x in str(es).split("-"))
        else:
            ph, pa = r["gp_home"], r["gp_away"]
        pts, n_exact = gopt.score_prediction(ph, pa, ah, aa, scoring)
        r["gp_points"] = pts
        r["gp_entered_pred"] = f"{ph}-{pa}"
        total += pts
        exact_total += n_exact
        rows.append({"num": num, "home": r["home"], "away": r["away"],
                     "pred": f"{ph}-{pa}", "actual": f"{ah}-{aa}", "points": pts,
                     "exact": n_exact, "missed": False})
    return {"total": total, "exact": exact_total, "rows": rows, "n": len(rows), "missed": missed}


def run_pipeline(run_date: str | None = None, fetch: bool = True, sim: bool = True,
                 render: bool = True, log=print) -> dict:
    ensure_dirs()
    cfg = load_config()
    run_date = run_date or io_utils.today_str()
    gen_cet = now_cet()
    log(f"=== Run {run_date} | {gen_cet.strftime('%Y-%m-%d %H:%M %Z')} ===")

    state = _load_state()

    bundle = load_bundle(cfg, run_date, fetch=fetch, log=log)
    for w in bundle.warnings:
        log(f"  [warn] {w}")

    results, ko_advancers = _load_results()
    if results:
        log(f"Ingesting {len(results)} actual result(s) — standings/advancement will condition on them.")
    stale = [m["num"] for m in bundle.fixtures["matches"]
             if (m.get("date") or "9999") < run_date and m["num"] not in results]
    if stale:
        w = (f"{len(stale)} match(es) dated before {run_date} have no entry in data/manual/results.json "
             f"(nums {stale[:10]}{'...' if len(stale) > 10 else ''}) — refresh it, or stage detection/scoring will lag.")
        bundle.warnings.append(w)
        log(f"  [warn] {w}")

    stage, target_round, stage_label = _stage(cfg, run_date, bundle.fixtures, results)
    log(f"Stage: {stage_label}")
    _reconcile_owned(state, target_round, log)

    # ---- shared forecast model ----
    log("Building ensemble strengths...")
    strengths = build_strengths(bundle.teams, bundle.ratings_odds, cfg)
    for n in strengths.notes:
        log(f"  {n}")
    log("Building per-match Dixon-Coles forecasts...")
    forecast = Forecast(bundle.teams, strengths, cfg, bundle.ratings_odds, bundle.fixtures)

    # ---- Monte-Carlo bracket ----
    if sim:
        import os
        n_sims = int(os.environ.get("WC2026_SIM_ITERS") or cfg.get("model.sim_iterations", 100000))
        seed = int(cfg.get("model.rng_seed", 20260605))
        log(f"Simulating bracket: {n_sims:,} iterations...")
        simr = BracketSimulator(forecast, bundle.fixtures, cfg, played=results,
                                ko_advancers=ko_advancers).run(n_sims, seed)
    else:
        prev = io_utils.latest_run_date()
        simr = io_utils.load_json(io_utils.processed_dir(prev) / "advancement.json") if prev else {"advancement": {}, "group_standings": {}}
        log("Skipping simulation (reusing cached advancement).")
    advancement = simr["advancement"]

    # ---- Nostradamus ----
    log("Optimizing Nostradamus scorelines...")
    scoring = cfg.get("nostradamus", {})
    preds = nopt.optimize_all(forecast, scoring)
    pred_records = [_decorate_prediction(p, forecast) for p in preds]
    scoring_summary = _score_predictions(results, pred_records, state, scoring)
    if scoring_summary["n"]:
        log(f"Scored {scoring_summary['n']} played match(es): {scoring_summary['total']} Nostradamus points.")

    # ---- GoPicks (same matches, different scoring -> its own EV optimum) ----
    log("Optimizing GoPicks scorelines...")
    gp_scoring = cfg.get("gopicks", {})
    gp_by_num = {p.num: p for p in gopt.optimize_all(forecast, gp_scoring)}
    for r in pred_records:
        g = gp_by_num.get(r["num"])
        if g:
            r.update({"gp_home": g.pred_home, "gp_away": g.pred_away,
                      "gp_ev": round(g.ev, 4), "gp_exact_ev": round(g.exact_ev, 4),
                      "gp_runner_home": g.runner_home, "gp_runner_away": g.runner_away,
                      "gp_runner_ev": round(g.runner_ev, 4),
                      "gp_confidence": g.confidence, "gp_rationale": g.rationale})
    gp_summary = _score_gopicks(results, pred_records, state, gp_scoring)
    if gp_summary["n"]:
        log(f"Scored {gp_summary['n']} played match(es): {gp_summary['total']} GoPicks points "
            f"({gp_summary['exact']} exact goal picks, {gp_summary['missed']} missed match(es)).")
    # one combined scored-results table: attach the GoPicks columns to the Nostradamus rows
    gp_rows = {row["num"]: row for row in gp_summary["rows"]}
    for row in scoring_summary["rows"]:
        g = gp_rows.get(row["num"])
        if g:
            row.update({"gp_pred": g["pred"], "gp_points": g["points"], "gp_missed": g["missed"]})

    # ---- Fantasy ----
    fantasy_out = None
    if bundle.players and bundle.squads_map:
        log("Projecting fantasy points + optimizing squad...")
        fantasy_out = _run_fantasy(cfg, bundle, forecast, advancement, stage, target_round,
                                   state, results, log)
    else:
        log("  [warn] Skipping fantasy (no player feed).")

    # ---- assemble result ----
    result = _assemble(cfg, run_date, gen_cet, stage, stage_label, bundle, strengths,
                       forecast, simr, pred_records, fantasy_out, scoring_summary, gp_summary)
    result["target_round"] = target_round

    # cumulative Nostradamus/GoPicks points = idempotent recompute from results;
    # fantasy from state (user-entered)
    cum = state.setdefault("cumulative", {})
    cum["nostradamus_points"] = scoring_summary["total"]
    cum["gopicks_points"] = gp_summary["total"]
    cum["gopicks_exact_goals"] = gp_summary["exact"]
    result["performance"] = {
        "nostradamus_points": scoring_summary["total"],
        "gopicks_points": gp_summary["total"],
        "gopicks_exact": gp_summary["exact"],
        "gopicks_missed": gp_summary["missed"],
        "fantasy_points": (state.get("cumulative") or {}).get("fantasy_points", 0),
        "predictions_scored": scoring_summary["n"],
        "owned_set": bool((state.get("owned") or {}).get("player_ids")),
    }

    # ---- persist ----
    _persist(run_date, result, forecast, simr, pred_records, fantasy_out, log)

    # ---- changelog vs previous ----
    result["changelog"] = _changelog(run_date, result, log)

    # ---- reconcile + save state ----
    _update_state(state, run_date, result, fantasy_out)

    if render:
        from .report.render import render_all
        log("Rendering HTML reports...")
        render_all(result, OUTPUT, log=log)
        log(f"Reports written to {OUTPUT}/  (open index.html)")

    io_utils.update_latest_pointer(run_date)
    log("=== Done ===")
    return result


def _decorate_prediction(p, forecast) -> dict:
    mf = forecast.match_forecasts[p.num]
    utc, cet = kickoff_datetimes(mf.date, mf.kickoff_local, mf.tz)
    rec = p.to_record()
    rec.update({
        "venue": mf.venue, "city": mf.city, "date": mf.date,
        "kickoff_local": fmt_local(mf.date, mf.kickoff_local, mf.tz),
        "deadline_cet": fmt_cet(cet), "group": mf.group,
        "_sort": (mf.date or "", mf.kickoff_local or ""),
    })
    return rec


def _run_fantasy(cfg, bundle, forecast, advancement, stage, target_round, state, results, log) -> dict:
    projs = build_projections(bundle.players, bundle.squads_map, forecast, advancement,
                              bundle.squads_research, bundle.fixtures, cfg,
                              player_stats=bundle.player_stats, lineups=bundle.lineups,
                              played=set(results))
    budget = _budget(cfg, target_round)
    nation_cap = _nation_cap(cfg, target_round)
    owned = (state.get("owned") or {}).get("player_ids") or []
    by_pid = {p.pid: p for p in projs}

    # The unconstrained optimum — directly buildable pre-lock and in unlimited
    # windows; otherwise a reference for how far the owned squad has drifted.
    optimal = fopt.build_squad(projs, cfg, budget, nation_cap)
    chips_remaining = [c for c in ALL_CHIPS if c not in (state.get("chips_used") or [])]
    ft = _free_transfers(cfg, target_round)
    ko_ties_known = any(mf.round != "group" for mf in forecast.match_forecasts.values())
    plan = None

    if not owned or stage == "pre":
        squad = rec_squad = optimal
        transfer_block = {"mode": "initial", "free_transfers": "unlimited",
                          "note": "Squad is freely editable until the first kickoff — set this exact 15."}
    elif ft == "unlimited" and (target_round != "R32" or ko_ties_known):
        # unlimited window (before the R32): full rebuild to the optimum, no hits
        squad = rec_squad = optimal
        transfer_block = {"mode": "rebuild", "free_transfers": "unlimited",
                          "moves": _rebuild_moves(owned, squad, by_pid),
                          "note": f"Transfers before the {STAGE_LABEL[target_round]} are unlimited — "
                                  f"rebuild to this exact 15 at no cost."}
    elif ft == "unlimited":
        # unlimited window already, but R32 ties unknown — hold the moves until they are
        squad = rec_squad = _owned_squad(projs, owned, budget, optimal, log,
                                         lineup=(state.get("owned") or {}).get("lineup"))
        transfer_block = {"mode": "hold", "free_transfers": "unlimited",
                          "note": "Unlimited transfers before the Round of 32 — but the ties aren't set yet. "
                                  "HOLD all moves; the rebuild recommendation lands once R32 matchups are known."}
    else:
        bank = float((state.get("owned") or {}).get("bank", 0.0))
        penalty = float(cfg.get("fantasy.extra_transfer_penalty", -3))
        plan = ftr.plan_transfers(owned, projs, budget, nation_cap, int(ft), bank,
                                  extra_penalty=penalty)
        # The squad to OWN for target_round = owned + this round's planned moves.
        effective = _apply_moves(owned, plan["moves"])
        rec_squad = _owned_squad(projs, effective, budget, optimal, log)
        if target_round == stage:
            # Window still open (this round locks today): the headline IS the
            # post-transfer team — build it now, before today's lock.
            squad = rec_squad
            transfer_block = {"mode": "transfers", "window": "open", "applies_to": target_round,
                              "plan": _serialize_plan(plan, by_pid), "free_transfers": ft,
                              "note": (f"Make these transfer(s) now — {STAGE_LABEL[stage]} locks today. "
                                       f"The 15 / XI / captain below are the post-transfer team.")}
        else:
            # A locked round is in progress: headline = your ACTIVE squad (what's
            # scoring now). The moves are the suggested swaps for the next deadline —
            # don't make them yet (team news can shift the pick; a free transfer rolls over).
            squad = _owned_squad(projs, owned, budget, optimal, log,
                                 lineup=(state.get("owned") or {}).get("lineup"))
            transfer_block = {"mode": "transfers", "window": "upcoming", "applies_to": target_round,
                              "plan": _serialize_plan(plan, by_pid), "free_transfers": ft,
                              "note": (f"Your ACTIVE {STAGE_LABEL[stage]} squad — the XI, captain and bench "
                                       f"below are what's scoring now. The swaps under 'Transfer plan' are "
                                       f"for {STAGE_LABEL[target_round]}; don't make them until the lock-eve run.")}

    gap = round(optimal.squad_horizon - squad.squad_horizon, 1)
    owned_set = {p.pid for p in squad.players}
    twelfth = max((p for p in projs if p.pid not in owned_set and p.minutes_prob > 0.5),
                  key=lambda p: p.exp_next, default=None)
    chips = ftr.chip_advice(stage, target_round, chips_remaining, squad, plan,
                            advancement=advancement, optimal_gap=gap if gap > 0.05 else None,
                            twelfth={"name": twelfth.name, "ev": round(twelfth.exp_next, 1)} if twelfth else None)

    return {
        "squad": _serialize_squad(squad),
        "recommended": _serialize_squad(rec_squad),
        "playbook": _playbook(squad, _active_round_id(bundle.fantasy_rounds, stage),
                              _stage_matches_done(bundle.fixtures, results, stage)),
        "transfers": transfer_block,
        "chips": chips,
        "chips_remaining": chips_remaining,
        "nation_cap": nation_cap,
        "budget": budget,
        "target_round": target_round,
        "optimal_gap": gap,
        "pool_top": [p.to_record() for p in sorted(projs, key=lambda x: x.horizon, reverse=True)[:60]],
        "pool_by_pos": _pool_by_pos(projs),
        "all_projs": {p.pid: p for p in projs},
    }


def _active_round_id(rounds_raw, stage) -> str | None:
    """Fantasy round currently scoring: the feed's status=='playing' round, with a
    deterministic stage-order fallback when the feed is unavailable (cache-only runs)."""
    rows = rounds_raw if isinstance(rounds_raw, list) else (rounds_raw or {}).get("data", [])
    for r in rows:
        if r.get("status") == "playing":
            return str(r.get("id"))
    return {"MD1": "1", "MD2": "2", "MD3": "3", "R32": "4", "R16": "5",
            "QF": "6", "SF": "7", "final": "8"}.get(stage)


def _stage_matches_done(fixtures, results, stage) -> set[str]:
    """Normalized team names whose match in the CURRENT round has finished."""
    from .model.teams import normalize_team
    md_of = _group_matchdays(fixtures)
    done = set()
    for m in fixtures["matches"]:
        if _round_of(m, md_of) == stage and m["num"] in results:
            done.add(normalize_team(str(m.get("home") or "")))
            done.add(normalize_team(str(m.get("away") or "")))
    return done


def _captain_ladder(squad, banked_of) -> list[dict]:
    """Mid-round captaincy relay: the armband can move to a not-yet-played starter
    once the current captain's match ends (forfeiting his double). Switching is +EV
    whenever banked points < the next rung's expected points — that's the threshold.

    Relay candidates are MID/FWD starters whose own round match has NOT finished yet
    (``banked_of`` is None). Crucially, once the captain has played, the live feed has
    advanced his ``next_date`` to a LATER round — so anchor the chain at "" in that
    case (every not-yet-played starter is eligible) rather than at his now-stale
    next_date, which would otherwise filter out every teammate still to play this round
    and collapse the ladder to a false HOLD."""
    by_pid = squad.by_pid()
    cap = by_pid[squad.captain]
    rungs = [cap]
    cap_played = banked_of(cap) is not None
    cands = [by_pid[pid] for pid in squad.starters
             if pid != squad.captain and by_pid[pid].position in ("MID", "FWD")
             and banked_of(by_pid[pid]) is None]
    last_date = "" if cap_played else (cap.next_date or "")
    while len(rungs) < 3:
        later = [p for p in cands
                 if p.next_date and p.next_date > last_date and p.exp_next >= 3.0]
        if not later:
            break
        # Chain forward in TIME, not by EV: the relay's value is seeing each result
        # before committing the armband, so take the earliest viable kickoff next
        # (best projection only breaks date ties). Picking the latest high-EV name
        # instead would idle the armband through the earlier games it could capture.
        nxt = min(later, key=lambda p: (p.next_date, -p.exp_next))
        rungs.append(nxt)
        cands.remove(nxt)
        last_date = nxt.next_date
    rows = []
    for i, p in enumerate(rungs):
        row = {"name": p.name, "nation": p.nation, "date": p.next_date or "TBD",
               "exp": round(p.exp_next, 1)}
        b = banked_of(p)
        if b is not None:
            row["banked"] = int(b)
        if i + 1 < len(rungs):
            row["switch_if"] = int(rungs[i + 1].exp_next)
            row["next_name"] = rungs[i + 1].name
        rows.append(row)
    return rows


def _lineup_fixes(squad, banked_of) -> list[dict]:
    """Pre-kickoff line-up repair for a LOCKED round. Each player locks at his OWN
    match kickoff, so until then you can freely move him between the XI and the
    bench — a starter who won't play his upcoming match (injury, rotation, a
    back-up keeper) should simply be benched for a same-position bench player whose
    match also hasn't kicked off. This is a free line-up edit, NOT the narrower
    "manual sub" (which only matters once a starter has already locked / played).

    Returns one {out, in, ...} per fixable starter: low minutes-probability, his
    match not yet finished, and a same-position bench player who is markedly more
    likely to play and worth more EV. Naturally empty for a freshly-optimised squad
    (the optimiser never starts a non-player), so it only fires on a locked XI."""
    by_pid = squad.by_pid()
    bench = [by_pid[pid] for pid in squad.bench]
    used: set[int] = set()
    fixes: list[dict] = []
    for pid in squad.starters:
        s = by_pid[pid]
        if banked_of(s) is not None:            # his match already finished -> result is locked in
            continue
        if s.minutes_prob >= 0.5:               # expected to play -> keep him
            continue
        cands = [b for b in bench if b.position == s.position and b.pid not in used
                 and banked_of(b) is None and b.minutes_prob >= s.minutes_prob + 0.3
                 and b.exp_next > s.exp_next]
        if not cands:
            continue
        b = max(cands, key=lambda x: x.exp_next)
        used.add(b.pid)
        fixes.append({"out": s.name, "out_nation": s.nation, "out_mins": round(100 * s.minutes_prob),
                      "out_date": s.next_date or "TBD", "in": b.name, "in_nation": b.nation,
                      "in_mins": round(100 * b.minutes_prob), "in_date": b.next_date or "TBD",
                      "position": s.position, "gain": round(b.exp_next - s.exp_next, 1)})
    return fixes


def _playbook(squad, active_rid=None, ended=frozenset()) -> dict:
    """In-round actions the user can take between daily runs. With the live feed's
    per-round points (populated once matches finish), the captain rule resolves to a
    concrete SWITCH/HOLD verdict instead of a threshold the user has to evaluate."""
    by_pid = squad.by_pid()

    def banked_of(p):
        if active_rid is None or p.nation not in ended:
            return None             # his match hasn't finished (or no live feed)
        return p.round_points.get(active_rid, 0.0)   # finished, no feed entry -> played 0'

    ladder = _captain_ladder(squad, banked_of)
    live = None
    if ladder and ladder[0].get("banked") is not None:
        b = ladder[0]["banked"]
        if len(ladder) > 1:
            live = {"banked": b, "verdict": "SWITCH" if b < ladder[1]["exp"] else "HOLD",
                    "to": ladder[1]["name"], "to_exp": ladder[1]["exp"]}
        else:
            live = {"banked": b, "verdict": "HOLD", "to": None, "to_exp": None}

    done_starters = [p for p in (by_pid[pid] for pid in squad.starters)
                     if banked_of(p) is not None]
    xi_pts = sum(banked_of(p) for p in done_starters)
    cap_done = banked_of(by_pid[squad.captain]) is not None
    if cap_done:
        xi_pts += banked_of(by_pid[squad.captain])   # captain counts double
    xi_live = {"points": int(xi_pts), "done": len(done_starters),
               "captain_doubled": cap_done} if done_starters else None

    bench_first = None
    bench_out = [by_pid[pid] for pid in squad.bench if by_pid[pid].position != "GK"]
    if bench_out:
        b = bench_out[0]
        bench_first = {"name": b.name, "nation": b.nation, "exp": round(b.exp_next, 1),
                       "date": b.next_date or "TBD"}
    fixes = _lineup_fixes(squad, banked_of)
    return {"ladder": ladder, "fixes": fixes, "bench_first": bench_first,
            "live": live, "xi_live": xi_live}


def _apply_moves(owned: list[int], moves) -> list[int]:
    pids = list(owned)
    for m in moves:
        if m.out_pid in pids:
            pids[pids.index(m.out_pid)] = m.in_pid
    return pids


def _owned_squad(projs, pids, budget, fallback, log, lineup=None):
    """Squad object for the user's reachable 15; falls back to the optimum if the
    owned pids can't be resolved against the current player pool. `lineup` freezes
    the XI/captain for a round that is already locked."""
    try:
        return fopt.squad_from_pids(projs, pids, budget, lineup=lineup)
    except (ValueError, RuntimeError) as e:
        log(f"  [warn] Could not assemble owned squad ({e}); presenting the optimal squad instead.")
        return fallback


def _rebuild_moves(owned: list[int], squad, by_pid) -> list[dict]:
    """out/in pairs (matched by position) describing the rebuild diff."""
    new_pids = {p.pid for p in squad.players}
    outs = [pid for pid in owned if pid not in new_pids and pid in by_pid]
    ins = [p.pid for p in squad.players if p.pid not in owned]
    moves = []
    for pos in ("GK", "DEF", "MID", "FWD"):
        pos_out = [pid for pid in outs if by_pid[pid].position == pos]
        pos_in = [pid for pid in ins if by_pid[pid].position == pos]
        for o, i in zip(pos_out, pos_in):
            moves.append({"out": by_pid[o].name, "in": by_pid[i].name, "position": pos})
    return moves


def _nation_cap(cfg, target_round) -> int:
    cap = cfg.get("fantasy.nation_cap", 3)
    if isinstance(cap, dict):
        key = {"R32": "round_of_32", "R16": "round_of_16", "QF": "quarter_final",
               "SF": "semi_final", "final": "final"}.get(target_round, "group_stage")
        return int(cap.get(key, 3))
    return int(cap)


def _budget(cfg, target_round) -> float:
    base = float(cfg.get("fantasy.budget", 100.0))
    if target_round in KO_ROUNDS:
        return float(cfg.get("fantasy.ko_budget",
                             base + float(cfg.get("fantasy.ko_budget_bonus", 5.0))))
    return base


def _free_transfers(cfg, target_round):
    """Free transfers for the round being planned: int or "unlimited"."""
    ft = cfg.get("fantasy.free_transfers_per_round", 2)
    if isinstance(ft, dict):
        key = {"MD1": "pre_tournament", "MD2": "before_matchday_2", "MD3": "before_matchday_3",
               "R32": "before_round_of_32", "R16": "before_round_of_16",
               "QF": "before_quarter_finals", "SF": "before_semi_finals",
               "final": "before_final"}.get(target_round)
        v = ft.get(key) if key else None
        if v == "unlimited":
            return "unlimited"
        if v is not None:
            return int(v)
        return 2
    return int(ft) if isinstance(ft, int) else 2


def _serialize_squad(squad) -> dict:
    by_pid = squad.by_pid()

    def pinfo(pid):
        p = by_pid[pid]
        return {"pid": pid, "name": p.name, "nation": p.nation, "group": p.group,
                "position": p.position, "price": round(p.price, 1), "ownership": round(p.ownership, 1),
                "exp_next": round(p.exp_next, 2), "horizon": round(p.horizon, 1),
                "minutes_prob": round(p.minutes_prob, 2), "tags": p.tags, "why": p.why,
                "is_captain": pid == squad.captain, "is_vice": pid == squad.vice}

    return {
        "starters": [pinfo(pid) for pid in squad.starters],
        "bench": [pinfo(pid) for pid in squad.bench],
        "all": [pinfo(p.pid) for p in squad.players],
        "captain": pinfo(squad.captain), "vice": pinfo(squad.vice),
        "formation": squad.formation, "cost": squad.cost, "bank": squad.bank,
        "budget": squad.budget, "xi_exp": squad.xi_exp, "squad_horizon": squad.squad_horizon,
        "nation_counts": squad.nation_counts,
    }


def _serialize_plan(plan, by_pid) -> dict:
    def mv(m):
        return {"out": m.out_name, "in": m.in_name, "position": m.position,
                "gain": m.gain, "price_delta": m.price_delta}
    return {"moves": [mv(m) for m in plan["moves"]], "free_used": plan["free_used"],
            "hits": plan["hits"], "hit_cost": plan["hit_cost"], "net_gain": plan["net_gain"]}


def _pool_by_pos(projs) -> dict:
    out = {}
    for pos in ("GK", "DEF", "MID", "FWD"):
        ranked = sorted([p for p in projs if p.position == pos], key=lambda x: x.horizon, reverse=True)[:15]
        out[pos] = [p.to_record() for p in ranked]
    return out


def _assemble(cfg, run_date, gen_cet, stage, stage_label, bundle, strengths, forecast,
              simr, pred_records, fantasy_out, scoring_summary=None, gp_summary=None) -> dict:
    from .model.teams import normalize_team
    scoring_summary = scoring_summary or {"total": 0, "rows": [], "n": 0}
    gp_summary = gp_summary or {"total": 0, "exact": 0, "rows": [], "n": 0, "missed": 0}
    groups_canon = {letter: [normalize_team(t) for t in tnames]
                    for letter, tnames in bundle.fixtures["groups"].items()}
    groups_of = {t: letter for letter, tnames in groups_canon.items() for t in tnames}
    # team strength table
    team_rows = []
    adv = simr["advancement"]
    for t in bundle.teams:
        a = adv.get(t, {})
        team_rows.append({
            "team": t, "strength": round(strengths.s.get(t, 0.0), 3),
            "title_prob": round(a.get("champion", 0.0), 4),
            "reach_final": round(a.get("reach_final", 0.0), 4),
            "reach_QF": round(a.get("reach_QF", 0.0), 4),
            "win_group": round(a.get("win_group", 0.0), 4),
            "top2": round(a.get("top2", 0.0), 4),
            "qualify": round(a.get("top2", 0.0) + a.get("third_qualify", 0.0), 4),
            "exp_matches": round(a.get("exp_remaining_matches", 0.0), 2),
            "title_odds_devig": round(strengths.title_prob_devig.get(t, 0.0), 4),
        })
    team_rows.sort(key=lambda r: r["title_prob"], reverse=True)

    preds_all = sorted(pred_records, key=lambda r: r["_sort"])
    preds_sorted = [r for r in preds_all if not r.get("played")]   # upcoming only (display)

    # deadlines: only matches that have NOT kicked off yet (a mid-day rerun must not
    # point the user at a deadline that already passed)
    now = now_cet()
    future = []
    for r in preds_sorted:
        mf = forecast.match_forecasts[r["num"]]
        _, cet = kickoff_datetimes(mf.date, mf.kickoff_local, mf.tz)
        if cet is not None and cet > now:
            future.append((r, cet, mf))
    upcoming = [r for r, _, _ in future][:16]

    next_deadline = {"label": "—", "cet": "TBD", "countdown": "—"}
    first_day = None
    if future:
        r, cet, mf = future[0]
        note = "squad locks; MD1 predictions due" if stage == "pre" else "prediction due at kickoff"
        next_deadline = {
            "label": f"{r['home']} v {r['away']} kicks off ({note})",
            "cet": fmt_cet(cet), "countdown": countdown_str(cet, now), "date": mf.date,
        }
        first_day = mf.date
    first_day_matches = [r for r in preds_sorted if r.get("date") == first_day] if first_day else []

    sources = {
        "fixtures": bundle.fixtures.get("sources", []),
        "ratings_odds": bundle.ratings_odds.get("sources", []),
        "squads": bundle.squads_research.get("sources", []),
        "nostradamus": (bundle.nostradamus or {}).get("sources", []),
        "gopicks": cfg.get("gopicks.sources", []),
        "fantasy": cfg.get("fantasy.rules_sources", []),
        "fantasy_feed": ["https://play.fifa.com/json/fantasy/players.json",
                         "https://play.fifa.com/json/fantasy/squads.json"],
    }

    return {
        "run_date": run_date,
        "generated_at_cet": gen_cet.strftime("%a %d %b %Y, %H:%M %Z"),
        "stage": stage, "stage_label": stage_label,
        "tournament": cfg.get("tournament", {}),
        "warnings": bundle.warnings,
        "model_notes": strengths.notes,
        "n_sims": simr.get("n_sims"),
        "teams": team_rows,
        "team_lookup": {r["team"]: r for r in team_rows},
        "group_standings": simr["group_standings"],
        "groups": groups_canon,
        "groups_of": groups_of,
        "predictions": preds_sorted,
        "upcoming_deadlines": upcoming,
        "next_deadline": next_deadline,
        "first_day": first_day,
        "first_day_matches": first_day_matches,
        "scored_results": scoring_summary["rows"],
        "scoring_total": scoring_summary["total"],
        "gopicks_total": gp_summary["total"],
        "gopicks_exact": gp_summary["exact"],
        "gopicks_missed": gp_summary["missed"],
        "fantasy": fantasy_out,
        "fantasy_rules": cfg.get("fantasy", {}),
        "nostradamus_rules": cfg.get("nostradamus", {}),
        "gopicks_rules": cfg.get("gopicks", {}),
        "sources": sources,
        "third_slot_fallbacks": simr.get("third_slot_fallbacks", 0),
    }


def _persist(run_date, result, forecast, simr, pred_records, fantasy_out, log):
    pdir = io_utils.processed_dir(run_date)
    io_utils.save_json(pdir / "forecast_matches.json", forecast.to_records())
    io_utils.save_json(pdir / "advancement.json", {"advancement": simr["advancement"],
                                                   "group_standings": simr["group_standings"],
                                                   "n_sims": simr.get("n_sims")})
    io_utils.save_json(pdir / "predictions.json", pred_records)
    if fantasy_out:
        squad = fantasy_out["squad"]
        io_utils.save_json(pdir / "fantasy_squad.json",
                           {k: v for k, v in fantasy_out.items() if k != "all_projs"})
    # lightweight summary for diffing
    io_utils.save_json(pdir / "summary.json", {
        "run_date": run_date,
        "predictions": {str(r["num"]): f'{r["pred_home"]}-{r["pred_away"]}' for r in pred_records},
        "gopicks_predictions": {str(r["num"]): f'{r["gp_home"]}-{r["gp_away"]}'
                                for r in pred_records if "gp_home" in r},
        "title_odds": {r["team"]: r["title_prob"] for r in result["teams"][:24]},
        "squad": [p["pid"] for p in fantasy_out["squad"]["all"]] if fantasy_out else [],
        "captain": fantasy_out["squad"]["captain"]["name"] if fantasy_out else None,
    })
    # CSVs (human-never-opens, but useful/diffable)
    try:
        import csv
        with open(pdir / "predictions.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["num", "round", "home", "away", "pred", "gp_pred", "p_home", "p_draw", "p_away",
                        "ev_applied", "gp_ev", "confidence"])
            for r in pred_records:
                gp = f'{r["gp_home"]}-{r["gp_away"]}' if "gp_home" in r else ""
                w.writerow([r["num"], r["round"], r["home"], r["away"], f'{r["pred_home"]}-{r["pred_away"]}',
                            gp, r["p_home"], r["p_draw"], r["p_away"], r["ev_applied"],
                            r.get("gp_ev", ""), r["confidence"]])
    except Exception as e:  # noqa: BLE001
        log(f"  [warn] CSV write failed: {e}")


def _changelog(run_date, result, log) -> dict:
    prev = io_utils.previous_run_date(run_date)
    if not prev:
        return {"prev_run": None, "note": "First run — no previous run to diff against.",
                "pred_flips": [], "gp_pred_flips": [], "squad_changes": [], "odds_moves": []}
    try:
        prev_sum = io_utils.load_json(io_utils.processed_dir(prev) / "summary.json")
    except FileNotFoundError:
        return {"prev_run": prev, "note": "Previous run has no summary to diff.", "pred_flips": [],
                "gp_pred_flips": [], "squad_changes": [], "odds_moves": []}

    cur_preds = {str(r["num"]): f'{r["pred_home"]}-{r["pred_away"]}' for r in result["predictions"]}
    flips = []
    for num, pred in cur_preds.items():
        old = prev_sum.get("predictions", {}).get(num)
        if old and old != pred:
            flips.append({"num": int(num), "from": old, "to": pred})
    gp_flips = []
    for r in result["predictions"]:
        if "gp_home" not in r:
            continue
        pred = f'{r["gp_home"]}-{r["gp_away"]}'
        old = prev_sum.get("gopicks_predictions", {}).get(str(r["num"]))
        if old and old != pred:
            gp_flips.append({"num": r["num"], "from": old, "to": pred})
    odds_moves = []
    old_odds = prev_sum.get("title_odds", {})
    for r in result["teams"][:18]:
        old = old_odds.get(r["team"])
        if old is not None and abs(old - r["title_prob"]) >= 0.005:
            odds_moves.append({"team": r["team"], "from": round(old, 4), "to": r["title_prob"]})
    squad_changes = []
    if result.get("fantasy"):
        names = {pid: pr.name for pid, pr in (result["fantasy"].get("all_projs") or {}).items()}
        cur_squad = set(p["pid"] for p in result["fantasy"]["squad"]["all"])
        old_squad = set(prev_sum.get("squad", []))
        if old_squad:
            for pid in old_squad - cur_squad:
                squad_changes.append({"change": "out", "pid": pid, "name": names.get(pid, f"#{pid}")})
            for pid in cur_squad - old_squad:
                squad_changes.append({"change": "in", "pid": pid, "name": names.get(pid, f"#{pid}")})
    return {"prev_run": prev, "note": f"Diff vs {prev}.", "pred_flips": flips,
            "gp_pred_flips": gp_flips, "squad_changes": squad_changes, "odds_moves": odds_moves}


def _lineup_of(sq: dict) -> dict:
    """The XI/captain to freeze for a locked round (pids), from a serialized squad."""
    return {"starters": [p["pid"] for p in sq.get("starters", [])],
            "bench": [p["pid"] for p in sq.get("bench", [])],
            "captain": (sq.get("captain") or {}).get("pid")}


def _reconcile_owned(state, target_round, log):
    """assume_followed bookkeeping: advance `owned` to a previously-recommended squad
    only once the round it targeted has actually locked — i.e. the current transfer
    target has moved PAST it. While that round is still the open or a future window,
    `owned` stays = the squad currently locked, so a not-yet-executed forward transfer
    plan is never silently folded into the real team. (No-op if the user opted out of
    assume_followed or stated a deviation that already matches the recommendation.)"""
    if not state.get("assume_followed", True):
        return
    rec = state.get("recommended_squad") or {}
    fr = rec.get("for_round")
    owned = state.get("owned") or {}
    if not fr or not rec.get("player_ids"):
        return
    try:
        locked_past = STAGE_SEQ.index(target_round) > STAGE_SEQ.index(fr)
    except ValueError:
        return
    if locked_past and rec["player_ids"] != (owned.get("player_ids") or []):
        state["owned"] = {
            "player_ids": list(rec["player_ids"]), "captain": rec.get("captain"),
            "formation": rec.get("formation"), "bank": owned.get("bank", 0.0),
            "lineup": rec.get("lineup"),
            "note": (f"Auto-advanced to the squad locked for {STAGE_LABEL.get(fr, fr)} "
                     f"(assume_followed=true — the planned transfers took effect at that deadline)."),
        }
        log(f"  Advanced owned squad to the {STAGE_LABEL.get(fr, fr)} lock (planned transfers applied).")


def _update_state(state, run_date, result, fantasy_out):
    state.setdefault("schema", "wc2026-state-v1")
    state["last_run"] = run_date
    if fantasy_out:
        recsq = fantasy_out.get("recommended") or fantasy_out["squad"]
        state["recommended_squad"] = {
            "player_ids": [p["pid"] for p in recsq["all"]],
            "captain": recsq["captain"]["name"],
            "formation": recsq["formation"],
            "as_of": run_date,
            "for_round": fantasy_out.get("target_round"),
            "lineup": _lineup_of(recsq),
        }
    state.setdefault("owned", {"player_ids": [], "captain": None, "formation": None,
                              "bank": 0.0, "note": "CONFIRM your real team here each run (see RUNBOOK)."})
    state.setdefault("chips_used", [])
    state.setdefault("cumulative", {"nostradamus_points": 0, "gopicks_points": 0,
                                    "gopicks_exact_goals": 0, "fantasy_points": 0})
    state.setdefault("predictions_entered", {})
    # GoPicks (gopicks.app): same assume-followed semantics, separate entries because
    # its EV-optimal pick can differ from the Nostradamus one. Matches dated before
    # `joined` (the user signed up two matches into the tournament) score 0 / missed.
    state.setdefault("gopicks", {"joined": run_date, "predictions_entered": {},
                                 "points_official": None, "rank": None,
                                 "note": "predictions_entered: '<num>': 'H-A' for a stated deviation, "
                                         "'missed' for a match not entered; absent = followed the recommendation."})
    # Standing instruction: the user follows every recommendation exactly unless they
    # say otherwise, so the real team == the recommendation. Keep `owned` in sync so
    # reconciliation is automatic. (Set assume_followed:false in state.json to opt out.)
    state.setdefault("assume_followed", True)
    # Only fold the recommendation into `owned` when it is a squad you can own in the
    # CURRENT window — the pre-lock initial 15 or an unlimited-window rebuild. A locked
    # round's forward transfer plan is NOT folded in here; `owned` advances to it later
    # (via _reconcile_owned) once that round's deadline actually passes, so the file
    # always reflects the team you really have locked.
    tb = (fantasy_out or {}).get("transfers") or {}
    if state.get("assume_followed") and fantasy_out and tb.get("mode") in ("initial", "rebuild"):
        recsq = fantasy_out.get("recommended") or fantasy_out["squad"]
        rec = state["recommended_squad"]
        state["owned"] = {
            "player_ids": rec["player_ids"], "captain": rec["captain"],
            "formation": rec["formation"], "bank": recsq["bank"],
            "lineup": _lineup_of(recsq),
            "note": "Auto-synced to the recommendation (assume_followed=true; directly buildable now). "
                    "Tell the session if you deviated and it will re-optimize from your real team.",
        }
    _save_state(state)
