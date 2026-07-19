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
-- Every human (or automated) account that can log into the system: SHA
-- officers, admins, finance staff, and one login per health provider
-- (role='provider', linked via provider_id) for submitting claims.
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
-- The hospitals/clinics/pharmacies/labs accredited to bill SHA. risk_tier
-- and status are what the auto-suspension logic (ml_pipeline/auto_suspend.py)
-- writes to when a provider crosses the flagged-claims threshold.
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
-- The insured patients (SHA beneficiaries) that claims are filed on behalf
-- of. eligibility_status/eligibility_expiry are checked in Stage 1 of the
-- verification pipeline before a claim is allowed to proceed to ML scoring.
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
-- The official approved-amount schedule: for a given diagnosis + procedure
-- + facility tier, this is the maximum SHA will reimburse. This table is
-- the single source of truth for two separate checks — "is this a valid
-- diagnosis/procedure pairing at all" (check_clinical_match) and "is the
-- claimed amount reasonable" (amount_ratio, billing_compliant).
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
-- The central table: one row per insurance claim filed by a provider.
-- amount_ratio and submission_delay_days are GENERATED columns — Postgres
-- computes and stores them automatically from the other columns on every
-- insert/update, so the application code never has to keep them in sync
-- by hand, and they're always consistent even if a claim is edited directly.
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
    -- claimed / approved — the single strongest fraud signal the model
    -- uses (fed into the ML feature vector as "amount_ratio").
    amount_ratio          DECIMAL(8,4)  GENERATED ALWAYS AS (
                              CASE WHEN sha_tariff_amount > 0
                              THEN claimed_amount / sha_tariff_amount
                              ELSE NULL END
                          ) STORED,
    -- Days between the service being rendered and the claim being filed —
    -- an unusually long delay is itself a feature the model looks at.
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
-- One row per claim, written by ClaimsVerificationPipeline._persist_result
-- once it's been through all 6 pipeline stages: the four rule-based
-- pre-check booleans, the model's xgboost_score, and shap_values/
-- top_features (the SHAP explanation) so an officer can see WHY a claim
-- was flagged, not just that it was.
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
-- FR-09: an append-only trail of every decision made on a claim or provider
-- — officer approvals/rejections, escalations, automated suspensions, even
-- just viewing a claim's SHAP breakdown. REVOKE below enforces "append-only"
-- at the database level, not just by convention in application code, so
-- history genuinely cannot be rewritten even by a compromised app server.
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
-- Pre-joined, reusable read models so the API routers issue one simple
-- SELECT instead of repeating the same multi-table JOIN logic everywhere.

-- All currently-flagged, still-pending claims, worst score first — this is
-- the original "flagged only" queue definition (superseded in the live app
-- by review.py's broader query, kept here as it still documents the
-- pattern and is used by earlier report logic).
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

-- One row per calendar day: claim counts by outcome plus average model
-- score/amount-ratio for that day. Powers both the Officer Dashboard
-- metric cards and the PDF verification summary report.
CREATE OR REPLACE VIEW v_daily_claims_summary AS
SELECT
    DATE(c.submission_date)                                    AS claim_date,
    COUNT(*)                                                    AS total_claims,
    COUNT(*) FILTER (WHERE c.status = 'approved')               AS approved_count,
    COUNT(*) FILTER (WHERE c.status IN ('flagged','under_review')) AS flagged_count,
    COUNT(*) FILTER (WHERE c.status = 'rejected')               AS rejected_count,
    ROUND(AVG(c.claimed_amount), 2)                             AS avg_claimed_amount,
    SUM(c.claimed_amount) FILTER (WHERE c.status = 'approved')  AS total_approved_value,
    ROUND(AVG(vr.xgboost_score)::numeric, 3)                    AS avg_xgboost_score,
    ROUND(AVG(c.amount_ratio)::numeric, 2)                      AS avg_amount_ratio
FROM claims c
LEFT JOIN verification_results vr ON vr.claim_id = c.claim_id
GROUP BY DATE(c.submission_date)
ORDER BY claim_date DESC;

