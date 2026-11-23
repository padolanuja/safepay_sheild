# SafePay Shield (prototype, in progress)

Detects **coerced UPI payments**: payments made by the real account holder under scammer
pressure. It combines a receiver-side mule model with payer-side coercion signals.
**Simulation only.** It never connects to real UPI, NPCI or bank systems, and holds are always simulated.

> Full README (architecture, demo walkthrough, fusion logic, limitations) arrives in Phase 8.
> See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python scripts\evaluate_mule_rbih.py      # needs RBIH data, see below
.\.venv\Scripts\python scripts\build_demo_payees.py       # needs the output of the line above
.\.venv\Scripts\python -m safepay.seed
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\flask --app app run
```

The two RBIH scripts only need to run once. Their outputs are kept in the project
(`reports/rbih_mule_eval*.{json,csv}`, `data/demo/`), so seeding and running the app
don't need the RBIH data.

## Data

| Data | Location | Notes |
|---|---|---|
| Mule model | `models/safepay_mule_model.pkl` | Provided, read-only. Needs `scikit-learn==1.6.1`. |
| Synthetic coercion dataset | `data/coercion/` | Unzipped from `safepay_coercion_dataset.zip`. |
| **RBIH-NFPC Phase 2** | **Not copied into the project.** Read in place from `RBIH_DATA_DIR` in `config.py`, which defaults to `C:\Users\Anuja\Downloads\dataset fintech`. Override it with the `SAFEPAY_RBIH_DIR` environment variable. | About 16 GB. Only a labelled sample is read (see below). |
| Demo payee histories | `data/demo/` | 8 real RBIH accounts, pseudonymised. Provenance is in `rbih_payee_provenance.json`. |

### Mule model on RBIH (labelled sample)

`scripts/evaluate_mule_rbih.py` reads **20 randomly chosen transaction parts** (seed 42),
plus the one part that holds rows for accounts across the whole range, so that every
sampled account has its full history. That is **11.9 M transactions** for **4,751 labelled
accounts** (151 mules). Results are in `reports/rbih_mule_eval.json`.

> ⚠️ **These metrics may be optimistic (possible overlap with training data).** The mule
> model was trained on this dataset, and its train/test split is unknown. The 9 features
> are also reconstructed from the RBIH schema (`safepay/mule/features.py`), because the
> original feature code wasn't provided.

### Minimum history for the mule model

Payees with fewer than `MULE_MIN_HISTORY_TXNS = 5` transactions get the status
`insufficient_history`. The mule model is not called for them, and the decision rests on
coercion signals alone. Evidence: `scripts/probe_mule_history.py` →
`reports/mule_history_probe.json`.

## Coercion model: known caveats

- **Synthetic data, feasibility only.** Metrics on `safepay_coercion_synthetic.csv` show the design
  is feasible, not real-world accuracy.
- **`job_task_fee` is a synthetic-data artefact.** The purposes `job_task_fee`, `govt_fine_verification`
  and `refund_cashback` appear **only** on coerced rows. Demo Scenario 4 (job/task scam) scores
  **0.999** post-pause with purpose `job_task_fee`, and that score is optimistic. With purpose
  `other` it still scores 0.963 (HOLD), because `other` is also over-represented among coerced rows
  (36.2% vs a 12.8% base rate). Without the model, its escalation pattern alone still gives WARN.
- **Rules revised after one look at the test set.** Two rule changes were made after the test set
  had been seen once, and were justified on validation data only:
  1. "Someone is guiding me" is severe only during a call with an unknown number.
  2. The "I know this payee" contradiction is soft.

  Model cut-offs were not changed. Because the test set had been seen, **the Phase 6 evaluation
  numbers may be slightly optimistic.** Details are in `models/coercion_model_card.json`
  → `rule_revisions`.

## Known limitation: too many WARNs on genuine "risky-looking" payments

From `scripts/evaluate_system.py` (`reports/evaluation.json` → `known_limitations`):

- SafePay Shield interrupts (WARN or HOLD) **65% of hard negatives**: genuine payments built to
  look risky. That is more than the simple "new payee + 5× amount" rule (48%).
- Most of these are **WARN** (a pause with questions). Only **10% are held**.
- The worst types are a large genuine payment to a new payee (99% WARN or HOLD, 19% HOLD) and an
  FD broken for a genuine need (91%, 9% HOLD).

**Why:** the first-pass WARN cut-off was chosen on validation data for 90% recall, which makes it a
screening level. On top of that, the unusual-payment check (R6b) asks the user to verify every first
payment of 5× their usual amount or more.

**Planned fix (not done in this version):** re-tune the first-pass WARN cut-off and the R6b
threshold on the **validation** split, never the test set, against a stated hard-negative WARN
budget. Disclose it as a further change made after the test set was seen. Thresholds were
deliberately left unchanged here.

## Privacy

Only fictional demo users. No UPI PINs, OTPs, passwords, call audio, screen content or
contact lists are stored. VPAs are masked in logs. A real SDK would need consent under the
DPDP Act, 2023.
