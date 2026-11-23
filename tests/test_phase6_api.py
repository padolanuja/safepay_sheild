"""Phase 6: every API endpoint, input validation, JSON errors and privacy of the logs."""
import json
import sqlite3

import pytest

from safepay.coercion.model import CoercionModel

CALM = {"on_voice_call": 0, "on_video_call": 0, "call_duration_min": 0, "caller_in_contacts": 0,
        "caller_is_international": 0, "screen_share_active": 0, "remote_access_app_installed_7d": 0,
        "session_duration_sec": 30, "hesitation_events": 0, "segmented_typing": 0, "app_switches": 0,
        "vpa_pasted": 0, "fd_or_loan_liquidated_7d": 0, "payee_in_contacts": 0, "hour_of_day": 10}
GROCERY = "freshmart.grocery@demomerchant"


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    """A fresh database for this module, because these tests write to it."""
    from app import create_app
    from safepay import seed
    path = tmp_path_factory.mktemp("api") / "api.db"
    seed.build(path)
    app = create_app(db_path=path)
    app.config["TESTING"] = True
    return app.test_client(), path


def _assess(c, **kw):
    body = {"user_id": 4, "payee_vpa": GROCERY, "amount": 640, "situation": CALM, **kw}
    return c.post("/api/assess", json=body)


# ------------------------------------------------------------------- /api/assess
def test_assess_ok_and_saved_masked(api):
    c, path = api
    r = _assess(c)
    j = r.get_json()
    assert r.status_code == 200 and j["decision"] == "ALLOW" and j["simulated"] is True
    assert isinstance(j["assessment_id"], int)
    row = sqlite3.connect(path).execute("SELECT payee_vpa_masked, decision FROM risk_assessments WHERE id = ?",
                                        (j["assessment_id"],)).fetchone()
    assert row == ("fr****@demomerchant", "ALLOW")


def test_raw_vpas_never_in_assessment_log(api):
    c, path = api
    _assess(c, payee_vpa="verify.desk.2291@demoupi", amount=50_000)
    conn = sqlite3.connect(path)
    for (text,) in conn.execute("SELECT payee_vpa_masked || explanation_json FROM risk_assessments"):
        assert "freshmart.grocery@" not in text and "verify.desk.2291@" not in text


@pytest.mark.parametrize("patch,code", [
    ({"situation": None}, 400),
    ({"situation": {**CALM, "screen_share_active": "yes"}}, 400),
    ({"situation": {k: v for k, v in CALM.items() if k != "app_switches"}}, 400),
    ({"amount": 0}, 400), ({"amount": 100_001}, 400), ({"amount": "500"}, 400),
    ({"payee_vpa": "bad"}, 400), ({"user_id": "1"}, 400), ({"user_id": 999}, 404),
    ({"stage": "later"}, 400), ({"stage": "post_pause"}, 400),          # post_pause needs answers
    ({"payee_vpa": "stranger@demoupi"}, 400),                          # not in directory, no payee_type
    ({"surprise": 1}, 400),
])
def test_assess_validation(api, patch, code):
    c, _ = api
    r = _assess(c, **patch)
    assert r.status_code == code and r.get_json()["status"] == code


def test_assess_unknown_payee_with_type_is_insufficient_history(api):
    c, _ = api
    j = _assess(c, payee_vpa="stranger@demoupi", payee_type="P2P", amount=300).get_json()
    assert j["mule"]["status"] == "insufficient_history"
    assert j["explanation"]["decided_on_coercion_only"] is True


def test_assess_post_pause_with_answers(api):
    c, _ = api
    pre = _assess(c).get_json()
    j = _assess(c, stage="post_pause", pre_assessment_id=pre["assessment_id"],
                answers={"ans_know_payee": "yes", "ans_someone_guiding": "no",
                         "ans_purpose": "bills_shopping", "checklist": []}).get_json()
    assert j["stage"] == "post_pause" and j["inputs"]["ans_purpose"]["source"] == "user_answer"
    assert j["pre_decision"] == pre["decision"] and j["final_decision"] == j["decision"]


