"""System evaluation: rule baselines vs mule-only vs coercion-only vs combined SafePay Shield.

JOINT EVALUATION SET
  Rows: the 30% held-out TEST split of the synthetic coercion CSV (same split and seed as
  scripts/train_coercion_model.py, so no row was used for training or cut-off selection).
  Each row is paired with a payee profile (seed 42):
    payee profiles  new_no_history  a payee with < MULE_MIN_HISTORY_TXNS transactions
                                    (mule status insufficient_history; the model isn't called)
                    rbih_legit      a real RBIH account labelled legitimate (from the RBIH
                                    evaluation sample), with its actual mule-model score
                    rbih_mule       a real RBIH account labelled mule, with its actual score
    pairing ratios (by row type)
      coerced, new payee     60% new_no_history / 25% rbih_legit / 15% rbih_mule
      coerced, known payee   80% rbih_legit / 20% rbih_mule
      genuine, new payee     15% new_no_history / 80% rbih_legit / 5% rbih_mule
      genuine, known payee   97% rbih_legit / 3% rbih_mule
  Why: fresh or rented mule accounts often have no history or no flags yet (DATA_CARD:
  about 55% of mule accounts are newly opened, and only 30% have complaints at payment time).
  rbih_legit paired with a coerced row stands in for an unflagged mule account.
  Ground truth: should_intervene = label_coerced OR payee is an RBIH-labelled mule.

APPROACHES (all on the same rows)
  rule_new_payee_5x     DATA_CARD baseline: new payee AND amount >= 5x usual
  rule_new_unknown_call DATA_CARD baseline: new payee AND on a call with an unknown number
  mule_only             mule model at 0.10 (insufficient_history = not flagged)
  coercion_only         rules + ML + gates, fusion with the mule input removed
  combined              full SafePay Shield fusion (the two-stage flow)
  "Intervene" = any decision other than ALLOW. HOLD-level figures are reported separately.

NOT COVERED: splitting detection (the CSV has no individual earlier payments, only 24 h
velocity totals), trusted beneficiaries / planned payments and the victim checklist
(not in the CSV). Mule scores on RBIH accounts may be optimistic (possible overlap with
the mule model's training data). Results show feasibility, not real-world accuracy.

Also measures /api/assess response time (p50/p95 over 1,000 in-process calls on a copy of
the demo database).

Outputs: reports/evaluation.json, reports/evaluation.csv, reports/eval_*.png
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from safepay.assess import score_coercion  # noqa: E402
from safepay.coercion.model import CoercionModel  # noqa: E402
from safepay.fusion.engine import LEVEL, ORDER, decide  # noqa: E402
from safepay.mule.predictor import band_for  # noqa: E402

SEED = config.RANDOM_SEED
CAVEAT = ("Synthetic coercion data — feasibility only, not real-world accuracy. Mule scores on RBIH "
          "accounts may be optimistic (possible overlap with training data). Two coercion rules were revised "
          "after the test set had been seen once, so these numbers may be slightly optimistic.")
PAIRING = {  # (coerced, payee_is_new) -> probabilities for new_no_history, rbih_legit, rbih_mule
    (1, 1): (0.60, 0.25, 0.15), (1, 0): (0.0, 0.80, 0.20),
    (0, 1): (0.15, 0.80, 0.05), (0, 0): (0.0, 0.97, 0.03),
}
APPROACHES = ["rule_new_payee_5x", "rule_new_unknown_call", "mule_only", "coercion_only", "combined"]
NAMES = {"rule_new_payee_5x": "Rule: new payee + 5x amount", "rule_new_unknown_call": "Rule: new payee + unknown call",
         "mule_only": "Mule model only", "coercion_only": "Coercion only", "combined": "SafePay Shield (combined)"}


# ------------------------------------------------------------------ joint set
def build_joint_set():
    df = pd.read_csv(config.COERCION_CSV)
    _, te = train_test_split(df, test_size=0.30, stratify=df.label_coerced, random_state=SEED)
    te = te.reset_index(drop=True)
    rbih = pd.read_csv(config.REPORTS_DIR / "rbih_mule_eval_sample.csv")
    rbih = rbih[rbih.total_transactions >= config.MULE_MIN_HISTORY_TXNS]
    legit, mules = rbih[rbih.is_mule == 0].reset_index(drop=True), rbih[rbih.is_mule == 1].reset_index(drop=True)

    rng = np.random.default_rng(SEED)
    kinds, probs, accts = [], [], []
    for row in te.itertuples():
        p = PAIRING[(int(row.label_coerced), int(row.payee_is_new))]
        kind = rng.choice(["new_no_history", "rbih_legit", "rbih_mule"], p=p)
        if kind == "new_no_history":
            prob, acct = np.nan, None
        else:
            pool = legit if kind == "rbih_legit" else mules
            r = pool.iloc[int(rng.integers(len(pool)))]
            prob, acct = float(r.mule_probability), r.account_id
        kinds.append(kind); probs.append(prob); accts.append(acct)
    te["payee_profile"] = kinds
    te["mule_probability"] = probs
    te["rbih_account"] = accts
    te["payee_known_mule"] = (te.payee_profile == "rbih_mule").astype(int)
    te["should_intervene"] = ((te.label_coerced == 1) | (te.payee_known_mule == 1)).astype(int)
    return te, {"rbih_legit_pool": len(legit), "rbih_mule_pool": len(mules)}


def mule_result(row):
    if row.payee_profile == "new_no_history":
        return {"status": "insufficient_history"}
    return {"status": "ok", "mule_probability": row.mule_probability, "band": band_for(row.mule_probability)}


# ------------------------------------------------------------------ decisions
def run_pipeline(te, cm):
    card = cm.card
    ml = {s: cm.predict_batch(te, s) for s in ("pre_pause", "post_pause")}
    no_split, no_trust = {"detected": False}, {"trusted_beneficiary": False, "planned_payment_match": False}
    out = {"combined": [], "coercion_only": [], "combined_hold": [], "coercion_only_hold": []}
    rows = te.to_dict("records")
    for i, (row, r) in enumerate(zip(rows, te.itertuples())):
        mule = mule_result(r)
        unusual = {"detected": bool(row["payee_is_new"] and row["amount_to_avg_ratio"] >= config.LARGE_AMOUNT_RATIO)}
        coer = {}
        for stage in ("pre_pause", "post_pause"):
            ctx = {**row, "stage": stage, "checklist": []}
            m = {"status": "ok", **ml[stage].iloc[i].to_dict()}
            coer[stage] = score_coercion(ctx, m, card, stage)
        for name, mule_in in (("combined", mule), ("coercion_only", {"status": "unavailable"})):
            pre = decide(mule_in, coer["pre_pause"], no_split, no_trust, unusual, "pre_pause")["decision"]
            final = pre
            if pre != "ALLOW":   # pause questions asked; answers can only raise the decision
                post = decide(mule_in, coer["post_pause"], no_split, no_trust, unusual, "post_pause")["decision"]
                final = ORDER[max(LEVEL[pre], LEVEL[post])]
            out[name].append(final)
    dec = {k: np.array(v) for k, v in out.items() if v}
    on_call = (te.on_voice_call + te.on_video_call) > 0
    dec["rule_new_payee_5x"] = np.where((te.payee_is_new == 1) & (te.amount_to_avg_ratio >= 5), "WARN", "ALLOW")
    dec["rule_new_unknown_call"] = np.where((te.payee_is_new == 1) & on_call & (te.caller_in_contacts == 0),
                                            "WARN", "ALLOW")
    dec["mule_only"] = np.where(te.mule_probability.fillna(-1) >= config.MULE_THRESHOLD, "WARN", "ALLOW")
    return dec


# -------------------------------------------------------------------- metrics
def metrics(te, decision):
    y = te.should_intervene.values
    flag = decision != "ALLOW"
    hold = decision == "HOLD"
    neg = y == 0
    hard = neg & (te.sample_group == "hard_negative").values

    def prf(pred):
        tp, fp = int((pred & (y == 1)).sum()), int((pred & neg).sum())
        fn = int((~pred & (y == 1)).sum())
        p, r = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
        return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4), "recall": round(r, 4),
                "f1": round(2 * p * r / max(p + r, 1e-9), 4), "fp_rate": round(float(pred[neg].mean()), 4),
                "fp_rate_hard_negatives": round(float(pred[hard].mean()), 4)}

    coerced = te.label_coerced.values == 1
    res = {"intervene": prf(flag), "hold": prf(hold),
           "decision_share": {d: round(float((decision == d).mean()), 4) for d in ("ALLOW", "WARN", "HOLD")},
           "recall_by_scam_type": {}, "fp_rate_by_hard_negative": {}}
    for st, idx in te[coerced].groupby("scam_type").groups.items():
        res["recall_by_scam_type"][st] = {"intervene": round(float(flag[idx].mean()), 3),
                                          "hold": round(float(hold[idx].mean()), 3), "n": int(len(idx))}
    for hn, idx in te[hard].groupby("hard_negative_reason").groups.items():
        res["fp_rate_by_hard_negative"][hn] = {"intervene": round(float(flag[idx].mean()), 3),
                                               "hold": round(float(hold[idx].mean()), 3), "n": int(len(idx))}
    return res


def headline(te, dec):
    clean = (te.label_coerced == 1) & (te.payee_profile != "rbih_mule")
    missed = clean & (dec["mule_only"] == "ALLOW")
    n_missed = int(missed.sum())
    caught = int((missed & (dec["combined"] != "ALLOW")).sum())
    held = int((missed & (dec["combined"] == "HOLD")).sum())
    no_hist = clean & (te.payee_profile == "new_no_history")
    gm = (te.label_coerced == 0) & (te.payee_profile == "rbih_mule")
    return {
        "question": "How many coerced payments to clean payees does mule-only miss, and how many of those "
                    "does the combined system catch?",
        "coerced_to_clean_payees": int(clean.sum()),
        "missed_by_mule_only": n_missed,
        "of_those_caught_by_combined": caught,
        "of_those_caught_by_combined_pct": round(100 * caught / max(n_missed, 1), 1),
        "of_those_held_by_combined": held,
        "of_those_held_by_combined_pct": round(100 * held / max(n_missed, 1), 1),
        "coerced_to_payees_with_no_history": int(no_hist.sum()),
        "no_history_caught_by_combined_pct": round(100 * float((dec["combined"][no_hist] != "ALLOW").mean()), 1),
        "genuine_payer_to_rbih_mule": int(gm.sum()),
        "genuine_payer_to_mule_caught": {k: round(100 * float((dec[k][gm] != "ALLOW").mean()), 1)
                                         for k in ("mule_only", "coercion_only", "combined")},
    }


# -------------------------------------------------------------------- latency
def measure_latency(n=1000):
    from app import create_app
    from safepay.seed import build
    tmp = Path(tempfile.mkdtemp()) / "latency.db"
    build(tmp)
    app = create_app(db_path=tmp)
    client = app.test_client()
    import sqlite3
    c = sqlite3.connect(tmp)
    sc = c.execute("SELECT user_id, payee_vpa, amount, situation_json FROM scenarios ORDER BY id").fetchall()
    c.close()
    payloads = []
    for uid, vpa, amt, sit in sc:
        s = json.loads(sit)
        s.pop("simulated_day_of_month", None)
        payloads.append({"user_id": uid, "payee_vpa": vpa, "amount": amt, "situation": s})
    for p in payloads[:3]:                      # warm-up (not timed)
        client.post("/api/assess", json=p)
    wall, server = [], []
    for i in range(n):
        t = time.perf_counter()
        r = client.post("/api/assess", json=payloads[i % len(payloads)])
        wall.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200, r.get_json()
        server.append(r.get_json()["latency_ms"])
    shutil.rmtree(tmp.parent, ignore_errors=True)
    q = lambda a, p: round(float(np.percentile(a, p)), 1)  # noqa: E731
    return {"calls": n, "endpoint": "POST /api/assess (pre_pause, includes the DB write)",
            "method": "Flask test client, in-process (no network); cycles through the 13 scenario inputs",
            "wall_ms": {"p50": q(wall, 50), "p95": q(wall, 95), "max": q(wall, 100)},
            "server_reported_ms": {"p50": q(server, 50), "p95": q(server, 95)}}


# ---------------------------------------------------------------------- charts
LIGHT = {"surface": "#fcfcfb", "text": "#0b0b0b", "text2": "#52514e", "grid": "#e4e3df"}
COLORS = {"combined": "#2a78d6", "coercion_only": "#eb6834", "mule_only": "#1baf7a",
          "rule_new_payee_5x": "#eda100", "rule_new_unknown_call": "#e87ba4"}   # fixed per entity


def _style(ax):
    ax.set_facecolor(LIGHT["surface"])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(LIGHT["grid"])
    ax.tick_params(colors=LIGHT["text2"], labelsize=9, length=0)
    ax.xaxis.grid(True, color=LIGHT["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def charts(results):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": LIGHT["text"]})
    files = []

    # 1) comparison: small multiples, one panel per metric
    panels = [("Detection rate (recall)", "recall"), ("Precision", "precision"),
              ("False-positive rate", "fp_rate"), ("False positives on hard negatives", "fp_rate_hard_negatives")]
    fig, axes = plt.subplots(1, 4, figsize=(15, 3.6), sharey=True, facecolor=LIGHT["surface"])
    order = list(reversed(APPROACHES))
    for ax, (title, key) in zip(axes, panels):
        _style(ax)
        vals = [results[a]["intervene"][key] for a in order]
        ax.barh(range(len(order)), vals, height=0.55, color=[COLORS[a] for a in order],
                edgecolor=LIGHT["surface"], linewidth=2)
        for i, v in enumerate(vals):
            ax.text(v + 0.02, i, f"{v:.0%}", va="center", fontsize=9, color=LIGHT["text"])
        ax.set_xlim(0, 1.15)
        ax.set_xticks([0, 0.5, 1]); ax.set_xticklabels(["0%", "50%", "100%"])
        ax.set_title(title, fontsize=10.5, color=LIGHT["text"], loc="left")
    axes[0].set_yticks(range(len(order))); axes[0].set_yticklabels([NAMES[a] for a in order], color=LIGHT["text"])
    fig.suptitle("Intervene (WARN or HOLD) — same joint test set for every approach", x=0.01, ha="left",
                 fontsize=12, color=LIGHT["text"])
    fig.text(0.01, -0.04, "Synthetic coercion test split + RBIH payee profiles. Feasibility only, not real-world accuracy.",
             fontsize=8.5, color=LIGHT["text2"])
    fig.tight_layout()
    f = config.REPORTS_DIR / "eval_comparison.png"
    fig.savefig(f, dpi=150, bbox_inches="tight", facecolor=LIGHT["surface"]); plt.close(fig); files.append(f.name)

    # 2) and 3) breakdowns for mule-only, coercion-only, combined
    trio = ["mule_only", "coercion_only", "combined"]
    for fname, key, title, sub in (
            ("eval_recall_by_scam_type.png", "recall_by_scam_type", "Coerced payments caught (WARN or HOLD), by scam type",
             "Higher is better."),
            ("eval_fp_by_hard_negative.png", "fp_rate_by_hard_negative",
             "Genuine 'risky-looking' payments interrupted (WARN or HOLD), by type", "Lower is better.")):
        cats = [c for c in results["combined"][key] if c != "none"]
        cats.sort(key=lambda c: results["combined"][key][c]["intervene"])
        fig, ax = plt.subplots(figsize=(9, 0.62 * len(cats) + 1.4), facecolor=LIGHT["surface"])
        _style(ax)
        h = 0.24
        for j, a in enumerate(trio):
            vals = [results[a][key][c]["intervene"] for c in cats]
            ax.barh(np.arange(len(cats)) + (j - 1) * h, vals, height=h, color=COLORS[a], label=NAMES[a],
                    edgecolor=LIGHT["surface"], linewidth=1.5)
            if a == "combined":
                for i, v in enumerate(vals):
                    ax.text(v + 0.015, i + h, f"{v:.0%}", va="center", fontsize=8.5, color=LIGHT["text"])
        ax.set_yticks(range(len(cats)))
        ax.set_yticklabels([f"{c.replace('_', ' ')} (n={results['combined'][key][c]['n']})" for c in cats],
                           color=LIGHT["text"])
        ax.set_xlim(0, 1.12); ax.set_xticks([0, 0.25, 0.5, 0.75, 1]); ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"])
        ax.set_title(title, fontsize=11.5, loc="left", color=LIGHT["text"])
        ax.legend(loc="lower center", bbox_to_anchor=(0.45, 1.06), ncol=3, frameon=False, fontsize=9,
                  labelcolor=LIGHT["text"])
        fig.text(0.01, -0.02, f"{sub} Values shown for SafePay Shield. Synthetic data — feasibility only.",
                 fontsize=8.5, color=LIGHT["text2"])
        fig.tight_layout()
        f = config.REPORTS_DIR / fname
        fig.savefig(f, dpi=150, bbox_inches="tight", facecolor=LIGHT["surface"]); plt.close(fig); files.append(f.name)
    return files


# ------------------------------------------------------------ known limitations
def known_limitations(out: dict) -> list[dict]:
    """Built from the measured numbers in `out`, so the text always matches the results."""
    c = out["approaches"]["combined"]
    rule = out["approaches"]["rule_new_payee_5x"]["intervene"]["fp_rate_hard_negatives"]
    hn = c["fp_rate_by_hard_negative"]
    worst = sorted(hn.items(), key=lambda kv: -kv[1]["intervene"])[:2]
    return [{
        "id": "hard_negative_warn_rate",
        "finding": (f"SafePay Shield interrupts (WARN or HOLD) {c['intervene']['fp_rate_hard_negatives']:.0%} of genuine "
                    f"'risky-looking' payments (hard negatives), above the simple new-payee + 5x rule ({rule:.0%}). "
                    f"Most of this is WARN: only {c['hold']['fp_rate_hard_negatives']:.0%} are held. Worst types: "
                    + ", ".join(f"{k.replace('_', ' ')} {v['intervene']:.0%} (HOLD {v['hold']:.0%})" for k, v in worst) + "."),
        "cause": ("The first-pass WARN cut-off was chosen for 90% recall on validation (a screening level), and the "
                  "unusual-payment check (R6b) asks to verify every first payment of 5x the usual amount or more."),
        "planned_fix": ("Re-tune the first-pass WARN cut-off and the unusual-payment (R6b) threshold on the VALIDATION "
                        "split (not the test set), targeting a stated hard-negative WARN budget, and disclose it as a "
                        "further post-test change. Thresholds were deliberately NOT changed in this version."),
    }]


def annotate_only():
    """Add/refresh the known-limitations block of an existing reports/evaluation.json without re-running."""
    p = config.REPORTS_DIR / "evaluation.json"
    out = json.loads(p.read_text())
    out["known_limitations"] = known_limitations(out)
    p.write_text(json.dumps(out, indent=2))
    for lim in out["known_limitations"]:
        print(lim["finding"])


# ------------------------------------------------------------------------ main
def main():
    if "--annotate-only" in sys.argv:
        return annotate_only()
    t0 = time.time()
    cm = CoercionModel()
    if not cm.available:
        sys.exit(cm.error)
    te, pools = build_joint_set()
    print(f"joint set: {len(te)} rows; payee profiles {te.payee_profile.value_counts().to_dict()}; "
          f"should_intervene {int(te.should_intervene.sum())}")
    dec = run_pipeline(te, cm)
    print(f"decisions done ({time.time() - t0:.0f}s)")
    results = {a: metrics(te, dec[a]) for a in APPROACHES}
    head = headline(te, dec)
    print("measuring /api/assess latency (1,000 calls)...")
    lat = measure_latency()

    rbih_path = config.REPORTS_DIR / "rbih_mule_eval.json"
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "caveat": CAVEAT,
        "datasets": {"rows": "data/coercion/safepay_coercion_synthetic.csv — 30% test split (seed 42)",
                     "payee_profiles": "reports/rbih_mule_eval_sample.csv (RBIH-NFPC Phase 2 labelled sample)"},
        "design": __doc__.split("JOINT EVALUATION SET")[1].split("Also measures")[0].strip(),
        "joint_set": {"rows": len(te), "seed": SEED, "pairing_ratios": {f"coerced={k[0]},payee_is_new={k[1]}": dict(
            zip(["new_no_history", "rbih_legit", "rbih_mule"], v)) for k, v in PAIRING.items()},
            "payee_profiles": te.payee_profile.value_counts().to_dict(), "pools": pools,
            "should_intervene": int(te.should_intervene.sum()), "coerced": int(te.label_coerced.sum()),
            "genuine_to_mule": int(((te.label_coerced == 0) & (te.payee_known_mule == 1)).sum())},
        "approaches": {a: {"name": NAMES[a], **results[a]} for a in APPROACHES},
        "headline": head,
        "latency": lat,
        "mule_model_rbih": json.loads(rbih_path.read_text()) if rbih_path.exists() else
        {"status": "not measured here (RBIH data absent)"},
        "not_covered": ["splitting detection (no individual earlier payments in the CSV)",
                        "trusted beneficiaries / planned payments", "victim checklist answers"],
        "runtime_sec": None,
    }
    out["known_limitations"] = known_limitations(out)
    out["charts"] = charts(results)
    out["runtime_sec"] = round(time.time() - t0, 1)
    (config.REPORTS_DIR / "evaluation.json").write_text(json.dumps(out, indent=2))
    pd.DataFrame([{"approach": a, "name": NAMES[a], **{f"intervene_{k}": v for k, v in results[a]["intervene"].items()},
                   **{f"hold_{k}": v for k, v in results[a]["hold"].items()},
                   **{f"share_{k}": v for k, v in results[a]["decision_share"].items()}} for a in APPROACHES]
                 ).to_csv(config.REPORTS_DIR / "evaluation.csv", index=False)

    print(f"\n{'approach':<28}{'recall':>8}{'prec':>8}{'F1':>8}{'FPR':>8}{'FPR-hard':>10}{'HOLD-rec':>10}{'HOLD-FPRh':>11}")
    for a in APPROACHES:
        m, h = results[a]["intervene"], results[a]["hold"]
        print(f"{NAMES[a]:<28}{m['recall']:>8.3f}{m['precision']:>8.3f}{m['f1']:>8.3f}{m['fp_rate']:>8.3f}"
              f"{m['fp_rate_hard_negatives']:>10.3f}{h['recall']:>10.3f}{h['fp_rate_hard_negatives']:>11.3f}")
    print("\nheadline:", json.dumps(head, indent=1))
    print("latency:", lat["wall_ms"], "server:", lat["server_reported_ms"])
    print(f"done in {out['runtime_sec']}s")


if __name__ == "__main__":
    main()
