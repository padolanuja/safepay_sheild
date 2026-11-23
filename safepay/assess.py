"""End-to-end assessment of one payment attempt (the service behind POST /api/assess).

    context (every input tagged with its source)
      -> Module A: mule risk (receiver side)
      -> Module B: coercion rules + ML model + answer check (payer side)
      -> behaviour profile + splitting detection
      -> fusion engine (documented decision matrix)
      -> explanation (ranked factors from rules, SHAP and behaviour; plain-language reason)

Two-stage flow (run_flow): the first pass runs before any questions (pre_pause). If it
isn't ALLOW, the pause questions are asked and a post_pause pass runs. The final
decision is the HIGHER of the two: answers can raise risk (contradictions, admitted
guidance) but never lower it, because coerced victims are coached to lie (DATA_CARD).
A WARN that the answers don't escalate lets the user proceed after verifying.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import config
from safepay import db
from safepay.behaviour import profile as prof
from safepay.behaviour.splitting import detect as detect_splitting
from safepay.coercion.answer_check import check_answers
from safepay.coercion.model import CATEGORIES
from safepay.coercion.rules import CHECKLIST_ITEMS, evaluate_rules, has_severe, rule_points
from safepay.coercion.scoring import combine
from safepay.fusion.engine import LEVEL, ORDER, decide
from safepay.validation import (ValidationError, validate_amount, validate_bool, validate_enum,
                                validate_vpa)

SITUATION_BOOL = ["on_voice_call", "on_video_call", "caller_in_contacts", "caller_is_international",
                  "screen_share_active", "remote_access_app_installed_7d", "segmented_typing", "vpa_pasted",
                  "fd_or_loan_liquidated_7d", "payee_in_contacts"]
SITUATION_NUM = {"call_duration_min": (0, 900), "session_duration_sec": (1, 3600), "hesitation_events": (0, 50),
                 "app_switches": (0, 50)}

REASONS = {
    "R1": "Serious warning signs: {severe}. For your safety this payment is on a simulated hold.",
    "R2": "Several strong signs that someone may be pressuring you to pay.",
    "R3": "This account shows suspicious money movement, and there are signs this payment may not be your free choice.",
    "R4": "Your answers don't match what your phone shows, and there are signs of pressure.",
    "R5": "Some signs that this payment may not be your own free choice. Please check before you pay.",
    "R6": "This account shows suspicious money movement.",
    "R6b": ("This payment is much larger than usual and goes to someone you haven't paid before. "
            "Please check the details before paying. This alone is not a sign of a scam."),
    "R7": "No warning signs found.",
}
ACTIONS = {
    "ALLOW": "Payment can go ahead (simulated).",
    "WARN": "Pause and answer a few questions. Check the payee with someone you trust before paying.",
    "HOLD": (f"Cooling-off: wait {config.COOLING_OFF_MINUTES} minutes (simulated hold). Hang up on anyone "
             f"guiding you. Call the cybercrime helpline {config.HELPLINE_NUMBER} or visit {config.HELPLINE_URL}."),
}


class NotFound(LookupError):
    pass


# --------------------------------------------------------------------- validation
def validate_situation(s) -> dict:
    if not isinstance(s, dict):
        raise ValidationError("situation must be an object")
    out = {}
    missing = [k for k in SITUATION_BOOL + list(SITUATION_NUM) if k not in s]
    if missing:
        raise ValidationError(f"situation is missing: {missing}")
    for k in SITUATION_BOOL:
        out[k] = validate_bool(k, s[k])
    for k, (lo, hi) in SITUATION_NUM.items():
        v = s[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValidationError(f"{k} must be a number between {lo} and {hi}")
        out[k] = float(v)
    if not (out["on_voice_call"] or out["on_video_call"]) and out["call_duration_min"] > 0:
        raise ValidationError("call_duration_min must be 0 when not on a call")
    for k in ("hour_of_day", "simulated_day_of_month"):
        if s.get(k) is not None:
            lim = 23 if k == "hour_of_day" else 31
            if isinstance(s[k], bool) or not isinstance(s[k], int) or not (0 if lim == 23 else 1) <= s[k] <= lim:
                raise ValidationError(f"{k} out of range")
            out[k] = s[k]
    return out


def validate_answers(a) -> dict:
    if not isinstance(a, dict):
        raise ValidationError("answers must be an object for the post_pause stage")
    out = {k: validate_enum(k, a.get(k), CATEGORIES[k]) for k in CATEGORIES}
    checklist = a.get("checklist", [])
    if not isinstance(checklist, list) or any(c not in CHECKLIST_ITEMS for c in checklist):
        raise ValidationError(f"checklist items must be from {sorted(CHECKLIST_ITEMS)}")
    out["checklist"] = list(dict.fromkeys(checklist))
    return out


# ------------------------------------------------------------------------ helpers
def _tag(ctx_sources: dict, key, value, source):
    ctx_sources[key] = {"value": value, "source": source}
    return value


def _planned_match(conn, user_id, vpa, amount, day) -> dict | None:
    for p in conn.execute("SELECT * FROM planned_payments WHERE user_id = ? AND payee_vpa = ?", (user_id, vpa)):
        amt_ok = abs(amount - p["amount"]) <= p["amount"] * p["amount_tolerance_pct"] / 100
        day_ok = p["day_of_month"] is None or abs(day - p["day_of_month"]) <= p["day_window"]
        if amt_ok and day_ok:
            return {"id": p["id"], "purpose": p["purpose"], "amount": p["amount"], "day_of_month": p["day_of_month"]}
    return None


# --------------------------------------------------------------------- assessment
def assess(conn, mule_predictor, coercion_model, *, user_id, payee_vpa, amount, situation,
           stage="pre_pause", answers=None, now: datetime | None = None, payee_type=None) -> dict:
    t0 = time.perf_counter()
    now = now or datetime.now().replace(microsecond=0)
    vpa = validate_vpa(payee_vpa)
    amount = validate_amount(amount)
    validate_enum("stage", stage, ("pre_pause", "post_pause"))
    sit = validate_situation(situation)
    ans = validate_answers(answers) if stage == "post_pause" else None

    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        raise NotFound(f"user {user_id} not found")
    payee = conn.execute("SELECT * FROM payees WHERE vpa = ?", (vpa,)).fetchone()
    if payee is None and payee_type is None:
        raise ValidationError("payee is not in the demo directory; payee_type (P2P/P2M) is required")
    ptype = payee["payee_type"] if payee else validate_enum("payee_type", payee_type, ("P2P", "P2M"))

    # ---- context, each input tagged with where it came from
    src = {}
    hist = prof.load_history(conn, user_id, now)
    profile = prof.build_profile(hist)
    hour = sit.get("hour_of_day", now.hour)
    day = sit.get("simulated_day_of_month", now.day)
    is_new = prof.payee_is_new(hist, vpa, now)
    vel = prof.velocity(hist, now)
    avg = float(user["avg_txn_amount_90d"])
    bal = float(user["balance_inr"])
    ctx = {"stage": stage}
    ctx["amount_inr"] = _tag(src, "amount_inr", amount, "user_entered")
    ctx["payee_type"] = _tag(src, "payee_type", ptype, "db_derived")
    for k in ("user_age", "account_tenure_months"):
        col = "age" if k == "user_age" else k
        ctx[k] = _tag(src, k, int(user[col]), "db_derived")
    ctx["avg_txn_amount_90d"] = _tag(src, "avg_txn_amount_90d", avg, "db_derived")
    ctx["balance_before_txn"] = _tag(src, "balance_before_txn", bal, "db_derived")
    ctx["amount_to_avg_ratio"] = _tag(src, "amount_to_avg_ratio", round(amount / avg, 2), "db_derived")
    ctx["amount_pct_of_balance"] = _tag(src, "amount_pct_of_balance", round(100 * amount / bal, 2), "db_derived")
    ctx["payee_is_new"] = _tag(src, "payee_is_new", int(is_new), "db_derived")
    for k, v in vel.items():
        ctx[k] = _tag(src, k, v, "db_derived")
    ctx["hour_of_day"] = _tag(src, "hour_of_day", hour,
                              "simulated_device_signal" if "hour_of_day" in sit else "clock")
    for k in SITUATION_BOOL + list(SITUATION_NUM):
        ctx[k] = _tag(src, k, sit[k], "simulated_device_signal")
    if ans:
        for k, v in ans.items():
            ctx[k] = _tag(src, k, v, "user_answer")

    # ---- Module B: coercion
    contradictions = check_answers(ctx)
    ctx["contradictions"] = contradictions
    indicators = evaluate_rules(ctx)
    points, severe = rule_points(indicators), has_severe(indicators)
    ml = coercion_model.predict(ctx, stage) if coercion_model.available else \
        {"status": "unavailable", "reason": coercion_model.error}
    card = coercion_model.card if coercion_model.available else None
    if card is None:
        raise RuntimeError("coercion model card unavailable: cut-offs were never validated; refusing to score")
    cut, cap = card["cutoffs"][stage], card["rule_score_cap"][stage]
    # Pressure gate for the ML model (mirrors the rules' gate): with no pressure indicator,
    # if most of the model's risk-raising evidence is the payee and the amount, the model's
    # concern is not counted. An unknown payee or a high amount can never establish coercion
    # on its own; the fusion engine's R6b may still ask the user to verify a large first payment.
    pressure = any(i["kind"] == "pressure" and i["counted"] for i in indicators)
    ml_in, gate = None, "not_applied"
    if ml["status"] == "ok":
        ml_in = ml["ml_probability"]
        if not pressure and ml["payee_amount_share_of_risk"] >= config.PAYEE_AMOUNT_ONLY_SHARE:
            ml_in, gate = None, "applied"
    score = combine(points, ml_in, severe, cut, cap)
    coercion = {**score, "severe": severe, "indicators": indicators, "contradictions": contradictions,
                "strong_contradiction": any(c["strength"] == "strong" for c in contradictions), "ml": ml,
                "pressure_present": pressure, "payee_amount_gate": gate}

    # ---- Module A: mule
    mule = mule_predictor.predict_for_payee(conn, vpa) if payee else {
        "vpa": vpa, "status": "insufficient_history", "model_called": False, "transactions": 0,
        "min_history": config.MULE_MIN_HISTORY_TXNS, "reason_key": "mule.insufficient_history",
        "reason": "This UPI ID has no transaction history with us. The decision rests on the payment situation only."}

    # ---- behaviour
    new_payees = {v for v in hist.payee_vpa.unique() if prof.payee_is_new(hist, v, now)}
    split = detect_splitting(hist, vpa, amount, now, avg, new_payees)
    devs = prof.deviations(profile, amount, hour, vpa, avg, is_new)

    # ---- trust
    tb = conn.execute("SELECT label FROM trusted_beneficiaries WHERE user_id = ? AND payee_vpa = ?",
                      (user_id, vpa)).fetchone()
    planned = _planned_match(conn, user_id, vpa, amount, day)
    trust = {"trusted_beneficiary": tb is not None, "trusted_label": tb["label"] if tb else None,
             "planned_payment_match": planned is not None, "planned_payment": planned}

    # ---- fusion
    unusual = {"detected": bool(is_new and amount / avg >= config.LARGE_AMOUNT_RATIO),
               "ratio": round(amount / avg, 1), "source": "behaviour_profile"}
    fusion = decide(mule, coercion, split, trust, unusual, stage)
    explanation = explain(fusion, mule, coercion, split, devs, trust, cap)

    return {
        "simulated": True,
        "stage": stage,
        "decision": fusion["decision"],
        "fusion": fusion,
        "mule": mule,
        "coercion": coercion,
        "behaviour": {"profile": profile, "deviations": devs, "splitting": split},
        "trust": trust,
        "explanation": explanation,
        "recommended_action": ACTIONS[fusion["decision"]],
        "language": user["language"],
        "inputs": src,
        "payee": {"vpa": vpa, "masked": db.mask_vpa(vpa), "display_name": payee["display_name"] if payee else None,
                  "payee_type": ptype, "new_for_user": is_new},
        "user_id": user_id,
        "amount": amount,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def _scaled(factors, score):
    """SHAP units differ (probability for RF, log-odds for XGBoost). Put them on one scale:
    the factor's share of its model's risk-raising evidence x that model's score."""
    total = sum(f["contribution"] for f in factors if f["contribution"] > 0)
    return [(f, round(score * f["contribution"] / total, 4) if total else 0.0) for f in factors]


