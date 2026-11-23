"""Phase 3: model contract, feature computation, seed data and the API skeleton."""
import json

import pandas as pd
import pytest

import config
from safepay import db
from safepay.mule.features import compute_features
from safepay.mule.loader import load_mule_model


# ------------------------------------------------------------------ mule model contract
def test_mule_model_contract():
    b = load_mule_model()
    assert list(b["model"].feature_names_in_) == config.MULE_FEATURES
    assert b["threshold"] == config.MULE_THRESHOLD
    assert len(b["model"].estimators_) == 150


def test_loader_rejects_wrong_feature_order(tmp_path, monkeypatch):
    from safepay.mule import loader
    monkeypatch.setattr(config, "MULE_FEATURES", list(reversed(config.MULE_FEATURES)))
    with pytest.raises(loader.MuleModelError):
        loader.load_mule_model()


# ---------------------------------------------------------------------- feature formulas
def test_feature_formulas_on_handmade_history():
    t = pd.DataFrame([
        ("C", 1000.0, "a", "UPC"),
        ("C", 500.0, "b", "UPC"),
        ("D", 1200.0, "c", "UPD"),
        ("D", 300.0, "a", "IPM"),
        ("D", -300.0, "a", "IPM"),   # reversal
    ], columns=["txn_type", "amount", "counterparty_id", "channel"])
    f = compute_features(t)
    assert list(f) == config.MULE_FEATURES
    assert f["total_transactions"] == 5
    assert f["total_debit"] == 1200.0          # 1200 + 300 - 300
    assert f["total_credit"] == 1500.0
    assert f["avg_transaction_amount"] == pytest.approx(2700.0 / 5)
    assert f["unique_counterparties"] == 3
    assert f["upi_transaction_count"] == 3
    assert f["incoming_counterparties"] == 2   # a, b
    assert f["outgoing_counterparties"] == 2   # c, a
    assert f["reversal_count"] == 1


def test_features_refuse_empty_history():
    with pytest.raises(ValueError):
        compute_features(pd.DataFrame(columns=["txn_type", "amount", "counterparty_id", "channel"]))


# ------------------------------------------------------------------------------- seed
def test_seed_counts(seeded_db):
    _, counts = seeded_db
    assert counts["users"] == 4
    assert counts["scenarios"] == 13
    assert counts["payee_transactions"] > 10_000     # RBIH-derived histories


def test_rbih_payee_scores_match_provenance(seeded_db):
    """Each RBIH-derived payee, rebuilt from the DB, gets the score recorded in its provenance."""
    path, _ = seeded_db
    prov = json.loads(config.DEMO_PAYEE_PROVENANCE.read_text())["payees"]
    model = load_mule_model()["model"]
    conn = db.connect(path)
    for vpa, role in conn.execute("SELECT vpa, demo_role FROM payees WHERE history_source='rbih_derived'"):
        t = pd.read_sql("SELECT txn_type, amount, counterparty_id, channel FROM payee_transactions "
                        "WHERE payee_vpa = ?", conn, params=(vpa,))
        f = compute_features(t)
        p = model.predict_proba(pd.DataFrame([f])[config.MULE_FEATURES])[0, 1]
        assert p == pytest.approx(prov[role]["mule_probability"], abs=1e-4), role
    conn.close()


def test_new_payees_are_below_min_history(seeded_db):
    path, _ = seeded_db
    conn = db.connect(path)
    rows = conn.execute(
        "SELECT p.vpa, COUNT(t.id) n FROM payees p LEFT JOIN payee_transactions t ON t.payee_vpa = p.vpa "
        "WHERE p.history_source IN ('synthetic_demo','none') GROUP BY p.vpa").fetchall()
    conn.close()
    assert rows and all(r["n"] < config.MULE_MIN_HISTORY_TXNS for r in rows)


def test_scenario_1_uses_insufficient_history_payee(seeded_db):
    path, _ = seeded_db
    conn = db.connect(path)
    s = conn.execute("SELECT * FROM scenarios WHERE id = 1").fetchone()
    n = conn.execute("SELECT COUNT(*) FROM payee_transactions WHERE payee_vpa = ?", (s["payee_vpa"],)).fetchone()[0]
    conn.close()
    assert 0 < n < config.MULE_MIN_HISTORY_TXNS
    assert json.loads(s["expected_decisions"]) == ["HOLD"]


def test_no_secrets_columns_in_schema(seeded_db):
    path, _ = seeded_db
    conn = db.connect(path)
    cols = {c[1].lower() for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            for c in conn.execute(f"PRAGMA table_info({t[0]})")}
    conn.close()
    assert not any(w in c for c in cols for w in ("pin", "otp", "password", "cvv"))


def test_mask_vpa():
    assert db.mask_vpa("ramesh.k@demobank") == "ra****@demobank"


# -------------------------------------------------------------------------------- API
def test_health(client):
    r = client.get("/api/health").get_json()
    assert r["models"]["mule"]["loaded"] is True
    assert r["models"]["coercion"]["loaded"] is False
    assert r["simulation"] is True


def test_users_and_404(client):
    assert len(client.get("/api/users").get_json()["users"]) == 4
    r = client.get("/api/users/999")
    assert r.status_code == 404 and r.get_json()["status"] == 404


def test_language_validation(client):
    assert client.put("/api/users/1/language", json={"language": "fr"}).status_code == 400
    assert client.put("/api/users/1/language", json={"language": "mr"}).get_json()["language"] == "mr"


def test_payee_lookup(client):
    r = client.get("/api/payees/verify.desk.2291@demoupi?user_id=1").get_json()
    assert r["history_transactions"] == 2 and r["new_for_user"] is True
    assert client.get("/api/payees/not-a-vpa").status_code == 400
    assert client.get("/api/payees/nobody@demoupi").status_code == 404
