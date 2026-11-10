"""Provider-agnostic LLM client (OpenAI-compatible chat completions API).

Works with OpenRouter (default), OpenAI, compatible gateways or a local server exposing
`/chat/completions`. The API key stays in the backend environment and is never returned
by any endpoint.

Features
- fallback models (OpenRouter `models` routing) for free models that may be rate-limited;
- reasoning control for reasoning models (OpenRouter `reasoning`: disabled by default, or low/medium/high);
- token usage tracking: every call (success or failure) is recorded in `self.calls` and in
  the `llm_calls` table (see `usage.py`), with tokens, latency and the model that answered;
- a daily call budget (`LLM_DAILY_CALL_LIMIT`) that protects free-tier quotas: when it is
  reached the client raises `LLMError` and the agent falls back to deterministic logic.

When the LLM is not configured or fails, callers fall back to deterministic logic;
numerical computation never depends on the LLM.
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from ..config import Settings, get_settings

logger = logging.getLogger("m4.llm")


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings | None = None, record_usage: bool = True):
        self.s = settings or get_settings()
        self.record_usage = record_usage
        self.calls: list[dict] = []
        self.request_id: str | None = None

    @property
    def available(self) -> bool:
        return self.s.llm_configured

    @property
    def model(self) -> str:
        return self.s.llm_model

    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in self.s.llm_fallback_models.split(",") if m.strip() and m.strip() != self.s.llm_model]

    def _body(self, messages: list[dict], tools: list[dict] | None, json_mode: bool, max_tokens: int) -> dict:
        body: dict = {"model": self.s.llm_model, "messages": messages, "temperature": self.s.llm_temperature,
                      "max_tokens": max_tokens}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.s.llm_provider == "openrouter":
            if self.fallback_models:
                body["models"] = [self.s.llm_model] + self.fallback_models
            effort = (self.s.llm_reasoning_effort or "").strip().lower()
            if effort == "none":      # reasoning models answer directly (faster, no hidden token budget)
                body["reasoning"] = {"enabled": False}
            elif effort:              # low | medium | high
                body["reasoning"] = {"effort": effort, "exclude": True}
        return body

    def _record(self, entry: dict) -> None:
        self.calls.append(entry)
        if self.record_usage:
            from .usage import record_call
            record_call({**entry, "request_id": self.request_id})

    def _post(self, body: dict, purpose: str) -> tuple[httpx.Response | None, dict, dict]:
        """One HTTP call. Returns (response or None, parsed JSON, call record with error details if any)."""
        headers = {"Authorization": f"Bearer {self.s.llm_api_key}", "Content-Type": "application/json",
                   "X-Title": "M4 Energy Planning"}
        url = self.s.llm_base_url.rstrip("/") + "/chat/completions"
        entry = {"purpose": purpose, "model_requested": self.s.llm_model, "model_used": None, "status": "error",
                 "prompt_tokens": None, "completion_tokens": None, "reasoning_tokens": None, "total_tokens": None,
                 "seconds": None, "error": None}
        t0 = time.perf_counter()
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=self.s.llm_timeout_seconds)
        except httpx.TimeoutException:
            entry.update(seconds=round(time.perf_counter() - t0, 2), error="timeout",
                         error_message=f"LLM request timed out after {self.s.llm_timeout_seconds:.0f} s.")
            return None, {}, entry
        except httpx.HTTPError as exc:
            entry.update(seconds=round(time.perf_counter() - t0, 2), error=type(exc).__name__,
                         error_message=f"Cannot reach the LLM provider: {type(exc).__name__}.")
            return None, {}, entry
        entry["seconds"] = round(time.perf_counter() - t0, 2)
        try:
            data = resp.json()
        except Exception:
            data = {}
        if resp.status_code != 200 or "error" in data:
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            detail = err.get("message") or (resp.text[:200] if resp.status_code != 200 else "unknown error")
            raw = (err.get("metadata") or {}).get("raw")
            if raw and isinstance(raw, str):
                detail = f"{detail} ({raw[:160]})"
            code = resp.status_code if resp.status_code != 200 else err.get("code", "error")
            entry["error"] = f"HTTP {code}: {detail}"[:300]
            entry["error_message"] = f"LLM provider returned HTTP {code}: {detail}".strip()
        return resp, data, entry

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False,
             max_tokens: int | None = None, purpose: str = "chat") -> dict:
        if not self.available:
            raise LLMError("LLM provider not configured (set LLM_API_KEY and LLM_MODEL in M4-Mahdi/.env).")
        limit = self.s.llm_daily_call_limit
        if self.record_usage and limit > 0:
            from .usage import calls_today
            if calls_today() >= limit:
                raise LLMError(f"Daily LLM call budget reached ({limit} calls, LLM_DAILY_CALL_LIMIT); "
                               "deterministic logic is used until tomorrow (UTC).")
        body = self._body(messages, tools, json_mode, max_tokens or self.s.llm_max_tokens)
        resp, data, entry = self._post(body, purpose)
        if resp is not None and resp.status_code == 400 and body.get("reasoning") == {"enabled": False} \
                and "reasoning" in str(entry.get("error", "")).lower():
            # some endpoints (e.g. a fallback model) require reasoning: retry once with a low effort
            self._record(entry)
            body["reasoning"] = {"effort": "low", "exclude": True}
            resp, data, entry = self._post(body, purpose + ":retry_with_reasoning")
        if resp is None or resp.status_code != 200 or "error" in data:
            self._record(entry)
            raise LLMError(entry["error_message"])
        usage = data.get("usage") or {}
        entry.update(model_used=data.get("model"), prompt_tokens=usage.get("prompt_tokens"),
                     completion_tokens=usage.get("completion_tokens"), total_tokens=usage.get("total_tokens"),
                     reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"))
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except Exception as exc:
            entry["error"] = "unexpected response format"
            self._record(entry)
            raise LLMError("Unexpected response format from the LLM provider.") from exc
        if not (msg.get("content") or msg.get("tool_calls")):
            entry["error"] = f"empty answer (finish_reason={choice.get('finish_reason')})"
            self._record(entry)
            raise LLMError(f"LLM returned an empty answer (finish_reason={choice.get('finish_reason')}); "
                           "increase LLM_MAX_TOKENS or lower LLM_REASONING_EFFORT.")
        entry["status"] = "ok"
        self._record(entry)
        logger.info("LLM %s via %s: %s tokens in %.1f s", purpose, entry["model_used"], entry["total_tokens"], entry["seconds"])
        return msg

    def json_completion(self, system: str, user: str, max_tokens: int | None = None, purpose: str = "json") -> dict:
        msg = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                        json_mode=True, max_tokens=max_tokens, purpose=purpose)
        return parse_json(msg.get("content") or "")


def parse_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise LLMError("LLM did not return a JSON object.")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM returned invalid JSON: {exc.msg}") from exc


def usage_totals(calls: list[dict]) -> dict:
    """Aggregate a list of call records (used per planning request)."""
    ok = [c for c in calls if c.get("status") == "ok"]
    return {"calls": len(calls), "successful": len(ok), "failed": len(calls) - len(ok),
            "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in ok),
            "completion_tokens": sum(c.get("completion_tokens") or 0 for c in ok),
            "total_tokens": sum(c.get("total_tokens") or 0 for c in ok),
            "seconds": round(sum(c.get("seconds") or 0 for c in calls), 2),
            "models": sorted({c["model_used"] for c in ok if c.get("model_used")})}
