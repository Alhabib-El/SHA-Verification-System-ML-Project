"""
app/routers/review.py
FR-05,06,07,09 — flagged claims queue, SHAP display, officer decisions.

FIX: admin role added alongside officer so admins can review during testing.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db
from ..schemas import OfficerDecisionRequest
from ..auth import require_role

router = APIRouter(prefix="/review", tags=["Officer Review"])


@router.get("/queue")
def get_flagged_queue(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("officer", "admin")),  # FIXED: added admin
):
    rows = db.execute(text("SELECT * FROM v_flagged_claims_queue")).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/{claim_id}")
def get_claim_for_review(
    claim_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("officer", "admin")),  # FIXED: added admin
):
    row = db.execute(text("""
        SELECT c.claim_id, c.diagnosis_code, c.procedure_code,
               c.claimed_amount, c.sha_tariff_amount, c.amount_ratio, c.status,
               p.name AS provider_name, p.county,
               m.full_name AS patient_name,
               vr.eligibility_check, vr.provider_check, vr.coverage_check,
               vr.clinical_match, vr.billing_compliant,
               vr.xgboost_score, vr.is_flagged, vr.top_features
        FROM claims c
        JOIN health_providers p      ON c.provider_id = p.provider_id
        JOIN sha_members m           ON c.patient_id  = m.patient_id
        JOIN verification_results vr ON c.claim_id    = vr.claim_id
        WHERE c.claim_id = :cid
    """), {"cid": claim_id}).fetchone()

    if not row:
        raise HTTPException(404, "Claim not found or not yet verified")

    # Log view in audit trail
    db.execute(text("""
        INSERT INTO audit_log
            (log_id, claim_id, officer_id, action, officer_comments, shap_viewed)
        VALUES (:lid, :cid, :oid, 'viewed', 'Claim opened for review', TRUE)
    """), {"lid": f"LOG-{uuid.uuid4().hex[:10].upper()}",
           "cid": claim_id, "oid": current_user["sub"]})
    db.commit()
    return dict(row._mapping)


@router.post("/decide")
def submit_decision(
    payload: OfficerDecisionRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("officer", "admin")),  # FIXED: added admin
):
    claim = db.execute(text("SELECT status FROM claims WHERE claim_id = :cid"),
                        {"cid": payload.claim_id}).fetchone()
    if not claim:
        raise HTTPException(404, "Claim not found")

    status_map = {"approved": "approved", "rejected": "rejected", "escalated": "under_review"}
    new_status = status_map.get(payload.action)
    if not new_status:
        raise HTTPException(400, "Invalid action. Use: approved, rejected, or escalated")

    log_id = f"LOG-{uuid.uuid4().hex[:10].upper()}"
    db.execute(text("""
        INSERT INTO audit_log
            (log_id, claim_id, officer_id, action, previous_status,
             new_status, officer_comments, shap_viewed)
        VALUES (:lid, :cid, :oid, :action, :prev, :new, :comments, :shap)
    """), {
        "lid": log_id, "cid": payload.claim_id,
        "oid": current_user["sub"], "action": payload.action,
        "prev": claim.status, "new": new_status,
        "comments": payload.comments, "shap": payload.shap_viewed,
    })
    db.execute(text("UPDATE claims SET status = :s WHERE claim_id = :cid"),
               {"s": new_status, "cid": payload.claim_id})
    db.commit()

    return {"claim_id": payload.claim_id, "new_status": new_status,
            "action": payload.action, "log_id": log_id}
