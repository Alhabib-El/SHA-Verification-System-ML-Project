"""
app/routers/claims.py
FR-01, FR-02, FR-03, FR-04, FR-11 — claim submission, eligibility/provider
verification, XGBoost scoring, and status tracking.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db
from ..schemas import ClaimSubmitRequest, ClaimResponse
from ..auth import require_role
from ml_pipeline.pipeline import ClaimsVerificationPipeline

router = APIRouter(prefix="/claims", tags=["Claims"])
pipeline = ClaimsVerificationPipeline()


@router.post("/submit", response_model=ClaimResponse, status_code=201)
def submit_claim(
    payload: ClaimSubmitRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("provider")),
):
    """FR-01: Healthcare provider submits a new claim."""
    patient = db.execute(text(
        "SELECT patient_id FROM sha_members WHERE sha_member_no = :no"
    ), {"no": payload.patient_sha_member_no}).fetchone()
    if not patient:
        raise HTTPException(404, "SHA member not found")

    claim_id = f"SHA-{uuid.uuid4().hex[:10].upper()}"
    db.execute(text("""
        INSERT INTO claims
            (claim_id, patient_id, provider_id, service_date,
             diagnosis_code, procedure_code, claimed_amount)
        VALUES
            (:cid, :pid, :prov, :svc, :diag, :proc, :amt)
    """), {
        "cid": claim_id, "pid": patient.patient_id, "prov": payload.provider_id,
        "svc": payload.service_date, "diag": payload.diagnosis_code,
        "proc": payload.procedure_code, "amt": payload.claimed_amount,
    })
    db.commit()

    # FR-02/03/04: run the full verification pipeline synchronously
    pipeline.verify(db, claim_id)

    claim = db.execute(text("SELECT * FROM claims WHERE claim_id = :cid"),
                        {"cid": claim_id}).fetchone()
    return dict(claim._mapping)


@router.get("/{claim_id}/status")
def get_claim_status(claim_id: str, db: Session = Depends(get_db),
                      current_user: dict = Depends(require_role("provider", "officer", "admin"))):
    """FR-11: Real-time claim status tracking."""
    claim = db.execute(text(
        "SELECT claim_id, status, submission_date FROM claims WHERE claim_id = :cid"
    ), {"cid": claim_id}).fetchone()
    if not claim:
        raise HTTPException(404, "Claim not found")
    return dict(claim._mapping)


@router.get("/tariff-lookup")
def lookup_tariff(diagnosis_code: str, procedure_code: str,
                   db: Session = Depends(get_db)):
    """Helper endpoint powering the auto-populated tariff field in the
    claim submission screen (Figure 5.4)."""
    tariff = db.execute(text("""
        SELECT approved_amount FROM sha_tariffs
        WHERE diagnosis_code = :d AND procedure_code = :p
          AND effective_to IS NULL
        LIMIT 1
    """), {"d": diagnosis_code, "p": procedure_code}).fetchone()
    if not tariff:
        raise HTTPException(404, "No tariff found for this diagnosis-procedure combination")
    return {"approved_amount": float(tariff.approved_amount)}
