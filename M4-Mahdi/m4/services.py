"""Application services: orchestrate engine calls and persistence.

Routers stay thin (HTTP concerns only); the engine stays persistence-free.
"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .agent.graph import run_workflow
from .agent.llm import LLMClient, usage_totals
from .data_integration.repository import get_inputs
from .db.models import OptimizationRun, PlanningRequest, Scenario, Strategy, StrategyDecision, User
from .modeling.simulation import simulate
from .modeling.system import build_system_inputs
from .operations.engine import run_operational
from .operations.inputs import build_operational_inputs
from .operations.schema import OperationalProblem
from .optimization.engine import pareto_frontier, run_optimization
from .scenarios.schema import ObjectiveConfig, ScenarioConfig


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


# ================================================================= scenarios
def scenario_dict(s: Scenario) -> dict:
    return {"id": s.id, "name": s.name, "description": s.description, "is_predefined": s.is_predefined,
            "owner_id": s.owner_id, "config": s.config, "created_at": _iso(s.created_at), "updated_at": _iso(s.updated_at)}


def create_scenario(db: Session, user: User, cfg: ScenarioConfig) -> Scenario:
    build_system_inputs(get_inputs(), cfg)  # data-consistency validation (raises ScenarioDataError, DemandPathwayError)
    s = Scenario(name=cfg.name, description=cfg.description, is_predefined=False, owner_id=user.id,
                 config=cfg.model_dump(mode="json"))
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ================================================================= runs
def run_summary(r: OptimizationRun) -> dict:
    return {"id": r.id, "kind": r.kind, "scenario_id": r.scenario_id, "scenario_name": r.scenario_name,
            "owner_id": r.owner_id, "planning_request_id": r.planning_request_id, "status": r.status,
            "feasible": r.feasible, "method": r.method, "data_status": r.data_status, "summary": r.summary,
            "duration_seconds": r.duration_seconds, "created_at": _iso(r.created_at)}


def save_run(db: Session, user: User | None, result: dict, kind: str, scenario_id: str | None, scenario_name: str,
             seconds: float, planning_request_id: str | None = None) -> OptimizationRun:
    summary = None
    if result.get("results"):
        summary = {**result["results"]["summary"], "objective_values": result["results"]["objective_values"]}
    run = OptimizationRun(kind=kind, scenario_id=scenario_id, scenario_name=scenario_name,
                          owner_id=user.id if user else None, planning_request_id=planning_request_id,
                          status=result.get("status", "error"), feasible=bool(result.get("feasible")),
                          method=result.get("method", kind), data_status=result.get("data_status", "UNKNOWN"),
                          summary=summary, result=result, duration_seconds=round(seconds, 3))
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def resolve_scenario(db: Session, user: User, scenario_id: str | None, scenario: ScenarioConfig | None) -> tuple[ScenarioConfig, str | None]:
    if scenario is not None:
        return scenario, scenario_id
    if scenario_id is None:
        raise ServiceError("Provide either scenario_id or scenario.", 422)
    row = db.get(Scenario, scenario_id)
    if row is None or (row.owner_id not in (None, user.id) and user.role != "ADMIN"):
        raise ServiceError("Scenario not found.", 404)
    return ScenarioConfig.model_validate(row.config), row.id


def optimize(db: Session, user: User, cfg: ScenarioConfig, scenario_id: str | None,
             objective: ObjectiveConfig | None) -> OptimizationRun:
    t0 = time.perf_counter()
    inp = build_system_inputs(get_inputs(), cfg)
    result = run_optimization(inp, objective or cfg.objective)
    return save_run(db, user, result, "optimization", scenario_id, cfg.name, time.perf_counter() - t0)


def simulate_scenario(db: Session, user: User, cfg: ScenarioConfig, scenario_id: str | None,
                      capacity_plan: dict[str, dict[int, float]] | None) -> OptimizationRun:
    t0 = time.perf_counter()
    inp = build_system_inputs(get_inputs(), cfg)
    plan = {(t, int(y)): float(v) for t, ys in (capacity_plan or {}).items() for y, v in ys.items()}
    res = simulate(inp, plan)
    result = {"status": "simulated", "feasible": res["adequate"], "method": "merit_order_simulation",
              "results": res, "scenario": cfg.model_dump(mode="json"), "data_status": inp.data_status,
              "provenance": inp.provenance, "assumptions": inp.assumptions, "demand_meta": inp.demand_meta,
              "baseline_2024": inp.baseline_metrics, "capacity_plan": {f"{t}:{y}": v for (t, y), v in plan.items()},
              "message": "Simulation completed: supply is adequate." if res["adequate"]
              else "Simulation completed: adequacy issues detected (see adequacy_issues)."}
    return save_run(db, user, result, "simulation", scenario_id, cfg.name, time.perf_counter() - t0)


def pareto(db: Session, user: User, cfg: ScenarioConfig, scenario_id: str | None, primary: str, secondary: str,
           points: int) -> OptimizationRun:
    t0 = time.perf_counter()
    inp = build_system_inputs(get_inputs(), cfg)
    res = pareto_frontier(inp, primary, secondary, points)
    res.update({"scenario": cfg.model_dump(mode="json"), "method": "pareto_epsilon_constraint",
                "feasible": res["status"] == "optimal", "provenance": inp.provenance})
    return save_run(db, user, res, "pareto", scenario_id, cfg.name, time.perf_counter() - t0)


def optimize_operations(db: Session, user: User, problem: OperationalProblem) -> OptimizationRun:
    t0 = time.perf_counter()
    result = run_operational(build_operational_inputs(get_inputs(), problem))
    return save_run(db, user, result, "operational", None, problem.name, time.perf_counter() - t0)


def compare_runs(runs: list[OptimizationRun]) -> dict:
    rows = []
    for r in runs:
        row = {"run_id": r.id, "scenario_name": r.scenario_name, "kind": r.kind, "status": r.status,
               "feasible": r.feasible, "data_status": r.data_status, "method": r.method}
        res = (r.result or {}).get("results")
        if res and r.kind == "operational":
            row["problem_type"] = "operational"
            row.update({k: res["summary"].get(k) for k in ("operating_cost_musd", "unserved_gwh", "gas_gwh", "imports_gwh",
                                                            "emissions_mt", "min_reserve_margin", "renewable_share")})
        elif res:
            s = res["summary"]
            row.update({k: s.get(k) for k in ("total_cost_npv_musd", "total_investment_musd", "total_emissions_mt",
                                               "final_year_emissions_mt", "renewable_share_final_year",
                                               "gas_share_final_year", "dependence_share_horizon",
                                               "import_share_horizon", "total_new_capacity_mw",
                                               "final_capacity_mw", "emission_change_vs_2024_pct")})
            row["annual"] = [{k: a[k] for k in ("year", "renewable_share", "gas_share", "emissions_mt",
                                                 "investment_musd", "capacity_mw", "total_cost_musd")}
                             for a in res["annual"]]
        rows.append(row)
    statuses = {r["data_status"] for r in rows}
    return {"runs": rows, "data_status": statuses.pop() if len(statuses) == 1 else "MIXED"}


# ================================================================= planning requests (agent)
def request_dict(r: PlanningRequest, full: bool = True) -> dict:
    st = r.state or {}
    d = {"id": r.id, "title": r.title, "status": r.status, "owner_id": r.owner_id,
         "owner_email": r.owner.email if r.owner else None,
         "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at),
         "strategy_ids": st.get("strategy_ids", [])}
    if full:
        d.update({k: st.get(k) for k in ("messages", "trace", "intent", "questions", "investigation", "scenario",
                                         "formulation", "strategy_plan", "assumptions_to_approve", "tradeoffs",
                                         "warnings", "baseline_simulation", "llm", "approval", "admin_feedback",
                                         "understanding", "formulation_validation", "decision_report", "user_selection")})
        d["problem_type"] = st.get("problem_type", "planning")
        d["llm_usage"] = usage_totals(st.get("llm_calls", []))
        d["llm_calls"] = [{k: c.get(k) for k in ("purpose", "model_used", "status", "total_tokens", "seconds", "error")}
                          for c in st.get("llm_calls", [])][-20:]
        if d.get("investigation"):
            inv = dict(d["investigation"])
            inv.pop("results", None)  # raw tool payloads available through /investigation endpoint
            d["investigation"] = inv
    return d


def _persistable(state: dict) -> dict:
    st = {k: v for k, v in state.items() if k not in ("strategy_result", "user_message", "answers", "stage")}
    return copy.deepcopy(st)


def _llm_info() -> dict:
    c = LLMClient()
    return {"configured": c.available, "model": c.model if c.available else None,
            "fallback_models": c.fallback_models if c.available else []}


def _run(state: dict, req: PlanningRequest) -> dict:
    client = LLMClient()
    client.request_id = req.id
    return run_workflow(state, llm=client)


def start_request(db: Session, user: User, message: str, title: str | None = None) -> PlanningRequest:
    req = PlanningRequest(owner_id=user.id, title=(title or message[:80]).strip(), status="new",
                          state={"messages": [], "trace": [], "warnings": []})
    db.add(req)
    db.commit()
    return post_message(db, user, req, message, None)


def post_message(db: Session, user: User, req: PlanningRequest, message: str | None, answers: dict | None) -> PlanningRequest:
    if req.status in ("optimizing",):
        raise ServiceError("The request is being optimized; wait for the results.", 409)
    if req.status == "approved":
        raise ServiceError("A strategy of this request has been approved; start a new planning request.", 409)
    state = copy.deepcopy(req.state or {})
    role = "admin" if user.role == "ADMIN" and user.id != req.owner_id else "user"
    if message or answers:
        state.setdefault("messages", []).append({"role": role, "content": message or "", "kind": "text",
                                                 "data": {"answers": answers} if answers else None,
                                                 "at": datetime.now(timezone.utc).isoformat()})
    state.update({"stage": "message", "user_message": message or "", "answers": answers or {}, "llm": _llm_info()})
    out = _run(state, req)
    req.state = _persistable(out)
    req.status = out.get("status", req.status)
    db.commit()
    db.refresh(req)
    return req


def approve_formulation(db: Session, user: User, req: PlanningRequest, approve: bool, comment: str = "") -> PlanningRequest:
    if req.status != "awaiting_approval":
        raise ServiceError(f"Request is '{req.status}', not awaiting approval.", 409)
    state = copy.deepcopy(req.state or {})
    approval = {"approved": approve, "by": user.email, "comment": comment, "at": datetime.now(timezone.utc).isoformat()}
    state["approval"] = approval
    state.setdefault("messages", []).append({"role": "user", "kind": "approval", "data": approval, "at": approval["at"],
                                             "content": ("Formulation approved." if approve else "Formulation rejected.")
                                             + (f" {comment}" if comment else "")})
    if not approve:
        state["status"] = "formulation_rejected"
        req.state, req.status = state, "formulation_rejected"
        db.commit()
        return req
    req.status = "optimizing"
    db.commit()
    state.update({"stage": "approved", "llm": _llm_info()})
    try:
        out = _run(state, req)
    except Exception:
        req.status = "awaiting_approval"
        db.commit()
        raise
    result = out.get("strategy_result") or {}
    rec = (out.get("decision_report") or {}).get("recommendation")
    ids = _store_strategies(db, req, result, rec["strategy_key"] if rec else None)
    out["strategy_ids"] = ids
    if rec:
        rec["strategy_id"] = next((sid for sid, s in zip(ids, result.get("strategies", [])) if s["key"] == rec["strategy_key"]), None)
    req.state = _persistable(out)
    req.status = "pending_review" if out.get("status") == "strategies_ready" else out.get("status", "infeasible")
    req.state["status"] = req.status
    db.commit()
    db.refresh(req)
    return req


def _store_strategies(db: Session, req: PlanningRequest, result: dict, recommended_key: str | None = None) -> list[str]:
    owner = db.get(User, req.owner_id)
    ids = []
    previous = {s.key: s for s in db.scalars(select(Strategy).where(Strategy.planning_request_id == req.id)
                                             .order_by(Strategy.version))}
    if result.get("status") != "ok" and result.get("base_run"):
        op = result.get("problem_type") == "operational"
        save_run(db, owner, result["base_run"], "operational" if op else "optimization", None,
                 ("Operational request" if op else "Planning request") + " (infeasible)", 0.0, planning_request_id=req.id)
    for s in result.get("strategies", []):
        run = s["run"]
        op = result.get("problem_type") == "operational"
        run_row = save_run(db, owner, run, "operational" if op else "optimization", None, f"{s['name']} — {req.title[:60]}",
                           sum(x.get("seconds", 0) for x in (run.get("solver") or {}).get("solves", [])),
                           planning_request_id=req.id) if run.get("status") != "untested" else None
        prev = previous.get(s["key"])
        status = "pending_review" if s["feasible"] and not s["duplicate_of"] else \
            ("duplicate" if s["feasible"] else ("infeasible" if s["status"] == "infeasible" else s["status"]))
        row = Strategy(planning_request_id=req.id, owner_id=req.owner_id, run_id=run_row.id if run_row else None,
                       key=s["key"], name=s["name"], strategic_objective=s["strategic_objective"],
                       version=(prev.version + 1) if prev else 1, parent_strategy_id=prev.id if prev else None,
                       status=status, feasible=s["feasible"], duplicate_of=s["duplicate_of"],
                       data_status=run.get("data_status", "UNKNOWN"), report=s.get("report"),
                       explanation=s.get("explanation"), problem_type="operational" if op else "planning",
                       recommended=(s["key"] == recommended_key and status == "pending_review"))
        db.add(row)
        db.flush()
        if prev and prev.status in ("pending_review", "revision_requested", "duplicate"):
            prev.status, prev.superseded_by = "superseded", row.id
        ids.append(row.id)
    db.commit()
    return ids


def select_strategy(db: Session, user: User, req: PlanningRequest, strategy_id: str, comment: str = "") -> PlanningRequest:
    """The requester records the option they prefer (human decision support); the administrator still decides."""
    s = db.get(Strategy, strategy_id)
    if s is None or s.planning_request_id != req.id:
        raise ServiceError("Strategy not found in this planning request.", 404)
    if not s.feasible or s.duplicate_of or s.status not in ("pending_review", "approved"):
        raise ServiceError(f"Strategy is '{s.status}' and cannot be selected.", 409)
    for other in db.scalars(select(Strategy).where(Strategy.planning_request_id == req.id)):
        other.selected_by_requester = other.id == s.id
    st = copy.deepcopy(req.state or {})
    rec = (st.get("decision_report") or {}).get("recommendation") or {}
    sel = {"strategy_id": s.id, "strategy_key": s.key, "strategy_name": s.name, "by": user.email, "comment": comment,
           "follows_recommendation": rec.get("strategy_key") == s.key, "at": datetime.now(timezone.utc).isoformat()}
    st["user_selection"] = sel
    st.setdefault("messages", []).append({
        "role": "user" if user.id == req.owner_id else "admin", "kind": "selection", "data": sel, "at": sel["at"],
        "content": f"Preferred option: {s.name} (v{s.version})." + (" This follows the agent's recommendation."
                                                                   if sel["follows_recommendation"] else
                                                                   " This differs from the agent's recommendation.")
        + (f" {comment}" if comment else "") + " The administrator makes the final decision."})
    req.state = st
    db.commit()
    db.refresh(req)
    return req


# ================================================================= strategies & admin
def strategy_dict(s: Strategy, full: bool = False) -> dict:
    d = {"id": s.id, "planning_request_id": s.planning_request_id, "owner_id": s.owner_id, "run_id": s.run_id,
         "key": s.key, "name": s.name, "strategic_objective": s.strategic_objective, "version": s.version,
         "parent_strategy_id": s.parent_strategy_id, "status": s.status, "is_reference": s.is_reference,
         "feasible": s.feasible, "duplicate_of": s.duplicate_of, "superseded_by": s.superseded_by,
         "data_status": s.data_status, "created_at": _iso(s.created_at),
         "problem_type": s.problem_type or "planning", "recommended": bool(s.recommended),
         "selected_by_requester": bool(s.selected_by_requester),
         "headline": (s.report or {}).get("headline")}
    if full:
        d.update({"report": s.report, "explanation": s.explanation})
    return d


def decision_dict(d: StrategyDecision, strategy: Strategy | None = None) -> dict:
    return {"id": d.id, "strategy_id": d.strategy_id, "strategy_version": d.strategy_version,
            "strategy_name": strategy.name if strategy else None, "admin_id": d.admin_id,
            "admin_email": d.admin_email, "decision": d.decision, "comment": d.comment, "created_at": _iso(d.created_at)}


def _record(db: Session, s: Strategy, admin: User, decision: str, comment: str) -> StrategyDecision:
    d = StrategyDecision(strategy_id=s.id, strategy_version=s.version, admin_id=admin.id, admin_email=admin.email,
                         decision=decision, comment=comment)
    db.add(d)
    return d


REVIEWABLE = ("pending_review",)


def decide(db: Session, admin: User, strategy: Strategy, decision: str, comment: str) -> Strategy:
    if strategy.status not in REVIEWABLE:
        raise ServiceError(f"Strategy is '{strategy.status}' and cannot be reviewed.", 409)
    if not strategy.feasible:
        raise ServiceError("Only feasible, validated strategies can be reviewed.", 409)
    req = db.get(PlanningRequest, strategy.planning_request_id)
    if decision == "approved":
        operational = (strategy.problem_type or "planning") == "operational"
        if not operational:  # one long-term planning reference at a time; operating plans do not replace it
            for old in db.scalars(select(Strategy).where(Strategy.is_reference.is_(True))):
                old.is_reference = False
                _record(db, old, admin, "reference_superseded", f"Planning reference replaced by strategy {strategy.id} "
                                                                f"({strategy.name} v{strategy.version}).")
        strategy.status, strategy.is_reference = "approved", not operational
        _record(db, strategy, admin, "approved", comment)
        if req:
            req.status = "approved"
            st = copy.deepcopy(req.state or {})
            st["status"] = "approved"
            st.setdefault("messages", []).append({"role": "admin", "kind": "decision", "at": datetime.now(timezone.utc).isoformat(),
                                                  "content": f"Administrator approved '{strategy.name}' (v{strategy.version}) "
                                                             + ("as the operating plan (decision support only; nothing is executed "
                                                                "automatically)." if operational else "as the planning reference.")
                                                             + (f" Comment: {comment}" if comment else ""),
                                                  "data": None})
            req.state = st
    elif decision == "rejected":
        if len(comment.strip()) < 5:
            raise ServiceError("A rejection reason of at least 5 characters is required.", 422)
        strategy.status = "rejected"
        _record(db, strategy, admin, "rejected", comment.strip())
        if req:
            remaining = db.scalar(select(func.count()).select_from(Strategy).where(
                Strategy.planning_request_id == req.id, Strategy.status == "pending_review", Strategy.id != strategy.id))
            if not remaining and req.status == "pending_review":
                req.status = "rejected"
    elif decision == "revision_requested":
        if len(comment.strip()) < 5:
            raise ServiceError("Explain the requested revision (at least 5 characters).", 422)
        strategy.status = "revision_requested"
        _record(db, strategy, admin, "revision_requested", comment.strip())
        if req:
            req.status = "revision_requested"
            st = copy.deepcopy(req.state or {})
            st["status"] = "revision_requested"
            st["admin_feedback"] = {"strategy_id": strategy.id, "comment": comment.strip(), "by": admin.email,
                                    "at": datetime.now(timezone.utc).isoformat()}
            st.setdefault("messages", []).append({"role": "admin", "kind": "revision_request", "data": st["admin_feedback"],
                                                  "at": st["admin_feedback"]["at"],
                                                  "content": f"Administrator requested a revision of '{strategy.name}': {comment.strip()}"})
            req.state = st
    else:
        raise ServiceError("Unknown decision.", 422)
    db.commit()
    db.refresh(strategy)
    return strategy
