"""
app/schemas.py
Pydantic schemas for request/response validation (FastAPI).
"""
from datetime import date, datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ── Claims ──────────────────────────────────────────────────────────────────
class ClaimSubmitRequest(BaseModel):
    patient_sha_member_no: str = Field(..., example="SHA-2024-00123")
    provider_id: str            = Field(..., example="PRV-001")
    service_date: date
    diagnosis_code: str          = Field(..., example="J18.0")
    procedure_code: str           = Field(..., example="99233")
    claimed_amount: float           = Field(..., gt=0, example=9800.00)


class ClaimResponse(BaseModel):
    claim_id: str
    patient_id: str
    provider_id: str
    submission_date: datetime
    service_date: date
    diagnosis_code: str
    procedure_code: str
    claimed_amount: float
    sha_tariff_amount: Optional[float]
    amount_ratio: Optional[float]
    status: str

    class Config:
        from_attributes = True


# ── Verification ────────────────────────────────────────────────────────────
class TopFeature(BaseModel):
    feature: str
    value: float
    direction: str   # "up" or "down"


class VerificationResultResponse(BaseModel):
    result_id: str
    claim_id: str
    eligibility_check: bool
    coverage_check: bool
    provider_check: bool
    clinical_match: bool
    billing_compliant: bool
    xgboost_score: Optional[float]
    is_flagged: bool
    top_features: Optional[List[TopFeature]]

    class Config:
        from_attributes = True


# ── Officer Decision ────────────────────────────────────────────────────────
class OfficerDecisionRequest(BaseModel):
    claim_id: str
    action: str = Field(..., pattern="^(approved|rejected|escalated)$")
    comments: str = Field(..., min_length=5)
    shap_viewed: bool = True


# ── CRUD: Provider ──────────────────────────────────────────────────────────
class ProviderCreateRequest(BaseModel):
    name: str
    facility_type: str = Field(..., pattern="^(Hospital|Clinic|Pharmacy|Laboratory)$")
    facility_tier: str
    county: str
    accreditation_no: str
    regulatory_body: str = Field(..., pattern="^(KMPDC|NCK|PPB)$")


class ProviderUpdateRequest(BaseModel):
    status: Optional[str] = Field(None, pattern="^(active|suspended|revoked)$")
    risk_tier: Optional[str] = Field(None, pattern="^(low|medium|high)$")


class ProviderResponse(BaseModel):
    provider_id: str
    name: str
    facility_type: str
    county: str
    status: str
    risk_tier: str

    class Config:
        from_attributes = True


# ── Auth ─────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
