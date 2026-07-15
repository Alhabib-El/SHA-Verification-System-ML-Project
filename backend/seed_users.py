"""
backend/seed_users.py
Run ONCE after database setup to create initial system users.

Windows usage:
    cd backend
    venv\\Scripts\\activate
    python seed_users.py
"""
import os
import re
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
import bcrypt

DATABASE_URL = os.getenv("DATABASE_URL",
    "postgresql://sha_app_user:password@localhost:5432/sha_claims_db")
engine = create_engine(DATABASE_URL)

PROVIDER_TEST_PASSWORD = "Provider@2025"

def hash_pw(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(12)).decode()

def slugify(name: str) -> str:
    """'City Hospital' -> 'cityhospital' (used to build the portal@... email)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())

USERS = [
    {"user_id":"USR-SYSTEM","full_name":"System Automation",
     "email":"system@sha.go.ke","password_hash":hash_pw("system_no_login"),
     "role":"admin","provider_id":None,"is_active":False},
    {"user_id":"USR-001","full_name":"James Mwangi",
     "email":"j.mwangi@sha.go.ke","password_hash":hash_pw("Officer@2025"),
     "role":"officer","provider_id":None,"is_active":True},
    {"user_id":"USR-002","full_name":"Al-Habib",
     "email":"sichemo@mylife.mku.ac.ke","password_hash":hash_pw("Admin@2026"),
     "role":"admin","provider_id":None,"is_active":True},
    {"user_id":"USR-003","full_name":"Della Achando",
     "email":"d.achando@sha.go.ke","password_hash":hash_pw("Officer@2025"),
     "role":"finance","provider_id":None,"is_active":True},
    {"user_id":"USR-005","full_name":"SHA Admin",
     "email":"admin@sha.go.ke","password_hash":hash_pw("Admin@2025"),
     "role":"admin","provider_id":None,"is_active":True},
    {"user_id":"USR-004","full_name":"City Hospital Portal",
     "email":"portal@cityhospital.co.ke","password_hash":hash_pw(PROVIDER_TEST_PASSWORD),
     "role":"provider","provider_id":"PRV-001","is_active":True},
]

with engine.connect() as conn:
    for u in USERS:
        exists = conn.execute(text(
            "SELECT 1 FROM system_users WHERE user_id = :uid"),
            {"uid": u["user_id"]}).fetchone()
        if exists:
            print(f"  SKIP  {u['user_id']} ({u['email']})")
            continue
        conn.execute(text("""
            INSERT INTO system_users
            (user_id,full_name,email,password_hash,role,provider_id,is_active)
            VALUES(:user_id,:full_name,:email,:password_hash,:role,:provider_id,:is_active)
        """), u)
        print(f"  OK    {u['user_id']} — {u['email']} [{u['role']}]")

    # ── Provider portal logins ────────────────────────────────────────────
    # Every row in health_providers gets a portal@<slug>.co.ke login
    # automatically, so newly added providers never have to be added here
    # by hand — just re-run this script.
    providers = conn.execute(text(
        "SELECT provider_id, name FROM health_providers ORDER BY provider_id"
    )).fetchall()
    existing_provider_ids = {r.provider_id for r in conn.execute(text(
        "SELECT provider_id FROM system_users WHERE provider_id IS NOT NULL"
    )).fetchall()}

    for p in providers:
        if p.provider_id in existing_provider_ids:
            print(f"  SKIP  {p.provider_id} already has a portal login")
            continue
        email = f"portal@{slugify(p.name)}.co.ke"
        user_id = f"USR-{p.provider_id}"
        conn.execute(text("""
            INSERT INTO system_users
            (user_id,full_name,email,password_hash,role,provider_id,is_active)
            VALUES(:user_id,:full_name,:email,:password_hash,'provider',:provider_id,TRUE)
        """), {
            "user_id": user_id, "full_name": f"{p.name} Portal",
            "email": email, "password_hash": hash_pw(PROVIDER_TEST_PASSWORD),
            "provider_id": p.provider_id,
        })
        print(f"  OK    {user_id} — {email} [provider:{p.provider_id}]")

    conn.commit()

print("\nDone. Login credentials:")
print("  Officer  : j.mwangi@sha.go.ke        / Officer@2025")
print("  Admin    : sichemo@mylife.mku.ac.ke  / Admin@2026")
print("  Admin    : admin@sha.go.ke           / Admin@2025")
print("  Finance  : d.achando@sha.go.ke       / Officer@2025")
print("  Provider : portal@<hospital-slug>.co.ke / Provider@2025  (one per health_providers row)")
