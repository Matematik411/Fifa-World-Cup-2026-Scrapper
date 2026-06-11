"""Per-player expected fantasy points.

Decomposes each player's expected points per match by position from the shared
model's per-match team goals + clean-sheet probability, the player's share of
team goal involvement, and minutes/rotation probability. Then horizon-weights by
the team's advancement probabilities (you can't churn players freely after
lockout, so longevity is value).

Goal/assist shares come from **real per-90 underlying numbers** (xG/xA from
data/manual/player_stats.json) when available, falling back to a position+price
heuristic on the same goals-per-90 scale. Minutes use confirmed/predicted
line-ups (data/manual/lineups.json) and squad-news status when available.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

import numpy as np

from ..model.teams import normalize_team


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c))
    return s.strip().lower()


def _display_name(p: dict) -> str:
    return p.get("knownName") or " ".join(x for x in [p.get("firstName"), p.get("lastName")] if x).strip() or f"Player {p.get('id')}"


@dataclass
class PlayerProj:
    pid: int
    name: str
    nation: str
    group: str
    position: str
    price: float
    ownership: float
    minutes_prob: float
    exp_next: float
    exp_avg: float
    horizon: float
    per_match: dict
    next_date: str = ""          # date of the next unplayed match ("" if none known)
    tags: list[str] = field(default_factory=list)
    why: str = ""

    def to_record(self) -> dict:
        d = dict(self.__dict__)
        for k in ("price", "ownership", "minutes_prob", "exp_next", "exp_avg", "horizon"):
            d[k] = round(float(d[k]), 3)
        d["per_match"] = {k: round(float(v), 3) for k, v in self.per_match.items()}
        return d


# verified position scoring constants
GOAL_PTS = {"GK": 9, "DEF": 7, "MID": 6, "FWD": 5}
CS_PTS = {"GK": 5, "DEF": 5, "MID": 1, "FWD": 0}
ASSIST_PTS = 3
ASSIST_RATE = 0.75    # share of goals that carry an assist
# typical per-90 rates for a STARTER at each position (goals-per-90 scale; xG-comparable)
BASE_GOAL_RATE = {"GK": 0.0, "DEF": 0.06, "MID": 0.17, "FWD": 0.42}
BASE_CRE_RATE = {"GK": 0.0, "DEF": 0.08, "MID": 0.22, "FWD": 0.18}


def _team_match_env(forecast, fixtures: dict) -> dict:
    """Per-team list of forecast match environments — group games plus any
    knockout tie whose teams are already known (those are certain to happen)."""
    env: dict[str, list] = {}
    for num, mf in forecast.match_forecasts.items():
        P = mf.P
        home_goal_marg = P.sum(axis=1)
        away_goal_marg = P.sum(axis=0)
        env.setdefault(mf.home, []).append({
            "num": num, "round": mf.round, "lam_for": mf.lam_home, "cs_prob": float(P[:, 0].sum()),
            "opp_goal_marg": away_goal_marg, "opp": mf.away, "is_home": True, "date": mf.date})
        env.setdefault(mf.away, []).append({
            "num": num, "round": mf.round, "lam_for": mf.lam_away, "cs_prob": float(P[0, :].sum()),
            "opp_goal_marg": home_goal_marg, "opp": mf.home, "is_home": False, "date": mf.date})
    for t in env:
        env[t].sort(key=lambda x: (x["date"] or "9999-99-99", x["num"]))
    return env


def _expected_extra_conceded(goal_marg: np.ndarray) -> float:
    c = np.arange(len(goal_marg))
    return float((np.maximum(c - 1, 0) * goal_marg).sum())


class _NameMatcher:
    """Match a research/stat name onto a feed player's display name."""
    @staticmethod
    def matches(cand, player_name: str) -> bool:
        pn = _norm(player_name)
        if isinstance(cand, str):
            cand = [cand]
        for nm in cand or []:
            tok = _norm(nm).split("(")[0].strip()
            if not tok:
                continue
            if tok in pn or tok.split()[-1] in pn.split():
                return True
        return False


