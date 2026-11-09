"""Create database tables and load the predefined scenarios (idempotent).

Usage (from M4-Mahdi):  python scripts/init_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from m4.config import get_settings  # noqa: E402
from m4.data_integration.repository import load_inputs  # noqa: E402
from m4.db.session import init_db  # noqa: E402

if __name__ == "__main__":
    s = get_settings()
    init_db()
    bundle = load_inputs()
    print(f"Database ready: {s.resolved_database_url.split('://')[0]} ({s.database_url})")
    print(f"Input data loaded through adapters: overall status {bundle.overall_status.value}")
    for p in bundle.provenance:
        print(f"  - {p.source:24s} {p.data_status.value:8s} {p.dataset_version}  ({p.path}, {p.rows} rows)")
