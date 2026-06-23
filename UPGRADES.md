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
- **Pure best-EV only.** No rank-aware / differential / catch-up strategy (user declined, 2026-06-12).
  Variance work is allowed *only* where EV is inherently distributional (captain ceiling, exact-score mode).
- **Verified rules override config.** `data/manual/{fantasy_rules,nostradamus,gopicks}.json` are the source
  of truth for game rules; `config.yaml` is overridden by them. Read rules from there; never hardcode.
- **Keep the daily run working.** `./run.sh run` must stay green and idempotent; `uv run pytest` must pass.
- **One shared match model.** All three optimizers must draw from the same scoreline distribution — keep
  them coherent (e.g. fantasy clean-sheet/goals-conceded must come from the same model as Nostradamus).
- **Keep tool state out of the host repo** (venv → `$SANDBOX_VM_STATE`); NixOS needs
  `LD_LIBRARY_PATH=$HOME/.nix-profile/lib` (run.sh handles it).

---

## 3. Phasing — what to do at each trigger

| Trigger (when the command is run) | One-time items due | Plus recurring (§5) |
|---|---|---|
| **After MD2** (now; this is also "before MD3") | **U1** (backtest, FIRST), **U2** (team form), **U5** (player form), **U3** (rotation→minutes part; group-stakes part only if quick — closing window, MD3 only), **U6** (minutes dist), **U8** (freshness tags, cheap) | R1, R2 |
| **Before R32** (group stage finished, ties known) | **U4** (booster engine + R32-burner + lookahead), **U7** (correlation) | R1, R2, R3 |
| **Before R16** | — (catch up any deferred item) | R1, R2, R3 |
| **Before QF / SF / Final** | — | R1, R2, R3 |

Rationale for the ordering (so future sessions don't re-litigate it):
- **U1 first** — it's the measuring stick for U2/U3/U5.
- **U2/U5 (form) ASAP** — reusable every remaining match; value compounds with data.
- **U3 group-stakes is a closing window** — it only applies to MD3 (knockout has uniform max intensity, no
  dead rubbers). The reusable half is rotation→minutes. Don't over-invest in the group-position-intensity
  part; the manual dead-rubber handling is a working fallback for MD3.
- **U4/U7 before R32, not now** — they target the knockouts and can't be exercised until the rebuild +
  chips are live. Building them earlier means testing against hypotheticals.

---

## 4. Status tracker (the executing agent keeps this current)

| ID | Item | Phase | Status | Depends on |
|----|------|-------|--------|------------|
| U1 | Backtesting & calibration harness | After MD2 | TODO | — |
| U2 | Team form, opponent-adjusted | After MD2 | TODO | U1 |
| U3 | Stakes/intensity + rotation→minutes | After MD2 | TODO | U1 |
| U4 | Booster engine: schedule + lookahead + R32-burner | Before R32 | TODO | U1 |
| U5 | Player form (WC xG + shrinkage) | After MD2 | TODO | U1 |
| U6 | Minutes as a distribution | After MD2 | TODO | — |
| U7 | Player-level correlation (CS stacking, captain ceiling) | Before R32 | TODO | — |
| U8 | Data freshness / confidence tags | Anytime | TODO | — |

Statuses: `TODO` · `IN-PROGRESS` (leave a resume note in §7) · `DONE` · `DEFERRED` (say why in §7).

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
- **Approach:** walk-forward — for each played matchday, predict using only prior-matchday data, score vs
  actual. Metrics: **RPS** (scorelines), **Brier/log-loss** (1X2), **MAE + reliability curve** (goals).
  Also **realized-vs-EV** per optimizer: Nostradamus pts, GoPicks pts (+ exact-goal tiebreak), fantasy
  captain hit-rate / XI points. Emit `output/backtest.html` (or extend `model.html`).
- **Gotchas:** strict walk-forward (no leakage); enough sample only emerges through the group stage.
- **Done-when:** one command reproduces all metrics; any new signal can be toggled and A/B'd out-of-sample.

### U2 — Team form, opponent-adjusted
- **Goal:** update team strength with in-tournament (and lightly, recent pre-tournament) results.
- **Why:** strength priors are pre-tournament; form is currently ignored.
- **Where:** `src/model/ensemble.py` (strength prior), `src/model/teams.py`.
- **Approach:** rolling attack/defense from WC matches using **xG where available**, **opponent-adjusted**
  (beating Curaçao ≠ beating France) — Elo-style update or Bayesian update of the strength prior.
- **Gotchas:** (a) **double-counting** — lambdas are market-calibrated and books already price form; enter
  form via the **prior**, or shift the model-vs-market blend weight as the sample grows — **not both**.
  (b) small sample → **shrink**. (c) friendlies: near-zero weight on output; fitness/minutes only.
- **Done-when:** backtest (U1) shows out-of-sample RPS/log-loss improvement vs current.

### U3 — Stakes/intensity + rotation→minutes
- **Goal:** condition matches on what each team needs, and model end-of-group rotation.
- **Why:** dead rubbers lower intensity *and* rest starters; must-win games don't. Rotation today is only a
  **manual `rotation_risk` flag** in `src/fantasy/projections.py`.
- **Where:** `src/pipeline.py` (standings), `src/model/forecast.py` (intensity on expected goals),
  `src/fantasy/projections.py` (rotation→minutes).
- **Approach:** derive each team's qualification state from standings (clinched-top / clinched-through /
  needs-result / eliminated) → (a) intensity multiplier on expected goals; (b) rotation probability →
  minutes distribution for key players (augments/replaces the manual flag).