class ResearchIndex:
    def __init__(self, squads_research: dict):
        self.by_nation: dict[str, dict] = {}
        for nation, info in (squads_research.get("teams") or {}).items():
            self.by_nation[normalize_team(nation)] = info

    def lookup(self, nation: str, player_name: str) -> dict:
        info = self.by_nation.get(normalize_team(nation))
        if not info:
            return {}
        m = _NameMatcher.matches
        out = {
            "is_pen": m(info.get("penalty_taker") or info.get("penalty_takers"), player_name),
            "is_fk": m(info.get("fk_taker") or info.get("free_kick_taker"), player_name),
            "nailed": m(info.get("nailed_starters"), player_name),
            "rotation": m(info.get("rotation_risk"), player_name),
            "key_att": m(info.get("key_attackers"), player_name),
            "key_cre": m(info.get("key_creators"), player_name),
            "in_xi": m(info.get("likely_xi"), player_name),
            "injury_text": "",
        }
        for inj in info.get("injuries_suspensions") or []:
            if m([str(inj).split("-")[0].split("(")[0]], player_name):
                out["injury_text"] = inj
        return out


class StatsIndex:
    """Per-90 attacking stats from data/manual/player_stats.json."""
    def __init__(self, player_stats: dict):
        self.by_nation: dict[str, list] = {}
        for nation, rows in (player_stats.get("players") or {}).items():
            self.by_nation[normalize_team(nation)] = rows or []

    def lookup(self, nation: str, player_name: str) -> dict | None:
        for row in self.by_nation.get(normalize_team(nation), []):
            if _NameMatcher.matches(row.get("name", ""), player_name):
                return row
        return None


class LineupIndex:
    """Confirmed/predicted line-ups from data/manual/lineups.json (day-of overrides)."""
    def __init__(self, lineups: dict):
        self.by_nation = {normalize_team(k): v for k, v in (lineups.get("teams") or {}).items()}

    def status(self, nation: str, player_name: str) -> str | None:
        info = self.by_nation.get(normalize_team(nation))
        if not info:
            return None
        if _NameMatcher.matches(info.get("out") or [], player_name):
            return "out"
        if _NameMatcher.matches(info.get("xi") or [], player_name):
            return "confirmed_xi" if info.get("confirmed") else "predicted_xi"
        if info.get("confirmed"):
            return "confirmed_bench"
        return None


def _minutes_prob(p: dict, rs: dict, lineup_status: str | None, stat: dict | None,
                  is_first_choice_gk: bool) -> float:
    pos = p["position"]
    price = float(p["price"])
    own = float(p.get("percentSelected") or 0.0)
    # 1) confirmed/predicted line-up wins
    if lineup_status == "out":
        return 0.02
    if lineup_status == "confirmed_xi":
        return 0.98
    if lineup_status == "confirmed_bench":
        return 0.10
    if lineup_status == "predicted_xi":
        return 0.90
    # 2) injuries / suspensions
    if rs.get("injury_text"):
        t = rs["injury_text"].lower()
        if any(w in t for w in ["out", "acl", "ruled out", "omitted", "torn", "season", "suspend"]):
            return 0.02
        return 0.30
    # 3) GK: only the first choice plays
    if pos == "GK":
        return 0.96 if is_first_choice_gk else 0.03
    # 4) squad-news status
    if rs.get("nailed"):
        base = 0.93
    elif rs.get("in_xi"):
        base = 0.88
    elif rs.get("rotation"):
        base = 0.52
    else:
        price_h = np.clip(0.40 + 0.06 * (price - 3.5), 0.40, 0.90)
        own_h = np.clip(0.50 + own / 45.0, 0.50, 0.93) if own > 6 else 0.0
        base = float(max(price_h, own_h))
    # 5) season minutes sanity (very low minutes -> fringe/injured)
    if stat and stat.get("minutes") is not None:
        mins = float(stat["minutes"])
        if mins < 600:
            base = min(base, 0.45)
        elif mins > 2200:
            base = max(base, 0.80)
    return float(base)


def _rates(p: dict, rs: dict, stat: dict | None) -> tuple[float, float]:
    """(goal_rate_p90, assist_rate_p90) — real underlying numbers if available."""
    pos = p["position"]
    price = float(p["price"])
    if stat:
        g = stat.get("xg_p90"); gg = stat.get("goals_p90")
        a = stat.get("xa_p90"); ga = stat.get("assists_p90"); kp = stat.get("key_passes_p90")
        if g is not None and gg is not None:
            goal_rate = 0.55 * float(g) + 0.45 * float(gg)
        elif g is not None:
            goal_rate = float(g)
        elif gg is not None:
            goal_rate = float(gg)
        else:
            goal_rate = None
        if a is not None and ga is not None:
            cre_rate = 0.55 * float(a) + 0.45 * float(ga)
        elif a is not None:
            cre_rate = float(a)
        elif ga is not None:
            cre_rate = float(ga)
        elif kp is not None:
            cre_rate = 0.1 * float(kp)
        else:
            cre_rate = None
    else:
        goal_rate = cre_rate = None
    # heuristic fallback on the same goals-per-90 scale
    pf = float(np.clip((price / 5.5) ** 1.0, 0.5, 2.2))
    if goal_rate is None:
        goal_rate = BASE_GOAL_RATE[pos] * pf
    if cre_rate is None:
        cre_rate = BASE_CRE_RATE[pos] * (pf ** 0.8)
    # WC penalty taker upside (may differ from club): add expected pens/90 * conversion
    if rs.get("is_pen"):
        goal_rate += 0.10
    return max(goal_rate, 0.0), max(cre_rate, 0.0)


