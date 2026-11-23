"""Payer behaviour profile and deviation signals.

Built from user_transactions. Deviations add CONTEXT to an explanation; they never
establish coercion on their own (they don't feed the rule score; only the derived
ML inputs below do, as the dataset defines them).

Also derives the payment-relationship and velocity inputs the coercion model needs:
    payee_is_new                first payment to this payee was < NEW_PAYEE_DAYS ago, or never
    new_payee_txns_24h          earlier payments in the last 24 h to payees that were new then
    amount_sent_new_payees_24h  their total
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import config


def load_history(conn, user_id: int, before: datetime) -> pd.DataFrame:
    df = pd.read_sql("SELECT payee_vpa, payee_type, amount, ts, source FROM user_transactions "
                     "WHERE user_id = ? AND ts < ? ORDER BY ts", conn,
                     params=(user_id, before.strftime("%Y-%m-%dT%H:%M:%S")))
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def build_profile(hist: pd.DataFrame) -> dict:
    if hist.empty:
        return {"n_payments": 0}
    hours = hist.ts.dt.hour.value_counts(normalize=True)
    return {
        "n_payments": int(len(hist)),
        "mean_amount": round(float(hist.amount.mean()), 2),
        "median_amount": round(float(hist.amount.median()), 2),
        "std_amount": round(float(hist.amount.std(ddof=0)), 2),
        "max_amount": round(float(hist.amount.max()), 2),
        "usual_hours": sorted(int(h) for h, s in hours.items() if s >= 0.05),
        "usual_payees": sorted(hist.payee_vpa.value_counts().head(5).index.tolist()),
        "p2p_share": round(float((hist.payee_type == "P2P").mean()), 3),
    }


def payee_is_new(hist: pd.DataFrame, vpa: str, now: datetime) -> bool:
    first = hist.loc[hist.payee_vpa == vpa, "ts"].min()
    return bool(pd.isna(first) or first > now - timedelta(days=config.NEW_PAYEE_DAYS))


def velocity(hist: pd.DataFrame, now: datetime) -> dict:
    """Earlier payments in the last 24 h to payees that were new at the time."""
    if hist.empty:
        return {"new_payee_txns_24h": 0, "amount_sent_new_payees_24h": 0.0}
    first_seen = hist.groupby("payee_vpa").ts.min()
    recent = hist[hist.ts >= now - timedelta(hours=24)]
    new_then = recent.apply(lambda r: first_seen[r.payee_vpa] > r.ts - timedelta(days=config.NEW_PAYEE_DAYS),
                            axis=1) if not recent.empty else pd.Series(dtype=bool)
    sel = recent[new_then] if not recent.empty else recent
    return {"new_payee_txns_24h": int(len(sel)), "amount_sent_new_payees_24h": round(float(sel.amount.sum()), 2)}


def deviations(profile: dict, amount: float, hour: int, vpa: str, avg_90d: float, is_new: bool) -> list[dict]:
    out = []
    if avg_90d and amount / avg_90d >= config.UNUSUAL_AMOUNT_RATIO:
        out.append({"id": "amount_vs_usual", "text": f"{amount / avg_90d:.1f}x your usual amount",
                    "text_key": "deviation.amount", "params": {"ratio": round(amount / avg_90d, 1)}})
    if profile.get("n_payments") and hour not in profile["usual_hours"]:
        out.append({"id": "unusual_hour", "text": f"you rarely pay at {hour:02d}:00",
                    "text_key": "deviation.hour", "params": {"hour": hour}})
    if is_new:
        out.append({"id": "new_payee", "text": "first payment to this payee",
                    "text_key": "deviation.new_payee", "params": {}})
    if profile.get("n_payments") and amount > profile["max_amount"]:
        out.append({"id": "largest_ever", "text": "larger than any payment in your history",
                    "text_key": "deviation.largest", "params": {}})
    for d in out:
        d["source"] = "behaviour_profile"
    return out
