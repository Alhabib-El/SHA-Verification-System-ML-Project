"""
app/auth.py
JWT authentication and Role-Based Access Control (RBAC) — Section 5.6.1.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

SECRET_KEY = os.getenv("JWT_SECRET", "CHANGE_ME_IN_PRODUCTION")
ALGORITHM = "HS256"
TOKEN_EXPIRY_MINUTES = 60

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def hash_password(plain_password: str) -> str:
    """Bcrypt hash with cost factor 12, per NFR-01 security requirement."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain_password.encode(), salt).decode()


def verify_password(plain_password: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed.encode())


def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRY_MINUTES),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """FastAPI dependency: decodes JWT and returns the user claims."""
    return decode_token(token)


def require_role(*allowed_roles: str):
    """
    FastAPI dependency factory enforcing RBAC (Table 5.4).
    Usage: @router.get(..., dependencies=[Depends(require_role("officer", "admin"))])
    """
    def _checker(current_user: dict = Depends(get_current_user)):
        if current_user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user['role']}' is not permitted to access this resource."
            )
        return current_user
    return _checker
