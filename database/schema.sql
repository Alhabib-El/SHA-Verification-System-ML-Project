-- =====================================================================
-- SHA CLAIMS VERIFICATION SYSTEM — DATABASE SCHEMA
-- Design and Evaluation of a ML Pipeline for Health Insurance Claims
-- Verification in Kenya's Social Health Authority (SHA)
-- Student: Elvis Paul Sichemo | BIT/2019/42945 | Mount Kenya University
-- Database: PostgreSQL 15
-- =====================================================================

-- Clean slate (development only — remove in production)
DROP TABLE IF EXISTS audit_log CASCADE;
DROP TABLE IF EXISTS verification_results CASCADE;
DROP TABLE IF EXISTS claims CASCADE;
DROP TABLE IF EXISTS sha_tariffs CASCADE;
DROP TABLE IF EXISTS sha_members CASCADE;
DROP TABLE IF EXISTS health_providers CASCADE;
DROP TABLE IF EXISTS system_users CASCADE;

-- =====================================================================
-- 1. SYSTEM USERS — login accounts for all four roles
-- =====================================================================
CREATE TABLE system_users (
    user_id         VARCHAR(20)  PRIMARY KEY,
    full_name       VARCHAR(150) NOT NULL,
    email           VARCHAR(150) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(20)  NOT NULL CHECK (role IN ('provider','officer','admin','finance')),
    provider_id     VARCHAR(20),               -- only set if role = 'provider'
    is_active       BOOLEAN      DEFAULT TRUE,
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    last_login      TIMESTAMP
);

