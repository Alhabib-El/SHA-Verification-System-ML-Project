"""
ml_pipeline/shap_explainer.py
Stage 5 of the verification pipeline (Figure 5.3): SHAP Explanation.
Translates raw SHAP values into the human-readable format shown in
the Officer Review screen (Figure 5.6).
"""
import shap
import numpy as np
from typing import List, Dict
from .feature_engineering import FEATURE_ORDER

# Human-readable labels and explanation templates for each feature
FEATURE_LABELS = {
    "amount_ratio": "Billed amount is {ratio}x the SHA approved tariff",
    "provider_claim_freq_30d": "Provider submitted {val} claims in the last 30 days",
    "provider_amount_zscore": "Provider's billing is a statistical outlier (z-score {val})",
    "diagnosis_procedure_match": "Procedure code does not match the diagnosis submitted",
    "submission_delay_days": "Unusual delay of {val} days between service and submission",
    "patient_facility_count_7d": "Patient claimed at {val} facilities within 7 days",
    "provider_age_days": "Provider was empanelled only {val} days ago",
    "provider_approval_rate": "Provider has a {val}% historical approval rate",
    "patient_claim_count_90d": "Patient has {val} claims in the past 90 days",
    "repeat_diagnosis_flag": "Same diagnosis claimed for this patient within 30 days",
}


class SHAPExplainer:
    """Wraps shap.TreeExplainer for the trained XGBoost model."""

    def __init__(self, trained_model):
        self.explainer = shap.TreeExplainer(trained_model)

    def explain(self, feature_vector: np.ndarray, top_n: int = 5) -> List[Dict]:
        """
        Computes SHAP values for a single claim's feature vector and
        returns the top N most influential features, formatted for
        display in the Officer Review screen (Figure 5.6).
        """
        shap_values = self.explainer.shap_values(feature_vector)[0]  # single instance

        # Pair each feature name with its SHAP value
        contributions = list(zip(FEATURE_ORDER, shap_values, feature_vector[0]))

        # Sort by absolute SHAP value, descending — most influential first
        contributions.sort(key=lambda x: abs(x[1]), reverse=True)
        top_contributions = contributions[:top_n]

        formatted = []
        for feature_name, shap_val, raw_val in top_contributions:
            label_template = FEATURE_LABELS.get(feature_name, feature_name)
            try:
                label = label_template.format(
                    ratio=round(raw_val, 2),
                    val=round(raw_val) if raw_val == int(raw_val) else round(raw_val, 2)
                )
            except (KeyError, ValueError):
                label = label_template

            formatted.append({
                "feature": feature_name,
                "label": label,
                "value": round(float(shap_val), 4),
                "direction": "up" if shap_val > 0 else "down",
            })

        return formatted

    def get_raw_shap_dict(self, feature_vector: np.ndarray) -> Dict[str, float]:
        """Returns the complete SHAP value set for audit/storage purposes."""
        shap_values = self.explainer.shap_values(feature_vector)[0]
        return {name: round(float(val), 4) for name, val in zip(FEATURE_ORDER, shap_values)}
