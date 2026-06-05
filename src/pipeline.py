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
from .nostradamus import optimizer as nopt
from .sources.loader import load_bundle
from .timeutil import countdown_str, fmt_cet, fmt_local, kickoff_datetimes, now_cet

ALL_CHIPS = ["Wildcard", "12th Man", "Maximum Captain", "Qualification Booster", "Mystery Booster"]


def _stage(cfg, run_date: str, state: dict) -> tuple[str, str]:
    start = cfg.get("tournament.start_date", "2026-06-11")
    if run_date < start:
        return "pre", f"Pre-tournament — squad & Matchday-1 predictions lock at first kickoff ({start})"
    # crude matchday inference from entered predictions could go here; keep simple/honest
    return "group", "Group stage in progress"


def _load_state() -> dict:
    path = ROOT / "state.json"
    if path.exists():
        return io_utils.load_json(path)
    return {}


def _save_state(state: dict) -> None:
    io_utils.save_json(ROOT / "state.json", state)


def _load_results() -> dict:
    """Actual 90-minute results: data/manual/results.json -> {match_num: (home, away)}."""
    path = MANUAL / "results.json"
    if not path.exists():
        return {}
    raw = io_utils.load_json(path)
    rows = raw.get("results", raw) if isinstance(raw, dict) else {}
    out = {}
    for k, v in rows.items():
        try:
            out[int(k)] = (int(v[0]), int(v[1]))
        except (ValueError, TypeError, IndexError):
            continue
    return out


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


