# CLAUDE.md — read me first (every session)

This repo is a **re-runnable FIFA World Cup 2026 forecasting system**. It builds one
shared match model and feeds it into three optimizers — **RTV SLO Nostradamus** (score
predictor), **GoPicks** (gopicks.app score predictor, Sentora partners league) and
**official FIFA Fantasy** — then renders 5 HTML reports in `output/`
(the only thing the human reads). The human **follows the recommendations**, so be
decisive: one squad, one captain, one scoreline per match, each with a confidence tag.

## How to do a run (≈5 steps)

1. **Bootstrap the toolchain** (the VM is disposable — venv & tools are wiped on reboot, but this host-mounted repo persists):
   ```bash
   nix profile add nixpkgs#{python313,uv,cbc,chromium} nixpkgs#stdenv.cc.cc.lib nixpkgs#zlib
   cd <repo> && export UV_PROJECT_ENVIRONMENT="$SANDBOX_VM_STATE/fifa-wc-2026/venv" && uv sync
   ```
2. **Refresh research** → overwrite `data/manual/*.json` with today's data (odds, lineups, injuries, *yesterday's results*). This is the part *you* (Claude) do with WebSearch/WebFetch. **Follow `RUNBOOK.md` — it has the exact per-source checklist.**
3. **Reconcile `state.json`** → **Standing instruction: assume the user followed every recommendation exactly** (squad/captain/transfers/chips + scorelines in BOTH score leagues) unless they say otherwise. `assume_followed:true` keeps `owned` truthful **without drift**: during a locked round `owned` stays = your real locked 15 (with the XI you locked), and only advances to a round's transfer plan once that round's deadline passes — the pre-lock initial 15 and unlimited-window rebuilds sync immediately, since those are buildable now. **Don't ask for confirmation — run straight through**; the user volunteers deviations unprompted (confirmed 2026-06-11). If he states one, set his real `owned`/`chips_used`/`predictions_entered` before optimizing. **Never ask for his points/positions in any league** (declined 2026-06-12) — he wants pure best-EV recommendations, not rank-aware strategy; all point tracking is automatic (results + live feed).
4. **Run:** `./run.sh run`  (fetch → model → optimize → render; idempotent; `run.sh` sets the venv path + `LD_LIBRARY_PATH` for NixOS).
5. **Verify & report:** open `output/index.html` (screenshot with chromium if useful), skim the changelog, and tell the user exactly what to do before the next deadline (in CET).

## Where the truth lives (nothing important is only in chat)

