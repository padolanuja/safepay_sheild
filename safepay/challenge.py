"""Blind Test Lab: score payments the models have NEVER seen, live, in front of the judges.

Why this exists. A demo driven by hand-picked presets invites the fair question "did you just
pick values that give the answer you wanted?". This module answers it with data the team did
not choose:

* Rows come from the 30% HELD-OUT TEST split of the synthetic coercion dataset (same split and
  seed as scripts/train_coercion_model.py, so no row here was used to fit the models).
* Each row is paired with a payee profile exactly as scripts/evaluate_system.py does
  (no history / real RBIH clean account / real RBIH mule account).
* The draw is random per click (the seed is returned so any draw can be replayed).
* The ground-truth label is hidden until after the decision is made.
* Scoring uses the SAME functions as /api/assess: CoercionModel, score_coercion() and the
  fusion engine's decide(). Only the database context is replaced by the row's own values.

Nothing here writes to the database.
"""
from __future__ import annotations

import threading
import zipfile

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

import config
from safepay.assess import score_coercion
from safepay.fusion.engine import LEVEL, ORDER, decide

PAIRING = {  # (coerced, payee_is_new) -> P(new_no_history, rbih_legit, rbih_mule); from evaluate_system.py
    (1, 1): (0.60, 0.25, 0.15), (1, 0): (0.0, 0.80, 0.20),
    (0, 1): (0.15, 0.80, 0.05), (0, 0): (0.0, 0.97, 0.03),
}
SIGNALS = ["hour_of_day", "user_age", "amount_inr", "amount_to_avg_ratio", "amount_pct_of_balance", "payee_is_new",
           "payee_in_contacts", "payee_type", "on_voice_call", "on_video_call", "call_duration_min",
           "caller_in_contacts", "caller_is_international", "screen_share_active", "remote_access_app_installed_7d",
           "session_duration_sec", "hesitation_events", "segmented_typing", "app_switches", "vpa_pasted",
           "new_payee_txns_24h", "amount_sent_new_payees_24h", "fd_or_loan_liquidated_7d",
           "ans_know_payee", "ans_someone_guiding", "ans_purpose"]

_lock = threading.Lock()
_cache: dict = {}


class LabUnavailable(RuntimeError):
    pass


def _band_for(p: float) -> str:
    return "high" if p >= config.MULE_HIGH_BAND else "elevated" if p >= config.MULE_THRESHOLD else "low"


def _load_csv() -> pd.DataFrame:
    if not config.COERCION_CSV.exists():
        z = config.BASE_DIR / "safepay_coercion_dataset.zip"
        if not z.exists():
            raise LabUnavailable("coercion dataset not found (data/coercion/ or safepay_coercion_dataset.zip)")
        config.COERCION_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            member = next(n for n in zf.namelist() if n.endswith("safepay_coercion_synthetic.csv"))
            config.COERCION_CSV.write_bytes(zf.read(member))
    return pd.read_csv(config.COERCION_CSV)


def test_pool() -> dict:
    """The held-out test split plus the RBIH payee pools. Built once, then cached."""
    with _lock:
        if not _cache:
            df = _load_csv()
            train, te = train_test_split(df, test_size=0.30, stratify=df.label_coerced,
                                         random_state=config.RANDOM_SEED)
            rbih = pd.read_csv(config.REPORTS_DIR / "rbih_mule_eval_sample.csv")
            rbih = rbih[rbih.total_transactions >= config.MULE_MIN_HISTORY_TXNS]
            _cache.update(
                test=te.reset_index(drop=True),
                train_ids=set(train.txn_id),
                legit=rbih[rbih.is_mule == 0].reset_index(drop=True),
                mules=rbih[rbih.is_mule == 1].reset_index(drop=True),
                total_rows=len(df),
            )
        return _cache


def _pair_payees(rows: pd.DataFrame, rng: np.random.Generator, pool: dict) -> pd.DataFrame:
    rows = rows.copy()
    kinds, probs, accts = [], [], []
    for r in rows.itertuples():
        kind = rng.choice(["new_no_history", "rbih_legit", "rbih_mule"], p=PAIRING[(int(r.label_coerced), int(r.payee_is_new))])
        if kind == "new_no_history":
            prob, acct = np.nan, None
        else:
            src = pool["legit"] if kind == "rbih_legit" else pool["mules"]
            a = src.iloc[int(rng.integers(len(src)))]
            prob, acct = float(a.mule_probability), str(a.account_id)
        kinds.append(kind); probs.append(prob); accts.append(acct)
    rows["payee_profile"], rows["mule_probability"], rows["rbih_account"] = kinds, probs, accts
    rows["should_intervene"] = ((rows.label_coerced == 1) | (rows.payee_profile == "rbih_mule")).astype(int)
    return rows


