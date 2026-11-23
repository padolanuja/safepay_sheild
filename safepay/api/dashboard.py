"""GET /api/dashboard/summary?user_id=   aggregates over risk_assessments (demo activity).

Counts final decisions per payment flow: when a post-pause assessment exists, the flow
is counted once, using its final decision.
"""
import json
from collections import Counter

from flask import Blueprint, jsonify, request

from safepay.api import get_db

bp = Blueprint("dashboard", __name__)


@bp.get("/dashboard/summary")
def summary():
    uid = request.args.get("user_id", type=int)
    q = "SELECT id, created_at, stage, decision, coercion_json, latency_ms FROM risk_assessments"
    rows = get_db().execute(q + (" WHERE user_id = ?" if uid else "") + " ORDER BY id", (uid,) if uid else ())
    by_decision, by_day, indicators, lat, n = Counter(), {}, Counter(), [], 0
    for r in rows:
        n += 1
        lat.append(r["latency_ms"])
        by_decision[r["decision"]] += 1
        day = r["created_at"][:10]
        by_day.setdefault(day, Counter())[r["decision"]] += 1
        for i in json.loads(r["coercion_json"]).get("indicators", []):
            if i.get("counted"):
                indicators[i["id"]] += 1
    return jsonify(
        assessments=n,
        by_decision={d: by_decision.get(d, 0) for d in ("ALLOW", "WARN", "HOLD")},
        by_day=[{"day": d, **{k: c.get(k, 0) for k in ("ALLOW", "WARN", "HOLD")}} for d, c in sorted(by_day.items())],
        top_indicators=[{"id": k, "count": v} for k, v in indicators.most_common(8)],
        avg_latency_ms=round(sum(lat) / len(lat), 1) if lat else None,
        note="Demo activity on fictional users. Counts every assessment (first pass and after the questions).",
    )
