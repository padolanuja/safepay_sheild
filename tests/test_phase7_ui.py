"""Phase 7: pages, translations, simulated alerts, dashboard summary, scenario preparation,
the simulator endpoint and the server-side 'answers never lower a decision' rule."""
import json
import re
import sqlite3
from pathlib import Path

import pytest

from safepay.alerts.trusted_contact import amount_range_key
from safepay.i18n import strings, t

ROOT = Path(__file__).resolve().parents[1]
CALM = {"on_voice_call": 0, "on_video_call": 0, "call_duration_min": 0, "caller_in_contacts": 0,
        "caller_is_international": 0, "screen_share_active": 0, "remote_access_app_installed_7d": 0,
        "session_duration_sec": 30, "hesitation_events": 0, "segmented_typing": 0, "app_switches": 0,
        "vpa_pasted": 0, "fd_or_loan_liquidated_7d": 0, "payee_in_contacts": 0, "hour_of_day": 10}
ANSWERS = {"ans_know_payee": "unsure", "ans_someone_guiding": "no", "ans_purpose": "govt_fine_verification",
           "checklist": []}


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    from app import create_app
    from safepay import seed
    path = tmp_path_factory.mktemp("ui") / "ui.db"
    seed.build(path)
    app = create_app(db_path=path)
    app.config["TESTING"] = True
    return app.test_client(), path


# ------------------------------------------------------------------- pages
@pytest.mark.parametrize("url,marker", [("/", "phone-screen"), ("/dashboard", "histTable"),
                                        ("/evaluation", "evalBody"), ("/settings", "addContact")])
def test_pages_render(ui, url, marker):
    c, _ = ui
    r = c.get(url)
    assert r.status_code == 200 and marker in r.get_data(as_text=True)
    assert "Simulated" in r.get_data(as_text=True)


def test_unknown_page_404(ui):
    assert ui[0].get("/nope").status_code == 404


def test_report_images_only(ui):
    c, _ = ui
    assert c.get("/reports/eval_comparison.png").status_code == 200
    for bad in ("/reports/evaluation.json", "/reports/..%2Fconfig.py", "/reports/x.png.json"):
        assert c.get(bad).status_code == 404


# ------------------------------------------------------------------- i18n
def test_all_languages_have_every_english_key():
    en = set(strings("en"))
    for lang in ("hi", "mr"):
        missing = en - set(strings(lang))
        assert not missing, f"{lang} missing {sorted(missing)[:10]}"


def test_every_reason_rule_and_feature_is_translated():
    from safepay.coercion.model import NUMERIC
    from config import MULE_FEATURES
    keys = set(strings("en"))
    for row in ("R1", "R2", "R3", "R4", "R5", "R6", "R6b", "R7"):
        assert f"reason.{row}" in keys
    rules = set(re.findall(r'_ind\(\s*"([a-z_]+)"', (ROOT / "safepay/coercion/rules.py").read_text()))
    rules |= {"guided_by_unknown_caller", "contradiction_guidance", "contradiction_know_payee"}
    assert {f"rule.{r}" for r in rules} <= keys, sorted({f"rule.{r}" for r in rules} - keys)
    assert {f"feat.{f}" for f in NUMERIC + MULE_FEATURES} <= keys


def test_frontend_uses_only_existing_keys():
    keys = set(strings("en"))
    used = set()
    for p in list((ROOT / "templates").glob("*.html")) + list((ROOT / "static/js").glob("*.js")):
        used |= set(re.findall(r'data-i18n(?:-ph)?="([a-z_.]+)"', p.read_text(encoding="utf-8")))
        used |= set(re.findall(r'SP\.t\("([a-z_]+\.[a-z_.]+)"', p.read_text(encoding="utf-8")))
    assert used and not (used - keys), sorted(used - keys)


def test_t_formats_params_and_falls_back():
    assert t("hi", "pay.history", n=5) == "इस खाते में 5 पुराने लेन-देन"
    assert t("mr", "no.such.key") == "no.such.key"


def test_i18n_endpoint(ui):
    c, _ = ui
    assert c.get("/api/i18n/mr").get_json()["q.know"] == "तुम्ही या व्यक्तीला ओळखता का?"
    assert c.get("/api/i18n/fr").status_code == 404


# -------------------------------------------------- two-stage flow, server-side
def test_answers_never_lower_the_decision_server_side(ui):
    c, _ = ui
    body = {"user_id": 1, "payee_vpa": "verify.desk.2291@demoupi", "amount": 95_000,
            "situation": {**CALM, "on_video_call": 1, "call_duration_min": 120}}
    pre = c.post("/api/assess", json=body).get_json()
    assert pre["decision"] == "HOLD"
    calm_answers = {"ans_know_payee": "yes", "ans_someone_guiding": "no", "ans_purpose": "family", "checklist": []}
    post = c.post("/api/assess", json={**body, "stage": "post_pause", "answers": calm_answers,
                                       "pre_assessment_id": pre["assessment_id"]}).get_json()
    assert post["final_decision"] == "HOLD"


def test_post_pause_requires_matching_pre_assessment(ui):
    c, _ = ui
    body = {"user_id": 4, "payee_vpa": "freshmart.grocery@demomerchant", "amount": 640, "situation": CALM,
            "stage": "post_pause", "answers": ANSWERS}
    assert c.post("/api/assess", json=body).status_code == 400
    other = c.post("/api/assess", json={"user_id": 2, "payee_vpa": "freshmart.grocery@demomerchant",
                                        "amount": 640, "situation": CALM}).get_json()["assessment_id"]
    assert c.post("/api/assess", json={**body, "pre_assessment_id": other}).status_code == 404


