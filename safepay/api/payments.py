"""POST /api/payments/confirm  {"assessment_id": 12, "action": "proceed"|"cancel", "payee_vpa": "..."}

Records the SIMULATED outcome after an assessment. No money moves.
  * cancel  -> outcome 'cancelled'
  * proceed -> outcome 'proceeded' and a user_transactions row (status completed_simulated)
  * proceed on a HOLD is refused (409) until the cooling-off period has passed
The payee VPA is re-sent because logs only keep it masked; it must match the masked value.
"""
from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, request

import config
from safepay import db
from safepay.api import get_db
from safepay.validation import ValidationError, validate_enum, validate_vpa

bp = Blueprint("payments", __name__)


@bp.post("/payments/confirm")
def confirm():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, description="JSON object body required")
    try:
        action = validate_enum("action", body.get("action"), ("proceed", "cancel"))
        vpa = validate_vpa(body.get("payee_vpa"))
    except ValidationError as e:
        abort(400, description=str(e))
    aid = body.get("assessment_id")
    if isinstance(aid, bool) or not isinstance(aid, int):
        abort(400, description="assessment_id must be an integer")

    conn = get_db()
    a = conn.execute("SELECT * FROM risk_assessments WHERE id = ?", (aid,)).fetchone()
    if a is None:
        abort(404, description=f"assessment {aid} not found")
    if a["outcome"] is not None:
        abort(409, description=f"assessment {aid} already has an outcome: {a['outcome']}")
    if db.mask_vpa(vpa) != a["payee_vpa_masked"]:
        abort(400, description="payee_vpa does not match the assessed payment")

    created = datetime.strptime(a["created_at"], "%Y-%m-%dT%H:%M:%S")
    hold_until = created + timedelta(minutes=config.COOLING_OFF_MINUTES)
    if action == "proceed" and a["decision"] == "HOLD" and datetime.now() < hold_until:
        abort(409, description=f"simulated cooling-off hold until {hold_until:%H:%M}; payment not allowed yet")

    outcome = "proceeded" if action == "proceed" else "cancelled"
    conn.execute("UPDATE risk_assessments SET outcome = ? WHERE id = ?", (outcome, aid))
    txn_id = None
    if action == "proceed":
        ptype = conn.execute("SELECT payee_type FROM payees WHERE vpa = ?", (vpa,)).fetchone()
        cur = conn.execute(
            "INSERT INTO user_transactions (user_id, payee_vpa, payee_type, amount, ts, status, decision,"
            " assessment_id, source) VALUES (?,?,?,?,?, 'completed_simulated', ?, ?, 'app')",
            (a["user_id"], vpa, ptype["payee_type"] if ptype else "P2P", a["amount"],
             datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), a["decision"], aid))
        txn_id = cur.lastrowid
    conn.commit()
    return jsonify(assessment_id=aid, outcome=outcome, transaction_id=txn_id, simulated=True,
                   message="Simulated — no real money moved.")
