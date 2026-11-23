"""Run a seeded demo scenario through the full two-stage flow.

Each scenario's earlier payments (prior_payments_json) are inserted as
user_transactions with source='scenario_setup', relative to the run time. Earlier
scenario_setup rows for that payer are removed first, so scenarios don't leak into each other.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from safepay.assess import run_flow


def run_scenario(conn, mule_predictor, coercion_model, scenario_id: int, now: datetime | None = None) -> dict:
    s = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    if s is None:
        raise LookupError(f"scenario {scenario_id} not found")
    now = now or datetime.now().replace(microsecond=0)
    conn.execute("DELETE FROM user_transactions WHERE user_id = ? AND source = 'scenario_setup'", (s["user_id"],))
    ptype = conn.execute("SELECT payee_type FROM payees WHERE vpa = ?", (s["payee_vpa"],)).fetchone()[0]
    for p in json.loads(s["prior_payments_json"]):
        ts = (now - timedelta(minutes=p["minutes_ago"])).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute("INSERT INTO user_transactions (user_id, payee_vpa, payee_type, amount, ts, source)"
                     " VALUES (?,?,?,?,?, 'scenario_setup')", (s["user_id"], s["payee_vpa"], ptype, p["amount"], ts))
    conn.commit()
    flow = run_flow(conn, mule_predictor, coercion_model, user_id=s["user_id"], payee_vpa=s["payee_vpa"],
                    amount=s["amount"], situation=json.loads(s["situation_json"]),
                    answers=json.loads(s["answers_json"]), now=now)
    expected = json.loads(s["expected_decisions"])
    return {"scenario_id": scenario_id, "title": s["title"], "expected": expected,
            "actual": flow["final_decision"], "passed": flow["final_decision"] in expected,
            "expected_note": s["expected_note"], "flow": flow}
