"""Trust settings (all fictional demo data).

GET|POST           /api/contacts                    trusted contacts (?user_id=)
POST               /api/contacts/<id>/consent       {"consent_given": true|false}
DELETE             /api/contacts/<id>
GET|POST           /api/trusted-beneficiaries       (?user_id=)
GET|POST           /api/planned-payments            (?user_id=)

Phone numbers are masked before storage (only the first 2 and last 2 digits are kept).
"""
import re
from datetime import datetime

from flask import Blueprint, abort, jsonify, request

from safepay.api import get_db
from safepay.validation import ValidationError, validate_amount, validate_bool, validate_vpa

bp = Blueprint("trust", __name__)


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _user_id_arg():
    uid = request.args.get("user_id", type=int)
    if uid is None:
        abort(400, description="user_id query parameter required")
    return uid


def _body():
    b = request.get_json(silent=True)
    if not isinstance(b, dict):
        abort(400, description="JSON object body required")
    return b


def _check_user(conn, uid):
    if isinstance(uid, bool) or not isinstance(uid, int) or \
            conn.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone() is None:
        abort(404, description=f"user {uid} not found")


def _text(b, key, max_len=60, required=True):
    v = b.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip() or len(v) > max_len:
        abort(400, description=f"{key} must be a non-empty string (max {max_len} chars)")
    return v.strip()


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if not 10 <= len(digits) <= 13:
        raise ValidationError("phone must have 10-13 digits")
    national = digits[-10:]          # drop a country code such as +91
    return national[:2] + "x" * 6 + national[-2:]


# ------------------------------------------------------------------ contacts
@bp.get("/contacts")
def list_contacts():
    rows = get_db().execute("SELECT * FROM trusted_contacts WHERE user_id = ? ORDER BY id", (_user_id_arg(),))
    return jsonify(contacts=[dict(r) for r in rows])


@bp.post("/contacts")
def add_contact():
    b, conn = _body(), get_db()
    _check_user(conn, b.get("user_id"))
    try:
        phone = mask_phone(_text(b, "phone", 20))
        consent = validate_bool("consent_given", b.get("consent_given", False))
    except ValidationError as e:
        abort(400, description=str(e))
    cur = conn.execute(
        "INSERT INTO trusted_contacts (user_id, name, relation, phone_masked, consent_given, consent_timestamp)"
        " VALUES (?,?,?,?,?,?)", (b["user_id"], _text(b, "name"), _text(b, "relation", 30), phone, consent,
                                  _now() if consent else None))
    conn.commit()
    return jsonify(dict(conn.execute("SELECT * FROM trusted_contacts WHERE id = ?", (cur.lastrowid,)).fetchone())), 201


@bp.post("/contacts/<int:cid>/consent")
def set_consent(cid: int):
    b, conn = _body(), get_db()
    try:
        consent = validate_bool("consent_given", b.get("consent_given"))
    except ValidationError as e:
        abort(400, description=str(e))
    cur = conn.execute("UPDATE trusted_contacts SET consent_given = ?, consent_timestamp = ? WHERE id = ?",
                       (consent, _now() if consent else None, cid))
    if cur.rowcount == 0:
        abort(404, description=f"contact {cid} not found")
    conn.commit()
    return jsonify(dict(conn.execute("SELECT * FROM trusted_contacts WHERE id = ?", (cid,)).fetchone()))


@bp.delete("/contacts/<int:cid>")
def delete_contact(cid: int):
    conn = get_db()
    if conn.execute("SELECT 1 FROM alerts_log WHERE contact_id = ?", (cid,)).fetchone():
        # keep the audit trail: withdraw consent instead of deleting a contact that was alerted
        conn.execute("UPDATE trusted_contacts SET consent_given = 0, consent_timestamp = NULL WHERE id = ?", (cid,))
        conn.commit()
        return jsonify(id=cid, deleted=False, consent_withdrawn=True,
                       message="Contact has past simulated alerts; consent withdrawn instead of deleting.")
    cur = conn.execute("DELETE FROM trusted_contacts WHERE id = ?", (cid,))
    if cur.rowcount == 0:
        abort(404, description=f"contact {cid} not found")
    conn.commit()
    return jsonify(id=cid, deleted=True)


# ------------------------------------------------------------ beneficiaries
@bp.get("/trusted-beneficiaries")
def list_beneficiaries():
    rows = get_db().execute("SELECT * FROM trusted_beneficiaries WHERE user_id = ? ORDER BY id", (_user_id_arg(),))
    return jsonify(beneficiaries=[dict(r) for r in rows])


@bp.post("/trusted-beneficiaries")
def add_beneficiary():
    b, conn = _body(), get_db()
    _check_user(conn, b.get("user_id"))
    try:
        vpa = validate_vpa(b.get("payee_vpa"))
    except ValidationError as e:
        abort(400, description=str(e))
    if conn.execute("SELECT 1 FROM trusted_beneficiaries WHERE user_id = ? AND payee_vpa = ?",
                    (b["user_id"], vpa)).fetchone():
        abort(409, description="already a trusted beneficiary")
    cur = conn.execute("INSERT INTO trusted_beneficiaries (user_id, payee_vpa, label, added_at) VALUES (?,?,?,?)",
                       (b["user_id"], vpa, _text(b, "label", required=False), _now()))
    conn.commit()
    return jsonify(dict(conn.execute("SELECT * FROM trusted_beneficiaries WHERE id = ?", (cur.lastrowid,)).fetchone())), 201


# --------------------------------------------------------------- planned
@bp.get("/planned-payments")
def list_planned():
    rows = get_db().execute("SELECT * FROM planned_payments WHERE user_id = ? ORDER BY id", (_user_id_arg(),))
    return jsonify(planned_payments=[dict(r) for r in rows])


@bp.post("/planned-payments")
def add_planned():
    b, conn = _body(), get_db()
    _check_user(conn, b.get("user_id"))
    try:
        vpa = validate_vpa(b.get("payee_vpa"))
        amount = validate_amount(b.get("amount"))
    except ValidationError as e:
        abort(400, description=str(e))
    day = b.get("day_of_month")
    if day is not None and (isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31):
        abort(400, description="day_of_month must be 1-31 or null")
    cur = conn.execute(
        "INSERT INTO planned_payments (user_id, payee_vpa, amount, day_of_month, purpose, created_at)"
        " VALUES (?,?,?,?,?,?)", (b["user_id"], vpa, amount, day, _text(b, "purpose", required=False), _now()))
    conn.commit()
    return jsonify(dict(conn.execute("SELECT * FROM planned_payments WHERE id = ?", (cur.lastrowid,)).fetchone())), 201
