"""
reports/report_generator.py
FR-10 — Report generation module.

Generates the verification reports required by SHA management and
auditors: daily claims summaries, provider performance reports, and
exportable audit trail extracts. Supports PDF and CSV export formats.
"""
import csv
import io
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import text
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)


class ReportGenerator:
    """
    Generates verification reports for SHA administrators and auditors.
    All reports are built from live database queries — no cached data —
    to guarantee accuracy at the moment of generation.
    """

    def __init__(self, db: Session):
        self.db = db
        self.styles = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(
            name="ReportTitle", fontSize=16, leading=20, spaceAfter=6,
            textColor=colors.HexColor("#1F4E79"), fontName="Helvetica-Bold"
        ))
        self.styles.add(ParagraphStyle(
            name="ReportSubtitle", fontSize=10, textColor=colors.HexColor("#666666"),
            spaceAfter=14
        ))

    # ── DATA QUERIES ─────────────────────────────────────────────────────────
    def _get_daily_summary(self, start_date: date, end_date: date):
        rows = self.db.execute(text("""
            SELECT * FROM v_daily_claims_summary
            WHERE claim_date BETWEEN :start AND :end
            ORDER BY claim_date
        """), {"start": start_date, "end": end_date}).fetchall()
        return [dict(r._mapping) for r in rows]

    def _get_provider_performance(self, start_date: date, end_date: date):
        rows = self.db.execute(text("""
            SELECT
                p.provider_id, p.name, p.county,
                COUNT(c.claim_id)                                    AS total_claims,
                COUNT(*) FILTER (WHERE vr.is_flagged = TRUE)          AS flagged_claims,
                ROUND(AVG(vr.xgboost_score)::numeric, 3)               AS avg_score,
                ROUND(AVG(c.amount_ratio)::numeric, 2)                   AS avg_amount_ratio,
                COUNT(*) FILTER (WHERE c.status = 'approved')              AS approved_claims
            FROM health_providers p
            LEFT JOIN claims c ON p.provider_id = c.provider_id
                AND c.submission_date BETWEEN :start AND :end
            LEFT JOIN verification_results vr ON c.claim_id = vr.claim_id
            GROUP BY p.provider_id, p.name, p.county
            ORDER BY flagged_claims DESC
        """), {"start": start_date, "end": end_date}).fetchall()
        return [dict(r._mapping) for r in rows]

    def _get_audit_trail(self, claim_id: Optional[str] = None,
                          start_date: Optional[date] = None,
                          end_date: Optional[date] = None):
        query = """
            SELECT al.log_id, al.claim_id, su.full_name AS officer_name,
                   al.action, al.previous_status, al.new_status,
                   al.officer_comments, al.shap_viewed, al.action_timestamp
            FROM audit_log al
            JOIN system_users su ON al.officer_id = su.user_id
            WHERE 1=1
        """
        params = {}
        if claim_id:
            query += " AND al.claim_id = :cid"
            params["cid"] = claim_id
        if start_date:
            query += " AND al.action_timestamp >= :start"
            params["start"] = start_date
        if end_date:
            query += " AND al.action_timestamp <= :end"
            params["end"] = end_date
        query += " ORDER BY al.action_timestamp DESC"

        rows = self.db.execute(text(query), params).fetchall()
        return [dict(r._mapping) for r in rows]

    # ── PDF REPORT: VERIFICATION SUMMARY ────────────────────────────────────
    def generate_verification_summary_pdf(self, start_date: date, end_date: date) -> bytes:
        """
        Produces the periodic Claims Verification Summary Report (PDF)
        for SHA management — covers Section 5.5 / FR-10 reporting requirement.
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4,
                                 topMargin=2*cm, bottomMargin=2*cm,
                                 leftMargin=2*cm, rightMargin=2*cm)
        elements = []

        elements.append(Paragraph("SHA Claims Verification Summary Report", self.styles["ReportTitle"]))
        elements.append(Paragraph(
            f"Period: {start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')} "
            f"| Generated: {datetime.now().strftime('%d %b %Y, %H:%M')}",
            self.styles["ReportSubtitle"]
        ))

        # Daily summary table
        summary_data = self._get_daily_summary(start_date, end_date)
        table_data = [["Date", "Total", "Approved", "Flagged", "Avg. Score", "Avg. Ratio"]]
        for row in summary_data:
            table_data.append([
                row["claim_date"].strftime("%d %b"),
                str(row["total_claims"]),
                str(row["approved_count"]),
                str(row["flagged_count"]),
                f"{row['avg_xgboost_score']:.3f}" if row["avg_xgboost_score"] else "—",
                f"{row['avg_amount_ratio']:.2f}x" if row["avg_amount_ratio"] else "—",
            ])

        t = Table(table_data, colWidths=[2.5*cm, 2.3*cm, 2.3*cm, 2.3*cm, 2.5*cm, 2.5*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EAF2FF")]),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 16))

        # Provider performance table
        elements.append(Paragraph("Provider Performance — Flagged Claim Rate", self.styles["Heading2"]))
        perf_data = self._get_provider_performance(start_date, end_date)
        perf_table_data = [["Provider", "County", "Total", "Flagged", "Avg Score", "Approved"]]
        for row in perf_data[:15]:  # top 15 providers
            perf_table_data.append([
                row["name"], row["county"], str(row["total_claims"]),
                str(row["flagged_claims"]),
                f"{row['avg_score']:.3f}" if row["avg_score"] else "—",
                str(row["approved_claims"]),
            ])

        pt = Table(perf_table_data, colWidths=[4*cm, 2.5*cm, 1.8*cm, 1.8*cm, 2.2*cm, 1.9*cm])
        pt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EAF2FF")]),
        ]))
        elements.append(pt)

        doc.build(elements)
        buffer.seek(0)
        return buffer.read()

    # ── CSV EXPORT: AUDIT TRAIL ──────────────────────────────────────────────
    def generate_audit_trail_csv(self, claim_id: Optional[str] = None,
                                  start_date: Optional[date] = None,
                                  end_date: Optional[date] = None) -> str:
        """
        Exports the audit log as CSV for compliance review — satisfies
        FR-09's requirement that the audit log be exportable.
        """
        rows = self._get_audit_trail(claim_id, start_date, end_date)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Log ID", "Claim ID", "Officer", "Action",
            "Previous Status", "New Status", "Comments", "SHAP Viewed", "Timestamp"
        ])
        for row in rows:
            writer.writerow([
                row["log_id"], row["claim_id"], row["officer_name"], row["action"],
                row["previous_status"], row["new_status"], row["officer_comments"],
                row["shap_viewed"], row["action_timestamp"].isoformat(),
            ])
        return output.getvalue()

    # ── CSV EXPORT: FLAGGED CLAIMS WITH SHAP TOP FEATURES ───────────────────
    def generate_flagged_claims_csv(self, start_date: date, end_date: date) -> str:
        """Exports all flagged claims with their top SHAP features —
        used for model behaviour analysis in Chapter Six."""
        rows = self.db.execute(text("""
            SELECT c.claim_id, p.name AS provider_name, c.diagnosis_code,
                   c.claimed_amount, c.amount_ratio, vr.xgboost_score,
                   vr.top_features, c.status
            FROM claims c
            JOIN health_providers p ON c.provider_id = p.provider_id
            JOIN verification_results vr ON c.claim_id = vr.claim_id
            WHERE vr.is_flagged = TRUE
              AND c.submission_date BETWEEN :start AND :end
            ORDER BY vr.xgboost_score DESC
        """), {"start": start_date, "end": end_date}).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Claim ID", "Provider", "Diagnosis", "Claimed Amount",
            "Amount Ratio", "XGBoost Score", "Top Feature 1", "Status"
        ])
        for r in rows:
            row = dict(r._mapping)
            top_feat = row["top_features"][0]["feature"] if row["top_features"] else "—"
            writer.writerow([
                row["claim_id"], row["provider_name"], row["diagnosis_code"],
                row["claimed_amount"], row["amount_ratio"], row["xgboost_score"],
                top_feat, row["status"],
            ])
        return output.getvalue()
