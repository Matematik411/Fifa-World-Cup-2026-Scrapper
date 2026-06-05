# RUNBOOK — how a (future) Claude session does a daily run

You are a fresh session. Everything you need is on disk. Work autonomously and be
decisive — the user follows the output. Target: refresh data, regenerate `output/`,
tell the user what to do before the next CET deadline.

---

## 0. TL;DR

```bash
# one-time per VM boot (disposable VM wipes the venv + nix tools, repo persists):
nix profile add nixpkgs#{python313,uv,cbc,chromium} nixpkgs#stdenv.cc.cc.lib nixpkgs#zlib
export UV_PROJECT_ENVIRONMENT="$SANDBOX_VM_STATE/fifa-wc-2026/venv" && uv sync

# every run:
#  1) refresh data/manual/*.json (see §2)   2) reconcile state.json (see §3)
./run.sh run                                # 3) fetch+model+optimize+render (idempotent)
#  4) open output/index.html, verify, brief the user (see §5)
```

`./run.sh` sets the venv path + `LD_LIBRARY_PATH` and runs `python -m src.cli run`.
Flags: `--no-fetch` (use cached feed), `--no-sim` (reuse advancement), `--date YYYY-MM-DD`.

---

## 1. Read state first

Open `state.json`. Note `last_run`, the user's **`owned`** squad (may be empty if never
confirmed), `chips_used`, `cumulative` points, and `predictions_entered`. The run
diffs against `data/processed/latest/` for the changelog, so don't delete old runs.

## 2. Refresh research → `data/manual/*.json`

This is the part **you** do with WebSearch/WebFetch (no API key needed). Overwrite each
file with today's data, keeping the SAME schema (the pipeline + `config.yaml` overrides
depend on it). Cache raw pulls under `data/raw/<today>/`. Re-pull, in priority order:

| File | Refresh cadence | What to update each run |
|---|---|---|
| `lineups.json` | **every run during tournament** | `{"teams": {"<nation>": {"confirmed": true/false, "xi": ["name",...], "out": ["name",...]}}}`. **Confirmed XIs come ~1h before kickoff** — an evening run catches them and lets you fix captaincy. `confirmed:true` → players in `xi` are nailed (0.98), others benched; `confirmed:false` → treated as predicted (0.90). Overrides everything for minutes. |
| `ratings_odds.json` + `odds_extra.json` | **every run** | Title odds + Elo move slowly; **per-match odds for the next 1–2 days' fixtures are the priority**. Put extra/sharper (Pinnacle) and later-matchday lines in `odds_extra.json` — the loader **merges** them into `ratings_odds.match_odds` (Pinnacle wins on dupes). Add odds for KO ties as teams are known. |
| `squads.json` | **every run during groups/KO** | Injuries, suspensions, rotation, penalty/FK-taker changes, `likely_xi`. Backstop for minutes when `lineups.json` isn't confirmed yet. |
| `results.json` | **after each matchday** | `{"results": {"<match_num>": [home_goals, away_goals], ...}}` — 90-minute results only. Feeds standings/advancement and scores our picks. |
| `player_stats.json` | **occasionally** | Per-90 xG/xA/shots/key-passes driving intra-team goal/assist shares. Club-season numbers — refresh pre-tournament and if a player's role/form shifts; not needed daily. |
| `fixtures.json` | when KO bracket fills | Group stage is fixed. As knockouts resolve, fill the actual teams into the KO match `home`/`away` (replace `1A`/`W74` placeholders) so predictions unlock for them. |
| `fantasy_feed.json`/players | automatic | `./run.sh run` pulls `play.fifa.com/json/fantasy/{players,squads,rounds}.json` live (prices fixed; ownership/status/points update). Nothing to do unless the endpoint changes. |
| `fantasy_rules.json`, `nostradamus.json` | rarely | Stable for 2026. Re-verify only if rules change. |
| *optional APIs* | if keys set | `.env` keys (football-data.org / API-Football / The Odds API) are optional enrichment for fixtures/lineups/odds. The tested primary path is the curated files above; APIs are a convenience, not required. |