def explain(fusion, mule, coercion, split, devs, trust, cap) -> dict:
    # impact = share of the coercion/mule score this factor accounts for (common 0-1 scale)
    factors = []
    for i in coercion["indicators"]:
        if i["counted"]:
            factors.append({"source": "rule", "id": i["id"], "text": i["evidence"], "severity": i["severity"],
                            "impact": round(min(1.0, i["weight"] / cap), 4), "text_key": i["text_key"]})
    ml = coercion["ml"]
    if ml["status"] == "ok" and coercion["ml_used"]:
        for f, imp in _scaled(ml["risk_raising_factors"], ml["ml_probability"]):
            factors.append({"source": "model_prediction:coercion", "id": f["feature"], "text": f["text"],
                            "impact": imp, "shap_value": f["contribution"], "text_key": f["text_key"]})
    if mule.get("status") == "ok" and mule.get("band") != "low":
        for f, imp in _scaled(mule["risk_raising_factors"], mule["mule_probability"]):
            factors.append({"source": "model_prediction:mule", "id": f["feature"], "text": f["text"],
                            "impact": imp, "shap_value": f["contribution"], "text_key": f["text_key"]})
    for p in split["patterns"]:
        factors.append({"source": "rule:splitting", "id": p["id"], "text": p["text"], "impact": None,
                        "text_key": f"split.{p['id']}"})
    factors.sort(key=lambda f: (f.get("severity") != "severe", -(f["impact"] or 0)))

    severe_text = "; ".join(i["evidence"] for i in coercion["indicators"] if i["severity"] == "severe" and i["counted"])
    reason = REASONS[fusion["matrix_row"]].format(severe=severe_text)
    notes = []
    if coercion.get("payee_amount_gate") == "applied":
        notes.append("A new payee or a large amount was noted, but with no signs of pressure it is not "
                     "treated as coercion on its own.")
    if fusion["coercion_only"]:
        notes.append(mule.get("reason") or "The payee account could not be checked.")
    if split["detected"]:
        notes.append("Several payments in a short time: " + "; ".join(p["text"] for p in split["patterns"]) + ".")
    for step in fusion["trail"]:
        if step["step"] == "A2":
            notes.append(step["note"].capitalize() + ".")
    return {
        "reason": reason,
        "reason_key": f"reason.{fusion['matrix_row']}",
        "notes": notes,
        "factors": factors,
        "context": [d["text"] for d in devs],      # behaviour deviations: context, not evidence
        "contradictions": [c["gentle_message"] for c in coercion["contradictions"]],
        "decided_on_coercion_only": fusion["coercion_only"],
        "disclaimer": "Simulated — no real money moved. Model results come from synthetic or competition "
                      "data and show feasibility, not real-world accuracy.",
    }


