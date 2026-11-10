"""Live LLM provider test (opt-in, uses real API quota).

Skipped unless M4_LIVE_LLM=1. Reads LLM_* settings from M4-Mahdi/.env (the test conftest blanks
them, so they are re-read from the file here). Run:
    M4_LIVE_LLM=1 python -m pytest tests/test_live_llm.py -s          (Linux/macOS)
    $env:M4_LIVE_LLM=1; python -m pytest tests/test_live_llm.py -s    (PowerShell)
"""
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

pytestmark = pytest.mark.skipif(os.environ.get("M4_LIVE_LLM") != "1", reason="live LLM test is opt-in (M4_LIVE_LLM=1)")


def _client():
    from m4.agent.llm import LLMClient
    from m4.config import Settings
    env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
    if not env.get("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set in M4-Mahdi/.env")
    s = Settings(**{k.lower(): v for k, v in env.items() if k.startswith("LLM_") and v not in (None, "")},
                 jwt_secret_key="live-test")
    return LLMClient(s, record_usage=False)


def test_live_operational_workflow():
    from m4.agent.graph import run_workflow
    c = _client()
    s = run_workflow({"stage": "message", "trace": [], "messages": [], "warnings": [],
                      "user_message": "Next month 500 MW of gas capacity is unavailable for maintenance. "
                                      "Minimize operating cost while satisfying demand with a 10% reserve margin."}, llm=c)
    assert s["problem_type"] == "operational"
    if s["status"] == "needs_clarification":
        s.update(stage="message", user_message="", answers={q["id"]: (q.get("options") or [{}])[0].get("value", 0)
                                                            if q["type"] != "number" else (q.get("proposed_value") or 0)
                                                            for q in s["questions"] if q.get("required")})
        s = run_workflow(s, llm=c)
    assert s["status"] == "awaiting_approval", s.get("scenario_error")
    s["stage"] = "approved"
    s = run_workflow(s, llm=c)
    assert s["status"] == "strategies_ready"
    r = s["decision_report"]
    print("\nLLM calls:", [(x["purpose"], x["model_used"], x["status"], x["total_tokens"], x["seconds"]) for x in c.calls])
    print("Narrative source:", r["narrative"]["source"], r["narrative"].get("llm_note"))
    assert any(x["status"] == "ok" for x in c.calls), c.calls
    # whatever the LLM did, every reported number comes from the optimizer
    assert all(x["run"]["validation"]["status"] == "valid" for x in s["strategy_result"]["strategies"] if x["feasible"])