- **`RUNBOOK.md`** — the detailed per-run checklist (sources, verifications, commands, sanity checks). **Start there.**
- **`state.json`** — the user's real team, chips used, free transfers, bank, cumulative Nostradamus + GoPicks + fantasy points, predictions entered (`predictions_entered` for Nostradamus, `gopicks.predictions_entered` for GoPicks — `"missed"` marks a match he never entered; matches before `gopicks.joined` auto-count as missed), `last_run`. `owned` also carries the locked `lineup` (the XI/bench/captain frozen at the round deadline, so the active-squad display never re-optimises the XI mid-round); `recommended_squad` carries `for_round` (the round its transfers target — this is what drives the deferred `owned`-advance). Read first, rewrite last.
- **`data/manual/*.json`** — Claude-curated research inputs: `fixtures.json`, `ratings_odds.json` (+ `odds_extra.json`, merged), `squads.json`, `player_stats.json` (per-90 xG/xA → intra-team shares), `lineups.json` (confirmed/predicted XI → minutes), `results.json` (actual 90' scores + `ko_advancers` for KO ties decided in ET/pens), `nostradamus.json`, `gopicks.json`, `fantasy_rules.json`, `fantasy_feed.json`.
- **`data/processed/latest/`** — newest model run (for the changelog diff); `data/raw/<date>/` — cached source pulls.
- **`config.yaml`** — budget/rules/ensemble weights/sim iters. **Verified game rules in `data/manual/{fantasy_rules,nostradamus,gopicks}.json` override config automatically.**

## Architecture (where code lives)

`src/sources/` fetch+normalize · `src/model/` ensemble + Dixon-Coles + Monte-Carlo bracket ·
`src/nostradamus/` EV-optimal scoreline · `src/gopicks/` EV-optimal scoreline under GoPicks scoring ·
`src/fantasy/` projections + ILP + transfers ·
`src/report/` jinja2 templates + renderer · `src/pipeline.py` orchestration · `src/cli.py` entry.

## Confirmed facts (verified 2026-06-05 — re-verify if a new edition/season)

- **Nostradamus:** exact 3 / outcome+one-team 2 / outcome 1 / wrong 0; **doubled R32→final**; 90-min result only; covers **all 104** matches; per-match deadlines at kickoff.
- **GoPicks** (gopicks.app; rules user-quoted 2026-06-12, coverage + deadlines user-verified on-site 2026-06-12): correct result 3 + exact home goals 1 + exact away goals 1 — the components **stack** (exact score = 5) and are independent (a wrong result can still score 1–2 via goal counts); 90-min result only; **no KO doubling**; covers all 104 matches, per-match lock at kickoff; leaderboard tiebreaker = total exact goal picks (the optimizer tie-breaks on it too). User joined 2026-06-12 → matches 1–2 missed (0 pts, in `state.json`). Its EV pick can differ from Nostradamus' — reports show both side by side; brief the user on both lists every run.
- **FIFA Fantasy:** $100M (+$5M KO); 2/5/5/3; **nation cap 3→3→4→5→6→8** by stage; free transfers: unlimited pre-lock & before R32, else 2 (MD2/MD3) and 4/4/5/6 (R16/QF/SF/F), −3 per extra; goals **GK9/DEF7/MID6/FWD5**, assist 3, CS 5/5/1/0, MID tackles+chances, FWD shots, GK/DEF goals-conceded −1 each after the first; chips: Wildcard (not MD1/R32), 12th Man, Maximum Captain, Qualification Booster (R32+), Mystery Booster (revealed R32). Feed: `play.fifa.com/json/fantasy/{players,squads,rounds}.json` (unauth).
- **Tournament:** 48 teams / 12 groups; Italy & Denmark did NOT qualify, Norway did. Opener Mexico–South Africa, 2026-06-11.
- **The pipeline auto-detects the stage** (pre, MD1–3, R32…final) from `results.json` + fixture dates and applies the right budget/nation cap/transfer window itself — keeping `results.json` current is what makes every downstream number right. During a **locked round** the report headline is your **active squad** — the real 15 in the XI you locked (from `owned.lineup`), captain included — and the next round's moves are shown separately as **upcoming** swaps (don't make them yet). On the **lock-eve/open window** the same `transfers` mode flips to *execute-now*: the post-transfer team to build for the imminent deadline. `owned` advances to that plan only once the round's deadline passes, so the file always mirrors the team you really have locked; before the R32 it switches to HOLD → full-rebuild once ties are known. The bracket sim conditions on entered group results AND actual KO outcomes.
- **Mid-round rules (exploited by the "live-round playbook"):** the captain can be moved during a live round to a starter whose match hasn't started, once the old captain's match has ended (old double forfeited → switch iff banked < E[new]); manual subs score only the incoming player; **any manual change cancels that round's auto-subs**. Each run emits a captaincy-relay ladder + blank-rescue rule on the fantasy page; captain selection prefers the earliest kickoff among near-equal premiums to keep the relay open. Since MD1 the unauth feed carries **live per-player round points** (`stats.roundPoints`, keyed by fantasy round id; verified vs official scoring 2026-06-12) — the playbook auto-resolves the captain rule to SWITCH/HOLD and tallies "XI banked N pts" for players whose current-round match is in `results.json`, so keeping results fresh also drives live fantasy tracking. Personal official total/rank still needs login → user-entered.

## Gotchas

Keep tool state OUT of this host-mounted repo (venv → `$SANDBOX_VM_STATE`). NixOS needs
`LD_LIBRARY_PATH=$HOME/.nix-profile/lib` for numpy/scipy (run.sh handles it). Knockout
predictions only unlock once teams are known — that's expected; re-run daily. The
optimizers are unit-tested (`uv run pytest`); run them after code changes.
