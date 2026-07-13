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
| `results.json` | **after each matchday** | `{"results": {"<match_num>": [home_goals, away_goals], ...}}` — 90-minute results only. In the knockouts also fill `{"ko_advancers": {"<match_num>": "<Team>"}}` for any tie that was a draw after 90' (ET/pens decided it) — a decisive 90' KO score identifies the advancer automatically. **Keeping this file current matters doubly now: it drives stage detection (matchday/round, transfer windows, KO budget, nation caps) and conditions the bracket sim on real outcomes.** |
| `player_stats.json` | **occasionally** | Per-90 xG/xA/shots/key-passes driving intra-team goal/assist shares. Club-season numbers — refresh pre-tournament and if a player's role/form shifts; not needed daily. |
| `wc_form.json` | **every matchday (U5)** | In-tournament player form. Per player: `wc_minutes`/`wc_goals`/`wc_assists` (factual — from `results.json`) **plus real `wc_xg`/`wc_xa`/`wc_shots`** (pull from fbref/Opta/understat). The model shrinks the club-season per-90 toward these, minutes-weighted and leaning on xG over goals (finishing heat regresses), so 2 games barely move it and it grows through the run. Add every fantasy-relevant performer each matchday. The current file is a SEED (goals/mins factual, xG estimated) — replace estimates with real WC xG. The `teams` block (`wc_xg_for`/`wc_xg_against`/`opponents` per nation) feeds U2 team form. |
| `fixtures.json` | when KO bracket fills | Group stage is fixed. As knockouts resolve, fill the actual teams into the KO match `home`/`away` (replace `1A`/`W74` placeholders) so predictions unlock for them. |
| `fantasy_feed.json`/players | automatic | `./run.sh run` pulls `play.fifa.com/json/fantasy/{players,squads,rounds}.json` live (prices fixed; ownership/status/points update). Since MD1 the feed carries **live per-player round points** (`stats.roundPoints`, verified vs official scoring 2026-06-12); the pipeline reads them automatically — captain SWITCH/HOLD verdict + "XI banked N pts" on the fantasy page once matches in `results.json` finish. Nothing to do unless the endpoint changes. |
| `fantasy_rules.json`, `nostradamus.json`, `gopicks.json` | rarely | Stable for 2026. Re-verify only if rules change. |
| *optional APIs* | if keys set | `.env` keys (football-data.org / API-Football / The Odds API) are optional enrichment for fixtures/lineups/odds. The tested primary path is the curated files above; APIs are a convenience, not required. |

> Fast path: launch parallel research subagents (one per file) exactly as the build did
> — see the prompts pattern. Each writes its JSON + a raw cache and returns a summary.

## 3. Reconcile the user's REAL team (`state.json`)

**Standing instruction (the user set this explicitly): assume they followed every
recommendation exactly — squad, captain, transfers, chips AND the per-match
scorelines — unless they state a deviation this session.** So:
- `state.json` has `assume_followed: true`. The pipeline keeps `owned` truthful
  **without drift**: during a locked round `owned` stays = the real locked 15 (with
  its frozen `lineup`), and only advances to a round's transfer plan once that round's
  deadline passes (the pre-lock initial 15 and unlimited-window rebuilds sync
  immediately, being buildable now). You do **not** need to ask them to list their 15
  each time.
- Nostradamus scoring already defaults to the recommended scoreline when
  `predictions_entered` has no explicit entry — so picks are scored as followed.
- **GoPicks** (Sentora partners league on gopicks.app, joined 2026-06-12) works the
  same way via `state.gopicks.predictions_entered`: absent = followed the GoPicks
  recommendation (NOT the Nostradamus one — the picks can differ), `"<num>": "H-A"`
  = stated deviation, `"missed"` = not entered. Matches dated before
  `state.gopicks.joined` auto-score as missed (matches 1–2). Points are recomputed
  from results each run; only `points_official`/`rank` (his leaderboard standing)
  are user-entered, like fantasy points.
