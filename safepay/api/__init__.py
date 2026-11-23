"""API blueprints. Each module owns one resource. More are added in Phase 6."""
from flask import Flask, current_app, g

from safepay import db


def get_db():
    """One SQLite connection per request, closed on teardown."""
    if "db" not in g:
        g.db = db.connect(current_app.config["DB_PATH"])
    return g.db


def _close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def register_blueprints(app: Flask) -> None:
    from safepay.api import (assess, feedback, health, history, metrics, mule, payees, payments,
                             scenarios, trust, users)

    app.teardown_appcontext(_close_db)
    for bp in (health.bp, users.bp, payees.bp, mule.bp, assess.bp, payments.bp, history.bp, trust.bp,
               feedback.bp, scenarios.bp, metrics.bp):
        app.register_blueprint(bp, url_prefix="/api")
