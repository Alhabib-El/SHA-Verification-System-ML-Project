"""
ml_pipeline/auto_suspend.py
Auto-suspension logic — called from pipeline.py after every verification.

Integration: at the end of ClaimsVerificationPipeline._persist_result()
add these two lines before db.commit():

    from ml_pipeline.auto_suspend import check_and_suspend
    check_and_suspend(db, claim["provider_id"], claim_id)
"""
import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import text

FLAG_THRESHOLD      = 10
ROLLING_WINDOW_DAYS = 30
SYSTEM_OFFICER_ID   = "USR-SYSTEM"


def check_and_suspend(db: Session, provider_id: str, trigger_claim_id: str) -> dict:
    """
    Checks provider flag count and suspends automatically if threshold reached.
    Returns dict describing action taken.
    """
    flag_count = _get_flag_count(db, provider_id)
    if flag_count < FLAG_THRESHOLD:
        return {"action": "no_action", "flag_count": flag_count}

    current_status = _get_provider_status(db, provider_id)
    if current_status in ("suspended", "revoked"):
        return {"action": "already_suspended", "flag_count": flag_count}

    # Suspend
    db.execute(text("""
        UPDATE health_providers
        SET status = 'suspended', risk_tier = 'high'
        WHERE provider_id = :pid
    """), {"pid": provider_id})

    # Audit log
    log_id = f"LOG-SUS-{uuid.uuid4().hex[:8].upper()}"
    comment = (
        f"AUTOMATED SUSPENSION: {flag_count} flagged claims in last "
        f"{ROLLING_WINDOW_DAYS} days (threshold: {FLAG_THRESHOLD}). "
        f"Triggered by claim {trigger_claim_id} at "
        f"{datetime.utcnow().isoformat()} UTC."
    )
    db.execute(text("""
        INSERT INTO audit_log
        (log_id, claim_id, officer_id, action, previous_status,
         new_status, officer_comments, shap_viewed)
        VALUES (:lid, :cid, :oid, 'suspended', 'active', 'suspended', :comment, FALSE)
    """), {"lid": log_id, "cid": trigger_claim_id,
           "oid": SYSTEM_OFFICER_ID, "comment": comment})

    db.commit()
    print(f"[AUTO-SUSPEND] {provider_id} suspended — {flag_count} flags. Log: {log_id}")
    return {"action": "suspended", "flag_count": flag_count, "log_id": log_id}


def _get_flag_count(db: Session, provider_id: str) -> int:
    result = db.execute(text(f"""
        SELECT COUNT(*) FROM claims c
        JOIN verification_results vr ON c.claim_id = vr.claim_id
        WHERE c.provider_id = :pid AND vr.is_flagged = TRUE
          AND c.submission_date >= NOW() - INTERVAL '{ROLLING_WINDOW_DAYS} days'
    """), {"pid": provider_id}).scalar()
    return result or 0


def _get_provider_status(db: Session, provider_id: str) -> str:
    row = db.execute(text(
        "SELECT status FROM health_providers WHERE provider_id = :pid"),
        {"pid": provider_id}).fetchone()
    return row.status if row else "unknown"
