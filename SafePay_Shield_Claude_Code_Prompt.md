# SAFEPAY SHIELD: FULL PROTOTYPE BUILD PROMPT (for Claude Code)

> **How to use:** Create a project folder. Put these files in it: `safepay_mule_model.pkl`, `safepay_coercion_dataset.zip` and `DATA_CARD.md`. If you can, also add the RBIH-NFPC Phase 2 parquet files in `data/rbih/`. Open Claude Code in that folder and paste everything below this line.

---

Act as a senior Python developer, ML engineer and fintech product designer.

I'm building **SafePay Shield**, a hackathon prototype for the FinTech Innovation Hackathon 2026 (RBIH is the knowledge partner). It detects **coerced UPI payments**. These are payments where the real account holder pays because a scammer is pressuring, threatening or coaching them, as in digital arrest, fake KYC and investment scams.

**Key differentiator:** SafePay Shield can catch a coerced payment **even when the receiving account has no fraud history**. Receiver-side mule detection alone misses these cases. The payer-side coercion signals catch them. Every part of the demo should make this point visible.

The design is a **hybrid** with two parts:
1. **Receiver-side mule risk.** This is my trained Random Forest, built on the RBIH-NFPC Phase 2 dataset.
2. **Payer-side coercion risk.** This combines transparent rules with an ML model trained on my synthetic coercion dataset.

One explainable **risk fusion engine** combines the two. It returns one of three decisions: **ALLOW**, **WARN + VERIFY** or **COOLING-OFF + SIMULATED HOLD**.

The final output is a working end-to-end demo. A user enters a UPI ID and amount in a simulated UPI app and turns on situations such as an unknown call, screen sharing or a new payee. SafePay Shield then analyses the payment, explains why it was flagged and responds. The whole flow must actually work. Static screens or a dashboard alone are not enough.

---

## 0. Ground rules (apply throughout)

- This is a **simulation**. Never connect to real UPI, NPCI or bank systems. Never claim that a payment was really blocked. Holds are always labelled "simulated".
- **Never store** real UPI PINs, OTPs, passwords or banking credentials. Use fictional demo data only.
- **Never send random or invented values to a model.** If the data a model needs is missing, return `status: "unavailable"` and explain why.
- **Never fabricate metrics.** Every number shown must come from a script that actually ran, and the UI must name the dataset it came from. Results on synthetic data show **feasibility, not real-world accuracy**. Say this wherever metrics appear.
- In the UI and the API, clearly separate **model predictions**, **rule results** and **simulated/demo data**.
- Do not auto-retrain any model from user feedback. Feedback is stored for human review only.
- If a required feature, field or file is missing, **stop and explain** the problem. Do not silently assume a value.
- Keep the code modular and commented. Do not put the whole application in one file.

---

## 1. Input files: inspect them first (Phase 1)

### 1a. `safepay_mule_model.pkl` (already checked; confirm on load)
It is a joblib **dict** with these keys:
- `model`: `RandomForestClassifier` (150 trees, `class_weight="balanced"`, classes `[0, 1]`)
- `features`, in this exact order:
  `total_transactions, total_debit, total_credit, avg_transaction_amount, unique_counterparties, upi_transaction_count, incoming_counterparties, outgoing_counterparties, reversal_count`
- `threshold`: `0.10`

The model was saved with **scikit-learn 1.6.1**. Pin `scikit-learn==1.6.1` in `requirements.txt` to avoid version-mismatch warnings. **Do not retrain or replace this model.** At startup, assert that the 9 feature names and their order match `model.feature_names_in_`, and refuse to start the mule module if they don't.

### 1b. `safepay_coercion_dataset.zip`
It contains `safepay_coercion_synthetic.csv` (20,000 rows × 37 columns), `generate_coercion_dataset.py`, `evaluate_baseline.py`, `baseline_results.txt` and `DATA_CARD.md`. Read `DATA_CARD.md` in full. It defines every column, the 7 scam types, the 6 hard-negative types and the research behind each signal.
- Target: `label_coerced`
- **Never train on** `txn_id`, `scam_type`, `sample_group`, `hard_negative_reason` or `label_noisy`. Use them only for analysis and breakdowns.

