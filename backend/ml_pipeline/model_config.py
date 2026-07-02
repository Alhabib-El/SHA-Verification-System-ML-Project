"""
ml_pipeline/model_config.py
XGBoost model configuration and training routine (Figure 5.3, Section 5.4.3).
"""
import xgboost as xgb
import mlflow
import mlflow.xgboost
from sklearn.model_selection import train_test_split, StratifiedKFold, GridSearchCV
from sklearn.metrics import classification_report, roc_auc_score, f1_score
from imblearn.over_sampling import SMOTE

# Base hyperparameter configuration — starting point before grid search tuning
BASE_CONFIG = {
    "objective":        "binary:logistic",
    "eval_metric":      ["auc", "aucpr"],
    "n_estimators":      300,
    "learning_rate":      0.05,
    "max_depth":           6,
    "scale_pos_weight":     19,    # ratio of valid : invalid claims — computed from training data
    "alpha":                 0.1,   # L1 regularisation
    "reg_lambda":              1.0,   # L2 regularisation
    "subsample":                 0.8,
    "colsample_bytree":            0.8,
    "random_state":                  42,
}

# Grid search space used for hyperparameter tuning (Table 3.2, Chapter Three)
PARAM_GRID = {
    "learning_rate": [0.01, 0.05, 0.1, 0.3],
    "max_depth":     [3, 5, 7, 10],
    "n_estimators":  [100, 200, 300, 500],
    "alpha":         [0, 0.1, 0.5, 1.0],
    "reg_lambda":    [0.5, 1.0, 2.0],
}


def apply_smote(X_train, y_train):
    """Synthetic Minority Over-sampling — addresses class imbalance (Section 3.6.1)."""
    smote = SMOTE(random_state=42)
    X_resampled, y_resampled = smote.fit_resample(X_train, y_train)
    return X_resampled, y_resampled


def train_xgboost_model(X, y, tune_hyperparameters: bool = False):
    """
    Trains the XGBoost claims verification model.

    Steps:
      1. 70/15/15 train/validation/test split (stratified)
      2. SMOTE applied to training set only
      3. Optional grid search with 5-fold stratified cross-validation
      4. Final model trained with early stopping
      5. All runs logged to MLflow
    """
    X_train, X_temp, y_train, y_temp = train_test_split(
        X, y, test_size=0.30, stratify=y, random_state=42
    )
    X_val, X_test, y_val, y_test = train_test_split(
        X_temp, y_temp, test_size=0.50, stratify=y_temp, random_state=42
    )

    X_train_res, y_train_res = apply_smote(X_train, y_train)

    with mlflow.start_run(run_name="xgboost_claims_verification"):

        if tune_hyperparameters:
            cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
            base_model = xgb.XGBClassifier(**{**BASE_CONFIG, **{
                k: v for k, v in BASE_CONFIG.items() if k not in PARAM_GRID
            }})
            grid = GridSearchCV(
                base_model, PARAM_GRID, scoring="f1", cv=cv, n_jobs=-1, verbose=1
            )
            grid.fit(X_train_res, y_train_res)
            best_params = grid.best_params_
            mlflow.log_params(best_params)
            config = {**BASE_CONFIG, **best_params}
        else:
            config = BASE_CONFIG
            mlflow.log_params(config)

        model = xgb.XGBClassifier(**config, early_stopping_rounds=20)
        model.fit(
            X_train_res, y_train_res,
            eval_set=[(X_val, y_val)],
            verbose=50
        )

        # Evaluation on held-out test set (Section 3.6.4)
        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]

        metrics = {
            "test_f1_score": f1_score(y_test, y_pred),
            "test_auc_roc":  roc_auc_score(y_test, y_proba),
        }
        mlflow.log_metrics(metrics)
        mlflow.xgboost.log_model(model, "xgboost_claims_verifier")

        print(classification_report(y_test, y_pred))
        print(f"AUC-ROC: {metrics['test_auc_roc']:.4f}")

        return model, metrics
