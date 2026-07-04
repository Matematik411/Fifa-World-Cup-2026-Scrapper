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

import math
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
    round_points: dict = field(default_factory=dict)   # live feed: fantasy round id -> banked pts
    total_points: float = 0.0                          # live feed: tournament total so far
    next_minutes: float = 0.0                          # per-match start prob for the NEXT match (rest-adjusted)
    # U7 (player correlation): the data the joint-scoreline sampler needs for the NEXT match
    next_num: int = 0                                  # match number of the next unplayed fixture
    next_is_home: bool = True                          # this team is the home side in that match
    goal_share: float = 0.0                            # share of the team's goals (intra-team)
    assist_share: float = 0.0                          # share of the team's assists (intra-team)

    def to_record(self) -> dict:
        d = dict(self.__dict__)
        for k in ("price", "ownership", "minutes_prob", "next_minutes", "exp_next", "exp_avg",
                  "horizon", "goal_share", "assist_share"):
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


KO_ROUNDS = {"R32", "R16", "QF", "SF", "final", "third-place"}


def _et_adjust(entry: dict, p00: float, p_draw: float, lam_opp: float, et_factor: float) -> None:
    """U10 — FIFA Fantasy scores EXTRA TIME (verbatim rule: "not including
    shootouts"), but every match env above is built from the 90' scoreline
    matrix. In a KO tie the scored window stretches ~30' with prob P(draw@90):

      * attacking volume: E[team goals in scored window] = λ90·(1 + p_draw·f)
      * clean sheet: a 90' CS dies if the opponent scores in ET, which needs
        the tie level at 90' → cs = cs90 − P(0-0@90)·P(opp scores in ET)
      * concessions (GK/DEF malus + GK saves): mix the opponent-goals marginal
        toward one-more-goal with the same conditional weight

    Nostradamus/GoPicks stay pure 90' — their scoring resolves at 90'."""
    p_et_concede = 1.0 - math.exp(-lam_opp * et_factor)
    entry["lam_for"] = entry["lam_for"] * (1.0 + p_draw * et_factor)
    entry["cs_prob"] = max(0.0, entry["cs_prob"] - p00 * p_et_concede)
    w = p_draw * p_et_concede
    marg = entry["opp_goal_marg"]
    shifted = np.concatenate(([0.0], marg[:-1]))
    shifted[-1] += marg[-1]                       # keep the tail mass — stay a distribution
    entry["opp_goal_marg"] = (1.0 - w) * marg + w * shifted


def _team_match_env(forecast, fixtures: dict, stakes: dict | None = None) -> dict:
    """Per-team list of forecast match environments — group games plus any
    knockout tie whose teams are already known (those are certain to happen).
    Each entry carries the team's dead-rubber `team_state` for that match
    (clinched/eliminated/live, from src/model/standings) so minutes can be
    rested per-fixture. Knockout entries get the U10 extra-time adjustment."""
    stakes = stakes or {}
    _cfg = getattr(forecast, "cfg", None)
    et_factor = float(_cfg.get("model.ko_et_goal_factor", 0.28)) if _cfg is not None else 0.0
    env: dict[str, list] = {}
    for num, mf in forecast.match_forecasts.items():
        P = mf.P
        home_goal_marg = P.sum(axis=1)
        away_goal_marg = P.sum(axis=0)
        sk = stakes.get(num, {})
        he = {"num": num, "round": mf.round, "lam_for": mf.lam_home, "cs_prob": float(P[:, 0].sum()),
              "opp_goal_marg": away_goal_marg, "opp": mf.away, "is_home": True, "date": mf.date,
              "team_state": sk.get("home_state", "live")}
        ae = {"num": num, "round": mf.round, "lam_for": mf.lam_away, "cs_prob": float(P[0, :].sum()),
              "opp_goal_marg": home_goal_marg, "opp": mf.home, "is_home": False, "date": mf.date,
              "team_state": sk.get("away_state", "live")}
        if mf.round in KO_ROUNDS and et_factor > 0:
            p00 = float(P[0, 0])
            _et_adjust(he, p00, mf.p_draw, mf.lam_away, et_factor)
            _et_adjust(ae, p00, mf.p_draw, mf.lam_home, et_factor)
        env.setdefault(mf.home, []).append(he)
        env.setdefault(mf.away, []).append(ae)
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


