"""Check the LLM configuration: key, model availability on the provider, one JSON call and one tool call.

Usage (from M4-Mahdi/):  python scripts/check_llm.py
Uses 2 requests of the provider quota. Never prints the API key.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from m4.agent.llm import LLMClient, LLMError  # noqa: E402
from m4.config import get_settings  # noqa: E402


def main() -> int:
    s = get_settings()
    if not s.llm_configured:
        print("LLM not configured: set LLM_API_KEY and LLM_MODEL in M4-Mahdi/.env (the agent runs in deterministic mode).")
        return 1
    print(f"Provider: {s.llm_provider}  base URL: {s.llm_base_url}")
    print(f"Model: {s.llm_model}  fallbacks: {s.llm_fallback_models or '-'}  reasoning: {s.llm_reasoning_effort or 'provider default'}")
    if s.llm_provider == "openrouter":
        try:
            models = {m["id"] for m in httpx.get(s.llm_base_url.rstrip("/") + "/models", timeout=30).json()["data"]}
            for m in [s.llm_model] + [x.strip() for x in s.llm_fallback_models.split(",") if x.strip()]:
                print(f"  {m}: {'listed' if m in models else 'NOT LISTED (choose another model)'}")
            key = httpx.get(s.llm_base_url.rstrip("/") + "/key", headers={"Authorization": f"Bearer {s.llm_api_key}"},
                            timeout=30).json().get("data", {})
            if key:
                print(f"  key: free tier={key.get('is_free_tier')}  free requests today={key.get('free_model_daily_requests')}")
        except Exception as exc:
            print(f"  could not query the provider catalogue: {exc}")
    c = LLMClient(record_usage=False)
    try:
        out = c.json_completion('Return JSON {"status": "ok", "objective": <the optimization objective in the text>}.',
                                "Minimize operating cost next month.", purpose="check")
        print(f"JSON call: OK -> {out}")
        msg = c.chat([{"role": "user", "content": "Get the 2024 reference energy system."}], purpose="check",
                     tools=[{"type": "function", "function": {"name": "get_reference_energy_system",
                                                              "description": "2024 reference system",
                                                              "parameters": {"type": "object", "properties": {}}}}])
        print(f"Tool call: {'OK -> ' + str([t['function']['name'] for t in msg.get('tool_calls') or []]) if msg.get('tool_calls') else 'no tool call returned (the agent then uses its default plan)'}")
    except LLMError as exc:
        print(f"LLM call FAILED: {exc}")
        return 2
    for x in c.calls:
        print(f"  {x['purpose']}: model {x['model_used']}, {x['total_tokens']} tokens, {x['seconds']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
