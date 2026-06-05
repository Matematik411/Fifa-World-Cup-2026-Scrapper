"""Run-directory management, JSON/CSV persistence, and the `latest` pointer.

Runs are timestamped and never destroyed, so the changelog can diff the newest
processed run against the prior one.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .config import PROCESSED, RAW


def today_str() -> str:
    # Date is injected by the harness context; default to UTC date.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _json_default(o: Any):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (datetime,)):
        return o.isoformat()
    raise TypeError(f"Not JSON serializable: {type(o)}")


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=_json_default, ensure_ascii=False))


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text())


def raw_dir(run_date: str) -> Path:
    d = RAW / run_date
    d.mkdir(parents=True, exist_ok=True)
    return d


def processed_dir(run_date: str) -> Path:
    d = PROCESSED / run_date
    d.mkdir(parents=True, exist_ok=True)
    return d


def update_latest_pointer(run_date: str) -> None:
    """Point data/processed/latest at the newest run (symlink, or text fallback)."""
    link = PROCESSED / "latest"
    target = run_date
    try:
        if link.is_symlink() or link.exists():
            if link.is_symlink():
                link.unlink()
            elif link.is_dir():
                shutil.rmtree(link)
            else:
                link.unlink()
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        # 9p / Windows hosts may not allow symlinks — write a pointer file instead.
        (PROCESSED / "latest_run.txt").write_text(run_date)


def latest_run_date() -> str | None:
    link = PROCESSED / "latest"
    if link.is_symlink():
        return Path(link).resolve().name
    ptr = PROCESSED / "latest_run.txt"
    if ptr.exists():
        return ptr.read_text().strip()
    runs = sorted([p.name for p in PROCESSED.iterdir() if p.is_dir() and p.name[:4].isdigit()]) if PROCESSED.exists() else []
    return runs[-1] if runs else None


def previous_run_date(current: str) -> str | None:
    """The most recent processed run strictly before `current`."""
    if not PROCESSED.exists():
        return None
    runs = sorted([p.name for p in PROCESSED.iterdir() if p.is_dir() and p.name[:4].isdigit()])
    prior = [r for r in runs if r < current]
    return prior[-1] if prior else None
