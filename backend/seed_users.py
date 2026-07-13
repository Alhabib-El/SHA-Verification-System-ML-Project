"""
backend/seed_users.py
Run ONCE after database setup to create initial system users.

Windows usage:
    cd backend
    venv\\Scripts\\activate
    python seed_users.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
import bcrypt

DATABASE_URL = os.getenv("DATABASE_URL",
    "postgresql://sha_app_user:password@localhost:5432/sha_claims_db")
engine = create_engine(DATABASE_URL)

def hash_pw(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(12)).decode()

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
    {"user_id":"USR-003","full_name":"Peter Kiprop",
     "email":"p.kiprop@sha.go.ke","password_hash":hash_pw("Finance@2025"),
     "role":"finance","provider_id":None,"is_active":True},
    {"user_id":"USR-004","full_name":"Kenyatta NH Portal",
     "email":"portal@kenyattanh.go.ke","password_hash":hash_pw("Provider@2025"),
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
    conn.commit()

print("\n✓ Done. Login credentials:")
print("  Officer  : j.mwangi@sha.go.ke     / Officer@2025")
print("  Admin    : sichemo@mylife.mku.ac.ke / Admin@2026")
print("  Finance  : p.kiprop@sha.go.ke     / Finance@2025")
print("  Provider : portal@kenyattanh.go.ke / Provider@2025")
