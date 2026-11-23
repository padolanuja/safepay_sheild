"""GET /api/history/<user_id>?decision=HOLD&limit=100

Returns the payer's simulated transactions (with the SafePay decision where one was made)
and their assessments (including cancelled payments, which have no transaction).
"""
import json

from flask import Blueprint, abort, jsonify, request

from safepay.api import get_db

bp = Blueprint("history", __name__)


@bp.get("/history/<int:user_id>")
def history(user_id: int):
    conn = get_db()
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        abort(404, description=f"user {user_id} not found")
    decision = request.args.get("decision")
    if decision is not None and decision not in ("ALLOW", "WARN", "HOLD"):
        abort(400, description="decision must be ALLOW, WARN or HOLD")
    limit = request.args.get("limit", default=100, type=int)
    if not 1 <= limit <= 1000:
        abort(400, description="limit must be between 1 and 1000")

    q = "SELECT t.*, p.display_name FROM user_transactions t LEFT JOIN payees p ON p.vpa = t.payee_vpa " \
        "WHERE t.user_id = ?"
    args = [user_id]
    if decision:
        q += " AND t.decision = ?"
        args.append(decision)
    txns = [dict(r) for r in conn.execute(q + " ORDER BY t.ts DESC LIMIT ?", (*args, limit))]

    q = "SELECT id, created_at, payee_vpa_masked, amount, stage, decision, outcome, latency_ms, scenario_id, " \
        "explanation_json FROM risk_assessments WHERE user_id = ?"
    args = [user_id]
    if decision:
        q += " AND decision = ?"
        args.append(decision)
    assessments = []
    for r in conn.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)):
        d = dict(r)
        d["reason"] = json.loads(d.pop("explanation_json"))["reason"]
        assessments.append(d)
    return jsonify(user_id=user_id, transactions=txns, assessments=assessments, simulated=True)
