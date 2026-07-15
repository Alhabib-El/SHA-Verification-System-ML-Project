"""
backend/expand_demo_data_v2.py
Second expansion pass:
  1. Ensures every one of Kenya's 47 counties has at least 5 Hospital-type
     providers (adds real-style county/sub-county/mission hospitals).
  2. Adds 150 more SHA members with Kenyan names, balanced across counties.
  3. Adds 100 more claims for training, backdated 2026-07-01..2026-07-15,
     run through the real verification pipeline and officer-decision
     simulation so they end up approved/rejected/flagged like real history.
  4. Pushes a handful of providers to "almost suspended" (7-9 flags) and a
     couple of new ones to real auto-suspension, spread across different
     counties this time (not concentrated in the same 2 as before).

Windows usage:
    cd backend
    venv\\Scripts\\activate
    python expand_demo_data_v2.py
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

random.seed(2026)

START_DATE = date(2026, 7, 1)
END_DATE = date(2026, 7, 15)

# ── County -> two representative towns, used to build realistic hospital names ──
COUNTY_TOWNS = {
    "Mombasa": ("Likoni", "Nyali"), "Kwale": ("Kwale", "Ukunda"),
    "Kilifi": ("Malindi", "Mariakani"), "Tana River": ("Hola", "Garsen"),
    "Lamu": ("Lamu", "Mpeketoni"), "Taita-Taveta": ("Voi", "Taveta"),
    "Garissa": ("Garissa", "Dadaab"), "Wajir": ("Wajir", "Habaswein"),
    "Mandera": ("Mandera", "Elwak"), "Marsabit": ("Marsabit", "Moyale"),
    "Isiolo": ("Isiolo", "Merti"), "Meru": ("Maua", "Nkubu"),
    "Tharaka-Nithi": ("Chuka", "Marimanti"), "Embu": ("Runyenjes", "Siakago"),
    "Kitui": ("Mwingi", "Mutomo"), "Machakos": ("Athi River", "Mwala"),
    "Makueni": ("Wote", "Kibwezi"), "Nyandarua": ("Ol Kalou", "Engineer"),
    "Nyeri": ("Karatina", "Othaya"), "Kirinyaga": ("Kerugoya", "Sagana"),
    "Murang'a": ("Kenol", "Kandara"), "Kiambu": ("Thika", "Ruiru"),
    "Turkana": ("Lodwar", "Kakuma"), "West Pokot": ("Kapenguria", "Chepareria"),
    "Samburu": ("Maralal", "Baragoi"), "Trans Nzoia": ("Kitale", "Endebess"),
    "Uasin Gishu": ("Eldoret", "Turbo"), "Elgeyo-Marakwet": ("Iten", "Kapsowar"),
    "Nandi": ("Kapsabet", "Nandi Hills"), "Baringo": ("Kabarnet", "Marigat"),
    "Laikipia": ("Nanyuki", "Rumuruti"), "Nakuru": ("Naivasha", "Molo"),
    "Narok": ("Narok", "Kilgoris"), "Kajiado": ("Ngong", "Kitengela"),
    "Kericho": ("Kericho", "Litein"), "Bomet": ("Bomet", "Sotik"),
    "Kakamega": ("Mumias", "Malava"), "Vihiga": ("Mbale", "Vihiga"),
    "Bungoma": ("Webuye", "Kimilili"), "Busia": ("Malaba", "Busia"),
    "Siaya": ("Bondo", "Ugunja"), "Kisumu": ("Muhoroni", "Ahero"),
    "Homa Bay": ("Mbita", "Oyugis"), "Migori": ("Rongo", "Awendo"),
    "Kisii": ("Ogembo", "Nyamache"), "Nyamira": ("Keroka", "Nyamira"),
    "Nairobi": ("Kasarani", "Embakasi"),
}
TIER_BY_SLOT = ["Level 5", "Level 4", "Level 3", "Level 4", "Level 2"]


def name_for_slot(county, town1, town2, slot):
    return {
        0: f"{county} County Referral Hospital",
        1: f"{town1} Sub-County Hospital",
        2: f"{town2} District Hospital",
        3: f"St. Joseph's Mission Hospital, {town1}",
        4: f"{town2} Health Centre",
    }[slot]


def add_hospitals(conn):
    counts = dict(conn.execute(text("""
        SELECT county, COUNT(*) FILTER (WHERE facility_type='Hospital')
        FROM health_providers GROUP BY county
    """)).fetchall())
    max_num = max(int(r.provider_id.split("-")[1]) for r in conn.execute(text(
        "SELECT provider_id FROM health_providers")).fetchall())
    existing_names = {r.name for r in conn.execute(text("SELECT name FROM health_providers")).fetchall()}

    next_id = max_num + 1
    accno_counter = 3000
    added = 0
    for county, (town1, town2) in COUNTY_TOWNS.items():
        have = counts.get(county, 0)
        need = max(0, 5 - have)
        slot = 0
        while need > 0:
            name = name_for_slot(county, town1, town2, slot % 5)
            slot += 1
            if slot > 20:  # safety valve, should never hit with 5 unique templates
                break
            if name in existing_names:
                continue
            existing_names.add(name)
            provider_id = f"PRV-{next_id:03d}"
            next_id += 1
            accno_counter += 1
            tier = TIER_BY_SLOT[(slot - 1) % 5]
            empanel = date(random.randint(2012, 2023), random.randint(1, 12), random.randint(1, 28))
            expiry = date(empanel.year + 10, empanel.month, min(empanel.day, 28))
            conn.execute(text("""
                INSERT INTO health_providers
                    (provider_id, name, facility_type, facility_tier, county,
                     accreditation_no, regulatory_body, empanelment_date,
                     accreditation_expiry, status, risk_tier)
                VALUES
                    (:pid, :name, 'Hospital', :tier, :county, :accno, 'KMPDC',
                     :empanel, :expiry, 'active', 'low')
            """), {
                "pid": provider_id, "name": name, "tier": tier, "county": county,
                "accno": f"KMPDC-{empanel.year}-{accno_counter}",
                "empanel": empanel, "expiry": expiry,
            })
            added += 1
            need -= 1
    return added


MALE_FIRST = ["James","Peter","John","Daniel","Michael","David","Samuel","Joseph","Paul",
              "Stephen","Francis","Charles","Anthony","Kevin","Brian","Dennis","Felix",
              "Moses","Amos","Erick","Vincent","Patrick","Edwin","Duncan","Collins",
              "Robert","George","Simon","Bernard","Geoffrey","Elias"]
FEMALE_FIRST = ["Mary","Jane","Grace","Faith","Ann","Alice","Ruth","Esther","Lucy","Nancy",
                "Catherine","Elizabeth","Winnie","Susan","Joyce","Beatrice","Agnes","Judith",
                "Sharon","Caroline","Purity","Mercy","Diana","Brenda","Irene","Rose",
                "Eunice","Loise","Consolata","Millicent"]
SURNAMES = ["Mwangi","Wanjiru","Otieno","Achieng","Kamau","Njoroge","Wafula","Nekesa","Kiptoo",
            "Chebet","Mutua","Nduta","Hassan","Abdi","Wekesa","Ouma","Kariuki","Njeri",
            "Cheruiyot","Kones","Barasa","Onyango","Wambui","Muthoni","Kilonzo","Mbithi",
            "Too","Rotich","Simiyu","Were","Mwende","Kioko","Wairimu","Gitau","Owino","Adhiambo",
            "Chepkoech","Langat","Mochama","Nyongesa","Wanyama","Kiplagat","Wangari","Njau",
            "Odhiambo","Auma","Kemboi","Bett","Sang","Cherono"]
PACKAGES = (["SHIF Basic"]*6 + ["Primary Healthcare Fund"]*3 + ["Chronic Illness Fund"]*1)


def random_date(d1, d2):
    return d1 + timedelta(days=random.randint(0, (d2 - d1).days))


def add_members(conn, n):
    existing_ids = [r.patient_id for r in conn.execute(text("SELECT patient_id FROM sha_members")).fetchall()]
    max_num = max(int(pid.split("-")[1]) for pid in existing_ids)
    existing_nids = {r.national_id for r in conn.execute(text("SELECT national_id FROM sha_members")).fetchall()}
    existing_memnos = {r.sha_member_no for r in conn.execute(text("SELECT sha_member_no FROM sha_members")).fetchall()}
    counties = list(COUNTY_TOWNS.keys())

    new_ids = []
    for i in range(1, n + 1):
        patient_id = f"PAT-{max_num + i:04d}"
        gender = random.choice(["Male", "Female"])
        first = random.choice(MALE_FIRST) if gender == "Male" else random.choice(FEMALE_FIRST)
        last = random.choice(SURNAMES)

        while True:
            nid = str(random.randint(20000000, 39999999))
            if nid not in existing_nids:
                existing_nids.add(nid); break
        memno = f"SHA-2025-{3000 + i:05d}"
        while memno in existing_memnos:
            memno = f"SHA-2025-{3000 + i + random.randint(1, 999):05d}"
        existing_memnos.add(memno)

        dob = random_date(date(1955, 1, 1), date(2007, 12, 31))
        reg_date = random_date(date(2024, 1, 1), date(2026, 6, 30))
        # county chosen round-robin across all 47 for even balance
        county = counties[i % len(counties)]
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
            "county": county, "reg": reg_date, "pkg": random.choice(PACKAGES),
            "elig": elig_status, "exp": elig_expiry,
        })
        new_ids.append(patient_id)
    return new_ids


APPROVE_REASONS = [
    "Approved — claim amount consistent with SHA tariff schedule and clinical documentation.",
    "Approved — verified eligibility, provider accreditation and billing all in order.",
    "Approved after review — minor discrepancy explained, documentation attached.",
    "Approved — pre-checks and clinical match confirmed, no further action required.",
]
REJECT_REASONS = [
    "Rejected — claimed amount significantly exceeds approved tariff with no supporting justification.",
    "Rejected — diagnosis and procedure combination inconsistent with clinical guidelines.",
    "Rejected — provider unable to provide supporting documentation for the billed amount.",
    "Rejected — duplicate claim detected for same patient and service date.",
]


def add_training_claims(conn, patients, providers, tariffs, n):
    generated = []
    for _ in range(n):
        patient_id = random.choice(patients)
        provider_id = random.choice(providers)
        tariff = random.choice(tariffs)
        is_anomalous = random.random() < 0.20
        svc_date = random_date(START_DATE, END_DATE)
        sub_date = min(svc_date + timedelta(days=random.randint(0, 2)), END_DATE)
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


def simulate_officer_decisions(conn, officer_ids, claim_ids):
    """Resolves ~70% of the given claims (if pending) into approved/rejected,
    with backdated audit_log entries — leaving some genuinely pending."""
    rows = conn.execute(text("""
        SELECT claim_id, status, submission_date FROM claims
        WHERE claim_id = ANY(:ids) AND status IN ('flagged', 'verified', 'under_review')
    """), {"ids": claim_ids}).fetchall()

    decided = 0
    for r in rows:
        if random.random() > 0.70:
            continue
        if r.status == "flagged":
            action = random.choices(["rejected", "approved"], weights=[55, 45])[0]
        else:
            action = random.choices(["approved", "rejected"], weights=[88, 12])[0]
        new_status = action
        reason = random.choice(APPROVE_REASONS if action == "approved" else REJECT_REASONS)

        sub_date = r.submission_date
        if hasattr(sub_date, "date"):
            sub_date = sub_date.date()
        decision_date = min(sub_date + timedelta(days=random.randint(0, 3)), END_DATE)

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


def push_toward_suspension(conn, near_threshold_providers, suspend_providers):
    """Adds deliberately over-billed claims: enough for `near_threshold_providers`
    to sit at 7-9 flags (visible as at-risk, not suspended), and enough for
    `suspend_providers` to cross the real 10-flag/30-day threshold."""
    patients = [r.patient_id for r in conn.execute(text("SELECT patient_id FROM sha_members")).fetchall()]
    tariffs = conn.execute(text("""
        SELECT diagnosis_code, procedure_code, approved_amount FROM sha_tariffs WHERE effective_to IS NULL
    """)).fetchall()

    new_claim_ids = []
    for provider_id, n_claims in near_threshold_providers + suspend_providers:
        for _ in range(n_claims):
            patient_id = random.choice(patients)
            tariff = random.choice(tariffs)
            svc_date = random_date(START_DATE, END_DATE)
            sub_date = min(svc_date + timedelta(days=random.randint(0, 2)), END_DATE)
            tariff_amount = float(tariff.approved_amount)
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
    return new_claim_ids


def main():
    with engine.connect() as conn:
        added_hospitals = add_hospitals(conn)
        conn.commit()
        print(f"Added {added_hospitals} hospitals across under-represented counties.")

        new_patients = add_members(conn, 150)
        conn.commit()
        print(f"Added {len(new_patients)} new SHA members.")

        patients = [r.patient_id for r in conn.execute(text("SELECT patient_id FROM sha_members")).fetchall()]
        providers = [r.provider_id for r in conn.execute(text(
            "SELECT provider_id FROM health_providers WHERE status = 'active'")).fetchall()]
        tariffs = conn.execute(text("""
            SELECT diagnosis_code, procedure_code, facility_tier, approved_amount
            FROM sha_tariffs WHERE effective_to IS NULL
        """)).fetchall()

        training_claim_ids = add_training_claims(conn, patients, providers, tariffs, 100)
        conn.commit()
        print(f"Inserted {len(training_claim_ids)} new training claims.")

        # Pick distinct, currently-unsuspended providers spread across
        # different counties for the "near-threshold" and "suspend" groups.
        # Mount Kenya University Teaching & Referral Hospital (PRV-005) is
        # explicitly pushed into real auto-suspension per request.
        candidates = conn.execute(text("""
            SELECT provider_id, county FROM health_providers
            WHERE status = 'active' AND facility_type = 'Hospital' AND provider_id != 'PRV-005'
            ORDER BY random() LIMIT 5
        """)).fetchall()
        near_threshold = [(candidates[0].provider_id, 8), (candidates[1].provider_id, 7), (candidates[2].provider_id, 9)]
        to_suspend = [(candidates[3].provider_id, 8), (candidates[4].provider_id, 8), ("PRV-005", 10)]
        print("Near-threshold providers:", near_threshold)
        print("Providers to push into suspension:", to_suspend)

        suspension_claim_ids = push_toward_suspension(conn, near_threshold, to_suspend)
        conn.commit()
        print(f"Inserted {len(suspension_claim_ids)} deliberately over-billed claims for threshold testing.")

    # ── Run the real verification pipeline on all new claims ──
    from ml_pipeline.pipeline import ClaimsVerificationPipeline
    pipeline = ClaimsVerificationPipeline()
    all_new_claims = training_claim_ids + suspension_claim_ids
    flagged = 0
    with Session(engine) as db:
        for cid in all_new_claims:
            try:
                result = pipeline.verify(db, cid)
                if result.get("status") == "flagged":
                    flagged += 1
            except Exception as e:
                print(f"  [WARN] pipeline failed for {cid}: {e}")
    print(f"Verification pipeline run complete: {flagged}/{len(all_new_claims)} flagged.")

    # ── Simulate officer decisions on the training batch only (not the
    #    threshold-testing claims, so flag counts for those stay intact) ──
    with engine.connect() as conn:
        officer_ids = [r.user_id for r in conn.execute(text(
            "SELECT user_id FROM system_users WHERE role IN ('officer','admin') AND is_active = TRUE"
        )).fetchall()]
        decided, total_pending = simulate_officer_decisions(conn, officer_ids, training_claim_ids)
        conn.commit()
        print(f"Officer decisions: resolved {decided} of {total_pending} pending training claims.")

        summary = conn.execute(text("SELECT status, COUNT(*) FROM claims GROUP BY status ORDER BY status")).fetchall()
        print("\nFinal claim status distribution:")
        for s in summary:
            print(f"  {s.status:<20} {s.count}")

        print("\nNear-threshold / suspended provider status:")
        for pid, _ in near_threshold + to_suspend:
            row = conn.execute(text("""
                SELECT p.provider_id, p.name, p.county, p.status,
                    COUNT(*) FILTER (WHERE vr.is_flagged AND c.submission_date >= NOW() - INTERVAL '30 days') AS flags_30d
                FROM health_providers p
                LEFT JOIN claims c ON c.provider_id = p.provider_id
                LEFT JOIN verification_results vr ON vr.claim_id = c.claim_id
                WHERE p.provider_id = :pid
                GROUP BY p.provider_id, p.name, p.county, p.status
            """), {"pid": pid}).fetchone()
            print(f"  {row.provider_id} {row.name} ({row.county}): status={row.status} flags_30d={row.flags_30d}")


if __name__ == "__main__":
    main()
