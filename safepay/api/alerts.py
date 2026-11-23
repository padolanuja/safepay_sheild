"""POST /api/alerts/trusted-contact  {"assessment_id", "contact_id"}  -> simulated alert preview
   GET  /api/alerts?user_id=                                         -> simulated alerts log
Only for a HOLD, only to a consenting contact. Nothing is ever sent.
"""
from flask import Blueprint, abort, jsonify, request

from safepay.alerts.trusted_contact import AlertRefused, send_simulated_alert
from safepay.api import get_db

bp = Blueprint("alerts", __name__)


@bp.post("/alerts/trusted-contact")
def alert():
    b = request.get_json(silent=True)
    if not isinstance(b, dict):
        abort(400, description="JSON object body required")
    aid, cid = b.get("assessment_id"), b.get("contact_id")
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (aid, cid)):
        abort(400, description="assessment_id and contact_id must be integers")
    try:
        return jsonify(send_simulated_alert(get_db(), aid, cid)), 201
    except LookupError as e:
        abort(404, description=str(e))
    except AlertRefused as e:
        abort(409, description=str(e))


@bp.get("/alerts")
def log():
    uid = request.args.get("user_id", type=int)
    if uid is None:
        abort(400, description="user_id query parameter required")
    rows = get_db().execute(
        "SELECT l.*, c.name AS contact_name FROM alerts_log l JOIN trusted_contacts c ON c.id = l.contact_id "
        "WHERE c.user_id = ? ORDER BY l.id DESC", (uid,))
    return jsonify(alerts=[dict(r) for r in rows], simulated=True)
