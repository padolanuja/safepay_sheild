"""Phase 8: Blind Test Lab (random held-out cases) and the what-if counterfactual endpoint.

These exist to answer "the demo only shows values you picked": the lab scores rows the models
never trained on, and what-if re-runs the real pipeline. The tests pin the properties that make
those claims true.
"""
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SIT = {"on_voice_call": 1, "on_video_call": 0, "call_duration_min": 35, "caller_in_contacts": 0,
       "caller_is_international": 0, "screen_share_active": 0, "remote_access_app_installed_7d": 0,
       "session_duration_sec": 120, "hesitation_events": 4, "segmented_typing": 1, "app_switches": 2,
       "vpa_pasted": 1, "fd_or_loan_liquidated_7d": 0, "payee_in_contacts": 0, "hour_of_day": 14}


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    from app import create_app
    from safepay import seed
    path = tmp_path_factory.mktemp("lab") / "lab.db"
    seed.build(path)
    app = create_app(db_path=path)
    app.config["TESTING"] = True
    return app.test_client()


def _strict(resp):
    """Browsers reject NaN/Infinity in JSON, so the API must never emit them."""
    return json.loads(resp.get_data(as_text=True), parse_constant=lambda c: pytest.fail(f"non-JSON constant {c}"))


def test_lab_page_renders(lab):
    r = lab.get("/lab")
    assert r.status_code == 200 and "Blind Test Lab" in r.get_data(as_text=True)


def test_random_rows_are_held_out_and_truth_is_returned(lab):
    out = _strict(lab.get("/api/lab/random?n=40&seed=11"))
    assert out["rows_in_training_set"] == 0
    assert out["n"] == 40 and out["test_pool_size"] == 6000
    r = out["results"][0]
    assert r["final_decision"] in ("ALLOW", "WARN", "HOLD")
    assert set(r["truth"]) >= {"label_coerced", "scam_type", "should_intervene", "payee_profile"}
    m = out["metrics"]
    c = m["confusion"]
    assert c["tp"] + c["fp"] + c["fn"] + c["tn"] == 40
    assert m["baseline"]["name"].startswith("Rule")


def test_seed_replays_exactly_and_unseeded_draws_differ(lab):
    a = _strict(lab.get("/api/lab/random?n=30&seed=5"))
    b = _strict(lab.get("/api/lab/random?n=30&seed=5"))
    assert [x["txn_id"] for x in a["results"]] == [x["txn_id"] for x in b["results"]]
    assert [x["final_decision"] for x in a["results"]] == [x["final_decision"] for x in b["results"]]
    c = _strict(lab.get("/api/lab/random?n=30"))
    assert c["seed"] != 5 and [x["txn_id"] for x in c["results"]] != [x["txn_id"] for x in a["results"]]


def test_balanced_draw_is_half_coerced(lab):
    out = _strict(lab.get("/api/lab/random?n=20&balanced=1&seed=3"))
    assert sum(r["truth"]["label_coerced"] for r in out["results"]) == 10


def test_no_history_rows_serialise_without_nan(lab):
    out = _strict(lab.get("/api/lab/random?n=200&seed=21"))
    no_hist = [r for r in out["results"] if r["truth"]["payee_profile"] == "new_no_history"]
    assert no_hist and all(r["mule"]["rbih_account"] is None for r in no_hist)


@pytest.mark.parametrize("q", ["n=0", "n=1001", "n=abc", "seed=x"])
def test_random_rejects_bad_params(lab, q):
    assert lab.get(f"/api/lab/random?{q}").status_code == 400


def test_lab_decisions_match_full_shap_computation():
    """The SHAP shortcut (only rows where the pressure gate can matter) must not change any result."""
    from safepay import challenge
    from safepay.assess import score_coercion
    from safepay.coercion.model import CoercionModel
    cm = CoercionModel()
    rows = challenge.test_pool()["test"].sample(40, random_state=7).reset_index(drop=True)
    rec = rows.to_dict("records")
    for stage in ("pre_pause", "post_pause"):
        fast = challenge._ml_with_needed_shap(rows, rec, cm, stage)
        full = cm.predict_batch(rows, stage)
        for i, row in enumerate(rec):
            ctx = {**row, "stage": stage, "checklist": []}
            a = score_coercion(ctx, {"status": "ok", **fast[i]}, cm.card, stage)
            b = score_coercion(ctx, {"status": "ok", **full.iloc[i].to_dict()}, cm.card, stage)
            # Score and band (all the fusion engine reads) must be identical. The gate *flag* can differ
            # on rows where the gate cannot change the score, so the lab never displays that flag.
            assert (a["coercion_score"], a["band"]) == (b["coercion_score"], b["band"]), (stage, row["txn_id"])


# ------------------------------------------------------------------ what-if
def test_what_if_base_matches_assess_and_probes_only_active_signals(lab):
    body = {"user_id": 1, "payee_vpa": "verify.desk.2291@demoupi", "amount": 45_000, "situation": SIT}
    real = lab.post("/api/assess", json=body).get_json()
    w = _strict(lab.post("/api/lab/what-if", json=body))
    assert w["base"]["decision"] == real["decision"]
    assert math.isclose(w["base"]["coercion_score"], real["coercion"]["coercion_score"])
    ids = {p["id"] for p in w["probes"]}
    assert {"no_call", "typed_vpa", "calm_typing", "no_app_switch"} <= ids
    assert not ids & {"no_screen_share", "no_remote_app", "no_fd", "daytime"}      # not active -> not probed
    no_call = next(p for p in w["probes"] if p["id"] == "no_call")
    assert no_call["delta"] < 0                       # removing the unknown call lowers coercion risk
    assert [s["amount"] for s in w["amount_sweep"]] == sorted(s["amount"] for s in w["amount_sweep"])


def test_what_if_does_not_save_assessments(lab, tmp_path):
    body = {"user_id": 2, "payee_vpa": "verify.desk.2291@demoupi", "amount": 9_000, "situation": SIT}
    before = lab.get("/api/dashboard/summary").get_json() if lab.get("/api/dashboard/summary").status_code == 200 else None
    lab.post("/api/lab/what-if", json=body)
    after = lab.get("/api/dashboard/summary").get_json() if before is not None else None
    assert before == after


def test_what_if_requires_situation(lab):
    assert lab.post("/api/lab/what-if", json={"user_id": 1}).status_code == 400
    assert lab.post("/api/lab/what-if", json={"user_id": "1", "situation": SIT}).status_code == 400


def test_requirements_pin_xgboost():
    """The saved coercion models are XGBoost; without it the app silently runs with no coercion model."""
    reqs = (ROOT / "requirements.txt").read_text()
    assert any(line.startswith("xgboost") for line in reqs.splitlines())