def _ml_with_needed_shap(rows: pd.DataFrame, records: list[dict], cm, stage: str) -> list[dict]:
    """ML probability for every row, plus the SHAP payee/amount share where it can matter.

    The share is only read by score_coercion's pressure gate, and the gate only changes the
    result when (a) no pressure indicator fired and (b) the ML probability is above the rule
    score (coercion_score = max(rule, ml)). Exact SHAP on the 300-tree pre-pause forest costs
    ~75 ms a row, so it is computed for exactly those rows. Every other row gets share 0.0,
    which gives the identical score, band and decision. Same model, same formula as predict().
    """
    from safepay.coercion.model import encode
    b = cm.models[stage]
    prob = b["model"].predict_proba(encode(rows, stage)[b["features"]])[:, 1]
    out = [{"ml_probability": float(p), "payee_amount_share_of_risk": 0.0} for p in prob]
    card = cm.card
    need = []
    for i, row in enumerate(records):
        probe = score_coercion({**row, "stage": stage, "checklist": []}, out[i], card, stage)
        if not probe["pressure_present"] and out[i]["ml_probability"] > probe["rule_score_normalised"]:
            need.append(i)
    cache = _share_cache()
    missing = [i for i in need if (records[i]["txn_id"], stage) not in cache]
    if missing:
        exact = cm.predict_batch(rows.iloc[missing], stage)
        fresh = {(records[i]["txn_id"], stage): float(exact.iloc[j]["payee_amount_share_of_risk"])
                 for j, i in enumerate(missing)}
        _store_shares(fresh)
    for i in need:
        out[i]["payee_amount_share_of_risk"] = cache[(records[i]["txn_id"], stage)]
    return out


# ---------------------------------------------------------------- SHAP share cache
# Exact SHAP on the 300-tree pre-pause forest costs ~0.1-0.2 s a row, so a 500-row stress test
# would take over a minute. The share is a deterministic function of (row, model), so it is
# computed once per held-out row and kept in data/lab/shap_share_cache.csv
# (build it with scripts/build_lab_cache.py). Rows not in the cache are computed live and added.
# Probability, rules and the fusion decision are always computed live.
SHARE_CACHE = config.DATA_DIR / "lab" / "shap_share_cache.csv"
_shares: dict | None = None


def _share_cache() -> dict:
    global _shares
    with _lock:
        if _shares is None:
            _shares = {}
            if SHARE_CACHE.exists():
                c = pd.read_csv(SHARE_CACHE)
                _shares = {(t, s): float(v) for t, s, v in zip(c.txn_id, c.stage, c.payee_amount_share_of_risk)}
        return _shares


def _store_shares(fresh: dict) -> None:
    cache = _share_cache()
    with _lock:
        cache.update(fresh)
        SHARE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        new_file = not SHARE_CACHE.exists()
        pd.DataFrame([{"txn_id": t, "stage": s, "payee_amount_share_of_risk": round(v, 6)}
                      for (t, s), v in fresh.items()]).to_csv(SHARE_CACHE, mode="a", header=new_file, index=False)


def warm_cache(cm, progress=print) -> int:
    """Compute the SHAP share for every held-out row that needs it (scripts/build_lab_cache.py)."""
    te = test_pool()["test"]
    done = 0
    for start in range(0, len(te), 200):
        rows = te.iloc[start:start + 200].reset_index(drop=True)
        rec = rows.to_dict("records")
        for stage in ("pre_pause", "post_pause"):
            _ml_with_needed_shap(rows, rec, cm, stage)
        done += len(rows)
        progress(f"{done}/{len(te)} rows")
    return len(_share_cache())


def _score(rows: pd.DataFrame, cm) -> list[dict]:
    """Same pipeline as the evaluation and as /api/assess, one result per row."""
    card = cm.card
    records = rows.to_dict("records")
    ml = {s: _ml_with_needed_shap(rows, records, cm, s) for s in ("pre_pause", "post_pause")}
    no_split = {"detected": False, "patterns": []}
    no_trust = {"trusted_beneficiary": False, "planned_payment_match": False}
    out = []
    for i, (row, r) in enumerate(zip(records, rows.itertuples())):
        if r.payee_profile == "new_no_history":
            mule = {"status": "insufficient_history"}
        else:
            mule = {"status": "ok", "mule_probability": r.mule_probability, "band": _band_for(r.mule_probability)}
        unusual = {"detected": bool(row["payee_is_new"] and row["amount_to_avg_ratio"] >= config.LARGE_AMOUNT_RATIO)}
        stages = {}
        for stage in ("pre_pause", "post_pause"):
            m = {"status": "ok", **ml[stage][i]}
            coer = score_coercion({**row, "stage": stage, "checklist": []}, m, card, stage)
            fus = decide(mule, coer, no_split, no_trust, unusual, stage)
            stages[stage] = {"decision": fus["decision"], "matrix_row": fus["matrix_row"],
                             "coercion_score": round(float(coer["coercion_score"]), 4), "band": coer["band"],
                             "ml_probability": round(float(m["ml_probability"]), 4), "severe": coer["severe"],
                             "indicators": [{"id": x["id"], "evidence": x["evidence"], "severity": x["severity"]}
                                            for x in coer["indicators"] if x["counted"]]}
        pre = stages["pre_pause"]["decision"]
        final = pre if pre == "ALLOW" else ORDER[max(LEVEL[pre], LEVEL[stages["post_pause"]["decision"]])]
        out.append({
            "txn_id": row["txn_id"],
            "signals": {k: (row[k].item() if hasattr(row[k], "item") else row[k]) for k in SIGNALS},
            "mule": {**mule, "rbih_account": None if pd.isna(r.rbih_account) else str(r.rbih_account),
                     "mule_probability": None if pd.isna(r.mule_probability) else round(float(r.mule_probability), 4)},
            "pre": stages["pre_pause"], "post": stages["post_pause"] if pre != "ALLOW" else None,
            "final_decision": final,
            "truth": {"label_coerced": int(r.label_coerced), "scam_type": r.scam_type, "sample_group": r.sample_group,
                      "hard_negative_reason": r.hard_negative_reason, "payee_profile": r.payee_profile,
                      "should_intervene": int(r.should_intervene)},
            "correct": (final != "ALLOW") == bool(r.should_intervene),
        })
    return out


