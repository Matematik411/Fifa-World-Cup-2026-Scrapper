"""Load and consolidate every input into one validated bundle.

Claude-curated research (data/manual/*.json) + the live FIFA fantasy feed.
Fails loudly if a required input is missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import MANUAL
from ..io_utils import load_json
from ..model.teams import normalize_team, register_canonical
from . import fifa_fantasy


@dataclass
class Bundle:
    fixtures: dict
    ratings_odds: dict
    squads_research: dict
    nostradamus: dict
    players: list = field(default_factory=list)
    squads_map: dict = field(default_factory=dict)
    teams: list = field(default_factory=list)
    fantasy_rounds: object = None
    player_stats: dict = field(default_factory=dict)
    lineups: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def _merge_odds_extra(ratings_odds: dict, extra: dict) -> None:
    """Merge data/manual/odds_extra.json into ratings_odds in place.

    Per-match odds are deduped by the unordered team pair; the extra (sharper /
    Pinnacle) entry wins. Pinnacle title odds and sources are attached.
    """
    if not extra:
        return
    base = ratings_odds.get("match_odds") or []
    merged: dict = {}

    def key(mo):
        return frozenset((normalize_team(mo.get("home", "")), normalize_team(mo.get("away", ""))))

    for mo in base:
        merged[key(mo)] = dict(mo)
    # Field-level overlay: extra (sharper) values win, but a null 1X2 in extra must
    # not wipe a working 1X2 already present in the base entry.
    for mo in (extra.get("match_odds") or []):
        k = key(mo)
        if k in merged:
            for fld, val in mo.items():
                if val is not None:
                    merged[k][fld] = val
        else:
            merged[k] = dict(mo)
    ratings_odds["match_odds"] = list(merged.values())
    if extra.get("title_odds_pinnacle"):
        ratings_odds["title_odds_pinnacle"] = extra["title_odds_pinnacle"]
    ratings_odds["sources"] = (ratings_odds.get("sources") or []) + (extra.get("sources") or [])


def load_bundle(cfg, run_date: str, fetch: bool = True, log=print) -> Bundle:
    warnings: list[str] = []

    def _load(name, required=True):
        path = MANUAL / name
        if not path.exists():
            if required:
                raise FileNotFoundError(
                    f"Required research input {path} is missing. Re-run the research step "
                    f"(see RUNBOOK.md) to regenerate data/manual/*.json.")
            warnings.append(f"Optional input {name} missing.")
            return {}
        return load_json(path)

    fixtures = _load("fixtures.json")
    ratings_odds = _load("ratings_odds.json")
    squads_research = _load("squads.json", required=False)
    nostradamus = _load("nostradamus.json", required=False)
    player_stats = _load("player_stats.json", required=False)
    lineups = _load("lineups.json", required=False)

    # Merge expanded / sharper odds (Pinnacle, more matchdays) non-destructively.
    odds_extra = _load("odds_extra.json", required=False)
    n_before = len(ratings_odds.get("match_odds") or [])
    _merge_odds_extra(ratings_odds, odds_extra)
    n_after = len(ratings_odds.get("match_odds") or [])
    if n_after != n_before:
        warnings.append(f"Merged odds_extra: match_odds {n_before} -> {n_after}.")

    teams = [normalize_team(t) for t in fixtures["teams"]]
    register_canonical(teams)
    if len(teams) != 48:
        warnings.append(f"Expected 48 teams, found {len(teams)}.")

    # fantasy feed (live with cache fallback)
    players, squads_map, rounds = [], {}, None
    if fetch:
        log("Fetching FIFA Fantasy feed...")
        feed = fifa_fantasy.fetch_fantasy(run_date, log=log)
        players = fifa_fantasy.player_list(feed.get("players"))
        squads_map = fifa_fantasy.build_squads_map(feed.get("squads"))
        rounds = feed.get("rounds")
    else:
        # use latest cached pulls without hitting the network
        cached_players = fifa_fantasy._latest_cached("players")
        cached_squads = fifa_fantasy._latest_cached("squads")
        if cached_players:
            players = fifa_fantasy.player_list(load_json(cached_players))
        if cached_squads:
            squads_map = fifa_fantasy.build_squads_map(load_json(cached_squads))
    if not players:
        warnings.append("No fantasy player pool available — fantasy optimization will be skipped.")
    if not squads_map:
        warnings.append("No fantasy squads map — player→nation join unavailable.")

    return Bundle(
        fixtures=fixtures, ratings_odds=ratings_odds, squads_research=squads_research,
        nostradamus=nostradamus, players=players, squads_map=squads_map, teams=teams,
        fantasy_rounds=rounds, player_stats=player_stats, lineups=lineups, warnings=warnings,
    )
