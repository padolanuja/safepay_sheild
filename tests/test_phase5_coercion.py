"""Phase 5: coercion rules, ML model, answer check, profiling, splitting, fusion and scenarios."""
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import config
from safepay import db
from safepay.assess import assess, run_flow
from safepay.behaviour import profile as prof
from safepay.behaviour.splitting import detect
from safepay.coercion.answer_check import check_answers
from safepay.coercion.model import PAYEE_AMOUNT_FEATURES, CoercionModel, encode
from safepay.coercion.rules import evaluate_rules, has_severe, rule_points
from safepay.coercion.scoring import combine
from safepay.fusion.engine import decide
from safepay.mule.loader import load_mule_model
from safepay.mule.predictor import MulePredictor
from safepay.scenarios import run_scenario
from safepay.validation import ValidationError

CALM = {"on_voice_call": 0, "on_video_call": 0, "call_duration_min": 0, "caller_in_contacts": 0,
        "caller_is_international": 0, "screen_share_active": 0, "remote_access_app_installed_7d": 0,
        "session_duration_sec": 40, "hesitation_events": 0, "segmented_typing": 0, "app_switches": 0,
        "vpa_pasted": 0, "fd_or_loan_liquidated_7d": 0, "payee_in_contacts": 0, "hour_of_day": 14}


@pytest.fixture(scope="module")
def models():
    return MulePredictor(load_mule_model()), CoercionModel()


@pytest.fixture()
def conn(seeded_db):
    c = db.connect(seeded_db[0])
    yield c
    c.close()


def ctx(**kw):
    base = {**CALM, "stage": "pre_pause", "payee_is_new": 0, "amount_to_avg_ratio": 1.0,
            "amount_pct_of_balance": 1.0, "new_payee_txns_24h": 0, "amount_sent_new_payees_24h": 0}
    base.update(kw)
    return base


def ids(indicators, counted_only=True):
    return {i["id"] for i in indicators if i["counted"] or not counted_only}


# ------------------------------------------------------------------------------ rules
@pytest.mark.parametrize("kw,expected,severe", [
    (dict(on_video_call=1, call_duration_min=90), "digital_arrest_pattern", True),
    (dict(screen_share_active=1), "screen_sharing_active", True),
    (dict(remote_access_app_installed_7d=1), "remote_app_recent", True),
    (dict(on_voice_call=1, call_duration_min=5), "unknown_caller_during_payment", False),
    (dict(on_voice_call=1, caller_is_international=1, call_duration_min=3), "international_caller", False),
    (dict(hour_of_day=1), "night_payment", False),
    (dict(hesitation_events=6), "high_hesitation", False),
    (dict(segmented_typing=1), "segmented_typing", False),
    (dict(new_payee_txns_24h=3), "repeated_new_payee_payments", False),
    (dict(amount_sent_new_payees_24h=80_000), "high_new_payee_volume", False),
    (dict(fd_or_loan_liquidated_7d=1), "savings_liquidated", False),
])
def test_each_rule_fires(kw, expected, severe):
    ind = evaluate_rules(ctx(**kw))
    assert expected in ids(ind)
    assert has_severe(ind) is severe


@pytest.mark.parametrize("item,expected", [("under_arrest", "digital_arrest_pattern"),
                                           ("told_not_to_tell", "secrecy_request"),
                                           ("asked_to_install_app", "asked_to_install_app")])
def test_checklist_items_are_severe_post_pause_only(item, expected):
    answers = {"ans_know_payee": "yes", "ans_someone_guiding": "no", "ans_purpose": "other"}
    post = evaluate_rules(ctx(stage="post_pause", checklist=[item], **answers))
    assert expected in ids(post) and has_severe(post)
    assert not has_severe(evaluate_rules(ctx(stage="pre_pause", checklist=[item])))


def test_guided_answer_severity_depends_on_the_call():
    a = {"stage": "post_pause", "ans_know_payee": "yes", "ans_someone_guiding": "yes", "ans_purpose": "family"}
    assert has_severe(evaluate_rules(ctx(on_voice_call=1, call_duration_min=8, **a)))          # unknown caller
    assert not has_severe(evaluate_rules(ctx(on_voice_call=1, caller_in_contacts=1, call_duration_min=8, **a)))
    assert not has_severe(evaluate_rules(ctx(**a)))                                           # no call


