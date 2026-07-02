"""
app/routers/auth_router.py
Login endpoint issuing JWT tokens (Section 5.6.1).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database import get_db
from ..schemas import LoginRequest, TokenResponse
from ..auth import verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.execute(text("""
        SELECT user_id, password_hash, role, is_active
        FROM system_users WHERE email = :email
    """), {"email": payload.email}).fetchone()

    if not user or not user.is_active:
        raise HTTPException(401, "Invalid credentials")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")

    db.execute(text("UPDATE system_users SET last_login_at = NOW() WHERE user_id = :uid"),
               {"uid": user.user_id})
    db.commit()

    token = create_access_token(user.user_id, user.role)
    return {"access_token": token, "role": user.role}
