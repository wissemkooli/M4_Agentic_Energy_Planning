"""Agent: structured intent outputs, anti-fabrication checks, LLM failure handling, workflow."""
import httpx
import pytest

from m4.agent.explain import explain_strategy, template_explanation, ungrounded_numbers
from m4.agent.graph import run_workflow
from m4.agent.intent import PlanningIntent, interpret, merge_intents, rule_parse
from m4.agent.llm import LLMClient, LLMError, parse_json
from m4.agent.tools import TOOLS, call_tool, tool_catalog
from m4.config import Settings


def test_rule_parser_example_request():
    i = rule_parse("I want Tunisia to reduce gas dependence and increase renewable electricity by 2035, "
                   "but I have a limited investment budget.")
    assert i.objectives == ["min_dependence", "max_renewables"]
    assert i.end_year == 2035
    assert i.budget_mentioned_without_value and i.budget_total_musd is None  # never invented


def test_rule_parser_numeric_constraints():
    i = rule_parse("Minimize cost with at least 40% renewable electricity by 2030, emissions reduced by 25% by 2035, "
                   "total budget 7500 MUSD, max 600 MUSD per year and a 15% reserve margin.")
    assert i.primary_objective == "min_cost"
    assert i.renewable_targets[0].value == 40 and i.renewable_targets[0].year == 2030
    assert i.emission_reduction_targets[0].value == 25 and i.emission_reduction_targets[0].year == 2035
    assert i.budget_total_musd == 7500 and i.annual_budget_musd == 600
    assert i.reserve_margin_pct == 15


def test_rule_parser_billion_conversion_and_unsupported():
    i = rule_parse("Reduce CO2 with a budget of 9 billion USD; consider batteries and nuclear")
    assert i.budget_total_musd == 9000
    assert any("Nuclear" in n for n in i.notes) and any("Storage" in n for n in i.notes)


def test_inferred_target_year_flagged():
    i = rule_parse("Minimize cost with 40% renewables by 2030 and reducing emissions by 25%.")
    assert i.emission_reduction_targets[0].year_inferred


def test_merge_intents_override_and_unlimited():
    a = rule_parse("Reduce emissions with a limited budget")
    b = merge_intents(a, rule_parse("no budget limit"))
    assert b.budget_unlimited_confirmed and not b.budget_mentioned_without_value
    c = merge_intents(a, rule_parse("budget of 5 billion dollars"))
    assert c.budget_total_musd == 5000 and c.objectives == ["min_emissions"]


def test_llm_ungrounded_numbers_discarded(fake_llm):
    llm = fake_llm(json_payloads=[{"objectives": ["min_emissions"], "budget_total_musd": 5000,
                                   "renewable_targets": [{"year": 2030, "value": 45}], "end_year": 2035}])
    intent, warnings = interpret("Reduce emissions by 2035 with a limited budget", llm)
    assert intent.parser == "llm" and intent.objectives == ["min_emissions"]
    assert intent.budget_total_musd is None and intent.renewable_targets == []
    assert intent.end_year == 2035
    assert any("budget_total_musd" in w for w in warnings)


def test_llm_failure_falls_back_to_rules(fake_llm):
    intent, warnings = interpret("Minimize cost until 2030", fake_llm(fail=True))
    assert intent.parser == "rules" and intent.objectives == ["min_cost"]
    assert "LLM interpretation failed" in warnings[0]


def test_llm_client_not_configured():
    c = LLMClient(Settings(llm_api_key="", llm_model=""))
    assert not c.available
    with pytest.raises(LLMError, match="not configured"):
        c.chat([{"role": "user", "content": "hi"}])


def test_llm_client_http_error(monkeypatch):
    c = LLMClient(Settings(llm_api_key="k", llm_model="m", jwt_secret_key="x"))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(LLMError, match="HTTP 401: bad key"):
        c.chat([{"role": "user", "content": "hi"}])

    def timeout(*a, **k):
        raise httpx.ReadTimeout("t")
    monkeypatch.setattr(httpx, "post", timeout)
    with pytest.raises(LLMError, match="timed out"):
        c.chat([{"role": "user", "content": "hi"}])


def test_parse_json_variants():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(LLMError):
        parse_json("no json here")


