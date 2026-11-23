"""Payer-side ML coercion model: shared feature encoding, loading, prediction, SHAP.

Two stages, two models (see docs/ARCHITECTURE.md 2.2):
    pre_pause   payer-side signals only (no pause answers exist yet)
    post_pause  the same + one-hot pause answers (ans_*)
Neither model sees payee_vpa_age_days or payee_prior_complaints: the payee side is
the mule model's job, so the two models stay independent.

Missing inputs are never filled in: predict() returns status "unavailable" with
the names of the missing fields.
"""
from __future__ import annotations

import json
import time

import joblib
import numpy as np
import pandas as pd

import config
from safepay.explain.shap_utils import TreeExplainer, ranked_factors

NUMERIC = [
    "hour_of_day", "user_age", "account_tenure_months", "avg_txn_amount_90d", "balance_before_txn",
    "amount_inr", "amount_to_avg_ratio", "amount_pct_of_balance", "payee_is_new", "payee_in_contacts",
    "on_voice_call", "on_video_call", "call_duration_min", "caller_in_contacts", "caller_is_international",
    "screen_share_active", "remote_access_app_installed_7d", "session_duration_sec", "hesitation_events",
    "segmented_typing", "app_switches", "vpa_pasted", "new_payee_txns_24h", "amount_sent_new_payees_24h",
    "fd_or_loan_liquidated_7d",
]
PAYEE_SIDE = ["payee_vpa_age_days", "payee_prior_complaints"]       # comparison variant only
CATEGORIES = {
    "ans_know_payee": ["no", "unsure", "yes"],
    "ans_someone_guiding": ["no", "yes"],
    "ans_purpose": ["bills_shopping", "family", "govt_fine_verification", "investment", "job_task_fee",
                    "loan_repayment", "medical", "other", "refund_cashback", "rent_fees"],
}
# "Unknown payee or high amount" features. When no pressure indicator fired, their SHAP
# contribution is removed from the ML score (the pressure gate, see assess.py), so they
# can never establish coercion on their own.
# The "do you know this person" answer is itself an unknown-payee signal, so it belongs here too.
PAYEE_AMOUNT_FEATURES = {"amount_inr", "amount_to_avg_ratio", "amount_pct_of_balance", "payee_is_new",
                         "payee_in_contacts", "payee_type_P2M", "avg_txn_amount_90d", "balance_before_txn",
                         "ans_know_payee_no", "ans_know_payee_unsure", "ans_know_payee_yes"}
NEVER_TRAIN = ["txn_id", "scam_type", "sample_group", "hard_negative_reason", "label_noisy", "label_coerced"]


def raw_inputs(stage: str, with_payee: bool = False) -> list[str]:
    cols = NUMERIC + ["payee_type"] + (PAYEE_SIDE if with_payee else [])
    return cols + (list(CATEGORIES) if stage == "post_pause" else [])


def encode(df: pd.DataFrame, stage: str, with_payee: bool = False) -> pd.DataFrame:
    """Raw columns -> model matrix with a fixed column set and order."""
    X = df[NUMERIC + (PAYEE_SIDE if with_payee else [])].astype(float).copy()
    if not df["payee_type"].isin(["P2P", "P2M"]).all():
        raise ValueError("payee_type must be P2P or P2M")
    X["payee_type_P2M"] = (df["payee_type"] == "P2M").astype(float)
    if stage == "post_pause":
        for col, cats in CATEGORIES.items():
            bad = set(df[col]) - set(cats)
            if bad:
                raise ValueError(f"{col} has unknown values {sorted(bad)}")
            for cat in cats:
                X[f"{col}_{cat}"] = (df[col] == cat).astype(float)
    return X


# ---------------------------------------------------------------- plain language
LABELS = {
    "hour_of_day": "Time of day", "user_age": "Payer's age", "account_tenure_months": "Account age",
    "avg_txn_amount_90d": "Usual payment size", "balance_before_txn": "Balance",
    "amount_inr": "Amount", "amount_to_avg_ratio": "Amount compared with usual",
    "amount_pct_of_balance": "Share of balance", "payee_is_new": "New payee",
    "payee_in_contacts": "Payee is a saved contact", "on_voice_call": "On a voice call",
    "on_video_call": "On a video call", "call_duration_min": "Call length (min)",
    "caller_in_contacts": "Caller is a saved contact", "caller_is_international": "International caller",
    "screen_share_active": "Screen sharing on", "remote_access_app_installed_7d": "Remote-access app installed recently",
    "session_duration_sec": "Time spent on the payment screen (s)", "hesitation_events": "Hesitation pauses",
    "segmented_typing": "Stop-start typing", "app_switches": "App switches", "vpa_pasted": "UPI ID pasted",
    "new_payee_txns_24h": "Payments to new payees in 24 h", "amount_sent_new_payees_24h": "Sent to new payees in 24 h",
    "fd_or_loan_liquidated_7d": "FD broken / loan taken this week", "payee_type_P2M": "Paying a merchant",
}


