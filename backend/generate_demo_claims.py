"""
backend/generate_demo_claims.py
Generates synthetic claims (dated 2026-07-01 onward) against the real
patients/providers/tariffs already in the database, builds a labeled
training CSV using the actual feature-engineering code, trains the
XGBoost model, then runs the real verification pipeline against every
synthetic claim so the app has realistic data to demonstrate end to end.

Windows usage:
    cd backend
    venv\\Scripts\\activate
    python generate_demo_claims.py
"""
import os
import random
import uuid
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import pandas as pd

DATABASE_URL = os.getenv("DATABASE_URL",
    "postgresql://sha_app_user:password@localhost:5432/sha_claims_db")
engine = create_engine(DATABASE_URL)

random.seed(7)

N_CLAIMS = 84
START_DATE = date(2026, 7, 1)
END_DATE = date(2026, 7, 14)
ANOMALY_RATE = 0.20   # share of claims deliberately over-billed (label = 1)


def random_date(d1: date, d2: date) -> date:
    span = (d2 - d1).days
    return d1 + timedelta(days=random.randint(0, span))


def main():
    with engine.connect() as conn:
        patients = [r.patient_id for r in conn.execute(text(
            "SELECT patient_id FROM sha_members")).fetchall()]
        providers = [r.provider_id for r in conn.execute(text(
            "SELECT provider_id FROM health_providers WHERE status = 'active'")).fetchall()]
        tariffs = conn.execute(text("""
            SELECT diagnosis_code, procedure_code, facility_tier, approved_amount
            FROM sha_tariffs WHERE effective_to IS NULL
        """)).fetchall()

        print(f"Pool: {len(patients)} patients, {len(providers)} active providers, {len(tariffs)} tariff pairs")

        generated = []  # (claim_id, patient_id, provider_id, service_date, submission_date, diag, proc, amount, tariff_amount, label)
        for i in range(N_CLAIMS):
            patient_id = random.choice(patients)
            is_anomalous = random.random() < ANOMALY_RATE
            provider_id = random.choice(providers)
            tariff = random.choice(tariffs)
            svc_date = random_date(START_DATE, END_DATE)
            delay_days = random.randint(0, 3)
            sub_date = min(svc_date + timedelta(days=delay_days), END_DATE)

            tariff_amount = float(tariff.approved_amount)
            if is_anomalous:
                claimed = round(tariff_amount * random.uniform(4.0, 7.0), 2)
            else:
                claimed = round(tariff_amount * random.uniform(0.97, 1.08), 2)

            claim_id = f"SHA-{uuid.uuid4().hex[:10].upper()}"
            generated.append({
                "claim_id": claim_id, "patient_id": patient_id, "provider_id": provider_id,
                "service_date": svc_date, "submission_date": sub_date,
                "diagnosis_code": tariff.diagnosis_code, "procedure_code": tariff.procedure_code,
                "claimed_amount": claimed, "tariff_amount": tariff_amount,
                "label": 1 if is_anomalous else 0,
            })

        # ── Insert claims (status starts 'submitted'; the pipeline run below updates it) ──
        for c in generated:
            conn.execute(text("""
                INSERT INTO claims
                    (claim_id, patient_id, provider_id, submission_date, service_date,
                     diagnosis_code, procedure_code, claimed_amount, sha_tariff_amount, status)
                VALUES
                    (:claim_id, :patient_id, :provider_id, :submission_date, :service_date,
                     :diagnosis_code, :procedure_code, :claimed_amount, :tariff_amount, 'submitted')
            """), {**c, "submission_date": c["submission_date"]})
        conn.commit()
        print(f"Inserted {len(generated)} synthetic claims ({sum(c['label'] for c in generated)} deliberately over-billed).")

    # ── Build the training CSV using the real feature-engineering code ──
    from ml_pipeline.feature_engineering import build_feature_vector, FEATURE_ORDER

    rows = []
    with Session(engine) as db:
        for c in generated:
            claim_row = db.execute(text("""
                SELECT claim_id, patient_id, provider_id, submission_date, service_date,
                       diagnosis_code, procedure_code, claimed_amount
                FROM claims WHERE claim_id = :cid
            """), {"cid": c["claim_id"]}).fetchone()
            provider_row = db.execute(text("""
                SELECT provider_id, facility_type, county, empanelment_date
                FROM health_providers WHERE provider_id = :pid
            """), {"pid": c["provider_id"]}).fetchone()

            claim_dict = dict(claim_row._mapping)
            provider_dict = dict(provider_row._mapping)
            vec = build_feature_vector(db, claim_dict, provider_dict, c["tariff_amount"])[0]
            row = dict(zip(FEATURE_ORDER, vec))
            row["label"] = c["label"]
            rows.append(row)

    os.makedirs("data", exist_ok=True)
    df = pd.DataFrame(rows)
    csv_path = "data/claims_training_data.csv"
    df.to_csv(csv_path, index=False)
    print(f"Wrote training CSV: {csv_path} ({len(df)} rows, {df['label'].sum()} positive)")

    # ── Train the model ──
    from ml_pipeline.model_config import train_xgboost_model
    X = df[FEATURE_ORDER].values
    y = df["label"].values
    print("Training XGBoost model...")
    model, metrics = train_xgboost_model(X, y, tune_hyperparameters=False)

    os.makedirs("ml_pipeline/artifacts", exist_ok=True)
    import joblib
    model_path = "ml_pipeline/artifacts/xgboost_claims_verifier.joblib"
    joblib.dump(model, model_path)
    print(f"Model saved to {model_path}")
    print(f"  Test F1-score: {metrics['test_f1_score']:.4f}")
    print(f"  Test AUC-ROC:  {metrics['test_auc_roc']:.4f}")

    # ── Run the real verification pipeline against every synthetic claim ──
    from ml_pipeline.pipeline import ClaimsVerificationPipeline
    pipeline = ClaimsVerificationPipeline(model_path=model_path)
    flagged_count = 0
    with Session(engine) as db:
        for c in generated:
            try:
                result = pipeline.verify(db, c["claim_id"])
                if result.get("status") == "flagged":
                    flagged_count += 1
            except Exception as e:
                print(f"  [WARN] pipeline failed for {c['claim_id']}: {e}")
    print(f"Verification pipeline run complete: {flagged_count}/{len(generated)} flagged for officer review.")


if __name__ == "__main__":
    main()
