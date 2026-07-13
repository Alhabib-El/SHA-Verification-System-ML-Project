"""
backend/hash_password.py
Prints a bcrypt hash for a plaintext password so it can be pasted into the
password_hash column when adding a user through a database GUI (e.g. the
PostgreSQL "Edit Data" grid in VS Code).

Usage:
    cd backend
    venv\\Scripts\\activate
    python hash_password.py MyPassword@2025
"""
import sys
import bcrypt

if len(sys.argv) != 2:
    print("Usage: python hash_password.py <plaintext-password>")
    sys.exit(1)

plain = sys.argv[1]
hashed = bcrypt.hashpw(plain.encode(), bcrypt.gensalt(12)).decode()
print(hashed)
