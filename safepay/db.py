"""SQLite schema and helpers.

All data in this database is fictional demo data or pseudonymised RBIH-derived
payee histories. It never holds UPI PINs, OTPs, passwords, raw call audio, screen
content or contact lists. Only derived indicators are stored.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import config

SCHEMA = """
PRAGMA foreign_keys = ON;

-- Fictional demo payers.
CREATE TABLE IF NOT EXISTS users (
    id                      INTEGER PRIMARY KEY,
    name                    TEXT NOT NULL,
    age                     INTEGER NOT NULL CHECK (age BETWEEN 18 AND 110),
    language                TEXT NOT NULL DEFAULT 'en' CHECK (language IN ('en','hi','mr')),
    account_tenure_months   INTEGER NOT NULL,
    balance_inr             REAL NOT NULL,
    avg_txn_amount_90d      REAL NOT NULL,      -- derived from user_transactions at seed time
    usual_active_hours      TEXT NOT NULL       -- JSON list of hours, derived at seed time
);

-- Payees. history_available = 1 when payee_transactions rows exist for the payee.
CREATE TABLE IF NOT EXISTS payees (
    vpa                     TEXT PRIMARY KEY,
    display_name            TEXT NOT NULL,
    payee_type              TEXT NOT NULL CHECK (payee_type IN ('P2P','P2M')),
    vpa_age_days            INTEGER NOT NULL,
    prior_complaints        INTEGER NOT NULL DEFAULT 0,
    history_available       INTEGER NOT NULL CHECK (history_available IN (0,1)),
    history_source          TEXT NOT NULL,      -- 'rbih_derived' | 'synthetic_demo' | 'none'
    demo_role               TEXT                -- e.g. 'mule_high_a'; provenance in data/demo/*.json
);

-- Payee transaction history, used to compute the 9 mule features.
-- Columns mirror RBIH: txn_type D/C, negative amount = reversal, channel code (UPC/UPD = UPI).
CREATE TABLE IF NOT EXISTS payee_transactions (
    id                      INTEGER PRIMARY KEY,
    payee_vpa               TEXT NOT NULL REFERENCES payees(vpa) ON DELETE CASCADE,
    ts                      TEXT NOT NULL,
    txn_type                TEXT NOT NULL CHECK (txn_type IN ('D','C')),
    amount                  REAL NOT NULL,
    counterparty_id         TEXT NOT NULL,
    channel                 TEXT NOT NULL,
    mcc_code                INTEGER
);
CREATE INDEX IF NOT EXISTS ix_payee_txn ON payee_transactions(payee_vpa);

-- The payer's own past payments (profiling, splitting detection, history view).
CREATE TABLE IF NOT EXISTS user_transactions (
    id                      INTEGER PRIMARY KEY,
    user_id                 INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    payee_vpa               TEXT NOT NULL,
    payee_type              TEXT NOT NULL CHECK (payee_type IN ('P2P','P2M')),
    amount                  REAL NOT NULL CHECK (amount > 0),
    ts                      TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'completed_simulated',
    decision                TEXT,               -- ALLOW / WARN / HOLD when made through SafePay Shield
    assessment_id           INTEGER REFERENCES risk_assessments(id),
    source                  TEXT NOT NULL DEFAULT 'seed'   -- 'seed' | 'app' | 'scenario_setup'
);
CREATE INDEX IF NOT EXISTS ix_user_txn ON user_transactions(user_id, ts);

CREATE TABLE IF NOT EXISTS trusted_contacts (
    id                      INTEGER PRIMARY KEY,
    user_id                 INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                    TEXT NOT NULL,
    relation                TEXT NOT NULL,
    phone_masked            TEXT NOT NULL,      -- e.g. '98xxxxxx21'; never a full number
    consent_given           INTEGER NOT NULL DEFAULT 0 CHECK (consent_given IN (0,1)),
    consent_timestamp       TEXT
);

CREATE TABLE IF NOT EXISTS trusted_beneficiaries (
    id                      INTEGER PRIMARY KEY,
    user_id                 INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    payee_vpa               TEXT NOT NULL,
    label                   TEXT,
    added_at                TEXT NOT NULL,
    UNIQUE (user_id, payee_vpa)
);

-- Payments declared in advance. A match lowers friction; it never overrides severe indicators.
CREATE TABLE IF NOT EXISTS planned_payments (
    id                      INTEGER PRIMARY KEY,
    user_id                 INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    payee_vpa               TEXT NOT NULL,
    amount                  REAL NOT NULL,
    amount_tolerance_pct    REAL NOT NULL DEFAULT 5,
    day_of_month            INTEGER CHECK (day_of_month BETWEEN 1 AND 31),
    day_window              INTEGER NOT NULL DEFAULT 3,
    purpose                 TEXT,
    created_at              TEXT NOT NULL
);

-- Every assessment, with inputs, scores, indicators, decision, explanation and latency.
CREATE TABLE IF NOT EXISTS risk_assessments (
    id                      INTEGER PRIMARY KEY,
    created_at              TEXT NOT NULL,
    user_id                 INTEGER REFERENCES users(id),
    payee_vpa_masked        TEXT NOT NULL,      -- masked in logs
    amount                  REAL NOT NULL,
    stage                   TEXT NOT NULL CHECK (stage IN ('pre_pause','post_pause')),
    inputs_json             TEXT NOT NULL,      -- derived indicators only, each tagged with its source
    mule_json               TEXT NOT NULL,
    coercion_json           TEXT NOT NULL,
    behaviour_json          TEXT NOT NULL,
    decision                TEXT NOT NULL CHECK (decision IN ('ALLOW','WARN','HOLD')),
    explanation_json        TEXT NOT NULL,
    language                TEXT NOT NULL,
    latency_ms              REAL NOT NULL,
    scenario_id             INTEGER,
    outcome                 TEXT                -- 'proceeded' | 'cancelled' (simulated)
);

-- Rescue feedback loop. Stored for human review only; nothing is auto-retrained from it.
CREATE TABLE IF NOT EXISTS feedback (
    id                      INTEGER PRIMARY KEY,
    assessment_id           INTEGER NOT NULL REFERENCES risk_assessments(id),
    outcome                 TEXT NOT NULL CHECK (outcome IN ('was_scam','was_genuine','rescued')),
    note                    TEXT,
    submitted_by            TEXT NOT NULL CHECK (submitted_by IN ('user','analyst')),
    verified                INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
    created_at              TEXT NOT NULL
);

-- Simulated trusted-contact alerts (nothing is actually sent).
CREATE TABLE IF NOT EXISTS alerts_log (
    id                      INTEGER PRIMARY KEY,
    assessment_id           INTEGER NOT NULL REFERENCES risk_assessments(id),
    contact_id              INTEGER NOT NULL REFERENCES trusted_contacts(id),
    message_preview         TEXT NOT NULL,      -- amount shown as a range only
    created_at              TEXT NOT NULL,
    simulated               INTEGER NOT NULL DEFAULT 1 CHECK (simulated = 1)
);

-- One-click demo scenarios: inputs plus the expected decision(s).
CREATE TABLE IF NOT EXISTS scenarios (
    id                      INTEGER PRIMARY KEY,
    title                   TEXT NOT NULL,
    description             TEXT NOT NULL,
    user_id                 INTEGER NOT NULL REFERENCES users(id),
    payee_vpa               TEXT NOT NULL REFERENCES payees(vpa),
    amount                  REAL NOT NULL,
    situation_json          TEXT NOT NULL,      -- simulated device signals
    answers_json            TEXT,               -- pause-screen answers (post-pause stage)
    prior_payments_json     TEXT NOT NULL DEFAULT '[]',  -- inserted relative to run time
    expected_decisions      TEXT NOT NULL,      -- JSON list, e.g. ["HOLD"] or ["WARN","HOLD"]
    expected_note           TEXT NOT NULL
);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(db_path: Path | str | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def mask_vpa(vpa: str) -> str:
    """'ramesh.k@demobank' -> 'ra****@demobank'. Used for every log entry."""
    name, _, handle = vpa.partition("@")
    return f"{name[:2]}****@{handle}" if handle else "****"


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
