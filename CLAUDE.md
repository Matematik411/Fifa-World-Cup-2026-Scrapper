# CLAUDE.md — read me first (every session)

This repo is a **re-runnable FIFA World Cup 2026 forecasting system**. It builds one
shared match model and feeds it into two optimizers — **RTV SLO Nostradamus** (score
predictor) and **official FIFA Fantasy** — then renders 5 HTML reports in `output/`
(the only thing the human reads). The human **follows the recommendations**, so be
decisive: one squad, one captain, one scoreline per match, each with a confidence tag.

## How to do a run (≈5 steps)

1. **Bootstrap the toolchain** (the VM is disposable — venv & tools are wiped on reboot, but this host-mounted repo persists):
   ```bash
   nix profile add nixpkgs#{python313,uv,cbc,chromium} nixpkgs#stdenv.cc.cc.lib nixpkgs#zlib
   cd <repo> && export UV_PROJECT_ENVIRONMENT="$SANDBOX_VM_STATE/fifa-wc-2026/venv" && uv sync
   ```
2. **Refresh research** → overwrite `data/manual/*.json` with today's data (odds, lineups, injuries, *yesterday's results*). This is the part *you* (Claude) do with WebSearch/WebFetch. **Follow `RUNBOOK.md` — it has the exact per-source checklist.**
3. **Reconcile `state.json`** → confirm the user's REAL `owned` team + `cumulative` points before optimizing (ask them, or read their fantasy team if an ID is set). Never assume prior advice was followed.
4. **Run:** `./run.sh run`  (fetch → model → optimize → render; idempotent; `run.sh` sets the venv path + `LD_LIBRARY_PATH` for NixOS).
5. **Verify & report:** open `output/index.html` (screenshot with chromium if useful), skim the changelog, and tell the user exactly what to do before the next deadline (in CET).

## Where the truth lives (nothing important is only in chat)

- **`RUNBOOK.md`** — the detailed per-run checklist (sources, verifications, commands, sanity checks). **Start there.**
- **`state.json`** — the user's real team, chips used, free transfers, bank, cumulative Nostradamus + fantasy points, predictions entered, `last_run`. Read first, rewrite last.
- **`data/manual/*.json`** — Claude-curated research inputs: `fixtures.json`, `ratings_odds.json` (+ `odds_extra.json`, merged), `squads.json`, `player_stats.json` (per-90 xG/xA → intra-team shares), `lineups.json` (confirmed/predicted XI → minutes), `results.json` (actual scores), `nostradamus.json`, `fantasy_rules.json`, `fantasy_feed.json`.
- **`data/processed/latest/`** — newest model run (for the changelog diff); `data/raw/<date>/` — cached source pulls.
- **`config.yaml`** — budget/rules/ensemble weights/sim iters. **Verified game rules in `data/manual/{fantasy_rules,nostradamus}.json` override config automatically.**

## Architecture (where code lives)

`src/sources/` fetch+normalize · `src/model/` ensemble + Dixon-Coles + Monte-Carlo bracket ·
`src/nostradamus/` EV-optimal scoreline · `src/fantasy/` projections + ILP + transfers ·
`src/report/` jinja2 templates + renderer · `src/pipeline.py` orchestration · `src/cli.py` entry.

## Confirmed facts (verified 2026-06-05 — re-verify if a new edition/season)

- **Nostradamus:** exact 3 / outcome+one-team 2 / outcome 1 / wrong 0; **doubled R32→final**; 90-min result only; covers **all 104** matches; per-match deadlines at kickoff.
- **FIFA Fantasy:** $100M (+$5M KO); 2/5/5/3; **nation cap 3→3→4→5→6→8** by stage; goals **GK9/DEF7/MID6/FWD5**, assist 3, CS 5/5/1/0, MID tackles+chances, FWD shots, GK/DEF goals-conceded −1 each after the first; chips: Wildcard (not MD1/R32), 12th Man, Maximum Captain, Qualification Booster (R32+), Mystery Booster (revealed R32). Feed: `play.fifa.com/json/fantasy/{players,squads,rounds}.json` (unauth).
- **Tournament:** 48 teams / 12 groups; Italy & Denmark did NOT qualify, Norway did. Opener Mexico–South Africa, 2026-06-11.

## Gotchas

Keep tool state OUT of this host-mounted repo (venv → `$SANDBOX_VM_STATE`). NixOS needs
`LD_LIBRARY_PATH=$HOME/.nix-profile/lib` for numpy/scipy (run.sh handles it). Knockout
predictions only unlock once teams are known — that's expected; re-run daily. The
optimizers are unit-tested (`uv run pytest`); run them after code changes.