def build_projections(players: list[dict], squads_map: dict, forecast, advancement: dict,
                      squads_research: dict, fixtures: dict, cfg,
                      player_stats: dict | None = None, lineups: dict | None = None,
                      played: set[int] | None = None) -> list[PlayerProj]:
    env = _team_match_env(forecast, fixtures)
    played = set(played or ())
    ridx = ResearchIndex(squads_research or {})
    sidx = StatsIndex(player_stats or {})
    lidx = LineupIndex(lineups or {})

    by_nation: dict[str, list[dict]] = {}
    nation_out: dict[str, bool] = {}
    for p in players:
        if p.get("status") != "playing":
            continue
        nation = squads_map.get(p["squadId"], {}).get("nation")
        if not nation:
            continue
        p = dict(p)
        p["_nation"] = normalize_team(nation)
        p["_group"] = squads_map.get(p["squadId"], {}).get("group", "")
        nation_out[p["_nation"]] = bool(squads_map.get(p["squadId"], {}).get("eliminated"))
        by_nation.setdefault(p["_nation"], []).append(p)

    gk_first: dict[str, int] = {}
    for nation, plist in by_nation.items():
        gks = [pp for pp in plist if pp["position"] == "GK"]
        if gks:
            gk_first[nation] = max(gks, key=lambda x: float(x["price"]))["id"]

    meta: dict[int, dict] = {}
    for nation, plist in by_nation.items():
        for p in plist:
            name = _display_name(p)
            rs = ridx.lookup(nation, name)
            stat = sidx.lookup(nation, name)
            lstat = lidx.status(nation, name)
            mins = _minutes_prob(p, rs, lstat, stat, is_first_choice_gk=(p["id"] == gk_first.get(nation)))
            goal_rate, cre_rate = _rates(p, rs, stat)
            meta[p["id"]] = {"name": name, "rs": rs, "stat": stat, "mins": mins,
                             "att_w": goal_rate * mins, "cre_w": cre_rate * mins,
                             "price": float(p["price"]), "own": float(p.get("percentSelected") or 0.0),
                             "has_stat": stat is not None}

    team_att_sum = {n: (sum(meta[p["id"]]["att_w"] for p in pl) or 1.0) for n, pl in by_nation.items()}
    team_cre_sum = {n: (sum(meta[p["id"]]["cre_w"] for p in pl) or 1.0) for n, pl in by_nation.items()}

    projs: list[PlayerProj] = []
    for nation, plist in by_nation.items():
        matches = env.get(nation, [])
        upcoming = [mx for mx in matches if mx["num"] not in played]
        adv = advancement.get(nation, {})
        # Expected matches still to play (advancement conditions on entered results);
        # the known fixtures in `upcoming` are certain, the rest is the KO residual.
        exp_remaining = float(adv.get("exp_remaining_matches",
                                      adv.get("exp_ko_matches", 0.0)))
        if nation_out.get(nation):
            upcoming, exp_remaining = [], 0.0
        residual = max(0.0, exp_remaining - len(upcoming))
        for p in plist:
            m = meta[p["id"]]
            goal_share = m["att_w"] / team_att_sum[nation]
            assist_share = m["cre_w"] / team_cre_sum[nation]
            all_eps = {mx["num"]: _player_match_ep(p["position"], m, goal_share, assist_share, mx)
                       for mx in matches}
            per_match = {mx["num"]: all_eps[mx["num"]] for mx in upcoming}
            upcoming_eps = list(per_match.values())
            # per-match typical EP — over all known fixtures (played ones included for
            # stability) — used to price the unknown-opponent KO residual
            exp_avg = float(np.mean(list(all_eps.values()))) if all_eps else 0.0
            if upcoming_eps:
                exp_next = upcoming_eps[0]
            else:
                # still alive but next tie not yet in fixtures (e.g. opponent TBD)
                exp_next = exp_avg if residual > 0.5 else 0.0
            horizon = float(sum(upcoming_eps) + exp_avg * 0.88 * residual)
            projs.append(PlayerProj(
                pid=p["id"], name=m["name"], nation=nation, group=str(p.get("_group", "")).upper(),
                position=p["position"], price=m["price"], ownership=m["own"], minutes_prob=m["mins"],
                exp_next=exp_next, exp_avg=exp_avg, horizon=horizon, per_match=per_match,
                next_date=(upcoming[0]["date"] or "") if upcoming else "",
                tags=_tags(p["position"], m), why=_why(p["position"], m, adv)))
    return projs


