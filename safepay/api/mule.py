"""POST /api/mule/predict  {"vpa": "name@handle"}  -> receiver-side mule result."""
from flask import Blueprint, abort, current_app, jsonify, request

from safepay.api import get_db
from safepay.validation import ValidationError, validate_vpa

bp = Blueprint("mule", __name__)


@bp.post("/mule/predict")
def predict():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, description="JSON body required: {\"vpa\": \"name@handle\"}")
    try:
        vpa = validate_vpa(body.get("vpa"))
    except ValidationError as e:
        abort(400, description=str(e))
    conn = get_db()
    if conn.execute("SELECT 1 FROM payees WHERE vpa = ?", (vpa,)).fetchone() is None:
        abort(404, description="payee not found in the demo directory")

    predictor = current_app.config["MULE_PREDICTOR"]
    result = predictor.predict_for_payee(conn, vpa)
    # Model missing or invalid -> 503, still with the explanatory body.
    return jsonify(result), (503 if result["status"] == "unavailable" else 200)