def test_assess_503_when_coercion_model_missing(api):
    c, path = api
    from app import create_app
    app = create_app(db_path=path)
    broken = CoercionModel.__new__(CoercionModel)
    broken.models, broken.explainers, broken.error = {}, {}, "coercion model not available: test"
    app.config["COERCION_MODEL"] = broken
    r = app.test_client().post("/api/assess", json={"user_id": 4, "payee_vpa": GROCERY, "amount": 640,
                                                    "situation": CALM})
    assert r.status_code == 503 and "not available" in r.get_json()["message"]
    assert app.test_client().get("/api/health").get_json()["status"] == "degraded"


def test_coercion_assess_returns_coercion_only_and_saves_nothing(api):
    c, path = api
    before = sqlite3.connect(path).execute("SELECT COUNT(*) FROM risk_assessments").fetchone()[0]
    j = c.post("/api/coercion/assess", json={"user_id": 2, "payee_vpa": "rohan.friend@demobank",
                                             "amount": 60_000, "situation": CALM}).get_json()
    assert set(j) >= {"coercion", "behaviour", "inputs"} and "decision" not in j
    assert j["coercion"]["band"] == "low"
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM risk_assessments").fetchone()[0] == before


# ------------------------------------------------------------- /api/payments/confirm
def test_confirm_proceed_and_no_double_confirm(api):
    c, _ = api
    aid = _assess(c).get_json()["assessment_id"]
    r = c.post("/api/payments/confirm", json={"assessment_id": aid, "action": "proceed", "payee_vpa": GROCERY})
    assert r.status_code == 200 and r.get_json()["transaction_id"] and r.get_json()["simulated"]
    r2 = c.post("/api/payments/confirm", json={"assessment_id": aid, "action": "cancel", "payee_vpa": GROCERY})
    assert r2.status_code == 409


def test_confirm_hold_blocks_proceed_but_allows_cancel(api):
    c, _ = api
    j = _assess(c, payee_vpa="kyc.update.cell@demoupi", amount=30_000,
                situation={**CALM, "screen_share_active": 1}).get_json()
    assert j["decision"] == "HOLD"
    body = {"assessment_id": j["assessment_id"], "payee_vpa": "kyc.update.cell@demoupi"}
    r = c.post("/api/payments/confirm", json={**body, "action": "proceed"})
    assert r.status_code == 409 and "cooling-off" in r.get_json()["message"]
    assert c.post("/api/payments/confirm", json={**body, "action": "cancel"}).get_json()["outcome"] == "cancelled"


@pytest.mark.parametrize("body,code", [
    ({"assessment_id": 999999, "action": "cancel", "payee_vpa": GROCERY}, 404),
    ({"assessment_id": 1, "action": "maybe", "payee_vpa": GROCERY}, 400),
    ({"assessment_id": "1", "action": "cancel", "payee_vpa": GROCERY}, 400),
])
def test_confirm_errors(api, body, code):
    c, _ = api
    assert c.post("/api/payments/confirm", json=body).status_code == code


def test_confirm_rejects_mismatched_payee(api):
    c, _ = api
    aid = _assess(c).get_json()["assessment_id"]
    r = c.post("/api/payments/confirm", json={"assessment_id": aid, "action": "proceed",
                                              "payee_vpa": "someone.else@demobank"})
    assert r.status_code == 400


# ------------------------------------------------------------------------ history
def test_history_and_filters(api):
    c, _ = api
    j = c.get("/api/history/4").get_json()
    assert j["transactions"] and j["assessments"] and j["simulated"]
    holds = c.get("/api/history/4?decision=HOLD").get_json()["assessments"]
    assert holds and all(a["decision"] == "HOLD" for a in holds)
    assert c.get("/api/history/4?decision=MAYBE").status_code == 400
    assert c.get("/api/history/4?limit=0").status_code == 400
    assert c.get("/api/history/999").status_code == 404


