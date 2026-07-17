"""chips_used round-tagging (2026-07-10 papercut): a chip played THIS round must be
detectable as live (MaxCap ⇒ no captaincy relay), while past-round chips stay spent."""
from src.pipeline import ALL_CHIPS, _chips_used_map


def test_legacy_plain_strings_still_parse():
    state = {"chips_used": ["Qualification Booster", "12th Man"]}
    m = _chips_used_map(state)
    assert m == {"Qualification Booster": None, "12th Man": None}


def test_tagged_dicts_carry_round():
    state = {"chips_used": [
        {"name": "Qualification Booster", "round": "R32"},
        {"name": "Maximum Captain", "round": "QF"},
    ]}
    m = _chips_used_map(state)
    assert m["Maximum Captain"] == "QF"
    assert m["Qualification Booster"] == "R32"


def test_mixed_forms_and_remaining():
    state = {"chips_used": ["Qualification Booster", {"name": "Maximum Captain", "round": "QF"}]}
    m = _chips_used_map(state)
    remaining = [c for c in ALL_CHIPS if c not in m]
    assert "Maximum Captain" not in remaining and "Qualification Booster" not in remaining
    assert set(remaining) == {"Wildcard", "12th Man", "Mystery Booster"}


def test_live_chip_detection_semantics():
    """The pipeline marks a used chip live iff its tagged round is the scoring stage or
    the round locking next — replicate the expression used in _run_fantasy."""
    used = {"Maximum Captain": "QF", "Qualification Booster": "R32"}
    # during the live QF (stage=QF, target=SF): MaxCap live, QB not
    live = {c for c, r in used.items() if r in ("QF", "SF")}
    assert live == {"Maximum Captain"}
    # after the QF, planning the SF (stage=SF, target=SF): nothing live
    live = {c for c, r in used.items() if r in ("SF", "SF")}
    assert live == set()


def test_empty_and_malformed_entries_ignored():
    state = {"chips_used": [None, {}, {"round": "QF"}, "12th Man"]}
    assert _chips_used_map(state) == {"12th Man": None}


def test_wildcard_final_round_is_play():
    """The Final is the Wildcard's last usable round — chip_advice must say PLAY
    (an unused chip expires with the tournament) even with zero planned moves."""
    from src.fantasy.transfers import chip_advice
    out = chip_advice("final", "final", ["Wildcard"], squad=None, transfer_plan=None)
    wc = [c for c in out if c["chip"] == "Wildcard"][0]
    assert wc["action"] == "PLAY"


def test_wildcard_final_not_forced_earlier():
    """Before the Final the zero-moves default (HOLD) still stands."""
    from src.fantasy.transfers import chip_advice
    out = chip_advice("QF", "QF", ["Wildcard"], squad=None, transfer_plan=None)
    wc = [c for c in out if c["chip"] == "Wildcard"][0]
    assert wc["action"] == "HOLD"
