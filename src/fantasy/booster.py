"""U4 — joint chip scheduling + the Qualification-Booster value that feeds squad selection.

Two jobs the old per-chip `chip_advice` (src/fantasy/transfers.py) couldn't do:

  1. **QB-aware squad selection.** `qb_advance_bonus` turns advancement probabilities into
     the Qualification Booster's per-starter payoff (+2 * P(advance this round)). Fed into
     `optimizer.select_squad_xi`, it makes the R32 build favour favourites who will actually
     bank the +2 — instead of selecting purely on points and only timing the chip afterwards.

  2. **A forward chip schedule** across the remaining knockout rounds under the
     one-chip-per-round constraint. It encodes the agreed plan (QB@R32, Wildcard@R16, then
     Max Captain / 12th Man / Mystery across QF→Final) but lets the numeric QB-EV-by-round
     and a revealed Mystery effect override the R32 slot, and resolves the contention the old
     code ignored (e.g. Wildcard and Max Captain both wanting R16).

Pure best-EV only: nothing here makes a rank-aware / variance play. The only distributional
input is the captain ceiling (U7), used solely to time the Maximum Captain chip.
"""
from __future__ import annotations

KO_ORDER = ["R32", "R16", "QF", "SF", "final"]

# (reached-this-round key, reached-next-round key) for P(advance | playing this round)
REACH_KEY = {"R32": ("reach_R32", "reach_R16"), "R16": ("reach_R16", "reach_QF"),
             "QF": ("reach_QF", "reach_SF"), "SF": ("reach_SF", "reach_final"),
             "final": ("reach_final", "champion")}


def qb_advance_bonus(advancement: dict, target_round: str, nations) -> dict[str, float]:
    """{nation: 2 * P(advance this round | reached it)} — the Qualification Booster's
    per-advancing-starter payoff, for the nations supplied (a squad's starters)."""
    cur_key, nxt_key = REACH_KEY.get(target_round, (None, None))
    if cur_key is None:
        return {}
    out: dict[str, float] = {}
    for nat in set(nations):
        adv = advancement.get(nat, {})
        cur, nxt = float(adv.get(cur_key, 0.0)), float(adv.get(nxt_key, 0.0))
        out[nat] = 2.0 * min(1.0, nxt / cur) if cur > 1e-9 else 0.0
    return out


def squad_qb_ev(squad, advancement: dict, target_round: str) -> float:
    """Expected Qualification-Booster points for `squad`'s current XI at `target_round`."""
    if not squad or target_round not in REACH_KEY:
        return 0.0
    by_pid = squad.by_pid()
    bonus = qb_advance_bonus(advancement, target_round,
                             [by_pid[pid].nation for pid in squad.starters])
    return round(sum(bonus.get(by_pid[pid].nation, 0.0) for pid in squad.starters), 1)


def qb_ev_by_round(squad, advancement: dict, from_round: str) -> dict[str, float]:
    """QB EV if played in each remaining round, using the current XI as the proxy squad.
    QB is normally maximised at the earliest round (most starters still alive)."""
    if from_round not in KO_ORDER:
        return {}
    return {r: squad_qb_ev(squad, advancement, r)
            for r in KO_ORDER[KO_ORDER.index(from_round):] if r != "final"}


# round -> (reach-this-round key, reach-PREVIOUS-round key); prev None means R32 (=1.0, all in)
_ROUND_REACH = {"R16": ("reach_R16", None), "QF": ("reach_QF", "reach_R16"),
                "SF": ("reach_SF", "reach_QF"), "final": ("reach_final", "reach_SF")}


