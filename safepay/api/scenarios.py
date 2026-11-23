"""GET  /api/scenarios            the 13 one-click demo scenarios with their expected decisions
   POST /api/scenarios/<id>/run   run one through the full two-stage flow (assessments are saved)
"""
import json

from flask import Blueprint, abort, current_app, jsonify, request

from safepay.api import get_db
from safepay.assess import save_assessment
from safepay.scenarios import prepare_scenario, run_scenario, simulate_new_payee_payments

bp = Blueprint("scenarios", __name__)


@bp.get("/scenarios")
def list_scenarios():
    out = []
    for r in get_db().execute("SELECT s.*, u.name AS user_name, p.display_name AS payee_name FROM scenarios s "
                              "JOIN users u ON u.id = s.user_id JOIN payees p ON p.vpa = s.payee_vpa ORDER BY s.id"):
        d = dict(r)
        for k in ("situation_json", "answers_json", "prior_payments_json", "expected_decisions"):
            d[k.replace("_json", "")] = json.loads(d.pop(k))
        out.append(d)
    return jsonify(scenarios=out)


@bp.post("/scenarios/<int:sid>/prepare")
def prepare(sid: int):
    """Load a scenario into the interactive Pay screen: inserts its earlier payments (demo data)
    and returns its inputs and expected decision. The UI then runs the normal two-stage flow."""
    try:
        return jsonify(prepare_scenario(get_db(), sid))
    except LookupError as e:
        abort(404, description=str(e))


@bp.post("/simulator/new-payee-payments")
def simulator_new_payees():
    """Situation-simulator slider: N earlier payments to new payees in the last 24 h (demo data)."""
    b = request.get_json(silent=True) or {}
    uid, count = b.get("user_id"), b.get("count")
    conn = get_db()
    if isinstance(uid, bool) or not isinstance(uid, int) or \
            conn.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone() is None:
        abort(404, description=f"user {uid} not found")
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 8:
        abort(400, description="count must be an integer from 0 to 8")
    simulate_new_payee_payments(conn, uid, count)
    return jsonify(user_id=uid, inserted=count, source="simulator", simulated=True)


@bp.post("/scenarios/<int:sid>/run")
def run(sid: int):
    cfg, conn = current_app.config, get_db()
    try:
        r = run_scenario(conn, cfg["MULE_PREDICTOR"], cfg["COERCION_MODEL"], sid)
    except LookupError as e:
        abort(404, description=str(e))
    for stage in ("pre", "post"):
        if r["flow"][stage]:
            r["flow"][stage]["assessment_id"] = save_assessment(conn, r["flow"][stage], scenario_id=sid)
    conn.commit()
    return jsonify(r)
