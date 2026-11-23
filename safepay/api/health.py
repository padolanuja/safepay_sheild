from pathlib import Path

from flask import Blueprint, current_app, jsonify

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    cfg = current_app.config
    mule_ok = cfg.get("MULE_MODEL") is not None
    cm = cfg.get("COERCION_MODEL")
    coercion_ok = cm is not None and cm.available
    return jsonify(
        status="ok" if mule_ok and coercion_ok else "degraded",
        database=Path(cfg["DB_PATH"]).exists(),
        models={
            "mule": {"loaded": mule_ok, "error": cfg.get("MULE_MODEL_ERROR")},
            "coercion": {"loaded": coercion_ok,
                         "error": None if coercion_ok else (cm.error if cm else "not configured"),
                         "stages": sorted(cm.models) if coercion_ok else []},
        },
        simulation=True,
    )