- **Do not ask for confirmation — proceed straight to the run** (the user confirmed
  2026-06-11 he agrees with all decisions and will state any deviation unprompted in
  his message). If he does state one → set `owned.player_ids` / `chips_used` /
  `predictions_entered` to what they actually did *before* optimizing, run, and it
  re-plans from their real team (the end-of-run sync then realigns `owned` to the
  new recommendation).
- **`chips_used` entries are `{"name": ..., "round": ...}`** (add the round a chip was
  played in — the report uses it to tell a chip LIVE this round, e.g. Maximum Captain
  suppressing the captaincy relay, from one spent earlier; legacy plain strings still
  parse but lose that). Add a played chip in the SAME session it locks.
- **Never ask about league standings (user said so, 2026-06-12):** he won't share his
  points/positions in any of the three leagues and wants pure best-EV recommendations,
  not rank-aware strategy (no differential/variance plays based on the leaderboard).
  Leave `cumulative.fantasy_points`, `gopicks.points_official` and `gopicks.rank` as
  they are; Nostradamus/GoPicks points are recomputed from results automatically and
  the fantasy XI tally comes from the live feed.

During a **locked round** the headline squad/XI/captain is your **active team** — the
real 15 in the XI you locked at the deadline (`owned.lineup`), not a re-optimised one —
and the next round's moves are shown separately as **upcoming** swaps (HOLD them: team
news until the lock can change the pick, and a free transfer rolls over). On the
**lock-eve / open window** the same `transfers` mode flips to *execute-now* (the
post-transfer team to build before the imminent deadline). `owned` advances to that plan
only when the round's deadline passes, so it always mirrors the team you really have
locked. Transfer modes by window (automatic, from the stage engine): `initial` (pre-lock
optimal 15) → `transfers` (N free per round; headline = active team while the round is
locked, execute-now once its window is open) → `hold` (unlimited window open but R32 ties
unknown — wait) → `rebuild` (unlimited window, ties known: full reset to the optimum, no
hits).

## 4. Run + sanity-check

`./run.sh run` then read the log. It auto-validates (matrices sum to 1; squad legal;
a prediction per offered match). Quick checks:
- The `Stage:` log line is right (MD1/2/3, R32… — derived from `results.json` +
  dates; a warning about matches missing results means refresh `results.json`).
- Favourites look right (Spain/France/Argentina/England top of `model.html`).
- Squad spends ~full budget, captain is a nailed premium with a good fixture.
- Predictions for the next day have sane scorelines and CET deadlines.
- `uv run pytest -q` after any code change (optimizers + stage engine are unit-tested).

## 5. Verify HTML + brief the user

Screenshot to eyeball layout (optional):
```bash
chromium --headless --no-sandbox --screenshot=/tmp/i.png --window-size=1180,2400 "file://$PWD/output/index.html"
```
Then tell the user, in CET and decisively:
1. **Fantasy:** the XI + captain + any transfers/chip to play, and the deadline.
   **Transfer timing (verified 2026-06-12):** advise confirming transfers shortly
   before the round lock, never right away — a confirmed transfer is **irreversible**
   (FIFA in-app rules: "Once confirmed a transfer cannot be reversed"; undoing one
   later is a new transfer, i.e. a likely −3 hit), prices are **fixed** (no FPL-style
   price-rise race) and 1 unused free rolls over within the group stage — so
   confirming early has zero upside and forfeits the option value of pending team
   news (lineups, knocks). Present the plan when it's computed; tell the user to
   execute it on the lock-eve run once that round's final reveals are in.
