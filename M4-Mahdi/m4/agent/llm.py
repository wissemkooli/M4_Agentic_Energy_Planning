"""Minimal provider-agnostic LLM client (OpenAI-compatible chat completions API).

Works with OpenRouter (default), OpenAI, Azure-compatible gateways or a local server
exposing `/chat/completions`. The API key stays in the backend environment and is never
returned by any endpoint. When the LLM is not configured or fails, callers fall back to
deterministic logic; numerical computation never depends on the LLM.
"""
from __future__ import annotations

import json
import re

import httpx

from ..config import Settings, get_settings


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()

    @property
    def available(self) -> bool:
        return self.s.llm_configured

    @property
    def model(self) -> str:
        return self.s.llm_model

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False,
             max_tokens: int = 1200) -> dict:
        if not self.available:
            raise LLMError("LLM provider not configured (set LLM_API_KEY and LLM_MODEL in M4-Mahdi/.env).")
        body: dict = {"model": self.s.llm_model, "messages": messages, "temperature": self.s.llm_temperature,
                      "max_tokens": max_tokens}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.s.llm_api_key}", "Content-Type": "application/json",
                   "X-Title": "M4 Energy Planning"}
        url = self.s.llm_base_url.rstrip("/") + "/chat/completions"
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=self.s.llm_timeout_seconds)
        except httpx.TimeoutException as exc:
            raise LLMError(f"LLM request timed out after {self.s.llm_timeout_seconds:.0f} s.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot reach the LLM provider: {type(exc).__name__}.") from exc
        if resp.status_code != 200:
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except Exception:
                detail = resp.text[:200]
            raise LLMError(f"LLM provider returned HTTP {resp.status_code}: {detail}".strip())
        try:
            return resp.json()["choices"][0]["message"]
        except Exception as exc:
            raise LLMError("Unexpected response format from the LLM provider.") from exc

    def json_completion(self, system: str, user: str, max_tokens: int = 1200) -> dict:
        msg = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                        json_mode=True, max_tokens=max_tokens)
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
