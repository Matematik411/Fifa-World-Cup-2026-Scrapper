"""Walk-forward backtest + calibration over the persisted daily runs, plus an
A/B counterfactual harness for measuring new model signals.

Two layers (see UPGRADES.md U1):

1. **Walk-forward (leak-free, no re-simulation).** Every daily run persists a
   pre-kickoff snapshot under ``data/processed/<date>/``. A match stays
   ``played: false`` in every run until its result is entered, so the genuine
   *pick-of-record* for a match is the prediction from the LATEST run where it is
   still ``played: false``. We score those picks (Nostradamus + GoPicks) and
   calibrate the matching pre-match scoreline matrices (rebuilt from the persisted
   Dixon-Coles lambdas) against the actual results. This is the trustworthy,
   out-of-sample headline.

2. **Counterfactual A/B (current data).** Re-run the CURRENT model over all played
   matches in two modes — *as-shipped* (market-where-available) and *ratings-only*
   (``Forecast(ignore_market=True)``, isolates the strength model). Comparing two
   variants on the same matches isolates a signal's effect; this is the vehicle
   U2/U5 use to gate a change. Not walk-forward for the slow-moving base ratings —
   labelled as such in the report.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import io_utils
from ..config import MANUAL, OUTPUT, PROCESSED, load_config
from ..gopicks import optimizer as gopt
from ..nostradamus import optimizer as nopt
from . import metrics as M

_KO_ROUNDS = {"R32", "R16", "QF", "SF", "final", "third-place"}


def _run_dates() -> list[str]:
    if not PROCESSED.exists():
        return []
    return sorted(p.name for p in PROCESSED.iterdir() if p.is_dir() and p.name[:4].isdigit())


def _load(run: str, name: str):
    try:
        return io_utils.load_json(PROCESSED / run / name)
    except FileNotFoundError:
        return None


def _load_results() -> dict[int, tuple[int, int]]:
    raw = _safe_results()
    rows = raw.get("results", raw) if isinstance(raw, dict) else {}
    out: dict[int, tuple[int, int]] = {}
    for k, v in rows.items():
        try:
            out[int(k)] = (int(v[0]), int(v[1]))
        except (ValueError, TypeError, IndexError):
            continue
    return out


def _safe_results() -> dict:
    path = MANUAL / "results.json"
    return io_utils.load_json(path) if path.exists() else {}


def _matchday(num: int, rnd: str) -> str:
    """Coarse stage label for the per-stage breakdown."""
    if rnd and rnd != "group":
        return "final" if rnd == "third-place" else rnd
    if num <= 24:
        return "MD1"
    if num <= 48:
        return "MD2"
    return "MD3"


def records_of_record(runs: list[str]) -> dict[int, dict]:
    """num -> {run, pred, fc}: the prediction + forecast from the LATEST run where
    the match was still ``played: false`` (its genuine pre-result snapshot)."""
    out: dict[int, dict] = {}
    fc_cache: dict[str, dict] = {}
    for run in runs:                                   # ascending -> later runs overwrite
        recs = _load(run, "predictions.json") or []
        for r in recs:
            if r.get("played"):
                continue                               # result was known then -> not pre-match
            if run not in fc_cache:
                fc_cache[run] = {x["num"]: x for x in (_load(run, "forecast_matches.json") or [])}
            out[r["num"]] = {"run": run, "pred": r, "fc": fc_cache[run].get(r["num"])}
    return out


def realized_scoring(results: dict, ror: dict, state: dict, cfg) -> dict:
    """Walk-forward realized points for both score-prediction leagues, using each
    match's pick-of-record (honouring any stated deviation in state)."""
    nscore = cfg.get("nostradamus", {})
    gscore = cfg.get("gopicks", {})
    entered = state.get("predictions_entered") or {}
    gp_state = state.get("gopicks") or {}
    gp_entered = gp_state.get("predictions_entered") or {}
    joined = gp_state.get("joined") or ""

    rows = []
    by_stage: dict[str, dict] = {}
    n_total = n_scored = gp_total = gp_exact = gp_missed = gp_scored = 0
    for num in sorted(results):
        rec = ror.get(num)
        if not rec or not rec.get("pred"):
            continue
        pred = rec["pred"]
        ah, aa = results[num]
        rnd = pred["round"]
        stage = _matchday(num, rnd)
        st = by_stage.setdefault(stage, {"n": 0, "nostra": 0, "gp": 0, "gp_exact": 0})

        # --- Nostradamus ---
        es = entered.get(str(num))
        if es and "-" in str(es):
            nh, na = (int(x) for x in str(es).split("-"))
        else:
            nh, na = pred["pred_home"], pred["pred_away"]
        npts = nopt.score_prediction(nh, na, ah, aa, rnd in _KO_ROUNDS, nscore)
        n_total += npts
        n_scored += 1

        # --- GoPicks (own pick; missed if before join or no pick on record) ---
        gp_pick = "—"
        gpts = gx = 0
        ges = gp_entered.get(str(num))
        if ges == "missed" or (ges is None and joined and (pred.get("date") or "") < joined):
            gp_missed += 1
        elif ges and "-" in str(ges):
            gh, ga = (int(x) for x in str(ges).split("-"))
            gpts, gx = gopt.score_prediction(gh, ga, ah, aa, gscore)
            gp_pick = f"{gh}-{ga}"
            gp_total += gpts; gp_exact += gx; gp_scored += 1
        elif pred.get("gp_home") is not None:
            gh, ga = pred["gp_home"], pred["gp_away"]
            gpts, gx = gopt.score_prediction(gh, ga, ah, aa, gscore)
            gp_pick = f"{gh}-{ga}"
            gp_total += gpts; gp_exact += gx; gp_scored += 1
        else:
            gp_missed += 1                              # no GoPicks pick persisted (pre-league)

        st["n"] += 1; st["nostra"] += npts; st["gp"] += gpts; st["gp_exact"] += gx
        rows.append({"num": num, "stage": stage, "home": pred["home"], "away": pred["away"],
                     "run": rec["run"], "nostra_pick": f"{nh}-{na}", "gp_pick": gp_pick,
                     "actual": f"{ah}-{aa}", "nostra_pts": npts, "gp_pts": gpts, "gp_exact": gx})

    cum = state.get("cumulative") or {}
    return {
        "rows": rows,
        "nostradamus_total": n_total, "nostradamus_scored": n_scored,
        "gopicks_total": gp_total, "gopicks_exact": gp_exact,
        "gopicks_scored": gp_scored, "gopicks_missed": gp_missed,
        "by_stage": by_stage,
        "nostradamus_per_match": round(n_total / n_scored, 2) if n_scored else None,
        "gopicks_per_match": round(gp_total / gp_scored, 2) if gp_scored else None,
        # cross-check vs the pipeline's current-model re-score (state.cumulative)
        "cum_nostradamus": cum.get("nostradamus_points"),
        "cum_gopicks": cum.get("gopicks_points"),
        "cum_gopicks_exact": cum.get("gopicks_exact_goals"),
    }


