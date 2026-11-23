from flask import Blueprint, abort, jsonify, request

from safepay.api import get_db
from safepay.validation import ValidationError, validate_vpa

bp = Blueprint("payees", __name__)


@bp.get("/payees/<path:vpa>")
def lookup(vpa: str):
    try:
        vpa = validate_vpa(vpa)
    except ValidationError as e:
        abort(400, description=str(e))
    conn = get_db()
    row = conn.execute("SELECT * FROM payees WHERE vpa = ?", (vpa,)).fetchone()
    if row is None:
        abort(404, description="payee not found in the demo directory")
    n = conn.execute("SELECT COUNT(*) FROM payee_transactions WHERE payee_vpa = ?", (vpa,)).fetchone()[0]
    out = {
        "vpa": row["vpa"], "display_name": row["display_name"], "payee_type": row["payee_type"],
        "vpa_age_days": row["vpa_age_days"], "history_available": bool(row["history_available"]),
        "history_transactions": n, "history_source": row["history_source"],
    }
    # Optional: tell the UI whether this payee is new for the given payer.
    user_id = request.args.get("user_id", type=int)
    if user_id is not None:
        prior = conn.execute("SELECT COUNT(*) FROM user_transactions WHERE user_id = ? AND payee_vpa = ?",
                             (user_id, vpa)).fetchone()[0]
        out["new_for_user"] = prior == 0
    return jsonify(out)
