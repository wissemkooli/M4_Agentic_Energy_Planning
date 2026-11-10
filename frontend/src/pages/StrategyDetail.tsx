import { CheckCircle2, RotateCcw, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { OperationalValidation, RunResult, StrategyDetail as SD } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { CapacityArea, ShareLines, StackedTechBars, annualToTech } from "../components/charts";
import { useApi } from "../components/hooks";
import { OperationalCharts, OperationalStats, OperationalValidationView, ScheduleTimeline } from "../components/operations";
import { Card, DataStatusBadge, ErrorState, Loading, Notice, PageHeader, Stat, StatusBadge, Tabs, fmt } from "../components/ui";
import { ConstraintTable } from "./RunResults";

export function validateDecision(kind: "approve" | "reject" | "revise", text: string): string | null {
  if ((kind === "reject" || kind === "revise") && text.trim().length < 5) return kind === "reject" ? "A rejection reason (at least 5 characters) is mandatory." : "Describe the requested revision (at least 5 characters).";
  return null;
}

function AdminDecisionPanel({ s, onDone }: { s: SD; onDone: () => void }) {
  const [kind, setKind] = useState<"approve" | "reject" | "revise">("approve");
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const v = validateDecision(kind, text);
    if (v) return setErr(v);
    setBusy(true);
    setErr(null);
    try {
      if (kind === "approve") await api.post(`/api/admin/strategies/${s.id}/approve`, { comment: text });
      if (kind === "reject") await api.post(`/api/admin/strategies/${s.id}/reject`, { reason: text });
      if (kind === "revise") await api.post(`/api/admin/strategies/${s.id}/request-revision`, { comment: text });
      setText("");
      onDone();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Administrator decision" subtitle="The agent proposes, the optimizer calculates, the administrator decides. Decisions are recorded in the audit trail and never overwritten." className="border-gold/60">
      <div className="mb-3 flex flex-wrap gap-2">
        {([["approve", "Accept", CheckCircle2], ["reject", "Reject", XCircle], ["revise", "Request revision", RotateCcw]] as const).map(([k, label, Icon]) => (
          <button key={k} onClick={() => { setKind(k); setErr(null); }} className={`btn ${kind === k ? "bg-ink text-white" : "border border-line bg-white"}`}><Icon size={15} /> {label}</button>
        ))}
      </div>
      <label className="label" htmlFor="decision-text">{kind === "approve" ? "Comment (optional)" : kind === "reject" ? "Rejection reason (required)" : "Requested revision (required)"}</label>
      <textarea id="decision-text" className="input mb-3" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      {err && <p role="alert" className="mb-3 text-sm text-red-700">{err}</p>}
      <button className={kind === "approve" ? "btn-gold" : kind === "reject" ? "btn-danger" : "btn-outline"} disabled={busy} onClick={submit}>
        {busy ? "Recording…" : kind === "approve" ? (s.problem_type === "operational" ? "Accept as operating plan" : "Accept as planning reference") : kind === "reject" ? "Reject strategy" : "Send revision request"}
      </button>
      {kind === "approve" && <p className="mt-2 text-xs text-ink-soft">{s.problem_type === "operational" ? "Accepting records the decision; nothing is executed on the real system." : "An approved strategy becomes the planning reference. It is a recommendation, not an authorization to execute investments."}</p>}
    </Card>
  );
}

export default function StrategyDetail() {
  const { strategyId } = useParams();
  const { user } = useAuth();
  const { data: s, loading, error, reload } = useApi<SD>(`/api/strategies/${strategyId}`);
  const [tab, setTab] = useState("roadmap");
  if (loading) return <Loading />;
  if (error || !s) return <ErrorState message={error ?? "Not found"} onRetry={reload} />;
  const rep = s.report;
  const annual = s.run?.annual ?? [];
  const h = rep?.headline;
  if (rep && h?.kind === "operational") return <OperationalStrategy s={s} reload={reload} isAdmin={user?.role === "ADMIN"} />;

  return (
    <>
      <PageHeader
        eyebrow={`Strategy · version ${s.version}`}
        title={s.name}
        subtitle={s.strategic_objective}
        actions={<><Badges s={s} />{s.is_reference && <span className="rounded-full bg-gold px-2 py-0.5 text-[11px] font-semibold text-ink">PLANNING REFERENCE</span>}</>}
      />
      {s.data_status !== "REAL" && <div className="mb-4"><Notice tone="warn">Based on MOCK inputs: this roadmap illustrates the method and is not an actual investment plan for Tunisia.</Notice></div>}
      {s.superseded_by && <div className="mb-4"><Notice>Superseded by a newer version: <Link className="underline" to={`/strategies/${s.superseded_by}`}>open the revision</Link>.</Notice></div>}

      {!rep || !h || h.kind ? <ErrorState message="This strategy has no validated solution." /> : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
            <Stat label="NPV system cost" value={fmt.num(h.total_cost_npv_musd)} unit="MUSD" />
            <Stat label="Investment" value={fmt.num(h.total_investment_musd)} unit="MUSD" />
            <Stat label="Cumulative CO2" value={fmt.num(h.total_emissions_mt, 1)} unit="Mt" hint={`${fmt.num(h.emission_change_vs_2024_pct, 1)} % vs 2024 (final year)`} />
            <Stat label="Renewables final year" value={fmt.pct(h.renewable_share_final_year)} accent />
            <Stat label="Min reserve margin" value={fmt.pct(h.min_reserve_margin)} hint={`Dependence ${fmt.pct(h.dependence_share_horizon)}`} />
          </div>

          {user?.role === "ADMIN" && s.status === "pending_review" && <AdminDecisionPanel s={s} onDone={reload} />}

          <Card title="Strategy explanation" subtitle={`Source: ${s.explanation?.source ?? "—"} · numbers come from the validated optimization run`}>
            {s.explanation && (
              <div className="grid gap-4 text-sm md:grid-cols-2">
                <p className="md:col-span-2">{s.explanation.summary}</p>
                <div><div className="label">What</div>{s.explanation.what}</div>
                <div><div className="label">Why</div>{s.explanation.why}</div>
                <div className="md:col-span-2"><div className="label">When</div>{s.explanation.when}</div>
              </div>
            )}
            <div className="mt-5 grid gap-4 md:grid-cols-3">
              <div><div className="label text-emerald-700">Advantages</div><ul className="list-disc space-y-1 pl-5 text-sm">{rep.advantages.length ? rep.advantages.map((a, i) => <li key={i}>{a}</li>) : <li className="text-ink-soft">No metric where this option is the best among the alternatives.</li>}</ul></div>
              <div><div className="label text-red-700">Disadvantages</div><ul className="list-disc space-y-1 pl-5 text-sm">{rep.disadvantages.length ? rep.disadvantages.map((a, i) => <li key={i}>{a}</li>) : <li className="text-ink-soft">None identified relative to the alternatives.</li>}</ul></div>
              <div><div className="label text-amber-700">Risks & uncertainties</div><ul className="list-disc space-y-1 pl-5 text-sm">{rep.risks.map((a, i) => <li key={i}>{a}</li>)}</ul></div>
            </div>
          </Card>

          <Tabs active={tab} onChange={setTab} tabs={[{ id: "roadmap", label: "Implementation roadmap" }, { id: "charts", label: "Capacity & generation" }, { id: "math", label: "Mathematical results" }, { id: "assumptions", label: "Assumptions & provenance" }, { id: "history", label: "Decisions & versions" }]} />

          {tab === "roadmap" && (
            <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
              <Card title="Annual roadmap" subtitle="What is commissioned each year, with resulting indicators">
                <ol className="relative border-l border-line pl-6">
                  {rep.roadmap.map((r) => (
                    <li key={r.year} className="mb-5">
                      <span className={`absolute -left-[7px] mt-1 h-3.5 w-3.5 rounded-full border-2 border-white ${r.investment_musd > 0 ? "bg-gold" : "bg-line"}`} />
                      <div className="flex flex-wrap items-baseline gap-x-3"><span className="font-semibold">{r.year}</span><span className="num text-xs text-ink-soft">{fmt.num(r.investment_musd)} MUSD · RE {fmt.pct(r.renewable_share)} · {fmt.num(r.emissions_mt, 2)} Mt · reserve {fmt.pct(r.reserve_margin)}</span></div>
                      <ul className="text-sm text-ink">{r.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
                    </li>
                  ))}
                </ol>
              </Card>
              <Card title="Milestones">
                <div className="space-y-3">
                  {rep.milestones.map((m) => {
                    const ms = m as { year: number; renewable_share: number; emissions_mt: number; cumulative_investment_musd: number; capacity_mw: Record<string, number>; demand_gwh: number };
                    return (
                      <div key={ms.year} className="rounded-lg border border-line p-3">
                        <div className="font-semibold text-gold-dark">{ms.year}</div>
                        <div className="text-xs text-ink-soft">Demand {fmt.num(ms.demand_gwh)} GWh · cumulative investment {fmt.num(ms.cumulative_investment_musd)} MUSD</div>
                        <div className="mt-1 text-xs">RE {fmt.pct(ms.renewable_share)} · CO2 {fmt.num(ms.emissions_mt, 2)} Mt</div>
                        <div className="mt-1 text-xs text-ink-soft">{Object.entries(ms.capacity_mw).map(([t, v]) => `${t} ${fmt.num(v)} MW`).join(" · ")}</div>
                      </div>
                    );
                  })}
                </div>
                {rep.tradeoff_vs_least_cost && (
                  <div className="mt-4 rounded-lg bg-gold-light/50 p-3 text-xs">
                    <div className="mb-1 font-semibold">Versus the least-cost plan</div>
                    <div>Additional cost: {fmt.num(rep.tradeoff_vs_least_cost.additional_cost_npv_musd as number)} MUSD ({fmt.num(rep.tradeoff_vs_least_cost.additional_cost_pct as number, 2)} %)</div>
                    <div>Emissions avoided: {fmt.num(rep.tradeoff_vs_least_cost.emissions_avoided_mt as number, 2)} Mt</div>
                    <div>Implied abatement cost: {rep.tradeoff_vs_least_cost.implied_abatement_cost_usd_per_t != null ? `${fmt.num(rep.tradeoff_vs_least_cost.implied_abatement_cost_usd_per_t as number)} USD/tCO2` : "—"}</div>
                  </div>
                )}
              </Card>
            </div>
          )}
          {tab === "charts" && annual.length > 0 && (
            <div className="grid gap-6 xl:grid-cols-2">
              <Card title="Installed capacity (MW)"><CapacityArea rows={annualToTech(annual, "capacity_mw")} /></Card>
              <Card title="New capacity per year (MW)"><StackedTechBars rows={annualToTech(annual, "new_capacity_mw")} unit="MW" /></Card>
              <Card title="Generation (GWh)"><StackedTechBars rows={annualToTech(annual, "generation_gwh", true)} unit="GWh" /></Card>
              <Card title="Demand–supply balance & emissions">
                <ShareLines rows={annual as unknown as Record<string, number>[]} series={[{ key: "renewable_share", name: "Renewable share", color: "#C6A04A" }, { key: "gas_share", name: "Gas share", color: "#a0563a" }, { key: "reserve_margin_achieved", name: "Reserve margin", color: "#171717" }]} />
              </Card>
            </div>
          )}
          {tab === "math" && s.run && (
            <Card title="Optimization run" subtitle={<>Method {s.run.method} · <Link className="underline" to={`/runs/${s.run.id}`}>open full run</Link></>}>
              <ConstraintTable result={{ validation: s.run.validation, solver: s.run.solver } as unknown as RunResult} />
              {s.run.formulation && <p className="mt-4 text-sm"><b>Objective:</b> {s.run.formulation.objective}</p>}
              {s.run.notes && s.run.notes.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-ink-soft">{s.run.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
            </Card>
          )}
          {tab === "assumptions" && (
            <Card title="Assumptions & data provenance">
              <ul className="list-disc space-y-1 pl-5 text-sm">{rep.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
              {s.run?.provenance && (
                <table className="mt-4 w-full"><tbody>{s.run.provenance.map((p) => <tr key={p.source}><td className="td">{p.source}</td><td className="td"><DataStatusBadge status={p.data_status} compact /></td><td className="td font-mono text-[11px]">{p.dataset_version}</td><td className="td text-xs text-ink-soft">{p.path}</td></tr>)}</tbody></table>
              )}
            </Card>
          )}
          {tab === "history" && (
            <Card title="Decision audit trail and versions">
              {s.decisions.length === 0 ? <p className="text-sm text-ink-soft">No decision recorded yet.</p> : (
                <table className="w-full"><thead><tr><th className="th">Date</th><th className="th">Decision</th><th className="th">Administrator</th><th className="th">Comment / reason</th></tr></thead>
                  <tbody>{s.decisions.map((d) => <tr key={d.id}><td className="td text-xs">{fmt.date(d.created_at)}</td><td className="td"><StatusBadge status={d.decision} /></td><td className="td text-xs">{d.admin_email}</td><td className="td text-sm">{d.comment || "—"}</td></tr>)}</tbody></table>
              )}
              <div className="label mt-5">Versions</div>
              <div className="flex flex-wrap gap-2">{s.versions.map((v) => <Link key={v.id} to={`/strategies/${v.id}`} className={`rounded border px-2 py-1 text-xs ${v.id === s.id ? "border-gold bg-gold-light" : "border-line"}`}>v{v.version} · {v.status}</Link>)}</div>
            </Card>
          )}
        </div>
      )}
      <div className="mt-6"><Link to={`/strategies?request=${s.planning_request_id}`} className="btn-outline">Other alternatives of this request</Link> <Link to={`/agent/${s.planning_request_id}`} className="btn-outline">Planning conversation</Link></div>
    </>
  );
}

function Badges({ s }: { s: SD }) {
  return (
    <>
      <StatusBadge status={s.status} />
      {s.recommended && <span className="rounded-full bg-gold px-2 py-0.5 text-[11px] font-semibold text-ink">RECOMMENDED</span>}
      {s.selected_by_requester && <span className="rounded-full bg-ink px-2 py-0.5 text-[11px] font-semibold text-white">REQUESTER'S CHOICE</span>}
      <DataStatusBadge status={s.data_status} />
    </>
  );
}

/** Short-term operating strategy (MILP): schedule of units in service, monthly dispatch, validation. */
function OperationalStrategy({ s, reload, isAdmin }: { s: SD; reload: () => void; isAdmin: boolean }) {
  const [tab, setTab] = useState("schedule");
  const rep = s.report!;
  const h = rep.headline;
  const monthly = s.run?.monthly ?? [];
  const trade = rep.tradeoff_vs_least_cost;
  if (h.kind !== "operational") return null;
  return (
    <>
      <PageHeader eyebrow={`Operating strategy · version ${s.version}`} title={s.name} subtitle={s.strategic_objective} actions={<Badges s={s} />} />
      {s.data_status !== "REAL" && <div className="mb-4"><Notice tone="warn">Based on MOCK inputs: this schedule illustrates the method and must not be used to operate the real system.</Notice></div>}
      <div className="space-y-6">
        <OperationalStats h={h} />
        {isAdmin && s.status === "pending_review" && <AdminDecisionPanel s={s} onDone={reload} />}
        <Card title="Strategy explanation" subtitle={`Source: ${s.explanation?.source ?? "—"} · numbers come from the validated MILP solution`}>
          {s.explanation && (
            <div className="grid gap-4 text-sm md:grid-cols-2">
              <p className="md:col-span-2">{s.explanation.summary}</p>
              <div><div className="label">What</div>{s.explanation.what}</div>
              <div><div className="label">Why</div>{s.explanation.why}</div>
              <div className="md:col-span-2"><div className="label">When</div>{s.explanation.when}</div>
            </div>
          )}
          <div className="mt-5 grid gap-4 md:grid-cols-3">
            <div><div className="label text-emerald-700">Advantages</div><ul className="list-disc space-y-1 pl-5 text-sm">{rep.advantages.length ? rep.advantages.map((a, i) => <li key={i}>{a}</li>) : <li className="text-ink-soft">No metric where this option is the best among the alternatives.</li>}</ul></div>
            <div><div className="label text-red-700">Disadvantages</div><ul className="list-disc space-y-1 pl-5 text-sm">{rep.disadvantages.length ? rep.disadvantages.map((a, i) => <li key={i}>{a}</li>) : <li className="text-ink-soft">None identified relative to the alternatives.</li>}</ul></div>
            <div><div className="label text-amber-700">Risks & uncertainties</div><ul className="list-disc space-y-1 pl-5 text-sm">{rep.risks.map((a, i) => <li key={i}>{a}</li>)}</ul></div>
          </div>
          {trade && (
            <div className="mt-4 rounded-lg bg-gold-light/50 p-3 text-xs">
              <div className="mb-1 font-semibold">Versus the least-cost operation</div>
              <div>Additional operating cost: {fmt.num(trade.additional_operating_cost_musd as number, 2)} MUSD ({fmt.num(trade.additional_cost_pct as number, 2)} %)</div>
              <div>Gas generation saved: {fmt.num(trade.gas_saved_gwh as number, 1)} GWh · unserved energy reduced: {fmt.num(trade.unserved_reduced_gwh as number, 1)} GWh · reserve margin {fmt.num(trade.reserve_margin_change_pp as number, 1)} pp</div>
            </div>
          )}
        </Card>
        <Tabs active={tab} onChange={setTab} tabs={[{ id: "schedule", label: "Monthly schedule" }, { id: "charts", label: "Dispatch & units" }, { id: "math", label: "Mathematical results" }, { id: "assumptions", label: "Assumptions & provenance" }, { id: "history", label: "Decisions & versions" }]} />
        {tab === "schedule" && rep.schedule && <Card title="What to do each month"><ScheduleTimeline schedule={rep.schedule} /></Card>}
        {tab === "charts" && monthly.length > 0 && <OperationalCharts monthly={monthly} />}
        {tab === "math" && s.run && (
          <Card title="Optimization run" subtitle={<>Method {s.run.method} · <Link className="underline" to={`/runs/${s.run.id}`}>open full run</Link></>}>
            <OperationalValidationView v={s.run.validation as unknown as OperationalValidation} solver={s.run.solver as never} />
            {s.run.formulation && <p className="mt-4 text-sm"><b>Objective:</b> {s.run.formulation.objective}</p>}
            {s.run.notes && s.run.notes.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-ink-soft">{s.run.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
          </Card>
        )}
        {tab === "assumptions" && (
          <Card title="Assumptions & data provenance">
            <ul className="list-disc space-y-1 pl-5 text-sm">{rep.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            {s.run?.provenance && <table className="mt-4 w-full"><tbody>{s.run.provenance.map((p) => <tr key={p.source}><td className="td">{p.source}</td><td className="td"><DataStatusBadge status={p.data_status} compact /></td><td className="td font-mono text-[11px]">{p.dataset_version}</td><td className="td text-xs text-ink-soft">{p.path}</td></tr>)}</tbody></table>}
          </Card>
        )}
        {tab === "history" && (
          <Card title="Decision audit trail and versions">
            {s.decisions.length === 0 ? <p className="text-sm text-ink-soft">No decision recorded yet.</p> : (
              <table className="w-full"><thead><tr><th className="th">Date</th><th className="th">Decision</th><th className="th">Administrator</th><th className="th">Comment / reason</th></tr></thead>
                <tbody>{s.decisions.map((d) => <tr key={d.id}><td className="td text-xs">{fmt.date(d.created_at)}</td><td className="td"><StatusBadge status={d.decision} /></td><td className="td text-xs">{d.admin_email}</td><td className="td text-sm">{d.comment || "—"}</td></tr>)}</tbody></table>
            )}
          </Card>
        )}
      </div>
      <div className="mt-6"><Link to={`/strategies?request=${s.planning_request_id}`} className="btn-outline">Other alternatives of this request</Link> <Link to={`/agent/${s.planning_request_id}`} className="btn-outline">Conversation</Link></div>
    </>
  );
}