-- Per-provider rollup of total vs flagged claims and average risk score —
-- the data source for the Admin CRUD screen's provider risk column and
-- the Reports screen's "Provider flag rate" chart.
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
('PRV-004','Nakuru Clinic','Clinic','Level 2','Nakuru','NCK-2020-0033','NCK','2020-06-15','2026-06-15','active','medium'),
-- 10 private hospitals
('PRV-005','Aga Khan University Hospital, Nairobi','Hospital','Level 6','Nairobi','KMPDC-2016-1001','KMPDC','2016-03-01','2027-03-01','active','low'),
('PRV-006','The Nairobi Hospital','Hospital','Level 6','Nairobi','KMPDC-2015-1002','KMPDC','2015-06-10','2027-06-10','active','low'),
('PRV-007','MP Shah Hospital','Hospital','Level 5','Nairobi','KMPDC-2017-1003','KMPDC','2017-01-15','2026-01-15','active','low'),
('PRV-008','Getrude''s Children''s Hospital','Hospital','Level 5','Nairobi','KMPDC-2018-1004','KMPDC','2018-09-20','2026-09-20','active','low'),
('PRV-009','Mater Misericordiae Hospital','Hospital','Level 5','Nairobi','KMPDC-2016-1005','KMPDC','2016-11-05','2026-11-05','active','low'),
('PRV-010','Aga Khan Hospital, Kisumu','Hospital','Level 5','Kisumu','KMPDC-2019-1006','KMPDC','2019-04-18','2027-04-18','active','low'),
('PRV-011','AIC Kijabe Hospital','Hospital','Level 5','Kiambu','KMPDC-2015-1007','KMPDC','2015-02-14','2026-02-14','active','low'),
('PRV-012','Tenwek Hospital','Hospital','Level 5','Bomet','KMPDC-2017-1008','KMPDC','2017-07-22','2026-07-22','active','low'),
('PRV-013','Mombasa Hospital','Hospital','Level 4','Mombasa','KMPDC-2020-1009','KMPDC','2020-01-30','2027-01-30','active','low'),
('PRV-014','Avenue Hospital, Nakuru','Hospital','Level 4','Nakuru','KMPDC-2021-1010','KMPDC','2021-05-12','2027-05-12','active','low'),
-- 20 public hospitals
('PRV-015','Kenyatta National Hospital','Hospital','Level 6','Nairobi','KMPDC-2005-2001','KMPDC','2005-01-01','2027-01-01','active','low'),
('PRV-016','Moi Teaching and Referral Hospital','Hospital','Level 6','Uasin Gishu','KMPDC-2006-2002','KMPDC','2006-01-01','2027-01-01','active','low'),
('PRV-017','Coast General Teaching and Referral Hospital','Hospital','Level 5','Mombasa','KMPDC-2010-2003','KMPDC','2010-03-15','2026-03-15','active','low'),
('PRV-018','Nakuru Level 5 Hospital','Hospital','Level 5','Nakuru','KMPDC-2011-2004','KMPDC','2011-06-01','2026-06-01','active','low'),
('PRV-019','Jaramogi Oginga Odinga Teaching and Referral Hospital','Hospital','Level 6','Kisumu','KMPDC-2009-2005','KMPDC','2009-08-11','2027-08-11','active','low'),
('PRV-020','Nyeri County Referral Hospital','Hospital','Level 5','Nyeri','KMPDC-2012-2006','KMPDC','2012-02-20','2026-02-20','active','low'),
('PRV-021','Machakos Level 5 Hospital','Hospital','Level 5','Machakos','KMPDC-2013-2007','KMPDC','2013-09-09','2026-09-09','active','low'),
('PRV-022','Kakamega County General Hospital','Hospital','Level 5','Kakamega','KMPDC-2014-2008','KMPDC','2014-04-04','2026-04-04','active','low'),
('PRV-023','Meru Teaching and Referral Hospital','Hospital','Level 5','Meru','KMPDC-2013-2009','KMPDC','2013-12-01','2026-12-01','active','low'),
('PRV-024','Embu Level 5 Hospital','Hospital','Level 5','Embu','KMPDC-2015-2010','KMPDC','2015-05-05','2026-05-05','active','low'),
('PRV-025','Garissa County Referral Hospital','Hospital','Level 4','Garissa','KMPDC-2016-2011','KMPDC','2016-07-07','2026-07-07','active','low'),
('PRV-026','Kisii Teaching and Referral Hospital','Hospital','Level 5','Kisii','KMPDC-2012-2012','KMPDC','2012-10-10','2026-10-10','active','low'),
('PRV-027','Bungoma County Referral Hospital','Hospital','Level 4','Bungoma','KMPDC-2017-2013','KMPDC','2017-03-03','2026-03-03','active','low'),
('PRV-028','Kericho County Referral Hospital','Hospital','Level 4','Kericho','KMPDC-2018-2014','KMPDC','2018-06-16','2026-06-16','active','low'),
('PRV-029','Kitui County Referral Hospital','Hospital','Level 4','Kitui','KMPDC-2019-2015','KMPDC','2019-02-25','2026-02-25','active','low'),
('PRV-030','Kajiado County Referral Hospital','Hospital','Level 4','Kajiado','KMPDC-2020-2016','KMPDC','2020-08-08','2026-08-08','active','low'),
('PRV-031','Kilifi County Hospital','Hospital','Level 4','Kilifi','KMPDC-2016-2017','KMPDC','2016-11-11','2026-11-11','active','low'),
('PRV-032','Kiambu Level 5 Hospital','Hospital','Level 5','Kiambu','KMPDC-2014-2018','KMPDC','2014-01-20','2026-01-20','active','low'),
('PRV-033','Homa Bay County Teaching and Referral Hospital','Hospital','Level 5','Homa Bay','KMPDC-2015-2019','KMPDC','2015-09-09','2026-09-09','active','low'),
('PRV-034','Busia County Referral Hospital','Hospital','Level 4','Busia','KMPDC-2017-2020','KMPDC','2017-10-10','2026-10-10','active','low');

