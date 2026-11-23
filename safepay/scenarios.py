"""Demo scenarios and simulator helpers.

Each scenario's earlier payments (prior_payments_json) are inserted as user_transactions
with source='scenario_setup', relative to the run time. Earlier scenario_setup and
simulator rows for that payer are removed first, so runs don't leak into each other.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from safepay.assess import run_flow

SIM_SOURCES = ("scenario_setup", "simulator")


def clear_simulated_history(conn, user_id: int) -> None:
    conn.execute(f"DELETE FROM user_transactions WHERE user_id = ? AND source IN {SIM_SOURCES}", (user_id,))


def prepare_scenario(conn, scenario_id: int, now: datetime | None = None) -> dict:
    s = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    if s is None:
        raise LookupError(f"scenario {scenario_id} not found")
    now = now or datetime.now().replace(microsecond=0)
    clear_simulated_history(conn, s["user_id"])
    ptype = conn.execute("SELECT payee_type FROM payees WHERE vpa = ?", (s["payee_vpa"],)).fetchone()[0]
    priors = json.loads(s["prior_payments_json"])
    for p in priors:
        ts = (now - timedelta(minutes=p["minutes_ago"])).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute("INSERT INTO user_transactions (user_id, payee_vpa, payee_type, amount, ts, source)"
                     " VALUES (?,?,?,?,?, 'scenario_setup')", (s["user_id"], s["payee_vpa"], ptype, p["amount"], ts))
    conn.commit()
    return {"id": s["id"], "title": s["title"], "description": s["description"], "user_id": s["user_id"],
            "payee_vpa": s["payee_vpa"], "amount": s["amount"], "situation": json.loads(s["situation_json"]),
            "answers": json.loads(s["answers_json"]), "prior_payments": priors,
            "expected_decisions": json.loads(s["expected_decisions"]), "expected_note": s["expected_note"]}


def simulate_new_payee_payments(conn, user_id: int, count: int, amount_each: float = 5_000,
                                now: datetime | None = None) -> int:
    """Simulator slider: `count` earlier payments to brand-new payees in the last 24 h (demo data)."""
    now = now or datetime.now().replace(microsecond=0)
    conn.execute("DELETE FROM user_transactions WHERE user_id = ? AND source = 'simulator'", (user_id,))
    for i in range(count):
        ts = (now - timedelta(minutes=45 * (i + 1))).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute("INSERT INTO user_transactions (user_id, payee_vpa, payee_type, amount, ts, source)"
                     " VALUES (?,?, 'P2P', ?, ?, 'simulator')", (user_id, f"sim.newpayee{i + 1}@demoupi", amount_each, ts))
    conn.commit()
    return count


def run_scenario(conn, mule_predictor, coercion_model, scenario_id: int, now: datetime | None = None) -> dict:
    now = now or datetime.now().replace(microsecond=0)
    s = prepare_scenario(conn, scenario_id, now)
    flow = run_flow(conn, mule_predictor, coercion_model, user_id=s["user_id"], payee_vpa=s["payee_vpa"],
                    amount=s["amount"], situation=s["situation"], answers=s["answers"], now=now)
    return {"scenario_id": scenario_id, "title": s["title"], "expected": s["expected_decisions"],
            "actual": flow["final_decision"], "passed": flow["final_decision"] in s["expected_decisions"],
            "expected_note": s["expected_note"], "flow": flow}