2. **Live-round playbook:** the **line-up fixes** (each player locks at his OWN
   kickoff, so before then you can freely bench a starter who won't play — injury,
   rotation, a back-up keeper — for a bench player whose match also hasn't started;
   the run lists out→in + per-player deadline), the captaincy relay ("if your
   captain ends on ≤N pts, move the armband to X before his kickoff") and the
   blank-rescue sub rule (the after-match-ended case) — the actions the user can
   take between daily runs. Remind them any manual change cancels that round's
   auto-subs, so once they touch the XI they should set every non-player by hand
   (don't rely on the auto-fill). Do NOT tell the user a not-yet-played starter is
   un-subbable — that was a past misread; the pre-kickoff line-up edit always works.
   **Free-roll bench activation (downside-protected; co-developed with the user 2026-06-16).**
   A starter whose match is in a LATER run-window (typically the next day) can be
   benched for FREE right now — he scores nothing this window anyway — to start a
   bench player whose match IS in this window, banking that player's points at zero
   cost. On a later daily run, restore the parked starter by manual-subbing out whoever
   TRULY scored lowest (results now known). This beats committing the forfeit up-front:
   if the activated player hauls, keep him and drop a low scorer; if he blanks, drop HIM
   back for the parked starter → baseline, no harm. Safe because the user runs ≥1h before
   each day's first match (a reliable restore checkpoint); mind formation legality at each
   step and don't forget the restore — a later starter left benched past his kickoff
   forfeits his points. Ex 2026-06-16: bench Bruno Fernandes (Portugal, plays Jun 17) to
   start Otamendi (Argentina, plays tonight, E 3.2 > 2); next run, bring Bruno in for the
   lowest banked starter — or for Otamendi himself if he blanked.
3. **Nostradamus:** the scorelines to enter for the next matches + per-match deadlines.
4. **GoPicks:** the scorelines to enter at gopicks.app for the same matches — list them
   separately and call out any match where the GoPicks pick differs from Nostradamus
   (different scoring → different EV optimum; entering the wrong league's pick costs EV).
5. **Performance:** points since last run (from the changelog), both leagues.
6. One line on what changed and why (changelog highlights).

**Do NOT auto-commit daily runs** (user instruction 2026-06-12): leave the run's
artifacts (`data/manual/*`, `output/*`, `state.json`) uncommitted — the user reviews
and commits/pushes them himself from the host (the push is what deploys `output/` to
GitHub Pages; the sandbox has no SSH key anyway). Commit only when you change the
**project itself** (code, templates, runbook/docs, rules files) — set the identity
first (`git config user.name "Matematik411" && git config user.email
"nejc.zajc@aflabs.com"`; the VM has none) and keep such project commits free of run
artifacts.

## 6. Stage-specific notes

- **Pre-tournament:** initial 15 + captain + all of MD1's scorelines. Squad locks at the
  first kickoff (Mexico–South Africa, 2026-06-11). Wildcard can't be used MD1.
- **Group stage:** ingest yesterday's `results.json` **first** — the stage engine reads
  it. Free transfers (2 before MD2/MD3), budget and caps are applied automatically per
  the verified rules. Watch the best-third-placed race on `model.html`.
- **MD3 → R32 window:** transfers before the R32 are **unlimited**; the run says HOLD
  until ties are known, then switches to a full **rebuild** recommendation. Make the
  rebuild only once — the morning of the first R32 game is the sweet spot.
- **Knockouts:** budget ($105M), nation caps (3→3→4→5→6→8) and free transfers
  (4/4/5/6 before R16/QF/SF/F) are applied automatically from the detected stage.
  Your jobs that remain manual: (1) fill resolved KO teams into `fixtures.json`
  `home`/`away` so predictions + player projections unlock for those ties, (2) add
  per-match odds for known ties to `odds_extra.json` (feeds both optimizers), (3) keep
  `results.json` current incl. `ko_advancers` for ET/pens ties, (4) check the Mystery
  Booster's revealed effect at R32. Predictions are the **90-minute** result (a draw
  is a valid, often optimal pick) and are worth double.

## 6.5 One-time TODOs (do during the named run, then delete the line)

(none right now — both 2026-06-12 TODOs done: `results.json` exists, GoPicks rules
verified on-site by the user, and the feed's live per-round points are wired into
the playbook — see §2 fantasy_feed row.)

## 7. Troubleshooting

- `libstdc++ … cannot open shared object`: `LD_LIBRARY_PATH` missing — use `./run.sh`
  (it sets it) or re-add `nixpkgs#stdenv.cc.cc.lib`.
- ILP solver error: ensure `cbc` is installed (`nix profile add nixpkgs#cbc`).
- Fantasy feed 403/empty: it falls back to the last cached pull; note it to the user.
- A required `data/manual/*.json` missing → the run fails loudly; regenerate it (§2).
