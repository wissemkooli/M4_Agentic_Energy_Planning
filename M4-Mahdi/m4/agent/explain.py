"""Strategy explanations grounded in verified solver results.

`template_explanation` builds the explanation deterministically from the strategy report.
`llm_explanation` lets the LLM rephrase the same facts; its output is accepted only if
every number it contains matches a number in the verified facts (grounding check).
Otherwise the template explanation is used and the rejection is recorded.
"""
from __future__ import annotations

import json
import re

from .llm import LLMClient, LLMError


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f} %"


def template_explanation(strategy: dict) -> dict:
    rep = strategy["report"]
    h = rep["headline"]
    road = rep["roadmap"]
    first_year, last_year = road[0]["year"], road[-1]["year"]
    early = [r for r in road if r["investment_musd"] > 0][:3]
    when = "; ".join(f"{r['year']}: " + ", ".join(r["actions"]) for r in early) or "No investment required."
    trade = rep.get("tradeoff_vs_least_cost")
    if trade:
        why = (f"Compared with the least-cost plan it avoids {trade['emissions_avoided_mt']:,.1f} MtCO2 and changes the "
               f"discounted system cost by {trade['additional_cost_npv_musd']:+,.0f} MUSD.")
    else:
        why = "It is the least-cost way of meeting demand and every approved constraint."
    return {
        "source": "template",
        "summary": (f"{strategy['name']}: {rep['approach']} Renewables reach {_pct(h['renewable_share_final_year'])} "
                    f"of supply in {last_year}, gas {_pct(h['gas_share_final_year'])}; annual emissions change by "
                    f"{h['emission_change_vs_2024_pct']:+.1f} % versus 2024."),
        "what": rep["approach"],
        "why": f"Strategic objective: {strategy['strategic_objective']}. {why}",
        "when": f"Implementation {first_year}-{last_year}. First investment steps — {when}",
        "tradeoffs": rep["advantages"] + rep["disadvantages"],
    }


def _facts(strategy: dict) -> dict:
    rep = strategy["report"]
    return {"name": strategy["name"], "objective": strategy["strategic_objective"], "approach": rep["approach"],
            "headline": rep["headline"], "tradeoff_vs_least_cost": rep.get("tradeoff_vs_least_cost"),
            "milestones": rep["milestones"], "advantages": rep["advantages"], "disadvantages": rep["disadvantages"],
            "data_status": rep["data_status"]}


def _collect_numbers(obj, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
        out.add(float(obj) * 100)  # shares written as percentages
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)
    elif isinstance(obj, str):
        for s in re.findall(r"-?\d[\d,]*(?:\.\d+)?", obj):
            try:
                out.add(float(s.replace(",", "")))
            except ValueError:
                pass


def ungrounded_numbers(text: str, facts: dict) -> list[str]:
    allowed: set[float] = set()
    _collect_numbers(facts, allowed)
    bad = []
    for s in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text):
        try:
            v = abs(float(s.replace(",", "")))
        except ValueError:
            continue
        if v <= 10 and float(v).is_integer():   # ordinals / small counts
            continue
        if 2000 <= v <= 2100:                   # years
            continue
        if not any(abs(v - abs(a)) <= max(0.051, 0.006 * abs(a)) for a in allowed):
            bad.append(s)
    return bad


LLM_SYSTEM = """You explain long-term electricity strategies for decision-makers.
Use ONLY the facts provided. Do not introduce any number that is not present in the facts.
Do not claim data is real if data_status is MOCK. Be concise and neutral.
Return JSON: {"summary": str, "what": str, "why": str, "when": str, "tradeoffs": [str]}."""


def llm_explanation(strategy: dict, client: LLMClient) -> tuple[dict | None, str | None]:
    facts = _facts(strategy)
    try:
        data = client.json_completion(LLM_SYSTEM, json.dumps(facts, default=str), max_tokens=900)
    except LLMError as exc:
        return None, str(exc)
    text = " ".join([str(data.get(k, "")) for k in ("summary", "what", "why", "when")]
                    + [str(x) for x in data.get("tradeoffs", [])])
    bad = ungrounded_numbers(text, facts)
    if bad:
        return None, f"LLM explanation rejected: numbers not found in verified results {bad[:5]}"
    if not all(isinstance(data.get(k), str) and data.get(k) for k in ("summary", "what", "why", "when")):
        return None, "LLM explanation rejected: missing fields"
    data["source"] = f"llm:{client.model} (grounding check passed)"
    data["tradeoffs"] = [str(x) for x in data.get("tradeoffs", [])]
    return data, None


def explain_strategy(strategy: dict, client: LLMClient | None = None) -> dict:
    base = template_explanation(strategy)
    if client is not None and client.available:
        llm, err = llm_explanation(strategy, client)
        if llm:
            llm["template"] = base
            return llm
        base["llm_note"] = err
    return base
