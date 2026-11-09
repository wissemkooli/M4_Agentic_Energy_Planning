"""Create the first administrator account securely.

Credentials are read from the environment (M4_ADMIN_EMAIL, M4_ADMIN_USERNAME,
M4_ADMIN_PASSWORD) or asked interactively (password input is hidden). Nothing is
hard-coded. Usage (from M4-Mahdi):  python scripts/create_admin.py
"""
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from m4.auth.security import create_user  # noqa: E402
from m4.db.models import User  # noqa: E402
from m4.db.session import SessionLocal, init_db  # noqa: E402


def main() -> int:
    init_db()
    email = os.environ.get("M4_ADMIN_EMAIL") or input("Administrator email: ").strip()
    username = os.environ.get("M4_ADMIN_USERNAME") or input("Administrator username: ").strip()
    password = os.environ.get("M4_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Password (min. 10 chars, letters and digits): ")
        if password != getpass.getpass("Confirm password: "):
            print("Passwords do not match.")
            return 1
    with SessionLocal() as db:
        existing = db.scalar(select(User).where((User.email == email.lower()) | (User.username == username.lower())))
        if existing:
            print(f"User already exists ({existing.email}, role {existing.role}). Nothing changed.")
            return 0
        try:
            user = create_user(db, email, username, password, role="ADMIN", full_name="Administrator")
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
    print(f"Administrator created: {user.email} (id {user.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