-- =====================================================================
-- 2. HEALTH PROVIDERS — empanelled facilities
-- =====================================================================
CREATE TABLE health_providers (
    provider_id         VARCHAR(20)  PRIMARY KEY,
    name                VARCHAR(150) NOT NULL,
    facility_type       VARCHAR(30)  NOT NULL CHECK (facility_type IN ('Hospital','Clinic','Pharmacy','Laboratory')),
    county              VARCHAR(50)  NOT NULL,
    accreditation_no    VARCHAR(50)  NOT NULL,
    regulatory_body     VARCHAR(10)  CHECK (regulatory_body IN ('KMPDC','NCK','PPB')),
    empanelment_date    DATE         NOT NULL,
    accreditation_expiry DATE,
    risk_tier           VARCHAR(10)  DEFAULT 'Normal' CHECK (risk_tier IN ('Low','Normal','High')),
    status              VARCHAR(15)  DEFAULT 'Active' CHECK (status IN ('Active','Suspended')),
    created_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_providers_county ON health_providers(county);
CREATE INDEX idx_providers_status ON health_providers(status);

-- =====================================================================
-- 3. SHA MEMBERS — registered patients/beneficiaries
-- =====================================================================
CREATE TABLE sha_members (
    patient_id          VARCHAR(20)  PRIMARY KEY,
    national_id         VARCHAR(20)  UNIQUE NOT NULL,
    sha_member_no        VARCHAR(30)  UNIQUE NOT NULL,
    full_name           VARCHAR(150) NOT NULL,   -- encrypted at application layer (AES-256)
    date_of_birth       DATE,
    registration_date   DATE         NOT NULL,
    coverage_package     VARCHAR(30)  DEFAULT 'SHIF Basic' CHECK (coverage_package IN ('SHIF Basic','SHIF Enhanced','Chronic/Critical')),
    eligibility_status   VARCHAR(15)  DEFAULT 'Active' CHECK (eligibility_status IN ('Active','Inactive','Expired')),
    eligibility_expiry   DATE,
    created_at           TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_members_sha_no ON sha_members(sha_member_no);
CREATE INDEX idx_members_eligibility ON sha_members(eligibility_status);

-- =====================================================================
-- 4. SHA TARIFFS — approved reimbursement reference table
-- =====================================================================
CREATE TABLE sha_tariffs (
    tariff_id        VARCHAR(20)  PRIMARY KEY,
    diagnosis_code   VARCHAR(10)  NOT NULL,     -- ICD-10 code
    procedure_code   VARCHAR(10)  NOT NULL,
    facility_tier    VARCHAR(10)  NOT NULL CHECK (facility_tier IN ('Level2','Level3','Level4','Level5','Level6')),
    approved_amount  DECIMAL(12,2) NOT NULL,
    effective_from   DATE         NOT NULL,
    effective_to     DATE,                       -- NULL = currently active
    UNIQUE(diagnosis_code, procedure_code, facility_tier, effective_from)
);

CREATE INDEX idx_tariffs_lookup ON sha_tariffs(diagnosis_code, procedure_code, facility_tier);

-- =====================================================================
-- 5. CLAIMS — central claim submission table
-- =====================================================================
CREATE TABLE claims (
    claim_id              VARCHAR(20)  PRIMARY KEY,
    patient_id            VARCHAR(20)  NOT NULL REFERENCES sha_members(patient_id),
    provider_id           VARCHAR(20)  NOT NULL REFERENCES health_providers(provider_id),
    submission_date       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    service_date          DATE         NOT NULL,
    diagnosis_code        VARCHAR(10)  NOT NULL,
    procedure_code        VARCHAR(10)  NOT NULL,
    claimed_amount         DECIMAL(12,2) NOT NULL CHECK (claimed_amount > 0),
    sha_tariff_amount      DECIMAL(12,2),
    amount_ratio           DECIMAL(8,4) GENERATED ALWAYS AS (
                               CASE WHEN sha_tariff_amount > 0
                               THEN claimed_amount / sha_tariff_amount
                               ELSE NULL END
                           ) STORED,
    submission_delay_days  INTEGER GENERATED ALWAYS AS (
                               EXTRACT(DAY FROM submission_date - service_date)::INTEGER
                           ) STORED,
    status                 VARCHAR(20)  DEFAULT 'submitted' CHECK (
                               status IN ('submitted','eligibility_failed','provider_failed',
                                          'verified','flagged','under_review',
                                          'approved','rejected','payment_queued','paid')
                           ),
    created_at             TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_claims_patient ON claims(patient_id);
CREATE INDEX idx_claims_provider ON claims(provider_id);
CREATE INDEX idx_claims_status ON claims(status);
CREATE INDEX idx_claims_submission_date ON claims(submission_date);

-- =====================================================================
-- 6. VERIFICATION RESULTS — XGBoost output + SHAP explanation
-- =====================================================================
CREATE TABLE verification_results (
    result_id             VARCHAR(20)  PRIMARY KEY,
    claim_id              VARCHAR(20)  UNIQUE NOT NULL REFERENCES claims(claim_id),
    eligibility_check      BOOLEAN      NOT NULL,
    provider_check         BOOLEAN      NOT NULL,
    coverage_check         BOOLEAN      NOT NULL,
    clinical_match         BOOLEAN,
    xgboost_score          FLOAT        CHECK (xgboost_score BETWEEN 0 AND 1),
    flag_threshold         FLOAT        DEFAULT 0.70,
    is_flagged             BOOLEAN      DEFAULT FALSE,
    shap_values            JSONB,                  -- full SHAP output
    top_features           JSONB,                  -- pre-computed top-5 ranked list
    model_version          VARCHAR(30),
    prediction_timestamp   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_verification_claim ON verification_results(claim_id);
CREATE INDEX idx_verification_flagged ON verification_results(is_flagged);
CREATE INDEX idx_verification_score ON verification_results(xgboost_score);

-- =====================================================================
-- 7. AUDIT LOG — append-only record of all decisions and actions
-- =====================================================================
CREATE TABLE audit_log (
    log_id              VARCHAR(20)  PRIMARY KEY,
    claim_id            VARCHAR(20)  REFERENCES claims(claim_id),
    officer_id          VARCHAR(20)  REFERENCES system_users(user_id),
    action              VARCHAR(20)  NOT NULL CHECK (action IN ('approved','rejected','escalated','overridden','crud_action')),
    previous_status     VARCHAR(20),
    new_status          VARCHAR(20),
    officer_comments    TEXT,
    shap_viewed         BOOLEAN      DEFAULT FALSE,
    action_timestamp    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_claim ON audit_log(claim_id);
CREATE INDEX idx_audit_officer ON audit_log(officer_id);
CREATE INDEX idx_audit_timestamp ON audit_log(action_timestamp);

-- Enforce append-only behaviour on audit_log (no UPDATE / DELETE)
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;

-- =====================================================================
-- SAMPLE SEED DATA (for development/testing only)
-- =====================================================================
INSERT INTO health_providers (provider_id, name, facility_type, county, accreditation_no, regulatory_body, empanelment_date, risk_tier, status) VALUES
('PRV-001', 'City Hospital', 'Hospital', 'Nairobi', 'KMPDC-2019-0042', 'KMPDC', '2019-04-12', 'Normal', 'Active'),
('PRV-002', 'Kisumu Hospital', 'Hospital', 'Kisumu', 'KMPDC-2018-0117', 'KMPDC', '2018-09-01', 'Normal', 'Active'),
('PRV-003', 'MedLab Plus', 'Laboratory', 'Mombasa', 'PPB-2024-0890', 'PPB', '2025-01-28', 'High', 'Suspended'),
('PRV-004', 'Nakuru Clinic', 'Clinic', 'Nakuru', 'NCK-2020-0033', 'NCK', '2020-06-15', 'Low', 'Active');

INSERT INTO sha_members (patient_id, national_id, sha_member_no, full_name, date_of_birth, registration_date, coverage_package, eligibility_status, eligibility_expiry) VALUES
('PAT-1001', '23456789', 'SHA-2024-00123', 'John Kamau', '1985-02-14', '2024-01-10', 'SHIF Basic', 'Active', '2026-12-31'),
('PAT-1002', '34567890', 'SHA-2024-00456', 'Mary Wanjiru', '1990-07-22', '2024-02-15', 'SHIF Enhanced', 'Active', '2026-12-31');

INSERT INTO sha_tariffs (tariff_id, diagnosis_code, procedure_code, facility_tier, approved_amount, effective_from) VALUES
('TRF-001', 'J18.0', '99233', 'Level4', 2330.00, '2024-01-01'),
('TRF-002', 'A09',   '99232', 'Level3', 1800.00, '2024-01-01'),
('TRF-003', 'K35',   '44950', 'Level5', 18000.00, '2024-01-01'),
('TRF-004', 'O80',   '59400', 'Level4', 12000.00, '2024-01-01'),
('TRF-005', 'E11',   '83036', 'Level3', 900.00, '2024-01-01');

-- =====================================================================
-- USEFUL VIEWS FOR REPORTING
-- =====================================================================
CREATE OR REPLACE VIEW v_daily_claims_summary AS
SELECT
    DATE(submission_date)                                   AS claim_date,
    COUNT(*)                                                 AS total_claims,
    COUNT(*) FILTER (WHERE status = 'approved')              AS approved,
    COUNT(*) FILTER (WHERE status = 'flagged')               AS flagged,
    COUNT(*) FILTER (WHERE status = 'rejected')              AS rejected,
    ROUND(AVG(claimed_amount), 2)                            AS avg_claimed_amount,
    SUM(claimed_amount) FILTER (WHERE status = 'approved')   AS total_approved_value
FROM claims
GROUP BY DATE(submission_date)
ORDER BY claim_date DESC;

CREATE OR REPLACE VIEW v_provider_risk_summary AS
SELECT
    p.provider_id,
    p.name,
    p.county,
    COUNT(c.claim_id)                                        AS total_claims,
    COUNT(c.claim_id) FILTER (WHERE vr.is_flagged)            AS flagged_claims,
    ROUND(AVG(vr.xgboost_score)::NUMERIC, 3)                  AS avg_xgboost_score,
    ROUND(AVG(c.amount_ratio)::NUMERIC, 2)                    AS avg_amount_ratio
FROM health_providers p
LEFT JOIN claims c ON c.provider_id = p.provider_id
LEFT JOIN verification_results vr ON vr.claim_id = c.claim_id
GROUP BY p.provider_id, p.name, p.county
ORDER BY flagged_claims DESC;
