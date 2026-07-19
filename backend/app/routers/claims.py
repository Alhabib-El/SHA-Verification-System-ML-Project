"""
app/routers/claims.py
FR-01, FR-02, FR-03, FR-04, FR-11 — claim submission and status tracking.

FIXES:
  - /tariff-lookup moved BEFORE /{claim_id}/status to avoid route collision
  - ClaimsVerificationPipeline loaded lazily (not at module level)
    so the app starts even if the model file does not yet exist
"""
import uuid
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db
from ..schemas import ClaimSubmitRequest, ClaimResponse
from ..auth import require_role

router = APIRouter(prefix="/claims", tags=["Claims"])

# A claim's service_date must be today — no backdating or forward-dating —
# enforced server-side (not just in the UI) so it can't be bypassed by
# calling the API directly.


def _get_pipeline():
    """Load the pipeline lazily so startup does not crash if model not trained yet.
    Keeps the API usable (submission still works, just without ML scoring)
    even before the first training run has produced a .joblib artifact."""
    try:
        from ml_pipeline.pipeline import ClaimsVerificationPipeline
        return ClaimsVerificationPipeline()
    except Exception as e:
        return None


# ── FORM-POPULATING LOOKUPS ───────────────────────────────────────────────────
# These two endpoints exist purely so the Submit Claim screen's dropdowns are
# always driven by live database content instead of a hardcoded list that
# would silently go stale every time an admin adds a member or tariff.

@router.get("/members")
def list_members(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("provider", "officer", "admin")),
):
    """Powers the SHA member no. dropdown on the Submit Claim screen —
    reads directly from sha_members instead of requiring free-text entry."""
    rows = db.execute(text("""
        SELECT sha_member_no, full_name, eligibility_status
        FROM sha_members
        ORDER BY full_name
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/tariffs")
def list_tariff_codes(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("provider", "officer", "admin")),
):
    """Powers the diagnosis/procedure dropdowns on the Submit Claim screen —
    reads directly from sha_tariffs so the form always matches the current
    approved schedule instead of a hardcoded list."""
    rows = db.execute(text("""
        SELECT diagnosis_code, procedure_code, facility_tier, approved_amount
        FROM sha_tariffs
        WHERE effective_to IS NULL
        ORDER BY diagnosis_code, procedure_code
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


# ── FIXED: tariff-lookup MUST be before /{claim_id}/status ───────────────────
@router.get("/tariff-lookup")
def lookup_tariff(
    diagnosis_code: str,
    procedure_code: str,
    db: Session = Depends(get_db),
):
    """Auto-populates SHA tariff in the claim submission form."""
    tariff = db.execute(text("""
        SELECT approved_amount FROM sha_tariffs
        WHERE diagnosis_code = :d AND procedure_code = :p
          AND effective_to IS NULL
        ORDER BY effective_from DESC LIMIT 1
    """), {"d": diagnosis_code, "p": procedure_code}).fetchone()
    if not tariff:
        raise HTTPException(404, "No tariff found for this diagnosis-procedure combination")
    return {"approved_amount": float(tariff.approved_amount)}


# ── CLAIM SUBMISSION (FR-01) ──────────────────────────────────────────────────
@router.post("/submit", status_code=201)
def submit_claim(
    payload: ClaimSubmitRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("provider", "admin")),
):
    """
    FR-01: Healthcare provider submits a new claim. End-to-end flow:
      1. Reject if service_date isn't exactly today (anti-backdating rule).
      2. Resolve the SHA member number to a patient_id.
      3. Look up the current approved tariff for this diagnosis/procedure
         pair, to store alongside the claim as sha_tariff_amount.
      4. Insert the claim (status defaults to 'submitted').
      5. Immediately run it through the ML verification pipeline (Stage 1-6,
         see ml_pipeline/pipeline.py) so the provider gets an instant
         verified/flagged/rejected outcome rather than waiting for an
         officer to pick it up manually.
      6. Return the claim plus its verification outcome (if the pipeline
         produced one) in a single response.
    """
    if payload.service_date != date.today():
        raise HTTPException(400, "Service date must be today's date and cannot be altered")

    patient = db.execute(text(
        "SELECT patient_id FROM sha_members WHERE sha_member_no = :no"
    ), {"no": payload.patient_sha_member_no}).fetchone()
    if not patient:
        raise HTTPException(404, "SHA member not found")

    # Lookup tariff to populate sha_tariff_amount
    tariff = db.execute(text("""
        SELECT approved_amount FROM sha_tariffs
        WHERE diagnosis_code = :d AND procedure_code = :p
          AND effective_to IS NULL
        ORDER BY effective_from DESC LIMIT 1
    """), {"d": payload.diagnosis_code, "p": payload.procedure_code}).fetchone()
    tariff_amount = float(tariff.approved_amount) if tariff else None

    claim_id = f"SHA-{uuid.uuid4().hex[:10].upper()}"
    db.execute(text("""
        INSERT INTO claims
            (claim_id, patient_id, provider_id, service_date,
             diagnosis_code, procedure_code, claimed_amount, sha_tariff_amount)
        VALUES
            (:cid, :pid, :prov, :svc, :diag, :proc, :amt, :tariff)
    """), {
        "cid": claim_id, "pid": patient.patient_id,
        "prov": payload.provider_id, "svc": payload.service_date,
        "diag": payload.diagnosis_code, "proc": payload.procedure_code,
        "amt": payload.claimed_amount, "tariff": tariff_amount,
    })
    db.commit()

    # Run ML verification pipeline if model is available
    pipeline = _get_pipeline()
    if pipeline:
        try:
            pipeline.verify(db, claim_id)
        except Exception as e:
            # Log but do not crash — claim is saved, pipeline failed gracefully.
            # A provider's submission should never be lost just because, say,
            # the model file is temporarily unavailable.
            print(f"[PIPELINE WARNING] {claim_id}: {e}")

    claim = db.execute(text("SELECT * FROM claims WHERE claim_id = :cid"),
                        {"cid": claim_id}).fetchone()
    result = dict(claim._mapping)

    verification = db.execute(text("""
        SELECT xgboost_score, is_flagged FROM verification_results
        WHERE claim_id = :cid
    """), {"cid": claim_id}).fetchone()
    if verification:
        result["verification"] = dict(verification._mapping)

    return result


# ── STATUS TRACKING & LISTING ─────────────────────────────────────────────────
@router.get("/{claim_id}/status")
def get_claim_status(
    claim_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("provider", "officer", "admin")),
):
    """FR-11: Real-time claim status tracking."""
    claim = db.execute(text(
        "SELECT claim_id, status, submission_date FROM claims WHERE claim_id = :cid"
    ), {"cid": claim_id}).fetchone()
    if not claim:
        raise HTTPException(404, "Claim not found")
    return dict(claim._mapping)


@router.get("/")
def list_claims(
    status: str = None,
    provider_id: str = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("officer", "admin")),
):
    """List claims for officer/admin dashboard."""
    q = "SELECT * FROM claims WHERE 1=1"
    p = {}
    if status:      q += " AND status = :status";         p["status"] = status
    if provider_id: q += " AND provider_id = :provider";  p["provider"] = provider_id
    q += " ORDER BY submission_date DESC LIMIT 100"
    rows = db.execute(text(q), p).fetchall()
    return [dict(r._mapping) for r in rows]
