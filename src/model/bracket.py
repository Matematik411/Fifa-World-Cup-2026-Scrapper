"""Monte-Carlo simulation of the full 48-team / Round-of-32 bracket.

Models the group stage from per-match Dixon-Coles scoreline matrices, the
best-8-third-placed qualification, the Annex-C third-placed slotting, and the
knockout rounds (90' + ET/penalty tilt) via the advance-probability matrix.

Produces, per team: group-finish distribution, advancement to each round,
title probability, and expected number of remaining matches (the fantasy
longevity weight).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .forecast import Forecast
from .teams import normalize_team

KO_ROUND_ORDER = ["R32", "R16", "QF", "SF", "third_place", "final"]


def _parse_ref(ref: str):
    """Parse a slot reference: '1A','2B','3rd A/B/C','W74','L101'."""
    ref = ref.strip()
    m = re.fullmatch(r"([12])([A-L])", ref)
    if m:
        return ("winner" if m.group(1) == "1" else "runner", m.group(2))
    m = re.fullmatch(r"W(\d+)", ref)
    if m:
        return ("winner_of", int(m.group(1)))
    m = re.fullmatch(r"L(\d+)", ref)
    if m:
        return ("loser_of", int(m.group(1)))
    if ref.lower().startswith("3rd"):
        letters = re.findall(r"[A-L]", ref.split(" ", 1)[1]) if " " in ref else re.findall(r"[A-L]", ref)
        return ("third_slot", tuple(letters))
    return ("unknown", ref)


def _kuhn_matching(slot_allowed: list[set[int]]) -> list[int] | None:
    """Perfect matching of 8 slots to their allowed group-columns (Kuhn's algo).

    Returns group-col assigned to each slot, or None if no perfect matching.
    """
    n = len(slot_allowed)
    groups = sorted(set().union(*slot_allowed)) if slot_allowed else []
    gcol = {g: i for i, g in enumerate(groups)}
    match_group = [-1] * len(groups)  # group-col-local -> slot

    def try_assign(s, seen):
        for g in slot_allowed[s]:
            gi = gcol[g]
            if not seen[gi]:
                seen[gi] = True
                if match_group[gi] == -1 or try_assign(match_group[gi], seen):
                    match_group[gi] = s
                    return True
        return False

    for s in range(n):
        if not try_assign(s, [False] * len(groups)):
            return None
    out = [-1] * n
    for gi, s in enumerate(match_group):
        if s != -1:
            out[s] = groups[gi]
    return out


def forced_ko_winners(played: dict, ko_advancers: dict, fixtures: dict,
                      team_idx: dict[str, int]) -> dict[int, int]:
    """KO matches whose advancing team is already known: match_num -> team idx.

    An explicit ko_advancers entry (needed when the 90' result was a draw and
    ET/pens decided it) wins; otherwise a decisive 90' result combined with the
    real team names filled into fixtures["matches"] determines the winner.
    """
    forced: dict[int, int] = {}
    ko_names = {m["num"]: (normalize_team(m.get("home", "")), normalize_team(m.get("away", "")))
                for m in fixtures["matches"] if m["round"] != "group"}
    for num, name in (ko_advancers or {}).items():
        t = normalize_team(str(name))
        if t in team_idx:
            forced[int(num)] = team_idx[t]
    for num, res in (played or {}).items():
        num = int(num)
        if num in forced or num not in ko_names:
            continue
        gh, ga = res
        if gh == ga:
            continue  # 90' draw — ET/pens decided; needs an explicit ko_advancers entry
        h, a = ko_names[num]
        winner = h if gh > ga else a
        if winner in team_idx:
            forced[num] = team_idx[winner]
    return forced


@dataclass
class BracketSpec:
    r32: dict[int, tuple]            # match_num -> (home_ref, away_ref)
    third_slot_nums: list[int]       # sorted match_nums of the 8 third-placed slots
    third_allowed: dict[int, list[int]]  # third-slot match_num -> allowed group cols
    progression: dict[str, dict[int, list[str]]]


def parse_bracket(fixtures: dict, group_letters: list[str]) -> BracketSpec:
    gcol = {g: i for i, g in enumerate(group_letters)}
    b = fixtures["bracket"]
    r32 = {}
    third_allowed = {}
    third_slot_nums = []
    for num_s, slot in b["r32_slots"].items():
        num = int(num_s)
        href, aref = str(slot["home"]), str(slot["away"])
        r32[num] = (href, aref)
        for ref in (href, aref):
            kind, payload = _parse_ref(ref)
            if kind == "third_slot":
                third_slot_nums.append(num)
                third_allowed[num] = [gcol[g] for g in payload if g in gcol]
    third_slot_nums = sorted(set(third_slot_nums))
    progression = {k: {int(mk): v for mk, v in d.items()} for k, d in b["progression"].items()}
    return BracketSpec(r32=r32, third_slot_nums=third_slot_nums, third_allowed=third_allowed, progression=progression)


class BracketSimulator:
    def __init__(self, forecast: Forecast, fixtures: dict, cfg, played: dict | None = None,
                 ko_advancers: dict | None = None):
        self.fc = forecast
        self.cfg = cfg
        self.teams = forecast.teams
        self.idx = forecast.team_idx
        self.n_teams = len(self.teams)
        self.group_letters = list(fixtures["groups"].keys())
        self.groups = {g: [self.idx[normalize_team(t)] for t in teams]
                       for g, teams in fixtures["groups"].items()}
        self.spec = parse_bracket(fixtures, self.group_letters)
        self.W = forecast.max_goals + 1
        self.played = played or {}  # match_num -> (gh, ga) actual results already in
        # KO matches with a known advancing team -> condition the sim on them
        self.forced = forced_ko_winners(self.played, ko_advancers or {}, fixtures, self.idx)
        self._prep_group_matches(fixtures)

    def _prep_group_matches(self, fixtures: dict):
        """For each group, the 6 matches as (home_local, away_local, cum_dist or fixed result)."""
        self.group_matches = {g: [] for g in self.group_letters}
        local = {g: {t: i for i, t in enumerate(self.groups[g])} for g in self.group_letters}
        for m in fixtures["matches"]:
            if m["round"] != "group":
                continue
            g = m["group"]
            h, a = normalize_team(m["home"]), normalize_team(m["away"])
            if h not in self.idx or a not in self.idx:
                continue
            hl, al = local[g][self.idx[h]], local[g][self.idx[a]]
            actual = self.played.get(m["num"])
            if actual is not None:
                self.group_matches[g].append((hl, al, None, tuple(actual)))
            else:
                P = self.fc.match_forecasts[m["num"]].P
                cum = np.cumsum(P.ravel())
                cum[-1] = 1.0
                self.group_matches[g].append((hl, al, cum, None))

    def run(self, n_sims: int, seed: int) -> dict:
        rng = np.random.default_rng(seed)
        N = n_sims
        T = self.n_teams
        GL = self.group_letters

        # accumulators
        win_group = np.zeros(T)
        runner_up = np.zeros(T)
        third_all = np.zeros(T)
        third_qual = np.zeros(T)
        finish_pos = {g: np.zeros((4, 4)) for g in GL}  # [local_team, position]
        reach = {r: np.zeros(T) for r in ["R32", "R16", "QF", "SF", "final"]}
        champion = np.zeros(T)
        ko_played = np.zeros(T)

        # per-sim group outputs
        winner_idx = {}     # g -> [N] team idx
        runner_idx = {}
        third_idx = {}      # g -> [N] team idx (3rd place)
        third_key = np.zeros((N, len(GL)))   # comparable key of each group's 3rd

        for gi, g in enumerate(GL):
            teams_local = np.array(self.groups[g])           # 4 team idx
            pts = np.zeros((N, 4)); gd = np.zeros((N, 4)); gf = np.zeros((N, 4))
            for (hl, al, cum, actual) in self.group_matches[g]:
                if actual is not None:
                    gh = np.full(N, actual[0]); ga = np.full(N, actual[1])
                else:
                    flat = np.searchsorted(cum, rng.random(N))
                    gh = flat // self.W; ga = flat % self.W
                hw = gh > ga; aw = ga > gh; dr = gh == ga
                pts[:, hl] += 3 * hw + dr; pts[:, al] += 3 * aw + dr
                gd[:, hl] += gh - ga; gd[:, al] += ga - gh
                gf[:, hl] += gh; gf[:, al] += ga
            noise = rng.random((N, 4)) * 1e-3
            key = pts * 1e6 + (gd + 100) * 1e3 + gf * 1.0 + noise
            order = np.argsort(-key, axis=1)        # [N,4] local positions sorted best->worst
            rows = np.arange(N)
            first_local = order[:, 0]; second_local = order[:, 1]; third_local = order[:, 2]
            winner_idx[g] = teams_local[first_local]
            runner_idx[g] = teams_local[second_local]
            third_idx[g] = teams_local[third_local]
            # comparable key for the 3rd-placed team (points, gd, gf only — no noise across groups bias)
            tp = pts[rows, third_local]; tgd = gd[rows, third_local]; tgf = gf[rows, third_local]
            third_key[:, gi] = tp * 1e6 + (tgd + 100) * 1e3 + tgf * 1.0 + rng.random(N) * 1e-3
            # tally finish positions
            for pos in range(4):
                np.add.at(finish_pos[g][:, pos], order[:, pos], 1)
            np.add.at(win_group, winner_idx[g], 1)
            np.add.at(runner_up, runner_idx[g], 1)
            np.add.at(third_all, third_idx[g], 1)

        # --- best 8 thirds ---
        third_order = np.argsort(-third_key, axis=1)         # [N,12] group cols best->worst
        qual_cols = third_order[:, :8]                       # [N,8] qualifying group cols

        # bitmask per sim of qualifying group cols
        masks = np.bitwise_or.reduce((1 << qual_cols).astype(np.int64), axis=1)
        uniq, inv = np.unique(masks, return_inverse=True)
        slot_allowed = [set(self.spec.third_allowed[num]) for num in self.spec.third_slot_nums]
        assign_table = np.full((len(uniq), len(self.spec.third_slot_nums)), -1, dtype=np.int64)
        n_fallback = 0
        for ui, mask in enumerate(uniq):
            qcols = [c for c in range(len(GL)) if mask & (1 << c)]
            qset = set(qcols)
            allowed_local = [a & qset for a in slot_allowed]
            assign = _kuhn_matching(allowed_local)
            if assign is None or any(x == -1 for x in assign):
                n_fallback += 1
                # fallback: assign remaining groups to slots arbitrarily (rare)
                used = set(x for x in (assign or []) if x != -1)
                remaining = [c for c in qcols if c not in used]
                assign = list(assign) if assign else [-1] * len(slot_allowed)
                ri = 0
                for s in range(len(assign)):
                    if assign[s] == -1:
                        assign[s] = remaining[ri]; ri += 1
            assign_table[ui] = assign
        slot_assign = assign_table[inv]    # [N, 8] group col assigned to each third-slot

        # third team idx per group, as [N,12] matrix for fancy indexing
        third_mat = np.stack([third_idx[g] for g in GL], axis=1)   # [N,12]
        rows = np.arange(N)
        # qualifying thirds tally (reach R32) and third_qual
        for k in range(8):
            cols = qual_cols[:, k]
            t = third_mat[rows, cols]
            np.add.at(third_qual, t, 1)

        # map third-slot index -> team idx per sim
        third_slot_team = {}
        for s_i, num in enumerate(self.spec.third_slot_nums):
            cols = slot_assign[:, s_i]
            third_slot_team[num] = third_mat[rows, cols]

        # --- resolve a slot reference to team idx array ---
        def resolve(ref, winners, third_slot_team):
            kind, payload = _parse_ref(ref)
            if kind == "winner":
                return winner_idx[payload]
            if kind == "runner":
                return runner_idx[payload]
            if kind == "third_slot":
                # find which third-slot match this ref belongs to — handled by caller via match num
                raise ValueError("third_slot resolved by match num")
            if kind == "winner_of":
                return winners[payload]
            if kind == "loser_of":
                return self._losers[payload]
            raise ValueError(f"Cannot resolve ref {ref}")

        winners: dict[int, np.ndarray] = {}
        self._losers = {}

        def play(num, a_idx, b_idx):
            p = self.fc.advance_prob[a_idx, b_idx]
            a_wins = rng.random(N) < p
            w = np.where(a_wins, a_idx, b_idx)
            forced = self.forced.get(num)
            if forced is not None:
                # condition on the real advancing team in every sim where it's in this tie
                w = np.where((a_idx == forced) | (b_idx == forced), forced, w)
            l = np.where(w == a_idx, b_idx, a_idx)
            winners[num] = w
            self._losers[num] = l
            np.add.at(ko_played, a_idx, 1); np.add.at(ko_played, b_idx, 1)
            return w

        # R32
        for num in sorted(self.spec.r32):
            href, aref = self.spec.r32[num]
            if _parse_ref(href)[0] == "third_slot":
                a_idx = third_slot_team[num]
            else:
                a_idx = resolve(href, winners, third_slot_team)
            if _parse_ref(aref)[0] == "third_slot":
                b_idx = third_slot_team[num]
            else:
                b_idx = resolve(aref, winners, third_slot_team)
            np.add.at(reach["R32"], a_idx, 1); np.add.at(reach["R32"], b_idx, 1)
            play(num, a_idx, b_idx)

        # subsequent rounds via progression
        round_reach_key = {"R16": "R16", "QF": "QF", "SF": "SF", "final": "final"}
        for rnd in ["R16", "QF", "SF", "third_place", "final"]:
            for num, refs in sorted(self.spec.progression.get(rnd, {}).items()):
                a_idx = resolve(refs[0], winners, third_slot_team)
                b_idx = resolve(refs[1], winners, third_slot_team)
                if rnd in round_reach_key:
                    np.add.at(reach[round_reach_key[rnd]], a_idx, 1)
                    np.add.at(reach[round_reach_key[rnd]], b_idx, 1)
                w = play(num, a_idx, b_idx)
                if rnd == "final":
                    np.add.at(champion, w, 1)

        # remaining group games per team (for expected-total-matches)
        remaining_group = np.zeros(T)
        for g in GL:
            for (hl, al, cum, actual) in self.group_matches[g]:
                if actual is None:
                    remaining_group[self.groups[g][hl]] += 1
                    remaining_group[self.groups[g][al]] += 1

        adv = {}
        for i, t in enumerate(self.teams):
            exp_ko = ko_played[i] / N
            adv[t] = {
                "win_group": win_group[i] / N,
                "runner_up": runner_up[i] / N,
                "top2": (win_group[i] + runner_up[i]) / N,
                "third_place": third_all[i] / N,
                "third_qualify": third_qual[i] / N,
                "reach_R32": reach["R32"][i] / N,
                "reach_R16": reach["R16"][i] / N,
                "reach_QF": reach["QF"][i] / N,
                "reach_SF": reach["SF"][i] / N,
                "reach_final": reach["final"][i] / N,
                "champion": champion[i] / N,
                "exp_ko_matches": exp_ko,
                "exp_remaining_matches": remaining_group[i] + exp_ko,
            }

        group_standings = {}
        for g in GL:
            group_standings[g] = {}
            for li, tidx in enumerate(self.groups[g]):
                row = finish_pos[g][li] / N
                group_standings[g][self.teams[tidx]] = {
                    "p1": row[0], "p2": row[1], "p3": row[2], "p4": row[3],
                }

        return {
            "n_sims": N,
            "advancement": adv,
            "group_standings": group_standings,
            "third_slot_fallbacks": int(n_fallback),
        }
