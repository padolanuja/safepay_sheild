"""Evidence for config.MULE_MIN_HISTORY_TXNS.

Scores 200 simulated, plausible *clean* payee histories at each history size and
reports how often the provided mule model flags them (probability >= threshold).
If clean histories get flagged as a matter of course at some size, the score at that
size reflects how little data there is, not how money moves. Writes
reports/mule_history_probe.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from safepay.mule.loader import load_mule_model  # noqa: E402

SIZES = [0, 1, 2, 3, 5, 8, 10, 15]
N = 200


def clean_history(n, rng):
    """A simple clean account: about half credits, repeat counterparties, mostly UPI, no reversals."""
    k = max(1, int(n * rng.uniform(0.3, 0.6)))
    inc = rng.random(n) < 0.5
    amt = rng.lognormal(7.5, 0.8, n)
    cps = rng.integers(0, k, n)
    return [n, amt[~inc].sum(), amt[inc].sum(), amt.mean(), len(set(cps)),
            int((rng.random(n) < 0.8).sum()), len(set(cps[inc])), len(set(cps[~inc])), 0]


def main():
    b = load_mule_model()
    m, thr = b["model"], b["threshold"]
    rng = np.random.default_rng(config.RANDOM_SEED)
    rows = []
    for n in SIZES:
        X = pd.DataFrame([[0] * 9] if n == 0 else [clean_history(n, rng) for _ in range(N)],
                         columns=config.MULE_FEATURES)
        p = m.predict_proba(X)[:, 1]
        rows.append({"transactions": n, "samples": len(X), "median_score": round(float(np.median(p)), 3),
                     "share_flagged": round(float((p >= thr).mean()), 3)})
        print(rows[-1])
    out = {"purpose": "evidence for MULE_MIN_HISTORY_TXNS",
           "data": "simulated clean histories (not RBIH); seed 42", "threshold": thr,
           "chosen_minimum": config.MULE_MIN_HISTORY_TXNS, "results": rows}
    config.REPORTS_DIR.mkdir(exist_ok=True)
    (config.REPORTS_DIR / "mule_history_probe.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