class WCFormIndex:
    """In-tournament per-player form from data/manual/wc_form.json (U5).

    Same `players: {nation: [rows]}` shape as StatsIndex; each row carries WC totals
    (wc_minutes/wc_goals/wc_assists/wc_xg/wc_xa/wc_shots). Consumed by `_rates` to
    shrink the club-season per-90 toward in-tournament evidence, minutes-weighted.
    """
    def __init__(self, wc_form: dict):
        self.by_nation: dict[str, list] = {}
        for nation, rows in (wc_form.get("players") or {}).items():
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


def _rates(p: dict, rs: dict, stat: dict | None, wc: dict | None = None,
           k_form: float = 600.0) -> tuple[float, float]:
    """(goal_rate_p90, assist_rate_p90) — club-season underlying numbers, shrunk toward
    in-tournament WC form (U5) when available."""
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
    # --- U5: shrink toward in-tournament form, minutes-weighted ---
    # Lean on xG/xA (process — sticky, captures a real role change) over goals/assists
    # (finishing — noisy, regresses), and weight by WC minutes so two games barely move
    # the club-season prior (m=180 -> ~23% weight at k_form=600; it grows as the run goes).
    if wc:
        m = float(wc.get("wc_minutes") or 0.0)
        if m >= 1.0:
            w = m / (m + k_form)
            wc_goal = (0.7 * float(wc.get("wc_xg") or 0.0) + 0.3 * float(wc.get("wc_goals") or 0.0)) * 90.0 / m
            wc_cre = (0.7 * float(wc.get("wc_xa") or 0.0) + 0.3 * float(wc.get("wc_assists") or 0.0)) * 90.0 / m
            goal_rate = (1.0 - w) * goal_rate + w * wc_goal
            cre_rate = (1.0 - w) * cre_rate + w * wc_cre
    # WC penalty taker upside (may differ from club): add expected pens/90 * conversion
    if rs.get("is_pen"):
        goal_rate += 0.10
    return max(goal_rate, 0.0), max(cre_rate, 0.0)


