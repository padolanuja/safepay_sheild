"""Translations: en, hi, mr. Every warning, question, reason and button label lives in the
JSON files next to this module. Hindi and Marathi text should be reviewed by a native speaker
before real use (see README).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import config

DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def strings(lang: str) -> dict:
    if lang not in config.SUPPORTED_LANGUAGES:
        raise KeyError(f"unsupported language {lang}")
    return json.loads((DIR / f"{lang}.json").read_text(encoding="utf-8"))


def t(lang: str, key: str, **params) -> str:
    """Look up a key, falling back to English, then to the key itself."""
    s = strings(lang).get(key) or strings("en").get(key) or key
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s
