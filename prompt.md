# BUILD PROMPT — FIFA World Cup 2026 Predictions & Fantasy Guide

> **You are a fresh Claude Opus 4.8 (max-effort) session. This file is your complete brief.**
> Build the project specified below, then immediately do the live web research and generate the
> first full set of HTML outputs. Work autonomously and decisively — the human will *follow your
> recommendations*, so commit to concrete picks (with confidence notes), don't hedge.

---

## 0. TL;DR of what to build

A re-runnable Python project that:
1. **Researches** the 2026 World Cup from *many* sources (odds, models, ratings, form, injuries, lineups).
2. **Builds one shared forecast model** → per-match scoreline probability distributions + team strength + advancement/title odds.
3. Feeds that model into **two independent optimizers**:
   - **Nostradamus** (RTV SLO score-predictor) → the expected-points-maximizing scoreline to enter for each match.
   - **FIFA World Cup Fantasy** (official, play.fifa.com) → the optimal 15-man squad, starting XI, captain, and per-round transfer plan.
4. **Saves working data** (json/csv — your format, the human never opens these).
5. **Renders self-contained HTML reports** — **the ONLY thing the human reads.** Make them excellent.
6. Can be **re-run anytime before/throughout the tournament**; each run refreshes research and regenerates outputs, with a changelog vs. the previous run.

**Today is ~2026-06-05. The tournament opens Thursday, 2026-06-11 (Mexico–South Africa).** The fantasy
squad locks at that first kickoff, and Matchday-1 Nostradamus picks are due per-match around then. So the
**first run must prioritize: (a) the initial fantasy squad, and (b) Matchday-1 score predictions.**

---

## 1. The user & the objective

- The user (Slovenian, comfortable in English — write all HTML in **English**) plays two **independent** games and wants to **win both**. Scoring in each is independent of what other players pick, so:
- **Objective = maximize EXPECTED points in each game on its own merits.** This is an expected-value problem, *not* a contrarian/differential one. Do NOT sacrifice EV for variance.
- **You do all the thinking; the user just follows.** Both games produce a **single, clearly-stated best pick** — for fantasy: one recommended squad + starting XI + captain + transfer moves; for Nostradamus: one recommended scoreline per match. **No risk tiers, no differential/variance options, no "gamble menu", no alternatives to weigh.** Make the call, state it plainly with a short rationale and a confidence tag, and move on.
- The user will **not** read the data files. They open the **HTML** and act on it. Recommendations must be **decisive and actionable** ("enter 2-1", "captain X", "transfer Y→Z"), each with a one-line rationale and a confidence tag.

---

## 2. The two games — rules (embedded; VERIFY the ⚠️-flagged items live before modeling)

### 2a. FIFA World Cup Fantasy — official, https://play.fifa.com/fantasy  (rules: /fantasy/help/rules)

Confirmed:
- Free. **$100M budget** to pick **15 players**: **2 GK, 5 DEF, 5 MID, 3 FWD**. Budget **increases by $5M for the Knockout Phase**.
- **Player prices are fixed** (do not change with performance).
- Scoring is **FPL-like** — points for appearances, goals, assists, clean sheets — **plus tackles, chances created, and shots**. Bonuses: **+1** for a goal from a direct free-kick; **+2** if a player scores >4 points and is owned by **<5%** of teams.
- **Captain scores 2×**; the captain can be changed an **unlimited** number of times during a live round.
- **Unlimited transfers until the first match kicks off (2026-06-11)**; limited per round afterwards.

⚠️ **Verify live from the rules page before modeling** (these drive the optimizer and may differ from FPL):
- Exact **point values per action by position** (goal/assist/CS/save/tackle/CC/shot, cards, penalties, conceded-goals penalty for GK/DEF).
- **Transfers per round** after lockout and any **points penalty** for extra transfers.
- **Boosters/chips** that exist (e.g. Wildcard / Triple-Captain / Bench-Boost equivalents) and when each can be used — *use them optimally and tell the user when to play them.*
- **Max players per national team** (there is usually a cap — find it).
- Valid **starting-XI formations** (min/max per position), **bench order**, and **auto-substitution** rules.
- The **fantasy data source**: the player list with prices/positions/ownership/points. Find the game's JSON data endpoint (inspect the site / community-documented endpoints, FPL-style `bootstrap`-type feed) and pull it directly; fall back to scraping if needed. This is your authoritative player pool & pricing.

### 2b. RTV SLO Nostradamus — score predictor  (https://www.rtvslo.si/nostradamus/...)