# --------------------------------------------------------------------------- trust
def test_contacts_crud_and_masking(api):
    c, _ = api
    r = c.post("/api/contacts", json={"user_id": 2, "name": "Asha", "relation": "sister",
                                      "phone": "+91 98765 43210", "consent_given": True})
    j = r.get_json()
    assert r.status_code == 201 and j["phone_masked"] == "98xxxxxx10" and j["consent_timestamp"]
    off = c.post(f"/api/contacts/{j['id']}/consent", json={"consent_given": False}).get_json()
    assert off["consent_given"] == 0 and off["consent_timestamp"] is None
    assert any(x["id"] == j["id"] for x in c.get("/api/contacts?user_id=2").get_json()["contacts"])
    assert c.delete(f"/api/contacts/{j['id']}").get_json()["deleted"] is True
    assert c.delete(f"/api/contacts/{j['id']}").status_code == 404
    assert c.post("/api/contacts", json={"user_id": 2, "name": "X", "relation": "y", "phone": "123"}).status_code == 400
    assert c.post("/api/contacts", json={"user_id": 99, "name": "X", "relation": "y",
                                         "phone": "9876543210"}).status_code == 404
    assert c.get("/api/contacts").status_code == 400


def test_beneficiaries_and_planned(api):
    c, _ = api
    r = c.post("/api/trusted-beneficiaries", json={"user_id": 4, "payee_vpa": GROCERY, "label": "Grocer"})
    assert r.status_code == 201
    assert c.post("/api/trusted-beneficiaries", json={"user_id": 4, "payee_vpa": GROCERY}).status_code == 409
    assert len(c.get("/api/trusted-beneficiaries?user_id=4").get_json()["beneficiaries"]) == 1
    p = c.post("/api/planned-payments", json={"user_id": 4, "payee_vpa": GROCERY, "amount": 2000,
                                              "day_of_month": 1, "purpose": "Monthly groceries"})
    assert p.status_code == 201
    assert c.post("/api/planned-payments", json={"user_id": 4, "payee_vpa": GROCERY, "amount": 2000,
                                                 "day_of_month": 40}).status_code == 400


# ------------------------------------------------------------------------ feedback
def test_feedback_queue_and_verify(api):
    c, _ = api
    aid = _assess(c).get_json()["assessment_id"]
    r = c.post("/api/feedback", json={"assessment_id": aid, "outcome": "was_genuine", "note": "my grocer"})
    j = r.get_json()
    assert r.status_code == 201 and j["verified"] is False and "never auto-retrained" in j["note"]
    assert any(f["id"] == j["id"] for f in c.get("/api/feedback?verified=0").get_json()["feedback"])
    assert c.post(f"/api/feedback/{j['id']}/verify").get_json()["verified"] is True
    assert c.post("/api/feedback", json={"assessment_id": aid, "outcome": "unsure"}).status_code == 400
    assert c.post("/api/feedback", json={"assessment_id": 999999, "outcome": "rescued"}).status_code == 404


# ----------------------------------------------------------------------- scenarios
def test_scenarios_list_and_run(api):
    c, path = api
    lst = c.get("/api/scenarios").get_json()["scenarios"]
    assert len(lst) == 13 and lst[0]["expected_decisions"] == ["HOLD"] and lst[0]["user_name"] == "Ramesh Kulkarni"
    r = c.post("/api/scenarios/1/run").get_json()
    assert r["actual"] == "HOLD" and r["passed"]
    n = sqlite3.connect(path).execute("SELECT COUNT(*) FROM risk_assessments WHERE scenario_id = 1").fetchone()[0]
    assert n == 2      # pre-pause and post-pause both saved
    assert c.post("/api/scenarios/99/run").status_code == 404


# ------------------------------------------------------------------------ metrics
def test_metrics_reads_reports_only(api):
    c, _ = api
    j = c.get("/api/metrics").get_json()
    assert "feasibility only" in j["banner"]
    assert j["reports"]["mule_rbih"]["caveat"].startswith("may be optimistic")
    for key in ("system_evaluation", "scenarios", "coercion_training"):
        rep = j["reports"][key]
        assert rep.get("status") == "not_generated" or "generated_at" in rep or "test_set" in rep
