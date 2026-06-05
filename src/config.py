"""Configuration loading: config.yaml + .env + live-verified rule overrides.

The build session writes verified game rules to data/manual/{fantasy_rules,nostradamus}.json;
those override the defaults baked into config.yaml so the optimizers always use the
latest live-confirmed values.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MANUAL = DATA / "manual"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
OUTPUT = ROOT / "output"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (override wins for leaves)."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """Thin dotted-access wrapper over the merged config dict."""

    def __init__(self, data: dict[str, Any]):
        self._d = data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._d
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def __getitem__(self, key: str) -> Any:
        return self._d[key]

    @property
    def raw(self) -> dict:
        return self._d


def _safe_load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def load_config(config_path: Path | None = None) -> Config:
    """Load config.yaml, overlay live-verified rule files, and load .env."""
    load_dotenv(ROOT / ".env")
    cfg_path = config_path or (ROOT / "config.yaml")
    data = yaml.safe_load(cfg_path.read_text()) or {}

    # Overlay verified fantasy rules (scoring/squad/transfers/chips) when present.
    fr = _safe_load_json(MANUAL / "fantasy_rules.json")
    if fr:
        fantasy = dict(data.get("fantasy", {}))
        if "squad" in fr and isinstance(fr["squad"], dict):
            sq = fr["squad"]
            if "composition" in sq and isinstance(sq["composition"], dict):
                fantasy["squad"] = sq["composition"]
            for key_src, key_dst in (("budget", "budget"), ("ko_budget", "ko_budget"),
                                     ("nation_cap", "nation_cap"), ("formations", "formations")):
                if key_src in sq:
                    fantasy[key_dst] = sq[key_src]
        if "transfers" in fr and isinstance(fr["transfers"], dict):
            tr = fr["transfers"]
            if "free_per_round" in tr:
                fantasy["free_transfers_per_round"] = tr["free_per_round"]
            if "extra_transfer_penalty" in tr:
                fantasy["extra_transfer_penalty"] = tr["extra_transfer_penalty"]
        fantasy["scoring_verified"] = fr.get("scoring", {})
        fantasy["chips"] = fr.get("chips", fantasy.get("chips", []))
        fantasy["rules_confidence"] = fr.get("confidence", "Med")
        fantasy["rules_sources"] = fr.get("sources", [])
        data["fantasy"] = fantasy

    nm = _safe_load_json(MANUAL / "nostradamus.json")
    if nm and "scoring" in nm:
        nos = dict(data.get("nostradamus", {}))
        nos.update(nm["scoring"])
        if "doubling" in nm:
            nos["ko_multiplier"] = nm["doubling"].get("ko_multiplier", nos.get("ko_multiplier", 2))
            nos["ko_doubling_starts"] = nm["doubling"].get("starts_round", nos.get("ko_doubling_starts", "R32"))
        nos["match_coverage"] = nm.get("match_coverage", "all_104")
        nos["confidence"] = nm.get("confidence", "Med")
        nos["sources"] = nm.get("sources", [])
        data["nostradamus"] = nos

    return Config(data)


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def ensure_dirs() -> None:
    for d in (DATA, MANUAL, RAW, PROCESSED, OUTPUT):
        d.mkdir(parents=True, exist_ok=True)
