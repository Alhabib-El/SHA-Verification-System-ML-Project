-- ============================================================================
-- SHA CLAIMS VERIFICATION SYSTEM — FIXED DATABASE SCHEMA
-- Elvis Paul Sichemo | BIT/2019/42945 | Mount Kenya University
-- PostgreSQL 15
--
-- FIXES APPLIED:
--   - All status/risk values now lowercase (active/suspended/low/medium/high)
--     to match pipeline.py and models.py
--   - last_login renamed to last_login_at to match models.py
--   - billing_compliant column added to verification_results
--   - audit_log action CHECK expanded to include 'viewed' and 'suspended'
--   - facility_tier uses spaces: 'Level 2'..'Level 6' to match models.py
--   - coverage_package values match models.py CHECK values
-- ============================================================================

DROP TABLE IF EXISTS audit_log CASCADE;
DROP TABLE IF EXISTS verification_results CASCADE;
DROP TABLE IF EXISTS claims CASCADE;
DROP TABLE IF EXISTS sha_tariffs CASCADE;
DROP TABLE IF EXISTS sha_members CASCADE;
DROP TABLE IF EXISTS health_providers CASCADE;
DROP TABLE IF EXISTS system_users CASCADE;

-- ── 1. system_users ───────────────────────────────────────────────────────────
CREATE TABLE system_users (
    user_id         VARCHAR(20)  PRIMARY KEY,
    full_name       VARCHAR(150) NOT NULL,
    email           VARCHAR(150) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(20)  NOT NULL
                     CHECK (role IN ('provider','officer','admin','finance')),
    provider_id     VARCHAR(20),
    is_active       BOOLEAN      DEFAULT TRUE,
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    last_login_at   TIMESTAMP                              -- FIXED: was last_login
);

