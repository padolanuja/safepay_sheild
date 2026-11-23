"""Evaluate the provided mule model on a labelled RBIH sample.

Sampling (documented in the output JSON):
  * The transaction files are ordered by account: each part holds a contiguous block
    of about 410 accounts. The exception is batch-4/part_0396, which holds rows for
    accounts across the whole range.
  * We pick N_PARTS contiguous parts at random (seed 42). We keep accounts strictly
    inside each part's range (the two boundary accounts may continue into a
    neighbouring part), add their rows from part_0396, and so get complete histories.
  * Only accounts with a label in train_labels.parquet are kept.

Caveat: the mule model was trained on this dataset and its train/test split is
unknown, so these accounts may have been in its training data. The metrics are
labelled "may be optimistic (possible overlap with training data)".

Usage:  python scripts/evaluate_mule_rbih.py [--parts 20]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq
from sklearn.metrics import (average_precision_score, confusion_matrix, f1_score,
                             precision_score, recall_score, roc_auc_score)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from safepay.mule.features import compute_features_grouped  # noqa: E402
from safepay.mule.loader import load_mule_model  # noqa: E402

COLS = ["account_id", "transaction_timestamp", "txn_type", "amount", "counterparty_id", "channel"]
CAVEAT = "may be optimistic (possible overlap with training data)"


def part_index(root: Path) -> pd.DataFrame:
    """Account-id range of every transaction part, read from parquet statistics (no row reads)."""
    rows = []
    for p in sorted(glob.glob(str(root / "transactions" / "batch-*" / "part_*.parquet"))):
        md = pq.ParquetFile(p).metadata
        ci = md.schema.names.index("account_id")
        mins = [md.row_group(i).column(ci).statistics.min for i in range(md.num_row_groups)]
        maxs = [md.row_group(i).column(ci).statistics.max for i in range(md.num_row_groups)]
        rows.append((p, md.num_rows, min(mins), max(maxs)))
    df = pd.DataFrame(rows, columns=["path", "rows", "amin", "amax"])
    df["span"] = df.amax.str[5:].astype(int) - df.amin.str[5:].astype(int)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", type=int, default=20)
    args = ap.parse_args()
    t0 = time.time()
    root = config.RBIH_DATA_DIR
    if not (root / "train_labels.parquet").exists():
        sys.exit(f"RBIH data not found at {root} (set SAFEPAY_RBIH_DIR). Not measured.")

    idx = part_index(root)
    scattered = idx[idx.span > 5000]
    contiguous = idx[idx.span <= 5000]
    rng = np.random.default_rng(config.RANDOM_SEED)
    chosen = contiguous.iloc[sorted(rng.choice(len(contiguous), size=args.parts, replace=False))]

    labels = pd.read_parquet(root / "train_labels.parquet")
    labelled = set(labels.account_id)

    # Accounts strictly inside each chosen part's range, labelled only.
    frames, keep = [], set()
    for _, r in chosen.iterrows():
        t = pd.read_parquet(r.path, columns=COLS)
        inner = t[(t.account_id > r.amin) & (t.account_id < r.amax) & t.account_id.isin(labelled)]
        keep |= set(inner.account_id.unique())
        frames.append(inner)
    for p in scattered.path:  # complete the histories from the scattered part(s)
        tbl = pq.read_table(p, columns=COLS, filters=[("account_id", "in", sorted(keep))])
        frames.append(tbl.to_pandas())
    txns = pd.concat(frames, ignore_index=True)
    print(f"read {len(chosen)} parts + {len(scattered)} scattered part(s): "
          f"{len(txns):,} transactions, {len(keep):,} labelled accounts ({time.time()-t0:.0f}s)")

    feats = compute_features_grouped(txns)
    bundle = load_mule_model()
    model, thr = bundle["model"], bundle["threshold"]
    prob = model.predict_proba(feats[config.MULE_FEATURES])[:, 1]

    df = feats.join(labels.set_index("account_id")[["is_mule", "alert_reason"]], how="left")
    df["mule_probability"] = prob
    df["flag"] = (prob >= thr).astype(int)
    df["band"] = np.where(prob >= config.MULE_HIGH_BAND, "high", np.where(prob >= thr, "elevated", "low"))
    y, p = df.is_mule.values, df.flag.values
    tn, fp, fn, tp = confusion_matrix(y, p).ravel()

    mules = df[df.is_mule == 1]
    recall_by_reason = (mules.groupby("alert_reason")["flag"].agg(["mean", "size"])
                        .rename(columns={"mean": "recall", "size": "n"}).round(3)
                        .sort_values("n", ascending=False).reset_index().to_dict("records"))
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": "RBIH-NFPC Phase 2 (train_labels.parquet + transactions/)",
        "caveat": CAVEAT,
        "caveat_detail": ("The mule model was trained on this dataset and its train/test split "
                          "is unknown; these labelled accounts may have been seen in training. "
                          "Labels may also contain noise/red-herrings (RBIH README)."),
        "feature_definitions": "safepay/mule/features.py (reconstructed; original code not provided)",
        "sample": {
            "method": f"{len(chosen)} random contiguous parts (seed {config.RANDOM_SEED}) + scattered part(s) "
                      "for complete histories; boundary accounts dropped; labelled accounts only",
            "parts": [os.path.relpath(x, root) for x in chosen.path] + [os.path.relpath(x, root) for x in scattered.path],
            "transactions": int(len(txns)),
            "accounts": int(len(df)),
            "mules": int(df.is_mule.sum()),
            "legitimate": int((df.is_mule == 0).sum()),
            "mule_rate_sample": round(float(df.is_mule.mean()), 4),
            "mule_rate_all_labels": round(float(labels.is_mule.mean()), 4),
        },
        "threshold": thr,
        "metrics_at_threshold": {
            "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
            "precision": round(precision_score(y, p, zero_division=0), 4),
            "recall": round(recall_score(y, p), 4),
            "f1": round(f1_score(y, p), 4),
            "false_positive_rate": round(fp / (fp + tn), 4),
        },
        "roc_auc": round(roc_auc_score(y, prob), 4),
        "pr_auc": round(average_precision_score(y, prob), 4),
        "band_share": {b: round(float((df.band == b).mean()), 4) for b in ("low", "elevated", "high")},
        "band_share_mules": {b: round(float((mules.band == b).mean()), 4) for b in ("low", "elevated", "high")},
        "score_quantiles": {
            "mules": {q: round(float(mules.mule_probability.quantile(q)), 3) for q in (0.1, 0.5, 0.9)},
            "legitimate": {q: round(float(df[df.is_mule == 0].mule_probability.quantile(q)), 3) for q in (0.5, 0.9, 0.99)},
        },
        "recall_by_alert_reason": recall_by_reason,
        "feature_medians": {k: round(float(v), 1) for k, v in feats.median().items()},
        "runtime_sec": round(time.time() - t0, 1),
    }
    config.REPORTS_DIR.mkdir(exist_ok=True)
    (config.REPORTS_DIR / "rbih_mule_eval.json").write_text(json.dumps(result, indent=2))
    df.reset_index().to_csv(config.REPORTS_DIR / "rbih_mule_eval_sample.csv", index=False)
    print(json.dumps({k: result[k] for k in ("sample", "metrics_at_threshold", "roc_auc", "pr_auc",
                                             "band_share", "band_share_mules", "score_quantiles")}, indent=1))
    print("recall by alert_reason:")
    for r in recall_by_reason:
        print(f"  {r['alert_reason']:<42} {r['recall']:.3f} (n={r['n']})")
    print("feature medians:", result["feature_medians"])


if __name__ == "__main__":
    main()