def run_pipeline(run_date: str | None = None, fetch: bool = True, sim: bool = True,
                 render: bool = True, log=print) -> dict:
    ensure_dirs()
    cfg = load_config()
    run_date = run_date or io_utils.today_str()
    gen_cet = now_cet()
    log(f"=== Run {run_date} | {gen_cet.strftime('%Y-%m-%d %H:%M CET')} ===")

    state = _load_state()
    stage, stage_label = _stage(cfg, run_date, state)

    bundle = load_bundle(cfg, run_date, fetch=fetch, log=log)
    for w in bundle.warnings:
        log(f"  [warn] {w}")

    results = _load_results()
    if results:
        log(f"Ingesting {len(results)} actual result(s) — standings/advancement will condition on them.")

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
        simr = BracketSimulator(forecast, bundle.fixtures, cfg, played=results).run(n_sims, seed)
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

    # ---- Fantasy ----
    fantasy_out = None
    if bundle.players and bundle.squads_map:
        log("Projecting fantasy points + optimizing squad...")
        fantasy_out = _run_fantasy(cfg, bundle, forecast, advancement, stage, state, log)
    else:
        log("  [warn] Skipping fantasy (no player feed).")

    # ---- assemble result ----
    result = _assemble(cfg, run_date, gen_cet, stage, stage_label, bundle, strengths,
                       forecast, simr, pred_records, fantasy_out, scoring_summary)

    # cumulative Nostradamus points = idempotent recompute from results; fantasy from state (user-entered)
    state.setdefault("cumulative", {})["nostradamus_points"] = scoring_summary["total"]
    result["performance"] = {
        "nostradamus_points": scoring_summary["total"],
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


def _run_fantasy(cfg, bundle, forecast, advancement, stage, state, log) -> dict:
    projs = build_projections(bundle.players, bundle.squads_map, forecast, advancement,
                              bundle.squads_research, bundle.fixtures, cfg,
                              player_stats=bundle.player_stats, lineups=bundle.lineups)
    budget = float(cfg.get("fantasy.budget", 100.0))
    nation_cap = _nation_cap(cfg, stage)
    owned = (state.get("owned") or {}).get("player_ids") or []

    squad = fopt.build_squad(projs, cfg, budget, nation_cap)
    by_pid = {p.pid: p for p in projs}

    # transfers + chips
    chips_remaining = [c for c in ALL_CHIPS if c not in (state.get("chips_used") or [])]
    if owned:
        ft = _free_transfers(cfg, stage)
        bank = float((state.get("owned") or {}).get("bank", 0.0))
        plan = ftr.plan_transfers(owned, projs, budget, nation_cap, ft, bank)
        chips = ftr.chip_advice(stage, chips_remaining, squad, plan)
        transfer_block = {"mode": "transfers", "plan": _serialize_plan(plan, by_pid), "free_transfers": ft}
    else:
        chips = ftr.chip_advice(stage, chips_remaining, squad, None)
        transfer_block = {"mode": "initial", "note": "First run — set this as your initial 15 before the 11 Jun lock."}

    return {
        "squad": _serialize_squad(squad),
        "transfers": transfer_block,
        "chips": chips,
        "chips_remaining": chips_remaining,
        "nation_cap": nation_cap,
        "budget": budget,
        "pool_top": [p.to_record() for p in sorted(projs, key=lambda x: x.horizon, reverse=True)[:60]],
        "pool_by_pos": _pool_by_pos(projs),
        "all_projs": {p.pid: p for p in projs},
    }


def _nation_cap(cfg, stage) -> int:
    cap = cfg.get("fantasy.nation_cap", 3)
    if isinstance(cap, dict):
        key = {"pre": "group_stage", "MD1": "group_stage", "group": "group_stage"}.get(stage, "group_stage")
        return int(cap.get(key, 3))
    return int(cap)


def _free_transfers(cfg, stage) -> int:
    ft = cfg.get("fantasy.free_transfers_per_round", 2)
    if isinstance(ft, dict):
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
              simr, pred_records, fantasy_out, scoring_summary=None) -> dict:
    from .model.teams import normalize_team
    scoring_summary = scoring_summary or {"total": 0, "rows": [], "n": 0}
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
    # upcoming deadlines (group matches with a CET deadline)
    upcoming = [r for r in preds_sorted if r["deadline_cet"] != "TBD"][:16]

    # headline next deadline + the first matchday's matches (for the dashboard)
    now = now_cet()
    next_deadline = {"label": "—", "cet": "TBD", "countdown": "—"}
    first_day = None
    first_cet = None
    for r in preds_sorted:
        mf = forecast.match_forecasts[r["num"]]
        utc, cet = kickoff_datetimes(mf.date, mf.kickoff_local, mf.tz)
        if cet is not None:
            first_cet = cet
            first_day = mf.date
            next_deadline = {
                "label": f"{r['home']} v {r['away']} kicks off (squad locks; MD1 predictions due)",
                "cet": fmt_cet(cet), "countdown": countdown_str(cet, now), "date": mf.date,
            }
            break
    first_day_matches = [r for r in preds_sorted if r.get("date") == first_day] if first_day else []

    sources = {
        "fixtures": bundle.fixtures.get("sources", []),
        "ratings_odds": bundle.ratings_odds.get("sources", []),
        "squads": bundle.squads_research.get("sources", []),
        "nostradamus": (bundle.nostradamus or {}).get("sources", []),
        "fantasy": cfg.get("fantasy.rules_sources", []),
        "fantasy_feed": ["https://play.fifa.com/json/fantasy/players.json",
                         "https://play.fifa.com/json/fantasy/squads.json"],
    }

    return {
        "run_date": run_date,
        "generated_at_cet": gen_cet.strftime("%a %d %b %Y, %H:%M CET"),
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
        "fantasy": fantasy_out,
        "fantasy_rules": cfg.get("fantasy", {}),
        "nostradamus_rules": cfg.get("nostradamus", {}),
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
        "title_odds": {r["team"]: r["title_prob"] for r in result["teams"][:24]},
        "squad": [p["pid"] for p in fantasy_out["squad"]["all"]] if fantasy_out else [],
        "captain": fantasy_out["squad"]["captain"]["name"] if fantasy_out else None,
    })
    # CSVs (human-never-opens, but useful/diffable)
    try:
        import csv
        with open(pdir / "predictions.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["num", "round", "home", "away", "pred", "p_home", "p_draw", "p_away", "ev_applied", "confidence"])
            for r in pred_records:
                w.writerow([r["num"], r["round"], r["home"], r["away"], f'{r["pred_home"]}-{r["pred_away"]}',
                            r["p_home"], r["p_draw"], r["p_away"], r["ev_applied"], r["confidence"]])
    except Exception as e:  # noqa: BLE001
        log(f"  [warn] CSV write failed: {e}")


def _changelog(run_date, result, log) -> dict:
    prev = io_utils.previous_run_date(run_date)
    if not prev:
        return {"prev_run": None, "note": "First run — no previous run to diff against.",
                "pred_flips": [], "squad_changes": [], "odds_moves": []}
    try:
        prev_sum = io_utils.load_json(io_utils.processed_dir(prev) / "summary.json")
    except FileNotFoundError:
        return {"prev_run": prev, "note": "Previous run has no summary to diff.", "pred_flips": [], "squad_changes": [], "odds_moves": []}

    cur_preds = {str(r["num"]): f'{r["pred_home"]}-{r["pred_away"]}' for r in result["predictions"]}
    flips = []
    for num, pred in cur_preds.items():
        old = prev_sum.get("predictions", {}).get(num)
        if old and old != pred:
            flips.append({"num": int(num), "from": old, "to": pred})
    odds_moves = []
    old_odds = prev_sum.get("title_odds", {})
    for r in result["teams"][:18]:
        old = old_odds.get(r["team"])
        if old is not None and abs(old - r["title_prob"]) >= 0.005:
            odds_moves.append({"team": r["team"], "from": round(old, 4), "to": r["title_prob"]})
    squad_changes = []
    if result.get("fantasy"):
        cur_squad = set(p["pid"] for p in result["fantasy"]["squad"]["all"])
        old_squad = set(prev_sum.get("squad", []))
        if old_squad:
            for pid in old_squad - cur_squad:
                squad_changes.append({"change": "out", "pid": pid})
            for pid in cur_squad - old_squad:
                squad_changes.append({"change": "in", "pid": pid})
    return {"prev_run": prev, "note": f"Diff vs {prev}.", "pred_flips": flips,
            "squad_changes": squad_changes, "odds_moves": odds_moves}


def _update_state(state, run_date, result, fantasy_out):
    state.setdefault("schema", "wc2026-state-v1")
    state["last_run"] = run_date
    if fantasy_out:
        state["recommended_squad"] = {
            "player_ids": [p["pid"] for p in fantasy_out["squad"]["all"]],
            "captain": fantasy_out["squad"]["captain"]["name"],
            "formation": fantasy_out["squad"]["formation"],
            "as_of": run_date,
        }
    state.setdefault("owned", {"player_ids": [], "captain": None, "formation": None,
                              "bank": 0.0, "note": "CONFIRM your real team here each run (see RUNBOOK)."})
    state.setdefault("chips_used", [])
    state.setdefault("cumulative", {"nostradamus_points": 0, "fantasy_points": 0})
    state.setdefault("predictions_entered", {})
    _save_state(state)
