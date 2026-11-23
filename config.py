"""SafePay Shield configuration.

Every threshold, weight and path lives here, with the reason for its value.
Nothing in this file is tuned on a test set. Values that later phases will set
from a validation split are left as None until then, and code that needs them
refuses to run while they are None.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- paths
MODELS_DIR = BASE_DIR / "models"
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
DB_PATH = Path(os.environ.get("SAFEPAY_DB_PATH", DATA_DIR / "safepay.db"))

MULE_MODEL_PATH = MODELS_DIR / "safepay_mule_model.pkl"   # provided, read-only copy
COERCION_DATA_DIR = DATA_DIR / "coercion"                 # unzipped synthetic dataset
COERCION_CSV = COERCION_DATA_DIR / "safepay_coercion_synthetic.csv"

# RBIH-NFPC Phase 2 data (about 16 GB) is NOT copied into the project. It is read
# in place from this folder. Override it with the SAFEPAY_RBIH_DIR environment variable.
RBIH_DATA_DIR = Path(os.environ.get("SAFEPAY_RBIH_DIR", r"C:\Users\Anuja\Downloads\dataset fintech"))

# Demo payee histories derived from real RBIH accounts (built by
# scripts/build_demo_payees.py; IDs pseudonymised; provenance in the JSON).
DEMO_PAYEE_HISTORIES = DATA_DIR / "demo" / "rbih_payee_histories.csv.gz"
DEMO_PAYEE_PROVENANCE = DATA_DIR / "demo" / "rbih_payee_provenance.json"

# ------------------------------------------------------------------ mule model (A)
# The exact feature order the provided RandomForest was trained on. Checked at
# startup against model.feature_names_in_. The mule module refuses to start on a mismatch.
MULE_FEATURES = [
    "total_transactions", "total_debit", "total_credit", "avg_transaction_amount",
    "unique_counterparties", "upi_transaction_count", "incoming_counterparties",
    "outgoing_counterparties", "reversal_count",
]
MULE_THRESHOLD = 0.10      # stored in the pkl; flag = probability >= 0.10
MULE_HIGH_BAND = 0.40      # demo choice: elevated = [0.10, 0.40), high = >= 0.40

# Minimum transaction history before the mule model is called.
# Why 5: probing the provided model with 200 simulated clean histories per size,
# clean payees were flagged (>= 0.10) 100% of the time at 1 transaction, 53% at 2
# and 31% at 3, but only 1-6% from 5 to 15. An all-zero history scores 0.747.
# Below 5, the score reflects how little data there is, not how money moves.
# Below this count -> status "insufficient_history" and the model is NOT called;
# the fusion engine then decides on coercion signals alone and says so.
# Evidence: scripts/probe_mule_history.py -> reports/mule_history_probe.json
MULE_MIN_HISTORY_TXNS = 5

# RBIH channel codes counted as UPI (README: UPC = UPI Credit, UPD = UPI Debit).
UPI_CHANNELS = ("UPC", "UPD")

# ------------------------------------------------------------- coercion rules (B)
# Weights are set by hand from the DATA_CARD evidence, before seeing any results:
#   1 = weak sign on its own, 2 = clear sign, 3 = strong sign, severe = forces HOLD.
RULE_SEVERE_WEIGHT = 5
RULE_WEIGHTS = {
    "govt_verification_purpose": 3,     # authority impersonation purpose (digital arrest, courier)
    "authority_claim": 3,               # victim checklist
    "international_caller": 2,
    "unknown_caller_during_payment": 2, # baseline rule "new payee + unknown call": precision 0.85
    "night_payment": 1,
    "high_hesitation": 1,
    "segmented_typing": 1,
    "repeated_new_payee_payments": 2,
    "high_new_payee_volume": 1,
    "savings_liquidated": 2,
    "guided_by_known_contact": 1,       # families helping honestly say yes ~45% (DATA_CARD)
    "guided_no_call": 1,                # revision v2: validation precision 0.22 with no call active
    # context: counted only when at least one pressure indicator fired
    "vpa_pasted": 1,
    "frequent_app_switching": 1,
    "unknown_call_and_new_payee": 2,
    "new_payee_large_amount": 2,        # baseline "new + 5x amount" alone: 47% false alarms on hard negatives
    "large_share_of_balance": 1,
}
CONTRADICTION_WEIGHT = 2              # strong answer-vs-signal contradiction (answer_check.py)
CONTRADICTION_SOFT_WEIGHT = 1         # soft contradiction (revision v2, validation precision 0.24)

DIGITAL_ARREST_VIDEO_MIN = 60         # video call with an unknown number at least this long
NIGHT_HOURS = (23, 0, 1, 2, 3, 4, 5)
HIGH_HESITATION_EVENTS = 4
FREQUENT_APP_SWITCHES = 4
REPEAT_NEW_PAYEE_TXNS_24H = 2
HIGH_NEW_PAYEE_VOLUME_24H = 50_000
LARGE_AMOUNT_RATIO = 5                # same 5x used by the DATA_CARD baseline rule
HIGH_PCT_OF_BALANCE = 50
# Pressure gate for the ML model: with no pressure indicator, if at least this share of the
# model's risk-raising SHAP evidence comes from payee/amount features, the model's concern is
# "payee and amount alone" and is not counted toward the coercion band. Hand-set (a clear
# majority); its effect is measured by scripts/evaluate_system.py.
PAYEE_AMOUNT_ONLY_SHARE = 0.75
NEW_PAYEE_DAYS = 7                   # payee is "new" if the first payment to it was < 7 days ago

# ------------------------------------------------------------- coercion model (B)
# Chosen by scripts/train_coercion_model.py on a VALIDATION split carved from the 70%
# training portion (never the 30% test set), and written to models/coercion_model_card.json.
# The app reads them from the model card, not from here. These stay None so nothing
# can use an unvalidated cut-off by accident.
COERCION_MEDIUM = None
COERCION_HIGH = None
RULE_SCORE_CAP = None
COERCION_MODEL_PATHS = {
    "pre_pause": MODELS_DIR / "coercion_model_prepause.pkl",
    "post_pause": MODELS_DIR / "coercion_model_postpause.pkl",
}
COERCION_MODEL_CARD = MODELS_DIR / "coercion_model_card.json"

# --------------------------------------------------- behaviour profile / splitting
UNUSUAL_AMOUNT_RATIO = 3              # deviation note when amount >= 3x the usual average
SPLIT_WINDOW_MIN = 30                 # rolling window for rapid repeats / near-limit splits
SPLIT_MIN_TXNS = 3                    # including the current payment
ESCALATION_WINDOW_H = 24
ROUND_LIMITS = (10_000, 20_000, 50_000, 100_000)
NEAR_LIMIT_PCT = 2                    # "just under": within 2% below a round limit (e.g. 9,999)

# ------------------------------------------------------------------- fusion
DECISIONS = ("ALLOW", "WARN", "HOLD")

# --------------------------------------------------------------- payment limits
AMOUNT_MIN_INR = 1
AMOUNT_MAX_INR = 100_000    # UPI P2P per-transaction limit

# ------------------------------------------------------------------- response
COOLING_OFF_MINUTES = 30
HELPLINE_NUMBER = "1930"
HELPLINE_URL = "https://cybercrime.gov.in"
SUPPORTED_LANGUAGES = ("en", "hi", "mr")
DEFAULT_LANGUAGE = "en"

# Every random choice in seeding and evaluation uses this.
RANDOM_SEED = 42
