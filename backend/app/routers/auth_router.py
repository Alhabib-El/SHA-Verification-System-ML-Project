"""
app/routers/auth_router.py
Login endpoint — returns JWT token + user info the frontend needs.

FIX: now returns full_name and user_id so the frontend can display
     the logged-in user's name in the topbar.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db
from ..schemas import LoginRequest
from ..auth import verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.execute(text("""
        SELECT user_id, full_name, password_hash, role, is_active
        FROM system_users WHERE email = :email
    """), {"email": payload.email}).fetchone()

    if not user or not user.is_active:
        raise HTTPException(401, "Invalid credentials")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")

    db.execute(text(
        "UPDATE system_users SET last_login_at = NOW() WHERE user_id = :uid"),
        {"uid": user.user_id})
    db.commit()

    token = create_access_token(user.user_id, user.role)
    return {
        "access_token": token,
        "token_type":   "bearer",
        "role":         user.role,
        "full_name":    user.full_name,   # ADDED — frontend displays this in topbar
        "user_id":      user.user_id,     # ADDED — frontend uses for audit log display
    }
