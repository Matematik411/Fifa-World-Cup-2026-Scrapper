# UPGRADES.md — upgrade backlog & playbook

This is a **living, stateful work queue** for improving the forecasting model. It is meant to be
driven by the command:

> **"upgrade the project according to the upgrades.md"**

run **after MD2** (the first time) and **again before each knockout round** (R32, R16, QF, SF, Final).
The same command is run at each of those points; the executing agent figures out *what is due this time*
from the current tournament stage + the Status tracker below, does that work, validates it, and updates
this file. Read this whole file before doing anything.

---

## 0. How an upgrade session works (procedure — follow in order)

1. **Setup the toolchain.** The VM is disposable; follow CLAUDE.md §"How to do a run" step 1
   (`nix profile add …`, `export UV_PROJECT_ENVIRONMENT=…`, `uv sync`). Confirm `uv run pytest` is green
   *before* you change anything (record the baseline).
2. **Detect the current stage.** Read `state.json` + `data/manual/results.json` + `data/manual/fixtures.json`
   to determine where the tournament is (pre / MD1–3 / R32 / R16 / QF / SF / Final). The pipeline already
   auto-detects stage in `src/pipeline.py` — reuse that logic, don't reinvent it.
3. **Select what's due.** Cross-reference the stage against the **Phasing** table (§3) and the **Status
   tracker** (§4). Do: (a) any one-time items whose phase has arrived and are still `TODO`; (b) every item
   in **Recurring tasks** (§5) whose machinery now exists.
4. **Implement** one item at a time. Keep `./run.sh run` working and the daily pipeline intact. Do NOT
   touch the daily research inputs (`data/manual/*.json` curated by the daily run) except where an item
   explicitly adds a new field/source. Respect the **Golden rules** (§2).
5. **Validate (the gate).** Run `uv run pytest`. For any change that affects model numbers, run the
   **backtest** (item U1) and record the **out-of-sample delta**. *Never ship a model change that
   regresses out-of-sample accuracy.* A neutral delta is acceptable only with a stated reason.
6. **Update this file.** Flip the item's status in §4, append a dated entry to the **Run log** (§7) with
   what you did + the backtest delta + any follow-ups discovered. If you defer or re-scope an item, say why.
7. **Report to the user** in chat: what you built, the measured improvement, what's queued for the next run.

If a one-time item is only partially completed, set it `IN-PROGRESS` and leave a precise "resume here" note
in the Run log so the next session can continue it.

---

## 1. Project context (for a session with no prior memory)

Re-runnable FIFA World Cup 2026 forecasting system. **One shared match model** feeds **three EV
optimizers** → **five HTML reports** in `output/` (the only thing the human reads):

- `src/model/` — ensemble team strength → Dixon-Coles scorelines → Monte-Carlo bracket.
- `src/nostradamus/`, `src/gopicks/`, `src/fantasy/` — the three optimizers.
- `src/report/`, `src/pipeline.py`, `src/cli.py` — rendering + orchestration + entry.

Read **CLAUDE.md** and **RUNBOOK.md** first. The human *follows the recommendations*, so output must stay
decisive and coherent.

---

## 2. Golden rules (do not violate)

- **Gate everything on the backtest.** No model-affecting change ships without an out-of-sample number
  (item U1). Add signals **one at a time** so each one's effect is measurable. No big-bang.
- **Pure best-EV only — everywhere (the one exception was revoked 2026-07-09).** No rank-aware /
  differential / catch-up strategy in any league (user declined 2026-06-12). GoPicks was briefly the
  sanctioned exception (2026-07-02 → 2026-07-09: real prizes, top-3 only → U9 podium simulator +
  decorrelation tilts); at the QF the user closed it — podium out of realistic reach, goal is now best
  final placement = pure best-EV — and the U9 machinery was **deleted** (podium.py, pipeline hook +
  auto-flips, report card, tests). Do not rebuild it without an explicit user re-open. Variance work is
  allowed *only* where EV is inherently distributional (captain ceiling, exact-score mode).
- **Verified rules override config.** `data/manual/{fantasy_rules,nostradamus,gopicks}.json` are the source
  of truth for game rules; `config.yaml` is overridden by them. Read rules from there; never hardcode.
- **Keep the daily run working.** `./run.sh run` must stay green and idempotent; `uv run pytest` must pass.
- **One shared match model.** All three optimizers must draw from the same scoreline distribution — keep
  them coherent (e.g. fantasy clean-sheet/goals-conceded must come from the same model as Nostradamus).
- **Keep tool state out of the host repo** (venv → `$SANDBOX_VM_STATE`); NixOS needs
  `LD_LIBRARY_PATH=$HOME/.nix-profile/lib` (run.sh handles it).

---

## 3. Phasing — what to do at each trigger

| Trigger (when the command is run) | One-time items due (in order) | Plus recurring (§5) |
|---|---|---|
| **After MD2** (now; this is also "before MD3") | **U1** (backtest, FIRST) → **U6+U3** (per-match minutes incl. light dead-rubber detector) → **U5** (player form, manual WC xG) → **U8** (freshness tags) → **U2** (team form — mechanism + small weight) | R1, R2 |
| **Before R32** (group stage finished, ties known) | **U4** (booster engine + R32-burner + lookahead), **U7** (correlation) | R1, R2, R3 |
| **Before R16** | **U9** (GoPicks podium sim — user re-opened the prize-league question) + **U10** (KO extra-time fantasy adjustment) — both scoped & shipped 2026-07-04 | R1, R2, R3 |
| **Before QF / SF / Final** | — | R1, R2, R3 |

