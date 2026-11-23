"""Train the payer-side coercion models and choose cut-offs on a validation split.

Data: data/coercion/safepay_coercion_synthetic.csv (synthetic — feasibility only).

Splits (all stratified on label_coerced, seed 42):
    70% train / 30% test           test is touched ONLY for the final report
    train -> 80% fit / 20% valid   model choice, band cut-offs and rule cap use valid only

Choices made on the validation split (criteria fixed before looking at test):
    model kind     RandomForest vs XGBoost: higher validation PR-AUC
    HIGH cut-off   smallest threshold where validation precision >= 0.90 (a HOLD should be
                   right at least 9 times in 10 on this data)
    MEDIUM cut-off largest threshold where validation recall >= 0.90 (a WARN should
                   catch at least 9 in 10 coerced payments)
    rule cap       smallest integer cap (4-40) for which the combined score keeps
                   validation precision >= 0.90 at HIGH and does not lower precision at
                   MEDIUM by more than 0.02 compared with the ML model alone

Final models are the ones fitted on the 80% part, so the cut-offs match them exactly.
A comparison variant that includes the payee-side columns is trained too (never used
in the app). Outputs: models/coercion_model_{prepause,postpause}.pkl,
models/coercion_model_card.json, reports/coercion_training.json.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from safepay.coercion.answer_check import check_answers  # noqa: E402
from safepay.coercion.model import NEVER_TRAIN, encode  # noqa: E402
from safepay.coercion.rules import evaluate_rules, has_severe, rule_points  # noqa: E402
from safepay.coercion.scoring import band_for  # noqa: E402

SEED = config.RANDOM_SEED
STAGES = ("pre_pause", "post_pause")
CAVEAT = "synthetic data — feasibility only, not real-world accuracy"

try:
    from xgboost import XGBClassifier
    HAVE_XGB = True
except Exception:  # noqa: BLE001
    HAVE_XGB = False


def candidates(y):
    out = {"random_forest": RandomForestClassifier(n_estimators=300, min_samples_leaf=3,
                                                   class_weight="balanced", n_jobs=-1, random_state=SEED)}
    if HAVE_XGB:
        out["xgboost"] = XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.08, subsample=0.9,
                                       colsample_bytree=0.9, scale_pos_weight=float((y == 0).sum() / (y == 1).sum()),
                                       eval_metric="logloss", random_state=SEED, n_jobs=-1)
    return out


def pick_cutoffs(y, s):
    prec, rec, thr = precision_recall_curve(y, s)
    prec, rec = prec[:-1], rec[:-1]          # align with thresholds
    ok_high = np.where(prec >= 0.90)[0]
    high = float(thr[ok_high[0]]) if len(ok_high) else float(thr[-1])
    ok_med = np.where(rec >= 0.90)[0]
    medium = float(thr[ok_med[-1]]) if len(ok_med) else float(thr[0])
    medium = min(medium, high)
    return {"medium": round(medium, 4), "high": round(high, 4)}


def rule_frame(df: pd.DataFrame, stage: str) -> pd.DataFrame:
    pts, sev = [], []
    for row in df.to_dict("records"):
        ctx = {**row, "stage": stage, "checklist": []}
        ctx["contradictions"] = check_answers(ctx)
        ind = evaluate_rules(ctx)
        pts.append(rule_points(ind))
        sev.append(has_severe(ind))
    return pd.DataFrame({"points": pts, "severe": sev}, index=df.index)


def bands(ml, rules, cut, cap, use_ml=True, use_rules=True):
    rule_norm = np.minimum(1.0, rules.points.values / cap) if use_rules else np.zeros(len(ml))
    score = np.maximum(rule_norm, ml) if use_ml else rule_norm
    b = np.array([band_for(x, cut) for x in score], dtype=object)
    if use_rules:
        b[rules.severe.values] = "high"
    return b


def prf(y, pred):
    tp = int(((pred == 1) & (y == 1)).sum()); fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    return tp / max(tp + fp, 1), tp / max(tp + fn, 1)


def pick_cap(y, ml, rules, cut):
    p_med_ml, _ = prf(y, (ml >= cut["medium"]).astype(int))
    for cap in range(4, 41):
        b = bands(ml, rules.assign(severe=False), cut, cap)
        p_high, _ = prf(y, (b == "high").astype(int))
        p_med, _ = prf(y, np.isin(b, ["medium", "high"]).astype(int))
        if p_high >= 0.90 and p_med >= p_med_ml - 0.02:
            return cap
    return 40


def report_block(y, b, meta):
    out = {}
    for level, mask in (("warn_or_hold", np.isin(b, ["medium", "high"])), ("hold", b == "high")):
        pred = mask.astype(int)
        p, r = prf(y, pred)
        neg = y == 0
        hard = (meta.sample_group == "hard_negative").values
        norm = (meta.sample_group == "normal_negative").values
        pos = y == 1
        out[level] = {
            "precision": round(p, 4), "recall": round(r, 4),
            "f1": round(2 * p * r / max(p + r, 1e-9), 4),
            "fp_rate": round(float(pred[neg].mean()), 4),
            "fp_rate_hard_negatives": round(float(pred[hard & neg].mean()), 4),
            "fp_rate_normal": round(float(pred[norm & neg].mean()), 4),
            "recall_by_scam_type": {k: {"recall": round(float(v.mean()), 3), "n": int(len(v))} for k, v in
                                    pd.Series(pred[pos], index=meta.scam_type.values[pos]).groupby(level=0)},
            "fp_rate_by_hard_negative": {k: {"fp_rate": round(float(v.mean()), 3), "n": int(len(v))} for k, v in
                                         pd.Series(pred[hard & neg], index=meta.hard_negative_reason.values[hard & neg]).groupby(level=0)},
        }
    out["band_share"] = {k: round(float((b == k).mean()), 4) for k in ("low", "medium", "high")}
    return out


def main():
    df = pd.read_csv(config.COERCION_CSV)
    assert df.shape == (20000, 37), df.shape
    y_all = df.label_coerced.values
    tr, te = train_test_split(df, test_size=0.30, stratify=y_all, random_state=SEED)
    fit, va = train_test_split(tr, test_size=0.20, stratify=tr.label_coerced, random_state=SEED)
    print(f"fit {len(fit)} / valid {len(va)} / test {len(te)}")

    card = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data_source": str(config.COERCION_CSV.relative_to(config.BASE_DIR)), "caveat": CAVEAT,
            "target": "label_coerced", "never_trained_on": NEVER_TRAIN,
            "excluded_payee_side": ["payee_vpa_age_days", "payee_prior_complaints"],
            "split": {"seed": SEED, "train": len(tr), "fit": len(fit), "validation": len(va), "test": len(te)},
            "selection_rules": __doc__.split("Choices made")[1].split("Final models")[0].strip(),
            "rule_revisions": [
                "v1: initial weights from DATA_CARD evidence.",
                "v2 (after the first test run, justified on VALIDATION data only): 'someone is guiding me' "
                "is severe only while on a call with an unknown number (validation precision 0.83; 0.22 "
                "with no call); the know-payee contradiction became 'soft' (validation precision 0.24). "
                "The test set was seen once before this revision; model cut-offs were not changed by it."],
            "known_artefact": ("ans_purpose values govt_fine_verification, refund_cashback and job_task_fee "
                               "appear only on coerced rows of the synthetic data; post-pause metrics are "
                               "therefore optimistic. Example: demo scenario 4 (job/task scam) scores 0.999 "
                               "post-pause with purpose job_task_fee; that score is a synthetic-data artefact "
                               "and is optimistic. With purpose 'other' it scores 0.963 (purpose 'other' is "
                               "also over-represented among coerced rows: 36.2% vs a 12.8% base rate)."),
            "stages": {}, "cutoffs": {}, "rule_score_cap": {}, "comparison_with_payee_features": {}}
    report = {"caveat": CAVEAT, "test_set": {}, "validation": {}}

    for stage in STAGES:
        for with_payee in (False, True):
            Xf, Xv, Xt = (encode(d, stage, with_payee) for d in (fit, va, te))
            scores = {}
            for kind, m in candidates(fit.label_coerced).items():
                m.fit(Xf, fit.label_coerced)
                scores[kind] = (average_precision_score(va.label_coerced, m.predict_proba(Xv)[:, 1]), m)
            kind = max(scores, key=lambda k: scores[k][0])
            model = scores[kind][1]
            sv = model.predict_proba(Xv)[:, 1]
            st = model.predict_proba(Xt)[:, 1]
            cut = pick_cutoffs(va.label_coerced.values, sv)
            val_prauc = {k: round(v[0], 4) for k, v in scores.items()}

            if with_payee:   # comparison only
                card["comparison_with_payee_features"][stage] = {
                    "model_kind": kind, "validation_pr_auc": val_prauc, "cutoffs": cut,
                    "test_roc_auc": round(roc_auc_score(te.label_coerced, st), 4),
                    "test_pr_auc": round(average_precision_score(te.label_coerced, st), 4),
                    "test_ml_only": report_block(te.label_coerced.values,
                                                 bands(st, None, cut, 1, use_rules=False), te)}
                continue

            rv, rt = rule_frame(va, stage), rule_frame(te, stage)
            cap = pick_cap(va.label_coerced.values, sv, rv, cut)
            joblib.dump({"model": model, "kind": kind, "stage": stage, "with_payee": False,
                         "features": list(Xf.columns), "sklearn_compatible": True},
                        config.COERCION_MODEL_PATHS[stage])
            card["stages"][stage] = {
                "model_kind": kind, "features": list(Xf.columns), "n_features": Xf.shape[1],
                "validation_pr_auc": val_prauc, "xgboost_available": HAVE_XGB,
                "test_roc_auc": round(roc_auc_score(te.label_coerced, st), 4),
                "test_pr_auc": round(average_precision_score(te.label_coerced, st), 4)}
            card["cutoffs"][stage] = cut
            card["rule_score_cap"][stage] = cap
            yt = te.label_coerced.values
            report["test_set"][stage] = {
                "ml_only": report_block(yt, bands(st, rt, cut, cap, use_rules=False), te),
                "rules_only": report_block(yt, bands(st, rt, cut, cap, use_ml=False), te),
                "combined": report_block(yt, bands(st, rt, cut, cap), te),
            }
            report["validation"][stage] = {
                "combined": report_block(va.label_coerced.values, bands(sv, rv, cut, cap), va)}
            print(f"{stage}: {kind} (valid PR-AUC {val_prauc}) cutoffs {cut} rule cap {cap} "
                  f"test AUC {card['stages'][stage]['test_roc_auc']}")

    card["metrics_test"] = report["test_set"]
    card["metrics_scope"] = ("Coercion module only, per stage: ML alone, rules alone, and rules+ML with severe "
                             "override. Not included: the ML pressure gate, the fusion engine (R6b, trust, "
                             "mule) and the two-stage flow. Those are measured end to end by "
                             "scripts/evaluate_system.py (Phase 6).")
    report["metrics_scope"] = card["metrics_scope"]
    config.COERCION_MODEL_CARD.write_text(json.dumps(card, indent=2))
    config.REPORTS_DIR.mkdir(exist_ok=True)
    (config.REPORTS_DIR / "coercion_training.json").write_text(json.dumps(
        {**report, "comparison_with_payee_features": card["comparison_with_payee_features"],
         "cutoffs": card["cutoffs"], "rule_score_cap": card["rule_score_cap"]}, indent=2))

    for stage in STAGES:
        print(f"\n=== {stage} (TEST set, {CAVEAT})")
        for method, blk in report["test_set"][stage].items():
            for lvl in ("warn_or_hold", "hold"):
                m = blk[lvl]
                print(f"  {method:<10} {lvl:<12} P={m['precision']:.3f} R={m['recall']:.3f} "
                      f"FP={m['fp_rate']:.3f} FP-hard={m['fp_rate_hard_negatives']:.3f} FP-normal={m['fp_rate_normal']:.3f}")
        c = report["test_set"][stage]["combined"]
        print("  combined recall by scam type (WARN+ / HOLD):")
        for k in c["warn_or_hold"]["recall_by_scam_type"]:
            print(f"    {k:<30} {c['warn_or_hold']['recall_by_scam_type'][k]['recall']:.3f} / "
                  f"{c['hold']['recall_by_scam_type'][k]['recall']:.3f}  (n={c['hold']['recall_by_scam_type'][k]['n']})")
        print("  combined FP rate by hard-negative type (WARN+ / HOLD):")
        for k in c["warn_or_hold"]["fp_rate_by_hard_negative"]:
            print(f"    {k:<32} {c['warn_or_hold']['fp_rate_by_hard_negative'][k]['fp_rate']:.3f} / "
                  f"{c['hold']['fp_rate_by_hard_negative'][k]['fp_rate']:.3f}  (n={c['hold']['fp_rate_by_hard_negative'][k]['n']})")
    print("\ncomparison (ML only, with payee columns):")
    for stage, v in card["comparison_with_payee_features"].items():
        print(f"  {stage}: AUC {v['test_roc_auc']} vs payer-only {card['stages'][stage]['test_roc_auc']}")


if __name__ == "__main__":
    main()