def _calibration_over(items: list[dict], cfg) -> dict:
    """items: list of {round, num, p_home, p_draw, p_away, exp_home, exp_away,
    lam_home, lam_away, actual:(h,a)}. Computes RPS/Brier/log-loss/goals-MAE,
    the reliability curve, and the clean-sheet expected-vs-actual diagnostic."""
    rho = float(cfg.get("model.dixon_coles_rho", -0.12))
    max_goals = int(cfg.get("model.goal_cap", 8))
    rps, brier, ll, gmae, rel = [], [], [], [], []
    cs_pred = cs_act = team_innings = 0.0
    cs_by_stage: dict[str, dict] = {}
    for it in items:
        ah, aa = it["actual"]
        ph, pd, pa = it["p_home"], it["p_draw"], it["p_away"]
        oc = M.outcome_of(ah, aa)
        rps.append(M.rps_1x2(ph, pd, pa, oc))
        brier.append(M.brier_1x2((ph, pd, pa), oc))
        ll.append(M.log_loss_1x2((ph, pd, pa), oc))
        gmae.append(abs(it["exp_home"] - ah))
        gmae.append(abs(it["exp_away"] - aa))
        rel.extend(M.reliability_pairs(ph, pd, pa, oc))
        lam, mu = it.get("lam_home"), it.get("lam_away")
        if lam and mu:
            stage = _matchday(it["num"], it["round"])
            cbs = cs_by_stage.setdefault(stage, {"pred": 0.0, "act": 0, "n": 0})
            p_home_cs, p_away_cs = M.cs_probs_from_lambdas(lam, mu, rho, max_goals)
            cs_pred += p_home_cs + p_away_cs
            cs_act += (1 if aa == 0 else 0) + (1 if ah == 0 else 0)
            team_innings += 2
            cbs["pred"] += p_home_cs + p_away_cs
            cbs["act"] += (1 if aa == 0 else 0) + (1 if ah == 0 else 0)
            cbs["n"] += 2
    for cbs in cs_by_stage.values():
        cbs["pred"] = round(cbs["pred"], 1)
    return {
        "n": len(items),
        "rps": M.summarize(rps), "brier": M.summarize(brier), "log_loss": M.summarize(ll),
        "goals_mae": M.summarize(gmae),
        "reliability": M.reliability(rel, n_bins=5),
        "cs_expected": round(cs_pred, 1), "cs_actual": int(cs_act),
        "cs_team_innings": int(team_innings),
        "cs_ratio": round(cs_pred / cs_act, 2) if cs_act else None,
        "cs_by_stage": cs_by_stage,
    }


