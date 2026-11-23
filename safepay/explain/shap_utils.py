"""Tree-model explanations, shared by the mule and coercion models.

Primary method: shap.TreeExplainer (exact for tree ensembles). We take the class-1
output: each value is how much a feature moved the predicted probability away from
the model's base value. The values add up exactly to the probability (checked in tests).

Fallback (used only if shap can't be imported): Saabas decision-path contributions.
For every tree we walk the path the sample takes and credit each change in the
node's class-1 probability to the feature split on. The contributions also add up
to the probability, but they are a heuristic that can over-credit features near the
root. Results always carry `method`, so the UI can label which method was used.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

try:  # pragma: no cover - exercised implicitly
    import shap  # noqa: F401
    SHAP_AVAILABLE = True
except Exception:  # noqa: BLE001 - any import failure -> fallback
    SHAP_AVAILABLE = False

METHOD_SHAP = "shap_tree_explainer"
METHOD_FALLBACK = "saabas_path_contributions (fallback: shap not installed)"


class TreeExplainer:
    """Explain the class-1 probability of a fitted tree ensemble (or a single tree)."""

    def __init__(self, model, force_fallback: bool = False):
        self.model = model
        self.use_shap = SHAP_AVAILABLE and not force_fallback
        if self.use_shap:
            import shap
            self._ex = shap.TreeExplainer(model)
            ev = np.atleast_1d(self._ex.expected_value)
            self.base_value = float(ev[1] if ev.size > 1 else ev[0])
        else:
            self._ex = None
            self.base_value = float(np.mean([self._tree_p1(t.tree_)[0] for t in self._trees()]))
        self.method = METHOD_SHAP if self.use_shap else METHOD_FALLBACK

    # ---------------------------------------------------------------- public
    def contributions(self, X: pd.DataFrame) -> np.ndarray:
        """Return an array (n_rows, n_features): class-1 contribution of each feature."""
        if self.use_shap:
            raw = self._ex.shap_values(X)
            if isinstance(raw, list):                    # older shap: one array per class
                return np.asarray(raw[1])
            sv = np.asarray(raw)
            return sv[:, :, 1] if sv.ndim == 3 else sv   # (n, features, classes) in shap >= 0.45
        return self._saabas(X.to_numpy(dtype=float))

    # -------------------------------------------------------------- fallback
    def _trees(self):
        return getattr(self.model, "estimators_", [self.model])

    @staticmethod
    def _tree_p1(tree) -> np.ndarray:
        v = tree.value[:, 0, :]
        return v[:, 1] / v.sum(axis=1)   # class-1 share at every node

    def _saabas(self, X: np.ndarray) -> np.ndarray:
        out = np.zeros_like(X, dtype=float)
        trees = self._trees()
        for est in trees:
            t = est.tree_
            p1 = self._tree_p1(t)
            for i, x in enumerate(X):
                node = 0
                while t.children_left[node] != -1:
                    f = t.feature[node]
                    nxt = t.children_left[node] if x[f] <= t.threshold[node] else t.children_right[node]
                    out[i, f] += p1[nxt] - p1[node]
                    node = nxt
        return out / len(trees)


def ranked_factors(feature_names, values, contributions, describe, top_n: int = 5) -> list[dict]:
    """Turn one row of contributions into ranked, plain-language factors.

    `describe(feature, value, contribution) -> str` gives the English sentence. The
    `text_key` and `params` fields let the UI translate it (Phase 7 i18n).
    """
    order = np.argsort(-np.abs(contributions))
    factors = []
    for j in order[:top_n]:
        f, v, c = feature_names[j], float(values[j]), float(contributions[j])
        factors.append({
            "feature": f,
            "value": v,
            "contribution": round(c, 4),
            "direction": "raises_risk" if c > 0 else "lowers_risk",
            "text": describe(f, v, c),
            "text_key": f"factor.{f}.{'up' if c > 0 else 'down'}",
            "params": {"value": v},
        })
    return factors
