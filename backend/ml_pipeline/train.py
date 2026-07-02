"""
ml_pipeline/train.py
Standalone training script — run this to train and save the XGBoost
claims verification model (Chapter Six implementation step).

Usage:
    python -m ml_pipeline.train --data data/claims_training_data.csv
"""
import argparse
import joblib
import pandas as pd

from .model_config import train_xgboost_model
from .feature_engineering import FEATURE_ORDER

MODEL_OUTPUT_PATH = "ml_pipeline/artifacts/xgboost_claims_verifier.joblib"


def main(data_path: str, tune: bool):
    print(f"Loading training data from {data_path}...")
    df = pd.read_csv(data_path)

    missing_cols = set(FEATURE_ORDER + ["label"]) - set(df.columns)
    if missing_cols:
        raise ValueError(f"Training data is missing required columns: {missing_cols}")

    X = df[FEATURE_ORDER].values
    y = df["label"].values   # 1 = invalid/flagged claim, 0 = valid claim

    print(f"Training on {len(X)} samples ({y.sum()} positive / {len(y) - y.sum()} negative)...")
    model, metrics = train_xgboost_model(X, y, tune_hyperparameters=tune)

    print(f"Saving trained model to {MODEL_OUTPUT_PATH}...")
    joblib.dump(model, MODEL_OUTPUT_PATH)

    print("Training complete.")
    print(f"  Test F1-score: {metrics['test_f1_score']:.4f}")
    print(f"  Test AUC-ROC:  {metrics['test_auc_roc']:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train the SHA claims verification XGBoost model")
    parser.add_argument("--data", required=True, help="Path to training data CSV")
    parser.add_argument("--tune", action="store_true", help="Run hyperparameter grid search")
    args = parser.parse_args()
    main(args.data, args.tune)
