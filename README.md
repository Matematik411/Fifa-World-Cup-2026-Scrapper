# FIFA World Cup 2026 — Predictions & Fantasy Guide

A re-runnable forecasting system for the 2026 World Cup that builds **one shared
match-outcome model** and feeds it into **two independent optimizers**:

- **Nostradamus** (RTV SLO score predictor) → the expected-points-maximizing
  scoreline to enter for every match.
- **FIFA World Cup Fantasy** (official, play.fifa.com) → the optimal 15-man
  squad, starting XI, captain, bench order, and per-round transfer/chip plan.

It produces **five self-contained HTML reports** in `output/` — the only thing
you need to read. Open `output/index.html` for "what to do right now".

> The model does the thinking; you just follow the single recommended call per
> decision. No risk tiers, no menus — one squad, one captain, one scoreline per
> match, each with a confidence tag.

## Quick start

```bash
# One command does a full refresh: fetch -> model -> optimize -> render
./run.sh run

# Then open the dashboard
open output/index.html      # macOS;  xdg-open on Linux
```

`run.sh` keeps the Python virtualenv **outside** this (host-mounted) repo. If
you prefer raw `uv`:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.local/state/sandbox-vm/fifa-wc-2026/venv"
uv sync
uv run python -m src.cli run
```

### Useful invocations

```bash
./run.sh run                 # full pipeline
./run.sh run --no-fetch      # re-model + re-render from cached data (fast)
./run.sh run --no-sim        # skip Monte-Carlo (uses cached advancement table)
./run.sh validate            # run validation checks on the latest run
./run.sh --help
```

## How updates work (read this before re-running)

This project is designed to be **re-run at the start of each day** during the
tournament. **Each run is a fresh Claude session** that re-does the web research
— there is no long-lived chat. Everything needed to resume lives on disk:

- **`CLAUDE.md`** — auto-loaded by every new Claude session; points here.
- **`RUNBOOK.md`** — the explicit per-run checklist for the Claude session
  (what to re-pull, what to verify, the commands, how to sanity-check).
- **`state.json`** — the source of truth for your *real* fantasy team + running
  scores. Reconciled at the start of every run.
- **`data/manual/*.json`** — Claude-curated research inputs (odds, ratings,
  squads, fixtures, rules). Refreshed each run.
- **`data/raw/<date>/`** & **`data/processed/<date>/`** — timestamped, never
  destroyed, used for the changelog diff.

So a normal update is just: open a new Claude Code session in this repo, say
"do a run" (Claude reads `CLAUDE.md` → `RUNBOOK.md`), then open `output/`.

## What's where

```
src/sources/      one fetcher per source, all normalize to common schemas
src/model/        ensemble strengths, Dixon-Coles scorelines, Monte-Carlo bracket
src/nostradamus/  expected-points-maximizing scoreline optimizer
src/fantasy/      per-player projections, ILP squad optimizer, transfer planner
src/report/       jinja2 templates + renderer (the 5 HTML pages)
src/pipeline.py   orchestrates a full run
src/cli.py        `python -m src.cli ...`
config.yaml       budget, rules, ensemble weights, sim iterations
data/, output/    working data (git-ignored) and the HTML you read
```

## Data sources

Bookmaker odds (de-vigged), prediction markets, eloratings.net, public
supercomputer models, FIFA ranking, recent form, and squad/injury news — plus
the official play.fifa.com fantasy feed for the authoritative player pool &
pricing. Optional free-tier API keys (`.env`, see `.env.example`) enrich
structured fixture/stat pulls but are not required. Every page cites its sources.

## Method (short)

De-vig market odds + Elo + public models → per-team attack/defence strength →
per-match expected goals (venue/altitude/rest/incentive-adjusted) →
**Dixon–Coles** scoreline matrix (calibrated to the de-vigged market 1X2) →
**Monte-Carlo** simulation of the full 48-team / Round-of-32 bracket
(best-third-placed logic) for advancement & title odds. The Nostradamus
optimizer maximizes expected points over that scoreline matrix; the fantasy
optimizer is a horizon-weighted ILP. See `model.html` for the live writeup.