### 1c. Optional: RBIH-NFPC Phase 2 data in `data/rbih/`
If it is present, use a held-out sample to report the mule model's own metrics and to build realistic demo payee profiles. If it is absent, say so in the README. In that case, report mule-model metrics as "not measured here" and do not invent them.

**Report the Phase 1 findings to me before you continue.**

---

## 2. Architecture and folder structure (Phase 2)

Propose an architecture, then build it roughly like this:

```
safepay_shield/
├── app.py                      # Flask app factory, registers blueprints
├── config.py                   # thresholds, weights, paths (all documented)
├── requirements.txt
├── README.md
├── models/
│   ├── safepay_mule_model.pkl          # provided, read-only
│   ├── coercion_model.pkl              # trained by scripts/train_coercion_model.py
│   └── coercion_model_card.json        # features, metrics, training date, data source
├── data/
│   ├── coercion/ (unzipped dataset)
│   └── rbih/ (optional)
├── safepay/
│   ├── db.py                   # SQLite schema and helpers
│   ├── seed.py                 # fictional demo users, payees and histories
│   ├── mule/
│   │   ├── loader.py           # load and validate the pkl
│   │   ├── features.py         # transaction history → the exact 9 features
│   │   └── predictor.py        # probability, threshold, SHAP explanation
│   ├── coercion/
│   │   ├── rules.py            # transparent indicator rules
│   │   ├── model.py            # ML coercion model and SHAP
│   │   └── answer_check.py     # answer-versus-signal verification
│   ├── behaviour/
│   │   ├── profile.py          # user spending profile and deviation
│   │   └── splitting.py        # multiple-transaction and splitting detection
│   ├── fusion/engine.py        # decision logic (documented matrix)
│   ├── alerts/trusted_contact.py   # simulated alerts, consent-gated
│   ├── i18n/ en.json hi.json mr.json
│   └── api/ (blueprints: users, payees, assess, payments, history, contacts, feedback, metrics, scenarios)
├── scripts/
│   ├── train_coercion_model.py
│   ├── evaluate_system.py      # mule-only vs coercion-only vs combined
│   └── run_scenarios.py
├── templates/ and static/      # frontend (Phase 7)
├── reports/                    # generated metrics (JSON, CSV, PNG)
└── tests/                      # pytest
```

---

## 3. Database: SQLite (Phase 3)

Tables:
- `users`: fictional demo payers (name, age, language preference, account tenure, balance, 90-day average transaction amount, usual active hours)
- `payees`: VPA, display name, type (P2P/P2M), VPA age in days, prior complaints, and a flag saying whether transaction history is available
- `payee_transactions`: simulated transaction history used to compute the 9 mule features (direction, amount, counterparty, channel = UPI/other, is_reversal, timestamp)
- `user_transactions`: the payer's past payments (feeds profiling, splitting detection and history)
- `trusted_contacts`: name, relation, masked phone, `consent_given` and `consent_timestamp`
- `trusted_beneficiaries`: payees the user marked as trusted
- `planned_payments`: payments the user declared in advance, such as "Rent ₹15,000 to landlord@upi on the 5th". A matching payment lowers friction, but it never overrides serious coercion indicators.
- `risk_assessments`: every assessment, with all inputs, scores, indicators, the decision, the explanation and latency in ms
- `feedback`: user/analyst outcome (`was_scam` / `was_genuine` / `rescued`), free text and `verified` (bool). This is the rescue feedback loop, and nothing is auto-retrained from it.
- `alerts_log`: simulated trusted-contact alerts

Seed it with enough demo data for every scenario in Section 9.

---

## 4. Module A: Mule risk, receiver side (Phase 4)

