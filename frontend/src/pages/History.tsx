import { useState } from "react";
import { Link } from "react-router-dom";
import type { Decision, PlanningRequest, RunSummary, Scenario } from "../api/types";
import { useApi } from "../components/hooks";
import { Empty, ErrorState, Loading, PageHeader, StatusBadge, Tabs, fmt } from "../components/ui";
import { RunsTable } from "./RunsList";

interface HistoryData { runs: RunSummary[]; planning_requests: PlanningRequest[]; scenarios: Scenario[]; decisions: Decision[] }

export default function HistoryPage() {
  const { data, loading, error, reload } = useApi<HistoryData>("/api/history");
  const [tab, setTab] = useState("runs");
  return (
    <>
      <PageHeader eyebrow="Traceability" title="History" subtitle="Previous runs, planning requests, custom scenarios and decisions, with their parameters and timestamps." />
      {loading ? <Loading /> : error ? <ErrorState message={error} onRetry={reload} /> : (
        <>
          <Tabs active={tab} onChange={setTab} tabs={[
            { id: "runs", label: `Runs (${data!.runs.length})` },
            { id: "requests", label: `Planning requests (${data!.planning_requests.length})` },
            { id: "scenarios", label: `Custom scenarios (${data!.scenarios.length})` },
            { id: "decisions", label: `Decisions (${data!.decisions.length})` },
          ]} />
          {tab === "runs" && (data!.runs.length ? <RunsTable runs={data!.runs} /> : <Empty title="No runs yet" />)}
          {tab === "requests" && (data!.planning_requests.length ? (
            <div className="card overflow-x-auto"><table className="w-full">
              <thead><tr><th className="th">Request</th><th className="th">Status</th><th className="th">Strategies</th><th className="th">Owner</th><th className="th">Updated</th></tr></thead>
              <tbody>{data!.planning_requests.map((r) => <tr key={r.id}><td className="td"><Link className="font-medium hover:text-gold-dark" to={`/agent/${r.id}`}>{r.title}</Link></td><td className="td"><StatusBadge status={r.status} /></td><td className="td">{r.strategy_ids.length ? <Link className="text-gold-dark underline" to={`/strategies?request=${r.id}`}>{r.strategy_ids.length}</Link> : 0}</td><td className="td text-xs">{r.owner_email}</td><td className="td text-xs text-ink-soft">{fmt.date(r.updated_at)}</td></tr>)}</tbody>
            </table></div>) : <Empty title="No planning requests yet" />)}
          {tab === "scenarios" && (data!.scenarios.length ? (
            <div className="space-y-3">{data!.scenarios.map((s) => (
              <details key={s.id} className="card p-4"><summary className="cursor-pointer text-sm font-medium">{s.name} <span className="text-xs text-ink-soft">· {s.config.start_year}–{s.config.end_year} · {fmt.date(s.created_at)}</span></summary>
                <pre className="mt-3 overflow-auto rounded bg-paper-soft p-3 text-xs">{JSON.stringify(s.config, null, 2)}</pre></details>
            ))}</div>) : <Empty title="No custom scenarios" />)}
          {tab === "decisions" && (data!.decisions.length ? (
            <div className="card overflow-x-auto"><table className="w-full">
              <thead><tr><th className="th">Date</th><th className="th">Strategy</th><th className="th">Decision</th><th className="th">Administrator</th><th className="th">Comment</th></tr></thead>
              <tbody>{data!.decisions.map((d) => <tr key={d.id}><td className="td text-xs">{fmt.date(d.created_at)}</td><td className="td"><Link className="hover:text-gold-dark" to={`/strategies/${d.strategy_id}`}>{d.strategy_name} v{d.strategy_version}</Link></td><td className="td"><StatusBadge status={d.decision} /></td><td className="td text-xs">{d.admin_email}</td><td className="td text-sm">{d.comment || "—"}</td></tr>)}</tbody>
            </table></div>) : <Empty title="No decisions yet" />)}
        </>
      )}
    </>
  );
}
