"""Per-match forecasts and the knockout advance-probability matrix.

Bridges ensemble strengths + fixtures + market odds into:
  * `match_forecasts`: a Dixon-Coles scoreline matrix for every match whose
    teams are known (all group games now; KO games once the bracket fills),
    using market-derived lambdas where odds exist and rating-derived otherwise.
  * `advance_prob[i, j]`: P(team i eliminates team j) in a neutral knockout,
    for all 48x48 pairs (90-min result + extra-time/penalty tilt by strength).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, tanh

import numpy as np
from scipy.stats import skellam

from . import dixon_coles as dc
from .ensemble import Strengths, devig_multiplicative
from .teams import normalize_team


@dataclass
class MatchForecast:
    num: int
    round: str
    group: str | None
    home: str
    away: str
    lam_home: float
    lam_away: float
    p_home: float
    p_draw: float
    p_away: float
    exp_home: float
    exp_away: float
    source: str
    P: np.ndarray = field(repr=False, default=None)
    venue: str = ""
    city: str = ""
    date: str = ""
    kickoff_local: str = ""
    tz: str = ""

    def to_record(self) -> dict:
        return {
            "num": self.num, "round": self.round, "group": self.group,
            "home": self.home, "away": self.away,
            "lam_home": round(self.lam_home, 3), "lam_away": round(self.lam_away, 3),
            "p_home": round(self.p_home, 4), "p_draw": round(self.p_draw, 4), "p_away": round(self.p_away, 4),
            "exp_home": round(self.exp_home, 3), "exp_away": round(self.exp_away, 3),
            "source": self.source, "venue": self.venue, "city": self.city,
            "date": self.date, "kickoff_local": self.kickoff_local, "tz": self.tz,
        }


HOSTS = {"USA", "Mexico", "Canada"}


class Forecast:
    def __init__(self, teams: list[str], strengths: Strengths, cfg, ratings_odds: dict, fixtures: dict,
                 ignore_market: bool = False, stakes: dict | None = None):
        self.teams = teams
        self.team_idx = {t: i for i, t in enumerate(teams)}
        self.st = strengths
        self.cfg = cfg
        self.rho = float(cfg.get("model.dixon_coles_rho", -0.12))
        self.max_goals = int(cfg.get("model.goal_cap", 8))
        self.k_et = 0.35
        # When True, skip per-match market odds and force rating-derived lambdas — the
        # "ratings-only" mode the backtest (src/eval) uses to isolate the strength model.
        self.ignore_market = ignore_market
        # Dead-rubber stakes (U3): {num: {both_settled: bool, ...}} from src/model/standings.
        # A light goal-intensity damping is applied to a match only when BOTH teams are
        # settled (clinched/eliminated) — a lower-stakes end-of-group game tends to be
        # slower and lower-scoring. Empty pre-MD3 -> no effect.
        self.stakes = stakes or {}
        self.dead_rubber_intensity = float(cfg.get("model.dead_rubber_intensity", 0.92))
        self.altitude_cities = set(cfg.get("model.altitude_venues", []))
        self._market = self._index_market(ratings_odds.get("match_odds") or [])
        self.match_forecasts: dict[int, MatchForecast] = {}
        self.advance_prob = self._build_advance_prob()
        self._build_match_forecasts(fixtures)

    # ---- strength -> lambdas ----
    def _rating_lambdas(self, home: str, away: str, host_home=False, host_away=False, altitude=False):
        s = self.st.s
        sh, sa = s.get(home, 0.0), s.get(away, 0.0)
        lh = self.st.mu_base + self.st.beta * (sh - sa)
        la = self.st.mu_base + self.st.beta * (sa - sh)
        if host_home:
            lh += self.st.host_log
        if host_away:
            la += self.st.host_log
        if altitude:
            lh += self.st.altitude_log
            la += 0.5 * self.st.altitude_log
        return float(np.clip(exp(lh), 0.12, 5.0)), float(np.clip(exp(la), 0.12, 5.0))

    def _index_market(self, rows: list[dict]) -> dict:
        idx = {}
        for mo in rows:
            try:
                h, a = normalize_team(mo["home"]), normalize_team(mo["away"])
            except (KeyError, TypeError):
                continue
            idx[(h, a)] = mo
        return idx

    def _market_lambdas(self, home: str, away: str):
        mo = self._market.get((home, away))
        swap = False
        if mo is None and (away, home) in self._market:
            mo = self._market[(away, home)]
            swap = True
        if mo is None:
            return None
        try:
            o = {"H": float(mo["home_dec"]), "D": float(mo["draw_dec"]), "A": float(mo["away_dec"])}
            dv = devig_multiplicative(o)
            total = float(mo["total_line"]) if mo.get("total_line") else None
            lam, mu = dc.solve_lambdas_from_market(dv["H"], dv["D"], dv["A"], total_hint=total, rho=self.rho, max_goals=self.max_goals)
            if swap:
                lam, mu = mu, lam
            return lam, mu
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None

    def match_matrix(self, home: str, away: str, *, neutral: bool, city: str = "") -> tuple[float, float, np.ndarray, str]:
        """Return (lam_home, lam_away, P, source) for a (possibly hypothetical) match."""
        mk = None if self.ignore_market else self._market_lambdas(home, away)
        if mk is not None:
            lam, mu = mk
            source = "market"
        else:
            host_home = (not neutral) and home in HOSTS
            host_away = (not neutral) and away in HOSTS
            altitude = (not neutral) and (city in self.altitude_cities)
            lam, mu = self._rating_lambdas(home, away, host_home, host_away, altitude)
            source = "rating"
        P = dc.score_matrix(lam, mu, self.rho, self.max_goals)
        return lam, mu, P, source

    def _build_match_forecasts(self, fixtures: dict) -> None:
        known = set(self.teams)
        for m in fixtures["matches"]:
            home, away = normalize_team(m.get("home", "")), normalize_team(m.get("away", ""))
            if home not in known or away not in known:
                continue  # KO slot placeholders — resolved in later runs as the bracket fills
            neutral = m["round"] != "group"  # group host teams play at "home"; KO treated neutral
            # Group games: home team is nominal host designation; apply host/altitude only at group stage.
            lam, mu, P, source = self.match_matrix(home, away, neutral=neutral, city=m.get("city", ""))
            if self.stakes.get(m["num"], {}).get("both_settled"):
                f = self.dead_rubber_intensity
                lam, mu = lam * f, mu * f
                P = dc.score_matrix(lam, mu, self.rho, self.max_goals)
                source += "+deadrubber"
            summ = dc.summarize(P)
            self.match_forecasts[m["num"]] = MatchForecast(
                num=m["num"], round=m["round"], group=m.get("group"), home=home, away=away,
                lam_home=lam, lam_away=mu, p_home=summ.p_home, p_draw=summ.p_draw, p_away=summ.p_away,
                exp_home=summ.exp_home, exp_away=summ.exp_away, source=source, P=P,
                venue=m.get("venue", ""), city=m.get("city", ""), date=m.get("date", ""),
                kickoff_local=m.get("kickoff_local", ""), tz=m.get("tz", ""),
            )

    def _build_advance_prob(self) -> np.ndarray:
        """48x48 P(i eliminates j) at a neutral venue (Skellam 90' + ET/pens tilt)."""
        n = len(self.teams)
        s = np.array([self.st.s.get(t, 0.0) for t in self.teams])
        S = s[:, None] - s[None, :]                       # supremacy matrix
        L = np.exp(self.st.mu_base + self.st.beta * S)     # lam_i (i as nominal home, neutral)
        M = np.exp(self.st.mu_base - self.st.beta * S)     # lam_j
        L = np.clip(L, 0.12, 5.0)
        M = np.clip(M, 0.12, 5.0)
        p_draw = skellam.pmf(0, L, M)
        p_i_win = skellam.sf(0, L, M)                      # P(diff > 0)
        et_tilt = 0.5 + 0.5 * np.tanh(self.k_et * S)
        adv = p_i_win + p_draw * et_tilt
        np.fill_diagonal(adv, 0.5)
        return adv

    # ---- persistence ----
    def to_records(self) -> list[dict]:
        return [self.match_forecasts[k].to_record() for k in sorted(self.match_forecasts)]
