"""SafePay Shield: Flask app factory.

Run:  flask run        (after `python -m safepay.seed`)

Models load once at startup. If the mule model fails validation, the app still
starts, but the mule module reports status "unavailable" with the reason. It is
never silently replaced.
"""
from __future__ import annotations

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

import config
from safepay.api import register_blueprints
from safepay.mule.loader import MuleModelError, load_mule_model
from safepay.mule.predictor import MulePredictor


def create_app(db_path=None) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = str(db_path or config.DB_PATH)
    app.json.ensure_ascii = False      # Hindi and Marathi text in responses

    # ---- models (loaded once)
    try:
        app.config["MULE_MODEL"] = load_mule_model()
        app.config["MULE_MODEL_ERROR"] = None
    except MuleModelError as e:
        app.config["MULE_MODEL"] = None
        app.config["MULE_MODEL_ERROR"] = str(e)
    app.config["MULE_PREDICTOR"] = MulePredictor(app.config["MULE_MODEL"], app.config["MULE_MODEL_ERROR"])
    app.config["COERCION_MODEL"] = None   # trained in Phase 5 (scripts/train_coercion_model.py)

    register_blueprints(app)

    @app.errorhandler(HTTPException)
    def _http_error(e: HTTPException):
        return jsonify(error=e.name, message=e.description, status=e.code), e.code

    @app.errorhandler(Exception)
    def _unexpected(e: Exception):
        app.logger.exception("unhandled error")
        return jsonify(error="Internal Server Error", message=str(e), status=500), 500

    return app


app = create_app()
