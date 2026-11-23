"""Load and validate the provided mule model (read-only; never retrained).

The pkl is a joblib dict {model, features, threshold}. We refuse to use it unless:
  * the model is a fitted classifier with classes [0, 1],
  * the stored feature list equals config.MULE_FEATURES, and
  * model.feature_names_in_ has the same names in the same order.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import joblib

import config


class MuleModelError(RuntimeError):
    """Raised when the mule model is missing or doesn't match the expected contract."""


def load_mule_model(path: Path | None = None) -> dict:
    path = Path(path or config.MULE_MODEL_PATH)
    if not path.exists():
        raise MuleModelError(f"mule model not found: {path}")
    with warnings.catch_warnings():
        # A version mismatch must stop us, not just print a warning.
        warnings.simplefilter("error", category=UserWarning)
        try:
            bundle = joblib.load(path)
        except UserWarning as w:  # sklearn InconsistentVersionWarning subclasses UserWarning
            raise MuleModelError(f"scikit-learn version mismatch while loading {path.name}: {w}") from w

    if not isinstance(bundle, dict) or not {"model", "features", "threshold"} <= set(bundle):
        raise MuleModelError("pkl must be a dict with keys model, features, threshold")
    model, feats = bundle["model"], list(bundle["features"])
    names_in = list(getattr(model, "feature_names_in_", []))
    if feats != config.MULE_FEATURES:
        raise MuleModelError(f"stored feature list {feats} != config.MULE_FEATURES")
    if names_in != config.MULE_FEATURES:
        raise MuleModelError(f"model.feature_names_in_ {names_in} != config.MULE_FEATURES (order matters)")
    if list(getattr(model, "classes_", [])) != [0, 1]:
        raise MuleModelError(f"expected classes [0, 1], got {getattr(model, 'classes_', None)}")
    if float(bundle["threshold"]) != config.MULE_THRESHOLD:
        raise MuleModelError(f"pkl threshold {bundle['threshold']} != config.MULE_THRESHOLD")
    return {"model": model, "features": feats, "threshold": float(bundle["threshold"])}
