"""
backend/generate_todays_claims.py
Generates >=50 new claims dated TODAY, covering the full range of outcomes
a real day of submissions would produce: rejected at precheck (inactive
member / no tariff match), cleanly verified, ML-flagged, then officer
decisions on a portion of the pending ones (approved / rejected /
escalated), leaving a realistic remainder still pending.

Windows usage:
    cd backend
    venv\\Scripts\\activate
    python generate_todays_claims.py
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

random.seed(729)

TODAY = date.today()
N_CLAIMS = 55
ANOMALY_RATE = 0.25          # inflated a bit so today's batch visibly includes flags
INACTIVE_PATIENT_RATE = 0.10  # deliberately triggers rejected_precheck

APPROVE_REASONS = [
    "Approved — claim amount consistent with SHA tariff schedule and clinical documentation.",
    "Approved — verified eligibility, provider accreditation and billing all in order.",
    "Approved after review — minor discrepancy explained, documentation attached.",
]
REJECT_REASONS = [
    "Rejected — claimed amount significantly exceeds approved tariff with no supporting justification.",
    "Rejected — diagnosis and procedure combination inconsistent with clinical guidelines.",
    "Rejected — provider unable to provide supporting documentation for the billed amount.",
]
ESCALATE_REASONS = [
    "Escalated — unusual billing pattern requires senior officer review.",
]


def main():
    with engine.connect() as conn:
        active_patients = [r.patient_id for r in conn.execute(text(
            "SELECT patient_id FROM sha_members WHERE eligibility_status = 'active'")).fetchall()]
        inactive_patients = [r.patient_id for r in conn.execute(text(
            "SELECT patient_id FROM sha_members WHERE eligibility_status != 'active'")).fetchall()]
        providers = [r.provider_id for r in conn.execute(text(
            "SELECT provider_id FROM health_providers WHERE status = 'active'")).fetchall()]
        tariffs = conn.execute(text("""
            SELECT diagnosis_code, procedure_code, approved_amount
            FROM sha_tariffs WHERE effective_to IS NULL
        """)).fetchall()
        print(f"Pool: {len(active_patients)} active + {len(inactive_patients)} inactive patients, "
              f"{len(providers)} active providers, {len(tariffs)} tariffs. Dating batch to {TODAY}.")

        claim_ids = []
        for _ in range(N_CLAIMS):
            use_inactive = inactive_patients and random.random() < INACTIVE_PATIENT_RATE
            patient_id = random.choice(inactive_patients) if use_inactive else random.choice(active_patients)
            provider_id = random.choice(providers)
            tariff = random.choice(tariffs)
            is_anomalous = random.random() < ANOMALY_RATE
            tariff_amount = float(tariff.approved_amount)
            claimed = round(tariff_amount * (random.uniform(4.0, 7.0) if is_anomalous else random.uniform(0.97, 1.08)), 2)

            claim_id = f"SHA-{uuid.uuid4().hex[:10].upper()}"
            conn.execute(text("""
                INSERT INTO claims
                    (claim_id, patient_id, provider_id, submission_date, service_date,
                     diagnosis_code, procedure_code, claimed_amount, sha_tariff_amount, status)
                VALUES
                    (:cid, :pid, :prov, :ts, :svc, :diag, :proc, :amt, :tariff, 'submitted')
            """), {
                "cid": claim_id, "pid": patient_id, "prov": provider_id,
                "ts": TODAY, "svc": TODAY,
                "diag": tariff.diagnosis_code, "proc": tariff.procedure_code,
                "amt": claimed, "tariff": tariff_amount,
            })
            claim_ids.append(claim_id)
        conn.commit()
        print(f"Inserted {len(claim_ids)} claims dated {TODAY}.")

    # ── Run the real verification pipeline (produces rejected_precheck /
    #    verified / flagged naturally, exactly like a live submission would) ──
    from ml_pipeline.pipeline import ClaimsVerificationPipeline
    pipeline = ClaimsVerificationPipeline()
    with Session(engine) as db:
        for cid in claim_ids:
            try:
                pipeline.verify(db, cid)
            except Exception as e:
                print(f"  [WARN] pipeline failed for {cid}: {e}")

    # ── Officer decisions on a portion of today's pending claims ──
    with engine.connect() as conn:
        officer_ids = [r.user_id for r in conn.execute(text(
            "SELECT user_id FROM system_users WHERE role IN ('officer','admin') AND is_active = TRUE"
        )).fetchall()]
        rows = conn.execute(text("""
            SELECT claim_id, status FROM claims
            WHERE claim_id = ANY(:ids) AND status IN ('flagged', 'verified')
        """), {"ids": claim_ids}).fetchall()

        decided = 0
        for r in rows:
            if random.random() > 0.55:   # ~45% resolved, rest stay pending for the live demo
                continue
            if r.status == "flagged":
                action = random.choices(["rejected", "approved", "escalated"], weights=[45, 35, 20])[0]
            else:
                action = random.choices(["approved", "rejected"], weights=[88, 12])[0]
            new_status = {"approved": "approved", "rejected": "rejected", "escalated": "under_review"}[action]
            reason = random.choice({"approved": APPROVE_REASONS, "rejected": REJECT_REASONS, "escalated": ESCALATE_REASONS}[action])

            log_id = f"LOG-{uuid.uuid4().hex[:10].upper()}"
            conn.execute(text("""
                INSERT INTO audit_log
                    (log_id, claim_id, officer_id, action, previous_status,
                     new_status, officer_comments, shap_viewed, action_timestamp)
                VALUES (:lid, :cid, :oid, :action, :prev, :new, :reason, TRUE, NOW())
            """), {
                "lid": log_id, "cid": r.claim_id, "oid": random.choice(officer_ids),
                "action": action, "prev": r.status, "new": new_status, "reason": reason,
            })
            conn.execute(text("UPDATE claims SET status = :s WHERE claim_id = :cid"),
                         {"s": new_status, "cid": r.claim_id})
            decided += 1
        conn.commit()
        print(f"Officer decisions: resolved {decided} of {len(rows)} pending claims from today's batch.")

        summary = conn.execute(text("""
            SELECT status, COUNT(*) FROM claims WHERE claim_id = ANY(:ids) GROUP BY status ORDER BY status
        """), {"ids": claim_ids}).fetchall()
        print(f"\nToday's ({TODAY}) batch — final status breakdown:")
        for s in summary:
            print(f"  {s.status:<20} {s.count}")


if __name__ == "__main__":
    main()