def build_projections(players: list[dict], squads_map: dict, forecast, advancement: dict,
                      squads_research: dict, fixtures: dict, cfg,
                      player_stats: dict | None = None, lineups: dict | None = None,
                      played: set[int] | None = None, stakes: dict | None = None,
                      wc_form: dict | None = None) -> list[PlayerProj]:
    env = _team_match_env(forecast, fixtures, stakes)
    rest_factor = float(cfg.get("fantasy.rotation_rest_factor", 0.6)) if cfg else 0.6
    k_form = float(cfg.get("fantasy.wc_form_shrink_minutes", 600)) if cfg else 600.0
    played = set(played or ())
    ridx = ResearchIndex(squads_research or {})
    sidx = StatsIndex(player_stats or {})
    lidx = LineupIndex(lineups or {})
    wcidx = WCFormIndex(wc_form or {})

    by_nation: dict[str, list[dict]] = {}
    nation_out: dict[str, bool] = {}
    for p in players:
        status = p.get("status")
        # Keep ELIMINATED players in the pool (forced to zero future EP via nation_out
        # below) so a LOCKED squad that still holds one — a knocked-out team's player who
        # already banked this round's points — can still be assembled and his banked
        # round-points counted in the live tally. They have exp_next 0 so the optimiser
        # never picks them. Genuinely unavailable players (transferred/injured/suspended)
        # stay filtered out.
        if status not in ("playing", "eliminated"):
            continue
        nation = squads_map.get(p["squadId"], {}).get("nation")
        if not nation:
            continue
        p = dict(p)
        p["_nation"] = normalize_team(nation)
        p["_group"] = squads_map.get(p["squadId"], {}).get("group", "")
        elim = bool(squads_map.get(p["squadId"], {}).get("eliminated")) or status == "eliminated"
        nation_out[p["_nation"]] = nation_out.get(p["_nation"], False) or elim
        by_nation.setdefault(p["_nation"], []).append(p)

    gk_first: dict[str, int] = {}
    for nation, plist in by_nation.items():
        gks = [pp for pp in plist if pp["position"] == "GK"]
        if not gks:
            continue
        # The starting keeper is whoever the known line-up names; the price heuristic
        # is only a fallback when no line-up is available. If the line-up names a
        # keeper who ISN'T one of our pooled GKs (an un-ownable #1, e.g. a backup-
        # priced Raya behind Unai Simón), then NONE of the pooled GKs start — leave
        # the nation out of gk_first so they're all treated as bench (won't play).
        xi = (lidx.by_nation.get(normalize_team(nation)) or {}).get("xi") or []
        if not xi:
            # No day-of line-up posted yet: take the #1 keeper from squad-news
            # likely_xi ("GK <name>"). This stops a backup who is merely priced like
            # a starter (e.g. David Raya, $5.0) from being assumed first-choice over
            # the nation's actual #1 (Unai Simón) just because he is the costliest
            # pooled GK. If the named #1 isn't in our pool, all pooled GKs are bench.
            info = ridx.by_nation.get(normalize_team(nation)) or {}
            xi = [str(e).strip()[3:] for e in (info.get("likely_xi") or [])
                  if str(e).strip()[:3].upper() == "GK "]
        named = [g for g in gks if _NameMatcher.matches(xi, _display_name(g))] if xi else []
        if xi and not named:
            continue
        gk_first[nation] = (named[0] if named else max(gks, key=lambda x: float(x["price"])))["id"]

    meta: dict[int, dict] = {}
    for nation, plist in by_nation.items():
        for p in plist:
            name = _display_name(p)
            rs = ridx.lookup(nation, name)
            stat = sidx.lookup(nation, name)
            wc = wcidx.lookup(nation, name)
            lstat = lidx.status(nation, name)
            mins = _minutes_prob(p, rs, lstat, stat, is_first_choice_gk=(p["id"] == gk_first.get(nation)))
            goal_rate, cre_rate = _rates(p, rs, stat, wc, k_form)
            meta[p["id"]] = {"name": name, "rs": rs, "stat": stat, "mins": mins,
                             "att_w": goal_rate * mins, "cre_w": cre_rate * mins,
                             "price": float(p["price"]), "own": float(p.get("percentSelected") or 0.0),
                             "has_stat": stat is not None, "has_wc": wc is not None}

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
            st = p.get("stats") or {}
            rp = st.get("roundPoints")
            live_rounds = {str(k): float(v) for k, v in rp.items()} if isinstance(rp, dict) else {}
            goal_share = m["att_w"] / team_att_sum[nation]
            assist_share = m["cre_w"] / team_cre_sum[nation]
            # Per-match minutes (U6/U3): a nailed starter (high baseline) is rested in a
            # clinched team's dead-rubber last group game -> reduced start prob for THAT
            # fixture only. Every other fixture keeps the baseline, so normal-stakes
            # behaviour is unchanged.
            base_mins = m["mins"]
            nailed = base_mins >= 0.7

            def _eff(mx, _base=base_mins, _nailed=nailed):
                if _nailed and mx.get("team_state") == "clinched":
                    return _base * rest_factor
                return _base

            all_eps = {mx["num"]: _player_match_ep(p["position"], m, goal_share, assist_share, mx, _eff(mx))
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
            nxt = upcoming[0] if upcoming else None
            next_minutes = _eff(nxt) if nxt else base_mins
            projs.append(PlayerProj(
                pid=p["id"], name=m["name"], nation=nation, group=str(p.get("_group", "")).upper(),
                position=p["position"], price=m["price"], ownership=m["own"], minutes_prob=m["mins"],
                exp_next=exp_next, exp_avg=exp_avg, horizon=horizon, per_match=per_match,
                next_date=(nxt["date"] or "") if nxt else "",
                tags=_tags(p["position"], m), why=_why(p["position"], m, adv),
                round_points=live_rounds, total_points=float(st.get("totalPoints") or 0.0),
                next_minutes=next_minutes,
                next_num=int(nxt["num"]) if nxt else 0, next_is_home=bool(nxt["is_home"]) if nxt else True,
                goal_share=float(goal_share), assist_share=float(assist_share)))
    return projs


def _player_match_ep(pos: str, m: dict, goal_share: float, assist_share: float, mx: dict,
                     eff_mins: float | None = None) -> float:
    # eff_mins overrides the player's baseline start prob for THIS fixture (per-match
    # minutes — e.g. a nailed starter rested in a clinched team's dead rubber). When
    # None, behaviour is identical to the old single-scalar model.
    mins = m["mins"] if eff_mins is None else eff_mins
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
    if m.get("has_wc"):
        t.append("WC form")
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