- `features.py` computes the **exact 9 features** from `payee_transactions`. Document the formula for each one, e.g. `incoming_counterparties` = distinct counterparties with a credit to this payee.
- Seed payee histories so that the mule model's output is realistic. Make some payees clean and some mule-like (fan-in from many senders, fast fan-out, reversals). Include **one brand-new, clean-looking payee with a low mule score**. This payee powers the key-differentiator demo.
- `predictor.py` returns:
  - `mule_probability`
  - `flag` (probability ≥ 0.10)
  - `band`: `low` (< 0.10), `elevated` (0.10–0.40) or `high` (≥ 0.40). The 0.40 cut-off is a documented demo choice; put it in `config.py`.
  - `top_factors`: the top SHAP contributions in plain language
  - `status`: `ok`, or `unavailable` when there is no history. In that case the model is not called.
- **Explainability:** use `shap.TreeExplainer` on the RF and select the class-1 output. If SHAP can't be installed, fall back to a documented per-feature contribution method and label it as such.

---

## 5. Module B: Coercion risk, payer side (Phase 5)

### 5a. Transparent rules (`rules.py`)
Each indicator has a name, a weight, a severity (`normal` or `severe`), the evidence that triggered it and a source note from the DATA_CARD. Cover these:
- **Threats and intimidation / authority impersonation:** stated purpose `govt_fine_verification`, a long video call (digital-arrest pattern), an international caller, or victim-checklist items such as "caller said I'm under arrest" or "caller claims to be police, CBI, RBI or a bank".
- **Urgent payment pressure:** night-time payment, high hesitation, segmented typing, short time between call start and payment.
- **Secrecy requests:** victim-checklist item "told not to tell anyone or disconnect".
- **Remote access or screen sharing:** `screen_share_active`, or `remote_access_app_installed_7d` → **severe**.
- **Repeated payment pressure:** `new_payee_txns_24h`, `amount_sent_new_payees_24h`, `fd_or_loan_liquidated_7d`.
- **Victim-reported pressure:** `ans_someone_guiding = yes`, or answers from the victim checklist.
- **Suspicious context:** on a call with an unknown number while paying a new payee; a pasted VPA; frequent app switching.

**Rule:** an unknown payee or a high amount **alone must never establish coercion**. They only add weight when combined with a pressure signal.

### 5b. ML coercion model (`model.py` and `scripts/train_coercion_model.py`)
This is the mentor's requirement: a **separate coercion detection model**.
- Train a Random Forest, plus an XGBoost model if it installs, on the synthetic CSV with a stratified 70/30 split and `seed=42`. One-hot encode `payee_type`, `ans_know_payee`, `ans_someone_guiding` and `ans_purpose`.
- **Important design choice:** train the coercion model on **payer-side features only**. Exclude `payee_vpa_age_days` and `payee_prior_complaints`, because the mule model already covers the payee side. This keeps the two models independent and makes the "no payee history needed" claim honest. Optionally train a second version that includes those features, and report both.
- Save `coercion_model.pkl` and `coercion_model_card.json` (features, metrics and the line "synthetic data — feasibility only").
- Explain each prediction with SHAP.
- Report recall by scam type and false-positive rate by hard-negative type, as `evaluate_baseline.py` does.

### 5c. Answer-versus-signal verification (`answer_check.py`)
Compare the pause-screen answers with the device signals. Examples:
- The user says "No one is guiding me", but they are on a 40-minute call with an unknown number and screen sharing is on → **contradiction**.
- The user says they know the payee, but the payee is new, not in contacts and the VPA was pasted → **contradiction**.

The DATA_CARD notes that victims are coached to lie: only 5–35% admit that someone is guiding them. A contradiction therefore **raises** risk and is shown to the user gently, never as an accusation.

### 5d. Combined coercion score
`coercion_score` = a documented combination of the rule score and the ML probability, for example `max(rule_score_normalised, ml_prob)`. Any **severe** indicator forces at least `high`. Bands are `low`, `medium` and `high`, with cut-offs in `config.py` and a justification for each.

---

## 6. Behaviour profiling, splitting detection and fusion

### 6a. User behaviour profiling (`profile.py`)
Build each payer's profile from `user_transactions`: average and spread of amounts, usual hours, usual payees and P2P/P2M mix. Output deviation signals such as "6.2× your usual amount" or "you rarely pay after 11 PM". A deviation adds context. It never establishes coercion on its own.

