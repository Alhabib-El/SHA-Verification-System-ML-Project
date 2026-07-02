"""
app/database.py
Database connection and session management — SHA Claims Verification System.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Loaded from environment variables — never hardcoded
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://sha_app_user:CHANGE_ME@localhost:5432/sha_claims_db"
)

engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency providing a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
