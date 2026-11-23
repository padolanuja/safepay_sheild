"""Receiver-side mule risk for one payee.

Result statuses:
    ok                    model called; probability, flag, band and SHAP factors returned
    insufficient_history  fewer than config.MULE_MIN_HISTORY_TXNS transactions; the model is
                          NOT called (too little data for a meaningful score)
    unavailable           the model failed to load or validate; nothing is guessed

Bands (config): low < 0.10 <= elevated < 0.40 <= high.
"""
from __future__ import annotations

import time

import pandas as pd

import config
from safepay.explain.shap_utils import TreeExplainer, ranked_factors
from safepay.mule.features import compute_features

SOURCE = "model_prediction:mule_rf (RBIH-NFPC Phase 2)"

LABELS = {
    "total_transactions": "Number of transactions on this account",
    "total_debit": "Total money sent out",
    "total_credit": "Total money received",
    "avg_transaction_amount": "Average transaction size",
    "unique_counterparties": "Number of different accounts it deals with",
    "upi_transaction_count": "Number of UPI transactions",
    "incoming_counterparties": "Number of different senders",
    "outgoing_counterparties": "Number of different receivers",
    "reversal_count": "Number of reversed transactions",
}
MONEY = {"total_debit", "total_credit", "avg_transaction_amount"}


def _fmt(feature: str, value: float) -> str:
    if feature in MONEY:
        if abs(value) >= 1e7:
            return f"Rs {value / 1e7:.1f} crore"
        if abs(value) >= 1e5:
            return f"Rs {value / 1e5:.1f} lakh"
        return f"Rs {value:,.0f}"
    return f"{value:,.0f}"


def describe(feature: str, value: float, contribution: float) -> str:
    verb = "raises" if contribution > 0 else "lowers"
    return f"{LABELS[feature]}: {_fmt(feature, value)} — {verb} the risk"


def band_for(prob: float) -> str:
    if prob >= config.MULE_HIGH_BAND:
        return "high"
    if prob >= config.MULE_THRESHOLD:
        return "elevated"
    return "low"


class MulePredictor:
    """Holds the validated model and its explainer. Build once at app start."""

    def __init__(self, bundle: dict | None, load_error: str | None = None, force_fallback: bool = False):
        self.bundle = bundle
        self.load_error = load_error
        self.explainer = TreeExplainer(bundle["model"], force_fallback) if bundle else None

    @property
    def available(self) -> bool:
        return self.bundle is not None

    def predict_from_transactions(self, txns: pd.DataFrame, top_n: int = 5) -> dict:
        t0 = time.perf_counter()
        n = int(len(txns))
        base = {"source": SOURCE, "transactions": n, "min_history": config.MULE_MIN_HISTORY_TXNS}

        if not self.available:
            return {**base, "status": "unavailable", "model_called": False,
                    "reason": f"Mule model not available: {self.load_error}",
                    "reason_key": "mule.unavailable"}
        if n < config.MULE_MIN_HISTORY_TXNS:
            return {**base, "status": "insufficient_history", "model_called": False,
                    "reason": (f"This account has only {n} transaction(s); at least "
                               f"{config.MULE_MIN_HISTORY_TXNS} are needed to check how money moves "
                               "through it. The decision will rest on the payment situation only."),
                    "reason_key": "mule.insufficient_history", "reason_params": {"n": n}}

        feats = compute_features(txns)
        X = pd.DataFrame([feats])[config.MULE_FEATURES]
        prob = float(self.bundle["model"].predict_proba(X)[0, 1])
        contrib = self.explainer.contributions(X)[0]
        return {
            **base,
            "status": "ok",
            "model_called": True,
            "mule_probability": round(prob, 4),
            "threshold": self.bundle["threshold"],
            "flag": prob >= self.bundle["threshold"],
            "band": band_for(prob),
            "features": feats,
            "explanation_method": self.explainer.method,
            "base_value": round(self.explainer.base_value, 4),
            "top_factors": ranked_factors(config.MULE_FEATURES, X.iloc[0].to_numpy(), contrib,
                                          describe, top_n),
            # The SHAP base value is about 0.50 (the model used balanced class weights), so
            # any score below 0.50 is mostly made of "lowers the risk" factors. The UI shows
            # this list for a warning: what pushed the score UP.
            "risk_raising_factors": [f for f in ranked_factors(
                config.MULE_FEATURES, X.iloc[0].to_numpy(), contrib, describe, len(contrib))
                if f["contribution"] > 0][:top_n],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def predict_for_payee(self, conn, vpa: str, top_n: int = 5) -> dict:
        """Load the payee's history from SQLite and predict. The caller checks the payee exists."""
        txns = pd.read_sql(
            "SELECT txn_type, amount, counterparty_id, channel FROM payee_transactions WHERE payee_vpa = ?",
            conn, params=(vpa,))
        return {"vpa": vpa, **self.predict_from_transactions(txns, top_n)}