def wc_breakage_by_round(squad, advancement: dict, ft_by_round: dict, rounds) -> dict[str, float]:
    """Wildcard value proxy per round = E[squad players forced out ENTERING the round]
    (their team eliminated the round before) − that round's free transfers.

    The Wildcard's only edge over free transfers is making MORE than the free allowance of
    changes in ONE round, so it's worth most where this is largest. A favourites-heavy R32
    squad SURVIVES the R32 (few eliminations entering R16, well under R16's free transfers),
    so the peak is deeper (QF/SF) as the field collapses — NOT a fixed R16. Returns
    {round: elim − free_transfers}; the Wildcard goes to the argmax.
    """
    if not squad or not advancement:
        return {}
    out: dict[str, float] = {}
    for r in rounds:
        if r not in _ROUND_REACH:
            continue
        cur_k, prev_k = _ROUND_REACH[r]
        elim = 0.0
        for p in squad.players:
            a = advancement.get(p.nation, {})
            prev = 1.0 if prev_k is None else float(a.get(prev_k, 0.0))
            elim += max(0.0, prev - float(a.get(cur_k, 0.0)))
        out[r] = round(elim - float((ft_by_round or {}).get(r, 4)), 2)
    return out


def chip_schedule(target_round: str, chips_remaining, qb_by_round: dict[str, float], *,
                  squad=None, advancement: dict | None = None, ft_by_round: dict | None = None,
                  mystery: dict | None = None, max_cap_ev: float | None = None,
                  twelfth: dict | None = None) -> dict:
    """Forward chip plan across the remaining KO rounds (one chip per round).

    Returns {"this_round": {...} | None, "schedule": [{round, chip, ev, status, reason}],
             "notes": [...]}. `this_round` is the chip (if any) to play at `target_round`.

    Statuses: PLAY (do it this round) · PLAN (scheduled for a later round) · WAIT (effect
    not yet known — Mystery before R32) · HOLD (kept in reserve).
    """
    chips = set(chips_remaining or [])
    rounds = KO_ORDER[KO_ORDER.index(target_round):] if target_round in KO_ORDER else []
    assigned: dict[str, dict] = {}
    notes: list[str] = []
    used: set[str] = set()

    def place(rnd, chip, ev, reason, status="PLAN", display=None):
        # membership/dedup keys on the canonical chip name; `display` overrides the shown
        # label (e.g. the Mystery Booster shown as its revealed name "Clean Sheet Shield").
        if rnd not in rounds or rnd in assigned or chip in used or chip not in chips:
            return False
        assigned[rnd] = {"round": rnd, "chip": display or chip, "ev": ev, "reason": reason, "status": status}
        used.add(chip)
        return True

    # With 5 chips and 5 KO rounds left it's a one-chip-per-round assignment. Place QB first
    # (anchored at R32), then the Wildcard at the round where the squad breaks HARDEST (not a
    # fixed R16), then fit the rest. The QF/SF/Final order is provisional (re-optimized as the
    # bracket sets). `first_open` picks the earliest still-free round from a preference.
    def first_open(prefs):
        return next((r for r in prefs if r in rounds and r not in assigned), None)

    # --- Qualification Booster -> the remaining round with the highest QB EV (≈ R32) ---
    if "Qualification Booster" in chips and qb_by_round:
        qb_round = max(qb_by_round, key=lambda r: qb_by_round.get(r, 0.0))
        ev = qb_by_round.get(qb_round, 0.0)
        place(qb_round, "Qualification Booster", round(ev, 1),
              f"+2 per starter who advances; worth ≈{ev:.0f} pts here — most of your XI are alive and "
              f"favoured. QB EV falls each round as players are knocked out.",
              status="PLAY" if qb_round == target_round else "PLAN")

    # --- Wildcard -> the round of MAXIMUM squad breakage (forced changes beyond the free
    #     allowance), HELD past R16. A favourites-heavy R32 squad survives the R32, so R16's
    #     free transfers cover the few casualties; the unlimited rebuild is worth most deeper
    #     (QF/SF) as the field collapses. Falls back to QF when no squad data is supplied. ---
    if "Wildcard" in chips:
        brk = wc_breakage_by_round(squad, advancement, ft_by_round,
                                   [r for r in rounds if r != "R32"])
        wc_round = max(brk, key=brk.get) if brk else first_open(["QF", "SF", "final", "R16"])
        if wc_round == "R32":
            wc_round = first_open(["QF", "SF", "final", "R16"])
        if wc_round:
            b = brk.get(wc_round)
            why = (f"Unlimited rebuild where the squad breaks hardest ({wc_round}: ~{b:+.1f} forced "
                   f"changes vs the free allowance). " if b is not None else
                   f"Unlimited rebuild for the deep run ({wc_round}). ")
            place(wc_round, "Wildcard", None,
                  why + "Held past R16 — a favourites-heavy R32 squad survives the R32, so R16's free "
                  "transfers cover it; the Wildcard is saved for the deeper break.")

    # --- 12th Man -> the EARLIEST open deep round (R16 has the most games → the most of your
    #     players active, so an extra scorer adds the most). (Wasted at the Final: only 2 teams
    #     play and your XI already holds both sides' best — user, 2026-06-28.) ---
    if "12th Man" in chips:
        tm_round = first_open(["R16", "QF", "SF", "final"])
        if tm_round:
            extra = f" Best external option now: {twelfth['name']} (E {twelfth['ev']:.1f})." if twelfth else ""
            place(tm_round, "12th Man", None,
                  f"One extra scorer (no budget/nation limits) — best in a round with many games so several "
                  f"of your players are active.{extra}")

    # --- Mystery Booster ---
    if "Mystery Booster" in chips:
        if mystery and mystery.get("known") and mystery.get("clean_sheet"):
            # Clean Sheet Shield: a defensive-stack chip → a deep round where you field several
            # GK/DEF/MID and ties tighten (1-goal games), so the one-goal buffer converts near-misses.
            cs_round = first_open([mystery.get("best_round", "SF"), "SF", "QF", "final", "R16"])
            if cs_round:
                place(cs_round, "Mystery Booster", None,
                      f"{mystery.get('effect', 'One-goal clean-sheet buffer.')} Pairs with a defensive "
                      f"stack — field several GK/DEF/MID from a mean defence in a tight tie.",
                      display=mystery.get("name", "Clean Sheet Shield"))
        elif mystery and mystery.get("known"):
            ev = float(mystery.get("ev", 0.0))
            r = "R32" if ("R32" in rounds and "R32" not in assigned and ev >= float(mystery.get("threshold", 8.0))) \
                else first_open(["QF", "SF", "final", "R16"])
            if r:
                place(r, "Mystery Booster", round(ev, 1),
                      mystery.get("reason", "Revealed effect worth playing here."),
                      display=mystery.get("name", "Mystery Booster"))
        else:
            notes.append("Mystery Booster: effect is revealed at the Round of 32 — re-check the moment it "
                         "unlocks; if clean-sheet-related it pairs with a defensive stack (a deep round).")

    # --- Maximum Captain -> the latest open round (default the Final): double your standout in the one
    #     game that matters most, by which point the Wildcard has built the strongest possible squad. ---
    if "Maximum Captain" in chips:
        mc_round = first_open(["final", "SF", "QF", "R16"])
        if mc_round:
            ev_txt = (f" Captain-ceiling edge over a fixed armband ≈ +{max_cap_ev:.0f} pts on the current XI."
                      if max_cap_ev else "")
            place(mc_round, "Maximum Captain", max_cap_ev,
                  f"Auto-doubles your highest scorer — save it for the biggest game, when your premium is "
                  f"set and captain risk is highest.{ev_txt}")

    schedule = [assigned[r] for r in rounds if r in assigned]
    this_round = assigned.get(target_round)
    if this_round:
        this_round = dict(this_round, status="PLAY")
    return {"this_round": this_round, "schedule": schedule, "notes": notes,
            "qb_by_round": {r: round(v, 1) for r, v in qb_by_round.items()}}
