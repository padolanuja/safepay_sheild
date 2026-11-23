"""POST /api/assess            main endpoint: full combined decision (saved to risk_assessments)
   POST /api/coercion/assess   payer-side coercion result only (same pipeline, nothing saved)

Body:
  {"user_id": 1, "payee_vpa": "name@handle", "amount": 5000,
   "situation": {...simulated device signals...},
   "stage": "pre_pause" | "post_pause",              (default pre_pause)
   "answers": {...} (post_pause only), "payee_type": "P2P"|"P2M" (only for payees not in the directory)}
Errors: 400 invalid input, 404 unknown user, 503 model unavailable.
"""
from flask import Blueprint, abort, current_app, jsonify, request

from safepay.api import get_db
from safepay.assess import assess as run_assess
from safepay.assess import save_assessment

bp = Blueprint("assess", __name__)
ALLOWED = {"user_id", "payee_vpa", "amount", "situation", "stage", "answers", "payee_type"}


def _body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, description="JSON object body required")
    unknown = set(body) - ALLOWED
    if unknown:
        abort(400, description=f"unknown fields: {sorted(unknown)}")
    if isinstance(body.get("user_id"), bool) or not isinstance(body.get("user_id"), int):
        abort(400, description="user_id must be an integer")
    return body


def _run(body):
    cfg = current_app.config
    return run_assess(get_db(), cfg["MULE_PREDICTOR"], cfg["COERCION_MODEL"],
                      user_id=body["user_id"], payee_vpa=body.get("payee_vpa"), amount=body.get("amount"),
                      situation=body.get("situation"), stage=body.get("stage", "pre_pause"),
                      answers=body.get("answers"), payee_type=body.get("payee_type"))


@bp.post("/assess")
def assess():
    result = _run(_body())
    conn = get_db()
    result["assessment_id"] = save_assessment(conn, result)
    conn.commit()
    return jsonify(result)


@bp.post("/coercion/assess")
def coercion_assess():
    r = _run(_body())
    return jsonify({"simulated": True, "stage": r["stage"], "coercion": r["coercion"],
                    "behaviour": r["behaviour"], "inputs": r["inputs"], "latency_ms": r["latency_ms"]})