- **⚠ Closing window:** the **group-position-intensity** half only applies to **MD3** (KO is uniform max
  intensity). Prioritize the **rotation→minutes** half (reusable in KO). For MD3 itself, the manual
  dead-rubber handling is an acceptable fallback if the modeled version isn't ready before lock.
- **Done-when:** rotation auto-derived (not hand-set); MD3-type matches show improved backtest accuracy.

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
  should be updated on evidence).
- **Where:** `src/fantasy/projections.py` (consumes per-90 → `exp_next`/`exp_avg`/`horizon`); possibly a new
  form module + a new field/source feeding `player_stats.json`.
- **Approach:** update per-90 via **Bayesian shrinkage** on **WC xG/xA** (weight by minutes/shots — 2 games
  barely moves it, by R16 it moves more). **Separate usage/role change (sticky, predictive)** — new penalty
  taker, now the lone #9, more shots — **from finishing heat (noisy, regresses).** Friendlies → minutes only.
- **Done-when:** backtest (realized-vs-EV for fantasy + Nostradamus) improves out-of-sample.

### U6 — Minutes as a distribution
- **Goal:** replace scalar `minutes_prob` with `P(start)` / `P(60+)` / sub-time.
- **Why:** drives appearance points, **60′ clean-sheet eligibility**, and captain risk; supports U3.
- **Where:** `src/fantasy/projections.py`.
- **Done-when:** projections use the distribution; CS eligibility respects the 60′ threshold probabilistically.

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

---

## 7. Run log (append-only — newest at top)

> Template: `### <date> — <stage> — <agent>` then bullets: items touched, status changes, **backtest delta**,
> follow-ups / resume-notes.

### 2026-06-23 — planning session (no code changes)
- Created this file from a planning discussion. All items `TODO`. No code touched; `pytest` not re-run.
- Decisions captured: hold all 5 chips for the KO (QB@R32, WC@R16, then Max-Cap/12th/Mystery across
  QF/SF/Final); R32-burner synergy (U4); group-stakes is a closing-window feature (MD3 only, U3).
- Next session (run **after MD2**): start with **U1**, then U2/U5/U6/U8 and the rotation half of U3, each
  gated by U1. Defer U4/U7 to the **before-R32** run.
