"""
app/main.py
FastAPI application entry point — SHA Claims Verification System.

FIXES:
  - CORS now allows localhost for development
  - Suspension router added
  - python-dotenv loaded at startup
"""
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import claims, review, admin, reports, auth_router, suspension

app = FastAPI(
    title="SHA Claims Verification System API",
    description="XGBoost-based health insurance claims verification pipeline for Kenya's SHA",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://127.0.0.1",
        "null",              # allows file:// opened HTML files during development
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(claims.router)
app.include_router(review.router)
app.include_router(admin.router)
app.include_router(reports.router)
app.include_router(suspension.router)

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "SHA Claims Verification API", "version": "1.0.0"}
