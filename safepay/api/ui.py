"""Page routes for the frontend, translations and generated report images."""
from flask import Blueprint, abort, jsonify, render_template, send_from_directory

import config
from safepay.i18n import strings

bp = Blueprint("ui", __name__)        # registered without the /api prefix
api_bp = Blueprint("i18n", __name__)  # registered under /api

PAGES = {"pay": "pay.html", "dashboard": "dashboard.html", "evaluation": "evaluation.html", "settings": "settings.html", "lab": "lab.html"}


@bp.get("/")
def pay():
    return render_template("pay.html", page="pay")


@bp.get("/<page>")
def page(page):
    if page not in PAGES or page == "pay":
        abort(404, description="page not found")
    return render_template(PAGES[page], page=page)


@bp.get("/reports/<name>")
def report_file(name):
    """Serve generated chart images only (no JSON/CSV, no paths)."""
    if not name.endswith(".png") or "/" in name or "\\" in name or ".." in name:
        abort(404, description="not found")
    return send_from_directory(config.REPORTS_DIR, name)


@api_bp.get("/i18n/<lang>")
def i18n(lang):
    if lang not in config.SUPPORTED_LANGUAGES:
        abort(404, description=f"language must be one of {list(config.SUPPORTED_LANGUAGES)}")
    return jsonify(strings(lang))
