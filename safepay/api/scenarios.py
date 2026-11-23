"""GET  /api/scenarios            the 13 one-click demo scenarios with their expected decisions
   POST /api/scenarios/<id>/run   run one through the full two-stage flow (assessments are saved)
"""
import json

from flask import Blueprint, abort, current_app, jsonify

from safepay.api import get_db
from safepay.assess import save_assessment
from safepay.scenarios import run_scenario

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
