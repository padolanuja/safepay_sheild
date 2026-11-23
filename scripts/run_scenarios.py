"""Run all 13 demo scenarios through the full two-stage flow and check them against their
expected decisions.

Usage:  python scripts/run_scenarios.py        (uses data/safepay.db; run `python -m safepay.seed` first)
Writes reports/scenarios.json. Exit code 1 if any scenario's decision isn't one it expects.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from safepay import db  # noqa: E402
from safepay.coercion.model import CoercionModel  # noqa: E402
from safepay.mule.loader import load_mule_model  # noqa: E402
from safepay.mule.predictor import MulePredictor  # noqa: E402
from safepay.scenarios import run_scenario  # noqa: E402

import config  # noqa: E402


def stage_summary(x):
    if x is None:
        return None
    return {"decision": x["decision"], "matrix_row": x["fusion"]["matrix_row"],
            "coercion_band": x["coercion"]["band"], "coercion_score": x["coercion"]["coercion_score"],
            "severe": x["coercion"]["severe"], "payee_amount_gate": x["coercion"]["payee_amount_gate"],
            "mule_status": x["mule"]["status"], "mule_band": x["mule"].get("band"),
            "splitting": [p["id"] for p in x["behaviour"]["splitting"]["patterns"]],
            "trust_applied": x["fusion"]["trust_applied"], "reason": x["explanation"]["reason"],
            "latency_ms": x["latency_ms"]}


def main():
    conn = db.connect()
    mp, cm = MulePredictor(load_mule_model()), CoercionModel()
    if not cm.available:
        sys.exit(cm.error)
    rows = []
    for (sid,) in conn.execute("SELECT id FROM scenarios ORDER BY id").fetchall():
        r = run_scenario(conn, mp, cm, sid)
        rows.append({"id": sid, "title": r["title"], "expected": r["expected"], "actual": r["actual"],
                     "passed": r["passed"], "expected_note": r["expected_note"],
                     "asked_questions": r["flow"]["asked_questions"],
                     "pre_pause": stage_summary(r["flow"]["pre"]), "post_pause": stage_summary(r["flow"]["post"])})
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {sid:>2}  {r['title'][:50]:<50} expected {'/'.join(r['expected']):<10} "
              f"got {r['actual']:<5}  ({rows[-1]['pre_pause']['matrix_row']}"
              f"{' -> ' + rows[-1]['post_pause']['matrix_row'] if rows[-1]['post_pause'] else ''})")
    conn.close()
    passed = sum(r["passed"] for r in rows)
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "data": "fictional demo users; payees partly derived from RBIH accounts (pseudonymised)",
           "passed": passed, "total": len(rows), "scenarios": rows}
    config.REPORTS_DIR.mkdir(exist_ok=True)
    (config.REPORTS_DIR / "scenarios.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\n{passed}/{len(rows)} scenarios match their expected decision")
    sys.exit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