Rationale for the ordering (decided 2026-06-24 — so future sessions don't re-litigate it):
- **U1 first** — the measuring stick for every model-affecting item, and it has a known target to diagnose
  on day one (MD1 over-predicted clean sheets, ~14 vs 7 actual — see the calibration-watch memory).
- **Player-side before team-side (the "market-override" reframing).** Curated per-match odds OVERRIDE team
  strength for that fixture — `Forecast.match_matrix` (`src/model/forecast.py`) tries `_market_lambdas`
  first and only falls back to rating-derived λ when no odds exist. So **team form (U2) barely moves the
  next-match scoreline**; it only moves the KO `advance_prob` matrix (pure strength, `forecast.py`), matches
  with no curated odds, and everything downstream of the bracket sim (title/deep-run probs, fantasy
  `horizon`, QB EV). **Player projections (U5/U6) are NOT market-shielded** → they move fantasy/captain/
  transfer calls directly. Near-term ROI order is therefore U6+U3 → U5 → U8 → U2, not the old U2-first.
- **U6 and U3-rotation are one job: per-match minutes.** Today minutes is a single scalar reused for every
  fixture (`projections.py` applies one `mins` to all of a player's matches), which is exactly why the model
  can't rest a starter for one match (the global-min-prob pain). A per-match P(start)/P(60+)/sub-time
  distribution is the shared fix for U6 (60′ CS eligibility, appearance, captain risk) AND U3-rotation
  (rested-in-a-dead-rubber). Build them together.
- **U3 group-stakes — light auto detector THIS cycle.** MD3 is the next round and many teams have already
  clinched/are out, so the closing window is now. Build a *light* automatic dead-rubber detector (standings
  → clinched/eliminated → modest intensity multiplier + rotation-prob bump), not a heavy stakes model. The
  manual dead-rubber handling stays as the fallback if a number looks off.
- **U2 — build the mechanism, expect the win in the bracket, not next-match RPS.** Gate hard on U1's
  ratings-only mode; keep the form weight small. Watch the calibration trap: the strength→goals regression
  in `ensemble.py` re-fits `beta` to match the market, so form must enter as an offset AFTER calibration (or
  re-fit on a form-frozen strength) — never both, or the fit silently re-absorbs it.
- **U4/U7 before R32, not now** — they target the knockouts and can't be exercised until the rebuild +
  chips are live. Building them earlier means testing against hypotheticals.

---

## 4. Status tracker (the executing agent keeps this current)

| ID | Item | Phase | Status | Depends on |
|----|------|-------|--------|------------|
| U1 | Backtesting & calibration harness | After MD2 | DONE | — |
| U2 | Team form, opponent-adjusted | After MD2 | DONE | U1 |
| U3 | Stakes/intensity + rotation→minutes | After MD2 | DONE | U1 |
| U4 | Booster engine: schedule + lookahead + R32-burner | Before R32 | DONE | U1 |
| U5 | Player form (WC xG + shrinkage) | After MD2 | DONE | U1 |
| U6 | Minutes as a distribution | After MD2 | DONE | — |
| U7 | Player-level correlation (CS stacking, captain ceiling) | Before R32 | DONE | — |
| U8 | Data freshness / confidence tags | Anytime | DONE | — |
| U9 | GoPicks podium simulator (rank-aware endgame) | Before R16 | REMOVED 2026-07-09 | — |
| U10 | KO extra-time adjustment for fantasy projections | Before R16 | DONE | — |
| U11 | Pick-of-record auto-freeze (drift-proof scoring) | Before QF | DONE | — |

Statuses: `TODO` · `IN-PROGRESS` (leave a resume note in §7) · `DONE` · `DEFERRED` (say why in §7).

**Session decisions (2026-06-24, after MD2 — see §3 rationale & §7 run log):**
- Reprioritized to **U1 → U6+U3 → U5 → U8 → U2** (the market-override reframing).
- **U6 + U3-rotation are implemented together** as one *per-match* minutes distribution; **U3 group-stakes**
  ships as a *light* automatic dead-rubber detector.
- **U5/U2 form uses manually-curated WC xG** — new `data/manual/wc_form.json` (per-player WC minutes/xG/xA/
  shots + per-team opponent-adjusted xG for/against) and an optional per-match `xg` map in `results.json`.
  This adds a daily research step (document it in RUNBOOK §2 when U5 lands).
- **U1 ships in two modes** — *as-shipped* (market-where-available; grades realized league performance) and
  *ratings-only* (forces rating-derived λ; isolates the model so U2/U5 can be A/B'd) — and scores the
  already-persisted `data/processed/<date>/predictions.json` as a leak-free walk-forward record (use each
  match's pick from the last run STRICTLY BEFORE its kickoff).

---

## 5. Recurring tasks (run EVERY upgrade session, once the machinery exists)

- **R1 — Re-backtest & re-tune.** Re-run U1 on the latest `results.json`. If new ensemble weights / config
  values improve out-of-sample accuracy, update `config.yaml` and log the delta. (Skip until U1 exists.)
- **R2 — Refresh form.** Recompute team (U2) and player (U5) form from the latest results before the
  upcoming round. (Skip until U2/U5 exist.)
- **R3 — Re-plan chips & transfers for the upcoming round** (U4). Concretely:
  - *Before R32:* build the **R32-burner** squad (horizon weight ≈ 0) optimized for R32 points + QB, since
    the Wildcard is queued for R16. Confirm via bracket sim it beats "durable + WC held."
  - *Before R16:* play the **Wildcard** → build the **durable KO core** for QF→Final.
  - *Before QF / SF / Final:* deploy the remaining chips per plan (Max Captain, 12th Man, Mystery) and
    re-resolve one-chip-per-round contention as the bracket narrows.
  - (Skip until U4 exists; until then the daily run's existing chip_advice + the hand-built plan apply.)

The current hand-built chip plan (encode it into U4, don't lose it): **hold all 5 for the KO** → QB@R32,
Wildcard@R16, then Max Captain / 12th Man / Mystery across QF/SF/Final. Mystery is revealed at R32 and may
contend with QB for the R32 slot — re-evaluate then.

---

## 6. Work items (detail)

### U1 — Backtesting & calibration harness  *(FOUNDATION — build first)*
- **Goal:** measure model quality on already-played matches; make weight-tuning empirical.
- **Why:** today the only "calibration" is the strength→goals slope fit to **market odds**
  (`src/model/ensemble.py`) — there is **no evaluation against actual results**.
- **Where:** new `src/eval/`. Ground truth: `data/manual/results.json` (+ `ko_advancers`). Outputs to
  score: `data/processed/<date>/{forecast_matches,predictions,advancement,fantasy_squad}.json`.
- **Approach:** two complementary layers.
  1. **Realized-scoring backtest (build first — leak-free, no re-simulation).** We already persist a daily
     pre-kickoff snapshot in `data/processed/<date>/predictions.json` since 2026-06-11. For each played
     match, take the pick from the **last run strictly before its kickoff** and score it vs the result →
     Nostradamus pts, GoPicks pts (+ exact-goal tiebreak), fantasy captain hit-rate / XI points. This is
     genuine out-of-sample and is exactly the realized performance the user cares about.
  2. **Counterfactual model backtest (the A/B harness).** Re-fit/predict on prior-matchday data only and
     score vs actual. Metrics: **RPS** (scorelines), **Brier/log-loss** (1X2), **MAE + reliability curve**
     (goals + clean-sheet predicted-vs-actual). Run it in **two modes** — *as-shipped* (market-where-
     available) and *ratings-only* (force rating-derived λ, ignore match odds) — because as-shipped mostly
     grades the market, while ratings-only isolates the strength model so U2/U5 produce a clean delta.
  Emit `output/backtest.html` (or extend `model.html`). **Lead with the reliability/CS-calibration view** —
  we have a known target (MD1 clean sheets ~14 expected vs 7 actual) and U6/U3 are judged against fixing it.
- **Gotchas:** strict walk-forward (no leakage) — a same-date *re-run* after results land overwrites the
  snapshot with post-hoc picks, so key on run_date < kickoff, not on the latest snapshot. Counterfactual
  mode needs prior odds snapshots (check `data/raw/<date>/`); if absent, ratings-only is the honest fallback
  (and is the correct mode for isolating model changes anyway). Sample only firms up through the group stage.
- **Done-when:** one command reproduces all metrics in both modes; any new signal can be toggled and A/B'd
  out-of-sample; the realized-scoring table matches `state.cumulative` (sanity cross-check).

### U2 — Team form, opponent-adjusted
- **Goal:** update team strength with in-tournament (and lightly, recent pre-tournament) results.
- **Why:** strength priors are pre-tournament; form is currently ignored.
- **Reality check (market-override):** because curated per-match odds override strength for that fixture,
  U2's measurable payoff is in the **bracket sim** (KO `advance_prob`, title/deep-run probs) and hence
  **fantasy `horizon` + QB EV**, plus odds-less matches — NOT the next-match scoreline. Build the mechanism;
  judge it on ratings-only RPS/log-loss and on advancement calibration, and keep the weight small.
- **Where:** `src/model/ensemble.py` (strength prior + the offset), `src/model/teams.py`, a small new form
  module.
- **Approach:** rolling **opponent-adjusted attack/defense from manually-curated WC xG** (chosen 2026-06-24
  — per-match team xG-for/against in `data/manual/wc_form.json` / `results.json`; beating Curaçao ≠ beating
  France), shrunk hard, applied as an **Elo-style offset to the strength prior AFTER market calibration**.
  Complementary free cross-check: **results-vs-expectation** (actual goals vs the model's pre-match expected
  goals, already persisted) — useful to sanity-check the xG-driven offset and as a fallback when xG is thin.
- **Gotchas:** (a) **double-counting twice over** — books already price form (so enter form via the prior,
  *or* shift the model-vs-market blend as sample grows, not both), AND the `ensemble.py` regression re-fits
  `beta` to the market (so add form as a post-calibration offset, or re-fit on a form-frozen strength —
  never let the fit re-absorb it). (b) small sample → **shrink** (2 games barely moves it). (c) friendlies:
  near-zero weight on output; fitness/minutes only.
- **Done-when:** backtest (U1, ratings-only mode) shows out-of-sample RPS/log-loss improvement vs current.

### U3 — Stakes/intensity + rotation→minutes  *(built together with U6 — see "per-match minutes")*
- **Goal:** condition matches on what each team needs, and model end-of-group rotation.
- **Why:** dead rubbers lower intensity *and* rest starters; must-win games don't. Rotation today is only a
  **manual `rotation_risk` flag** in `src/fantasy/projections.py`, and minutes is a single scalar reused for
  every fixture (so a starter can't be rested for one match — the global-min-prob pain).
- **Where:** `src/pipeline.py` (standings → qualification state), `src/model/forecast.py` (intensity on
  expected goals), `src/fantasy/projections.py` (rotation → the per-match minutes distribution of U6).
- **Approach (decided 2026-06-24):**
  - **Rotation → per-match minutes (primary, reusable in KO).** Derive each team's qualification state from
    standings (clinched-top / clinched-through / needs-result / eliminated). Feed it, with the manual flag,
    into U6's **per-match** P(start)/P(60+) so a clinched team's key starters can be rested in the dead
    rubber while staying nailed elsewhere. This is the half that retires the manual overrides.
  - **Light auto dead-rubber detector (group-stakes, this cycle only).** A *modest* intensity multiplier on
    expected goals when both teams are clinched/eliminated (lower stakes → fewer goals / more rotation). Keep
    it light and gated on U1; the manual dead-rubber handling remains the fallback if a number looks off.
- **⚠ Closing window:** the group-position-intensity half only applies to **MD3** (KO is uniform max
  intensity) and MD3 is imminent — hence "light auto detector now," not a heavy stakes model.
- **Done-when:** rotation auto-derived per-match (not a single hand-set scalar); MD3-type matches show
  improved backtest accuracy (or at least no regression with the manual flag removed).

### U4 — Booster engine: schedule + multi-round lookahead + R32-burner  *(before R32)*
- **Goal:** schedule all 5 chips jointly with transfers; exploit the R32/Wildcard/QB synergy.
- **Why:** `chip_advice` (`src/fantasy/transfers.py`) evaluates each chip's "best round" **in isolation**
  (e.g. suggests Max Captain for "R32/R16" even when those are taken). Multi-round transfer lookahead is
  **absent**.
- **Where:** `src/fantasy/transfers.py` (`chip_advice`, `qual_booster_ev`), `src/fantasy/optimizer.py`,
  `src/pipeline.py` (transfer modes incl. the unlimited R32 rebuild), `src/model/bracket.py`.
- **Approach:**
  - Joint **chip-schedule + multi-round transfer lookahead** across remaining rounds (don't buy a player
    you'll free-drop at the unlimited R32 rebuild; resolve one-chip-per-round contention).
  - **R32-burner synergy:** when the Wildcard is reserved for R16, set the R32 free-rebuild objective to
    **horizon weight ≈ 0** and optimize **R32 points + QB advancement** only (the R16 Wildcard then builds
    the durable core). Verify via bracket sim that "R32-burner + WC@R16" beats "durable + WC held" — the
    burner **commits the WC to R16** and loses option value, so quantify the trade.
  - **QB EV jointly** = `Σ per-match pts + 2·Σ P(advance)` over the XI (extend `qual_booster_ev`, feed it
    into **squad selection**, not just chip timing). Note QB pulls toward favorites (advancement) while
    points pull toward open games — balance both.
- **Constraints:** nation cap **3 at R32** (rises 4/5/6/8), **+$5M KO budget** at R32, **60′ clean-sheet**
  rule, valid formation/bench. **Mystery** is revealed at R32 (hint: clean-sheet-related) and contends with
  QB for the single R32 chip slot — surface that the moment it's revealed.
- **Done-when:** pipeline emits a forward chip+transfer schedule, and the R32 build switches to burner-mode
  when WC is queued for R16.

### U5 — Player form (WC xG, done right)
- **Goal:** augment the club-season per-90 baseline with in-tournament form.
- **Why:** `data/manual/player_stats.json` is **club-season 2025-26** per-90 only; WC form is ignored
  (the model currently treats a striker's hot streak as unsustainable, which is right *directionally* but
  should be updated on evidence). NB: player projections are **not** market-shielded, so this moves fantasy
  decisions directly — it is the highest-ROI *model* upgrade after U1.
- **Data source (decided 2026-06-24): manually-curated WC xG.** New `data/manual/wc_form.json`, refreshed
  each matchday. Proposed schema:
  ```json
  {"updated_at": "YYYY-MM-DD", "confidence": "...", "sources": [...],
   "players": {"<nation>": [{"name": "...", "wc_minutes": 180, "wc_xg": 0.9, "wc_xa": 0.4,
                             "wc_shots": 7, "wc_sot": 3, "wc_goals": 2, "wc_assists": 0,
                             "role_change": "now lone #9 / on pens", "notes": "..."}]},
   "teams": {"<nation>": {"wc_xg_for": 3.1, "wc_xg_against": 1.2, "matches": 2,
                          "opponents": ["...", "..."]}}}
  ```
  The `teams` block feeds U2; the `players` block feeds U5. Add a RUNBOOK §2 row when this lands.
- **Where:** `src/fantasy/projections.py` (consumes per-90 → `exp_next`/`exp_avg`/`horizon`); a new form
  module + a `WCFormIndex` loader alongside `StatsIndex`.
- **Approach:** update per-90 via **Bayesian shrinkage** of the club-season rate toward the WC rate, weighted
  by **WC minutes/shots** (2 games barely moves it; by R16 it moves more). **Separate usage/role change
  (sticky, predictive)** — new penalty taker, now the lone #9, more shots (lift the rate/shot-volume) —
  **from finishing heat (noisy, regresses)** — shrink goals-over-xG hard toward xG. Friendlies → minutes only.
- **Done-when:** backtest (realized-vs-EV for fantasy + Nostradamus) improves out-of-sample.

### U6 — Minutes as a distribution  *(built together with U3-rotation)*
- **Goal:** replace scalar `minutes_prob` with a **per-match** `P(start)` / `P(60+)` / sub-time distribution.
- **Why:** drives appearance points, **60′ clean-sheet eligibility** (today approximated as `cs_prob *
  minutes_prob`), and captain risk; and making it **per-match** is what lets U3 rest a starter for one
  fixture — the shared fix for the global-min-prob pain.
- **Where:** `src/fantasy/projections.py` (the scalar `m["mins"]` applied in `_player_match_ep` becomes a
  per-fixture draw; `_minutes_prob` returns a small distribution, conditioned on U3's qualification state).
- **Done-when:** projections use the per-match distribution; CS eligibility respects the 60′ threshold
  probabilistically; a clinched team's starter can show reduced minutes in the dead rubber only.

### U7 — Player-level correlation in fantasy  *(before R32)*
- **Goal:** model that a team's clean sheet correlates across DEF+GK, its goals across attackers, and
  captain+teammates covary.
- **Why:** per-player EVs are currently independent → can't value **clean-sheet defensive stacking** or
  estimate true **ceiling** for the Max-Captain decision.
- **Where:** `src/fantasy/projections.py` / `src/fantasy/optimizer.py`. Reuse the joint scoreline
  distribution — Dixon-Coles low-score correlation already exists (`src/model/dixon_coles.py`).
- **Done-when:** optimizer can value a defensive stack and report a ceiling (not just mean) per lineup.

### U8 — Data freshness / confidence tags  *(anytime, cheap)*
- **Goal:** surface per-input `as_of` + confidence in the reports; flag stale inputs (e.g. `player_stats`
  is pre-tournament).
- **Where:** `src/report/`, the loaders in `src/sources/`.
- **Done-when:** each report shows input freshness; stale/low-confidence inputs are visibly flagged.

### U9 — GoPicks podium simulator  *(REMOVED 2026-07-09 — user reverted GoPicks to pure best-EV; kept for history)*
- **Goal:** answer "when do I stop playing best-EV in the prize league?" with numbers.
- **Why:** GoPicks pays top-3 only; trailing + correlated picks = frozen gap. Decorrelation costs EV but
  buys gap variance; whether that trade is +P(top-3) depends on deficit, matches left and rival behavior.
- **Where:** `src/gopicks/podium.py`; pipeline hook after the GoPicks optimize step; card on
  `predictions.html`. Reads `state.gopicks.{points_official, rank, leaderboard_ahead}`.
- **How:** MC over remaining matches (real matrices for known ties; tightest-known proxies for later
  rounds). Rivals = q·EV-picker + (1−q)·(public favorite-modal + scatter), q sensitivity-gridded.
  Strategies are per-match rules: best-ev / goal-tilt / tilt2-45/55/65 (flip to 2nd-likely outcome when
  favorite < threshold) / tilt2-all (ceiling). Paired sampling (same worlds per strategy). Verdict rule:
  switch only if P(top-3) gains ≥1.3× AND ≥+1.5pp vs best-ev.
- **Approximations (stated):** unknown ranks 4–8 interpolated; ~5 chasers just behind; far field ignored;
  final-total ties split (real tiebreaker = exact-goal count, unknowable for rivals).

### U10 — KO extra-time adjustment for fantasy projections  *(before R16)*
- **Goal:** stop projecting KO fantasy off a 90'-only window. FIFA Fantasy scores ET (verbatim "not
  including shootouts"); Nostradamus/GoPicks resolve at 90' and stay untouched.
- **Where:** `src/fantasy/projections.py` `_et_adjust` inside `_team_match_env`; knob
  `model.ko_et_goal_factor` (default 0.28 ≈ 30' × ~0.85 pace).
- **How:** attack λ ×(1 + P(draw@90)·f); clean sheet −= P(0-0@90)·P(opp scores in ET); opponent-goals
  marginal mixed one-goal-up with the same conditional weight (drives GK/DEF concession malus + GK saves).
  Effect scales with tie tightness — exactly the matches where the 90' CS was optimistic (memory:
  wc2026-fantasy-extra-time). Scoreline metrics untouched by construction; validated by unit tests
  (`tests/test_et.py`) + face validity, like U5/U6.

### U11 — Pick-of-record auto-freeze  *(before QF; bookkeeping, no model numbers)*
- **Goal:** make the scored picks-of-record drift-proof without manual freezing.
- **Why:** played-match scoring replays the CURRENT run's recommendation unless an explicit
  `predictions_entered` entry exists — so a same-day re-run with fresh odds silently rewrites history
  (M95's intra-day 1-0→2-0 flip, 2026-07-07; the manual counter-practice was "freeze knife-edge picks
  at brief time", memory wc2026-freeze-knife-edge-picks).
- **How:** `pipeline._freeze_imminent_picks` — at the end of every run, each unplayed match kicking off
  before tomorrow 06:00 CET gets its recommended scoreline `setdefault`-ed into BOTH leagues'
  `predictions_entered` (user deviations / "missed" / earlier freezes always win; already-kicked-off
  matches are never frozen post-hoc). The user enters every briefed pick right after the run, so the
  frozen entry = what's really on the sites under assume-followed.
- **Done-when:** `tests/test_freeze.py` covers tonight-vs-tomorrow cutoff, deviation preservation,
  no post-hoc freezing, and first-freeze-wins on same-day re-runs. DONE 2026-07-09.

---

## 7. Run log (append-only — newest at top)

> Template: `### <date> — <stage> — <agent>` then bullets: items touched, status changes, **backtest delta**,
> follow-ups / resume-notes.

### 2026-07-09 — before QF (R16 complete) — U9 REMOVED, U11 shipped, R1/R2/R3 run
- **U9 (GoPicks podium simulator) REMOVED on user order** — GoPicks is pure best-EV permanently (his call:
  podium unrealistically far; goal = best final placement). Deleted `src/gopicks/podium.py`,
  `_apply_podium_flips` + the `auto_flips`/`leaderboard_ahead` state fields, the predictions.html card and
  its tests; §2 golden rule rewritten (NO sanctioned rank-aware league anymore). pytest 93 → 82.
- **U11 (pick-of-record auto-freeze) DONE** — `pipeline._freeze_imminent_picks` + `tests/test_freeze.py`
  (see §6). First live exercise: M97 frozen 2-0 (Nost) / 1-0 (GoPicks) at run time; survived two same-day
  re-runs byte-identical (setdefault semantics). pytest 82 → 87.
- **R1 (re-backtest = the "backwards propagation check", all 96 results):** walk-forward realized
  **148 Nost / 241 GoPicks / 64 exact = state.cumulative EXACTLY** (hand-check of the frozen M95/M96
  entries agrees: M95 2-0 vs 3-2 → +2/+3, M96 0-2 & 0-1 vs 0-0 → 0/+1). Calibration: RPS 0.1427 → 0.146,
  log-loss 0.820 → 0.8215, goals-MAE 0.859 → 0.883 — flat-to-noise on the 8 new KO matches (two 90'-draws
  incl. a 0-0); CS ratio 1.26× → 1.24×. KO scorelines are market-priced → **no re-tune** (calibration-watch).
- **R2 (form + research):** `wc_form.json` R16-complete (M89–96 folded, real ESPN/Opta xG; Messi 8 WC goals,
  ARG 9.5 xG-for). QF team news: Tchouaméni OUT, Saibari OUT, Quansah suspended (the only QF ban), Doku
  benched again, Sørloth 55/45, Guéhi/Rice precautionary-flagged but expected to start. Fresh Pinnacle QF
  odds incl. new M100 Argentina–Switzerland 1.71/3.59/5.77.
- **BUG FOUND & FIXED (process + guard):** fixtures.json M99/M100 still had W91/W95 placeholders → the
  forecast SILENTLY skipped them (both on Jul-7 and in this run's first pass), so England/Norway QF was
  never market-priced and M99/M100 predictions weren't offered. Filled Norway–England / Argentina–Switzerland,
  re-ran, and added a pipeline guard: any match with curated odds but no forecast now logs a loud
  fill-the-fixture warning.
- **R3 (chips + transfers, QF lock-eve):** 4 free, 0 hits, +23.2: Pulisic→Baena (+16.1), Guéhi→Tagliafico
  (+3.0), Dembélé→Yamal (+2.2), Kane→Mbappé (+1.9). XI 3-4-3, captain Yamal (E 5.4). **Maximum Captain @ QF
  CONFIRMED (EV +4.2)** — and since MaxCap auto-assigns the double to the XI's real top scorer, the QF
  armband is self-resolving: NO captain relay needed this round (ladder kept as the no-chip fallback).
  CSS @ SF, Wildcard @ Final (breakage argmax) unchanged. Free-rolls emitted: park Messi → start Cucurella,
  park Lisandro → start Cubarsí (both restore before ARG's Sun 03:00 KO).
- **Follow-ups:** (a) `ratings_odds.json` is 21d stale (U8 flags it) — full Elo/title-odds re-rate at the
  SF-eve run; (b) R16 realized XI = 61 pts (feed r5, Kane C ×2; season ≈ 446 pending user confirmation).

### 2026-07-04 — before R16 (R32 complete) — U9 + U10 DONE, R1/R2/R3 run
- **U9 (GoPicks podium simulator) DONE** — new `src/gopicks/podium.py` (see §6), pipeline hook, card on
  `predictions.html`, `state.gopicks.{points_official,rank,leaderboard_ahead}` inputs, 8 tests. Golden rule
  §2 amended: GoPicks is the ONE sanctioned rank-aware league (user re-opened 2026-07-04: 230 pts, 9th,
  podium at 259/244/242, 16 matches left). **Real-run verdict: best-EV P(top-3) ≈ 0.0%** (frozen-gap: rivals'
  correlated picks + 12-pt deficit past ~6 people) **→ SWITCH to tilt2-45** (flip to the 2nd-likely outcome
  where the favorite < 45%): **P(top-3) 6.5%** (7.0/6.4/6.3% across rival-overlap 0.4/0.55/0.7 — robust) for
  **−0.6 EV pts** this round (E[final] 264.2 → 262.4). R16 flips: M92 Mex–Eng 1-1, M94 USA–Bel 1-0,
  M96 Sui–Col 1-1; all other picks stay EV. tilt2-55/65 (−2.9 EV → 5.7%) and tilt2-all (−6.7 → 3.2%) are
  dominated — mild, cheap decorrelation is the sweet spot. Re-run each round as standings update.
- **U10 (KO extra-time fantasy adjustment) DONE** — `_et_adjust` in `projections._team_match_env`
  (`model.ko_et_goal_factor`=0.28), 5 tests. FIFA Fantasy scores ET (not shootouts); Lisandro Martínez's
  92' ET goal+assist vs Cape Verde (+~9 real pts the 90'-model called ~0) was same-day proof. Effect scales
  with P(draw@90') — tight ties (M92/M94) get the biggest attacker uplift + CS discount.
- **R1 (re-backtest, all 88):** RPS **0.1453 → 0.1427**, log-loss **0.8214 → 0.82**, goals-MAE
  **0.927 → 0.859** — all better/flat, **no re-tune**. Walk-forward realized **132 Nost / 230 GoPicks /
  59 exact = state.cumulative exactly** (and GoPicks 230 = the user's OFFICIAL score — assume-followed is
  drift-free). CS calibration 59.3 exp vs 47 actual (1.26×, up from 1.21×): driven by FIVE R32 90'-draws
  (M74/75/82/86/88); KO scorelines are market-priced, so no goals tilt — U10 is the targeted fantasy-side fix.
- **R2 (form):** `wc_form.json` — 13 matches folded (M73-88 now complete at team level), real ESPN/FOX/RealGM
  xG, all 16 R16 teams have full 4-game blocks. Movers: Messi 3.72 xG/7G, Mbappé 2.89/6G, Haaland 3.38/5G;
  Paraguay 1.9 xG-for (weakest R16 attack), Mexico 0 conceded in 4. Team-form offsets: BRA/FRA/ARG +0.07.
- **R3 (chips+transfers R16):** 4 free transfers → Díaz→Barcola, Kimmich→Digne (NOT Theo Hernandez — the
  Jul-4 team-news sweep found Digne won France's LB job; the Jul-2 draft's Theo+Lucas buys were both dodged),
  James→De Paul, A.Robinson→Guéhi; 0 hits, +44.1 horizon. Chip schedule: **12th Man @ R16 = PLAY on
  Vinícius Jr (E5.0, best external — edges Mbappé 4.9)** · Wildcard @ QF (breakage argmax) · Clean Sheet
  Shield @ SF · Max Captain @ Final (ceiling gain 4.7). Captain Dembélé (E6.1 tonight) → relay Pulisic → Messi.
- **Deferred (again):** wiring `_lineup_fixes`/`_freeroll` to per-match `next_minutes` — in KO every team
  plays once per round so the global scalar ≈ per-match; revisit only if a concrete mid-round case bites.
- **pytest 77 → 90 green.** Follow-ups: (a) podium sim assumes rival behavior — refresh
  `leaderboard_ahead` whenever the user reports standings and re-run; (b) after the R16 locks tonight,
  `chips_used` should gain "12th Man" (next session: verify assume-followed did it).

### 2026-06-28 (later, user review) — U4 chip-timing revision: Wildcard by squad-breakage, not fixed R16
- **The R16-Wildcard default was wrong for a burner.** The user caught it: a favourites-heavy R32 squad
  (built to max R32 pts + QB) SURVIVES the R32 — every owned team advances 79–97%, so **expected players
  eliminated entering R16 ≈ 1.7, well under R16's 4 free transfers**. A Wildcard at R16 would rebuild a
  near-intact squad = wasted chip; the burner premise ("blow up at R16, ignore horizon") is partly
  self-defeating.
- **Fix:** new `booster.wc_breakage_by_round` (E[forced changes entering a round] − that round's free
  transfers); `chip_schedule` now places the Wildcard at the **argmax breakage round (held past R16,
  typically QF/SF)**, not a fixed R16. `pipeline` passes `squad`/`advancement`/`ft_by_round`.
  `config.fantasy.burner_margin` 0.0 → **2.0** (WC no longer auto-committed to R16 → horizon has option
  value → durable wins ties; the R32 squad was a burner≈durable tie anyway, so the locked 15 is unchanged).
- **Revised KO chip plan:** QB@R32 (played) · **12th Man@R16** (most games) · **Wildcard@QF** (deepest
  break) · Clean Sheet Shield@SF · Maximum Captain@Final — all re-evaluated each round. **pytest 76→77**
  (new `test_wildcard_goes_to_max_squad_breakage_not_r16`). Also corrected the Haaland rationale (out on
  R32 EV+QB 5.41 < bench Oyarzabal 5.58, not "low horizon") and cleared two more resolved-injury artifacts
  (Vargas/SUI, Lukaku/BEL — non-owned). See [[wc2026-ko-chip-plan]] memory (updated).
- **Follow-up:** the breakage proxy is per-round (E[elim] − free transfers); a fuller version would model
  cumulative transfer backlog + re-optimization upside. Good enough to retire the R16 default.

### 2026-06-28 — before R32 (group stage finished) — U4 + U7 DONE, R1/R2/R3 run
- **U4 (booster engine) DONE.** New `src/fantasy/booster.py`: `qb_advance_bonus` (advancement → the QB's
  +2·P(advance) per starter, the value that now FEEDS selection), `chip_schedule` (forward one-chip-per-round
  plan across R32→final, QB valued numerically per round, Mystery-R32 contention + WAIT), `qb_ev_by_round`.
  New `optimizer.select_squad_xi` — a **joint 15+XI ILP** maximizing the STARTING XI's value + per-starter QB
  bonus with a cheap bench (`build_burner_squad`), the correct objective for the R32 burner. Pipeline R32
  rebuild now builds BOTH the durable optimum and the burner and picks the burner **only if it clears the
  durable squad on R32 EV by ≥`fantasy.burner_margin` (2.0)** — otherwise durable wins on dominance (same R32
  EV, more horizon = free option value, since WC@R16 rebuilds the core anyway). Emits the forward chip schedule.
  - **Key finding (this squad):** burner ≈ durable at R32 — burner R32-EV 70.9 vs durable 70.8 (Δ+0.1),
    burner horizon 195.7 vs durable 204.4. The EV-max R32 XI is already the durable optimum (the best
    long-run players also have the best R32 ties), so **durable is chosen**. The burner only diverges when
    maximizing R32 points means picking less-durable players; it didn't here.
- **U7 (player correlation) DONE.** New `src/fantasy/correlation.py`: `captain_ceiling` — a joint-scoreline
  Monte-Carlo (teammates share their match's sampled scoreline; each player's MEAN pinned to `exp_next` so
  squad EV is untouched) → XI ceiling (p90/p10), the captain's doubled distribution, the **Maximum-Captain
  EV** (E[top scorer]−E[captain]), and defensive-stack detection. `PlayerProj` gained `next_num/next_is_home/
  goal_share/assist_share`. Wired into the pipeline → feeds the chip schedule's Max-Captain timing + a report
  card. Respects the golden rule: distributional output used ONLY for the captain/Max-Cap call, never to bias
  selection toward variance.
- **Tests:** pytest **65 → 76 green** (new `test_booster.py`, `test_correlation.py`; incl. mean-consistency
  E[ceiling MC] ≈ Σ exp_next, QB bonus tips a borderline starter into the XI, schedule one-chip-per-round).
- **R1 (re-backtest) — IMPROVED, no config change.** On all 72 results: RPS **0.1543 → 0.1453**, log-loss
  **0.8668 → 0.8214** (both better), goals-MAE 0.905 → 0.927 (≈flat). Clean sheets exp **48.3 vs 40 actual**
  (mild **1.21×** over-prediction, down from MD1's 2.04× and largely MD1-driven; and **market-shielded for
  R32**, which uses curated odds). No re-tune: a blanket goals tilt would overfit (the calibration-watch rule),
  and there's no regression. Realized walk-forward 82 Nost / 185 GoPicks / 47 exact = exactly `state.cumulative`.
- **R2 (refresh form):** `wc_form.json` refreshed to real full-group-stage (3-game) WC xG (90 players / 20
  teams, FOX/Opta); `squads.json`/`lineups.json` reset for R32 (zero priority-team suspensions; Raphinha &
  Schlotterbeck out); R32 odds added (sharp Pinnacle). Team-form offsets: Brazil/France/Argentina/England/
  Spain +0.05–0.06.
- **R3 (chip+transfer plan for R32):** unlimited **durable** rebuild; **QB@R32** (EV ≈20, peaks at R32);
  **Wildcard@R16**; Max-Captain@QF (ceiling edge ≈+5); 12th Man@SF; **Mystery = WAIT** (effect reveals at R32;
  if clean-sheet-related it pairs with a defensive stack and may contend with QB for the R32 slot — re-check).
- **Follow-ups:** (a) wire the Mystery effect into `cfg.fantasy.mystery_booster` once revealed so the schedule
  resolves it automatically; (b) `_lineup_fixes`/`_freeroll` still key on the global `minutes_prob` not
  `next_minutes` (per-match) — left as a later pass; (c) at R16, re-run with the Wildcard to build the durable
  QF→Final core (Vinícius/Brazil the prime buy-back once Raphinha returns).

### 2026-06-24 — after MD2 — U2 DONE (team form, opponent-adjusted WC xG)
- New `ensemble.apply_team_form`: opponent-adjusted xG-difference per game (credited for the strength of
  opponents faced), centred on neutral, matches-shrunk, capped, applied as a SMALL strength offset AFTER
  market calibration (so `beta` is not re-fit on form — the double-count trap). Knobs in config.yaml
  (`team_form_weight`=0.15 → 0 disables, `team_form_shrink_matches`/`opp_coef`/`scale`). `wc_form.json`
  `teams` block seeded for the 7 verifiable contenders. Wired in pipeline after `build_strengths`.
- **pytest 61→65 green** (`test_team_form.py`). **Backtest UNCHANGED** (RPS 0.1543 etc.): the walk-forward
  calibration reads persisted lambdas, and the counterfactual is intentionally left form-free as the clean
  strength baseline — so no regression by construction.
- **Effect (face validity):** small, sensible nudges — Argentina +0.059, France +0.057, Netherlands +0.045,
  Germany +0.043, Spain +0.042, Norway +0.021, Mexico +0.018 z-units. These shift the bracket/title/
  advancement (hence fantasy horizon + QB EV) slightly — the market-shielded path, exactly as scoped — and
  NOT the odds-priced next-match scoreline.
- **HONEST CAVEAT (gating):** U2 can't be cleanly OOS-validated yet — form is computed from the same WC
  matches we'd score, so any in-sample RPS "win" would be leakage. Kept the weight deliberately small to
  bound risk. **True OOS validation = walk-forward form** (recompute each matchday's offset from STRICTLY
  prior results, scored via the counterfactual harness) — the cleanest next step for this item; the U1
  harness + `ignore_market` toggle already support it. Disable anytime with `team_form_weight: 0`.

### 2026-06-24 — after MD2 — U8 DONE (data freshness / confidence tags)
- Loader now collects per-input `{as_of, confidence}` (from each file's fetched_at/updated_at/as_of) →
  `bundle.freshness`. New `pipeline._freshness_rows` computes age + a status (baseline / stale /
  low-confidence / ok); a "Data freshness & confidence" card on `model.html` (colored pills), and any
  **stale daily input** (lineups/odds/squads/results/wc_form ≥2d old) raises the existing top-of-page
  warning banner. `player_stats` is correctly classed "baseline" (pre-tournament club-season), not stale.
- **pytest 57→61 green** (`test_freshness.py`). No model numbers touched (presentational + a loader field).
- On current data: player_stats=baseline (2026-06-05), wc_form=low-confidence (seed), squads/results recent.

### 2026-06-24 — after MD2 — U5 DONE (player form via manual WC xG)
- New `data/manual/wc_form.json` (SEED: 16 performers, goals/mins factual from results, xG estimated;
  `teams` block stubbed for U2) + `WCFormIndex` loader, wired through `loader.py`→`build_projections`→`_rates`.
  Shrinks the club-season per-90 toward WC form, minutes-weighted (`fantasy.wc_form_shrink_minutes`=600 →
  ~23% weight at 180 min) and leaning 0.7/0.3 on xG over goals so finishing heat regresses. New "WC form"
  tag. RUNBOOK §2 row added (the user now curates real WC xG each matchday — their chosen workflow).
- **pytest 52→57 green** (`test_form.py`: shrinkage pulls toward form but stays shrunk; leans on xG not
  finishing; 2 games barely move it; within-team redistribution). **Backtest delta: ZERO on scoreline
  metrics** (RPS 0.1543 etc.) — correct and expected: player form only redistributes a team's goals among
  its players (fantasy EP), it never touches the match λ or the bracket. The realized-fantasy gate is the
  relevant one but is weak in U1, so U5 is validated by construction + unit tests + face validity.
- **Face validity (real run):** modest, sensible lifts — Oyarzabal +12% (nailed #9 + pens + brace),
  Cunha +12%, Messi +8%, Haaland +8%. Nothing overblown (the 2-game shrink is doing its job).
- **Known artifact (inherited, documented):** the shared surname matcher tagged Canada's fringe "Promise
  David" off the "Jonathan David" seed entry ([[wc2026-name-collision-artifact]]). Harmless (fringe stays
  low-EP) and identical to the existing `player_stats.json` behaviour; user's transfer-in sanity-check covers it.
- **Follow-up:** seed `wc_xg`/`wc_xa` are estimates — the user replaces them with real fbref/Opta WC xG in
  the daily research step; the `teams` block gets populated when U2 lands.

### 2026-06-24 — after MD2 — U6+U3 DONE (per-match minutes + light dead-rubber detector)
- New `src/model/standings.py` (sim-independent `group_table` + `dead_rubber_flags`; LIGHT: clinched = ≥6 pts
  after 2 games, eliminated = 0 pts, only for a team's 3rd group game). Wired into `Forecast(stakes=…)` (goal
  intensity ×`model.dead_rubber_intensity`=0.92 when BOTH teams settled) and `build_projections(stakes=…)`
  (per-match minutes: a clinched team's nailed starter, base ≥0.7, drops to ×`fantasy.rotation_rest_factor`
  =0.6 start prob in its dead rubber only). New `PlayerProj.next_minutes`. `_player_match_ep` takes an
  `eff_mins` override (None ⇒ identical to the old scalar model).
- **pytest 46→52 green** (new `test_standings.py` + 2 per-match-minutes tests). **Backtest delta: ZERO** —
  RPS 0.1543 / log-loss 0.8668 / goals-MAE 0.9051 / CS 29.6, byte-identical to U1 baseline. Expected and
  correct: there are no dead rubbers in the played MD1/MD2 sample, so the metrics can't move. The value is
  in MD3 fantasy minutes + dead-rubber scorelines (not measurable on the historical scoreline sample —
  validated by unit tests + a full `./run.sh run` instead). Per the U1 finding (CS is MD1-only), kept the
  intensity deliberately light and did NOT re-tilt goals.
- **Live now:** the detector already flags **8 MD3 matches** on current data (results→44), 4 both-settled
  (Norway–France both-clinched, Senegal–Iraq both-eliminated, Jordan–Argentina, +1) — damping + rest will
  apply automatically on the user's MD3-eve run. Verified end-to-end ("All checks passed"); state restored.
- **Known soft edge (documented, manual fallback):** a "both-clinched" MD3 game (e.g. Norway–France) still
  has seeding stakes, so the 8% damping is debatable there; mild + overridable. Follow-up if it matters: the
  playbook's `_lineup_fixes`/`_freeroll` still key on the global `minutes_prob`, not `next_minutes` — wiring
  them to per-match minutes would fully retire the global-min-prob manual override (left for a later pass).

### 2026-06-24 — after MD2 — U1 DONE (backtest & calibration harness)
- Built `src/eval/` (`metrics.py` pure-fn metrics, `backtest.py` walk-forward + A/B), a `backtest` CLI
  subcommand, `output/backtest.html`, the `Forecast(ignore_market=…)` ratings-only toggle, and
  `tests/test_eval.py`. **pytest 38→46 green; `./run.sh run` verified end-to-end ("All checks passed"),
  then state.json/output restored (it was a mid-MD2 verification run, not the daily run).**
- **Harness validated:** walk-forward realized scoring = **48 Nostradamus / 103 GoPicks / 25 exact**, an
  EXACT match to `state.cumulative` — confirms the pick-of-record logic (latest run where a match is still
  `played:false`) and that all 44 played matches are covered.
- **Baseline calibration (as-shipped, walk-forward):** RPS **0.1543**, log-loss **0.8668**, goals-MAE
  **0.905**. These are the numbers every later item must beat (ratings-only mode for U2/U5).
- **KEY FINDING — the clean-sheet over-prediction is MD1-only.** Expected vs actual CS: **MD1 16.3 vs 8
  (2.04×)** but **MD2 13.3 vs 14 (0.95×, calibrated)**. So MD1 was a variance-driven low-scoring opening
  (draw-glut), not a structural bias → **U6/U3 should NOT structurally "fix" clean sheets** (vindicates the
  prediction-calibration-watch memory). Reliability is near-perfect except the 80–100% favorite bin
  (87% pred vs 62.5% emp, n=8 — noisy, same early-favorites-dropped-points story).
- **A/B (current data):** ratings-only calibrates CS *better* than the market λ-inversion (24.7 vs 29.6,
  actual 22) while the market wins RPS/log-loss — i.e. the market→λ total-anchoring slightly inflates clean
  sheets. A hint for U2 (where odds are absent) and a reason to keep U6's CS handling honest.
- **Resume / next:** U6+U3 (per-match minutes) — still worth it for the global-min-prob fix + appearance/
  captain modeling, but keep CS-tuning conservative given the MD1-only finding. Gate on these baselines.

### 2026-06-24 — after MD2 — planning/discussion (no code yet)
- Reviewed the whole system + this file with the user. Key reframing: **per-match odds override team
  strength** (`Forecast.match_matrix` → `_market_lambdas` first), so player-side items move decisions
  directly while team-form mostly moves the bracket. Reprioritized §3 to **U1 → U6+U3 → U5 → U8 → U2**.
- Decisions (also in §4): U6+U3-rotation built as one **per-match minutes** distribution; U3 group-stakes =
  **light auto dead-rubber detector**; U5/U2 form = **manual WC xG** via new `data/manual/wc_form.json`;
  U1 = realized-scoring (leak-free, scores persisted `data/processed/<date>` snapshots) + counterfactual A/B
  in **as-shipped / ratings-only** modes, leading with the clean-sheet calibration view.
- Rewrote §3 rationale, §4 decisions block, and the U1/U2/U3/U5/U6 detail in §6. No code touched yet;
  toolchain not bootstrapped. **Resume:** bootstrap (CLAUDE.md §1) → baseline `uv run pytest` → build U1.

### 2026-06-23 — planning session (no code changes)
- Created this file from a planning discussion. All items `TODO`. No code touched; `pytest` not re-run.
- Decisions captured: hold all 5 chips for the KO (QB@R32, WC@R16, then Max-Cap/12th/Mystery across
  QF/SF/Final); R32-burner synergy (U4); group-stakes is a closing-window feature (MD3 only, U3).
- Next session (run **after MD2**): start with **U1**, then U2/U5/U6/U8 and the rotation half of U3, each
  gated by U1. Defer U4/U7 to the **before-R32** run.
