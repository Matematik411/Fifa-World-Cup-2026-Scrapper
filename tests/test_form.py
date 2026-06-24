"""U5 — in-tournament player form (WC xG) shrinkage in src/fantasy/projections."""
from src.fantasy.projections import _rates, build_projections
from src.model.dixon_coles import score_matrix, summarize
from src.model.forecast import MatchForecast

_P = {"position": "FWD", "price": 8.0}
_STAT = {"xg_p90": 0.4, "goals_p90": 0.4}   # club-season baseline ~0.40 g/90


def test_no_wc_is_unchanged():
    assert _rates(_P, {}, _STAT)[0] == _rates(_P, {}, _STAT, None)[0]


def test_wc_form_pulls_rate_up_but_shrinks_it():
    base = _rates(_P, {}, _STAT)[0]
    wc = {"wc_minutes": 180, "wc_xg": 2.7, "wc_goals": 3, "wc_xa": 0.2, "wc_assists": 0}
    up = _rates(_P, {}, _STAT, wc, k_form=600)[0]
    raw_wc_rate = (0.7 * 2.7 + 0.3 * 3) * 90 / 180        # = 1.395 g/90 in the tournament
    assert base < up < raw_wc_rate                         # moved toward form, but heavily shrunk
    assert abs(up - 0.63) < 0.05                            # ~0.40 prior + ~23% of the WC signal


def test_leans_on_xg_not_finishing_heat():
    # identical xG, wildly different goals: the rate must barely diverge (xG weighted 0.7)
    cold = _rates(_P, {}, _STAT, {"wc_minutes": 180, "wc_xg": 1.0, "wc_goals": 1}, 600)[0]
    hot = _rates(_P, {}, _STAT, {"wc_minutes": 180, "wc_xg": 1.0, "wc_goals": 5}, 600)[0]
    assert hot > cold                                      # finishing nudges it a little
    assert (hot - cold) < 0.2                              # ...but heat is damped, not chased


def test_two_games_barely_move_the_prior():
    base = _rates(_P, {}, {"xg_p90": 0.5, "goals_p90": 0.5})[0]
    wc = {"wc_minutes": 90, "wc_xg": 0.5, "wc_goals": 0}   # one game, on-xG, no goals
    out = _rates(_P, {}, {"xg_p90": 0.5, "goals_p90": 0.5}, wc, 600)[0]
    assert abs(out - base) < 0.1


def _two_fwd_team():
    """One Mexico match, two forwards with DISTINCT surnames (the name-matcher keys on
    surname, so collisions would tag both). Form must redistribute goal share within
    the team — that's the whole mechanism, so a 2-player team is the minimal test."""
    P = score_matrix(2.6, 0.5)
    s = summarize(P)

    class _FC:
        match_forecasts = {1: MatchForecast(num=1, round="group", group=None, home="Mexico",
                                            away="Foe", lam_home=2.6, lam_away=0.5, p_home=s.p_home,
                                            p_draw=s.p_draw, p_away=s.p_away, exp_home=s.exp_home,
                                            exp_away=s.exp_away, source="t", P=P, date="2026-06-20")}
    players = [{"id": 1, "squadId": 1, "position": "FWD", "price": 8.0, "percentSelected": 20.0,
                "status": "playing", "firstName": "Alfa", "lastName": "Alvarez"},
               {"id": 2, "squadId": 1, "position": "FWD", "price": 8.0, "percentSelected": 20.0,
                "status": "playing", "firstName": "Bruno", "lastName": "Bianchi"}]
    squads_map = {1: {"nation": "Mexico", "group": "A", "eliminated": False}}
    adv = {"Mexico": {"exp_remaining_matches": 1.0}}
    return _FC(), players, squads_map, adv


def test_wc_form_tag_and_redistributes_goal_share():
    fc, players, squads_map, adv = _two_fwd_team()
    wcf = {"players": {"Mexico": [{"name": "Alvarez", "wc_minutes": 180, "wc_xg": 2.6,
                                   "wc_goals": 3, "wc_xa": 0.2, "wc_assists": 0}]}}
    base = {p.pid: p for p in build_projections(players, squads_map, fc, adv, {}, {"matches": []},
                                                None, played=set())}
    formed = {p.pid: p for p in build_projections(players, squads_map, fc, adv, {}, {"matches": []},
                                                  None, played=set(), wc_form=wcf)}
    assert "WC form" in formed[1].tags and "WC form" not in formed[2].tags   # only Alvarez
    assert formed[1].exp_next > base[1].exp_next                              # hot striker lifts
    assert formed[2].exp_next < base[2].exp_next                             # teammate's share falls