def calibration_walkforward(results: dict, ror: dict, cfg) -> dict:
    """As-shipped calibration from each match's pre-match persisted lambdas."""
    items = []
    for num in sorted(results):
        rec = ror.get(num)
        fc = rec.get("fc") if rec else None
        if not fc:
            continue
        items.append({"num": num, "round": fc.get("round", "group"),
                      "p_home": fc["p_home"], "p_draw": fc["p_draw"], "p_away": fc["p_away"],
                      "exp_home": fc["exp_home"], "exp_away": fc["exp_away"],
                      "lam_home": fc.get("lam_home"), "lam_away": fc.get("lam_away"),
                      "actual": results[num]})
    return _calibration_over(items, cfg)


def counterfactual(cfg, results: dict, ignore_market: bool, log=print) -> dict | None:
    """Re-run the current model over played matches (current inputs) and calibrate.
    ignore_market=True forces rating-derived lambdas (isolates the strength model)."""
    try:
        from ..model.ensemble import build_strengths
        from ..model.forecast import Forecast
        from ..sources.loader import load_bundle
        bundle = load_bundle(cfg, io_utils.today_str(), fetch=False, log=lambda *_: None)
        strengths = build_strengths(bundle.teams, bundle.ratings_odds, cfg)
        fc = Forecast(bundle.teams, strengths, cfg, bundle.ratings_odds, bundle.fixtures,
                      ignore_market=ignore_market)
        items = []
        for num, mf in fc.match_forecasts.items():
            if num not in results:
                continue
            items.append({"num": num, "round": mf.round,
                          "p_home": mf.p_home, "p_draw": mf.p_draw, "p_away": mf.p_away,
                          "exp_home": mf.exp_home, "exp_away": mf.exp_away,
                          "lam_home": mf.lam_home, "lam_away": mf.lam_away,
                          "actual": results[num]})
        return _calibration_over(items, cfg)
    except Exception as e:  # noqa: BLE001 — A/B panel is best-effort; never break the headline
        log(f"  [warn] counterfactual ({'ratings-only' if ignore_market else 'as-shipped'}) failed: {e}")
        return None


def run_backtest(cfg=None, log=print) -> dict:
    cfg = cfg or load_config()
    state_path = (PROCESSED.parent.parent / "state.json")
    state = io_utils.load_json(state_path) if state_path.exists() else {}
    runs = _run_dates()
    results = _load_results()
    log(f"Backtest: {len(runs)} persisted run(s), {len(results)} played match(es).")
    ror = records_of_record(runs)
    covered = sum(1 for n in results if n in ror)

    realized = realized_scoring(results, ror, state, cfg)
    cal = calibration_walkforward(results, ror, cfg)
    log(f"  Realized (walk-forward): Nostradamus {realized['nostradamus_total']} pts, "
        f"GoPicks {realized['gopicks_total']} pts ({realized['gopicks_exact']} exact).")
    log(f"  Calibration: RPS {cal['rps']}, log-loss {cal['log_loss']}, goals-MAE {cal['goals_mae']}, "
        f"clean sheets expected {cal['cs_expected']} vs actual {cal['cs_actual']}.")

    cf_market = counterfactual(cfg, results, ignore_market=False, log=log)
    cf_ratings = counterfactual(cfg, results, ignore_market=True, log=log)

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "n_runs": len(runs), "first_run": runs[0] if runs else None,
        "last_run": runs[-1] if runs else None,
        "n_results": len(results), "covered": covered,
        "realized": realized,
        "calibration": cal,
        "counterfactual": {"as_shipped": cf_market, "ratings_only": cf_ratings},
    }


def render_backtest_html(result: dict, output_dir=None, log=print) -> None:
    from ..report.render import ASSETS, PAGES, SORT_JS, build_env
    output_dir = output_dir or OUTPUT
    output_dir.mkdir(parents=True, exist_ok=True)
    env = build_env()
    css = (ASSETS / "style.css").read_text()
    pages = PAGES + [("backtest.html", "Backtest")]
    ctx = dict(result)
    ctx.update({
        "css": css, "sort_js": SORT_JS, "pages": pages, "active": "backtest.html",
        "stage_label": "Backtest & calibration", "generated_at_cet": result["generated_at"],
        "run_date": result.get("last_run") or "—", "n_sims": None, "warnings": [],
        "sources": {"results": ["data/manual/results.json"],
                    "backtest": ["walk-forward over data/processed/<date>/ snapshots"]},
    })
    html = env.get_template("backtest.html").render(**ctx)
    (output_dir / "backtest.html").write_text(html)
    log(f"  wrote backtest.html ({len(html) // 1024} KB)")
