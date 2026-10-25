"""
Configuration for the Bank Statement Forensic Analyzer.

Detection thresholds are deliberately kept here, in one place, so an
analyst can tune them for a case without touching the detection code.

Author: Muhammad Ibrahim Gimba
"""

from pathlib import Path

# PATHS

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
DEMO_CASE_DIR = DATA_DIR / "demo_case"
DEMO_TRUTH_FILE = DEMO_CASE_DIR / "ground_truth.json"

REPORTS_DIR = PROJECT_ROOT / "reports"
ACCURACY_REPORT = REPORTS_DIR / "accuracy.json"

# RANDOMNESS

RANDOM_STATE = 42

# CURRENCY TRANSACTION REPORTING
# Money Laundering (Prevention and Prohibition) Act 2022: cash
# transactions above NGN 5m (individuals) / NGN 10m (corporates) must be
# reported. Splitting payments to stay just under such limits is
# "structuring".

REPORTING_THRESHOLD_INDIVIDUAL = 5_000_000
REPORTING_THRESHOLD_CORPORATE = 10_000_000

# Amounts within this share below a threshold count as "just under".
STRUCTURING_BAND = 0.10
STRUCTURING_MIN_COUNT = 3
STRUCTURING_WINDOW_DAYS = 14

# PASS-THROUGH (MULE) ACCOUNTS
# Most of what comes in leaves again within a short window.

PASS_THROUGH_WINDOW_HOURS = 48
PASS_THROUGH_MIN_INFLOW = 500_000
PASS_THROUGH_OUTFLOW_RATIO = 0.80
PASS_THROUGH_MIN_EVENTS = 2
# The money going out must be the money that came in: the balance held
# before the inflow may be at most this multiple of the inflow. Excludes
# businesses paying suppliers from accumulated savings.
PASS_THROUGH_MAX_PRIOR_BALANCE_RATIO = 1.0
# Mules receive large transfers; many small customer payments are retail.
PASS_THROUGH_MIN_AVERAGE_CREDIT = 100_000

# FAN-IN / FAN-OUT

FAN_WINDOW_DAYS = 7
FAN_IN_MIN_SENDERS = 8
FAN_OUT_MIN_RECIPIENTS = 8
FAN_MIN_TOTAL = 1_000_000
# Only transfers of at least this size count towards the number of
# senders/recipients. Excludes retail receipts and everyday small
# payments that would otherwise pad the count.
FAN_MIN_TRANSFER = 100_000

# ROUND-TRIPPING
# Money that leaves an account and returns to it through other accounts.

ROUND_TRIP_MAX_LENGTH = 4
ROUND_TRIP_MAX_DAYS = 30
ROUND_TRIP_MIN_AMOUNT = 1_000_000
ROUND_TRIP_AMOUNT_TOLERANCE = 0.25

# DORMANT ACCOUNT REACTIVATION

DORMANT_MIN_DAYS = 90
DORMANT_REACTIVATION_MIN_VOLUME = 2_000_000
DORMANT_REACTIVATION_WINDOW_DAYS = 30

# RAPID CASH-OUT
# Large inflow followed quickly by ATM / POS / cash withdrawals.

CASH_OUT_WINDOW_HOURS = 24
CASH_OUT_MIN_AMOUNT = 300_000
# Mules often withdraw only part in cash and forward the rest.
CASH_OUT_RATIO = 0.25

# RISK SCORING (points per finding, capped at 100)

RULE_WEIGHTS = {
    "pass_through": 30,
    "structuring": 30,
    "fan_in": 20,
    "fan_out": 20,
    "round_trip": 35,
    "dormant_reactivation": 15,
    "rapid_cash_out": 20,
}

# Network risk: points for each high-risk subject an account exchanges
# at least NETWORK_LINK_MIN_AMOUNT with (capped).
NETWORK_LINK_POINTS = 10
NETWORK_LINK_CAP = 30
NETWORK_LINK_MIN_AMOUNT = 500_000
NETWORK_HIGH_RISK_SCORE = 50

RISK_BANDS = [
    (0, "Low"),
    (25, "Medium"),
    (50, "High"),
    (75, "Critical"),
]