# -------------------------------------------- key check: unknown payee / high amount alone
def test_unknown_payee_and_high_amount_alone_never_count_in_rules():
    ind = evaluate_rules(ctx(payee_is_new=1, amount_to_avg_ratio=80, amount_pct_of_balance=90, vpa_pasted=1,
                             app_switches=6))
    assert ind, "context indicators should still be reported"
    assert all(not i["counted"] for i in ind)
    assert rule_points(ind) == 0 and not has_severe(ind)


def test_context_counts_once_pressure_is_present():
    ind = evaluate_rules(ctx(payee_is_new=1, amount_to_avg_ratio=80, on_voice_call=1, call_duration_min=20))
    assert {"new_payee_large_amount", "unknown_call_and_new_payee"} <= ids(ind)


@pytest.mark.parametrize("amount", [20_000, 60_000, 100_000])
def test_unknown_payee_high_amount_alone_pipeline(conn, models, amount):
    """Full pipeline: brand-new payee, huge amount, no pressure signals -> coercion low, never HOLD."""
    mp, cm = models
    for stage, answers in (("pre_pause", None),
                           ("post_pause", {"ans_know_payee": "yes", "ans_someone_guiding": "no",
                                           "ans_purpose": "bills_shopping", "checklist": []})):
        r = assess(conn, mp, cm, user_id=2, payee_vpa="rohan.friend@demobank", amount=amount,
                   situation=CALM, stage=stage, answers=answers)
        assert r["coercion"]["band"] == "low", (stage, r["coercion"])
        assert r["coercion"]["payee_amount_gate"] == "applied"
        assert r["decision"] != "HOLD"
        assert r["fusion"]["matrix_row"] == "R6b"     # unusual-payment check, not coercion


# -------------------------------------------------------------------- answer check
def test_guidance_contradiction_is_strong_and_gentle():
    c = ctx(stage="post_pause", on_video_call=1, call_duration_min=40, ans_someone_guiding="no",
            ans_know_payee="yes", ans_purpose="other")
    con = check_answers(c)
    assert [x["id"] for x in con] == ["guidance"] and con[0]["strength"] == "strong"
    assert "it is okay to stop" in con[0]["gentle_message"]


def test_know_payee_contradiction_is_soft():
    c = ctx(stage="post_pause", payee_is_new=1, vpa_pasted=1, ans_someone_guiding="no",
            ans_know_payee="yes", ans_purpose="family")
    con = check_answers(c)
    assert [x["id"] for x in con] == ["know_payee"] and con[0]["strength"] == "soft"


def test_no_contradictions_before_the_pause():
    assert check_answers(ctx(on_video_call=1, call_duration_min=40, ans_someone_guiding="no")) == []


# ------------------------------------------------------------------------- scoring
def test_severe_forces_high_band():
    s = combine(points=0, ml_prob=0.01, severe=True, cutoffs={"medium": 0.2, "high": 0.7}, cap=30)
    assert s["band"] == "high" and s["forced_high_by_severe"]


def test_score_is_max_of_rules_and_ml():
    s = combine(points=15, ml_prob=0.2, severe=False, cutoffs={"medium": 0.2, "high": 0.7}, cap=30)
    assert s["coercion_score"] == 0.5 and s["score_driver"] == "rules"


# ---------------------------------------------------------------------- ML model
def test_model_card_payer_side_only():
    card = json.loads(config.COERCION_MODEL_CARD.read_text())
    for stage, info in card["stages"].items():
        feats = set(info["features"])
        assert not feats & {"payee_vpa_age_days", "payee_prior_complaints"}, stage
        assert not feats & {"txn_id", "scam_type", "sample_group", "hard_negative_reason", "label_noisy"}
        assert any(f.startswith("ans_") for f in feats) == (stage == "post_pause")
    assert "feasibility only" in card["caveat"]
    assert set(card["cutoffs"]) == {"pre_pause", "post_pause"}
    assert all(c["medium"] <= c["high"] for c in card["cutoffs"].values())


