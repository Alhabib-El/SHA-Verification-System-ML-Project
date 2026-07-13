"""
app/routers/suspension.py
Auto-suspension API endpoints.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from ..database import get_db
from ..auth import require_role

router = APIRouter(prefix="/suspension", tags=["Auto-Suspension"])
FLAG_THRESHOLD = 10
WINDOW_DAYS    = 30


def _get_flag_count(db: Session, provider_id: str) -> int:
    result = db.execute(text(f"""
        SELECT COUNT(*) FROM claims c
        JOIN verification_results vr ON c.claim_id = vr.claim_id
        WHERE c.provider_id = :pid AND vr.is_flagged = TRUE
          AND c.submission_date >= NOW() - INTERVAL '{WINDOW_DAYS} days'
    """), {"pid": provider_id}).scalar()
    return result or 0


@router.get("/status/{provider_id}")
def flag_status(provider_id: str, db: Session = Depends(get_db),
                _=Depends(require_role("admin", "officer"))):
    flags = _get_flag_count(db, provider_id)
    row = db.execute(text(
        "SELECT status, risk_tier FROM health_providers WHERE provider_id = :pid"),
        {"pid": provider_id}).fetchone()
    if not row: raise HTTPException(404, "Provider not found")
    return {
        "provider_id":              provider_id,
        "current_status":           row.status,
        "risk_tier":                row.risk_tier,
        "flags_last_30d":           flags,
        "threshold":                FLAG_THRESHOLD,
        "flags_until_suspension":   max(0, FLAG_THRESHOLD - flags),
        "suspension_risk":          "HIGH" if flags >= 7 else "MEDIUM" if flags >= 4 else "LOW",
    }


@router.get("/at-risk")
def at_risk_providers(db: Session = Depends(get_db),
                       _=Depends(require_role("admin", "officer"))):
    rows = db.execute(text(f"""
        SELECT p.provider_id, p.name, p.county, p.status,
               COUNT(*) FILTER (WHERE vr.is_flagged = TRUE
                 AND c.submission_date >= NOW() - INTERVAL '{WINDOW_DAYS} days') AS flags
        FROM health_providers p
        LEFT JOIN claims c ON p.provider_id = c.provider_id
        LEFT JOIN verification_results vr ON c.claim_id = vr.claim_id
        GROUP BY p.provider_id, p.name, p.county, p.status
        HAVING COUNT(*) FILTER (WHERE vr.is_flagged = TRUE
          AND c.submission_date >= NOW() - INTERVAL '{WINDOW_DAYS} days') >= 4
        ORDER BY flags DESC
    """)).fetchall()
    return [{**dict(r._mapping),
             "flags_until_suspension": max(0, FLAG_THRESHOLD - r.flags),
             "suspension_risk": "HIGH" if r.flags >= 7 else "MEDIUM"}
            for r in rows]


class ReinstateRequest(BaseModel):
    provider_id: str
    reason: str


@router.post("/reinstate")
def reinstate(payload: ReinstateRequest, db: Session = Depends(get_db),
              user=Depends(require_role("admin"))):
    if len(payload.reason) < 10:
        raise HTTPException(400, "Detailed reason required (min 10 chars)")
    row = db.execute(text(
        "SELECT status FROM health_providers WHERE provider_id = :pid"),
        {"pid": payload.provider_id}).fetchone()
    if not row: raise HTTPException(404, "Provider not found")
    if row.status != "suspended":
        raise HTTPException(400, f"Provider is not suspended (status: {row.status})")

    db.execute(text("""
        UPDATE health_providers SET status = 'active', risk_tier = 'high'
        WHERE provider_id = :pid
    """), {"pid": payload.provider_id})

    # Find a recent claim for audit FK
    claim = db.execute(text("""
        SELECT claim_id FROM claims WHERE provider_id = :pid
        ORDER BY submission_date DESC LIMIT 1
    """), {"pid": payload.provider_id}).fetchone()

    if claim:
        log_id = f"LOG-RST-{uuid.uuid4().hex[:8].upper()}"
        db.execute(text("""
            INSERT INTO audit_log
            (log_id, claim_id, officer_id, action, previous_status,
             new_status, officer_comments, shap_viewed)
            VALUES (:lid, :cid, :oid, 'overridden', 'suspended', 'active', :reason, FALSE)
        """), {"lid": log_id, "cid": claim.claim_id,
               "oid": user["sub"], "reason": payload.reason})

    db.commit()
    return {"action": "reinstated", "provider_id": payload.provider_id, "risk_tier": "high"}