### 6b. Multiple-transaction and splitting detection (`splitting.py`)
Detect several payments to the same or new payees within a rolling window (for example 30 min or 24 h) where:
- the amounts are just under round limits (₹9,999, ₹19,999, ₹49,999)
- the total is far above the user's normal
- the amounts escalate (the investment and job scam pattern)

Return the pattern found, the total and the number of transactions.

### 6c. Risk fusion engine (`fusion/engine.py`)
Write the decision as a **documented decision matrix**, not one opaque score:

| Condition | Decision |
|---|---|
| Any **severe** coercion indicator (screen share or remote access during the payment, a digital-arrest pattern, victim reports guidance) | **COOLING-OFF + SIMULATED HOLD** |
| Coercion `high` (any mule band) | HOLD |
| Mule `high` and coercion ≥ `medium` | HOLD |
| Answer-vs-signal contradiction and coercion ≥ `medium` | HOLD |
| Coercion `medium` | WARN + VERIFY |
| Mule `elevated`/`high` and coercion `low` | WARN + VERIFY (show "This account shows suspicious money movement") |
| Splitting pattern detected | WARN + VERIFY at minimum |
| Mule `unavailable` | Decide on coercion alone, and say so |
| Otherwise | ALLOW |

- **Trusted beneficiaries and planned payments** can move a decision down by one level (for example WARN → ALLOW), but **never** when a severe coercion indicator is present.
- The output includes the decision, both risk components, every contributing factor ranked by impact (rules + SHAP), a plain-language reason, the recommended action, the language of the message and the latency in ms.
- The HOLD screen shows a cooling-off countdown (e.g. 30 min, set in `config.py`), helpline guidance (cybercrime helpline 1930 and cybercrime.gov.in) and the option to alert a trusted contact.

### 6d. Trusted-contact alerts (`alerts/trusted_contact.py`)
- These are **simulated only**: log them to `alerts_log` and show a preview such as "SMS that would be sent".
- They fire only on **HOLD**, and only if `consent_given = true`. Genuine payments never trigger alerts.
- The alert message shares only the minimum: no amount details beyond a range, and no account numbers.

### 6e. Multilingual warnings
`en.json`, `hi.json` and `mr.json` hold every warning, question, reason and button label. Use short, simple, non-technical sentences for elderly users. The user can switch language at any time. Add a note in the README that a native speaker should review the Hindi and Marathi text.

### 6f. Privacy-preserving processing
Document this in the README and in code comments:
- Signals are processed on the device or in the session. Store derived indicators only, never raw call audio, screen content or contact lists.
- Payee identifiers are masked in logs.
- A real SDK would need explicit user consent for call-state and behavioural telemetry under the **DPDP Act, 2023**.

---

## 7. API endpoints (with input validation and JSON errors)

- `GET /api/users`, `GET /api/users/<id>` (demo accounts and profile)
- `GET /api/payees/<vpa>` (lookup: name, type, history available)
- `POST /api/mule/predict` (VPA → mule result)
- `POST /api/coercion/assess` (payment context → coercion result)
- `POST /api/assess` (**main endpoint**: full combined decision)
- `POST /api/payments/confirm` (simulated confirm or cancel after a warning; records the outcome)
- `GET /api/history/<user_id>` (transactions with their decisions)
- `GET|POST|DELETE /api/contacts` (trusted contacts and consent)
- `GET|POST /api/trusted-beneficiaries`, `GET|POST /api/planned-payments`
- `POST /api/feedback` (rescue loop)
- `GET|PUT /api/users/<id>/language`
- `GET /api/scenarios` and `POST /api/scenarios/<id>/run` (preset demo scenarios)
- `GET /api/metrics` (reads from `reports/`; never computed on the fly from invented data)
- `GET /api/health` (model-loaded status for both models)

Validate inputs: the VPA format is `name@handle`, the amount must be between ₹1 and ₹1,00,000 (the UPI P2P limit), booleans are booleans and enums are checked. Return clear 400/404/503 errors.

---

## 8. Frontend: simulated UPI app plus dashboard (Phase 7)

