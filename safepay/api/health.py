from pathlib import Path

from flask import Blueprint, current_app, jsonify

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    cfg = current_app.config
    mule_ok = cfg.get("MULE_MODEL") is not None
    coercion_ok = cfg.get("COERCION_MODEL") is not None
    return jsonify(
        status="ok" if mule_ok else "degraded",
        database=Path(cfg["DB_PATH"]).exists(),
        models={
            "mule": {"loaded": mule_ok, "error": cfg.get("MULE_MODEL_ERROR")},
            "coercion": {"loaded": coercion_ok,
                         "error": None if coercion_ok else "not trained yet (Phase 5)"},
        },
        simulation=True,
    )
