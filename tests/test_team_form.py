"""U2 — opponent-adjusted team-form strength offset (src/model/ensemble.apply_team_form)."""
from src.model.ensemble import Strengths, apply_team_form


class _Cfg:
    def __init__(self, **over):
        self.d = {"model.team_form_weight": 0.15, "model.base_total_goals": 2.65,
                  "model.team_form_shrink_matches": 3, "model.team_form_opp_coef": 0.5,
                  "model.team_form_scale": 1.5}
        self.d.update(over)

    def get(self, k, default=None):
        return self.d.get(k, default)


def _strengths():
    return Strengths(teams=["Hotland", "Coldland", "Strongfoe", "Weakfoe"],
                     s={"Hotland": 0.0, "Coldland": 0.0, "Strongfoe": 1.0, "Weakfoe": -1.0},
                     beta=0.5, mu_base=0.26, host_log=0.0, altitude_log=0.0)


_WC = {"teams": {
    "Hotland": {"wc_xg_for": 5.0, "wc_xg_against": 1.0, "matches": 2, "opponents": ["Strongfoe"]},
    "Coldland": {"wc_xg_for": 1.0, "wc_xg_against": 5.0, "matches": 2, "opponents": ["Weakfoe"]},
}}


def test_in_form_team_nudged_up_out_of_form_down():
    st = _strengths()
    apply_team_form(st, _WC, _Cfg())
    assert st.s["Hotland"] > 0.0          # dominant xG vs a strong foe -> positive
    assert st.s["Coldland"] < 0.0         # dominated -> negative
    assert st.s["Strongfoe"] == 1.0       # teams with no WC-form row are untouched


def test_offset_is_small_and_bounded_by_weight():
    st = _strengths()
    apply_team_form(st, _WC, _Cfg())
    assert abs(st.s["Hotland"]) <= 0.15 + 1e-9   # capped by weight (×shrink×clip ≤ weight)


def test_weight_zero_disables():
    st = _strengths()
    before = dict(st.s)
    apply_team_form(st, _WC, _Cfg(**{"model.team_form_weight": 0.0}))
    assert st.s == before


def test_schedule_credit_rewards_tougher_opponents():
    # identical xG, but one faced a strong opponent and one a weak opponent
    wc = {"teams": {
        "Hotland": {"wc_xg_for": 4.0, "wc_xg_against": 2.0, "matches": 2, "opponents": ["Strongfoe"]},
        "Coldland": {"wc_xg_for": 4.0, "wc_xg_against": 2.0, "matches": 2, "opponents": ["Weakfoe"]},
    }}
    st = _strengths()
    apply_team_form(st, wc, _Cfg())
    assert st.s["Hotland"] > st.s["Coldland"]    # same output, tougher schedule -> bigger nudge