Use Flask templates with plain HTML, CSS and JS (no build step). It should look **polished and modern** and be suitable for a jury demo.

**Screen 1: Simulated UPI payment (phone mock-up in the centre)**
- A user picker at the top (demo payers such as "Ramesh, 62" and "Priya, 27") and a language toggle (EN / हिं / मरा).
- Fields for payee UPI ID and amount, with a payee lookup that shows the name and a "New payee" badge.
- A **Situation Simulator** side panel with toggles and sliders: on a voice call (unknown or known number), on a video call with its duration, international caller, screen sharing on, remote-access app installed recently, VPA pasted, hesitation level, night-time, FD broken recently, and the number of payments to new payees in the last 24 h. It also has a **Preset scenario** dropdown that fills all of these in.
- On **Pay**, a "SafePay Shield is checking…" animation appears, followed by:
  - **ALLOW:** a green success screen.
  - **WARN + VERIFY:** an amber screen with 2–3 pause questions ("Do you know this person?", "Is anyone on the phone telling you to make this payment?", "What is this payment for?"), an optional checklist ("They said I'm under arrest" / "They told me not to tell anyone" / "They asked me to install an app"), then a re-assessment that uses the answers.
  - **COOLING-OFF + SIMULATED HOLD:** a red screen with a countdown timer, a simple warning in the chosen language, helpline 1930, a "Call my trusted contact" button with a simulated alert preview, and "Cancel payment" as the main button.
  - Every screen has a **"Why?" panel**: the mule risk and coercion risk as two gauges, the top contributing factors as a horizontal bar chart (SHAP and rules, colour-coded by source), any answer-vs-signal contradictions, and a "Simulated — no real money moved" label.

**Screen 2: Dashboard**
- Transaction history with decision badges and filters
- Risk distribution, decisions over time and the most common indicators
- A feedback queue (mark as scam, genuine or rescued; analyst verifies)

**Screen 3: Evaluation**
- The metrics tables and charts from `reports/` (Section 10), with a clear "synthetic data — feasibility only" banner.

**Screen 4: Settings**
- Trusted contacts with consent, trusted beneficiaries, planned payments and language.

Use a clean colour system (green for ALLOW, amber for WARN, red for HOLD), large readable fonts for elderly users and mobile-friendly layouts.

---

## 9. Demo scenarios (seed these and make them one-click)

Each scenario stores its inputs **and an expected decision**. `scripts/run_scenarios.py` checks the actual result against the expected one.

