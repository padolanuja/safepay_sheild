"""Build demo payee histories from REAL RBIH accounts in the evaluation sample.

Input:  reports/rbih_mule_eval_sample.csv (from scripts/evaluate_mule_rbih.py)
Output: data/demo/rbih_payee_histories.csv.gz  (pseudonymised transactions)
        data/demo/rbih_payee_provenance.json   (source account, label, score, why chosen)

Each demo role is filled by an account whose label AND actual model score match
the role. The choice among candidates is random with seed 42, not hand-picked.
Pseudonymisation keeps the structure that the 9 features depend on: counterparty
IDs are remapped one-to-one per account and amounts/channels/types are kept. The
script recomputes the score after pseudonymising and fails if it changes.

Payees with too little history (brand-new payees) are NOT built here. They have
fewer than MULE_MIN_HISTORY_TXNS transactions, the model is never called for them,
and seed.py creates them directly.
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from safepay.mule.features import compute_features_grouped  # noqa: E402
from safepay.mule.loader import load_mule_model  # noqa: E402

COLS = ["account_id", "transaction_timestamp", "mcc_code", "txn_type", "amount", "counterparty_id", "channel"]

# role -> (required label, score range [lo, hi), description)
ROLES = {
    "mule_high_a":      (1, (config.MULE_HIGH_BAND, 1.01), "labelled mule, high band"),
    "mule_high_b":      (1, (config.MULE_HIGH_BAND, 1.01), "labelled mule, high band"),
    "mule_elevated":    (1, (config.MULE_THRESHOLD, config.MULE_HIGH_BAND), "labelled mule, elevated band"),
    "clean_p2p_family": (0, (0.0, 0.02), "legitimate, very low score"),
    "clean_p2p_rent":   (0, (0.0, 0.02), "legitimate, very low score"),
    "clean_p2p_misc":   (0, (0.0, 0.02), "legitimate, very low score"),
    "clean_merchant_a": (0, (0.0, 0.02), "legitimate, very low score (shown as a merchant in the demo)"),
    "clean_merchant_b": (0, (0.0, 0.02), "legitimate, very low score (shown as a merchant in the demo)"),
}
# Shift every history so its last transaction lands just before the demo "today".
DEMO_ANCHOR = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)


def main() -> None:
    sample = pd.read_csv(config.REPORTS_DIR / "rbih_mule_eval_sample.csv")
    rng = np.random.default_rng(config.RANDOM_SEED)
    picked, used = {}, set()
    for role, (label, (lo, hi), _) in ROLES.items():
        cand = sample[(sample.is_mule == label) & (sample.mule_probability >= lo)
                      & (sample.mule_probability < hi) & ~sample.account_id.isin(used)
                      & (sample.total_transactions >= config.MULE_MIN_HISTORY_TXNS)].sort_values("account_id")
        if cand.empty:
            sys.exit(f"no RBIH account in the sample fits role {role}; not inventing one")
        row = cand.iloc[int(rng.integers(len(cand)))]
        picked[role] = row
        used.add(row.account_id)

    # Read only the parts that hold the chosen accounts (stats-based), plus scattered part(s).
    ids = sorted(used)
    frames = []
    for p in sorted(glob.glob(str(config.RBIH_DATA_DIR / "transactions" / "batch-*" / "part_*.parquet"))):
        md = pq.ParquetFile(p).metadata
        ci = md.schema.names.index("account_id")
        lo = min(md.row_group(i).column(ci).statistics.min for i in range(md.num_row_groups))
        hi = max(md.row_group(i).column(ci).statistics.max for i in range(md.num_row_groups))
        if any(lo <= a <= hi for a in ids):
            frames.append(pq.read_table(p, columns=COLS, filters=[("account_id", "in", ids)]).to_pandas())
    raw = pd.concat(frames, ignore_index=True)

    bundle = load_mule_model()
    model = bundle["model"]
    out, prov = [], {}
    for role, row in picked.items():
        t = raw[raw.account_id == row.account_id].sort_values("transaction_timestamp").copy()
        # pseudonymise: one-to-one counterparty remap, shift timestamps
        cps = {c: f"cp_{i:05d}" for i, c in enumerate(pd.unique(t.counterparty_id))}
        t["counterparty_id"] = t.counterparty_id.map(cps)
        ts = pd.to_datetime(t.transaction_timestamp, utc=True)
        t["transaction_timestamp"] = (ts + (DEMO_ANCHOR - ts.max())).dt.strftime("%Y-%m-%dT%H:%M:%S")
        t["payee_key"] = role
        t = t.drop(columns="account_id")

        f = compute_features_grouped(t, key="payee_key")
        prob = float(model.predict_proba(f[config.MULE_FEATURES])[:, 1][0])
        if abs(prob - row.mule_probability) > 1e-9:
            sys.exit(f"{role}: score changed after pseudonymising ({row.mule_probability} -> {prob})")
        out.append(t)
        prov[role] = {
            "source": "RBIH-NFPC Phase 2",
            "source_account_id": row.account_id,
            "rbih_label_is_mule": int(row.is_mule),
            "rbih_alert_reason": None if pd.isna(row.alert_reason) else row.alert_reason,
            "mule_probability": round(prob, 4),
            "band": "high" if prob >= config.MULE_HIGH_BAND else ("elevated" if prob >= config.MULE_THRESHOLD else "low"),
            "transactions": int(len(t)),
            "role_requirement": ROLES[role][2],
            "features": {k: float(v) for k, v in f.iloc[0].items()},
        }

    config.DEMO_PAYEE_HISTORIES.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(out, ignore_index=True).to_csv(config.DEMO_PAYEE_HISTORIES, index=False, compression="gzip")
    config.DEMO_PAYEE_PROVENANCE.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selection": f"random among candidates matching label and score band, seed {config.RANDOM_SEED}",
        "pseudonymisation": "counterparty IDs remapped 1:1 per account; timestamps shifted so the last "
                            "transaction is 2026-11-20; amounts, channels, txn types, MCC kept",
        "score_caveat": "scores from the provided mule model; see reports/rbih_mule_eval.json caveat",
        "payees": prov,
    }, indent=2))
    for role, p in prov.items():
        print(f"{role:<18} {p['source_account_id']}  mule={p['rbih_label_is_mule']}  "
              f"p={p['mule_probability']:.3f} ({p['band']:<8}) n={p['transactions']:>5}  {p['rbih_alert_reason'] or ''}")


if __name__ == "__main__":
    main()
