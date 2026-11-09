"""API endpoints, authentication, role-based access control and the end-to-end admin workflow."""
import time

import jwt
import pytest

from m4.auth.security import hash_password, verify_password
from m4.config import get_settings


# ---------------------------------------------------------------- security
def test_health_is_public(app_client):
    r = app_client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["solver"] in ("appsi_highs", "highs") and body["data_status"] == "MOCK"
    assert "llm_api_key" not in str(body).lower()


def test_password_hashing():
    h = hash_password("Secret-Pass-1")
    assert h != "Secret-Pass-1" and h.startswith("$argon2")
    assert verify_password("Secret-Pass-1", h) and not verify_password("wrong", h)


def test_valid_login_returns_tokens_and_cookie(app_client):
    r = app_client.post("/api/auth/login", json={"login": "user@test.local", "password": "UserPass-123"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "USER" and r.json()["access_token"]
    assert "m4_refresh" in r.cookies or "m4_refresh" in r.headers.get("set-cookie", "")
    assert "password" not in str(r.json()).lower()


def test_invalid_password(app_client):
    r = app_client.post("/api/auth/login", json={"login": "user", "password": "nope-nope"})
    assert r.status_code == 401 and "Invalid" in r.json()["detail"]
    r = app_client.post("/api/auth/login", json={"login": "ghost", "password": "nope-nope"})
    assert r.status_code == 401


def test_unauthenticated_access_rejected(app_client):
    for path in ("/api/dashboard", "/api/scenarios", "/api/history", "/api/admin/overview", "/api/agent/requests"):
        assert app_client.get(path).status_code == 401
    assert app_client.get("/api/dashboard", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_user_cannot_access_admin_endpoints(app_client, user_headers):
    assert app_client.get("/api/admin/overview", headers=user_headers).status_code == 403
    assert app_client.get("/api/admin/review-queue", headers=user_headers).status_code == 403
    assert app_client.post("/api/admin/strategies/x/approve", json={}, headers=user_headers).status_code == 403
    assert app_client.get("/api/admin/users", headers=user_headers).status_code == 403


def test_admin_can_access_review_endpoints(app_client, admin_headers):
    assert app_client.get("/api/admin/overview", headers=admin_headers).status_code == 200
    assert app_client.get("/api/admin/review-queue", headers=admin_headers).status_code == 200
    assert app_client.get("/api/admin/decisions", headers=admin_headers).status_code == 200


def test_expired_access_token(app_client):
    s = get_settings()
    token = jwt.encode({"sub": "1", "role": "ADMIN", "type": "access", "exp": int(time.time()) - 10},
                       s.jwt_secret_key, algorithm="HS256")
    r = app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and "expired" in r.json()["detail"].lower()


def test_forged_token_rejected(app_client):
    token = jwt.encode({"sub": "1", "role": "ADMIN", "type": "access", "exp": int(time.time()) + 600},
                       "not-the-server-secret-but-long-enough-0123456789", algorithm="HS256")
    assert app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_refresh_rotation_and_logout(app_client):
    login = app_client.post("/api/auth/login", json={"login": "other", "password": "OtherPass-123"}).json()
    rt = login["refresh_token"]
    r1 = app_client.post("/api/auth/refresh", json={"refresh_token": rt})
    assert r1.status_code == 200
    new_rt = r1.json()["refresh_token"]
    # within the short rotation grace window the previous token still works (aborted browser responses)
    assert app_client.post("/api/auth/refresh", json={"refresh_token": rt}).status_code == 200
    # after the grace window a rotated token is rejected
    from datetime import datetime, timedelta, timezone
    from m4.auth.security import _hash_token
    from m4.db.models import RefreshToken
    from m4.db.session import SessionLocal
    with SessionLocal() as db:
        row = db.query(RefreshToken).filter_by(token_hash=_hash_token(rt)).one()
        row.revoked_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()
    assert app_client.post("/api/auth/refresh", json={"refresh_token": rt}).status_code == 401
    assert app_client.post("/api/auth/logout", json={"refresh_token": new_rt}).status_code == 204
    assert app_client.post("/api/auth/refresh", json={"refresh_token": new_rt}).status_code == 401  # logout: no grace


def test_admin_user_management(app_client, admin_headers):
    r = app_client.post("/api/admin/users", headers=admin_headers,
                        json={"email": "new.user@example.com", "username": "newbie", "password": "short"})
    assert r.status_code == 422
    r = app_client.post("/api/admin/users", headers=admin_headers,
                        json={"email": "new.user@example.com", "username": "newbie", "password": "NewUserPass-1"})
    assert r.status_code == 201 and r.json()["role"] == "USER"
    uid = r.json()["id"]
    r = app_client.patch(f"/api/admin/users/{uid}", headers=admin_headers, json={"is_active": False})
    assert r.status_code == 200 and not r.json()["is_active"]
    assert app_client.post("/api/auth/login", json={"login": "newbie", "password": "NewUserPass-1"}).status_code == 401


# ---------------------------------------------------------------- data & scenarios
def test_dashboard_and_data_endpoints(app_client, user_headers):
    d = app_client.get("/api/dashboard", headers=user_headers).json()
    assert d["data_status"] == "MOCK" and d["kpis"]["installed_capacity_mw"] == 4350
    assert len(d["demand"]["annual_forecast"]) == 16
    assert app_client.get("/api/energy/reference", headers=user_headers).status_code == 200
    f = app_client.get("/api/forecast?start=2025-01&end=2025-12", headers=user_headers).json()
    assert len(f["records"]) == 12
    assert app_client.get("/api/benchmark?indicator=renewable_share_electricity", headers=user_headers).status_code == 200
    assert app_client.get("/api/benchmark?indicator=unknown", headers=user_headers).status_code == 404
    src = app_client.get("/api/data/sources", headers=user_headers).json()
    assert src["overall_status"] == "MOCK" and src["assumptions"]["technologies"]


def test_scenarios_crud_and_validation(app_client, user_headers, other_headers):
    lst = app_client.get("/api/scenarios", headers=user_headers).json()
    assert {s["id"] for s in lst} >= {"reference", "energy_efficiency", "accelerated_renewables",
                                      "balanced_transition", "stress"}
    bad = {"config": {"name": "Bad", "start_year": 2030, "end_year": 2026}}
    assert app_client.post("/api/scenarios", json=bad, headers=user_headers).status_code == 422
    unknown = {"config": {"name": "Bad tech", "constraints": {"technology_limits": [{"technology": "fusion"}]}}}
    r = app_client.post("/api/scenarios", json=unknown, headers=user_headers)
    assert r.status_code == 422 and "fusion" in r.json()["detail"]
    good = {"config": {"name": "My scenario", "end_year": 2030,
                       "constraints": {"renewable_share_targets": [{"year": 2030, "value": 35}]}}}
    r = app_client.post("/api/scenarios", json=good, headers=user_headers)
    assert r.status_code == 201
    sid = r.json()["id"]
    assert app_client.get(f"/api/scenarios/{sid}", headers=user_headers).status_code == 200
    assert app_client.get(f"/api/scenarios/{sid}", headers=other_headers).status_code == 404  # isolation
    assert app_client.delete("/api/scenarios/reference", headers=user_headers).status_code == 403
    v = app_client.post("/api/scenarios/validate", json=good, headers=user_headers).json()
    assert v["valid"] and v["formulation"]["constraints"]


def test_optimization_and_simulation_endpoints(app_client, user_headers, other_headers):
    body = {"scenario": {"name": "API small case", "end_year": 2028}}
    r = app_client.post("/api/optimization/run", json=body, headers=user_headers)
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "optimal" and run["result"]["validation"]["status"] == "valid"
    assert app_client.get(f"/api/optimization/{run['id']}", headers=user_headers).status_code == 200
    assert app_client.get(f"/api/optimization/{run['id']}", headers=other_headers).status_code == 404
    r2 = app_client.post("/api/optimization/run", headers=user_headers,
                         json={"scenario_id": "reference", "objective": {"method": "single", "primary": "min_emissions"}})
    assert r2.status_code == 201 and r2.json()["method"] == "single"
    s = app_client.post("/api/simulation/run", headers=user_headers,
                        json={"scenario": {"name": "Sim", "end_year": 2030}, "capacity_plan": {"gas": {"2026": 800}}})
    assert s.status_code == 201 and s.json()["kind"] == "simulation"
    cmp = app_client.post("/api/comparison", json={"run_ids": [run["id"], r2.json()["id"]]}, headers=user_headers).json()
    assert len(cmp["runs"]) == 2 and cmp["data_status"] == "MOCK"
    inf = app_client.post("/api/optimization/run", headers=user_headers, json={"scenario": {
        "name": "Impossible", "end_year": 2027, "constraints": {"renewable_share_targets": [{"year": 2026, "value": 90}]}}})
    assert inf.status_code == 201 and inf.json()["status"] == "infeasible" and inf.json()["result"]["results"] is None
    missing = app_client.post("/api/optimization/run", json={}, headers=user_headers)
    assert missing.status_code == 422
    p = app_client.post("/api/optimization/pareto", headers=user_headers,
                        json={"scenario": {"name": "Pareto", "end_year": 2027}, "points": 3})
    assert p.status_code == 201 and len(p.json()["result"]["points"]) == 3
    hist = app_client.get("/api/history", headers=user_headers).json()
    assert len(hist["runs"]) >= 4


# ---------------------------------------------------------------- end-to-end workflow
def test_end_to_end_agent_and_admin_workflow(app_client, user_headers, admin_headers, other_headers):
    # 1. user submits an ambiguous request -> clarification
    r = app_client.post("/api/agent/requests", headers=user_headers, json={
        "message": "Reduce CO2 emissions until 2032 with a limited investment budget while keeping reliability."})
    assert r.status_code == 201
    req = r.json()
    assert req["status"] == "needs_clarification"
    rid = req["id"]
    assert app_client.get(f"/api/agent/requests/{rid}", headers=other_headers).status_code == 404
    # premature approval is refused
    assert app_client.post(f"/api/agent/requests/{rid}/approve", json={"approve": True},
                           headers=user_headers).status_code == 409
    # 2. answers -> formulation awaiting approval
    r = app_client.post(f"/api/agent/requests/{rid}/message", headers=user_headers,
                        json={"answers": {"budget_total_musd": 6000, "reserve_margin_pct": 15}})
    req = r.json()
    assert req["status"] == "awaiting_approval" and req["formulation"]
    # 3. user approves -> optimization -> strategies pending admin review
    r = app_client.post(f"/api/agent/requests/{rid}/approve", json={"approve": True, "comment": "ok"},
                        headers=user_headers)
    assert r.status_code == 200, r.text
    req = r.json()
    assert req["status"] == "pending_review" and len(req["strategy_ids"]) >= 3
    queue = app_client.get("/api/admin/review-queue", headers=admin_headers).json()
    pending = [s for s in queue if s["planning_request_id"] == rid]
    assert len(pending) >= 2
    detail = app_client.get(f"/api/strategies/{pending[0]['id']}", headers=user_headers).json()
    assert detail["report"]["roadmap"] and detail["run"]["validation"]["status"] == "valid"
    # 4. user cannot decide
    assert app_client.post(f"/api/admin/strategies/{pending[0]['id']}/approve", json={},
                           headers=user_headers).status_code == 403
    # 5. rejection requires a reason
    assert app_client.post(f"/api/admin/strategies/{pending[1]['id']}/reject", json={"reason": ""},
                           headers=admin_headers).status_code == 422
    r = app_client.post(f"/api/admin/strategies/{pending[1]['id']}/reject",
                        json={"reason": "Too expensive for the 2030 milestone"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    # 6. approval -> planning reference, audit trail
    r = app_client.post(f"/api/admin/strategies/{pending[0]['id']}/approve", json={"comment": "Preferred option"},
                        headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["is_reference"]
    assert app_client.post(f"/api/admin/strategies/{pending[0]['id']}/approve", json={},
                           headers=admin_headers).status_code == 409  # no double decision
    ref = app_client.get("/api/strategies/reference", headers=user_headers).json()
    assert ref["id"] == pending[0]["id"]
    decisions = app_client.get("/api/admin/decisions", headers=admin_headers).json()
    mine = [d for d in decisions if d["strategy_id"] in (pending[0]["id"], pending[1]["id"])]
    assert {d["decision"] for d in mine} == {"approved", "rejected"}
    assert all(d["admin_email"] == "admin@test.local" and d["created_at"] for d in mine)
    rejected = next(d for d in mine if d["decision"] == "rejected")
    assert rejected["comment"] == "Too expensive for the 2030 milestone"
    assert app_client.get(f"/api/agent/requests/{rid}", headers=user_headers).json()["status"] == "approved"


def test_revision_request_creates_new_version(app_client, user_headers, admin_headers):
    r = app_client.post("/api/agent/requests", headers=user_headers, json={
        "message": "Minimize cost until 2029 with a budget of 5000 MUSD"})
    rid = r.json()["id"]
    assert r.json()["status"] == "awaiting_approval"
    req = app_client.post(f"/api/agent/requests/{rid}/approve", json={"approve": True}, headers=user_headers).json()
    sid = next(s for s in req["strategy_ids"])
    r = app_client.post(f"/api/admin/strategies/{sid}/request-revision",
                        json={"comment": "Add a 30% renewable target from 2028"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "revision_requested"
    req = app_client.get(f"/api/agent/requests/{rid}", headers=user_headers).json()
    assert req["status"] == "revision_requested" and req["admin_feedback"]["comment"].startswith("Add a 30%")
    req = app_client.post(f"/api/agent/requests/{rid}/message", headers=user_headers,
                          json={"message": "Add at least 30% renewable electricity from 2028"}).json()
    assert req["status"] == "awaiting_approval"
    assert req["scenario"]["constraints"]["renewable_share_targets"] == [{"year": 2028, "value": 30.0}]
    req = app_client.post(f"/api/agent/requests/{rid}/approve", json={"approve": True}, headers=user_headers).json()
    new = app_client.get(f"/api/strategies?request_id={rid}", headers=user_headers).json()
    v2 = [s for s in new if s["version"] == 2]
    assert v2 and all(s["parent_strategy_id"] for s in v2)
    old = app_client.get(f"/api/strategies/{sid}", headers=user_headers).json()
    assert old["status"] == "superseded" and old["superseded_by"] in {s["id"] for s in v2}
    assert [d["decision"] for d in old["decisions"]] == ["revision_requested"]  # audit record preserved