def test_tools_execute_real_logic(bundle):
    assert {t["name"] for t in tool_catalog()} >= {"get_reference_energy_system", "get_demand_forecast",
                                                    "get_international_benchmark", "simulate_existing_fleet"}
    ref = call_tool(bundle, "get_reference_energy_system")
    assert ref["totals"]["total_installed_capacity_mw"] == 4350
    sim = call_tool(bundle, "simulate_existing_fleet", {"end_year": 2030})
    assert sim["summary"]["total_new_capacity_mw"]["solar"] == 0
    with pytest.raises(KeyError):
        call_tool(bundle, "run_anything")
    assert not TOOLS["formulate_optimization_problem"].investigation


def test_grounding_check_rejects_invented_numbers():
    facts = {"headline": {"total_cost_npv_musd": 14890.0, "renewable_share_final_year": 0.5331}}
    assert ungrounded_numbers("Cost 14,890 MUSD and renewables 53.3 % in 2040", facts) == []
    assert ungrounded_numbers("Cost 12,000 MUSD", facts) == ["12,000"]


def _state(msg):
    return {"stage": "message", "user_message": msg, "trace": [], "messages": [], "warnings": []}


def test_workflow_clarification_then_approval_then_strategies(fake_llm):
    s = run_workflow(_state("Reduce CO2 emissions until 2030 with a limited budget and keep reliability."))
    assert s["status"] == "needs_clarification"
    ids = {q["id"] for q in s["questions"]}
    assert {"budget_total_musd", "reserve_margin_pct"} <= ids
    budget_q = next(q for q in s["questions"] if q["id"] == "budget_total_musd")
    assert "least-cost plan" in budget_q["context"]  # computed by the optimizer, not invented
    assert [t["node"] for t in s["trace"]][-1] == "ask_clarification"

    s.update(stage="message", user_message="", answers={"budget_total_musd": 5000, "reserve_margin_pct": "accept"})
    s = run_workflow(s)
    assert s["status"] == "awaiting_approval", s.get("scenario_error")
    assert s["scenario"]["constraints"]["budget_total_musd"] == 5000
    assert s["scenario"]["constraints"]["reserve_margin_pct"] == 15.0
    assert s["formulation"]["constraints"] and s["strategy_plan"]
    assert "strategy_result" not in s or s.get("strategy_result") is None  # nothing optimized before approval

    s["stage"] = "approved"
    s = run_workflow(s)
    assert s["status"] == "strategies_ready"
    strategies = s["strategy_result"]["strategies"]
    assert len(strategies) >= 3 and all(x["feasible"] for x in strategies)
    assert all(x["explanation"]["source"] == "template" for x in strategies)
    nodes = [t["node"] for t in s["trace"]]
    for n in ("simulate_baseline", "optimize_strategies", "validate_results", "analyze_tradeoffs", "explain_strategies"):
        assert n in nodes


def test_workflow_infeasible_request_reported():
    s = run_workflow(_state("Minimize cost with 90% renewable electricity by 2026, horizon until 2028, no budget limit"))
    assert s["status"] == "awaiting_approval"
    s["stage"] = "approved"
    s = run_workflow(s)
    assert s["status"] == "infeasible"
    assert s["strategy_result"]["strategies"] == []
    assert "No feasible strategy" in s["messages"][-1]["content"]


def test_workflow_with_llm_tool_selection_and_mandatory_checks(fake_llm):
    llm = fake_llm(json_payloads=[{"objectives": ["min_cost"], "end_year": 2030}],
                   tool_calls=[{"function": {"name": "get_international_benchmark", "arguments": "{}"}},
                               {"function": {"name": "run_optimization", "arguments": "{}"}}])
    s = run_workflow(_state("Minimize cost until 2030"), llm=llm)
    called = [c["tool"] for c in s["investigation"]["tool_calls"]]
    assert "get_international_benchmark" in called
    assert "run_optimization" not in called  # not an investigation tool: refused
    assert {"get_reference_energy_system", "check_data_quality"} <= set(called)  # mandatory always run
    assert s["investigation"]["planner"] == "llm:fake-model"


def test_llm_explanation_rejected_when_ungrounded(fake_llm, bundle):
    from m4.scenarios.schema import ScenarioConfig
    from m4.strategy.generator import generate_strategies
    out = generate_strategies(bundle, ScenarioConfig(name="explain", end_year=2028))
    strat = out["strategies"][0]
    llm = fake_llm(json_payloads=[{"summary": "Costs 1,234,567 MUSD", "what": "x", "why": "y", "when": "z", "tradeoffs": []}])
    e = explain_strategy(strat, llm)
    assert e["source"] == "template" and "rejected" in e["llm_note"]
    t = template_explanation(strat)
    assert t["summary"].startswith(strat["name"])


