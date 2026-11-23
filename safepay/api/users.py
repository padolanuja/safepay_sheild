import json

from flask import Blueprint, abort, jsonify, request

import config
from safepay.api import get_db

bp = Blueprint("users", __name__)


def _user_dict(row) -> dict:
    d = dict(row)
    d["usual_active_hours"] = json.loads(d["usual_active_hours"])
    d["data_kind"] = "fictional_demo"
    return d


@bp.get("/users")
def list_users():
    rows = get_db().execute("SELECT * FROM users ORDER BY id").fetchall()
    return jsonify(users=[_user_dict(r) for r in rows])


@bp.get("/users/<int:user_id>")
def get_user(user_id: int):
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        abort(404, description=f"user {user_id} not found")
    return jsonify(_user_dict(row))


@bp.get("/users/<int:user_id>/language")
def get_language(user_id: int):
    row = get_db().execute("SELECT language FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        abort(404, description=f"user {user_id} not found")
    return jsonify(user_id=user_id, language=row["language"])


@bp.put("/users/<int:user_id>/language")
def set_language(user_id: int):
    body = request.get_json(silent=True) or {}
    lang = body.get("language")
    if lang not in config.SUPPORTED_LANGUAGES:
        abort(400, description=f"language must be one of {list(config.SUPPORTED_LANGUAGES)}")
    conn = get_db()
    cur = conn.execute("UPDATE users SET language = ? WHERE id = ?", (lang, user_id))
    if cur.rowcount == 0:
        abort(404, description=f"user {user_id} not found")
    conn.commit()
    return jsonify(user_id=user_id, language=lang)
