"""Simulated trusted-contact alerts. NOTHING IS SENT: alerts are logged to alerts_log and
returned as a preview ("SMS that would be sent").

Rules:
  * only for an assessment whose decision is HOLD (genuine payments never trigger alerts)
  * only to a contact of the same payer with consent_given = 1
  * minimum information: the payer's first name, an amount RANGE (never the exact amount),
    no payee ID and no account numbers
"""
from __future__ import annotations

from datetime import datetime

from safepay.i18n import t

RANGES = [(10_000, "range_below_10k"), (50_000, "range_10k_50k"), (float("inf"), "range_above_50k")]


class AlertRefused(ValueError):
    """The alert is not allowed (not a HOLD, no consent, wrong payer). Maps to HTTP 409/404."""


def amount_range_key(amount: float) -> str:
    return next(k for lim, k in RANGES if amount < lim)


def send_simulated_alert(conn, assessment_id: int, contact_id: int) -> dict:
    a = conn.execute("SELECT * FROM risk_assessments WHERE id = ?", (assessment_id,)).fetchone()
    if a is None:
        raise LookupError(f"assessment {assessment_id} not found")
    if a["decision"] != "HOLD":
        raise AlertRefused("alerts are only sent for a simulated HOLD")
    c = conn.execute("SELECT * FROM trusted_contacts WHERE id = ?", (contact_id,)).fetchone()
    if c is None or c["user_id"] != a["user_id"]:
        raise LookupError(f"contact {contact_id} not found for this payer")
    if not c["consent_given"]:
        raise AlertRefused("this contact has not given consent to receive alerts")

    user = conn.execute("SELECT name, language FROM users WHERE id = ?", (a["user_id"],)).fetchone()
    lang = user["language"]
    first = user["name"].split()[0]
    msg = t(lang, "alert.sms", name=first, contact=c["name"].split()[0],
            range=t(lang, f"alert.{amount_range_key(a['amount'])}"))
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.execute("INSERT INTO alerts_log (assessment_id, contact_id, message_preview, created_at, simulated)"
                       " VALUES (?,?,?,?,1)", (assessment_id, contact_id, msg, now))
    conn.commit()
    return {"id": cur.lastrowid, "simulated": True, "sent": False, "to": c["name"], "phone_masked": c["phone_masked"],
            "message_preview": msg, "language": lang, "created_at": now,
            "note": "Simulated — no SMS was sent."}
