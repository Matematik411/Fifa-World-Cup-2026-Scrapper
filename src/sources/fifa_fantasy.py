"""Official FIFA World Cup Fantasy feed (authoritative player pool & pricing).

Endpoints (unauthenticated JSON), discovered from the play.fifa.com SPA bundle:
  https://play.fifa.com/json/fantasy/players.json   (full pool: id, names, squadId, position, price, percentSelected, stats)
  https://play.fifa.com/json/fantasy/squads.json     (48 nations: id, name, group, abbr, isEliminated)
  https://play.fifa.com/json/fantasy/rounds.json      (rounds + fixtures)

Each run fetches live and caches to data/raw/<date>/; falls back to the most
recent cached pull if the network is unavailable.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from ..config import RAW
from ..io_utils import save_json
from ..model.teams import normalize_team

BASE = "https://play.fifa.com/json/fantasy"
ENDPOINTS = {"players": f"{BASE}/players.json", "squads": f"{BASE}/squads.json", "rounds": f"{BASE}/rounds.json"}
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _latest_cached(name: str) -> Path | None:
    """Most recent cached raw file for an endpoint across all run-date dirs."""
    candidates = sorted(RAW.glob(f"*/fantasy_{name}_raw.json"))
    return candidates[-1] if candidates else None


def _fetch_one(client: httpx.Client, name: str, url: str, run_date: str, log=print) -> object | None:
    raw_path = RAW / run_date / f"fantasy_{name}_raw.json"
    try:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
        save_json(raw_path, data)
        log(f"  fantasy/{name}: live OK ({len(data) if isinstance(data, list) else 'object'})")
        return data
    except Exception as e:  # noqa: BLE001
        cached = raw_path if raw_path.exists() else _latest_cached(name)
        if cached and cached.exists():
            log(f"  fantasy/{name}: live failed ({type(e).__name__}); using cache {cached.parent.name}")
            return json.loads(cached.read_text())
        log(f"  fantasy/{name}: live failed and no cache available ({e})")
        return None


def fetch_fantasy(run_date: str, log=print) -> dict:
    out: dict = {}
    with httpx.Client(timeout=25.0, headers={"User-Agent": _UA, "Accept": "application/json"}, follow_redirects=True) as client:
        for name, url in ENDPOINTS.items():
            out[name] = _fetch_one(client, name, url, run_date, log)
    return out


def build_squads_map(squads_raw) -> dict[int, dict]:
    """squadId -> {nation(canonical), group(upper), abbr, eliminated}."""
    out: dict[int, dict] = {}
    if not squads_raw:
        return out
    rows = squads_raw if isinstance(squads_raw, list) else squads_raw.get("data", [])
    for s in rows:
        out[s["id"]] = {
            "nation": normalize_team(s.get("name", "")),
            "group": str(s.get("group", "")).upper(),
            "abbr": s.get("abbr", ""),
            "eliminated": bool(s.get("isEliminated", False)),
        }
    return out


def player_list(players_raw) -> list[dict]:
    if not players_raw:
        return []
    return players_raw if isinstance(players_raw, list) else players_raw.get("data", [])