> Fast path: launch parallel research subagents (one per file) exactly as the build did
> — see the prompts pattern. Each writes its JSON + a raw cache and returns a summary.

## 3. Reconcile the user's REAL team (`state.json`)

**Standing instruction (the user set this explicitly): assume they followed every
recommendation exactly — squad, captain, transfers, chips AND the per-match
scorelines — unless they state a deviation this session.** So:
- `state.json` has `assume_followed: true`, and the pipeline auto-syncs
  `owned` ← `recommended_squad` at the end of every run. You do **not** need to
  ask them to list their 15 each time.
- Nostradamus scoring already defaults to the recommended scoreline when
  `predictions_entered` has no explicit entry — so picks are scored as followed.
- Just ask one quick question: **"anything different from what I recommended last
  time?"** If **no** (the normal case) → proceed straight to the run. If **yes** →
  set `owned.player_ids` / `chips_used` / `predictions_entered` to what they
  actually did *before* optimizing, run, and it re-plans from their real team
  (the end-of-run sync then realigns `owned` to the new recommendation).
- The only thing the feed can't give you is their personal **fantasy points/rank**
  — ask for it if they want rank tracked; otherwise `cumulative.fantasy_points`
  stays user-entered. Nostradamus points are computed from results automatically.

Pre-lock (`stage == "pre"`) the run always emits the **optimal initial 15**;
post-lock it emits a **transfer plan** from `owned`.

## 4. Run + sanity-check

`./run.sh run` then read the log. It auto-validates (matrices sum to 1; squad legal;
a prediction per offered match). Quick checks:
- Favourites look right (Spain/France/Argentina/England top of `model.html`).
- Squad spends ~full budget, captain is a nailed premium with a good fixture.
- Predictions for the next day have sane scorelines and CET deadlines.
- `uv run pytest -q` after any code change (optimizers are unit-tested).

## 5. Verify HTML + brief the user

Screenshot to eyeball layout (optional):
```bash
chromium --headless --no-sandbox --screenshot=/tmp/i.png --window-size=1180,2400 "file://$PWD/output/index.html"
```
Then tell the user, in CET and decisively:
1. **Fantasy:** the XI + captain + any transfers/chip to play, and the deadline.
2. **Nostradamus:** the scorelines to enter for the next matches + per-match deadlines.
3. **Performance:** points since last run (from the changelog).
4. One line on what changed and why (changelog highlights).

## 6. Stage-specific notes

- **Pre-tournament:** initial 15 + captain + all of MD1's scorelines. Squad locks at the
  first kickoff (Mexico–South Africa, 2026-06-11). Wildcard can't be used MD1.
- **Group stage:** ingest yesterday's `results.json`; 2 free transfers before MD2/MD3
  (1 rollover, group only); unlimited transfers before R32 (good reshuffle window).
  Watch best-third-placed race on `model.html`.
- **Knockouts:** budget rises to $105M; nation cap relaxes (3→4→5→6→8); Qualification
  Booster unlocks (R32+); Mystery Booster revealed at R32 — re-check its effect then.
  Predictions are the **90-minute** result (a draw is a valid, often optimal pick).
  Fill resolved KO teams into `fixtures.json` so those predictions generate.

## 7. Troubleshooting

- `libstdc++ … cannot open shared object`: `LD_LIBRARY_PATH` missing — use `./run.sh`
  (it sets it) or re-add `nixpkgs#stdenv.cc.cc.lib`.
- ILP solver error: ensure `cbc` is installed (`nix profile add nixpkgs#cbc`).
- Fantasy feed 403/empty: it falls back to the last cached pull; note it to the user.
- A required `data/manual/*.json` missing → the run fails loudly; regenerate it (§2).