def test_scenario_1_why_data_for_the_ui(ui):
    """What the Why? panel needs for Scenario 1: mule not checked + decided on coercion alone."""
    c, _ = ui
    s = c.post("/api/scenarios/1/prepare").get_json()
    j = c.post("/api/assess", json={"user_id": s["user_id"], "payee_vpa": s["payee_vpa"], "amount": s["amount"],
                                    "situation": s["situation"]}).get_json()
    assert j["decision"] == "HOLD"
    assert j["mule"]["status"] == "insufficient_history" and j["mule"]["transactions"] == 2
    assert j["mule"]["min_history"] == 5 and "mule_probability" not in j["mule"]
    assert j["explanation"]["decided_on_coercion_only"] is True
    assert j["fusion"]["matrix_row"] == "R1" and j["response"]["cooling_off_minutes"] == 30
    ml = [f for f in j["explanation"]["factors"] if f["source"] == "model_prediction:coercion"]
    assert ml and all("value" in f for f in ml)


# ------------------------------------------------------------- scenarios & simulator
def test_prepare_inserts_priors_and_clears_old_ones(ui):
    c, path = ui
    for _ in range(2):
        s = c.post("/api/scenarios/6/prepare").get_json()
    n = sqlite3.connect(path).execute("SELECT COUNT(*) FROM user_transactions WHERE user_id = ? AND "
                                      "source = 'scenario_setup'", (s["user_id"],)).fetchone()[0]
    assert n == len(s["prior_payments"]) == 4
    assert c.post("/api/scenarios/99/prepare").status_code == 404


def test_simulator_new_payee_payments_feed_velocity(ui):
    c, _ = ui
    assert c.post("/api/simulator/new-payee-payments", json={"user_id": 3, "count": 3}).get_json()["inserted"] == 3
    j = c.post("/api/assess", json={"user_id": 3, "payee_vpa": "freshmart.grocery@demomerchant", "amount": 600,
                                    "situation": CALM}).get_json()
    assert j["inputs"]["new_payee_txns_24h"]["value"] == 3
    assert c.post("/api/simulator/new-payee-payments", json={"user_id": 3, "count": 9}).status_code == 400
    c.post("/api/simulator/new-payee-payments", json={"user_id": 3, "count": 0})


# --------------------------------------------------------------------- alerts
def test_alert_only_on_hold_with_consent_and_minimal_info(ui):
    c, path = ui
    hold = c.post("/api/assess", json={"user_id": 1, "payee_vpa": "kyc.update.cell@demoupi", "amount": 45_000,
                                       "situation": {**CALM, "screen_share_active": 1}}).get_json()
    assert hold["decision"] == "HOLD"
    contact = c.get("/api/contacts?user_id=1").get_json()["contacts"][0]
    r = c.post("/api/alerts/trusted-contact", json={"assessment_id": hold["assessment_id"], "contact_id": contact["id"]})
    j = r.get_json()
    assert r.status_code == 201 and j["simulated"] and j["sent"] is False
    msg = j["message_preview"]
    assert "45,000" not in msg and "45000" not in msg and "kyc.update" not in msg   # range only, no payee id
    assert t("hi", "alert.range_10k_50k") in msg                                  # Ramesh's language is Hindi
    log = c.get("/api/alerts?user_id=1").get_json()["alerts"]
    assert log and log[0]["simulated"] == 1


def test_alert_refused_for_allow_and_without_consent(ui):
    c, path = ui
    allow = c.post("/api/assess", json={"user_id": 4, "payee_vpa": "freshmart.grocery@demomerchant", "amount": 640,
                                        "situation": CALM}).get_json()
    kamala_contact = c.get("/api/contacts?user_id=4").get_json()["contacts"][0]
    r = c.post("/api/alerts/trusted-contact", json={"assessment_id": allow["assessment_id"],
                                                    "contact_id": kamala_contact["id"]})
    assert r.status_code == 409                                   # genuine payments never trigger alerts
    hold = c.post("/api/assess", json={"user_id": 2, "payee_vpa": "kyc.update.cell@demoupi", "amount": 20_000,
                                       "situation": {**CALM, "screen_share_active": 1}}).get_json()
    priya_contact = c.get("/api/contacts?user_id=2").get_json()["contacts"][0]
    assert priya_contact["consent_given"] == 0
    r = c.post("/api/alerts/trusted-contact", json={"assessment_id": hold["assessment_id"],
                                                    "contact_id": priya_contact["id"]})
    assert r.status_code == 409 and "consent" in r.get_json()["message"]
    r = c.post("/api/alerts/trusted-contact", json={"assessment_id": hold["assessment_id"],
                                                    "contact_id": kamala_contact["id"]})
    assert r.status_code == 404                                   # another payer's contact


@pytest.mark.parametrize("amount,key", [(500, "range_below_10k"), (10_000, "range_10k_50k"),
                                        (49_999, "range_10k_50k"), (95_000, "range_above_50k")])
def test_amount_ranges(amount, key):
    assert amount_range_key(amount) == key


# ------------------------------------------------------------------ dashboard
def test_dashboard_summary(ui):
    c, _ = ui
    j = c.get("/api/dashboard/summary").get_json()
    assert j["assessments"] >= 1 and set(j["by_decision"]) == {"ALLOW", "WARN", "HOLD"}
    assert sum(j["by_decision"].values()) == j["assessments"]
    assert any(i["id"] == "screen_sharing_active" for i in j["top_indicators"])
    only = c.get("/api/dashboard/summary?user_id=4").get_json()
    assert only["assessments"] <= j["assessments"]
