"""
app/routers/review.py
FR-05,06,07,09 — flagged claims queue, SHAP display, officer decisions.

FIX: admin role added alongside officer so admins can review during testing.
"""
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db
from ..schemas import OfficerDecisionRequest
from ..auth import require_role

router = APIRouter(prefix="/review", tags=["Officer Review"])

VALID_STATUSES = {
    "submitted", "verified", "flagged", "under_review",
    "approved", "rejected", "rejected_precheck", "payment_queued", "paid",
}
PENDING_STATUSES = ("submitted", "verified", "flagged", "under_review")


@router.get("/queue")
def get_review_queue(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("officer", "admin")),  # FIXED: added admin
):
    """
    Defaults to everything a provider has submitted that isn't yet at a
    terminal status — not just ML-flagged claims. Pass ?status=approved (or
    rejected/flagged/etc.) to instead list claims already at that status, so
    the queue screen can show "what happened" as well as "what's pending".
    """
    if status and status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status filter: {status}")
    status_list = [status] if status else list(PENDING_STATUSES)

    rows = db.execute(text("""
        SELECT
            c.claim_id, c.submission_date, c.status,
            p.name AS provider_name, p.county,
            c.diagnosis_code, c.claimed_amount, c.amount_ratio,
            vr.xgboost_score, vr.is_flagged
        FROM claims c
        JOIN health_providers p      ON c.provider_id = p.provider_id
        LEFT JOIN verification_results vr ON c.claim_id = vr.claim_id
        WHERE c.status = ANY(:statuses)
        ORDER BY c.submission_date DESC
    """), {"statuses": status_list}).fetchall()
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
        LEFT JOIN verification_results vr ON c.claim_id = vr.claim_id
        WHERE c.claim_id = :cid
    """), {"cid": claim_id}).fetchone()

    if not row:
        raise HTTPException(404, "Claim not found")

    # Log view in audit trail
    db.execute(text("""
        INSERT INTO audit_log
            (log_id, claim_id, officer_id, action, officer_comments, shap_viewed)
        VALUES (:lid, :cid, :oid, 'viewed', 'Claim opened for review', TRUE)
    """), {"lid": f"LOG-{uuid.uuid4().hex[:10].upper()}",
           "cid": claim_id, "oid": current_user["sub"]})
    db.commit()

    result = dict(row._mapping)

    # If this claim already has a decision (approved/rejected/escalated/
    # suspended), surface it so the review screen can show "what happened"
    # instead of re-prompting for a decision that was already made.
    decision = db.execute(text("""
        SELECT al.action, al.officer_comments, al.action_timestamp,
               su.full_name AS officer_name
        FROM audit_log al
        LEFT JOIN system_users su ON su.user_id = al.officer_id
        WHERE al.claim_id = :cid AND al.action IN ('approved', 'rejected', 'escalated', 'suspended')
        ORDER BY al.action_timestamp DESC LIMIT 1
    """), {"cid": claim_id}).fetchone()
    if decision:
        result["decision"] = dict(decision._mapping)

    return result


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
