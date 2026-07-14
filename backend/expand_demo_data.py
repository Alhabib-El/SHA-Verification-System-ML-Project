"""
backend/expand_demo_data.py
Expands the demo dataset: adds 100 more SHA members, generates another batch
of claims (dated 2026-07-01 through today), runs the real verification
pipeline on them, then simulates officer decisions (approved/rejected/
escalated) across the pending queue — old and new claims alike — with
backdated audit_log timestamps and realistic reasons, so the system shows
the full claim lifecycle instead of just "flagged/verified".

Windows usage:
    cd backend
    venv\\Scripts\\activate
    python expand_demo_data.py
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

random.seed(21)

N_NEW_MEMBERS = 100
N_NEW_CLAIMS = 100
START_DATE = date(2026, 7, 1)
END_DATE = date(2026, 7, 14)
ANOMALY_RATE = 0.20

MALE_FIRST = ["James","Peter","John","Daniel","Michael","David","Samuel","Joseph","Paul",
              "Stephen","Francis","Charles","Anthony","Kevin","Brian","Dennis","Felix",
              "Moses","Amos","Erick","Vincent","Patrick","Edwin","Duncan","Collins"]
FEMALE_FIRST = ["Mary","Jane","Grace","Faith","Ann","Alice","Ruth","Esther","Lucy","Nancy",
                "Catherine","Elizabeth","Winnie","Susan","Joyce","Beatrice","Agnes","Judith",
                "Sharon","Caroline","Purity","Mercy","Diana","Brenda","Irene"]
SURNAMES = ["Mwangi","Wanjiru","Otieno","Achieng","Kamau","Njoroge","Wafula","Nekesa","Kiptoo",
            "Chebet","Mutua","Nduta","Hassan","Abdi","Wekesa","Ouma","Kariuki","Njeri",
            "Cheruiyot","Kones","Barasa","Onyango","Wambui","Muthoni","Kilonzo","Mbithi",
            "Too","Rotich","Simiyu","Were","Mwende","Kioko","Wairimu","Gitau","Owino","Adhiambo",
            "Chepkoech","Langat","Mochama","Nyongesa","Wanyama","Kiplagat"]
COUNTIES = ["Nairobi","Mombasa","Kisumu","Nakuru","Uasin Gishu","Kiambu","Machakos","Kakamega",
            "Meru","Embu","Garissa","Kisii","Bungoma","Kericho","Kitui","Kajiado","Kilifi",
            "Homa Bay","Busia","Nyeri","Bomet","Trans Nzoia","Nyandarua","Laikipia","Turkana","Marsabit"]
PACKAGES = (["SHIF Basic"]*6 + ["Primary Healthcare Fund"]*3 + ["Chronic Illness Fund"]*1)

APPROVE_REASONS = [
    "Approved — claim amount consistent with SHA tariff schedule and clinical documentation.",
    "Approved — verified eligibility, provider accreditation and billing all in order.",
    "Approved after review — minor discrepancy explained by facility tier, documentation attached.",
    "Approved — pre-checks and clinical match confirmed, no further action required.",
]
REJECT_REASONS = [
    "Rejected — claimed amount significantly exceeds approved tariff with no supporting justification.",
    "Rejected — diagnosis and procedure combination inconsistent with clinical guidelines.",
    "Rejected — provider unable to provide supporting documentation for the billed amount.",
    "Rejected — duplicate claim detected for same patient and service date.",
    "Rejected — billing pattern inconsistent with facility's historical claims.",
]
ESCALATE_REASONS = [
    "Escalated — unusual billing pattern requires senior officer / fraud unit review.",
    "Escalated — provider has multiple recent flags; escalating for broader investigation.",
    "Escalated — needs additional documentation from provider before a decision can be made.",
]


def random_date(d1: date, d2: date) -> date:
    span = max((d2 - d1).days, 0)
    return d1 + timedelta(days=random.randint(0, span))


def add_members(conn):
    existing_ids = [r.patient_id for r in conn.execute(text(
        "SELECT patient_id FROM sha_members")).fetchall()]
    max_num = max(int(pid.split("-")[1]) for pid in existing_ids)

    existing_nids = {r.national_id for r in conn.execute(text(
        "SELECT national_id FROM sha_members")).fetchall()}
    existing_memnos = {r.sha_member_no for r in conn.execute(text(
        "SELECT sha_member_no FROM sha_members")).fetchall()}

    new_patient_ids = []
    for i in range(1, N_NEW_MEMBERS + 1):
        patient_id = f"PAT-{max_num + i:04d}"
        gender = random.choice(["Male", "Female"])
        first = random.choice(MALE_FIRST) if gender == "Male" else random.choice(FEMALE_FIRST)
        last = random.choice(SURNAMES)

        while True:
            nid = str(random.randint(20000000, 39999999))
            if nid not in existing_nids:
                existing_nids.add(nid)
                break
        memno = f"SHA-2025-{2000 + i:05d}"
        while memno in existing_memnos:
            memno = f"SHA-2025-{2000 + i + random.randint(1, 999):05d}"
        existing_memnos.add(memno)

        dob = random_date(date(1955, 1, 1), date(2007, 12, 31))
        reg_date = random_date(date(2024, 1, 1), date(2026, 6, 30))
        elig_status = random.choices(["active", "inactive"], weights=[9, 1])[0]
        elig_expiry = date(2026, 12, 31) if elig_status == "active" else date(2025, 6, 30)

        conn.execute(text("""
            INSERT INTO sha_members
            (patient_id, national_id, sha_member_no, full_name, date_of_birth, gender,
             county, registration_date, coverage_package, eligibility_status, eligibility_expiry)
            VALUES (:pid, :nid, :memno, :name, :dob, :gender, :county, :reg, :pkg, :elig, :exp)
        """), {
            "pid": patient_id, "nid": nid, "memno": memno,
            "name": f"{first} {last}", "dob": dob, "gender": gender,
            "county": random.choice(COUNTIES), "reg": reg_date,
            "pkg": random.choice(PACKAGES), "elig": elig_status, "exp": elig_expiry,
        })
        new_patient_ids.append(patient_id)
    return new_patient_ids


def add_claims(conn, patients, providers, tariffs):
    generated = []
    for i in range(N_NEW_CLAIMS):
        patient_id = random.choice(patients)
        provider_id = random.choice(providers)
        tariff = random.choice(tariffs)
        is_anomalous = random.random() < ANOMALY_RATE
        svc_date = random_date(START_DATE, END_DATE)
        sub_date = min(svc_date + timedelta(days=random.randint(0, 3)), END_DATE)

        tariff_amount = float(tariff.approved_amount)
        claimed = round(tariff_amount * (random.uniform(4.0, 7.0) if is_anomalous else random.uniform(0.97, 1.08)), 2)

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
        generated.append(claim_id)
    return generated


def simulate_officer_decisions(conn, officer_ids):
    """Randomly resolves a portion of the pending queue (flagged/verified/
    under_review) into approved/rejected/escalated, with backdated
    audit_log entries and a reason — leaving the rest genuinely pending."""
    rows = conn.execute(text("""
        SELECT claim_id, status, submission_date FROM claims
        WHERE status IN ('flagged', 'verified', 'under_review')
    """)).fetchall()

    decided = 0
    for r in rows:
        if random.random() > 0.65:   # ~35% stay pending — a realistic backlog
            continue

        if r.status == "flagged":
            action = random.choices(["rejected", "approved", "escalated"], weights=[45, 35, 20])[0]
        else:  # verified / under_review
            action = random.choices(["approved", "rejected", "escalated"], weights=[85, 10, 5])[0]

        new_status = {"approved": "approved", "rejected": "rejected", "escalated": "under_review"}[action]
        reason = random.choice({
            "approved": APPROVE_REASONS, "rejected": REJECT_REASONS, "escalated": ESCALATE_REASONS
        }[action])

        sub_date = r.submission_date
        if hasattr(sub_date, "date"):
            sub_date = sub_date.date()
        decision_date = min(sub_date + timedelta(days=random.randint(0, 4)), END_DATE)

        log_id = f"LOG-{uuid.uuid4().hex[:10].upper()}"
        conn.execute(text("""
            INSERT INTO audit_log
                (log_id, claim_id, officer_id, action, previous_status,
                 new_status, officer_comments, shap_viewed, action_timestamp)
            VALUES
                (:lid, :cid, :oid, :action, :prev, :new, :reason, TRUE, :ts)
        """), {
            "lid": log_id, "cid": r.claim_id, "oid": random.choice(officer_ids),
            "action": action, "prev": r.status, "new": new_status,
            "reason": reason, "ts": decision_date,
        })
        conn.execute(text("UPDATE claims SET status = :s WHERE claim_id = :cid"),
                     {"s": new_status, "cid": r.claim_id})
        decided += 1
    return decided, len(rows)


def main():
    with engine.connect() as conn:
        new_patients = add_members(conn)
        conn.commit()
        print(f"Added {len(new_patients)} new SHA members.")

        patients = [r.patient_id for r in conn.execute(text("SELECT patient_id FROM sha_members")).fetchall()]
        providers = [r.provider_id for r in conn.execute(text(
            "SELECT provider_id FROM health_providers WHERE status = 'active'")).fetchall()]
        tariffs = conn.execute(text("""
            SELECT diagnosis_code, procedure_code, facility_tier, approved_amount
            FROM sha_tariffs WHERE effective_to IS NULL
        """)).fetchall()

        new_claim_ids = add_claims(conn, patients, providers, tariffs)
        conn.commit()
        print(f"Inserted {len(new_claim_ids)} new claims.")

    # Run the real verification pipeline on the newly submitted claims
    from ml_pipeline.pipeline import ClaimsVerificationPipeline
    pipeline = ClaimsVerificationPipeline()
    flagged = verified = errors = 0
    with Session(engine) as db:
        for cid in new_claim_ids:
            try:
                result = pipeline.verify(db, cid)
                if result.get("status") == "flagged":
                    flagged += 1
                else:
                    verified += 1
            except Exception as e:
                errors += 1
                print(f"  [WARN] pipeline failed for {cid}: {e}")
    print(f"Verification pipeline: {flagged} flagged, {verified} verified/other, {errors} errors.")

    # Simulate officer decisions across the whole pending queue (old + new)
    with engine.connect() as conn:
        officer_ids = [r.user_id for r in conn.execute(text(
            "SELECT user_id FROM system_users WHERE role IN ('officer','admin') AND is_active = TRUE"
        )).fetchall()]
        decided, total_pending = simulate_officer_decisions(conn, officer_ids)
        conn.commit()
        print(f"Officer decisions: resolved {decided} of {total_pending} pending claims.")

        summary = conn.execute(text("SELECT status, COUNT(*) FROM claims GROUP BY status ORDER BY status")).fetchall()
        print("\nFinal claim status distribution:")
        for s in summary:
            print(f"  {s.status:<20} {s.count}")


if __name__ == "__main__":
    main()
