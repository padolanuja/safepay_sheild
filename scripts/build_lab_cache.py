"""Pre-compute the SHAP payee/amount share for every held-out test row (Blind Test Lab speed-up).

    .\\.venv\\Scripts\\python scripts\\build_lab_cache.py

Writes data/lab/shap_share_cache.csv. Only the SHAP share used by the pressure gate is cached
(it is a fixed function of the row and the model). The probability, the rules and the fusion
decision are always computed live when the lab draws rows. Rows missing from the cache are
computed live and appended, so the lab works without this file, just more slowly.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safepay import challenge  # noqa: E402
from safepay.coercion.model import CoercionModel  # noqa: E402

if __name__ == "__main__":
    t0 = time.time()
    cm = CoercionModel()
    if not cm.available:
        sys.exit(cm.error)
    n = challenge.warm_cache(cm)
    print(f"cache entries: {n} -> {challenge.SHARE_CACHE} ({time.time() - t0:.0f} s)")
