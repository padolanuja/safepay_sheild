"""Multiple-transaction and splitting detection.

Looks at the payer's earlier payments plus the current one:
  near_limit_split  >= SPLIT_MIN_TXNS payments within SPLIT_WINDOW_MIN minutes, each just
                    under a round limit (within NEAR_LIMIT_PCT%, e.g. 9,999 / 19,999 / 49,999),
                    to the same payee or to new payees
  rapid_repeat      >= SPLIT_MIN_TXNS payments to the same payee within SPLIT_WINDOW_MIN minutes
  escalation        >= SPLIT_MIN_TXNS payments to the same payee within ESCALATION_WINDOW_H
                    hours, each larger than the one before (investment and job-scam pattern)
Returns the patterns found, the number of payments involved and their total, and how
the total compares with the payer's usual payment.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

import config


def _near_limit(a: float) -> bool:
    return any(lim * (1 - config.NEAR_LIMIT_PCT / 100) <= a < lim for lim in config.ROUND_LIMITS)


def detect(hist: pd.DataFrame, vpa: str, amount: float, now: datetime, avg_90d: float,
           new_payees: set[str]) -> dict:
    cur = pd.DataFrame([{"payee_vpa": vpa, "amount": amount, "ts": now}])
    h = pd.concat([hist[["payee_vpa", "amount", "ts"]], cur], ignore_index=True)
    win = h[h.ts >= now - timedelta(minutes=config.SPLIT_WINDOW_MIN)]
    day = h[h.ts >= now - timedelta(hours=config.ESCALATION_WINDOW_H)]
    same_win = win[win.payee_vpa == vpa]
    same_day = day[day.payee_vpa == vpa].sort_values("ts")

    patterns, involved = [], pd.DataFrame(columns=h.columns)
    near = win[win.amount.map(_near_limit) & ((win.payee_vpa == vpa) | win.payee_vpa.isin(new_payees | {vpa}))]
    if _near_limit(amount) and len(near) >= config.SPLIT_MIN_TXNS:
        patterns.append({"id": "near_limit_split",
                         "text": f"{len(near)} payments just under a round limit in {config.SPLIT_WINDOW_MIN} min"})
        involved = pd.concat([involved, near])
    if len(same_win) >= config.SPLIT_MIN_TXNS:
        patterns.append({"id": "rapid_repeat",
                         "text": f"{len(same_win)} payments to this payee in {config.SPLIT_WINDOW_MIN} min"})
        involved = pd.concat([involved, same_win])
    amounts = same_day.amount.tolist()
    if len(amounts) >= config.SPLIT_MIN_TXNS and all(b > a for a, b in zip(amounts, amounts[1:])):
        patterns.append({"id": "escalation",
                         "text": "rising amounts to this payee today: " + ", ".join(f"Rs {a:,.0f}" for a in amounts)})
        involved = pd.concat([involved, same_day])

    involved = involved.drop_duplicates()
    total = float(involved.amount.sum()) if len(involved) else 0.0
    return {
        "detected": bool(patterns),
        "patterns": patterns,
        "transactions": int(len(involved)),
        "total_amount": round(total, 2),
        "total_vs_usual": round(total / avg_90d, 1) if avg_90d and total else None,
        "source": "rule:splitting",
    }