def test_training_report_has_breakdowns():
    rep = json.loads((config.REPORTS_DIR / "coercion_training.json").read_text())
    blk = rep["test_set"]["post_pause"]["combined"]["hold"]
    assert "digital_arrest" in blk["recall_by_scam_type"]
    assert "large_new_payee_genuine" in blk["fp_rate_by_hard_negative"]


@pytest.mark.parametrize("stage", ["pre_pause", "post_pause"])
def test_coercion_shap_is_additive(models, stage):
    _, cm = models
    df = pd.read_csv(config.COERCION_CSV).head(20)
    b, ex = cm.models[stage], cm.explainers[stage]
    X = encode(df, stage)[b["features"]]
    p = b["model"].predict_proba(X)[:, 1]
    total = ex.base_value + ex.contributions(X).sum(axis=1)
    out = total if b["kind"] == "random_forest" else 1 / (1 + np.exp(-total))   # RF: prob, XGB: log-odds
    assert np.allclose(out, p, atol=1e-4)


def test_missing_ml_inputs_are_not_guessed(models):
    _, cm = models
    r = cm.predict({"stage": "pre_pause", "amount_inr": 500}, "pre_pause")
    assert r["status"] == "unavailable" and "hour_of_day" in r["missing"]


@pytest.mark.parametrize("stage", ["pre_pause", "post_pause"])
def test_payee_amount_share_is_a_fraction(models, stage):
    _, cm = models
    for row in pd.read_csv(config.COERCION_CSV).head(50).to_dict("records"):
        assert 0.0 <= cm.predict(row, stage)["payee_amount_share_of_risk"] <= 1.0


def test_gate_not_applied_when_pressure_present(conn, models):
    mp, cm = models
    r = assess(conn, mp, cm, user_id=2, payee_vpa="rohan.friend@demobank", amount=60_000,
               situation={**CALM, "on_voice_call": 1, "call_duration_min": 25})
    assert r["coercion"]["pressure_present"] and r["coercion"]["payee_amount_gate"] == "not_applied"
    assert r["coercion"]["ml_used"]


# ------------------------------------------------------------ profile & splitting
def _hist(rows, now):
    return pd.DataFrame([{"payee_vpa": v, "payee_type": "P2P", "amount": a, "ts": now - timedelta(minutes=m),
                          "source": "t"} for v, a, m in rows])


def test_velocity_counts_new_payees_in_24h():
    now = datetime(2026, 11, 20, 12)
    h = _hist([("old@x", 100, 60 * 24 * 30), ("old@x", 100, 30), ("new@x", 5000, 60), ("new2@x", 2000, 120),
               ("new@x", 900, 60 * 30)], now)
    v = prof.velocity(h, now)
    assert v == {"new_payee_txns_24h": 2, "amount_sent_new_payees_24h": 7000.0}
    assert prof.payee_is_new(h, "new@x", now) and not prof.payee_is_new(h, "old@x", now)


def test_splitting_near_limit_and_rapid_repeat():
    now = datetime(2026, 11, 20, 12)
    h = _hist([("s@x", 19_999, m) for m in (20, 15, 10, 5)], now)
    r = detect(h, "s@x", 19_999, now, 500, {"s@x"})
    assert {p["id"] for p in r["patterns"]} == {"near_limit_split", "rapid_repeat"}
    assert r["transactions"] == 5 and r["total_amount"] == 99_995


def test_splitting_escalation():
    now = datetime(2026, 11, 20, 12)
    h = _hist([("j@x", 200, 180), ("j@x", 500, 120), ("j@x", 1500, 60)], now)
    assert [p["id"] for p in detect(h, "j@x", 3000, now, 500, set())["patterns"]] == ["escalation"]


def test_splitting_quiet_for_normal_spending():
    now = datetime(2026, 11, 20, 12)
    h = _hist([("g@x", 600, 60 * 24 * 3), ("m@x", 55, 60 * 5)], now)
    assert not detect(h, "g@x", 640, now, 500, set())["detected"]


