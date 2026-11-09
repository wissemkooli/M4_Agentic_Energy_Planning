"""Shared test fixtures: isolated SQLite database, test settings, API client and tokens."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update({
    "APP_ENV": "test",
    "JWT_SECRET_KEY": "test-secret-key-for-automated-tests-only-0123456789",
    "LLM_API_KEY": "",
    "LLM_MODEL": "",
})


@pytest.fixture(scope="session")
def bundle():
    from m4.data_integration.repository import load_inputs
    return load_inputs()


@pytest.fixture()
def short_scenario():
    from m4.scenarios.schema import ScenarioConfig
    return ScenarioConfig(name="Short test horizon", start_year=2025, end_year=2028)


@pytest.fixture(scope="session")
def app_client(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    from m4.config import get_settings
    get_settings.cache_clear()
    from m4.db.session import reset_engine, init_db, SessionLocal
    reset_engine()
    init_db()
    from m4.auth.security import create_user
    with SessionLocal() as db:
        create_user(db, "admin@test.local", "admin", "AdminPass-123", role="ADMIN")
        create_user(db, "user@test.local", "user", "UserPass-123", role="USER")
        create_user(db, "other@test.local", "other", "OtherPass-123", role="USER")
    from fastapi.testclient import TestClient
    from m4.api.app import create_app
    with TestClient(create_app()) as client:
        yield client
    reset_engine()


def _login(client, login, password):
    r = client.post("/api/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="session")
def admin_headers(app_client):
    return {"Authorization": f"Bearer {_login(app_client, 'admin@test.local', 'AdminPass-123')['access_token']}"}


@pytest.fixture(scope="session")
def user_headers(app_client):
    return {"Authorization": f"Bearer {_login(app_client, 'user', 'UserPass-123')['access_token']}"}


@pytest.fixture(scope="session")
def other_headers(app_client):
    return {"Authorization": f"Bearer {_login(app_client, 'other', 'OtherPass-123')['access_token']}"}


class FakeLLM:
    """Deterministic stand-in for the LLM provider (no network)."""

    def __init__(self, json_payloads=None, tool_calls=None, fail=False):
        self.json_payloads = list(json_payloads or [])
        self.tool_calls = tool_calls
        self.fail = fail
        self.model = "fake-model"
        self.calls = 0

    @property
    def available(self):
        return True

    def chat(self, messages, tools=None, json_mode=False, max_tokens=1200):
        from m4.agent.llm import LLMError
        self.calls += 1
        if self.fail:
            raise LLMError("LLM provider returned HTTP 503: service unavailable")
        if tools is not None:
            return {"content": None, "tool_calls": self.tool_calls or []}
        import json
        return {"content": json.dumps(self.json_payloads.pop(0) if self.json_payloads else {})}

    def json_completion(self, system, user, max_tokens=1200):
        from m4.agent.llm import parse_json
        return parse_json(self.chat([{"role": "user", "content": user}], json_mode=True)["content"])


@pytest.fixture()
def fake_llm():
    return FakeLLM
