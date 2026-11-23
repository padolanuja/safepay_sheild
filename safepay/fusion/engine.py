"""Risk fusion engine: a documented decision matrix, not one opaque score.

Rows are checked top to bottom; the first match sets the base decision.
    R1  any counted SEVERE coercion indicator                    -> HOLD
    R2  coercion band high (any mule status)                     -> HOLD
    R3  mule band high AND coercion >= medium                    -> HOLD
    R4  STRONG answer-vs-signal contradiction AND coercion >= medium -> HOLD
    R5  coercion band medium                                     -> WARN
    R6  mule band elevated/high AND coercion low                 -> WARN ("suspicious money movement")
    R6b new payee AND amount >= LARGE_AMOUNT_RATIO x usual, coercion low
                                                                 -> WARN + VERIFY, labelled an
        "unusual payment check", NOT a coercion finding (coercion stays low). Added in Phase 5
        so hard negatives such as a large hospital bill get verification friction without a
        hold, while an unknown payee or high amount still never establishes coercion.
    R7  otherwise                                                -> ALLOW
Adjustments, in order:
    A1  splitting pattern detected        -> at least WARN
    A2  trusted beneficiary OR matching planned payment -> down one level,
        NEVER when a severe indicator is present
Stage rule: at pre_pause, R3 gives WARN (questions first), not HOLD. The pre-pause
"medium" cut-off was chosen for 90% recall: it is a screening level meaning "worth asking",
not evidence of pressure. R3's HOLD applies at post_pause, after the answers. (R4 needs
answers, so it can only fire at post_pause anyway.) R1 and R2 HOLD at either stage.
When the mule status is insufficient_history or unavailable, R3 and R6 can't fire.
The decision then rests on coercion signals alone, and the explanation says so.
"""
from __future__ import annotations

ALLOW, WARN, HOLD = "ALLOW", "WARN", "HOLD"
LEVEL = {ALLOW: 0, WARN: 1, HOLD: 2}
ORDER = [ALLOW, WARN, HOLD]
RANK = {"low": 0, "medium": 1, "high": 2}


def decide(mule: dict, coercion: dict, splitting: dict, trust: dict, unusual: dict | None = None,
           stage: str = "post_pause") -> dict:
    """mule: predictor result; coercion: {band, severe, strong_contradiction};
    splitting: detect() result; trust: {trusted_beneficiary, planned_payment_match};
    unusual: {detected} large first payment to a new payee (R6b)."""
    mule_ok = mule.get("status") == "ok"
    mule_band = mule.get("band") if mule_ok else None
    c_band, severe = coercion["band"], coercion["severe"]
    trail = []

    if severe:
        base, row = HOLD, "R1"
    elif c_band == "high":
        base, row = HOLD, "R2"
    elif mule_band == "high" and RANK[c_band] >= 1:
        base, row = (WARN if stage == "pre_pause" else HOLD), "R3"
    elif coercion["strong_contradiction"] and RANK[c_band] >= 1:
        base, row = HOLD, "R4"
    elif c_band == "medium":
        base, row = WARN, "R5"
    elif mule_band in ("elevated", "high"):
        base, row = WARN, "R6"
    elif unusual and unusual.get("detected"):
        base, row = WARN, "R6b"
    else:
        base, row = ALLOW, "R7"
    trail.append({"step": row, "decision": base})
    decision = base

    if splitting.get("detected") and LEVEL[decision] < LEVEL[WARN]:
        decision = WARN
        trail.append({"step": "A1", "decision": decision, "note": "splitting pattern -> at least WARN"})

    trusted = trust.get("trusted_beneficiary") or trust.get("planned_payment_match")
    if trusted:
        if severe:
            trail.append({"step": "A2", "decision": decision,
                          "note": "trusted payee / planned payment NOT applied: a severe indicator is present"})
        elif decision != ALLOW:
            decision = ORDER[LEVEL[decision] - 1]
            trail.append({"step": "A2", "decision": decision, "note": "trusted payee / planned payment: down one level"})

    return {
        "decision": decision,
        "matrix_row": row,
        "trail": trail,
        "mule_considered": mule_ok,
        "coercion_only": not mule_ok,
        "trust_applied": bool(trusted) and not severe and base != ALLOW,
    }