def run_flow(conn, mule_predictor, coercion_model, *, answers, **req) -> dict:
    """Pre-pause pass, then (if not ALLOW) the post-pause pass with answers. Final = the higher."""
    pre = assess(conn, mule_predictor, coercion_model, stage="pre_pause", **req)
    if pre["decision"] == "ALLOW":
        return {"pre": pre, "post": None, "final_decision": "ALLOW", "asked_questions": False}
    post = assess(conn, mule_predictor, coercion_model, stage="post_pause", answers=answers, **req)
    final = ORDER[max(LEVEL[pre["decision"]], LEVEL[post["decision"]])]
    return {"pre": pre, "post": post, "final_decision": final, "asked_questions": True,
            "answers_raised_decision": LEVEL[post["decision"]] > LEVEL[pre["decision"]]}


def save_assessment(conn, result: dict, scenario_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO risk_assessments (created_at, user_id, payee_vpa_masked, amount, stage, inputs_json, mule_json,"
        " coercion_json, behaviour_json, decision, explanation_json, language, latency_ms, scenario_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), result["user_id"], result["payee"]["masked"],
         result["amount"], result["stage"], db.dumps(result["inputs"]), db.dumps(result["mule"]),
         db.dumps({k: v for k, v in result["coercion"].items()}), db.dumps(result["behaviour"]),
         result["decision"], db.dumps(result["explanation"]), result["language"], result["latency_ms"], scenario_id))
    return cur.lastrowid
