"""Seed the demo database:  python -m safepay.seed

Rebuilds data/safepay.db from scratch. Contents:
  * 4 fictional payers with 90 days of synthetic payment history (seed 42),
    relative to the time of seeding. Re-seed before a demo.
  * Payees of three kinds (payees.history_source):
      - rbih_derived:   8 payees whose histories are real RBIH accounts, pseudonymised
                        (data/demo/*, built by scripts/build_demo_payees.py). Their mule
                        labels and scores are recorded in the provenance JSON.
      - synthetic_demo: brand-new payees with fewer than MULE_MIN_HISTORY_TXNS
                        transactions. The mule model is never called for them.
      - none:           everyday payees with no history at all.
  * Trusted contacts (with and without consent), trusted beneficiaries, a planned payment.
  * The 13 demo scenarios, each with its inputs and expected decision(s).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import config
from safepay import db

# --------------------------------------------------------------------------- users
USERS = [
    # id, name, age, language, tenure_months, balance
    (1, "Ramesh Kulkarni", 62, "hi", 216, 850_000),
    (2, "Priya Nair", 27, "en", 38, 95_000),
    (3, "Arjun Deshmukh", 34, "mr", 120, 240_000),
    (4, "Kamala Iyer", 70, "en", 240, 320_000),
]

# RBIH-derived payees: demo_role -> (vpa, display name, type)
RBIH_PAYEES = {
    "mule_high_a":      ("alpha.growth.desk@demoupi", "Alpha Growth Desk", "P2P"),
    "mule_high_b":      ("phone.resale.hub@demoupi", "Phone Resale Hub", "P2P"),
    "mule_elevated":    ("daniel.m.uk@demoupi", "Daniel M", "P2P"),
    "clean_p2p_family": ("lata.deshmukh@demobank", "Lata Deshmukh (mother)", "P2P"),
    "clean_p2p_rent":   ("suresh.landlord@demobank", "Suresh Pillai (landlord)", "P2P"),
    "clean_p2p_misc":   ("nikhil.kulkarni@demobank", "Nikhil Kulkarni (nephew)", "P2P"),
    "clean_merchant_a": ("citycare.hospital@demomerchant", "City Care Hospital", "P2M"),
    "clean_merchant_b": ("freshmart.grocery@demomerchant", "Fresh Mart Grocery", "P2M"),
}

# Brand-new payees: vpa -> (display name, vpa_age_days, list of (days_ago, D/C, amount))
# All have fewer than MULE_MIN_HISTORY_TXNS transactions -> mule status "insufficient_history".
NEW_PAYEES = {
    "verify.desk.2291@demoupi": ("Verification Desk", 3, [(2, "C", 1.0), (1, "C", 10.0)]),
    "kyc.update.cell@demoupi":  ("KYC Update Cell", 2, [(1, "C", 1.0)]),
    "task.rewards.hr@demoupi":  ("Task Rewards HR", 4, [(3, "C", 200.0), (2, "C", 500.0), (1, "D", 450.0)]),
    "quick.settle.77@demoupi":  ("Quick Settle", 5, [(4, "C", 1.0)]),
    "rohan.friend@demobank":    ("Rohan Joshi (friend)", 9, []),
}

# Everyday payees in the payers' own histories (no transaction history stored for them).
EVERYDAY_PAYEES = {
    "milk.dairy@demomerchant": ("Gokul Dairy", "P2M"),
    "power.bill@demomerchant": ("State Power Bill", "P2M"),
    "medplus.pharma@demomerchant": ("MedPlus Pharmacy", "P2M"),
    "metro.card@demomerchant": ("Metro Card Recharge", "P2M"),
    "cafe.brew@demomerchant": ("Brew Cafe", "P2M"),
    "mobile.recharge@demomerchant": ("Mobile Recharge", "P2M"),
    "temple.trust@demomerchant": ("Temple Trust", "P2M"),
}

# Payer history templates: (payee_vpa, type, median amount, payments per 90 days, usual hours)
HISTORY = {
    1: [("milk.dairy@demomerchant", "P2M", 60, 75, (7, 8, 9)),
        ("freshmart.grocery@demomerchant", "P2M", 900, 12, (10, 11, 18)),
        ("power.bill@demomerchant", "P2M", 2200, 3, (11, 12)),
        ("medplus.pharma@demomerchant", "P2M", 750, 6, (10, 17, 18)),
        ("nikhil.kulkarni@demobank", "P2P", 3000, 3, (19, 20)),
        ("temple.trust@demomerchant", "P2M", 500, 4, (8, 9))],
    2: [("suresh.landlord@demobank", "P2P", 15000, 3, (9, 10)),
        ("cafe.brew@demomerchant", "P2M", 320, 25, (13, 19, 20, 21)),
        ("metro.card@demomerchant", "P2M", 500, 8, (8, 9)),
        ("mobile.recharge@demomerchant", "P2M", 299, 3, (21, 22)),
        ("freshmart.grocery@demomerchant", "P2M", 1100, 10, (19, 20))],
    3: [("lata.deshmukh@demobank", "P2P", 8000, 3, (19, 20, 21)),
        ("freshmart.grocery@demomerchant", "P2M", 1600, 14, (18, 19, 20)),
        ("power.bill@demomerchant", "P2M", 2600, 3, (20, 21)),
        ("metro.card@demomerchant", "P2M", 600, 10, (8, 9)),
        ("cafe.brew@demomerchant", "P2M", 260, 12, (13, 14))],
    4: [("freshmart.grocery@demomerchant", "P2M", 700, 15, (9, 10, 11)),
        ("milk.dairy@demomerchant", "P2M", 55, 70, (7, 8)),
        ("medplus.pharma@demomerchant", "P2M", 1200, 6, (10, 11)),
        ("temple.trust@demomerchant", "P2M", 300, 6, (8, 9)),
        ("power.bill@demomerchant", "P2M", 1800, 3, (11, 12))],
}

# ---------------------------------------------------------------------- scenarios
# Situation keys use the coercion dataset's column names (simulated device signals).
# payee_is_new and the 24h velocity features are NOT set here: they are derived from
# the database (user_transactions + prior_payments) when the scenario runs.
BASE_SITUATION = {
    "on_voice_call": 0, "on_video_call": 0, "call_duration_min": 0.0,
    "caller_in_contacts": 0, "caller_is_international": 0,
    "screen_share_active": 0, "remote_access_app_installed_7d": 0,
    "session_duration_sec": 45, "hesitation_events": 0, "segmented_typing": 0,
    "app_switches": 0, "vpa_pasted": 0, "fd_or_loan_liquidated_7d": 0,
    "payee_in_contacts": 0, "hour_of_day": 14,
}


def sit(**kw):
    s = dict(BASE_SITUATION)
    unknown = set(kw) - set(s) - {"simulated_day_of_month"}
    if unknown:
        raise ValueError(f"unknown situation keys: {unknown}")
    s.update(kw)
    return s


def ans(know, guiding, purpose, checklist=()):
    return {"ans_know_payee": know, "ans_someone_guiding": guiding, "ans_purpose": purpose,
            "checklist": list(checklist)}


SCENARIOS = [
    (1, "Ramesh, 62: digital arrest",
     "2-hour video call with an unknown number, 'government verification', FD broken, says no one "
     "is guiding him. The payee is brand new (2 transactions), so the mule check has nothing to go on.",
     1, "verify.desk.2291@demoupi", 95_000,
     sit(on_video_call=1, call_duration_min=120, session_duration_sec=240, hesitation_events=5,
         segmented_typing=1, app_switches=4, vpa_pasted=1, fd_or_loan_liquidated_7d=1, hour_of_day=15),
     ans("unsure", "no", "govt_fine_verification"), [], ["HOLD"],
     "Key differentiator: mule status insufficient_history; caught by coercion alone."),
    (2, "Fake KYC update",
     "Unknown voice call, screen sharing on, remote-access app installed this week.",
     4, "kyc.update.cell@demoupi", 35_000,
     sit(on_voice_call=1, call_duration_min=18, screen_share_active=1, remote_access_app_installed_7d=1,
         session_duration_sec=150, hesitation_events=3, app_switches=3, vpa_pasted=1, hour_of_day=11),
     ans("no", "no", "other"), [], ["HOLD"], "Severe device-control indicators."),
    (3, "Investment scam: 3rd escalating payment today",
     "Calm user, no call. Third and largest payment today to an account the mule model scores high.",
     3, "alpha.growth.desk@demoupi", 50_000,
     sit(session_duration_sec=40, hour_of_day=21),
     ans("yes", "no", "investment"),
     [{"minutes_ago": 300, "amount": 10_000}, {"minutes_ago": 120, "amount": 25_000}],
     ["HOLD", "WARN"], "Mule (high, RBIH-derived) + escalation."),
    (4, "Job/task scam: escalating task fees",
     "Young user paying small, rising 'task fees' to a new payee.",
     2, "task.rewards.hr@demoupi", 3_000,
     sit(session_duration_sec=60, app_switches=2, vpa_pasted=1, hour_of_day=22),
     ans("no", "no", "job_task_fee"),
     [{"minutes_ago": 180, "amount": 200}, {"minutes_ago": 120, "amount": 500},
      {"minutes_ago": 60, "amount": 1_500}],
     ["WARN", "HOLD"],
     "Escalation pattern (splitting floor guarantees at least WARN). HOLD accepted: the post-pause model "
     "scores 0.999, but 'job_task_fee' appears only on coerced rows in the synthetic data, so that score "
     "is optimistic. With purpose 'other' it is still HOLD (0.963)."),
    (5, "Romance scam",
     "No call, large amount to a new payee, purpose 'family'. The payee scores elevated on the mule model.",
     2, "daniel.m.uk@demoupi", 60_000,
     sit(session_duration_sec=70, hour_of_day=23),
     ans("yes", "no", "family"), [], ["WARN"], "Elevated mule + unusual amount."),
    (6, "Splitting: 5 x Rs 19,999 in 20 minutes",
     "Fifth payment of Rs 19,999 to the same new payee within 20 minutes.",
     4, "quick.settle.77@demoupi", 19_999,
     sit(session_duration_sec=50, hesitation_events=2, hour_of_day=16),
     ans("unsure", "no", "other"),
     [{"minutes_ago": m, "amount": 19_999} for m in (20, 15, 10, 5)],
     ["WARN", "HOLD"], "Splitting just under a round limit."),
    (7, "Genuine: hospital bill",
     "Large payment to a new merchant (a real low-risk RBIH-derived account), no call.",
     1, "citycare.hospital@demomerchant", 85_000,
     sit(session_duration_sec=90, hesitation_events=1, hour_of_day=10),
     ans("yes", "no", "medical"), [], ["WARN"], "Hard negative: WARN, not HOLD."),
    (8, "Genuine: son paying mother while on a call with her",
     "Known contact on the call, trusted beneficiary.",
     3, "lata.deshmukh@demobank", 10_000,
     sit(on_voice_call=1, call_duration_min=6, caller_in_contacts=1, payee_in_contacts=1, hour_of_day=20),
     ans("yes", "no", "family"), [], ["ALLOW"], "Trusted beneficiary, known caller."),
    (9, "Genuine: rent matching a planned payment",
     "Rent to the landlord on the planned day.",
     2, "suresh.landlord@demobank", 15_000,
     sit(payee_in_contacts=1, hour_of_day=9, simulated_day_of_month=5),
     ans("yes", "no", "rent_fees"), [], ["ALLOW"], "Planned payment + trusted beneficiary."),
    (10, "Genuine payer paying a mule account",
     "Buying a second-hand phone; no coercion signals; the payee scores high on the mule model.",
     2, "phone.resale.hub@demoupi", 8_000,
     sit(session_duration_sec=55, hour_of_day=18),
     ans("no", "no", "bills_shopping"), [], ["WARN"], "Mule warning only."),
    (11, "Everyday grocery payment",
     "Small payment to a regular merchant.",
     4, "freshmart.grocery@demomerchant", 640,
     sit(session_duration_sec=25, hour_of_day=10),
     ans("yes", "no", "bills_shopping"), [], ["ALLOW"], "Everyday payment."),
    (12, "Payee with no transaction history",
     "Splitting a dinner bill with a friend whose account is brand new (0 transactions).",
     3, "rohan.friend@demobank", 500,
     sit(session_duration_sec=30, payee_in_contacts=1, hour_of_day=22),
     ans("yes", "no", "bills_shopping"), [], ["ALLOW"],
     "Mule status insufficient_history; decided on coercion signals only, and the explanation says so."),
    (13, "Trusted beneficiary, but screen sharing is on",
     "Paying a trusted nephew while an unknown caller has screen sharing running.",
     1, "nikhil.kulkarni@demobank", 20_000,
     sit(on_voice_call=1, call_duration_min=12, screen_share_active=1, payee_in_contacts=1,
         session_duration_sec=120, hesitation_events=2, hour_of_day=17),
     ans("yes", "no", "family"), [], ["HOLD"], "Trust never overrides a severe indicator."),
]


# --------------------------------------------------------------------------- build
def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _user_history(rng, now):
    rows = []
    for uid, items in HISTORY.items():
        for vpa, ptype, median, n, hours in items:
            for _ in range(n):
                day = int(rng.integers(1, 91))
                ts = (now - timedelta(days=day)).replace(hour=int(rng.choice(hours)),
                                                         minute=int(rng.integers(0, 60)), second=0)
                amt = median if median >= 5000 else round(float(rng.lognormal(np.log(median), 0.35)), 2)
                rows.append((uid, vpa, ptype, max(1.0, amt), _iso(ts)))
    return rows


def build(db_path=None) -> dict:
    db_path = db_path or config.DB_PATH
    if not config.DEMO_PAYEE_HISTORIES.exists():
        raise SystemExit(f"missing {config.DEMO_PAYEE_HISTORIES}: run scripts/build_demo_payees.py first")
    prov = json.loads(config.DEMO_PAYEE_PROVENANCE.read_text())["payees"]
    if set(prov) != set(RBIH_PAYEES):
        raise SystemExit(f"provenance roles {sorted(prov)} != expected {sorted(RBIH_PAYEES)}")

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    rng = np.random.default_rng(config.RANDOM_SEED)
    now = datetime.now().replace(microsecond=0)

    conn = db.connect(db_path)
    db.init_schema(conn)
    with conn:
        # ---- payees
        hist = pd.read_csv(config.DEMO_PAYEE_HISTORIES)
        for role, (vpa, name, ptype) in RBIH_PAYEES.items():
            h = hist[hist.payee_key == role]
            ts = pd.to_datetime(h.transaction_timestamp)
            age = int((ts.max() - ts.min()).days) + 1
            conn.execute("INSERT INTO payees VALUES (?,?,?,?,?,?,?,?)",
                         (vpa, name, ptype, age, 0, 1, "rbih_derived", role))
            conn.executemany(
                "INSERT INTO payee_transactions (payee_vpa, ts, txn_type, amount, counterparty_id, channel, mcc_code)"
                " VALUES (?,?,?,?,?,?,?)",
                [(vpa, r.transaction_timestamp, r.txn_type, float(r.amount), r.counterparty_id, r.channel,
                  int(r.mcc_code)) for r in h.itertuples()])
        for vpa, (name, age, txns) in NEW_PAYEES.items():
            assert len(txns) < config.MULE_MIN_HISTORY_TXNS
            conn.execute("INSERT INTO payees VALUES (?,?,?,?,?,?,?,?)",
                         (vpa, name, "P2P", age, 0, int(bool(txns)), "synthetic_demo" if txns else "none", None))
            conn.executemany(
                "INSERT INTO payee_transactions (payee_vpa, ts, txn_type, amount, counterparty_id, channel)"
                " VALUES (?,?,?,?,?,?)",
                [(vpa, _iso(now - timedelta(days=d)), t, a, f"cp_new_{i}", "UPC" if t == "C" else "UPD")
                 for i, (d, t, a) in enumerate(txns)])
        for vpa, (name, ptype) in EVERYDAY_PAYEES.items():
            conn.execute("INSERT INTO payees VALUES (?,?,?,?,?,?,?,?)",
                         (vpa, name, ptype, 1500, 0, 0, "none", None))

        # ---- users + their payment history
        hist_rows = _user_history(rng, now)
        for uid, name, age, lang, tenure, bal in USERS:
            mine = [r for r in hist_rows if r[0] == uid]
            amounts = [r[3] for r in mine]
            hours = pd.Series([int(r[4][11:13]) for r in mine]).value_counts(normalize=True)
            usual = sorted(int(h) for h, share in hours.items() if share >= 0.05)
            conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
                         (uid, name, age, lang, tenure, bal, round(float(np.mean(amounts)), 2), json.dumps(usual)))
        conn.executemany("INSERT INTO user_transactions (user_id, payee_vpa, payee_type, amount, ts)"
                         " VALUES (?,?,?,?,?)", hist_rows)

        # ---- trust settings (fictional people, masked phones)
        t = _iso(now - timedelta(days=30))
        conn.executemany(
            "INSERT INTO trusted_contacts (user_id, name, relation, phone_masked, consent_given, consent_timestamp)"
            " VALUES (?,?,?,?,?,?)",
            [(1, "Vikram Kulkarni", "son", "98xxxxxx21", 1, t),
             (4, "Meera Iyer", "daughter", "97xxxxxx08", 1, t),
             (3, "Sneha Deshmukh", "wife", "99xxxxxx45", 1, t),
             (2, "Ananya Rao", "friend", "90xxxxxx77", 0, None)])   # no consent -> never alerted
        conn.executemany(
            "INSERT INTO trusted_beneficiaries (user_id, payee_vpa, label, added_at) VALUES (?,?,?,?)",
            [(3, "lata.deshmukh@demobank", "Mother", t),
             (2, "suresh.landlord@demobank", "Landlord", t),
             (1, "nikhil.kulkarni@demobank", "Nephew", t)])
        conn.execute(
            "INSERT INTO planned_payments (user_id, payee_vpa, amount, day_of_month, purpose, created_at)"
            " VALUES (?,?,?,?,?,?)", (2, "suresh.landlord@demobank", 15_000, 5, "Monthly rent", t))

        # ---- scenarios
        for (sid, title, desc, uid, vpa, amount, situation, answers, prior, expected, note) in SCENARIOS:
            conn.execute("INSERT INTO scenarios VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (sid, title, desc, uid, vpa, amount, json.dumps(situation), json.dumps(answers),
                          json.dumps(prior), json.dumps(expected), note))

    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
              ("users", "payees", "payee_transactions", "user_transactions", "trusted_contacts",
               "trusted_beneficiaries", "planned_payments", "scenarios")}
    conn.close()
    return counts


if __name__ == "__main__":
    print(f"seeded {config.DB_PATH}")
    for k, v in build().items():
        print(f"  {k:<22} {v}")