# ------------------------------------------------------------------ short-term operational problems
def test_rule_parser_operational_request():
    i = rule_parse("We expect electricity demand to increase next month. We have limited production capacity and want "
                   "to minimize operational costs while satisfying demand.")
    assert i.problem_type == "operational" and i.objectives == []
    assert i.op_objectives == ["min_operating_cost", "min_unserved"]
    assert i.period_months == 1 and i.period_relative == "next month"
    assert i.demand_increase_mentioned and i.demand_change_pct is None        # no number invented
    assert i.capacity_limit_mentioned_without_value and i.capacity_outages == []
    j = rule_parse("For the next 3 months 800 MW of gas capacity is unavailable for maintenance and demand rises by 6%. "
                   "Minimize operating cost and gas consumption.")
    assert j.period_months == 3 and j.demand_change_pct == 6
    assert j.capacity_outages[0].technology == "gas" and j.capacity_outages[0].unavailable_mw == 800
    assert j.op_objectives == ["min_operating_cost", "min_gas"]
    k = rule_parse("Plan operation in July 2025 to August 2025 with a 12% reserve margin and minimize cost.")
    assert k.problem_type == "operational" and k.period_start == "2025-07" and k.period_months == 2
    assert k.reserve_margin_pct == 12
    # long-term requests stay planning problems
    assert rule_parse("Reduce CO2 emissions by 2035 with a limited investment budget.").problem_type == "planning"


def test_llm_operational_values_grounded(fake_llm):
    llm = fake_llm(json_payloads=[{"problem_type": "operational", "op_objectives": ["min_operating_cost"],
                                   "demand_change_pct": 12, "capacity_outages": [{"technology": "gas", "unavailable_mw": 900}]}])
    intent, warnings = interpret("Next month demand rises; 500 MW of gas is unavailable; minimize operating cost.", llm)
    assert intent.problem_type == "operational" and intent.period_months == 1
    assert intent.demand_change_pct is None                       # 12 is not in the text
    assert intent.capacity_outages[0].unavailable_mw == 500       # invented 900 discarded, rule value used
    assert any("demand_change_pct" in w for w in warnings)


def test_operational_workflow_end_to_end():
    s = run_workflow(_state("We expect electricity demand to increase next month. We have limited production capacity "
                            "and want to minimize operational costs while satisfying demand."))
    assert s["status"] == "needs_clarification" and s["problem_type"] == "operational"
    ids = {q["id"] for q in s["questions"]}
    assert {"demand_scenario", "gas_unavailable_mw"} <= ids
    q = next(x for x in s["questions"] if x["id"] == "demand_scenario")
    assert "GWh" in q["options"][0]["label"]                      # computed from M1, not invented
    assert s["understanding"]["problem_type"] == "operational"
    called = [c["tool"] for c in s["investigation"]["tool_calls"]]
    assert {"get_monthly_demand", "get_operational_fleet", "simulate_status_quo_operation"} <= set(called)
    assert "estimate_unconstrained_investment" not in called
    assert s["intent"]["period_start"] == "2025-01"               # anchored on M1 data, not the clock
    assert any("first forecast month" in n for n in s["intent"]["notes"])

    s.update(stage="message", user_message="", answers={"demand_scenario": "forecast", "gas_unavailable_mw": 500})
    s = run_workflow(s)
    assert s["status"] == "awaiting_approval", s.get("scenario_error")
    v = s["formulation_validation"]
    assert v["feasibility"]["feasible"] and {c["check"] for c in v["checks"]} >= {
        "Decision variables defined with units", "Parameters traced to data sources", "Feasibility pre-check"}
    assert s["formulation"]["problem_class"].startswith("Mixed-integer")
    assert s["scenario"]["capacity_outages"] == [{"technology": "gas", "unavailable_mw": 500.0}]
    assert s.get("strategy_result") is None                       # nothing optimized before approval

    s["stage"] = "approved"
    s = run_workflow(s)
    assert s["status"] == "strategies_ready"
    out = s["strategy_result"]
    assert out["problem_type"] == "operational" and all(x["feasible"] for x in out["strategies"])
    r = s["decision_report"]
    assert r["recommendation"]["strategy_key"] == "least_cost_operations"   # first stated priority: cost
    assert "final decision is yours" in r["recommendation"]["statement"]
    assert r["narrative"]["source"] == "template" and "Data used" in r["narrative"]["text"]
    assert r["computation"]["all_validated"]
    nodes = [t["node"] for t in s["trace"]]
    assert nodes.index("validate_formulation") < nodes.index("simulate_baseline") < nodes.index("build_decision_report")


