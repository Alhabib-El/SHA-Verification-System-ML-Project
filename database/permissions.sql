-- ============================================================================
-- SHA CLAIMS VERIFICATION SYSTEM — ROLE PERMISSIONS
-- Implements Section 5.6.2: append-only audit log, least-privilege access
--
-- Note the pattern throughout: sha_app_user (what the FastAPI backend
-- connects as) is NEVER granted DELETE on anything. Even the admin CRUD
-- "delete provider" feature in the app relies only on SELECT/INSERT/UPDATE.
-- This means even a fully compromised backend/API key cannot destroy
-- historical data — genuine deletions require connecting as the database
-- superuser directly, outside the application entirely.
-- ============================================================================

-- Application database role (used by the FastAPI backend connection pool)
CREATE ROLE sha_app_user WITH LOGIN PASSWORD 'CHANGE_ME';

GRANT CONNECT ON DATABASE sha_claims_db TO sha_app_user;
GRANT USAGE ON SCHEMA public TO sha_app_user;

-- Standard CRUD on operational tables
GRANT SELECT, INSERT, UPDATE ON claims, verification_results,
    sha_members, health_providers, sha_tariffs TO sha_app_user;

-- system_users: no DELETE — accounts are deactivated, never removed
GRANT SELECT, INSERT, UPDATE ON system_users TO sha_app_user;

-- audit_log: APPEND-ONLY — INSERT and SELECT only.
-- No UPDATE or DELETE grant, per Section 5.6.2 "Audit Trail Integrity".
GRANT SELECT, INSERT ON audit_log TO sha_app_user;
REVOKE UPDATE, DELETE ON audit_log FROM sha_app_user;

-- Allow use of generated/serial columns
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sha_app_user;

-- Read access to reporting views
GRANT SELECT ON v_flagged_claims_queue, v_daily_claims_summary TO sha_app_user;