INSERT INTO sha_members
(patient_id,national_id,sha_member_no,full_name,date_of_birth,gender,
 county,registration_date,coverage_package,eligibility_status,eligibility_expiry) VALUES
('PAT-1001','23456789','SHA-2024-00123','John Kamau','1985-02-14',NULL,NULL,'2024-01-10','SHIF Basic','active','2026-12-31'),
('PAT-1002','34567890','SHA-2024-00456','Mary Wanjiru','1990-07-22',NULL,NULL,'2024-02-15','SHIF Basic','active','2026-12-31'),
('PAT-1003','28217207','SHA-2025-01001','James Njeri','1969-03-24','Male','Nakuru','2024-10-14','SHIF Basic','active','2026-12-31'),
('PAT-1004','36956909','SHA-2025-01002','Samuel Wekesa','1993-01-18','Male','Machakos','2025-04-15','Chronic Illness Fund','active','2026-12-31'),
('PAT-1005','31416912','SHA-2025-01003','David Rotich','1972-03-07','Male','Turkana','2025-02-03','Primary Healthcare Fund','active','2026-12-31'),
('PAT-1006','21457954','SHA-2025-01004','Elizabeth Kariuki','2001-08-18','Female','Nakuru','2025-02-18','SHIF Basic','inactive','2025-06-30'),
('PAT-1007','22333883','SHA-2025-01005','Sharon Hassan','1957-11-08','Female','Turkana','2025-02-28','SHIF Basic','active','2026-12-31'),
('PAT-1008','32241736','SHA-2025-01006','Lucy Were','1965-06-12','Female','Machakos','2025-12-22','SHIF Basic','inactive','2025-06-30'),
('PAT-1009','25482877','SHA-2025-01007','Moses Ouma','1984-07-09','Male','Bomet','2024-11-11','SHIF Basic','active','2026-12-31'),
('PAT-1010','28983893','SHA-2025-01008','Francis Mbithi','1959-04-19','Male','Nyandarua','2025-04-21','Primary Healthcare Fund','active','2026-12-31'),
('PAT-1011','24685216','SHA-2025-01009','Ann Kariuki','1970-12-18','Female','Homa Bay','2025-12-19','Primary Healthcare Fund','inactive','2025-06-30'),
('PAT-1012','24641643','SHA-2025-01010','Elizabeth Wekesa','1987-08-03','Female','Turkana','2024-02-05','SHIF Basic','active','2026-12-31'),
('PAT-1013','39994697','SHA-2025-01011','Anthony Kilonzo','1984-09-09','Male','Homa Bay','2024-11-24','SHIF Basic','active','2026-12-31'),
('PAT-1014','29848230','SHA-2025-01012','Catherine Nekesa','1982-03-15','Female','Nairobi','2025-09-25','SHIF Basic','active','2026-12-31'),
('PAT-1015','26674349','SHA-2025-01013','Stephen Wairimu','1964-06-25','Male','Kiambu','2024-10-11','Primary Healthcare Fund','active','2026-12-31'),
('PAT-1016','28034686','SHA-2025-01014','Charles Kones','1958-04-19','Male','Kisumu','2024-12-16','SHIF Basic','active','2026-12-31'),
('PAT-1017','38448347','SHA-2025-01015','Michael Mwende','1965-05-17','Male','Nyeri','2025-04-18','SHIF Basic','active','2026-12-31'),
('PAT-1018','37366963','SHA-2025-01016','Elizabeth Simiyu','1983-02-08','Female','Kakamega','2024-06-01','Chronic Illness Fund','active','2026-12-31'),
('PAT-1019','20241284','SHA-2025-01017','Amos Wekesa','1959-12-21','Male','Mombasa','2024-02-02','SHIF Basic','active','2026-12-31'),
('PAT-1020','27188594','SHA-2025-01018','Paul Kioko','1989-03-24','Male','Busia','2025-04-26','Primary Healthcare Fund','active','2026-12-31'),
('PAT-1021','34463677','SHA-2025-01019','Daniel Wafula','1977-07-14','Male','Kitui','2024-11-21','SHIF Basic','active','2026-12-31'),
('PAT-1022','28343522','SHA-2025-01020','Catherine Wafula','1967-04-18','Female','Kitui','2024-07-06','SHIF Basic','active','2026-12-31'),
('PAT-1023','38465853','SHA-2025-01021','John Simiyu','1961-01-21','Male','Homa Bay','2024-02-25','SHIF Basic','active','2026-12-31'),
('PAT-1024','27172169','SHA-2025-01022','Beatrice Mwende','1980-01-06','Female','Bungoma','2024-07-09','Primary Healthcare Fund','active','2026-12-31'),
('PAT-1025','25194119','SHA-2025-01023','Judith Kioko','1967-05-07','Female','Mombasa','2024-12-11','SHIF Basic','active','2026-12-31'),
('PAT-1026','25282565','SHA-2025-01024','Agnes Gitau','1958-09-03','Female','Kiambu','2024-10-03','SHIF Basic','active','2026-12-31'),
('PAT-1027','39425300','SHA-2025-01025','Amos Ouma','1993-01-20','Male','Kisumu','2025-11-19','Chronic Illness Fund','active','2026-12-31'),
('PAT-1028','30542261','SHA-2025-01026','Lucy Abdi','1970-05-13','Female','Uasin Gishu','2025-08-11','SHIF Basic','active','2026-12-31'),
('PAT-1029','22458221','SHA-2025-01027','Caroline Wafula','1989-04-17','Female','Meru','2024-06-03','SHIF Basic','active','2026-12-31'),
('PAT-1030','38227370','SHA-2025-01028','Alice Simiyu','2000-05-20','Female','Marsabit','2024-11-27','Primary Healthcare Fund','active','2026-12-31'),
('PAT-1031','23873143','SHA-2025-01029','Michael Kariuki','1961-12-18','Male','Uasin Gishu','2025-05-20','SHIF Basic','active','2026-12-31'),
('PAT-1032','36392408','SHA-2025-01030','Paul Wairimu','1971-01-03','Male','Bomet','2025-05-02','SHIF Basic','active','2026-12-31'),
('PAT-1033','34825539','SHA-2025-01031','Paul Mutua','1990-12-14','Male','Homa Bay','2024-02-03','SHIF Basic','active','2026-12-31'),
('PAT-1034','24969202','SHA-2025-01032','Charles Adhiambo','1982-03-02','Male','Embu','2025-01-12','SHIF Basic','active','2026-12-31'),
('PAT-1035','33635762','SHA-2025-01033','Charles Adhiambo','1994-12-05','Male','Kakamega','2024-03-14','SHIF Basic','active','2026-12-31'),
('PAT-1036','28952514','SHA-2025-01034','Susan Ouma','1965-12-04','Female','Bungoma','2024-08-08','SHIF Basic','active','2026-12-31'),
('PAT-1037','27480073','SHA-2025-01035','Nancy Wekesa','1956-11-07','Female','Bungoma','2025-05-28','SHIF Basic','active','2026-12-31'),
('PAT-1038','37992414','SHA-2025-01036','Agnes Mbithi','1976-01-04','Female','Meru','2024-10-09','SHIF Basic','active','2026-12-31'),
('PAT-1039','34642948','SHA-2025-01037','Elizabeth Barasa','1993-09-04','Female','Bungoma','2024-05-02','Primary Healthcare Fund','active','2026-12-31'),
('PAT-1040','22347931','SHA-2025-01038','Charles Rotich','1997-06-20','Male','Garissa','2024-12-10','Primary Healthcare Fund','active','2026-12-31'),
('PAT-1041','29920542','SHA-2025-01039','Catherine Mbithi','1990-03-07','Female','Kericho','2025-11-24','SHIF Basic','inactive','2025-06-30'),
('PAT-1042','20013621','SHA-2025-01040','Winnie Adhiambo','1974-05-07','Female','Kericho','2025-08-15','Primary Healthcare Fund','active','2026-12-31');