def _player_match_ep(pos: str, m: dict, goal_share: float, assist_share: float, mx: dict) -> float:
    mins = m["mins"]
    p60 = mins * 0.92
    lam_for = mx["lam_for"]
    stat = m.get("stat") or {}
    exp_goals = lam_for * goal_share
    # Goalkeepers don't score from open play; cap defenders' share so an
    # attacker-depleted team's goals don't leak onto nailed full-backs.
    if pos == "GK":
        exp_goals = 0.0
    elif pos == "DEF":
        exp_goals = min(exp_goals, lam_for * 0.06)
    exp_assists = lam_for * ASSIST_RATE * assist_share
    pts = 1.0 * min(0.98, mins + 0.06)              # appearance
    pts += exp_goals * GOAL_PTS[pos]
    pts += exp_assists * ASSIST_PTS
    if pos in ("GK", "DEF"):
        pts += CS_PTS[pos] * mx["cs_prob"] * p60
        pts -= _expected_extra_conceded(mx["opp_goal_marg"]) * p60
    elif pos == "MID":
        pts += CS_PTS["MID"] * mx["cs_prob"] * p60
        kp = stat.get("key_passes_p90")
        cc = (kp / 2.0) if kp else (0.85 + 1.1 * assist_share)   # chances created (1 per 2)
        pts += (cc + 0.7) * mins                                  # + tackles approx
    if pos == "GK":
        exp_saves = 2.3 * float((np.arange(len(mx["opp_goal_marg"])) * mx["opp_goal_marg"]).sum())
        pts += (exp_saves / 3.0) * mins
    if pos == "FWD":
        sot = stat.get("sot_p90")
        pts += ((sot / 2.0) if sot else (0.6 + 18.0 * goal_share * 0.1)) * mins   # shots on target (1 per 2)
    if m["rs"].get("is_fk"):
        pts += 0.05
    pts += 0.10 * (goal_share * 3)                  # winning penalties (attackers), small
    pts -= (0.03 if pos == "GK" else 0.09)          # expected cards
    # Sub-5% scouting bonus (+2) is real EV but only when a >4-pt score is plausible —
    # size it by an explicit P(>4) proxy so it rewards genuine differentials, not fringe fillers.
    if m["own"] < 5.0:
        p_gt4 = max(0.0, min(0.28, (pts - 3.5) / 12.0))
        pts += 2.0 * p_gt4
    return max(pts, 0.0)


def _tags(pos: str, m: dict) -> list[str]:
    rs = m["rs"]
    t = []
    if rs.get("is_pen"):
        t.append("penalties")
    if rs.get("is_fk"):
        t.append("free-kicks")
    if rs.get("nailed") or rs.get("in_xi"):
        t.append("nailed")
    if rs.get("rotation"):
        t.append("rotation risk")
    if rs.get("injury_text"):
        t.append("injury doubt")
    if m["own"] < 5.0:
        t.append("differential")
    if m["has_stat"]:
        t.append("xG-backed")
    if m["mins"] < 0.4:
        t.append("bench risk")
    return t


def _why(pos: str, m: dict, adv: dict) -> str:
    rs = m["rs"]
    bits = []
    if rs.get("is_pen"):
        bits.append("penalty taker")
    if rs.get("nailed") or rs.get("in_xi"):
        bits.append("nailed starter")
    elif m["mins"] < 0.5:
        bits.append(f"{m['mins']:.0%} start prob")
    stat = m.get("stat")
    if stat and stat.get("xg_p90") is not None and pos in ("MID", "FWD"):
        bits.append(f"{float(stat['xg_p90']) + float(stat.get('xa_p90') or 0):.2f} xG+xA/90")
    if pos in ("GK", "DEF"):
        bits.append("clean-sheet upside")
    if (adv.get("reach_R16") or 0) > 0.6:
        bits.append("likely deep run")
    return ", ".join(bits) if bits else "model projection"
