"""Combined coercion score and band.

    rule_score_normalised = min(1, counted rule points / rule_score_cap)
    coercion_score        = max(rule_score_normalised, ml_probability)
    band                  = high   if coercion_score >= cutoffs.high
                            medium if coercion_score >= cutoffs.medium
                            low    otherwise
    Any counted severe indicator forces the band to "high".

The max() means either source can raise the alarm on its own: the rules catch
explicit patterns the model rarely saw (checklist items, contradictions), and the
model catches combinations the rules don't spell out. The cap and cut-offs come from
the validation split (models/coercion_model_card.json). If the ML model is
unavailable, the score is the rule score alone and the result says so.
"""
from __future__ import annotations


def band_for(score: float, cutoffs: dict) -> str:
    if score >= cutoffs["high"]:
        return "high"
    if score >= cutoffs["medium"]:
        return "medium"
    return "low"


def combine(points: float, ml_prob: float | None, severe: bool, cutoffs: dict, cap: float) -> dict:
    rule_norm = min(1.0, points / cap)
    score = rule_norm if ml_prob is None else max(rule_norm, ml_prob)
    band = band_for(score, cutoffs)
    forced = severe and band != "high"
    return {
        "coercion_score": round(score, 4),
        "rule_points": points,
        "rule_score_normalised": round(rule_norm, 4),
        "ml_probability": None if ml_prob is None else round(ml_prob, 4),
        "score_driver": ("rules" if ml_prob is None or rule_norm >= ml_prob else "ml_model"),
        "band": "high" if severe else band,
        "forced_high_by_severe": forced,
        "ml_used": ml_prob is not None,
        "cutoffs": cutoffs,
        "rule_score_cap": cap,
    }