-- Diagnosis/procedure tariffs sourced from References/icd-10-medical-diagnosis-codes.pdf,
-- selected for relevance to Kenya's disease burden (malaria, TB, HIV, typhoid,
-- cholera, NTDs, maternal/neonatal care, NCDs, trauma).
INSERT INTO sha_tariffs
(tariff_id,diagnosis_code,procedure_code,facility_tier,approved_amount,effective_from) VALUES
('TRF-001','A00.0'   ,'87045','Level 3',1500.00,'2026-07-14'),  -- Cholera due to Vibrio cholerae 01, biovar cholerae
('TRF-002','A01.00'  ,'87040','Level 3',1800.00,'2026-07-14'),  -- Typhoid fever, unspecified
('TRF-003','A09'     ,'99213','Level 2', 700.00,'2026-07-14'),  -- Infectious gastroenteritis and colitis, unspecified
('TRF-004','A15.0'   ,'87116','Level 3',1200.00,'2026-07-14'),  -- Tuberculosis of lung
('TRF-005','A23.0'   ,'86622','Level 3',1600.00,'2026-07-14'),  -- Brucellosis due to Brucella melitensis
('TRF-006','A37.00'  ,'99213','Level 2', 800.00,'2026-07-14'),  -- Whooping cough due to Bordetella pertussis without pneumonia
('TRF-007','A53.9'   ,'86592','Level 2', 500.00,'2026-07-14'),  -- Syphilis, unspecified
('TRF-008','A54.00'  ,'87591','Level 3',1200.00,'2026-07-14'),  -- Gonococcal infection of lower genitourinary tract, unspecified
('TRF-009','A90'     ,'86790','Level 3',1500.00,'2026-07-14'),  -- Dengue fever [classical dengue]
('TRF-010','B05.0'   ,'99223','Level 5',15000.00,'2026-07-14'), -- Measles complicated by encephalitis
('TRF-011','B20'     ,'87536','Level 4',3500.00,'2026-07-14'),  -- Human immunodeficiency virus [HIV] disease
('TRF-012','B54'     ,'87207','Level 2', 600.00,'2026-07-14'),  -- Unspecified malaria
('TRF-013','B65.0'   ,'81015','Level 2', 500.00,'2026-07-14'),  -- Schistosomiasis due to Schistosoma haematobium
('TRF-014','B76.0'   ,'87177','Level 2', 400.00,'2026-07-14'),  -- Ancylostomiasis (hookworm)
('TRF-015','B77.0'   ,'99213','Level 2', 400.00,'2026-07-14'),  -- Ascariasis with intestinal complications
('TRF-016','B79'     ,'87177','Level 2', 400.00,'2026-07-14'),  -- Trichuriasis
('TRF-017','D57.00'  ,'36430','Level 5',20000.00,'2026-07-14'), -- Hb-SS disease with crisis, unspecified
('TRF-018','D64.9'   ,'85025','Level 2', 600.00,'2026-07-14'),  -- Anemia, unspecified
('TRF-019','E10.9'   ,'83036','Level 3', 900.00,'2026-07-14'),  -- Type 1 diabetes mellitus without complications
('TRF-020','E11.9'   ,'83036','Level 2', 900.00,'2026-07-14'),  -- Type 2 diabetes mellitus without complications
('TRF-021','E40'     ,'99223','Level 4',8000.00,'2026-07-14'),  -- Kwashiorkor
('TRF-022','E43'     ,'99223','Level 4',8000.00,'2026-07-14'),  -- Unspecified severe protein-calorie malnutrition
('TRF-023','E78.5'   ,'80061','Level 3', 800.00,'2026-07-14'),  -- Hyperlipidemia, unspecified
('TRF-024','F32.9'   ,'90791','Level 4',3000.00,'2026-07-14'),  -- Major depressive disorder, single episode, unspecified
('TRF-025','F41.1'   ,'90791','Level 4',3000.00,'2026-07-14'),  -- Generalized anxiety disorder
('TRF-026','G00.1'   ,'62270','Level 5',25000.00,'2026-07-14'), -- Pneumococcal meningitis
('TRF-027','G40.909' ,'95816','Level 4',4500.00,'2026-07-14'),  -- Epilepsy, unspecified, not intractable, without status epilepticus
('TRF-028','G43.909' ,'99213','Level 3', 700.00,'2026-07-14'),  -- Migraine, unspecified, not intractable, without status migrainosus
('TRF-029','H66.90'  ,'92700','Level 2', 500.00,'2026-07-14'),  -- Otitis media, unspecified, unspecified ear
('TRF-030','I10'     ,'99213','Level 2', 600.00,'2026-07-14'),  -- Essential (primary) hypertension
('TRF-031','I21.4'   ,'92941','Level 6',350000.00,'2026-07-14'),-- Non-ST elevation (NSTEMI) myocardial infarction
('TRF-032','I50.20'  ,'93306','Level 5',6000.00,'2026-07-14'),  -- Unspecified systolic (congestive) heart failure
('TRF-033','I63.9'   ,'70450','Level 5',8000.00,'2026-07-14'),  -- Cerebral infarction, unspecified
('TRF-034','J02.9'   ,'99213','Level 2', 500.00,'2026-07-14'),  -- Acute pharyngitis, unspecified
('TRF-035','J18.9'   ,'71046','Level 3',2330.00,'2026-07-14'),  -- Pneumonia, unspecified organism
('TRF-036','J20.9'   ,'99213','Level 2', 700.00,'2026-07-14'),  -- Acute bronchitis, unspecified
('TRF-037','J44.9'   ,'94010','Level 3',1200.00,'2026-07-14'),  -- Chronic obstructive pulmonary disease, unspecified
('TRF-038','J45.909' ,'94640','Level 2', 800.00,'2026-07-14'),  -- Unspecified asthma, uncomplicated
('TRF-039','K02.9'   ,'D2391','Level 2',1500.00,'2026-07-14'),  -- Dental caries, unspecified
('TRF-040','K21.9'   ,'99213','Level 2', 600.00,'2026-07-14'),  -- Gastro-esophageal reflux disease without esophagitis
('TRF-041','K29.70'  ,'43235','Level 4',5000.00,'2026-07-14'),  -- Gastritis, unspecified, without bleeding
('TRF-042','K35.80'  ,'44950','Level 4',18000.00,'2026-07-14'), -- Unspecified acute appendicitis
('TRF-043','K80.00'  ,'47562','Level 5',45000.00,'2026-07-14'), -- Calculus of gallbladder with acute cholecystitis without obstruction
('TRF-044','M17.0'   ,'73562','Level 3',2000.00,'2026-07-14'),  -- Bilateral primary osteoarthritis of knee
('TRF-045','M54.5'   ,'99213','Level 2', 600.00,'2026-07-14'),  -- Low back pain
('TRF-046','N18.9'   ,'80069','Level 4',1500.00,'2026-07-14'),  -- Chronic kidney disease, unspecified
('TRF-047','N20.0'   ,'74176','Level 4',4500.00,'2026-07-14'),  -- Calculus of kidney
('TRF-048','N39.0'   ,'87086','Level 2', 600.00,'2026-07-14'),  -- Urinary tract infection, site not specified
('TRF-049','O14.90'  ,'59425','Level 5',10000.00,'2026-07-14'), -- Unspecified pre-eclampsia, unspecified trimester
('TRF-050','O80'     ,'59400','Level 4',12000.00,'2026-07-14'), -- Encounter for full-term uncomplicated delivery
('TRF-051','P07.30'  ,'99477','Level 6',40000.00,'2026-07-14'), -- Preterm newborn, unspecified weeks of gestation
('TRF-052','P59.9'   ,'99480','Level 4',3500.00,'2026-07-14'),  -- Neonatal jaundice, unspecified
('TRF-053','S72.001A','27245','Level 6',60000.00,'2026-07-14'), -- Fracture of neck of right femur, initial encounter, closed
('TRF-054','T30.0'   ,'16020','Level 4',5000.00,'2026-07-14'),  -- Burn of unspecified body region, unspecified degree
('TRF-055','T63.001A','96401','Level 5',15000.00,'2026-07-14'), -- Toxic effect of unspecified snake venom, accidental, initial encounter
('TRF-056','Z34.01'  ,'99213','Level 3', 800.00,'2026-07-14');  -- Encounter for supervision of normal first pregnancy, first trimester
