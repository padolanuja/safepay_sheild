"""Transaction history -> the 9 mule-model features, in the exact training order.

The same function is used for the RBIH evaluation (scripts/evaluate_mule_rbih.py)
and for demo payees in the app, so the two can't drift apart.

Input: one account's transactions as a DataFrame with RBIH-style columns
    txn_type         'D' (debit, money out) or 'C' (credit, money in)
    amount           INR; negative values are reversals (RBIH README)
    counterparty_id  the other party
    channel          RBIH channel code; UPC/UPD are UPI

Formulas (our reconstruction; the original feature-engineering code wasn't
provided, so these follow the RBIH schema and the feature names):
    total_transactions       number of rows
    total_debit              sum of amount where txn_type == 'D' (reversals keep their sign)
    total_credit             sum of amount where txn_type == 'C' (reversals keep their sign)
    avg_transaction_amount   mean of amount over all rows
    unique_counterparties    distinct counterparty_id over all rows
    upi_transaction_count    rows whose channel is in config.UPI_CHANNELS
    incoming_counterparties  distinct counterparty_id on credit rows
    outgoing_counterparties  distinct counterparty_id on debit rows
    reversal_count           rows with amount < 0
"""
from __future__ import annotations

import pandas as pd

import config

REQUIRED_COLUMNS = ("txn_type", "amount", "counterparty_id", "channel")


def compute_features_grouped(txns: pd.DataFrame, key: str = "account_id") -> pd.DataFrame:
    """Compute the 9 features for many accounts at once (one row per `key`)."""
    missing = [c for c in (key, *REQUIRED_COLUMNS) if c not in txns.columns]
    if missing:
        raise ValueError(f"transactions are missing required columns: {missing}")
    t = txns[[key, *REQUIRED_COLUMNS]].copy()
    is_d = t["txn_type"] == "D"
    is_c = t["txn_type"] == "C"
    t["_debit"] = t["amount"].where(is_d, 0.0)
    t["_credit"] = t["amount"].where(is_c, 0.0)
    t["_upi"] = t["channel"].isin(config.UPI_CHANNELS).astype(int)
    t["_rev"] = (t["amount"] < 0).astype(int)
    t["_cp_in"] = t["counterparty_id"].where(is_c)
    t["_cp_out"] = t["counterparty_id"].where(is_d)
    g = t.groupby(key, sort=True)
    out = pd.DataFrame({
        "total_transactions": g.size(),
        "total_debit": g["_debit"].sum(),
        "total_credit": g["_credit"].sum(),
        "avg_transaction_amount": g["amount"].mean(),
        "unique_counterparties": g["counterparty_id"].nunique(),
        "upi_transaction_count": g["_upi"].sum(),
        "incoming_counterparties": g["_cp_in"].nunique(),
        "outgoing_counterparties": g["_cp_out"].nunique(),
        "reversal_count": g["_rev"].sum(),
    })
    return out[config.MULE_FEATURES]


def compute_features(txns: pd.DataFrame) -> dict:
    """Compute the 9 features for ONE account. Returns {feature: value} in training order."""
    if len(txns) == 0:
        raise ValueError("no transactions: the caller must check MULE_MIN_HISTORY_TXNS first")
    t = txns.assign(account_id="_one")
    row = compute_features_grouped(t).iloc[0]
    return {f: float(row[f]) for f in config.MULE_FEATURES}
