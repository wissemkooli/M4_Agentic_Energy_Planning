import { Link } from "react-router-dom";
import type { RunSummary } from "../api/types";
import { useApi } from "../components/hooks";
import { DataStatusBadge, Empty, ErrorState, KindBadge, Loading, PageHeader, StatusBadge, fmt } from "../components/ui";

export function RunsTable({ runs }: { runs: RunSummary[] }) {
  return (
    <div className="card overflow-x-auto">
      <table className="w-full min-w-[760px]">
        <thead><tr><th className="th">Scenario / strategy</th><th className="th">Type</th><th className="th">Status</th><th className="th text-right">NPV cost</th><th className="th text-right">CO2 (Mt)</th><th className="th text-right">RE final</th><th className="th">Data</th><th className="th">Date</th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="hover:bg-paper-soft">
              <td className="td"><Link className="font-medium hover:text-gold-dark" to={`/runs/${r.id}`}>{r.scenario_name}</Link><div className="text-[11px] text-ink-soft">{r.method}</div></td>
              <td className="td"><KindBadge kind={r.kind} /></td>
              <td className="td"><StatusBadge status={r.status} /></td>
              {r.kind === "operational" ? (() => {
                const o = r.summary as unknown as { operating_cost_musd?: number; emissions_mt?: number; renewable_share?: number } | null;
                return (<>
                  <td className="td num text-right" title="Operating cost over the period (not NPV)">{fmt.num(o?.operating_cost_musd, 1)}<span className="text-[10px] text-ink-soft"> op.</span></td>
                  <td className="td num text-right">{fmt.num(o?.emissions_mt, 2)}</td>
                  <td className="td num text-right">{fmt.pct(o?.renewable_share)}</td>
                </>);
              })() : (<>
                <td className="td num text-right">{fmt.num(r.summary?.total_cost_npv_musd)}</td>
                <td className="td num text-right">{fmt.num(r.summary?.total_emissions_mt, 1)}</td>
                <td className="td num text-right">{fmt.pct(r.summary?.renewable_share_final_year)}</td>
              </>)}
              <td className="td"><DataStatusBadge status={r.data_status} compact /></td>
              <td className="td text-xs text-ink-soft">{fmt.date(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function RunsList() {
  const { data, loading, error, reload } = useApi<RunSummary[]>("/api/optimization?limit=200");
  return (
    <>
      <PageHeader eyebrow="Solver outputs" title="Optimization results" subtitle="Every simulation, optimization (long-term LP and short-term operational MILP) and Pareto analysis stored by the backend, with status and provenance." />
      {loading ? <Loading /> : error ? <ErrorState message={error} onRetry={reload} /> :
        data!.length === 0 ? <Empty title="No results yet" action={<Link to="/scenarios" className="btn-primary">Open scenario builder</Link>}>Run a scenario or ask the planning agent.</Empty> : <RunsTable runs={data!} />}
    </>
  );
}
