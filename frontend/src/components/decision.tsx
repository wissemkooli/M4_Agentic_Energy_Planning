import { AlertTriangle, Award, CheckCircle2, ChevronDown, ChevronRight, Star, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { DecisionReport, FormulationValidation, LlmUsage, StrategySummary, Understanding, UserSelection } from "../api/types";
import { Card, DataStatusBadge, fmt } from "./ui";

/** How the agent understood the request (objective, decisions, resources, constraints, data, missing information). */
export function UnderstandingCard({ u }: { u: Understanding }) {
  const rows: [string, string[]][] = [
    ["Objectives", u.objectives], ["Decision variables", u.decision_variables], ["Resources", u.resources],
    ["Constraints", u.constraints], ["Relevant data", u.relevant_data],
  ];
  return (
    <Card title="Problem understanding" subtitle={`${u.problem_type_label} · ${u.time_horizon} · ${u.interpreted_by}`}>
      <dl className="space-y-3 text-xs">
        {rows.map(([k, items]) => (
          <div key={k}><dt className="label mb-1">{k}</dt><dd><ul className="space-y-0.5">{items.map((x, i) => <li key={i} className="flex gap-1.5"><ChevronRight size={12} className="mt-0.5 shrink-0 text-gold" />{x}</li>)}</ul></dd></div>
        ))}
        {u.missing_information.length > 0 && (
          <div><dt className="label mb-1 text-amber-700">Missing information</dt><dd><ul className="space-y-0.5 text-amber-800">{u.missing_information.map((x, i) => <li key={i}>{x}</li>)}</ul></dd></div>
        )}
      </dl>
    </Card>
  );
}

const ICON = { pass: <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-emerald-600" />, warn: <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-600" />, fail: <XCircle size={14} className="mt-0.5 shrink-0 text-red-600" /> };

/** Formulation checks run before human approval (units, provenance, objective coverage, coherence, feasibility). */
export function FormulationChecks({ v }: { v: FormulationValidation }) {
  return (
    <div>
      <div className="label">Formulation validation (before optimization)</div>
      <ul className="space-y-1.5 text-xs">
        {v.checks.map((c) => (
          <li key={c.check} className="flex gap-2">{ICON[c.status]}<div><span className="font-medium">{c.check}</span><div className="text-ink-soft">{c.detail}</div></div></li>
        ))}
      </ul>
    </div>
  );
}

export function LlmUsageCard({ usage, calls }: { usage: LlmUsage; calls?: { purpose: string; model_used: string | null; status: string; total_tokens: number | null; seconds: number | null }[] }) {
  if (!usage.calls) return null;
  return (
    <Card title="LLM usage for this request" subtitle="Interpretation, tool selection and narrative only — never numbers">
      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div><div className="num text-lg font-semibold">{usage.successful}/{usage.calls}</div><div className="text-ink-soft">calls ok</div></div>
        <div><div className="num text-lg font-semibold">{fmt.num(usage.total_tokens)}</div><div className="text-ink-soft">tokens</div></div>
        <div><div className="num text-lg font-semibold">{fmt.num(usage.seconds, 1)}s</div><div className="text-ink-soft">latency</div></div>
      </div>
      {calls && calls.length > 0 && (
        <ul className="mt-3 space-y-1 text-[11px]">
          {calls.map((c, i) => <li key={i} className="flex justify-between gap-2"><span className="font-mono">{c.purpose}</span><span className={c.status === "ok" ? "text-ink-soft" : "text-red-700"}>{c.status === "ok" ? `${c.model_used ?? ""} · ${c.total_tokens ?? 0} tok` : "failed → deterministic fallback"}</span></li>)}
        </ul>
      )}
    </Card>
  );
}

const METRIC_LABELS: Record<string, [string, "num" | "pct", number, string]> = {
  operating_cost_musd: ["Operating cost", "num", 1, "MUSD"], unserved_gwh: ["Unserved", "num", 1, "GWh"], gas_gwh: ["Gas", "num", 0, "GWh"],
  imports_gwh: ["Imports", "num", 0, "GWh"], min_reserve_margin: ["Min reserve", "pct", 1, ""],
  total_cost_npv_musd: ["NPV cost", "num", 0, "MUSD"], total_investment_musd: ["Investment", "num", 0, "MUSD"],
  total_emissions_mt: ["CO2", "num", 1, "Mt"], renewable_share_final_year: ["RE final year", "pct", 1, ""], dependence_share_horizon: ["Dependence", "pct", 1, ""],
};

/** Human-readable decision report: options, recommendation under the stated priorities, human selection. */
export function DecisionReportView({ report, strategies, selection, canSelect, busy, onSelect }: {
  report: DecisionReport; strategies: StrategySummary[]; selection?: UserSelection | null; canSelect: boolean; busy: boolean; onSelect: (strategyId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const byKey = new Map<string, StrategySummary>();
  strategies.filter((s) => !s.superseded_by).forEach((s) => byKey.set(s.key, s));
  const rec = report.recommendation;
  const c = report.computation;
  return (
    <div className="card mt-2 space-y-4 p-4 text-left">
      <div className="flex flex-wrap items-center gap-2 text-xs"><span className="font-semibold uppercase tracking-wide text-gold-dark">Decision support report</span>
        <span className="text-ink-soft">{c.solves} HiGHS solves · {c.n_distinct} distinct option(s) · {c.all_validated ? "all validated" : "see validation"}</span>
        {report.data_used[0] && <DataStatusBadge status={report.data_used[0].data_status} compact />}
      </div>
      {rec && (
        <div className="rounded-lg border border-gold/60 bg-gold-light/40 p-3">
          <div className="flex items-center gap-2 text-sm font-semibold"><Award size={16} className="text-gold-dark" />Recommended under your stated priorities: {rec.strategy_name}</div>
          <p className="mt-1 text-sm">{rec.reason}</p>
          <p className="mt-1 text-xs text-ink-soft">{rec.statement}</p>
          {rec.alternatives.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
              {rec.alternatives.map((a) => <span key={a.criterion} className="rounded border border-line bg-white px-1.5 py-0.5">{a.criterion}: <b>{a.strategy_name}</b></span>)}
            </div>
          )}
        </div>
      )}
      <div className="grid gap-3">
        {report.options.map((o) => {
          const s = byKey.get(o.key);
          const preferred = selection?.strategy_key === o.key;
          return (
            <div key={o.key} className={`rounded-lg border p-3 ${preferred ? "border-ink ring-1 ring-ink/20" : "border-line"}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="font-medium">{o.name}</div>
                <div className="flex shrink-0 gap-1">
                  {rec?.strategy_key === o.key && <span className="rounded-full bg-gold px-2 py-0.5 text-[10px] font-semibold text-ink">RECOMMENDED</span>}
                  {preferred && <span className="rounded-full bg-ink px-2 py-0.5 text-[10px] font-semibold text-white">YOUR CHOICE</span>}
                </div>
              </div>
              <div className="mt-0.5 text-xs text-ink-soft">{o.objective}</div>
              <dl className="mt-2 grid grid-cols-3 gap-x-3 gap-y-1 text-xs sm:grid-cols-5">
                {Object.entries(o.metrics).map(([k, v]) => {
                  const m = METRIC_LABELS[k];
                  if (!m) return null;
                  return <div key={k}><dt className="text-ink-soft">{m[0]}</dt><dd className="num font-medium">{m[1] === "pct" ? fmt.pct(v, m[2]) : `${fmt.num(v, m[2])} ${m[3]}`}</dd></div>;
                })}
              </dl>
              {o.advantages[0] && <div className="mt-2 text-xs text-emerald-800">+ {o.advantages[0]}</div>}
              {o.disadvantages[0] && <div className="text-xs text-red-800">− {o.disadvantages[0]}</div>}
              <div className="mt-2 flex flex-wrap gap-2">
                {s && <Link to={`/strategies/${s.id}`} className="btn-outline px-2 py-1 text-xs">Details</Link>}
                {s && canSelect && !preferred && (
                  <button className="btn-outline px-2 py-1 text-xs" disabled={busy} onClick={() => onSelect(s.id)}><Star size={12} /> Prefer this option</button>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {selection && <p className="text-xs text-ink-soft">Your preferred option: <b>{selection.strategy_name}</b>{selection.follows_recommendation ? " (follows the recommendation)" : " (differs from the recommendation)"}. An administrator records the final decision.</p>}
      <div className="border-t border-line pt-3">
        <button className="flex items-center gap-1 text-left text-sm font-medium" onClick={() => setOpen((x) => !x)} aria-expanded={open}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Full explanation in plain language
        </button>
        <div className="ml-5 text-[11px] text-ink-soft [overflow-wrap:anywhere]">Written by: {report.narrative.source.startsWith("llm:") ? report.narrative.source.slice(4) : "deterministic template (no LLM)"}</div>
        {open && (
          <div className="mt-2 space-y-2 text-sm">
            {report.narrative.text.split(/\n\n+/).map((p, i) => <p key={i}>{p}</p>)}
            {report.narrative.llm_note && <p className="text-xs text-ink-soft">LLM text not used: {report.narrative.llm_note}</p>}
            <div className="label mt-3">Data used</div>
            <table className="w-full"><tbody>{report.data_used.map((d) => <tr key={d.source}><td className="td text-xs">{d.source}</td><td className="td"><DataStatusBadge status={d.data_status} compact /></td><td className="td font-mono text-[11px]">{d.dataset_version}</td><td className="td text-xs text-ink-soft">{d.used_for}</td></tr>)}</tbody></table>
          </div>
        )}
      </div>
    </div>
  );
}