# ---------------------------------------------------------------- fusion matrix
MULE_LOW = {"status": "ok", "band": "low"}
MULE_HIGH = {"status": "ok", "band": "high"}
MULE_ELEV = {"status": "ok", "band": "elevated"}
MULE_NONE = {"status": "insufficient_history"}
NO_SPLIT = {"detected": False}
NO_TRUST = {"trusted_beneficiary": False, "planned_payment_match": False}
TRUSTED = {"trusted_beneficiary": True, "planned_payment_match": False}


def C(band="low", severe=False, contra=False):
    return {"band": band, "severe": severe, "strong_contradiction": contra}


@pytest.mark.parametrize("mule,coer,split,unusual,stage,row,decision", [
    (MULE_LOW, C("low", severe=True), NO_SPLIT, None, "post_pause", "R1", "HOLD"),
    (MULE_LOW, C("high"), NO_SPLIT, None, "post_pause", "R2", "HOLD"),
    (MULE_NONE, C("high"), NO_SPLIT, None, "pre_pause", "R2", "HOLD"),
    (MULE_HIGH, C("medium"), NO_SPLIT, None, "post_pause", "R3", "HOLD"),
    (MULE_HIGH, C("medium"), NO_SPLIT, None, "pre_pause", "R3", "WARN"),   # questions first
    (MULE_LOW, C("medium", contra=True), NO_SPLIT, None, "post_pause", "R4", "HOLD"),
    (MULE_LOW, C("medium"), NO_SPLIT, None, "post_pause", "R5", "WARN"),
    (MULE_ELEV, C("low"), NO_SPLIT, None, "post_pause", "R6", "WARN"),
    (MULE_HIGH, C("low"), NO_SPLIT, None, "post_pause", "R6", "WARN"),
    (MULE_LOW, C("low"), NO_SPLIT, {"detected": True}, "post_pause", "R6b", "WARN"),
    (MULE_LOW, C("low"), NO_SPLIT, None, "post_pause", "R7", "ALLOW"),
    (MULE_LOW, C("low"), {"detected": True}, None, "post_pause", "R7", "WARN"),   # A1
])
def test_fusion_matrix_rows(mule, coer, split, unusual, stage, row, decision):
    r = decide(mule, coer, split, NO_TRUST, unusual, stage)
    assert (r["matrix_row"], r["decision"]) == (row, decision)


def test_insufficient_history_disables_mule_rows():
    r = decide(MULE_NONE, C("medium"), NO_SPLIT, NO_TRUST)
    assert r["matrix_row"] == "R5" and r["coercion_only"] and not r["mule_considered"]
    assert decide(MULE_NONE, C("low"), NO_SPLIT, NO_TRUST)["decision"] == "ALLOW"


def test_trust_lowers_one_level():
    assert decide(MULE_LOW, C("medium"), NO_SPLIT, TRUSTED)["decision"] == "ALLOW"
    assert decide(MULE_LOW, C("high"), NO_SPLIT, TRUSTED)["decision"] == "WARN"
    planned = {"trusted_beneficiary": False, "planned_payment_match": True}
    assert decide(MULE_ELEV, C("low"), NO_SPLIT, planned)["decision"] == "ALLOW"


@pytest.mark.parametrize("trust", [TRUSTED, {"trusted_beneficiary": False, "planned_payment_match": True},
                                   {"trusted_beneficiary": True, "planned_payment_match": True}])
def test_trust_never_overrides_severe(trust):
    r = decide(MULE_LOW, C("high", severe=True), NO_SPLIT, trust)
    assert r["decision"] == "HOLD" and not r["trust_applied"]
    assert any("NOT applied" in s.get("note", "") for s in r["trail"])


# ------------------------------------------------------------------- pipeline
def test_assess_validates_inputs(conn, models):
    mp, cm = models
    base = dict(user_id=1, payee_vpa="freshmart.grocery@demomerchant", amount=500, situation=CALM)
    with pytest.raises(ValidationError):
        assess(conn, mp, cm, **{**base, "amount": 200_000})
    with pytest.raises(ValidationError):
        assess(conn, mp, cm, **{**base, "situation": {k: v for k, v in CALM.items() if k != "vpa_pasted"}})
    with pytest.raises(ValidationError):
        assess(conn, mp, cm, **{**base, "situation": {**CALM, "screen_share_active": "yes"}})
    with pytest.raises(ValidationError):
        assess(conn, mp, cm, **base, stage="post_pause", answers={"ans_know_payee": "maybe"})


