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

## Privacy

Only fictional demo users. No UPI PINs, OTPs, passwords, call audio, screen content or
contact lists are stored. VPAs are masked in logs. A real SDK would need consent under the
DPDP Act, 2023.