def test_operational_infeasibility_reported_before_approval():
    s = run_workflow(_state("Next month 1500 MW of gas is unavailable because of an outage. Minimize operating cost."))
    assert s["status"] == "awaiting_approval"
    v = s["formulation_validation"]
    assert not v["feasibility"]["feasible"] and v["status"] == "fail"
    assert "max_achievable_reserve_margin" in v["feasibility"]
    s["stage"] = "approved"
    s = run_workflow(s)
    assert s["status"] == "infeasible" and s["strategy_result"]["strategies"] == []


def test_decision_report_with_llm_narrative_grounding(fake_llm):
    s = run_workflow(_state("Minimize cost and reduce CO2 emissions until 2028, no budget limit"))
    assert s["status"] == "awaiting_approval"
    s["stage"] = "approved"
    good = fake_llm(json_payloads=[{"narrative": "The optimizer compared several strategies and all passed validation. "
                                                 "The final decision is yours; the data are MOCK."}])
    out = run_workflow(dict(s), llm=good)
    assert out["decision_report"]["narrative"]["source"].startswith("llm:fake-model")
    assert out["decision_report"]["recommendation"]["strategy_key"] == "balanced"   # cost + emissions requested
    assert out["llm_calls"] and out["llm_calls"][-1]["purpose"] == "decision_narrative"
    bad = fake_llm(json_payloads=[{"narrative": "This plan costs 123,456,789 MUSD and is clearly the best option for everyone."}])
    out2 = run_workflow(dict(s), llm=bad)
    assert out2["decision_report"]["narrative"]["source"] == "template"
    assert "rejected" in out2["decision_report"]["narrative"]["llm_note"]


def test_llm_client_body_usage_and_daily_limit(monkeypatch):
    sent = {}

    def ok(url, json=None, headers=None, timeout=None):
        sent.update(json)
        return httpx.Response(200, json={"model": "m-fallback", "choices": [{"message": {"content": "{\"a\": 1}"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    monkeypatch.setattr(httpx, "post", ok)
    c = LLMClient(Settings(llm_api_key="k", llm_model="m", llm_fallback_models="m2, m3", llm_reasoning_effort="none",
                           jwt_secret_key="x"), record_usage=False)
    assert c.json_completion("s", "u", purpose="intent") == {"a": 1}
    assert sent["models"] == ["m", "m2", "m3"] and sent["reasoning"] == {"enabled": False}
    assert c.calls[0]["total_tokens"] == 15 and c.calls[0]["model_used"] == "m-fallback" and c.calls[0]["purpose"] == "intent"
    # empty answer (reasoning consumed the budget) is an explicit error, not a silent success
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json={
        "choices": [{"message": {"content": None}, "finish_reason": "length"}]}))
    with pytest.raises(LLMError, match="empty answer"):
        c.chat([{"role": "user", "content": "hi"}])
    assert c.calls[-1]["status"] == "error"
    # provider error returned with HTTP 200 (OpenRouter style)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200, json={"error": {"message": "rate limited", "code": 429}}))
    with pytest.raises(LLMError, match="429"):
        c.chat([{"role": "user", "content": "hi"}])
    # daily budget reached -> no HTTP call at all
    import m4.agent.usage as usage
    monkeypatch.setattr(usage, "calls_today", lambda: 5)
    limited = LLMClient(Settings(llm_api_key="k", llm_model="m", llm_daily_call_limit=5, jwt_secret_key="x"))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    with pytest.raises(LLMError, match="Daily LLM call budget"):
        limited.chat([{"role": "user", "content": "hi"}])


def test_llm_client_retries_when_reasoning_is_mandatory(monkeypatch):
    bodies = []

    def post(url, json=None, headers=None, timeout=None):
        bodies.append(dict(json))
        if json["reasoning"] == {"enabled": False}:
            return httpx.Response(400, json={"error": {"message": "Reasoning is mandatory for this endpoint and cannot be disabled."}})
        return httpx.Response(200, json={"model": "r", "choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 3}})
    monkeypatch.setattr(httpx, "post", post)
    c = LLMClient(Settings(llm_api_key="k", llm_model="m", llm_reasoning_effort="none", jwt_secret_key="x"), record_usage=False)
    assert c.chat([{"role": "user", "content": "hi"}], purpose="intent")["content"] == "ok"
    assert [b["reasoning"] for b in bodies] == [{"enabled": False}, {"effort": "low", "exclude": True}]
    assert [x["status"] for x in c.calls] == ["error", "ok"] and c.calls[1]["purpose"] == "intent:retry_with_reasoning"
