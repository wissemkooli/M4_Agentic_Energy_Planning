"""Create DEVELOPMENT demo accounts (refused when APP_ENV=production).

Passwords come from DEMO_ADMIN_PASSWORD / DEMO_USER_PASSWORD in M4-Mahdi/.env
(see .env.example). These accounts are for local testing only.
Usage (from M4-Mahdi):  python scripts/seed_demo.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import select  # noqa: E402

from m4.auth.security import create_user  # noqa: E402
from m4.config import BACKEND_ROOT, get_settings  # noqa: E402
from m4.db.models import User  # noqa: E402
from m4.db.session import SessionLocal, init_db  # noqa: E402

DEMO = [
    ("DEMO_ADMIN_EMAIL", "admin@m4.local", "DEMO_ADMIN_USERNAME", "admin", "DEMO_ADMIN_PASSWORD", "ADMIN", "Demo Administrator"),
    ("DEMO_USER_EMAIL", "planner@m4.local", "DEMO_USER_USERNAME", "planner", "DEMO_USER_PASSWORD", "USER", "Demo Planner"),
]


def main() -> int:
    load_dotenv(BACKEND_ROOT / ".env")
    if get_settings().app_env == "production":
        print("Refusing to create demo accounts when APP_ENV=production. Use scripts/create_admin.py.")
        return 1
    init_db()
    with SessionLocal() as db:
        for env_e, def_e, env_u, def_u, env_p, role, name in DEMO:
            email, username, password = os.environ.get(env_e, def_e), os.environ.get(env_u, def_u), os.environ.get(env_p)
            if not password:
                print(f"{env_p} is not set in M4-Mahdi/.env: skipping {role} demo account.")
                continue
            if db.scalar(select(User).where(User.email == email)):
                print(f"{role:5s} demo account already exists: {email}")
                continue
            create_user(db, email, username, password, role=role, full_name=name)
            print(f"{role:5s} demo account created: {email} / username '{username}' (password from {env_p})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
