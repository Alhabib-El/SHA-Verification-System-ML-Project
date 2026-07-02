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

from ..database import get_db
from ..auth import require_role
from reports.report_generator import ReportGenerator

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/dashboard-summary")
def dashboard_summary(db: Session = Depends(get_db),
                       current_user: dict = Depends(require_role("officer", "admin"))):
    """Powers the four metric cards on the Officer Dashboard (Figure 5.5):
    Today's Claims / Auto-verified / Flagged / Avg. Score."""
    gen = ReportGenerator(db)
    today_summary = gen._get_daily_summary(date.today(), date.today())
    if not today_summary:
        return {"total_claims": 0, "approved_count": 0, "flagged_count": 0, "avg_xgboost_score": 0}
    return today_summary[0]


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
