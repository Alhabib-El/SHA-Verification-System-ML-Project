"""
backend/trigger_auto_suspend_demo.py
Pushes 2 already-high-flag providers over the real 10-flagged-claims-in-30-days
threshold so the existing auto-suspension logic (ml_pipeline/auto_suspend.py,
already wired into pipeline.py's _persist_result) actually fires — not a
faked status flip, the real code path runs claim by claim until it trips.

Windows usage:
    cd backend
    venv\\Scripts\\activate
    python trigger_auto_suspend_demo.py
"""
import os
import random
import uuid
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_URL = os.getenv("DATABASE_URL",
    "postgresql://sha_app_user:password@localhost:5432/sha_claims_db")
engine = create_engine(DATABASE_URL)

random.seed(99)

TARGET_PROVIDERS = ["PRV-010", "PRV-006"]   # already the highest-flagged active providers
CLAIMS_PER_PROVIDER = 8                      # pushes both comfortably past the 10-flag threshold
START_DATE = date(2026, 7, 1)
END_DATE = date(2026, 7, 14)


def random_date(d1: date, d2: date) -> date:
    return d1 + timedelta(days=random.randint(0, (d2 - d1).days))


def main():
    with engine.connect() as conn:
        patients = [r.patient_id for r in conn.execute(text("SELECT patient_id FROM sha_members")).fetchall()]
        tariffs = conn.execute(text("""
            SELECT diagnosis_code, procedure_code, approved_amount
            FROM sha_tariffs WHERE effective_to IS NULL
        """)).fetchall()

        new_claim_ids = []
        for provider_id in TARGET_PROVIDERS:
            for _ in range(CLAIMS_PER_PROVIDER):
                patient_id = random.choice(patients)
                tariff = random.choice(tariffs)
                svc_date = random_date(START_DATE, END_DATE)
                sub_date = min(svc_date + timedelta(days=random.randint(0, 2)), END_DATE)
                tariff_amount = float(tariff.approved_amount)
                # Deliberately extreme over-billing — guaranteed to score as flagged
                claimed = round(tariff_amount * random.uniform(6.0, 9.0), 2)

                claim_id = f"SHA-{uuid.uuid4().hex[:10].upper()}"
                conn.execute(text("""
                    INSERT INTO claims
                        (claim_id, patient_id, provider_id, submission_date, service_date,
                         diagnosis_code, procedure_code, claimed_amount, sha_tariff_amount, status)
                    VALUES
                        (:cid, :pid, :prov, :sub, :svc, :diag, :proc, :amt, :tariff, 'submitted')
                """), {
                    "cid": claim_id, "pid": patient_id, "prov": provider_id,
                    "sub": sub_date, "svc": svc_date,
                    "diag": tariff.diagnosis_code, "proc": tariff.procedure_code,
                    "amt": claimed, "tariff": tariff_amount,
                })
                new_claim_ids.append(claim_id)
        conn.commit()
        print(f"Inserted {len(new_claim_ids)} deliberately over-billed claims across {TARGET_PROVIDERS}.")

    # Run the REAL verification pipeline claim by claim — check_and_suspend()
    # fires on its own once a provider's 30-day flag count crosses 10.
    from ml_pipeline.pipeline import ClaimsVerificationPipeline
    pipeline = ClaimsVerificationPipeline()
    with Session(engine) as db:
        for cid in new_claim_ids:
            try:
                pipeline.verify(db, cid)
            except Exception as e:
                print(f"  [WARN] pipeline failed for {cid}: {e}")

    with engine.connect() as conn:
        print("\nResulting provider status:")
        for pid in TARGET_PROVIDERS:
            row = conn.execute(text("""
                SELECT p.provider_id, p.name, p.status, p.risk_tier,
                    COUNT(*) FILTER (WHERE vr.is_flagged AND c.submission_date >= NOW() - INTERVAL '30 days') AS flags_30d
                FROM health_providers p
                LEFT JOIN claims c ON c.provider_id = p.provider_id
                LEFT JOIN verification_results vr ON vr.claim_id = c.claim_id
                WHERE p.provider_id = :pid
                GROUP BY p.provider_id, p.name, p.status, p.risk_tier
            """), {"pid": pid}).fetchone()
            print(f"  {row.provider_id} {row.name}: status={row.status} risk={row.risk_tier} flags_30d={row.flags_30d}")

        print("\nAuto-suspension audit log entries:")
        rows = conn.execute(text("""
            SELECT claim_id, officer_comments, action_timestamp FROM audit_log
            WHERE action = 'suspended' ORDER BY action_timestamp DESC LIMIT 5
        """)).fetchall()
        for r in rows:
            print(f"  [{r.action_timestamp}] {r.claim_id}: {r.officer_comments}")


if __name__ == "__main__":
    main()
