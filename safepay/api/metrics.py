"""GET /api/metrics: reads the generated reports in reports/. Nothing is computed on the fly.

Each block names its dataset and caveat. A missing report shows status "not_generated"
with the script that creates it.
"""
import json

from flask import Blueprint, jsonify

import config

bp = Blueprint("metrics", __name__)

REPORTS = {
    "system_evaluation": ("evaluation.json", "scripts/evaluate_system.py"),
    "scenarios": ("scenarios.json", "scripts/run_scenarios.py"),
    "coercion_training": ("coercion_training.json", "scripts/train_coercion_model.py"),
    "mule_rbih": ("rbih_mule_eval.json", "scripts/evaluate_mule_rbih.py"),
    "mule_history_probe": ("mule_history_probe.json", "scripts/probe_mule_history.py"),
}


@bp.get("/metrics")
def metrics():
    out = {}
    for key, (fname, script) in REPORTS.items():
        p = config.REPORTS_DIR / fname
        out[key] = json.loads(p.read_text()) if p.exists() else {"status": "not_generated", "generate_with": script}
    card = config.COERCION_MODEL_CARD
    out["coercion_model_card"] = json.loads(card.read_text()) if card.exists() else {"status": "not_generated"}
    charts = sorted(p.name for p in config.REPORTS_DIR.glob("*.png")) if config.REPORTS_DIR.exists() else []
    return jsonify(reports=out, charts=charts,
                   banner="Synthetic data — feasibility only. Mule metrics on RBIH may be optimistic "
                          "(possible overlap with training data).")
