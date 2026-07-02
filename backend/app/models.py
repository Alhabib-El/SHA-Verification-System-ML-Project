"""
app/models.py
SQLAlchemy ORM models mapping directly to the database schema (schema.sql).
"""
from sqlalchemy import (
    Column, String, Boolean, Float, Integer, Date, DateTime,
    DECIMAL, Text, ForeignKey, CheckConstraint, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from .database import Base


class SystemUser(Base):
    __tablename__ = "system_users"

    user_id       = Column(String(20), primary_key=True)
    full_name     = Column(String(150), nullable=False)
    email         = Column(String(150), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    role          = Column(String(20), nullable=False)   # provider/officer/admin/finance
    provider_id   = Column(String(20), nullable=True)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, server_default=func.now())
    last_login_at = Column(DateTime, nullable=True)


class SHAMember(Base):
    __tablename__ = "sha_members"

    patient_id         = Column(String(20), primary_key=True)
    national_id         = Column(String(20), nullable=False, unique=True)
    sha_member_no         = Column(String(20), nullable=False, unique=True)
    full_name               = Column(String(150), nullable=False)
    date_of_birth             = Column(Date, nullable=False)
    gender                      = Column(String(10))
    county                        = Column(String(50), nullable=False)
    registration_date               = Column(Date, nullable=False)
    coverage_package                  = Column(String(30), nullable=False)
    eligibility_status                  = Column(String(20), default="active")
    eligibility_expiry                    = Column(Date, nullable=False)
    created_at                              = Column(DateTime, server_default=func.now())

    claims = relationship("Claim", back_populates="patient")


class HealthProvider(Base):
    __tablename__ = "health_providers"

    provider_id           = Column(String(20), primary_key=True)
    name                    = Column(String(150), nullable=False)
    facility_type             = Column(String(20), nullable=False)
    facility_tier                = Column(String(10), nullable=False)
    county                         = Column(String(50), nullable=False)
    accreditation_no                 = Column(String(50), nullable=False, unique=True)
    regulatory_body                    = Column(String(10), nullable=False)
    empanelment_date                     = Column(Date, nullable=False)
    accreditation_expiry                   = Column(Date, nullable=False)
    status                                   = Column(String(20), default="active")
    risk_tier                                  = Column(String(10), default="low")
    bank_account_no                              = Column(String(30), nullable=True)
    created_at                                     = Column(DateTime, server_default=func.now())

    claims = relationship("Claim", back_populates="provider")


class SHATariff(Base):
    __tablename__ = "sha_tariffs"

    tariff_id         = Column(String(20), primary_key=True)
    diagnosis_code      = Column(String(10), nullable=False)
    procedure_code         = Column(String(10), nullable=False)
    facility_tier             = Column(String(10), nullable=False)
    approved_amount              = Column(DECIMAL(12, 2), nullable=False)
    effective_from                  = Column(Date, nullable=False)
    effective_to                      = Column(Date, nullable=True)
    created_at                          = Column(DateTime, server_default=func.now())


class Claim(Base):
    __tablename__ = "claims"

    claim_id              = Column(String(20), primary_key=True)
    patient_id              = Column(String(20), ForeignKey("sha_members.patient_id"), nullable=False)
    provider_id                = Column(String(20), ForeignKey("health_providers.provider_id"), nullable=False)
    submission_date               = Column(DateTime, server_default=func.now())
    service_date                     = Column(Date, nullable=False)
    diagnosis_code                      = Column(String(10), nullable=False)
    procedure_code                         = Column(String(10), nullable=False)
    claimed_amount                            = Column(DECIMAL(12, 2), nullable=False)
    sha_tariff_amount                            = Column(DECIMAL(12, 2), nullable=True)
    amount_ratio                                    = Column(DECIMAL(8, 4))   # generated column (read-only)
    submission_delay_days                              = Column(Integer)          # generated column (read-only)
    status                                                = Column(String(20), default="submitted")
    created_at                                              = Column(DateTime, server_default=func.now())
    updated_at                                                = Column(DateTime, server_default=func.now(), onupdate=func.now())

    patient  = relationship("SHAMember", back_populates="claims")
    provider = relationship("HealthProvider", back_populates="claims")
    verification = relationship("VerificationResult", back_populates="claim", uselist=False)
    audit_entries = relationship("AuditLog", back_populates="claim")


class VerificationResult(Base):
    __tablename__ = "verification_results"

    result_id             = Column(String(20), primary_key=True)
    claim_id                = Column(String(20), ForeignKey("claims.claim_id"), nullable=False, unique=True)
    eligibility_check          = Column(Boolean, nullable=False)
    coverage_check                = Column(Boolean, nullable=False)
    provider_check                   = Column(Boolean, nullable=False)
    clinical_match                      = Column(Boolean, nullable=False)
    billing_compliant                      = Column(Boolean, nullable=False)
    xgboost_score                             = Column(Float, nullable=True)
    model_version                                = Column(String(30), nullable=True)
    flag_threshold                                  = Column(Float, default=0.70)
    is_flagged                                         = Column(Boolean, default=False)
    shap_values                                           = Column(JSONB, nullable=True)
    top_features                                             = Column(JSONB, nullable=True)
    prediction_timestamp                                        = Column(DateTime, server_default=func.now())

    claim = relationship("Claim", back_populates="verification")


class AuditLog(Base):
    __tablename__ = "audit_log"

    log_id              = Column(String(20), primary_key=True)
    claim_id               = Column(String(20), ForeignKey("claims.claim_id"), nullable=False)
    officer_id                = Column(String(20), ForeignKey("system_users.user_id"), nullable=False)
    action                       = Column(String(20), nullable=False)   # approved/rejected/escalated/overridden/viewed
    previous_status                 = Column(String(20), nullable=True)
    new_status                         = Column(String(20), nullable=True)
    officer_comments                      = Column(Text, nullable=False)
    shap_viewed                              = Column(Boolean, default=False)
    action_timestamp                            = Column(DateTime, server_default=func.now())

    claim = relationship("Claim", back_populates="audit_entries")
