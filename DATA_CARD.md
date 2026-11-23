# SafePay Shield: Synthetic Victim-Coercion Dataset (v1)

**What it is:** 20,000 synthetic UPI payment attempts, as the payer's app would see them at payment time. Each row is labeled **coerced** (the real account holder is paying because a scammer is pressuring or coaching them) or **genuine**.
**Why synthetic:** no public dataset labels coercion from the payer's side, because payer-side signals stay inside bank and UPI apps. The RBIH mule dataset covers the **payee**. This dataset covers the **payer**, so the hybrid design can be prototyped end to end.
**Method:** PaySim-style parameterised simulation with injected fraud scenarios (Lopez-Rojas et al.). Each scam scenario's signals come from published fraud research and reports (see the table below).

> ⚠️ This is a synthetic, research-informed dataset for prototyping and demonstration. It is **not** real transaction data. Model metrics on it show the design is feasible; they are not real-world performance.

## Composition (default: `--n 20000 --coerced-rate 0.12 --hard-share 0.25 --label-noise 0.01 --seed 42`)

| Group | Rows | Meaning |
|---|---|---|
| `positive` | 2,400 | Coerced payments (label 1), 7 scam types |
| `hard_negative` | 4,400 | Genuine payments that **look risky** (label 0) |
| `normal_negative` | 13,200 | Everyday genuine payments (label 0) |

About 1% of labels are flipped on purpose (`label_noisy = 1`). This mimics real labels that are wrong because some scams go unreported and some fraud claims are false.
Real coercion prevalence is far lower than 12%. The rate was raised for training; use `--coerced-rate` to simulate a realistic base rate.

**Scam types (positives):** investment_trading 28%, bank_kyc_impersonation 20%, digital_arrest 15%, job_task 15%, courier_customs 8%, family_emergency_voiceclone 8%, romance 6%.

**Hard negatives:** large payment to a new payee (hospital, property token, fees), on a call with family while paying, senior paying family, FD broken for a genuine need, urgent night payment, first-time payment to a genuine investment platform.

## How the research shaped the signals

| Signal in dataset | Evidence | How it is modelled |
|---|---|---|
| `on_voice_call`, `on_video_call`, `caller_in_contacts`, `call_duration_min` | About 1 in 4 APP scams involve a live call; active-call detection raised in-progress APP detection by 120% in UK bank trials (LexisNexis). Telco "scam signal" from live calls (FICO + JT). | Call is very likely for impersonation, courier and voice-clone scams. Digital arrest uses long video calls (median about 140 min). Investment, job and romance scams rarely involve a call. |
| `hesitation_events`, `segmented_typing`, `session_duration_sec`, `app_switches` | Coaching shows up as segmented typing, dead time and hesitation (BioCatch, Transmit Security, Ping Identity). Manipulated users type at unusual speeds and hesitate more (ITM Conf. 2026). | A latent "stress" level drives all of these. It is high for fear-based scams and low for greed-based ones. Seniors and urgent genuine cases also get moderate stress, so these signals alone can't decide the label. |
| `screen_share_active`, `remote_access_app_installed_7d` | Remote-access tools and screen overlays are coercion signs (LexisNexis ThreatMetrix, industry analyses). | Common mainly in KYC impersonation (about 35–40%). |
| `payee_is_new`, `amount_to_avg_ratio`, `amount_pct_of_balance` | Coached victims send money to a new payee (Mastercard/NuData). Manipulated payments look legitimate from the transaction side (BioCatch, OneSpan). | High for positives, but hard negatives share these on purpose. |
| `payee_vpa_age_days`, `payee_prior_complaints` | Mule-account behaviour (link to the RBIH mule dataset). | About 55% of mule accounts are newly opened and 45% are older rented accounts. Only 30% have complaints at payment time. |
| `fd_or_loan_liquidated_7d`, `new_payee_txns_24h` | Digital arrest victims are pushed to liquidate savings over repeated transfers (I4C/MHA case reporting). | High for digital arrest. Escalating payments for job and investment scams. |
| `ans_know_payee`, `ans_someone_guiding`, `ans_purpose` | Victims are coached to lie when the bank intervenes (scam-warning practice). | Only 5–35% of coerced users admit someone is guiding them. Families helping honestly say "yes" about 45% of the time. |
| Scam mix, ages, amounts | India: digital arrest cases rose to 1,23,672 in 2024 (₹1,918 cr) and fell to 17,264 in 2025 (I4C via ThePrint). Seniors are prime targets. Investment scams lead losses (UK Finance; I4C). | Ages skew older for digital arrest and voice clone (mean about 58–60) and younger for job scams (mean about 27). Amounts are capped at the ₹1 lakh UPI P2P limit, and larger losses show up across repeated transactions. |

