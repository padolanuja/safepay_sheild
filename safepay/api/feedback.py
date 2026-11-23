"""Rescue feedback loop. Stored for human review ONLY: nothing is ever retrained from it.

POST /api/feedback                 {"assessment_id", "outcome": was_scam|was_genuine|rescued,
                                    "note", "submitted_by": user|analyst}
GET  /api/feedback?verified=0      review queue
POST /api/feedback/<id>/verify     analyst marks an item verified
"""
from datetime import datetime

from flask import Blueprint, abort, jsonify, request

from safepay.api import get_db
from safepay.validation import ValidationError, validate_enum

bp = Blueprint("feedback", __name__)
NOTE = "Stored for human review only. Models are never auto-retrained from feedback."


@bp.post("/feedback")
def add():
    b = request.get_json(silent=True)
    if not isinstance(b, dict):
        abort(400, description="JSON object body required")
    try:
        outcome = validate_enum("outcome", b.get("outcome"), ("was_scam", "was_genuine", "rescued"))
        by = validate_enum("submitted_by", b.get("submitted_by", "user"), ("user", "analyst"))
    except ValidationError as e:
        abort(400, description=str(e))
    note = b.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 500):
        abort(400, description="note must be a string of at most 500 characters")
    aid = b.get("assessment_id")
    conn = get_db()
    if isinstance(aid, bool) or not isinstance(aid, int) or \
            conn.execute("SELECT 1 FROM risk_assessments WHERE id = ?", (aid,)).fetchone() is None:
        abort(404, description=f"assessment {aid} not found")
    cur = conn.execute("INSERT INTO feedback (assessment_id, outcome, note, submitted_by, verified, created_at)"
                       " VALUES (?,?,?,?,0,?)", (aid, outcome, note, by, datetime.now().strftime("%Y-%m-%dT%H:%M:%S")))
    conn.commit()
    return jsonify(id=cur.lastrowid, assessment_id=aid, outcome=outcome, verified=False, note=NOTE), 201


@bp.get("/feedback")
def queue():
    verified = request.args.get("verified", type=int)
    q = ("SELECT f.*, a.decision, a.amount, a.payee_vpa_masked, a.user_id FROM feedback f "
         "JOIN risk_assessments a ON a.id = f.assessment_id")
    args = ()
    if verified in (0, 1):
        q += " WHERE f.verified = ?"
        args = (verified,)
    rows = get_db().execute(q + " ORDER BY f.id DESC", args)
    return jsonify(feedback=[dict(r) for r in rows], note=NOTE)


@bp.post("/feedback/<int:fid>/verify")
def verify(fid: int):
    conn = get_db()
    cur = conn.execute("UPDATE feedback SET verified = 1 WHERE id = ?", (fid,))
    if cur.rowcount == 0:
        abort(404, description=f"feedback {fid} not found")
    conn.commit()
    return jsonify(id=fid, verified=True, note=NOTE)