def draw(cm, n: int, seed: int | None = None, balanced: bool = False) -> dict:
    """Draw n random held-out rows and score them. Returns per-row results + live metrics."""
    if not cm.available:
        raise LabUnavailable(cm.error or "coercion model unavailable")
    pool = test_pool()
    seed = int(np.random.default_rng().integers(1, 2**31 - 1)) if seed is None else int(seed)
    rng = np.random.default_rng(seed)
    te = pool["test"]
    if balanced:   # half coerced, half genuine: makes a single draw a fair coin for the audience
        half = max(1, n // 2)
        pos = te[te.label_coerced == 1].sample(n=min(half, (te.label_coerced == 1).sum()), random_state=rng)
        neg = te[te.label_coerced == 0].sample(n=min(n - half, (te.label_coerced == 0).sum()), random_state=rng)
        rows = pd.concat([pos, neg]).sample(frac=1, random_state=rng)
    else:
        rows = te.sample(n=min(n, len(te)), random_state=rng)
    leaked = int(rows.txn_id.isin(pool["train_ids"]).sum())   # must always be 0: proves the split
    results = _score(_pair_payees(rows.reset_index(drop=True), rng, pool), cm)
    return {"simulated": True, "seed": seed, "n": len(results), "balanced": balanced,
            "source": "30% held-out test split of the synthetic coercion dataset (seed 42 split) + RBIH payee profiles",
            "rows_in_training_set": leaked, "test_pool_size": len(te), "dataset_rows": pool["total_rows"],
            "metrics": metrics(results), "results": results}


def metrics(results: list[dict]) -> dict:
    tp = sum(1 for r in results if r["final_decision"] != "ALLOW" and r["truth"]["should_intervene"])
    fp = sum(1 for r in results if r["final_decision"] != "ALLOW" and not r["truth"]["should_intervene"])
    fn = sum(1 for r in results if r["final_decision"] == "ALLOW" and r["truth"]["should_intervene"])
    tn = len(results) - tp - fp - fn
    hold_tp = sum(1 for r in results if r["final_decision"] == "HOLD" and r["truth"]["should_intervene"])
    hn = [r for r in results if r["truth"]["sample_group"] == "hard_negative"]
    pos = tp + fn
    neg = fp + tn
    rule = [bool(r["signals"]["payee_is_new"] and r["signals"]["amount_to_avg_ratio"] >= 5) for r in results]
    b_tp = sum(1 for f, r in zip(rule, results) if f and r["truth"]["should_intervene"])
    b_fp = sum(1 for f, r in zip(rule, results) if f and not r["truth"]["should_intervene"])
    b_hn = sum(1 for f, r in zip(rule, results) if f and r["truth"]["sample_group"] == "hard_negative")
    share = {d: sum(1 for r in results if r["final_decision"] == d) for d in ORDER}
    return {
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "accuracy": round((tp + tn) / len(results), 4) if results else None,
        "recall": round(tp / pos, 4) if pos else None,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "fp_rate": round(fp / (fp + tn), 4) if fp + tn else None,
        "hold_recall": round(hold_tp / pos, 4) if pos else None,
        "hard_negatives": len(hn),
        "hard_negative_interrupted": round(sum(1 for r in hn if r["final_decision"] != "ALLOW") / len(hn), 4) if hn else None,
        "positives": pos, "negatives": neg,
        "baseline": {"name": "Rule: new payee + 5x usual amount",
                     "recall": round(b_tp / pos, 4) if pos else None,
                     "precision": round(b_tp / (b_tp + b_fp), 4) if b_tp + b_fp else None,
                     "fp_rate": round(b_fp / neg, 4) if neg else None,
                     "hard_negative_interrupted": round(b_hn / len(hn), 4) if hn else None},
        "decisions": share,
    }
