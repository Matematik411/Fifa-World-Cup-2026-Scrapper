"""Re-render output/*.html from state.json's `owned` AS LOCKED — read-only.

Use when `owned` carries a hand-set lineup/captain that a stock `run` (even
--no-fetch) would clobber: the pipeline re-optimizes, re-derives the XI and
rewrites state.json (see memory wc2026-rerender-without-clobbering-override).
Built 2026-07-18 for the final-round lock; general for any locked round.

What it does:
  * no-ops every persistence hook (_persist, _update_state, _save_state,
    update_latest_pointer) — state.json and data/processed/ stay untouched;
  * runs the normal pipeline on the cached feed (fetch=False, sim=False);
  * replaces the presented fantasy squad with `owned` under its frozen
    `owned.lineup` (squad_from_pids honours XI + captain), recomputes the
    playbook + captain ceiling for THAT squad, and swaps the transfer block
    for a LOCKED notice (no moves);
  * renders the HTML.

Run:  ./run.sh python scripts/render_locked.py   (or uv run with the env vars)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.pipeline as pipeline
import src.io_utils as io_utils
from src.fantasy import optimizer as fopt


def main() -> None:
    state = pipeline._load_state()
    owned = (state.get("owned") or {})
    pids, lineup = owned.get("player_ids"), owned.get("lineup")
    if not pids or not lineup:
        sys.exit("state.json owned.player_ids/lineup missing — nothing to render as locked.")

    # ---- kill every side effect ----
    pipeline._persist = lambda *a, **k: None
    pipeline._update_state = lambda *a, **k: None
    pipeline._save_state = lambda *a, **k: None
    io_utils.update_latest_pointer = lambda *a, **k: None
    pipeline.io_utils.update_latest_pointer = lambda *a, **k: None

    # ---- present owned-as-locked instead of the re-optimized plan ----
    orig_run_fantasy = pipeline._run_fantasy

    def locked_run_fantasy(cfg, bundle, forecast, advancement, stage, target_round,
                           st, results, log, stakes=None):
        fout = orig_run_fantasy(cfg, bundle, forecast, advancement, stage, target_round,
                                st, results, log, stakes=stakes)
        projs = pipeline.build_projections(
            bundle.players, bundle.squads_map, forecast, advancement,
            bundle.squads_research, bundle.fixtures, cfg,
            player_stats=bundle.player_stats, lineups=bundle.lineups,
            played=set(results), stakes=stakes, wc_form=bundle.wc_form)
        budget = pipeline._budget(cfg, target_round)
        squad = fopt.squad_from_pids(projs, pids, budget, lineup=lineup)
        fout["squad"] = fout["recommended"] = pipeline._serialize_squad(squad)
        fout["playbook"] = pipeline._playbook(
            squad, pipeline._active_round_id(bundle.fantasy_rounds, stage),
            pipeline._stage_matches_done(bundle.fixtures, results, stage))
        fout["captain_ceiling"] = pipeline.fcorr.captain_ceiling(squad, forecast, cfg) \
            if target_round in pipeline.fboost.KO_ORDER else fout.get("captain_ceiling")
        fout["optimal_gap"] = 0.0
        # template-wise "hold" is the render-a-note-only mode — reuse it for LOCKED
        fout["transfers"] = {
            "mode": "hold", "free_transfers": 0, "moves": [],
            "note": ("Round LOCKED — this is your active squad in the XI you locked "
                     "(captain included). No transfers exist for this round; only "
                     "line-up edits for players whose own match hasn't kicked off."),
        }
        log("  [render_locked] presented owned-as-locked "
            f"({len(pids)} players, captain pid {lineup.get('captain')}).")
        return fout

    pipeline._run_fantasy = locked_run_fantasy

    pipeline.run_pipeline(fetch=False, sim=False, render=True)


if __name__ == "__main__":
    main()
