"""Command-line entrypoints: `python -m src.cli run` (and via ./run.sh)."""
from __future__ import annotations

import argparse
import sys

from .config import load_config
from .validate import validate_run


def _log():
    try:
        from rich.console import Console
        c = Console()
        return lambda msg: c.print(msg, highlight=False)
    except Exception:
        return print


def cmd_run(args):
    from .pipeline import run_pipeline
    log = _log()
    if args.sim_iters:
        # ephemeral override for quick iterations
        import os
        os.environ["WC2026_SIM_ITERS"] = str(args.sim_iters)
    cfg = load_config()
    if args.sim_iters:
        cfg.raw.setdefault("model", {})["sim_iterations"] = args.sim_iters
    result = run_pipeline(
        run_date=args.date, fetch=not args.no_fetch, sim=not args.no_sim,
        render=not args.no_render, log=log,
    )
    if not args.no_validate:
        problems = validate_run(result)
        if problems:
            log("\n[validation] Issues found:")
            for p in problems:
                log(f"  - {p}")
        else:
            log("\n[validation] All checks passed.")
    return 0


def cmd_validate(args):
    from . import io_utils
    log = _log()
    rd = args.date or io_utils.latest_run_date()
    if not rd:
        log("No processed run found.")
        return 1
    summary = io_utils.load_json(io_utils.processed_dir(rd) / "summary.json")
    log(f"Latest run: {rd} | {len(summary.get('predictions', {}))} predictions | captain {summary.get('captain')}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="wc2026", description="FIFA World Cup 2026 predictions & fantasy guide")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Full refresh: fetch -> model -> optimize -> render")
    r.add_argument("--date", default=None, help="Run date YYYY-MM-DD (default: today UTC)")
    r.add_argument("--no-fetch", action="store_true", help="Use cached data, don't hit the network")
    r.add_argument("--no-sim", action="store_true", help="Reuse cached advancement, skip Monte-Carlo")
    r.add_argument("--no-render", action="store_true", help="Skip HTML rendering")
    r.add_argument("--no-validate", action="store_true", help="Skip post-run validation")
    r.add_argument("--sim-iters", type=int, default=None, help="Override Monte-Carlo iterations")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("validate", help="Summarize/validate the latest processed run")
    v.add_argument("--date", default=None)
    v.set_defaults(func=cmd_validate)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
