"""
app/main.py
FastAPI application entry point — SHA Claims Verification System.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import claims, review, admin, reports, auth_router

app = FastAPI(
    title="SHA Claims Verification System API",
    description="XGBoost-based health insurance claims verification pipeline for Kenya's SHA",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://claims.sha.go.ke"],  # restrict in production
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(claims.router)
app.include_router(review.router)
app.include_router(admin.router)
app.include_router(reports.router)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "SHA Claims Verification API"}
