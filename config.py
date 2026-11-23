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

# ------------------------------------------------------------- coercion model (B)
# Set in Phase 5 from a validation split carved out of the 70% training portion.
COERCION_MEDIUM = None
COERCION_HIGH = None

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