| # | Scenario | Payee | Expected |
|---|---|---|---|
| 1 | **Ramesh, 62: digital arrest.** 2-hour video call with an unknown number, purpose "govt verification", FD broken, says "no one is guiding me" | **Brand-new, clean payee, low mule score** | **HOLD**: the key differentiator, caught by coercion alone |
| 2 | Fake KYC: unknown voice call, screen sharing on, remote app installed | New payee | HOLD (severe) |
| 3 | Investment scam: calm, no call, 3rd escalating payment today | Mule-like payee | HOLD or WARN (mule + velocity) |
| 4 | Job/task scam: young user, small escalating "task fees" | New payee | WARN (splitting/escalation) |
| 5 | Romance scam: no call, large amount, new payee, purpose "family" | Elevated mule | WARN |
| 6 | Splitting: 5 × ₹19,999 within 20 minutes | New payee | WARN or HOLD |
| 7 | Genuine: hospital bill, large amount, new merchant | Clean P2M | **WARN, not HOLD** (hard negative) |
| 8 | Genuine: son paying mother while on a call with her (known contact) | Trusted beneficiary | ALLOW |
| 9 | Genuine: rent that matches a planned payment | Trusted | ALLOW |
| 10 | Genuine payer paying a mule account (no coercion) | High mule | WARN (mule warning) |
| 11 | Everyday grocery P2M payment | Clean merchant | ALLOW |
| 12 | Payee with no transaction history | Unavailable | Decided on coercion only, and this is stated |
| 13 | Trusted beneficiary **but** screen sharing is on | Trusted | HOLD (trust doesn't override severe) |

---

## 10. Testing and evaluation (Phase 6): the mentor's requirement

### 10a. Unit and API tests (pytest)
Cover model loading and feature-order validation, the feature computation for each of the 9 features, each rule, answer-vs-signal checks, splitting detection, every row of the fusion matrix, the "unknown payee + high amount alone ≠ coercion" rule, the "trust never overrides severe" rule, input validation errors and all 13 scenarios.

### 10b. System evaluation (`scripts/evaluate_system.py`)
Build a **joint evaluation set**. Take the 30% held-out test rows of the coercion CSV and pair each one with a demo payee profile:
- Most coerced rows get a **clean, new payee**. This is realistic, because fresh or rented mule accounts often have no complaints yet; the DATA_CARD says only 30% have complaints at payment time.
- Some coerced rows get a mule-like payee.
- Most genuine rows get clean payees, and a small share get mule-like payees.

Document the pairing ratios and the random seed. Ground truth = `should_intervene` = coerced **or** payee is a known mule.

Compare these approaches **on the same set**:
1. Rule baselines from the DATA_CARD (new payee + ≥5× amount; new payee + unknown call)
2. **Mule-only** (mule model at 0.10)
3. **Coercion-only** (rules + ML)
4. **Combined SafePay Shield** (fusion engine)

For each one, report: detection rate (recall), precision, F1, false-positive rate overall **and on hard negatives**, recall by scam type, the share of decisions at each level (ALLOW/WARN/HOLD), and **response time** (p50 and p95 ms per `/api/assess` call over 1,000 calls).

Also report the mule model's own metrics, but **only if** the RBIH data is present.

Save the results to `reports/evaluation.json`, `reports/evaluation.csv` and PNG charts (a comparison bar chart, recall by scam type and FP rate by hard-negative type). The Evaluation screen reads these files.

The headline result to check honestly, not assume: **how many coerced payments to clean payees does mule-only miss, and how many of those does the combined system catch?**

### 10c. Honest reporting
If a result comes out worse than expected, report it as it is. Do not tune thresholds on the test set. If you tune anything, use a separate validation split and document it.

---

## 11. Deliverables

1. The folder structure above, with working code
2. `requirements.txt` (pin `scikit-learn==1.6.1`; include flask, pandas, numpy, joblib, shap, matplotlib, pytest, and xgboost as optional)
3. `README.md`: what it is, the architecture diagram (ASCII or Mermaid), setup (`python -m venv`, `pip install -r requirements.txt`, `python -m safepay.seed`, `python scripts/train_coercion_model.py`, `python scripts/evaluate_system.py`, `flask run`), the demo walkthrough of the 13 scenarios, the fusion logic, privacy notes, and a **Limitations** section (synthetic data; call-state/telemetry access varies by phone and needs DPDP consent; single-payment view of multi-day scams; next step is calibration through the RBIH sandbox)
4. All tests pass (`pytest -q`)
5. A short `DEMO_SCRIPT.md`: a 3-minute jury walkthrough. Scenario 1 (Ramesh, clean payee, HOLD), then Scenario 7 (genuine hospital bill, WARN not HOLD), then Scenario 13 (trust doesn't override screen sharing), then the Evaluation screen.

---

## 12. Work in this order. Stop after each phase and summarise what works.

1. **Phase 1:** Inspect the files and confirm model compatibility. Report the findings.
2. **Phase 2:** Show the architecture and folder structure.
3. **Phase 3:** Flask skeleton, SQLite schema and seed data.
4. **Phase 4:** Mule module, SHAP and tests.
5. **Phase 5:** Coercion rules, the ML model, answer-check, profiling, splitting, the fusion engine and tests.
6. **Phase 6:** APIs, the 13 scenarios and the evaluation script with real results.
7. **Phase 7:** Frontend (simulated UPI app, Why panel, dashboard, evaluation and settings), multilingual text and simulated alerts.
8. **Phase 8:** README, DEMO_SCRIPT and a final full run of tests, scenarios and evaluation.

Priority (from my mentor): **first get both models working, integrated into one risk engine, inside an end-to-end simulated payment flow.** Extras come after the core flow works.
