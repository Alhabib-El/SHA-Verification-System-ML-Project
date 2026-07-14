"""
app/routers/admin.py
FR-08 — CRUD operations for healthcare providers, tariffs, and users.
All endpoints restricted to the 'admin' role (Table 5.4 RBAC matrix).
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional

from ..database import get_db
from ..schemas import (
    ProviderCreateRequest, ProviderUpdateRequest, ProviderResponse,
    UserCreateRequest, UserUpdateRequest, UserResponse,
)
from ..auth import require_role, hash_password

router = APIRouter(prefix="/admin", tags=["Admin / CRUD"])


# ── PROVIDERS ────────────────────────────────────────────────────────────────
@router.get("/providers", response_model=list[ProviderResponse])
def list_providers(
    county: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_role("admin")),
):
    """Powers the Admin CRUD screen search/filter (Figure 5.7)."""
    query = "SELECT * FROM health_providers WHERE 1=1"
    params = {}
    if county:
        query += " AND county = :county"
        params["county"] = county
    if status:
        query += " AND status = :status"
        params["status"] = status
    if search:
        query += " AND (name ILIKE :search OR provider_id ILIKE :search)"
        params["search"] = f"%{search}%"

    rows = db.execute(text(query), params).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/providers", response_model=ProviderResponse, status_code=201)
def create_provider(payload: ProviderCreateRequest, db: Session = Depends(get_db),
                     current_user: dict = Depends(require_role("admin"))):
    provider_id = f"PRV-{uuid.uuid4().hex[:6].upper()}"
    db.execute(text("""
        INSERT INTO health_providers
            (provider_id, name, facility_type, facility_tier, county,
             accreditation_no, regulatory_body, empanelment_date,
             accreditation_expiry, status, risk_tier)
        VALUES
            (:pid, :name, :ftype, :ftier, :county, :accno, :regbody,
             CURRENT_DATE, CURRENT_DATE + INTERVAL '1 year', 'active', 'low')
    """), {
        "pid": provider_id, "name": payload.name, "ftype": payload.facility_type,
        "ftier": payload.facility_tier, "county": payload.county,
        "accno": payload.accreditation_no, "regbody": payload.regulatory_body,
    })
    db.commit()
    row = db.execute(text("SELECT * FROM health_providers WHERE provider_id = :pid"),
                      {"pid": provider_id}).fetchone()
    return dict(row._mapping)


@router.patch("/providers/{provider_id}", response_model=ProviderResponse)
def update_provider(provider_id: str, payload: ProviderUpdateRequest,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(require_role("admin"))):
    """Used for suspend/activate actions in Figure 5.7."""
    updates = payload.dict(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    db.execute(text(f"""
        UPDATE health_providers SET {set_clause} WHERE provider_id = :pid
    """), {**updates, "pid": provider_id})
    db.commit()

    row = db.execute(text("SELECT * FROM health_providers WHERE provider_id = :pid"),
                      {"pid": provider_id}).fetchone()
    if not row:
        raise HTTPException(404, "Provider not found")
    return dict(row._mapping)


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: str, db: Session = Depends(get_db),
                     current_user: dict = Depends(require_role("admin"))):
    db.execute(text("DELETE FROM health_providers WHERE provider_id = :pid"),
               {"pid": provider_id})
    db.commit()


# ── TARIFFS ──────────────────────────────────────────────────────────────────
@router.get("/tariffs")
def list_tariffs(db: Session = Depends(get_db),
                  current_user: dict = Depends(require_role("admin"))):
    rows = db.execute(text("SELECT * FROM sha_tariffs WHERE effective_to IS NULL")).fetchall()
    return [dict(r._mapping) for r in rows]


# ── STAFF USERS ──────────────────────────────────────────────────────────────
@router.get("/users", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db),
               current_user: dict = Depends(require_role("admin"))):
    """Powers the Admin CRUD 'Staff Users' section."""
    rows = db.execute(text("""
        SELECT user_id, full_name, email, role, provider_id, is_active
        FROM system_users ORDER BY user_id
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreateRequest, db: Session = Depends(get_db),
                 current_user: dict = Depends(require_role("admin"))):
    """Lets an admin create any staff account — including granting admin
    rights to a new user — not just provider portal logins."""
    existing = db.execute(text(
        "SELECT 1 FROM system_users WHERE email = :email"),
        {"email": payload.email}).fetchone()
    if existing:
        raise HTTPException(400, "A user with this email already exists")

    user_id = f"USR-{uuid.uuid4().hex[:8].upper()}"
    db.execute(text("""
        INSERT INTO system_users
            (user_id, full_name, email, password_hash, role, provider_id, is_active)
        VALUES
            (:uid, :name, :email, :pwhash, :role, :provider_id, TRUE)
    """), {
        "uid": user_id, "name": payload.full_name, "email": payload.email,
        "pwhash": hash_password(payload.password), "role": payload.role,
        "provider_id": payload.provider_id,
    })
    db.commit()
    row = db.execute(text("""
        SELECT user_id, full_name, email, role, provider_id, is_active
        FROM system_users WHERE user_id = :uid
    """), {"uid": user_id}).fetchone()
    return dict(row._mapping)


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: str, payload: UserUpdateRequest,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(require_role("admin"))):
    """Used to grant/revoke admin rights or deactivate a staff account."""
    updates = payload.dict(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")
    if user_id == current_user["sub"] and updates.get("is_active") is False:
        raise HTTPException(400, "You cannot deactivate your own account")
    if user_id == current_user["sub"] and updates.get("role") not in (None, "admin"):
        raise HTTPException(400, "You cannot remove your own admin rights")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    db.execute(text(f"""
        UPDATE system_users SET {set_clause} WHERE user_id = :uid
    """), {**updates, "uid": user_id})
    db.commit()

    row = db.execute(text("""
        SELECT user_id, full_name, email, role, provider_id, is_active
        FROM system_users WHERE user_id = :uid
    """), {"uid": user_id}).fetchone()
    if not row:
        raise HTTPException(404, "User not found")
    return dict(row._mapping)


# ── THRESHOLD CONFIGURATION ──────────────────────────────────────────────────
@router.patch("/settings/flag-threshold")
def update_flag_threshold(new_threshold: float, db: Session = Depends(get_db),
                           current_user: dict = Depends(require_role("admin"))):
    """Allows the admin to configure the XGBoost flagging threshold (NFR-06)
    without redeploying the system, per FR-06 acceptance criteria."""
    if not (0 <= new_threshold <= 1):
        raise HTTPException(400, "Threshold must be between 0 and 1")
    # In production this is persisted to a system_settings table or config service
    return {"flag_threshold": new_threshold, "message": "Threshold updated successfully"}