## Columns

| Column | Type | Description |
|---|---|---|
| txn_id | str | Unique ID |
| hour_of_day | int | 0–23 |
| user_age, account_tenure_months | int | Payer profile |
| avg_txn_amount_90d, balance_before_txn | float | Payer's normal spend and balance |
| amount_inr | float | This payment (≤ ₹1,00,000) |
| amount_to_avg_ratio, amount_pct_of_balance | float | Deviation from the payer's normal pattern |
| payee_is_new, payee_in_contacts | 0/1 | Payee familiarity |
| payee_type | P2P/P2M | Person or merchant |
| payee_vpa_age_days, payee_prior_complaints | int | Payee risk (stand-in for the mule score) |
| on_voice_call, on_video_call, call_duration_min | 0/1, float | Ongoing call during the payment |
| caller_in_contacts, caller_is_international | 0/1 | Caller identity |
| screen_share_active, remote_access_app_installed_7d | 0/1 | Device-control signals |
| session_duration_sec, hesitation_events, segmented_typing, app_switches, vpa_pasted | num | Behavioural signals |
| new_payee_txns_24h, amount_sent_new_payees_24h | num | Velocity |
| fd_or_loan_liquidated_7d | 0/1 | Savings liquidation |
| ans_know_payee | yes/unsure/no | Pause-screen question |
| ans_someone_guiding | yes/no | Pause-screen question |
| ans_purpose | category | Stated purpose |
| **label_coerced** | 0/1 | **Target** |
| scam_type, sample_group, hard_negative_reason, label_noisy | meta | For analysis only. **Exclude them from training.** |

## Baseline results (70/30 split; see `baseline_results.txt`)

| Model | TP | TN | FP | FN | Precision | Recall | AUC | False alarms on hard negatives |
|---|---|---|---|---|---|---|---|---|
| Rule: new payee + amount ≥5× average | 526 | 4572 | 658 | 244 | 0.44 | 0.68 | n/a | **47%** |
| Rule: new payee + call from an unknown number | 284 | 5181 | 49 | 486 | 0.85 | 0.37 | n/a | 3% |
| Logistic Regression | 693 | 4883 | 347 | 77 | 0.67 | 0.90 | 0.954 | 21% |
| Random Forest | 679 | 5075 | 155 | 91 | 0.81 | 0.88 | 0.960 | 11% |

**Points for the pitch:**
- An amount and new-payee rule would interrupt almost half of genuine urgent payments. Combining call, behaviour and payee signals cuts that to about 11% and still catches 88% of coercion cases.
- Call-based scams (digital arrest, KYC impersonation, courier, voice clone) are caught more than 95% of the time. **Investment (about 90%) and romance (about 80%) scams are harder** because the victim is willing and calm. The payee mule score and the pause questions matter most there.

## Limitations (say these openly to the jury)
1. The distributions are assumptions drawn from published reports, not fitted to real Indian UPI logs. Results show feasibility, not real-world accuracy.
2. Not every phone can read call state or behavioural telemetry, and some apps restrict it. A real SDK needs user consent under the DPDP Act, 2023.
3. Each row is a single payment, so the multi-day buildup of investment and romance scams is only approximated through the 24h velocity features.
4. Next step: calibrate against bank or NPCI data through the RBIH sandbox, and join `payee_*` columns with the mule-score model trained on the RBIH dataset.

## Regenerate
```
python generate_coercion_dataset.py --n 20000 --coerced-rate 0.12 --seed 42
python evaluate_baseline.py safepay_coercion_synthetic.csv
```

## Sources
- LexisNexis Risk Solutions: active call detection and APP fraud (via The Scotsman); ThreatMetrix scam signals
- FICO and JT telco scam signal (Finextra)
- BioCatch: vishing and coaching detection; Transmit Security and Ping Identity behavioural-biometrics glossaries
- Yuvaraja P. & Sathyanarayanan R., "Social Engineering Detection Using Behavioral Data and Form-Usage Patterns," ITM Web of Conferences, 2026, doi:10.1051/itmconf/20268502001
- Mastercard (NuData): behavioural biometrics against coached social-engineering scams
- I4C/MHA digital arrest statistics (ThePrint, 2026); UK Finance Annual Fraud Reports 2024–2026
- Lopez-Rojas, Elmir & Axelsson, "PaySim: A financial mobile money simulator for fraud detection," EMSS 2016
