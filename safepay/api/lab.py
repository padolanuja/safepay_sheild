"""Endpoints that let judges probe the system instead of watching presets.

GET  /api/lab/random?n=1&balanced=1&seed=   score random HELD-OUT test rows live (truth revealed after)
POST /api/lab/what-if                        same body as /api/assess: re-runs the real pipeline with one
                                             signal changed at a time ("what would change this decision?")

Nothing here is saved to the database.
"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, request

from safepay.api import get_db
from safepay.assess import assess as run_assess
from safepay.challenge import LabUnavailable, draw

bp = Blueprint("lab", __name__)


@bp.get("/lab/random")
def lab_random():
    try:
        n = int(request.args.get("n", 1))
        seed = request.args.get("seed")
        seed = int(seed) if seed not in (None, "") else None
    except ValueError:
        abort(400, description="n and seed must be integers")
    if not 1 <= n <= 1000:
        abort(400, description="n must be between 1 and 1000")
    balanced = request.args.get("balanced", "0") in ("1", "true", "yes")
    try:
        out = draw(current_app.config["COERCION_MODEL"], n, seed=seed, balanced=balanced)
    except LabUnavailable as e:
        abort(503, description=str(e))
    if n > 50:                       # keep the response small: metrics + a preview of rows
        out["results"] = out["results"][:50]
        out["results_truncated"] = True
    return jsonify(out)


# Each probe neutralises ONE signal that is currently present.
PROBES = [
    ("no_call", "If there were no phone call", lambda s: s["on_voice_call"] or s["on_video_call"],
     lambda s: {**s, "on_voice_call": 0, "on_video_call": 0, "call_duration_min": 0, "caller_in_contacts": 0,
                "caller_is_international": 0}),
    ("caller_known", "If the caller were a saved contact", lambda s: (s["on_voice_call"] or s["on_video_call"]) and not s["caller_in_contacts"],
     lambda s: {**s, "caller_in_contacts": 1, "caller_is_international": 0}),
    ("short_call", "If the call had lasted only 2 minutes", lambda s: s["call_duration_min"] > 5,
     lambda s: {**s, "call_duration_min": 2}),
    ("no_screen_share", "If screen sharing were off", lambda s: s["screen_share_active"],
     lambda s: {**s, "screen_share_active": 0}),
    ("no_remote_app", "If no remote-access app were installed", lambda s: s["remote_access_app_installed_7d"],
     lambda s: {**s, "remote_access_app_installed_7d": 0}),
    ("typed_vpa", "If the UPI ID had been typed, not pasted", lambda s: s["vpa_pasted"],
     lambda s: {**s, "vpa_pasted": 0}),
    ("calm_typing", "If typing had been calm (no hesitation, no stop-start)",
     lambda s: s["hesitation_events"] > 0 or s["segmented_typing"],
     lambda s: {**s, "hesitation_events": 0, "segmented_typing": 0}),
    ("no_app_switch", "If there had been no app switching", lambda s: s["app_switches"] > 0,
     lambda s: {**s, "app_switches": 0}),
    ("payee_contact", "If the payee were a saved contact", lambda s: not s["payee_in_contacts"],
     lambda s: {**s, "payee_in_contacts": 1}),
    ("no_fd", "If no FD had been broken this week", lambda s: s["fd_or_loan_liquidated_7d"],
     lambda s: {**s, "fd_or_loan_liquidated_7d": 0}),
    ("daytime", "If it were 2 pm instead", lambda s: s.get("hour_of_day") is not None and s["hour_of_day"] in (23, 0, 1, 2, 3, 4, 5),
     lambda s: {**s, "hour_of_day": 14}),
]


@bp.post("/lab/what-if")
def what_if():
    b = request.get_json(silent=True)
    if not isinstance(b, dict) or not isinstance(b.get("situation"), dict):
        abort(400, description="JSON body with a situation object required")
    if isinstance(b.get("user_id"), bool) or not isinstance(b.get("user_id"), int):
        abort(400, description="user_id must be an integer")
    cfg, conn = current_app.config, get_db()

    def run(situation, amount=None):
        r = run_assess(conn, cfg["MULE_PREDICTOR"], cfg["COERCION_MODEL"], user_id=b["user_id"],
                       payee_vpa=b.get("payee_vpa"), amount=amount if amount is not None else b.get("amount"),
                       situation=situation, stage="pre_pause", payee_type=b.get("payee_type"))
        return {"decision": r["decision"], "coercion_score": r["coercion"]["coercion_score"],
                "band": r["coercion"]["band"], "matrix_row": r["fusion"]["matrix_row"],
                "ml_probability": r["coercion"]["ml"].get("ml_probability")}

    sit = b["situation"]
    base = run(sit)
    probes = []
    for pid, label, applies, change in PROBES:
        try:
            if not applies(sit):
                continue
            r = run(change(sit))
        except Exception:  # noqa: BLE001 - a probe that can't run is skipped, never guessed
            continue
        probes.append({"id": pid, "label": label, **r,
                       "delta": round(r["coercion_score"] - base["coercion_score"], 4),
                       "flips": r["decision"] != base["decision"]})
    probes.sort(key=lambda p: p["delta"])

    # Amount sweep: the same situation at different amounts (shows a continuous response).
    sweep = []
    amt = float(b.get("amount") or 0)
    for mult in (0.1, 0.25, 0.5, 1, 2, 4):
        a = round(amt * mult)
        if 1 <= a <= 100_000:
            try:
                sweep.append({"multiplier": mult, "amount": a, **run(sit, a)})
            except Exception:  # noqa: BLE001
                pass
    return jsonify(simulated=True, base=base, probes=probes, amount_sweep=sweep)