def describe(feature: str, value: float, contribution: float) -> str:
    verb = "raises" if contribution > 0 else "lowers"
    for col, label in ANSWER_LABELS.items():
        if feature.startswith(col + "_"):
            cat = feature[len(col) + 1:].replace("_", " ")
            return f"{label}: {cat if value else 'not ' + cat} — {verb} the risk"
    v = ("yes" if value else "no") if feature in BINARY else f"{value:,.0f}" if abs(value) >= 10 else f"{value:g}"
    return f"{LABELS.get(feature, feature)}: {v} — {verb} the risk"


ANSWER_LABELS = {"ans_know_payee": "Answer 'do you know this person'",
                 "ans_someone_guiding": "Answer 'is someone guiding you'",
                 "ans_purpose": "Stated purpose"}
BINARY = {"payee_is_new", "payee_in_contacts", "on_voice_call", "on_video_call", "caller_in_contacts",
          "caller_is_international", "screen_share_active", "remote_access_app_installed_7d",
          "segmented_typing", "vpa_pasted", "fd_or_loan_liquidated_7d", "payee_type_P2M"}


# ---------------------------------------------------------------------- runtime
class CoercionModel:
    """Both stage models plus the validated cut-offs from the model card."""

    def __init__(self):
        self.models, self.explainers, self.error = {}, {}, None
        try:
            self.card = json.loads(config.COERCION_MODEL_CARD.read_text())
            for stage, path in config.COERCION_MODEL_PATHS.items():
                b = joblib.load(path)
                if b["stage"] != stage or b["with_payee"]:
                    raise ValueError(f"{path.name}: expected payer-only {stage} model")
                self.models[stage] = b
                self.explainers[stage] = TreeExplainer(b["model"])
        except Exception as e:  # noqa: BLE001 - any failure -> unavailable, never a guess
            self.models, self.error = {}, f"coercion model not available: {e}"

    @property
    def available(self) -> bool:
        return bool(self.models)

    def cutoffs(self, stage: str) -> dict:
        return self.card["cutoffs"][stage]

    def predict(self, ctx: dict, stage: str, top_n: int = 6) -> dict:
        t0 = time.perf_counter()
        if not self.available:
            return {"status": "unavailable", "reason": self.error}
        needed = raw_inputs(stage)
        missing = [k for k in needed if ctx.get(k) is None]
        if missing:
            return {"status": "unavailable", "reason": f"missing inputs: {missing}", "missing": missing}
        b = self.models[stage]
        X = encode(pd.DataFrame([{k: ctx[k] for k in needed}]), stage)[b["features"]]
        prob = float(b["model"].predict_proba(X)[0, 1])
        ex = self.explainers[stage]
        contrib = ex.contributions(X)[0]
        factors = ranked_factors(b["features"], X.iloc[0].to_numpy(), contrib, describe, len(contrib))

        # Share of the model's risk-raising evidence (positive SHAP) that comes from payee/amount
        # features. Unit-free, so it works for both the RF (probability) and XGBoost (log-odds).
        # Used by the pressure gate in assess.py.
        pos = np.clip(contrib, 0, None)
        pa = float(sum(c for f, c in zip(b["features"], pos) if f in PAYEE_AMOUNT_FEATURES))
        share = pa / float(pos.sum()) if pos.sum() > 0 else 0.0
        return {
            "status": "ok", "stage": stage, "model_kind": b["kind"],
            "source": "model_prediction:coercion_" + b["kind"] + " (synthetic data — feasibility only)",
            "ml_probability": round(prob, 4),
            "payee_amount_share_of_risk": round(share, 4),
            "explanation_method": ex.method,
            "top_factors": factors[:top_n],
            "risk_raising_factors": [f for f in factors if f["contribution"] > 0][:top_n],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
