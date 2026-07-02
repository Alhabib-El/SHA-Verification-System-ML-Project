"""
ml_pipeline/pipeline.py
ClaimsVerificationPipeline — the single public entry point implementing
all six stages of the verification process (Figure 5.3 / Table 5.2).
"""
import uuid
import joblib
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import text

from .feature_engineering import build_feature_vector
from .shap_explainer import SHAPExplainer

MODEL_PATH = "ml_pipeline/artifacts/xgboost_claims_verifier.joblib"
MODEL_VERSION = "xgb_v1.2_2025-03"
DEFAULT_FLAG_THRESHOLD = 0.70


class ClaimsVerificationPipeline:
    """
    Encapsulates the complete claims verification process.
    Usage:
        pipeline = ClaimsVerificationPipeline()
        result = pipeline.verify(db, claim_id)
    """

    def __init__(self, model_path: str = MODEL_PATH):
        self.model = joblib.load(model_path)
        self.shap_explainer = SHAPExplainer(self.model)

    # ── STAGE 1: Rule-based pre-checks ──────────────────────────────────────
    def _run_prechecks(self, db: Session, claim: dict) -> dict:
        member = db.execute(text("""
            SELECT eligibility_status, eligibility_expiry, coverage_package
            FROM sha_members WHERE patient_id = :pid
        """), {"pid": claim["patient_id"]}).fetchone()

        provider = db.execute(text("""
            SELECT status, accreditation_expiry FROM health_providers
            WHERE provider_id = :prov
        """), {"prov": claim["provider_id"]}).fetchone()

        tariff = db.execute(text("""
            SELECT approved_amount FROM sha_tariffs
            WHERE diagnosis_code = :diag AND procedure_code = :proc
              AND (effective_to IS NULL OR effective_to >= :svc_date)
            ORDER BY effective_from DESC LIMIT 1
        """), {
            "diag": claim["diagnosis_code"],
            "proc": claim["procedure_code"],
            "svc_date": claim["service_date"],
        }).fetchone()

        eligibility_check = bool(
            member and member.eligibility_status == "active"
            and member.eligibility_expiry >= date.today()
        )
        provider_check = bool(provider and provider.status == "active")
        coverage_check = bool(tariff is not None)
        tariff_amount = float(tariff.approved_amount) if tariff else None

        return {
            "eligibility_check": eligibility_check,
            "provider_check": provider_check,
            "coverage_check": coverage_check,
            "tariff_amount": tariff_amount,
            "coverage_package": member.coverage_package if member else None,
        }

    # ── PUBLIC ENTRY POINT ────────────────────────────────────────────────
    def verify(self, db: Session, claim_id: str) -> dict:
        """
        Runs the complete six-stage verification pipeline for a single claim
        and persists the result. Returns the structured verification result.
        """
        claim_row = db.execute(text("""
            SELECT claim_id, patient_id, provider_id, submission_date,
                   service_date, diagnosis_code, procedure_code, claimed_amount
            FROM claims WHERE claim_id = :cid
        """), {"cid": claim_id}).fetchone()
        if not claim_row:
            raise ValueError(f"Claim {claim_id} not found")
        claim = dict(claim_row._mapping)

        provider_row = db.execute(text("""
            SELECT provider_id, facility_type, county, empanelment_date
            FROM health_providers WHERE provider_id = :pid
        """), {"pid": claim["provider_id"]}).fetchone()
        provider = dict(provider_row._mapping)

        # ── STAGE 1 ──────────────────────────────────────────────────────
        prechecks = self._run_prechecks(db, claim)

        if not (prechecks["eligibility_check"] and prechecks["provider_check"]
                and prechecks["coverage_check"]):
            # Claim fails basic verification — reject without ML inference
            return self._persist_result(
                db, claim_id,
                eligibility_check=prechecks["eligibility_check"],
                provider_check=prechecks["provider_check"],
                coverage_check=prechecks["coverage_check"],
                clinical_match=False, billing_compliant=False,
                xgboost_score=None, is_flagged=True,
                top_features=None, shap_raw=None,
                claim_status="rejected_precheck",
            )

        claim["coverage_package"] = prechecks["coverage_package"]
        tariff_amount = prechecks["tariff_amount"]

        # ── STAGE 2 + 3: Preprocessing + Feature Engineering ────────────────
        feature_vector = build_feature_vector(db, claim, provider, tariff_amount)

        from .feature_engineering import check_clinical_match
        clinical_match = check_clinical_match(claim["diagnosis_code"], claim["procedure_code"])
        billing_compliant = (claim["claimed_amount"] / tariff_amount) <= 1.20 if tariff_amount else False

        # ── STAGE 4: XGBoost Inference ───────────────────────────────────
        xgboost_score = float(self.model.predict_proba(feature_vector)[0][1])

        # ── STAGE 5: SHAP Explanation ────────────────────────────────────
        top_features = self.shap_explainer.explain(feature_vector, top_n=5)
        shap_raw = self.shap_explainer.get_raw_shap_dict(feature_vector)

        # ── STAGE 6: Result Assembly ─────────────────────────────────────
        is_flagged = xgboost_score > DEFAULT_FLAG_THRESHOLD
        claim_status = "flagged" if is_flagged else "verified"

        return self._persist_result(
            db, claim_id,
            eligibility_check=True, provider_check=True, coverage_check=True,
            clinical_match=clinical_match, billing_compliant=billing_compliant,
            xgboost_score=xgboost_score, is_flagged=is_flagged,
            top_features=top_features, shap_raw=shap_raw,
            claim_status=claim_status,
        )

    def _persist_result(self, db: Session, claim_id: str, **kwargs) -> dict:
        result_id = f"RES-{uuid.uuid4().hex[:10].upper()}"
        claim_status = kwargs.pop("claim_status")

        db.execute(text("""
            INSERT INTO verification_results
                (result_id, claim_id, eligibility_check, coverage_check,
                 provider_check, clinical_match, billing_compliant,
                 xgboost_score, model_version, flag_threshold, is_flagged,
                 shap_values, top_features)
            VALUES
                (:result_id, :claim_id, :eligibility_check, :coverage_check,
                 :provider_check, :clinical_match, :billing_compliant,
                 :xgboost_score, :model_version, :flag_threshold, :is_flagged,
                 :shap_values, :top_features)
        """), {
            "result_id": result_id, "claim_id": claim_id,
            "model_version": MODEL_VERSION, "flag_threshold": DEFAULT_FLAG_THRESHOLD,
            "shap_values": kwargs.pop("shap_raw"),
            "top_features": kwargs.pop("top_features"),
            **kwargs,
        })

        db.execute(text("UPDATE claims SET status = :status WHERE claim_id = :cid"),
                   {"status": claim_status, "cid": claim_id})
        db.commit()

        return {"result_id": result_id, "claim_id": claim_id,
                "status": claim_status, **kwargs}