-- ── 2. health_providers ───────────────────────────────────────────────────────
CREATE TABLE health_providers (
    provider_id          VARCHAR(20)  PRIMARY KEY,
    name                 VARCHAR(150) NOT NULL,
    facility_type        VARCHAR(30)  NOT NULL
                          CHECK (facility_type IN ('Hospital','Clinic','Pharmacy','Laboratory')),
    facility_tier        VARCHAR(10)  NOT NULL
                          CHECK (facility_tier IN ('Level 2','Level 3','Level 4','Level 5','Level 6')),
    county               VARCHAR(50)  NOT NULL,
    accreditation_no     VARCHAR(50)  NOT NULL UNIQUE,
    regulatory_body      VARCHAR(10)
                          CHECK (regulatory_body IN ('KMPDC','NCK','PPB')),
    empanelment_date     DATE         NOT NULL,
    accreditation_expiry DATE,
    risk_tier            VARCHAR(10)  DEFAULT 'low'        -- FIXED: lowercase
                          CHECK (risk_tier IN ('low','medium','high')),
    status               VARCHAR(15)  DEFAULT 'active'     -- FIXED: lowercase
                          CHECK (status IN ('active','suspended','revoked')),
    bank_account_no      VARCHAR(30),
    created_at           TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_providers_county ON health_providers(county);
CREATE INDEX idx_providers_status ON health_providers(status);

-- ── 3. sha_members ────────────────────────────────────────────────────────────
CREATE TABLE sha_members (
    patient_id          VARCHAR(20)  PRIMARY KEY,
    national_id         VARCHAR(20)  UNIQUE NOT NULL,
    sha_member_no       VARCHAR(30)  UNIQUE NOT NULL,
    full_name           VARCHAR(150) NOT NULL,
    date_of_birth       DATE,
    gender              VARCHAR(10)  CHECK (gender IN ('Male','Female')),
    county              VARCHAR(50),
    registration_date   DATE         NOT NULL,
    coverage_package    VARCHAR(30)  DEFAULT 'SHIF Basic'
                         CHECK (coverage_package IN               -- FIXED: match models.py
                         ('SHIF Basic','Primary Healthcare Fund','Chronic Illness Fund')),
    eligibility_status  VARCHAR(15)  DEFAULT 'active'             -- FIXED: lowercase
                         CHECK (eligibility_status IN ('active','inactive','suspended')),
    eligibility_expiry  DATE,
    created_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_members_sha_no      ON sha_members(sha_member_no);
CREATE INDEX idx_members_eligibility ON sha_members(eligibility_status);

-- ── 4. sha_tariffs ────────────────────────────────────────────────────────────
CREATE TABLE sha_tariffs (
    tariff_id        VARCHAR(20)   PRIMARY KEY,
    diagnosis_code   VARCHAR(10)   NOT NULL,
    procedure_code   VARCHAR(10)   NOT NULL,
    facility_tier    VARCHAR(10)   NOT NULL
                      CHECK (facility_tier IN ('Level 2','Level 3','Level 4','Level 5','Level 6')),
    approved_amount  DECIMAL(12,2) NOT NULL,
    effective_from   DATE          NOT NULL,
    effective_to     DATE,
    UNIQUE (diagnosis_code, procedure_code, facility_tier, effective_from)
);

CREATE INDEX idx_tariffs_lookup ON sha_tariffs(diagnosis_code, procedure_code, facility_tier);

-- ── 5. claims ─────────────────────────────────────────────────────────────────
CREATE TABLE claims (
    claim_id              VARCHAR(20)   PRIMARY KEY,
    patient_id            VARCHAR(20)   NOT NULL REFERENCES sha_members(patient_id),
    provider_id           VARCHAR(20)   NOT NULL REFERENCES health_providers(provider_id),
    submission_date       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    service_date          DATE          NOT NULL,
    diagnosis_code        VARCHAR(10)   NOT NULL,
    procedure_code        VARCHAR(10)   NOT NULL,
    claimed_amount        DECIMAL(12,2) NOT NULL CHECK (claimed_amount > 0),
    sha_tariff_amount     DECIMAL(12,2),
    amount_ratio          DECIMAL(8,4)  GENERATED ALWAYS AS (
                              CASE WHEN sha_tariff_amount > 0
                              THEN claimed_amount / sha_tariff_amount
                              ELSE NULL END
                          ) STORED,
    submission_delay_days INTEGER       GENERATED ALWAYS AS (
                              EXTRACT(DAY FROM submission_date - service_date)::INTEGER
                          ) STORED,
    status                VARCHAR(20)   DEFAULT 'submitted'
                           CHECK (status IN (
                               'submitted','rejected_precheck','verified',
                               'flagged','under_review','approved',
                               'rejected','payment_queued','paid'
                           )),
    created_at            TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_claims_patient    ON claims(patient_id);
CREATE INDEX idx_claims_provider   ON claims(provider_id);
CREATE INDEX idx_claims_status     ON claims(status);
CREATE INDEX idx_claims_submission ON claims(submission_date);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION trg_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER claims_updated_at
    BEFORE UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ── 6. verification_results ───────────────────────────────────────────────────
CREATE TABLE verification_results (
    result_id             VARCHAR(20)  PRIMARY KEY,
    claim_id              VARCHAR(20)  UNIQUE NOT NULL REFERENCES claims(claim_id),
    eligibility_check     BOOLEAN      NOT NULL,
    provider_check        BOOLEAN      NOT NULL,
    coverage_check        BOOLEAN      NOT NULL,
    clinical_match        BOOLEAN,
    billing_compliant     BOOLEAN,                     -- FIXED: was missing
    xgboost_score         FLOAT        CHECK (xgboost_score BETWEEN 0 AND 1),
    flag_threshold        FLOAT        DEFAULT 0.70,
    is_flagged            BOOLEAN      DEFAULT FALSE,
    shap_values           JSONB,
    top_features          JSONB,
    model_version         VARCHAR(30),
    prediction_timestamp  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_verif_claim   ON verification_results(claim_id);
CREATE INDEX idx_verif_flagged ON verification_results(is_flagged);
CREATE INDEX idx_verif_score   ON verification_results(xgboost_score);

-- ── 7. audit_log ──────────────────────────────────────────────────────────────
CREATE TABLE audit_log (
    log_id           VARCHAR(20)  PRIMARY KEY,
    claim_id         VARCHAR(20)  REFERENCES claims(claim_id),
    officer_id       VARCHAR(20)  REFERENCES system_users(user_id),
    action           VARCHAR(20)  NOT NULL
                      CHECK (action IN (                 -- FIXED: added 'viewed','suspended'
                          'approved','rejected','escalated',
                          'overridden','viewed','suspended','crud_action'
                      )),
    previous_status  VARCHAR(20),
    new_status       VARCHAR(20),
    officer_comments TEXT,
    shap_viewed      BOOLEAN      DEFAULT FALSE,
    action_timestamp TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_claim     ON audit_log(claim_id);
CREATE INDEX idx_audit_officer   ON audit_log(officer_id);
CREATE INDEX idx_audit_timestamp ON audit_log(action_timestamp);

-- Append-only: no UPDATE or DELETE on audit_log
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;

-- ── VIEWS ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_flagged_claims_queue AS
SELECT
    c.claim_id, c.submission_date,
    p.name AS provider_name, p.county,
    c.diagnosis_code, c.claimed_amount, c.amount_ratio,
    vr.xgboost_score, vr.is_flagged, vr.top_features, c.status
FROM claims c
JOIN health_providers p      ON c.provider_id = p.provider_id
JOIN verification_results vr ON c.claim_id = vr.claim_id
WHERE vr.is_flagged = TRUE
  AND c.status IN ('flagged','under_review')
ORDER BY vr.xgboost_score DESC;

CREATE OR REPLACE VIEW v_daily_claims_summary AS
SELECT
    DATE(submission_date)                                    AS claim_date,
    COUNT(*)                                                  AS total_claims,
    COUNT(*) FILTER (WHERE status = 'approved')               AS approved_count,
    COUNT(*) FILTER (WHERE status IN ('flagged','under_review')) AS flagged_count,
    COUNT(*) FILTER (WHERE status = 'rejected')               AS rejected_count,
    ROUND(AVG(claimed_amount), 2)                             AS avg_claimed_amount,
    SUM(claimed_amount) FILTER (WHERE status = 'approved')    AS total_approved_value
FROM claims
GROUP BY DATE(submission_date)
ORDER BY claim_date DESC;

CREATE OR REPLACE VIEW v_provider_risk_summary AS
SELECT
    p.provider_id, p.name, p.county, p.status, p.risk_tier,
    COUNT(c.claim_id)                                AS total_claims,
    COUNT(c.claim_id) FILTER (WHERE vr.is_flagged)   AS flagged_claims,
    ROUND(AVG(vr.xgboost_score)::NUMERIC, 3)         AS avg_xgboost_score,
    ROUND(AVG(c.amount_ratio)::NUMERIC, 2)            AS avg_amount_ratio
FROM health_providers p
LEFT JOIN claims c ON c.provider_id = p.provider_id
LEFT JOIN verification_results vr ON vr.claim_id = c.claim_id
GROUP BY p.provider_id, p.name, p.county, p.status, p.risk_tier
ORDER BY flagged_claims DESC;

-- ── SEED DATA ─────────────────────────────────────────────────────────────────
INSERT INTO health_providers
(provider_id,name,facility_type,facility_tier,county,accreditation_no,
 regulatory_body,empanelment_date,accreditation_expiry,status,risk_tier) VALUES
('PRV-001','City Hospital','Hospital','Level 5','Nairobi','KMPDC-2019-0042','KMPDC','2019-04-12','2026-04-12','active','low'),
('PRV-002','Kisumu Hospital','Hospital','Level 4','Kisumu','KMPDC-2018-0117','KMPDC','2018-09-01','2026-09-01','active','low'),
('PRV-003','MedLab Plus','Laboratory','Level 3','Mombasa','PPB-2024-0890','PPB','2025-01-28','2026-01-28','suspended','high'),
('PRV-004','Nakuru Clinic','Clinic','Level 2','Nakuru','NCK-2020-0033','NCK','2020-06-15','2026-06-15','active','medium');

INSERT INTO sha_members
(patient_id,national_id,sha_member_no,full_name,date_of_birth,
 registration_date,coverage_package,eligibility_status,eligibility_expiry) VALUES
('PAT-1001','23456789','SHA-2024-00123','John Kamau','1985-02-14','2024-01-10','SHIF Basic','active','2026-12-31'),
('PAT-1002','34567890','SHA-2024-00456','Mary Wanjiru','1990-07-22','2024-02-15','SHIF Basic','active','2026-12-31');

INSERT INTO sha_tariffs
(tariff_id,diagnosis_code,procedure_code,facility_tier,approved_amount,effective_from) VALUES
('TRF-001','J18.0','99233','Level 5',2330.00,'2024-01-01'),
('TRF-002','J18.0','99232','Level 4',2330.00,'2024-01-01'),
('TRF-003','A09','99232','Level 3',1800.00,'2024-01-01'),
('TRF-004','K35','44950','Level 4',18000.00,'2024-01-01'),
('TRF-005','O80','59400','Level 4',12000.00,'2024-01-01'),
('TRF-006','E11','83036','Level 2',900.00,'2024-01-01');
