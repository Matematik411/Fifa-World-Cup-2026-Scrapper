"""Played-match awareness and KO-tie inclusion in the fantasy projections."""
from src.fantasy.projections import build_projections
from src.model.dixon_coles import score_matrix, summarize
from src.model.forecast import MatchForecast


def _mf(num, rnd, date, home, away, lam_h, lam_a):
    P = score_matrix(lam_h, lam_a)
    s = summarize(P)
    return MatchForecast(num=num, round=rnd, group=None, home=home, away=away,
                         lam_home=lam_h, lam_away=lam_a, p_home=s.p_home, p_draw=s.p_draw,
                         p_away=s.p_away, exp_home=s.exp_home, exp_away=s.exp_away,
                         source="test", P=P, date=date)


class _FC:
    pass


def _setup(eliminated=False):
    fc = _FC()
    fc.match_forecasts = {
        # juicy opener, tougher second game, KO tie already known
        1: _mf(1, "group", "2026-06-11", "Mexico", "South Africa", 2.6, 0.6),
        2: _mf(2, "group", "2026-06-18", "Mexico", "South Korea", 0.8, 1.6),
        73: _mf(73, "R32", "2026-06-28", "Mexico", "Spain", 1.0, 1.4),
    }
    players = [{"id": 10, "squadId": 1, "position": "FWD", "price": 8.0,
                "percentSelected": 20.0, "status": "playing",
                "firstName": "Raul", "lastName": "Jimenez"}]
    squads_map = {1: {"nation": "Mexico", "group": "A", "eliminated": eliminated}}
    adv = {"Mexico": {"exp_remaining_matches": 2.4, "exp_ko_matches": 1.4, "reach_R16": 0.5}}
    return fc, players, squads_map, adv


def _proj(played, eliminated=False):
    fc, players, squads_map, adv = _setup(eliminated)
    return build_projections(players, squads_map, fc, adv, {}, {"matches": []}, None,
                             played=played)[0]


def test_known_ko_tie_included():
    p = _proj(played=set())
    assert set(p.per_match) == {1, 2, 73}


def test_played_match_excluded_and_exp_next_advances():
    full = _proj(played=set())
    after = _proj(played={1})
    assert set(after.per_match) == {2, 73}
    # next_date tracks the next unplayed match (drives the captaincy relay)
    assert full.next_date == "2026-06-11" and after.next_date == "2026-06-18"
    # next-match EP must move from the (easy) opener to the (hard) second game
    assert full.exp_next > after.exp_next
    assert abs(after.exp_next - after.per_match[2]) < 1e-9
    # horizon must shrink once a match is consumed
    assert after.horizon < full.horizon


def test_horizon_covers_residual_beyond_known_fixtures():
    p = _proj(played={1})
    # exp_remaining 2.4 vs 2 known fixtures -> small positive residual priced at exp_avg
    assert p.horizon > sum(p.per_match.values())


def test_eliminated_team_zeroed():
    p = _proj(played={1}, eliminated=True)
    assert p.horizon == 0.0 and p.exp_next == 0.0 and p.per_match == {}
