import { Bot, CheckCircle2, ChevronRight, CircleDot, Loader2, Plus, Send, ShieldCheck, User as UserIcon, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { ChatMessage, DecisionReport, Formulation, FormulationValidation, PlanningRequest, Question, StrategySummary, TraceStep } from "../api/types";
import { DecisionReportView, FormulationChecks, LlmUsageCard, UnderstandingCard } from "../components/decision";
import { useApi } from "../components/hooks";
import { Card, DataStatusBadge, ErrorState, Loading, Notice, PageHeader, StatusBadge, fmt } from "../components/ui";

const EXAMPLES = [
  "We expect electricity demand to increase next month. We have limited production capacity and want to minimize operational costs while satisfying demand.",
  "For the next 3 months, 500 MW of gas capacity is unavailable for maintenance. Minimize operating cost and gas consumption with a 10% reserve margin.",
  "Reduce CO2 emissions by 2035 with a limited investment budget while keeping electricity supply reliable.",
  "I want Tunisia to reduce gas dependence and increase renewable electricity by 2035, but I have a limited investment budget.",
  "Minimize cost while ensuring at least 40% renewable electricity by 2030 and reducing emissions by 25% by 2035.",
];

const WORKFLOW = [
  ["understand_request", "Request understanding"],
  ["check_completeness", "Objective & constraint validation"],
  ["investigate_data", "Data investigation (M1, M3, reference)"],
  ["ask_clarification", "Clarification"],
  ["build_scenario", "Scenario construction"],
  ["formulate_problem", "Mathematical formulation"],
  ["validate_formulation", "Formulation checks & feasibility"],
  ["human_approval", "Human approval"],
  ["simulate_baseline", "Energy simulation"],
  ["optimize_strategies", "Optimization (Pyomo + HiGHS)"],
  ["validate_results", "Result validation"],
  ["analyze_tradeoffs", "Trade-off analysis"],
  ["explain_strategies", "Strategy explanations"],
  ["build_decision_report", "Recommendation & decision report"],
];

export default function PlanningAgent() {
  const { requestId } = useParams();
  const navigate = useNavigate();
  const list = useApi<PlanningRequest[]>("/api/agent/requests");
  const info = useApi<{ llm_configured: boolean; llm_model: string | null; mode: string; llm_usage_today?: { calls: number; daily_call_limit: number | null } }>("/api/agent/info");
  const [strategies, setStrategies] = useState<StrategySummary[]>([]);
  const [req, setReq] = useState<PlanningRequest | null>(null);
  const [loadingReq, setLoadingReq] = useState(false);
  const [busy, setBusy] = useState<null | "message" | "optimize">(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setError(null);
    if (!requestId) {
      setReq(null);
      return;
    }
    setLoadingReq(true);
    api.get<PlanningRequest>(`/api/agent/requests/${requestId}`)
      .then((r) => setReq(r.data))
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoadingReq(false));
  }, [requestId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [req?.messages?.length, busy]);

  const nStrategies = req?.strategy_ids?.length ?? 0;
  useEffect(() => {
    if (!req?.id || nStrategies === 0) {
      setStrategies([]);
      return;
    }
    api.get<StrategySummary[]>(`/api/strategies?request_id=${req.id}`).then((r) => setStrategies(r.data)).catch(() => setStrategies([]));
  }, [req?.id, nStrategies, req?.user_selection?.strategy_id]);

  const selectOption = async (strategyId: string) => {
    if (!req) return;
    setBusy("message");
    setError(null);
    try {
      const r = await api.post<PlanningRequest>(`/api/agent/requests/${req.id}/select`, { strategy_id: strategyId });
      setReq(r.data);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const send = async (message: string | null, answers?: Record<string, unknown>) => {
    setBusy("message");
    setError(null);
    try {
      if (!req) {
        const r = await api.post<PlanningRequest>("/api/agent/requests", { message });
        setText("");
        list.reload();
        navigate(`/agent/${r.data.id}`);
      } else {
        const r = await api.post<PlanningRequest>(`/api/agent/requests/${req.id}/message`, { message, answers });
        setReq(r.data);
        setText("");
        list.reload();
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const approve = async (approveIt: boolean, comment: string) => {
    if (!req) return;
    setBusy(approveIt ? "optimize" : "message");
    setError(null);
    try {
      const r = await api.post<PlanningRequest>(`/api/agent/requests/${req.id}/approve`, { approve: approveIt, comment });
      setReq(r.data);
      list.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const canWrite = !req || !["optimizing", "approved"].includes(req.status);

  return (
    <>
      <PageHeader
        eyebrow="Agentic workflow"
        title="AI energy planning agent"
        subtitle="Describe a long-term planning goal or a short-term operating problem. The agent investigates the data, asks for missing values, proposes a validated formulation for your approval, then runs the optimizer and recommends an option — you decide. Every number comes from Python tools and HiGHS."
        actions={
          <>
            {info.data && (
              <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${info.data.llm_configured ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-line bg-white text-ink-soft"}`}>
                {info.data.llm_configured ? `LLM: ${info.data.llm_model}${info.data.llm_usage_today?.daily_call_limit ? ` · ${info.data.llm_usage_today.calls}/${info.data.llm_usage_today.daily_call_limit} calls today` : ""}` : "Deterministic mode (no LLM configured)"}
              </span>
            )}
            <button className="btn-outline" onClick={() => navigate("/agent")}><Plus size={16} /> New request</button>
          </>
        }
      />
      <div className="grid gap-6 xl:grid-cols-[230px_minmax(0,1fr)_300px]">
        {/* request list */}
        <div className="space-y-2">
          <div className="label">Planning requests</div>
          {list.loading && <Loading label="" />}
          {list.data?.length === 0 && <p className="text-xs text-ink-soft">No requests yet.</p>}
          {list.data?.map((r) => (
            <Link key={r.id} to={`/agent/${r.id}`} className={`block rounded-lg border px-3 py-2 text-sm transition ${r.id === requestId ? "border-gold bg-gold-light/40" : "border-line bg-white hover:border-ink"}`}>
              <div className="line-clamp-2 font-medium">{r.title}</div>
              <div className="mt-1 flex items-center justify-between"><StatusBadge status={r.status} /><span className="text-[11px] text-ink-soft">{new Date(r.updated_at).toLocaleDateString()}</span></div>
            </Link>
          ))}
        </div>

        {/* conversation */}
        <div className="min-w-0">
          {error && <div className="mb-4"><ErrorState message={error} /></div>}
          {loadingReq ? <Loading /> : !req ? (
            <Card title="New planning request" subtitle="State objectives (what to optimize) and constraints (limits that must hold).">
              <div className="mb-4 grid gap-2">
                {EXAMPLES.map((e) => (
                  <button key={e} className="rounded-lg border border-line bg-paper-soft px-3 py-2 text-left text-sm hover:border-gold" onClick={() => setText(e)}>{e}</button>
                ))}
              </div>
            </Card>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={req.status} />
                {req.problem_type && <span className="rounded-full border border-line bg-white px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ink-soft">{req.problem_type === "operational" ? "Operational (MILP)" : "Long-term planning (LP)"}</span>}
                {req.investigation && <DataStatusBadge status={req.investigation.data_status} />}
                {req.owner_email && <span className="text-xs text-ink-soft">Requested by {req.owner_email}</span>}
              </div>
              {req.messages?.map((m, i) => (
                <MessageView key={i} m={m} req={req} last={i === (req.messages?.length ?? 0) - 1} busy={!!busy} onAnswer={(a) => send(null, a)} onApprove={approve}
                  strategies={strategies} onSelect={selectOption} />
              ))}
              {busy === "optimize" && (
                <div className="card flex items-center gap-3 p-4 text-sm"><Loader2 className="animate-spin text-gold" size={18} />Simulating the system and solving several optimization problems with HiGHS… this takes a few seconds.</div>
              )}
              {req.status === "pending_review" && (
                <Notice tone="ok">Strategies submitted for administrator review{req.user_selection ? ` with your preferred option “${req.user_selection.strategy_name}”` : " — mark your preferred option in the report above"}. <Link className="font-medium underline" to={`/strategies?request=${req.id}`}>Open strategies</Link></Notice>
              )}
              {req.status === "revision_requested" && req.admin_feedback && (
                <Notice tone="warn">An administrator requested a revision: “{req.admin_feedback.comment}”. Describe the modification below; a new formulation will be proposed for approval.</Notice>
              )}
              <div ref={bottom} />
            </div>
          )}
          {canWrite && (
            <form className="card mt-4 flex items-end gap-2 p-3" onSubmit={(e) => { e.preventDefault(); if (text.trim()) send(text.trim()); }}>
              <textarea
                aria-label="Message to the planning agent"
                className="input min-h-[64px] resize-y border-0 focus:ring-0"
                placeholder={req ? "Answer, modify a constraint (e.g. 'set the budget to 8000 MUSD'), or add a target…" : "Describe your planning objectives and constraints…"}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && text.trim()) send(text.trim()); }}
              />
              <button className="btn-primary" disabled={!!busy || !text.trim()} aria-label="Send">
                {busy === "message" ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
              </button>
            </form>
          )}
        </div>

        {/* workflow + investigation */}
        <div className="space-y-4">
          <WorkflowPanel trace={req?.trace ?? []} status={req?.status} busy={busy} />
          {req?.understanding && <UnderstandingCard u={req.understanding} />}
          {req?.investigation && (
            <Card title="Investigation report" subtitle={`Planner: ${req.investigation.planner}`}>
              <ul className="space-y-2 text-xs">
                {req.investigation.findings.map((f, i) => <li key={i} className="flex gap-2"><ChevronRight size={12} className="mt-0.5 shrink-0 text-gold" />{f}</li>)}
              </ul>
              {req.investigation.missing_information.length > 0 && (
                <>
                  <div className="label mt-4">Data issues</div>
                  <ul className="space-y-1 text-xs text-amber-800">{req.investigation.missing_information.map((m, i) => <li key={i}>{m}</li>)}</ul>
                </>
              )}
              <div className="label mt-4">Tools executed</div>
              <div className="flex flex-wrap gap-1">
                {req.investigation.tool_calls.map((c, i) => (
                  <span key={i} className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${c.status === "ok" ? "border-line bg-paper-soft" : "border-red-200 bg-red-50 text-red-700"}`}>{c.tool}</span>
                ))}
              </div>
            </Card>
          )}
          {req?.llm_usage && <LlmUsageCard usage={req.llm_usage} calls={req.llm_calls} />}
          {req?.warnings && req.warnings.length > 0 && <Notice tone="warn">{req.warnings.join(" ")}</Notice>}
        </div>
      </div>
    </>
  );
}

function WorkflowPanel({ trace, status, busy }: { trace: TraceStep[]; status?: string; busy: null | string }) {
  const done = new Map<string, TraceStep>();
  trace.forEach((t) => done.set(t.node, t));
  const approved = trace.some((t) => t.node === "simulate_baseline");
  return (
    <Card title="Workflow status" subtitle="LangGraph nodes executed for this request">
      <ol className="space-y-2">
        {WORKFLOW.map(([id, label]) => {
          const step = done.get(id);
          const isGate = id === "human_approval";
          const gateDone = isGate && approved;
          const waiting = (isGate && status === "awaiting_approval") || (id === "ask_clarification" && status === "needs_clarification");
          const running = busy === "optimize" && !step && ["simulate_baseline", "optimize_strategies", "validate_results", "analyze_tradeoffs", "explain_strategies", "build_decision_report"].includes(id);
          return (
            <li key={id} className="flex items-start gap-2 text-xs">
              {step?.status === "error" ? <XCircle size={14} className="mt-0.5 text-red-600" /> :
                step || gateDone ? <CheckCircle2 size={14} className="mt-0.5 text-emerald-600" /> :
                running ? <Loader2 size={14} className="mt-0.5 animate-spin text-gold" /> :
                waiting ? <CircleDot size={14} className="mt-0.5 text-gold" /> : <CircleDot size={14} className="mt-0.5 text-line" />}
              <div className={step || gateDone || waiting ? "text-ink" : "text-ink-soft"}>
                <div className="font-medium">{label}{isGate && <ShieldCheck size={11} className="ml-1 inline text-gold-dark" />}</div>
                {step && <div className="text-ink-soft">{step.summary} · {step.seconds}s</div>}
                {waiting && <div className="font-medium text-gold-dark">Waiting for you</div>}
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

function MessageView({ m, req, last, busy, onAnswer, onApprove, strategies, onSelect }: { m: ChatMessage; req: PlanningRequest; last: boolean; busy: boolean; onAnswer: (a: Record<string, unknown>) => void; onApprove: (ok: boolean, comment: string) => void; strategies: StrategySummary[]; onSelect: (id: string) => void }) {
  const mine = m.role === "user";
  const Icon = m.role === "assistant" ? Bot : m.role === "admin" ? ShieldCheck : UserIcon;
  return (
    <div className={`flex gap-3 ${mine ? "flex-row-reverse" : ""}`}>
      <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${m.role === "assistant" ? "bg-ink text-gold" : m.role === "admin" ? "bg-gold text-ink" : "bg-gold-light text-gold-dark"}`}><Icon size={15} /></div>
      <div className={`min-w-0 max-w-[92%] ${mine ? "text-right" : ""}`}>
        <div className={`inline-block whitespace-pre-wrap rounded-xl px-4 py-2.5 text-left text-sm ${mine ? "bg-ink text-white" : "border border-line bg-white"}`}>{m.content}</div>
        <div className="mt-1 text-[10px] text-ink-soft">{m.role} · {fmt.date(m.at)}</div>
        {m.kind === "questions" && last && req.status === "needs_clarification" && (
          <QuestionsForm questions={(m.data?.questions as Question[]) ?? []} busy={busy} onSubmit={onAnswer} />
        )}
        {m.kind === "formulation" && (
          <FormulationView data={m.data as { formulation: Formulation; strategy_plan: PlanningRequest["strategy_plan"]; assumptions: string[]; notes: string[]; validation?: FormulationValidation }}
            canApprove={last && req.status === "awaiting_approval"} busy={busy} onApprove={onApprove} />
        )}
        {m.kind === "results" && <ResultsTable rows={(m.data?.tradeoffs as { table: Record<string, unknown>[] })?.table ?? []} req={req} />}
        {m.kind === "results" && (m.data?.report as DecisionReport | undefined) && (
          <DecisionReportView report={m.data!.report as DecisionReport} strategies={strategies} selection={req.user_selection}
            canSelect={["pending_review", "approved"].includes(req.status) && last} busy={busy} onSelect={onSelect} />
        )}
        {m.kind === "infeasible" && <InfeasibleView data={m.data as { diagnostics: { summary: string; violations: { title: string; total_shortfall: number; unit: string; years: number[] }[] } }} />}
      </div>
    </div>
  );
}

export function QuestionsForm({ questions, busy, onSubmit }: { questions: Question[]; busy: boolean; onSubmit: (a: Record<string, unknown>) => void }) {
  const [values, setValues] = useState<Record<string, string | string[]>>({});
  const [err, setErr] = useState<string | null>(null);
  const submit = () => {
    const out: Record<string, unknown> = {};
    for (const q of questions) {
      const v = values[q.id];
      if (q.type === "multi_select") {
        if (!v || (v as string[]).length === 0) return setErr("Select at least one objective.");
        out[q.id] = v;
      } else if (q.type === "single_select" && q.options?.some((o) => o.value === v)) {
        out[q.id] = v;
      } else if (q.type === "single_select" && !q.allow_number) {
        return setErr(`Choose an option: ${q.question}`);
      } else {
        const s = String(v ?? "").trim();
        if (!s) return setErr(`Answer: ${q.question}`);
        if (["budget_total_musd", "gas_limit_gwh"].includes(q.id) && ["unlimited", "none"].includes(s.toLowerCase())) out[q.id] = s.toLowerCase();
        else if (q.type === "single_select" && Number.isNaN(Number(s.replace("%", "")))) return setErr(`"${s}" is not a valid percentage.`);
        else if (q.type === "single_select") out[q.id] = Number(s.replace("%", ""));
        else if (s === "accept") out[q.id] = "accept";
        else if (Number.isNaN(Number(s)) || Number(s) < 0) return setErr(`"${s}" is not a valid non-negative number.`);
        else out[q.id] = Number(s);
      }
    }
    setErr(null);
    onSubmit(out);
  };
  return (
    <div className="card mt-2 space-y-4 p-4 text-left">
      {questions.map((q) => (
        <div key={q.id}>
          <div className="text-sm font-medium">{q.question}</div>
          {q.why && <div className="text-xs text-ink-soft">{q.why}</div>}
          {q.context && <div className="mt-1 rounded bg-gold-light/50 px-2 py-1 text-xs text-gold-dark">{q.context}</div>}
          {q.type === "multi_select" ? (
            <div className="mt-2 grid gap-1">
              {q.options?.map((o) => (
                <label key={o.value} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" className="accent-[#C6A04A]" checked={((values[q.id] as string[]) ?? []).includes(o.value)}
                    onChange={(e) => {
                      const cur = new Set((values[q.id] as string[]) ?? []);
                      if (e.target.checked) cur.add(o.value); else cur.delete(o.value);
                      setValues({ ...values, [q.id]: [...cur] });
                    }} />
                  {o.label}
                </label>
              ))}
            </div>
          ) : q.type === "single_select" ? (
            <div className="mt-2 grid gap-1">
              {q.options?.map((o) => (
                <label key={o.value} className="flex items-center gap-2 text-sm">
                  <input type="radio" name={q.id} className="accent-[#C6A04A]" checked={values[q.id] === o.value} onChange={() => setValues({ ...values, [q.id]: o.value })} />
                  {o.label}
                </label>
              ))}
              {q.allow_number && (
                <div className="flex items-center gap-2">
                  <input aria-label={`${q.question} (custom value)`} className="input max-w-[140px]" placeholder="e.g. 8"
                    value={q.options?.some((o) => o.value === values[q.id]) ? "" : ((values[q.id] as string) ?? "")}
                    onChange={(e) => setValues({ ...values, [q.id]: e.target.value })} />
                  <span className="text-xs text-ink-soft">{q.unit}</span>
                </div>
              )}
            </div>
          ) : (
            <div className="mt-2 flex items-center gap-2">
              <input aria-label={q.question} className="input max-w-[200px]" value={(values[q.id] as string) ?? ""} placeholder={q.unit} onChange={(e) => setValues({ ...values, [q.id]: e.target.value })} />
              <span className="text-xs text-ink-soft">{q.unit}</span>
              {q.proposed_value !== undefined && q.proposed_value !== null && (
                <button type="button" className="btn-outline px-2 py-1 text-xs" onClick={() => setValues({ ...values, [q.id]: "accept" })}>
                  Accept proposal: {q.proposed_value}{q.proposal_source ? ` (${q.proposal_source})` : ""}
                </button>
              )}
            </div>
          )}
        </div>
      ))}
      {err && <p role="alert" className="text-sm text-red-700">{err}</p>}
      <button className="btn-primary" disabled={busy} onClick={submit}>Submit answers</button>
    </div>
  );
}

function FormulationView({ data, canApprove, busy, onApprove }: { data: { formulation: Formulation; strategy_plan: PlanningRequest["strategy_plan"]; assumptions: string[]; notes: string[]; validation?: FormulationValidation }; canApprove: boolean; busy: boolean; onApprove: (ok: boolean, c: string) => void }) {
  const [comment, setComment] = useState("");
  const f = data.formulation;
  return (
    <div className="card mt-2 space-y-4 p-4 text-left">
      <div className="flex flex-wrap items-center gap-2 text-xs"><span className="font-semibold uppercase tracking-wide text-gold-dark">Proposed formulation</span><span className="text-ink-soft">{f.problem_class} · {f.horizon}</span><DataStatusBadge status={f.data_status} compact /></div>
      <div>
        <div className="label">Alternative strategies to compute</div>
        <ul className="space-y-1 text-sm">
          {data.strategy_plan?.map((s) => <li key={s.key}><span className="font-medium">{s.name}</span> <span className="rounded bg-paper-soft px-1 font-mono text-[11px]">{s.method}</span> — <span className="text-ink-soft">{s.objective}</span></li>)}
        </ul>
      </div>
      <div>
        <div className="label">Constraints</div>
        <table className="w-full">
          <tbody>
            {f.constraints.map((c) => (
              <tr key={c.name}><td className="td w-1/4 font-medium">{c.name}</td><td className="td font-mono text-[11px] text-ink-soft">{c.expression}</td><td className="td text-xs">{c.values}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <div className="label">Decision variables</div>
        <div className="flex flex-wrap gap-2 text-xs">{f.decision_variables.map((v) => <span key={v.symbol} className="rounded border border-line px-2 py-1"><span className="font-mono">{v.symbol}</span> [{v.unit}] {v.meaning} · {v.count}</span>)}</div>
      </div>
      <div>
        <div className="label">Objective</div>
        <p className="text-sm">{f.objective}</p>
      </div>
      {data.validation && <FormulationChecks v={data.validation} />}
      {data.validation && !data.validation.feasibility.feasible && (
        <Notice tone="warn">The feasibility pre-check found no solution under these constraints{data.validation.feasibility.max_achievable_reserve_margin != null ? ` (highest achievable reserve margin: ${fmt.pct(data.validation.feasibility.max_achievable_reserve_margin)})` : ""}. Modify a constraint in the message box, or approve to record the infeasibility diagnosis.</Notice>
      )}
      <div>
        <div className="label">Assumptions requiring your approval</div>
        <ul className="list-disc space-y-1 pl-5 text-xs">{data.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
      </div>
      {data.notes?.length > 0 && <Notice tone="warn">{data.notes.join(" ")}</Notice>}
      {canApprove && (
        <div className="border-t border-line pt-4">
          <textarea className="input mb-2" placeholder="Optional comment" value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <button className="btn-gold" disabled={busy} onClick={() => onApprove(true, comment)}><CheckCircle2 size={16} /> Approve & run optimization</button>
            <button className="btn-outline" disabled={busy} onClick={() => onApprove(false, comment)}>Reject formulation</button>
          </div>
          <p className="mt-2 text-xs text-ink-soft">To change a constraint instead, write the modification in the message box.</p>
        </div>
      )}
    </div>
  );
}

function ResultsTable({ rows, req }: { rows: Record<string, unknown>[]; req: PlanningRequest }) {
  const n = (v: unknown, d = 0) => fmt.num(v as number, d);
  if (req.problem_type === "operational") {
    return (
      <div className="card mt-2 p-4 text-left">
        <div className="label">Operating strategies (HiGHS MILP results, validated)</div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr>
              <th className="th">Strategy</th><th className="th text-right">Cost MUSD</th><th className="th text-right">Unserved GWh</th>
              <th className="th text-right">Gas GWh</th><th className="th text-right">Imports GWh</th><th className="th text-right">Min reserve</th><th className="th text-right" title="Average gas units in service">Units</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={String(r.key)}>
                  <td className="td text-xs font-medium">{String(r.name)}{r.duplicate_of ? <div className="font-normal text-ink-soft">identical to {String(r.duplicate_of)}</div> : null}{r.status !== "optimal" ? <div><StatusBadge status={String(r.status)} /></div> : null}</td>
                  <td className="td num whitespace-nowrap text-right text-xs">{n(r.operating_cost_musd, 1)}</td>
                  <td className="td num whitespace-nowrap text-right text-xs">{n(r.unserved_gwh, 1)}</td>
                  <td className="td num whitespace-nowrap text-right text-xs">{n(r.gas_gwh)}</td>
                  <td className="td num whitespace-nowrap text-right text-xs">{n(r.imports_gwh)}</td>
                  <td className="td num whitespace-nowrap text-right text-xs">{fmt.pct(r.min_reserve_margin as number)}</td>
                  <td className="td num whitespace-nowrap text-right text-xs">{n(r.avg_units_in_service, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  return (
    <div className="card mt-2 p-4 text-left">
      <div className="label">Trade-off analysis (HiGHS results, validated)</div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead><tr>
            <th className="th">Strategy</th><th className="th text-right" title="Discounted total system cost">NPV MUSD</th>
            <th className="th text-right" title="Overnight investment">Inv. MUSD</th><th className="th text-right">CO2 Mt</th>
            <th className="th text-right" title="Renewable share in the final year">RE</th><th className="th text-right" title="Implied abatement cost vs least-cost plan">USD/t</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={String(r.key)}>
                <td className="td text-xs font-medium">{String(r.name)}{r.duplicate_of ? <div className="font-normal text-ink-soft">identical to {String(r.duplicate_of)}</div> : null}{r.status !== "optimal" ? <div><StatusBadge status={String(r.status)} /></div> : null}</td>
                <td className="td num whitespace-nowrap text-right text-xs">{n(r.total_cost_npv_musd)}</td>
                <td className="td num whitespace-nowrap text-right text-xs">{n(r.total_investment_musd)}</td>
                <td className="td num whitespace-nowrap text-right text-xs">{n(r.total_emissions_mt, 1)}</td>
                <td className="td num whitespace-nowrap text-right text-xs">{fmt.pct(r.renewable_share_final_year as number)}</td>
                <td className="td num whitespace-nowrap text-right text-xs">{r.implied_abatement_cost_usd_per_t != null ? n(r.implied_abatement_cost_usd_per_t) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {req.strategy_ids.length > 0 && <Link to={`/strategies?request=${req.id}`} className="btn-outline mt-3">View strategies & roadmaps</Link>}
    </div>
  );
}

function InfeasibleView({ data }: { data: { diagnostics: { summary: string; violations: { title: string; total_shortfall: number; unit: string; years: number[] }[] } } }) {
  const d = data?.diagnostics;
  if (!d) return null;
  return (
    <div className="card mt-2 border-red-200 p-4 text-left">
      <div className="label text-red-700">Infeasibility diagnosis (elastic reformulation)</div>
      <ul className="space-y-1 text-sm">
        {d.violations.map((v, i) => <li key={i}><b>{v.title}</b>{v.years.length ? ` (${v.years[0]}–${v.years[v.years.length - 1]})` : ""}: {fmt.num(v.total_shortfall, 1)} {v.unit}</li>)}
      </ul>
      <p className="mt-2 text-xs text-ink-soft">No strategy is reported. Relax a constraint in the message box (e.g. a later target year or a higher budget).</p>
    </div>
  );
}

