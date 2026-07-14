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

# ICD-10 chapter grouping, derived from the code's leading letter + numeric
# range (per the WHO ICD-10-CM chapter structure) rather than a hand-maintained
# per-code table — so any diagnosis_code in sha_tariffs (or a new one added
# later) is categorized automatically without needing a code change here.
ICD10_CHAPTER_RANGES = [
    ("A", 0,  "B", 99, 0),   # Certain infectious and parasitic diseases
    ("C", 0,  "D", 49, 1),   # Neoplasms
    ("D", 50, "D", 89, 2),   # Diseases of the blood and blood-forming organs
    ("E", 0,  "E", 90, 3),   # Endocrine, nutritional and metabolic diseases
    ("F", 0,  "F", 99, 4),   # Mental, behavioral and neurodevelopmental disorders
    ("G", 0,  "G", 99, 5),   # Diseases of the nervous system
    ("H", 0,  "H", 59, 6),   # Diseases of the eye and adnexa
    ("H", 60, "H", 95, 7),   # Diseases of the ear and mastoid process
    ("I", 0,  "I", 99, 8),   # Diseases of the circulatory system
    ("J", 0,  "J", 99, 9),   # Diseases of the respiratory system
    ("K", 0,  "K", 95, 10),  # Diseases of the digestive system
    ("L", 0,  "L", 99, 11),  # Diseases of the skin and subcutaneous tissue
    ("M", 0,  "M", 99, 12),  # Diseases of the musculoskeletal system
    ("N", 0,  "N", 99, 13),  # Diseases of the genitourinary system
    ("O", 0,  "O", 99, 14),  # Pregnancy, childbirth and the puerperium
    ("P", 0,  "P", 96, 15),  # Certain conditions originating in the perinatal period
    ("Q", 0,  "Q", 99, 16),  # Congenital malformations
    ("R", 0,  "R", 99, 17),  # Symptoms, signs and abnormal clinical findings
    ("S", 0,  "T", 88, 18),  # Injury, poisoning and other consequences of external causes
    ("V", 0,  "Y", 99, 19),  # External causes of morbidity
    ("Z", 0,  "Z", 99, 20),  # Factors influencing health status
]


def encode_diagnosis_category(diagnosis_code: str) -> int:
    """Maps an ICD-10 code to its chapter category via ICD10_CHAPTER_RANGES."""
    if not diagnosis_code:
        return -1
    code = diagnosis_code.replace(".", "").upper()
    letter = code[0]
    try:
        number = int(code[1:3])
    except ValueError:
        return -1
    for start_letter, start_num, end_letter, end_num, category in ICD10_CHAPTER_RANGES:
        if letter < start_letter or letter > end_letter:
            continue
        if letter == start_letter and number < start_num:
            continue
        if letter == end_letter and number > end_num:
            continue
        return category
    return -1


def check_clinical_match(db: Session, diagnosis_code: str, procedure_code: str) -> bool:
    """
    Stage 1 pre-check: a diagnosis/procedure pairing is clinically valid if
    it appears in the current SHA tariff schedule — the same table that
    defines which combinations are reimbursable, kept as the single source
    of truth instead of a separately hand-maintained rule table.
    """
    row = db.execute(text("""
        SELECT 1 FROM sha_tariffs
        WHERE diagnosis_code = :d AND procedure_code = :p
        LIMIT 1
    """), {"d": diagnosis_code, "p": procedure_code}).fetchone()
    return row is not None


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
    return float((claimed_amount - float(row.mean_amt)) / float(row.std_amt))


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
    claimed_amount = float(claim["claimed_amount"])   # Postgres NUMERIC comes back as Decimal
    amount_ratio = claimed_amount / tariff_amount if tariff_amount else 1.0
    submission_delay_days = (claim["submission_date"].date() - claim["service_date"]).days

    features: Dict[str, float] = {
        "amount_ratio": amount_ratio,
        "provider_claim_freq_30d": get_provider_claim_frequency_30d(db, provider["provider_id"]),
        "provider_amount_zscore": get_provider_amount_zscore(db, provider["provider_id"], claimed_amount),
        "diagnosis_procedure_match": int(check_clinical_match(db, claim["diagnosis_code"], claim["procedure_code"])),
        "submission_delay_days": submission_delay_days,
        "patient_facility_count_7d": get_patient_facility_count_7d(db, claim["patient_id"]),
        "provider_age_days": get_provider_age_days(db, provider["provider_id"]),
        "facility_type_encoded": FACILITY_TYPE_MAP.get(provider["facility_type"], -1),
        "county_encoded": COUNTY_MAP.get(provider["county"], -1),
        "diagnosis_category_encoded": encode_diagnosis_category(claim["diagnosis_code"]),
        "claimed_amount_log": float(np.log1p(claimed_amount)),
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