Confirmed scoring, **per match, based on the 90-minute regular-time result only (extra time & penalties ignored)**:
- **Exact score** (e.g. predict 2-1, actual 2-1) → **3 points**
- **Correct outcome (1/X/2) + exactly one team's goal count** (e.g. predict 2-0 / 3-1 / 4-1 when actual is 2-1) → **2 points**
- **Correct outcome only**, both goal counts wrong (e.g. predict 1-0 / 3-2 / 4-0 when actual is 2-1) → **1 point**
- Anything else (wrong outcome) → **0 points**. (Guessing one team's goals but the wrong outcome = 0.)
- **Knockout rounds** — confirmed for 2026: doubling **starts at the Round of 32** and runs **through the final**. In those rounds all values **double** → outcome **2**, outcome+one-team **4**, exact **6**. (Group stage = single values.)
- Predictions can be **edited any time until that match kicks off** (per-match deadlines). A live leaderboard is published. No jokers/bonuses known.

⚠️ **Verify live for the 2026 edition:**
- **Which matches the app includes** (all 104, or only RTV-broadcast matches?). Generate a prediction for **every match the app offers**.
- Any other 2026 rule changes. (The doubling boundary is **confirmed: it starts at the Round of 32** — no need to re-verify that.)

**Strategic implication of this scheme:** because partial credit is given for the *outcome*, the EV-optimal
prediction is NOT simply the single most-likely scoreline nor simply the most-likely outcome — it's the
scoreline that maximizes expected points across the full scoreline distribution. Compute it properly (§4b).
Note that in knockout matches a **draw after 90 minutes is a valid and often optimal prediction** for tight ties.

---

## 3. Tournament facts (embedded)

- **2026-06-11 → 2026-07-19.** Hosts: USA (11 cities), Mexico (3), Canada (2). Opener: **Mexico vs South Africa**, Estadio Azteca.
- **48 teams**, **12 groups of 4**. Each plays 3 group games. **Top 2 of each group + 8 best third-placed teams** advance to a **Round of 32** → Round of 16 → Quarterfinals → Semifinals → Final. **104 matches total.**
- Implement the **"best third-placed teams"** logic and the new R32 bracket correctly — it affects advancement probabilities and how many future matches each player is likely to play (key for fantasy longevity).
- Kickoffs span many US/CA/MX time zones; the user is in **Slovenia (CET/CEST)**. Show deadlines in **both local match time and CET**.
- The actual **groups, fixtures, kickoff times, and final 26-man squads** are set by now — fetch them live; do not hardcode guesses.

---

## 4. Methodology — the core IP (build this carefully)

### 4a. Shared forecast model
Build **one** team-strength / match model that both optimizers consume. Don't trust a single source — **ensemble**:
1. Pull **multiple** signals (§5): de-vigged **bookmaker odds** (outright title + per-match 1X2 / over-under / correct-score where available), **prediction-market** implied probs, **Elo** (eloratings.net), reputable **public models** (e.g. Nate Silver, Opta-style), **FIFA ranking**, recent **form**, and **squad/injury** news.
2. Combine into, **per match**: P(win/draw/loss) and an **expected-goals estimate for each team** (λ_home, λ_away), adjusted for opponent, venue/host advantage, altitude (Mexico City/Guadalajara), heat, rest days, and must-win incentives.
3. Convert (λ_home, λ_away) into a **scoreline probability matrix** P(i, j) using a **Dixon–Coles / bivariate-Poisson** model (apply the low-score correlation correction; calibrate so the matrix's marginal 1X2 matches the de-vigged market 1X2). Cap goals at ~0..8.
4. Derive **group qualification**, **advancement to each round**, and **title** probabilities via **Monte-Carlo simulation** of the full 48-team bracket (≥100k iterations), correctly modeling the best-third-placed logic and knockout 90-min/ET resolution.
5. Persist the model (team ratings, per-match P(i,j), advancement table) to `data/` as the single source of truth, with the run timestamp and **source attributions**.

### 4b. Nostradamus optimizer (expected-points-maximizing scoreline)
For each upcoming match with scoreline distribution `P(i,j)`:
- Define the points function exactly per §2b. For a candidate prediction `(a,b)` and actual `(i,j)`:
  - `i==a and j==b` → 3 (×2 from the Round of 32 onward)
  - else if `outcome(a,b)==outcome(i,j)` and `(i==a or j==b)` → 2 (×4)
  - else if `outcome(a,b)==outcome(i,j)` → 1 (×2)
  - else → 0  (where `outcome` ∈ {home win, draw, away win})
- Compute `E[points](a,b) = Σ_{i,j} pts(a,b,i,j)·P(i,j)` for all candidate `(a,b)` over `0..6` each, and **pick the argmax**. Break ties toward the higher-probability exact score. (Doubling is a per-match constant, so it doesn't change the argmax within a match — but report doubled EV so the user sees which matches matter most.)
- For each match output: the **recommended scoreline**, the modeled **P(1/X/2)** and expected goals, the **expected Nostradamus points** of the pick (and of the runner-up alternative), and a one-line rationale. Flag knockout matches with the "90-min result, draw allowed" note.

### 4c. Fantasy optimizer (constrained squad selection)
1. **Per-player expected points.** From the shared model + player roles, estimate each player's expected fantasy points **per match** and over the relevant horizon. Decompose by position using the *verified* scoring values:
   - GK/DEF: appearance + **clean-sheet probability** (from opponent-adjusted P(0 conceded)) + saves/tackles/CBI − goals-conceded penalty.
   - MID/FWD: appearance + **expected goals & assists** (team λ × player's share of goal involvement) × position multiplier + shots + chances created (+ CS for MID).
   - Multiply by **start/minutes probability** (research nailed-on starters vs. rotation risk) and add **set-piece / penalty / direct-FK** premiums for the designated takers.
2. **Horizon weighting (important — transfers are limited after lockout):** value = Σ over expected remaining matches of per-match expected points, where "expected remaining matches" uses the team's **advancement probabilities** from §4a. Players on teams likely to go deep are worth more because you can't easily churn them. Make the horizon **stage-aware** (pre-tournament: whole group stage + expected knockout games; mid-tournament: the matches before the next transfer window).
3. **Optimize** with an ILP (use `PuLP` or `python-mip`): maximize Σ expected_points·x subject to **budget ($100M, +$5M in KO), 2/5/5/3 squad, the per-nation cap, and ≤15 players**. Then pick the **starting XI** (valid formation) maximizing the next round's expected points, the **captain** (max expected, ×2) and **vice**, and **bench order**.
4. **Transfer planning:** each run, given the current/known squad, recommend the best **in/out moves** for the upcoming round within the transfer limit (and advise when to spend a **booster/chip**), with the expected-points gain of each move. For the very first run, output the **optimal initial 15**. Output exactly one recommended squad — no alternatives or risk variants.

---

## 5. Data sources (use several; cite them in the HTML)

Architecture for updates is **Claude-in-session** (you, now and on each rerun) doing the web research with
your built-in WebSearch/WebFetch — **no LLM API key needed.** The user is fine creating **free-tier API keys**
for structured data; make those **optional** (read from `.env`, degrade gracefully if absent) and document them
in `.env.example`.

- **Official fantasy data** — play.fifa.com fantasy feed: player pool, prices, positions, ownership %, points, fixtures. *Authoritative for the fantasy side.*
- **Odds / markets** — Oddschecker, bet365, FOX Sports odds, Covers, Pinnacle (sharp), prediction markets. De-vig before use.
- **Ratings & models** — eloratings.net (World Football Elo), FIFA world ranking, Nate Silver (natesilver.net), Opta/other public model write-ups & simulations.
- **Structured fixtures/stats (free tiers, optional keys)** — football-data.org, API-Football. Use for fixtures, lineups, results during the tournament.
- **xG / underlying numbers** — FBref / Understat / Opta where available, as player-role and team-strength priors.
- **Team news** — reputable outlets for confirmed/probable XIs, injuries, suspensions, rotation, penalty/FK takers (critical for fantasy minutes & returns).
- Cache every raw pull to `data/raw/<run-date>/` with the source URL + fetch timestamp so runs are reproducible and diffable.

---

## 6. Tech & project structure

- **Language: Python** (use **`uv`**). Pin dependencies. Suggested libs: `httpx` (HTTP), `selectolax`/`beautifulsoup4` (parse), `pandas`/`numpy` (data), `scipy` (Poisson/Dixon-Coles), `PuLP` or `python-mip` (ILP), `jinja2` (HTML), `pydantic` (config/schemas), `rich` (CLI logs).
- **Sandbox/env hygiene (this repo is on a host-mounted path):** do **not** write tool state into the repo. Put the venv outside it — e.g. `export UV_PROJECT_ENVIRONMENT="$SANDBOX_VM_STATE/fifa-wc-2026/venv"` — and keep `.venv`, caches, `__pycache__`, etc. out of the project (add to `.gitignore`). `data/` and `output/` *do* live in the repo (they're deliverables), but keep them git-ignored if they get large; commit nothing unless the user asks.
- Suggested layout:
  ```
  prompt.md                  # this brief
  README.md                  # human-facing: how to run / re-run
  CLAUDE.md                  # auto-loaded by every new session; points to RUNBOOK/state/data/latest
  RUNBOOK.md                 # the per-run checklist for your future self (see §8)
  pyproject.toml / uv.lock
  .env.example               # optional API keys
  config.yaml                # budget, squad rules, source list, sim iters
  state.json                 # the user's REAL team state + running scores (see §8) — source of truth across runs
  src/
    sources/                 # one fetcher per source; all normalize to common schemas
    model/                   # ensembling, Dixon-Coles scorelines, Monte-Carlo bracket sim
    nostradamus/             # §4b optimizer
    fantasy/                 # §4c projections + ILP + transfer planner
    report/                  # jinja2 templates + renderer
    pipeline.py              # orchestrates a full run
    cli.py                   # `python -m src ...` entrypoints
  data/
    raw/<run-date>/          # cached source pulls
    processed/<run-date>/    # model outputs (json/csv)
    latest -> <run-date>     # pointer to newest run for diffing
  output/                    # the HTML the user reads (see §7)
  ```
- One command does a full refresh, e.g. `uv run python -m src.cli run` → fetch → model → optimize → render. Make it **idempotent** and safe to re-run anytime.

---

## 7. HTML output — the ONLY thing the human reads. Make it genuinely good.

Requirements for all pages: **self-contained** (inline or single bundled CSS, no external/CDN runtime deps so they open offline), clean and **mobile-friendly**, **printable**, every page shows **"generated at <timestamp> CET"** and the **tournament stage**, sortable tables where useful, and a **Sources** footer. Be **decisive**; mark each recommendation with a **confidence** (High/Med/Low). Cross-link the pages.

Produce at least:
1. **`index.html` — dashboard / "what to do right now".** Last-updated, current stage, countdown to next deadline, and a short prioritized **action list** (e.g. "Set this XI & captain before 11 Jun 18:00 CET", "Enter these 4 score predictions for today"). Links to the detail pages.
2. **`fantasy.html`.** The recommended **15-man squad** (name, club/nation, position, price, expected pts), **total cost & money left**, the **starting XI in a formation pitch view**, **captain/vice**, **bench order**, and **transfer recommendations** for the upcoming round (in/out + expected-points gain + when to use a chip). Show per-player expected points and a one-line "why". This is the **single recommended team** — no alternatives or risk tiers to weigh, just the call, clearly laid out.
3. **`predictions.html`.** Every upcoming match grouped by matchday/round: **recommended scoreline to enter**, modeled **P(1/X/2)**, expected goals, **expected Nostradamus points** (doubled from the Round of 32 on), the runner-up scoreline, deadline (match-local + CET), and a one-line rationale. Clearly flag knockout 90-min/draw-allowed cases. **One single best scoreline per match — stated plainly, no alternatives.**
4. **`model.html`.** Team strength table, **group-by-group qualification probabilities**, advancement/bracket odds, title odds — with source attribution and a note on method & uncertainty.
5. **`changelog.html`.** What changed since the previous run: odds moves, injuries/suspensions, squad/price/ownership changes, and **which fantasy picks or score predictions flipped and why.** (Diff `data/processed/latest` vs. the prior run.) Also show **results since the last run** and our **running performance**: Nostradamus points earned per match and the cumulative fantasy total, so the user can see how the strategy is doing.

---

## 8. Re-run / update behavior + your future-self runbook

- **Expected cadence — the user re-runs at the START OF EACH DAY** during the tournament. Every run must therefore: (1) **ingest the previous day's actual results**, update group standings, advancement/title probabilities, and remaining fixtures; (2) **score how our picks did** — Nostradamus points earned per match plus the running fantasy total — and surface that on the dashboard/changelog so performance is visible over time; (3) **regenerate today's score predictions** and the **fantasy moves** for the next transfer window; (4) call out anything time-sensitive (today's per-match deadlines and any fantasy lock, in CET).
- Updates run **Claude-in-session**: each time, a Claude session re-does the web research, refreshes `data/`, and regenerates `output/`. So **write a `RUNBOOK.md` (or `CLAUDE.md`) for your future self** with an explicit per-run checklist: which sources to re-pull, what to verify (lineups/injuries that day, odds moves, yesterday's results), the exact commands to run, how to sanity-check, and what to tell the user.
- Runs are **idempotent and timestamped**; never destroy prior runs (keep history under `data/`), and always produce the **changelog diff**.
- Be **stage-aware**: before the tournament → initial squad + MD1 predictions; during groups → next-matchday transfers + upcoming predictions + live qualification picture; knockouts → bracket-aware predictions (90-min, draws valid) + KO-budget fantasy + chip timing.

**State & session continuity — fresh session every run (the operating model).** Do not rely on a long-lived chat; treat every run as a brand-new session that bootstraps entirely from disk. To make that reliable:
- The build session MUST write a root **`CLAUDE.md`** (auto-loaded by every new session) that points to `RUNBOOK.md`, `data/latest`, and `state.json`, and gives a ~5-line "how to do a run" summary.
- Maintain **`state.json`** as the source of truth for the user's *real-world* status — distinct from what was merely recommended: the 15 players actually owned + captain + formation, chips/boosters used and remaining, **free transfers remaining**, bank/budget, cumulative Nostradamus and fantasy points, which matches' predictions have been entered, and `last_run` date. Read it first thing each run; rewrite it at the end.
- **Reconcile real vs. recommended at the start of each run.** Past advice is not guaranteed to be what the user actually did, so the runbook's first step is to confirm/refresh the user's actual team — ask the user, or pull it from the game if a team ID/endpoint is available — and update `state.json` before optimizing. Never assume prior advice was followed exactly.
- Nothing important may live only in conversation context: everything needed to resume sits in the host-mounted repo (`data/` history, `output/`, `CLAUDE.md`, `RUNBOOK.md`, `state.json`).

---

## 9. Quality, validation & gotchas

- **Validate every run:** scoreline matrices sum to ~1; squad satisfies budget + 2/5/5/3 + nation cap; a prediction exists for every offered match; no missing prices/positions. Fail loudly with a clear message rather than emitting a silently-wrong report.
- **Unit-test the two optimizers** on hand-checked toy inputs (a known scoreline distribution → known argmax prediction; a tiny player set → known ILP pick).
- **Don't overfit to one source**; ensemble and de-vig. Tag low-confidence outputs.
- **Gotchas:** the new R32 / best-third-placed format; Nostradamus doubling runs from the **Round of 32 through the final** (group stage = single values); knockout predictions are the **90-minute** result (draws allowed); time-zone conversions for deadlines; fantasy **longevity weighting** because transfers are limited; **minutes/rotation risk** dominates cheap-player value; verify the exact FIFA Fantasy scoring values and chip rules before trusting projections.
- Handle missing/failed sources gracefully (skip + note in the report's Sources/limitations section).

## 10. First-run deliverables (do these now, after building)

1. Verify the ⚠️ live items (FIFA Fantasy exact scoring/chips/caps; Nostradamus 2026 doubling boundary & match coverage).
2. Do the multi-source research; build the model + bracket simulation.
3. Produce the **optimal initial fantasy 15 + starting XI + captain** and the **Matchday-1 score predictions**.
4. Generate **all** HTML pages and **open/verify** them (use the run/verify skills) so they render correctly and are self-contained.
5. Give the user a short summary: the squad, the captain, the top early predictions, and **exactly what to do before the 2026-06-11 lock** — plus how to re-run for updates.

## 11. Acceptance checklist

- [ ] Live rules verified (both games) and any 2026 deltas reflected.
- [ ] Multi-source ensemble model with Dixon-Coles scorelines + Monte-Carlo bracket (handles 48-team/R32 format).
- [ ] Nostradamus optimizer = true expected-points argmax (not just modal score/outcome); doubling handled.
- [ ] Fantasy ILP respects budget/positions/nation cap; horizon-weighted by advancement; XI + captain + transfer/chip plan.
- [ ] Both games presented as a single, clearly-stated best pick (fantasy squad/XI/captain/transfers; one scoreline per match) — no risk tiers, variants, or alternatives.
- [ ] Daily re-run ingests the prior day's results, updates standings/odds/advancement, and tracks running performance (Nostradamus + fantasy points).
- [ ] Fresh-session resumability: `CLAUDE.md` + `RUNBOOK.md` + `state.json` let a brand-new session bootstrap fully from disk; each run reconciles the user's REAL team state before optimizing.
- [ ] Working data saved to `data/` (json/csv); raw pulls cached + timestamped + diffable.
- [ ] Five self-contained, offline, printable, source-cited HTML pages; decisive recommendations with confidence tags.
- [ ] One-command idempotent re-run; `RUNBOOK.md` written for future-self updates; changelog/diff produced.
- [ ] Venv/tool-state kept out of the host-mounted repo.
- [ ] First-run outputs generated and visually verified; user given a clear pre-11-June action summary.
```
