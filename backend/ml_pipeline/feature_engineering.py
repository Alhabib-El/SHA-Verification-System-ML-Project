"""
ml_pipeline/feature_engineering.py
Stage 2 & 3 of the verification pipeline (Figure 5.3 / Table 5.2):
Data Preprocessing and Feature Engineering.

Produces the 17-feature vector specified in Table 5.3 of Chapter Five.
"""
from datetime import date, timedelta
from typing import Dict
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import text

# Fixed feature order — MUST match the order used during model training
FEATURE_ORDER = [
    "amount_ratio",
    "provider_claim_freq_30d",
    "provider_amount_zscore",
    "diagnosis_procedure_match",
    "submission_delay_days",
    "patient_facility_count_7d",
    "provider_age_days",
    "facility_type_encoded",
    "county_encoded",
    "diagnosis_category_encoded",
    "claimed_amount_log",
    "tariff_amount_log",
    "provider_approval_rate",
    "patient_claim_count_90d",
    "service_day_of_week",
    "coverage_package_encoded",
    "repeat_diagnosis_flag",
]

# Simple lookup encoders (in production these are fitted + persisted with joblib)
FACILITY_TYPE_MAP = {"Hospital": 0, "Clinic": 1, "Pharmacy": 2, "Laboratory": 3}
COUNTY_MAP = {"Nairobi": 0, "Kisumu": 1, "Mombasa": 2, "Nakuru": 3, "Eldoret": 4}
PACKAGE_MAP = {"SHIF Basic": 0, "Primary Healthcare Fund": 1, "Chronic Illness Fund": 2}

# ICD-10 chapter grouping (simplified — production uses a full lookup table)
DIAGNOSIS_CATEGORY_MAP = {
    "A000":   0,   # Cholera
    "A01000": 1,  # Typhoid fever
    "A021":   2,  # Salmonella infection
    "S14116D":   3,  # Lesion at C6
    "Z131":   4,  # Screening for diabetes mellitus
}

# Clinically valid diagnosis-procedure pairs (simplified rule table)
VALID_DIAGNOSIS_PROCEDURE_PAIRS = {
    ("A000", "99232"), ("A000", "99233"),
    ("A01000", "99232"),
    ("A021", "44950"),
    ("S14116D", "59400"),
    ("Z131", "83036"),
}


def check_clinical_match(diagnosis_code: str, procedure_code: str) -> bool:
    """Stage 1 pre-check: validates diagnosis-procedure clinical consistency."""
    return (diagnosis_code, procedure_code) in VALID_DIAGNOSIS_PROCEDURE_PAIRS


def get_provider_claim_frequency_30d(db: Session, provider_id: str) -> int:
    result = db.execute(text("""
        SELECT COUNT(*) FROM claims
        WHERE provider_id = :pid
          AND submission_date >= NOW() - INTERVAL '30 days'
    """), {"pid": provider_id}).scalar()
    return result or 0


def get_provider_amount_zscore(db: Session, provider_id: str, claimed_amount: float) -> float:
    """Z-score of this claim's amount vs the provider's own historical claims."""
    row = db.execute(text("""
        SELECT AVG(claimed_amount) AS mean_amt, STDDEV(claimed_amount) AS std_amt
        FROM claims WHERE provider_id = :pid
    """), {"pid": provider_id}).fetchone()
    if not row or row.std_amt is None or row.std_amt == 0:
        return 0.0
    return float((claimed_amount - row.mean_amt) / row.std_amt)


def get_patient_facility_count_7d(db: Session, patient_id: str) -> int:
    result = db.execute(text("""
        SELECT COUNT(DISTINCT provider_id) FROM claims
        WHERE patient_id = :pid
          AND submission_date >= NOW() - INTERVAL '7 days'
    """), {"pid": patient_id}).scalar()
    return result or 0


def get_provider_age_days(db: Session, provider_id: str) -> int:
    row = db.execute(text("""
        SELECT empanelment_date FROM health_providers WHERE provider_id = :pid
    """), {"pid": provider_id}).fetchone()
    if not row:
        return 0
    return (date.today() - row.empanelment_date).days


def get_provider_approval_rate(db: Session, provider_id: str) -> float:
    row = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'approved')::float
            / NULLIF(COUNT(*), 0) AS rate
        FROM claims
        WHERE provider_id = :pid
          AND submission_date >= NOW() - INTERVAL '12 months'
    """), {"pid": provider_id}).fetchone()
    return float(row.rate) if row and row.rate is not None else 0.85  # neutral prior for new providers


def get_patient_claim_count_90d(db: Session, patient_id: str) -> int:
    result = db.execute(text("""
        SELECT COUNT(*) FROM claims
        WHERE patient_id = :pid
          AND submission_date >= NOW() - INTERVAL '90 days'
    """), {"pid": patient_id}).scalar()
    return result or 0


def get_repeat_diagnosis_flag(db: Session, patient_id: str, provider_id: str, diagnosis_code: str) -> int:
    result = db.execute(text("""
        SELECT COUNT(*) FROM claims
        WHERE patient_id = :pid AND provider_id = :prov
          AND diagnosis_code = :diag
          AND submission_date >= NOW() - INTERVAL '30 days'
    """), {"pid": patient_id, "prov": provider_id, "diag": diagnosis_code}).scalar()
    return 1 if (result or 0) > 0 else 0


def build_feature_vector(db: Session, claim: dict, provider: dict, tariff_amount: float) -> np.ndarray:
    """
    Assembles the 17-feature vector for a single claim, in FEATURE_ORDER.
    `claim` and `provider` are plain dicts pulled from the ORM objects.
    """
    amount_ratio = claim["claimed_amount"] / tariff_amount if tariff_amount else 1.0
    submission_delay_days = (claim["submission_date"].date() - claim["service_date"]).days

    features: Dict[str, float] = {
        "amount_ratio": amount_ratio,
        "provider_claim_freq_30d": get_provider_claim_frequency_30d(db, provider["provider_id"]),
        "provider_amount_zscore": get_provider_amount_zscore(db, provider["provider_id"], claim["claimed_amount"]),
        "diagnosis_procedure_match": int(check_clinical_match(claim["diagnosis_code"], claim["procedure_code"])),
        "submission_delay_days": submission_delay_days,
        "patient_facility_count_7d": get_patient_facility_count_7d(db, claim["patient_id"]),
        "provider_age_days": get_provider_age_days(db, provider["provider_id"]),
        "facility_type_encoded": FACILITY_TYPE_MAP.get(provider["facility_type"], -1),
        "county_encoded": COUNTY_MAP.get(provider["county"], -1),
        "diagnosis_category_encoded": DIAGNOSIS_CATEGORY_MAP.get(claim["diagnosis_code"], -1),
        "claimed_amount_log": float(np.log1p(claim["claimed_amount"])),
        "tariff_amount_log": float(np.log1p(tariff_amount)) if tariff_amount else 0.0,
        "provider_approval_rate": get_provider_approval_rate(db, provider["provider_id"]),
        "patient_claim_count_90d": get_patient_claim_count_90d(db, claim["patient_id"]),
        "service_day_of_week": claim["service_date"].weekday(),
        "coverage_package_encoded": PACKAGE_MAP.get(claim.get("coverage_package", "SHIF Basic"), 0),
        "repeat_diagnosis_flag": get_repeat_diagnosis_flag(
            db, claim["patient_id"], provider["provider_id"], claim["diagnosis_code"]
        ),
    }

    # Return as ordered numpy array matching FEATURE_ORDER exactly
    return np.array([features[f] for f in FEATURE_ORDER]).reshape(1, -1)
