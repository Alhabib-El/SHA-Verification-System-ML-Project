"""
app/routers/reports.py
FR-10 — Reporting API endpoints. Exposes the ReportGenerator module
to the Officer Dashboard and Admin Panel (Figure 5.5).
"""
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session
import io

from sqlalchemy import text

from ..database import get_db
from ..auth import require_role
from reports.report_generator import ReportGenerator

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/dashboard-summary")
def dashboard_summary(db: Session = Depends(get_db),
                       current_user: dict = Depends(require_role("officer", "admin"))):
    """Powers the metric cards on the Officer Dashboard (Figure 5.5):
    Today's Claims / Approved / Flagged / Rejected / Verified / Under Review."""
    gen = ReportGenerator(db)
    today_summary = gen._get_daily_summary(date.today(), date.today())
    result = today_summary[0] if today_summary else {
        "total_claims": 0, "approved_count": 0, "flagged_count": 0, "rejected_count": 0,
    }

    status_counts = db.execute(text("""
        SELECT status, COUNT(*) FROM claims
        WHERE DATE(submission_date) = CURRENT_DATE
        GROUP BY status
    """)).fetchall()
    by_status = {row[0]: row[1] for row in status_counts}
    result["verified_count"] = by_status.get("verified", 0)
    result["under_review_count"] = by_status.get("under_review", 0)
    return result


@router.get("/verification-summary.pdf")
def download_verification_summary(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("admin", "officer")),
):
    """Downloads the periodic verification summary as a formatted PDF."""
    gen = ReportGenerator(db)
    pdf_bytes = gen.generate_verification_summary_pdf(start_date, end_date)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=verification_summary_{start_date}_{end_date}.pdf"}
    )


@router.get("/audit-trail.csv")
def download_audit_trail(
    claim_id: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("admin")),
):
    """Exports the audit log as CSV — supports FR-09 compliance reporting."""
    gen = ReportGenerator(db)
    csv_content = gen.generate_audit_trail_csv(claim_id, start_date, end_date)
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_trail.csv"}
    )


@router.get("/flagged-claims.csv")
def download_flagged_claims(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("admin", "officer")),
):
    """Exports flagged claims with SHAP top features for model analysis."""
    gen = ReportGenerator(db)
    csv_content = gen.generate_flagged_claims_csv(start_date, end_date)
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=flagged_claims.csv"}
    )