def test_every_input_is_source_tagged(conn, models):
    mp, cm = models
    r = assess(conn, mp, cm, user_id=1, payee_vpa="freshmart.grocery@demomerchant", amount=500, situation=CALM)
    sources = {v["source"] for v in r["inputs"].values()}
    assert sources <= {"user_entered", "db_derived", "simulated_device_signal", "clock", "user_answer"}
    assert r["inputs"]["screen_share_active"]["source"] == "simulated_device_signal"
    assert r["simulated"] is True and "no real money moved" in r["explanation"]["disclaimer"]


# ------------------------------------------------------------------- scenarios
@pytest.fixture(scope="module")
def scenario_results(seeded_db, models):
    mp, cm = models
    c = db.connect(seeded_db[0])
    now = datetime.now().replace(microsecond=0)
    out = {sid: run_scenario(c, mp, cm, sid, now) for sid in range(1, 14)}
    c.close()
    return out


@pytest.mark.parametrize("sid", range(1, 14))
def test_scenarios_match_expected(scenario_results, sid):
    r = scenario_results[sid]
    assert r["passed"], f"scenario {sid}: expected {r['expected']}, got {r['actual']}"


@pytest.mark.parametrize("purpose", ["job_task_fee", "other"])
def test_scenario_4_at_least_warn_without_the_artefact(seeded_db, models, purpose):
    """HOLD was accepted for scenario 4 because of the job_task_fee artefact; it must stay at least WARN
    with a neutral purpose, and the escalation (splitting) floor must hold on its own."""
    mp, cm = models
    c = db.connect(seeded_db[0])
    orig = c.execute("SELECT answers_json FROM scenarios WHERE id = 4").fetchone()[0]
    try:
        c.execute("UPDATE scenarios SET answers_json = ? WHERE id = 4",
                  (json.dumps({**json.loads(orig), "ans_purpose": purpose}),))
        r = run_scenario(c, mp, cm, 4)
    finally:
        c.execute("UPDATE scenarios SET answers_json = ? WHERE id = 4", (orig,))
        c.commit()
        c.close()
    assert r["actual"] in ("WARN", "HOLD")
    for x in (r["flow"]["pre"], r["flow"]["post"]):
        assert "escalation" in [p["id"] for p in x["behaviour"]["splitting"]["patterns"]]
        floor = decide(x["mule"], C("low"), x["behaviour"]["splitting"], x["trust"], None, "post_pause")
        assert floor["decision"] == "WARN" and floor["trail"][-1]["step"] == "A1"


def test_scenario_1_hold_on_coercion_alone(scenario_results):
    f = scenario_results[1]["flow"]
    assert scenario_results[1]["actual"] == "HOLD"
    for x in (f["pre"], f["post"]):
        assert x["mule"]["status"] == "insufficient_history" and x["mule"]["model_called"] is False
        assert x["explanation"]["decided_on_coercion_only"] is True
        assert "payment situation only" in " ".join(x["explanation"]["notes"])
    assert f["pre"]["decision"] == "HOLD"                     # caught before any question
    assert "guidance" in [c["id"] for c in f["post"]["coercion"]["contradictions"]]


def test_scenario_7_hospital_warn_not_hold(scenario_results):
    f = scenario_results[7]["flow"]
    assert scenario_results[7]["actual"] == "WARN"
    for x in (f["pre"], f["post"]):
        assert x["decision"] == "WARN" and x["coercion"]["band"] == "low"


def test_scenario_13_trust_does_not_override_screen_share(scenario_results):
    pre = scenario_results[13]["flow"]["pre"]
    assert pre["trust"]["trusted_beneficiary"] and pre["decision"] == "HOLD"
    assert pre["fusion"]["matrix_row"] == "R1" and not pre["fusion"]["trust_applied"]


def test_answers_never_lower_a_decision(scenario_results):
    for r in scenario_results.values():
        f = r["flow"]
        if f["post"]:
            order = ["ALLOW", "WARN", "HOLD"]
            assert order.index(f["final_decision"]) >= order.index(f["pre"]["decision"])
