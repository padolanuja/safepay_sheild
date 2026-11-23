"""Phase 4: mule predictor, bands, min-history rule, SHAP explanations and the API."""
import json

import numpy as np
import pandas as pd
import pytest

import config
from safepay import db
from safepay.explain.shap_utils import METHOD_FALLBACK, METHOD_SHAP
from safepay.mule.features import compute_features
from safepay.mule.loader import load_mule_model
from safepay.mule.predictor import MulePredictor, band_for


@pytest.fixture(scope="module")
def predictor():
    return MulePredictor(load_mule_model())


@pytest.fixture(scope="module")
def provenance():
    return json.loads(config.DEMO_PAYEE_PROVENANCE.read_text())["payees"]


def _history(conn, vpa):
    return pd.read_sql("SELECT txn_type, amount, counterparty_id, channel FROM payee_transactions "
                       "WHERE payee_vpa = ?", conn, params=(vpa,))


# ------------------------------------------------------------------------------ bands
@pytest.mark.parametrize("p,band", [(0.0, "low"), (0.0999, "low"), (0.10, "elevated"),
                                    (0.3999, "elevated"), (0.40, "high"), (1.0, "high")])
def test_band_boundaries(p, band):
    assert band_for(p) == band


# -------------------------------------------------------------------- min-history rule
class _ExplodingModel:
    """Stands in for the RF and fails the test if the model is ever called."""
    def __init__(self, real):
        self.real = real

    def predict_proba(self, X):
        raise AssertionError("mule model must not be called below MULE_MIN_HISTORY_TXNS")


@pytest.mark.parametrize("n", range(0, config.MULE_MIN_HISTORY_TXNS))
def test_insufficient_history_never_calls_model(n):
    real = load_mule_model()
    p = MulePredictor(dict(real))
    p.bundle["model"] = _ExplodingModel(real["model"])   # swap after the explainer is built
    txns = pd.DataFrame({"txn_type": ["C"] * n, "amount": [100.0] * n,
                         "counterparty_id": [f"c{i}" for i in range(n)], "channel": ["UPC"] * n})
    r = p.predict_from_transactions(txns)
    assert r["status"] == "insufficient_history"
    assert r["model_called"] is False
    assert "mule_probability" not in r and "band" not in r
    assert r["transactions"] == n


def test_exactly_min_history_calls_model(predictor):
    n = config.MULE_MIN_HISTORY_TXNS
    txns = pd.DataFrame({"txn_type": ["C", "D"] * n, "amount": [500.0] * 2 * n,
                         "counterparty_id": [f"c{i % 3}" for i in range(2 * n)], "channel": ["UPC"] * 2 * n}).head(n)
    r = predictor.predict_from_transactions(txns)
    assert r["status"] == "ok" and r["model_called"] is True


def test_unavailable_model():
    r = MulePredictor(None, load_error="file missing").predict_from_transactions(pd.DataFrame())
    assert r["status"] == "unavailable" and "file missing" in r["reason"]


# ----------------------------------------------------------------- RBIH-derived payees
def test_rbih_payees_get_their_recorded_band(seeded_db, predictor, provenance):
    path, _ = seeded_db
    conn = db.connect(path)
    for vpa, role in conn.execute("SELECT vpa, demo_role FROM payees WHERE history_source='rbih_derived'"):
        r = predictor.predict_for_payee(conn, vpa)
        assert r["status"] == "ok"
        assert r["band"] == provenance[role]["band"], role
        assert r["flag"] == (r["mule_probability"] >= config.MULE_THRESHOLD)
    conn.close()


def test_scenario_payees_have_expected_mule_status(seeded_db, predictor):
    path, _ = seeded_db
    conn = db.connect(path)
    status = {sid: predictor.predict_for_payee(conn, vpa) for sid, vpa in
              conn.execute("SELECT id, payee_vpa FROM scenarios")}
    conn.close()
    assert status[1]["status"] == "insufficient_history"          # Ramesh: key differentiator
    assert status[12]["status"] == "insufficient_history" and status[12]["transactions"] == 0
    assert status[3]["band"] == "high" and status[10]["band"] == "high"
    assert status[5]["band"] == "elevated"
    for sid in (7, 8, 9, 11, 13):
        assert status[sid]["band"] == "low", sid


# ------------------------------------------------------------------------ explanations
def _rbih_X(seeded_db, role):
    path, _ = seeded_db
    conn = db.connect(path)
    vpa = conn.execute("SELECT vpa FROM payees WHERE demo_role = ?", (role,)).fetchone()[0]
    X = pd.DataFrame([compute_features(_history(conn, vpa))])[config.MULE_FEATURES]
    conn.close()
    return X


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("role", ["mule_high_a", "mule_elevated", "clean_p2p_rent"])
def test_explanations_add_up_to_probability(seeded_db, role, fallback):
    p = MulePredictor(load_mule_model(), force_fallback=fallback)
    X = _rbih_X(seeded_db, role)
    prob = p.bundle["model"].predict_proba(X)[0, 1]
    contrib = p.explainer.contributions(X)[0]
    assert p.explainer.base_value + contrib.sum() == pytest.approx(prob, abs=1e-6)
    assert p.explainer.method == (METHOD_FALLBACK if fallback else METHOD_SHAP)


def test_top_factors_ranked_and_plain_language(seeded_db, predictor):
    path, _ = seeded_db
    conn = db.connect(path)
    r = predictor.predict_for_payee(conn, "alpha.growth.desk@demoupi")
    conn.close()
    f = r["top_factors"]
    assert len(f) == 5
    mags = [abs(x["contribution"]) for x in f]
    assert mags == sorted(mags, reverse=True)
    for x in f:
        assert x["feature"] in config.MULE_FEATURES
        assert ("raises" in x["text"]) == (x["direction"] == "raises_risk")
        assert x["text_key"].startswith(f"factor.{x['feature']}.")
    assert r["risk_raising_factors"] and all(x["contribution"] > 0 for x in r["risk_raising_factors"])
    assert r["explanation_method"] == "shap_tree_explainer"
    assert r["source"].startswith("model_prediction:")


# --------------------------------------------------------------------------------- API
def test_api_mule_predict_ok(client):
    r = client.post("/api/mule/predict", json={"vpa": "alpha.growth.desk@demoupi"})
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "ok" and body["band"] == "high"


def test_api_mule_predict_insufficient_history(client):
    body = client.post("/api/mule/predict", json={"vpa": "verify.desk.2291@demoupi"}).get_json()
    assert body["status"] == "insufficient_history" and body["model_called"] is False
    assert "payment situation only" in body["reason"]


@pytest.mark.parametrize("payload,code", [
    (None, 400), ({}, 400), ({"vpa": "no-handle"}, 400), ({"vpa": 42}, 400),
    ({"vpa": "nobody@demoupi"}, 404)])
def test_api_mule_predict_errors(client, payload, code):
    r = client.post("/api/mule/predict", json=payload) if payload is not None else \
        client.post("/api/mule/predict", data="not json", content_type="text/plain")
    assert r.status_code == code and r.get_json()["status"] == code


def test_api_mule_unavailable_returns_503(seeded_db):
    from app import create_app
    app = create_app(db_path=seeded_db[0])
    app.config["MULE_PREDICTOR"] = MulePredictor(None, load_error="validation failed")
    r = app.test_client().post("/api/mule/predict", json={"vpa": "alpha.growth.desk@demoupi"})
    assert r.status_code == 503 and r.get_json()["status"] == "unavailable"
