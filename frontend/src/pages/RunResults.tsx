import { Link, useParams } from "react-router-dom";
import type { RunDetail, RunResult } from "../api/types";
import { CapacityArea, ShareLines, StackedTechBars, annualToTech } from "../components/charts";
import { useApi } from "../components/hooks";
import { Card, Check, DataStatusBadge, ErrorState, KindBadge, Loading, Notice, PageHeader, Stat, StatusBadge, fmt } from "../components/ui";

export function ConstraintTable({ result }: { result: RunResult }) {
  const v = result.validation;
  if (!v) return null;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div>
        <div className="label">Constraint satisfaction</div>
        <table className="w-full">
          <thead><tr><th className="th">Constraint</th><th className="th">Scope</th><th className="th text-right">Value</th><th className="th text-right">Limit</th><th className="th" /></tr></thead>
          <tbody>
            {v.constraints.map((c, i) => (
              <tr key={i}>
                <td className="td">{c.constraint}</td><td className="td text-ink-soft">{c.scope}</td>
                <td className="td num text-right">{fmt.num(c.value, 2)} {c.unit}</td>
                <td className="td num text-right">{c.relation} {fmt.num(c.limit, 2)}</td>
                <td className="td"><Check ok={c.satisfied} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <div className="label">Independent validation</div>
        <ul className="space-y-2 text-sm">
          {v.physical_checks.map((c) => <li key={c.check} className="flex items-center gap-2"><Check ok={c.passed} />{c.check}</li>)}
          <li className="flex items-center gap-2"><Check ok={Object.values(v.objective_consistency).every((o) => o.consistent)} />Objective values recomputed outside the solver</li>
          <li className="flex items-center gap-2"><Check ok={v.simulation_check.adequate} />Merit-order re-simulation: {fmt.num(v.simulation_check.unserved_gwh, 2)} GWh unserved</li>
        </ul>
        {result.solver && (
          <>
            <div className="label mt-5">Solver log ({result.solver.name})</div>
            <table className="w-full">
              <tbody>{result.solver.solves.map((s, i) => <tr key={i}><td className="td font-mono text-[11px]">{s.step}</td><td className="td"><StatusBadge status={s.status} /></td><td className="td num text-right text-xs">{s.seconds}s</td></tr>)}</tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

export function ResultCharts({ result }: { result: RunResult }) {
  const annual = result.results!.annual;
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Card title="Installed capacity evolution (MW)"><CapacityArea rows={annualToTech(annual, "capacity_mw")} /></Card>
      <Card title="Investment decisions — new capacity (MW)"><StackedTechBars rows={annualToTech(annual, "new_capacity_mw")} unit="MW" /></Card>
      <Card title="Generation by technology (GWh)" subtitle="Imports shown separately"><StackedTechBars rows={annualToTech(annual, "generation_gwh", true)} unit="GWh" /></Card>
      <Card title="Renewable penetration, gas and import dependence">
        <ShareLines rows={annual as unknown as Record<string, number>[]} series={[
          { key: "renewable_share", name: "Renewable share", color: "#C6A04A" },
          { key: "gas_share", name: "Gas share", color: "#a0563a" },
          { key: "dependence_share", name: "Dependence (gas + imports)", color: "#171717" },
        ]} />
      </Card>
      <Card title="CO2 emissions (MtCO2/yr)"><ShareLines unit="Mt" rows={annual as unknown as Record<string, number>[]} series={[{ key: "emissions_mt", name: "Emissions", color: "#171717" }]} /></Card>
      <Card title="Investment requirements (MUSD/yr, overnight)"><ShareLines unit="MUSD" rows={annual as unknown as Record<string, number>[]} series={[{ key: "investment_musd", name: "Investment", color: "#A98232" }]} /></Card>
    </div>
  );
}

export default function RunResults() {
  const { runId } = useParams();
  const { data, loading, error, reload } = useApi<RunDetail>(`/api/optimization/${runId}`);
  if (loading) return <Loading />;
  if (error || !data) return <ErrorState message={error ?? "Not found"} onRetry={reload} />;
  const r = data.result;
  const s = r.results?.summary;

  return (
    <>
      <PageHeader
        eyebrow={data.kind === "simulation" ? "Simulation output" : data.kind === "pareto" ? "Pareto analysis" : "Optimization output"}
        title={data.scenario_name}
        subtitle={<>{r.message} · {fmt.date(data.created_at)} · {data.duration_seconds}s</>}
        actions={<><KindBadge kind={data.kind} /><StatusBadge status={data.status} />{r.validation && <StatusBadge status={r.validation.status} />}<DataStatusBadge status={data.data_status} /></>}
      />
      {data.data_status !== "REAL" && <div className="mb-4"><Notice tone="warn">Computed from MOCK inputs — illustrative results, not an investment recommendation.</Notice></div>}

      {data.kind === "pareto" ? <ParetoView r={r} /> : !r.results ? (
        <Card title="No solution reported">
          <p className="text-sm">{r.message}</p>
          {r.diagnostics && (
            <ul className="mt-3 space-y-1 text-sm">{r.diagnostics.violations.map((v, i) => <li key={i}>• <b>{v.title}</b>{v.years.length ? ` (${v.years[0]}–${v.years[v.years.length - 1]})` : ""}: {fmt.num(v.total_shortfall, 1)} {v.unit}</li>)}</ul>
          )}
          <p className="mt-3 text-xs text-ink-soft">The system never fabricates results when the solver does not return an optimal solution.</p>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="Total system cost (NPV)" value={fmt.num(s!.total_cost_npv_musd)} unit="MUSD" accent />
            <Stat label="Investment required" value={fmt.num(s!.total_investment_musd)} unit="MUSD" hint="Overnight, undiscounted" />
            <Stat label="Cumulative CO2" value={fmt.num(s!.total_emissions_mt, 1)} unit="Mt" hint={`${fmt.num(s!.emission_change_vs_2024_pct, 1)} % vs 2024 in ${s!.final_year}`} />
            <Stat label={`Renewable share ${s!.final_year}`} value={fmt.pct(s!.renewable_share_final_year)} hint={`Gas ${fmt.pct(s!.gas_share_final_year)} · dependence ${fmt.pct(s!.dependence_share_final_year)}`} />
          </div>
          {data.kind === "simulation" && r.results.adequacy_issues && r.results.adequacy_issues.length > 0 && (
            <Notice tone="warn"><b>Adequacy issues:</b> {r.results.adequacy_issues.slice(0, 5).join("; ")}{r.results.adequacy_issues.length > 5 ? "…" : ""}</Notice>
          )}
          {r.objective_values && (
            <Card title="Objective values" subtitle={`Method: ${r.method}${r.notes?.length ? " · " + r.notes.join(" · ") : ""}`}>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-5 text-sm">
                {Object.entries(r.objective_values).map(([k, v]) => <div key={k}><div className="text-xs text-ink-soft">{k}</div><div className="num font-medium">{fmt.num(v, 1)}</div></div>)}
              </div>
            </Card>
          )}
          <ResultCharts result={r} />
          {r.validation && <Card title="Validation & constraint satisfaction"><ConstraintTable result={r} /></Card>}
          <Card title="Scenario parameters & provenance">
            <div className="grid gap-6 lg:grid-cols-2">
              <ul className="list-disc space-y-1 pl-5 text-xs">{r.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
              <table className="w-full"><tbody>{r.provenance.map((p) => <tr key={p.source}><td className="td text-xs">{p.source}</td><td className="td"><DataStatusBadge status={p.data_status} compact /></td><td className="td font-mono text-[11px]">{p.dataset_version}</td></tr>)}</tbody></table>
            </div>
            <details className="mt-4 text-xs"><summary className="cursor-pointer text-ink-soft">Full scenario configuration (JSON)</summary><pre className="mt-2 overflow-auto rounded bg-paper-soft p-3">{JSON.stringify(r.scenario, null, 2)}</pre></details>
          </Card>
          <div className="flex gap-2"><Link to={`/compare?runs=${data.id}`} className="btn-outline">Compare with other runs</Link></div>
        </div>
      )}
    </>
  );
}

function ParetoView({ r }: { r: RunResult }) {
  const pts = (r.points ?? []).filter((p) => p.status === "optimal");
  if (!pts.length) return <Card title="No Pareto frontier"><p className="text-sm">{r.message ?? "Problem infeasible."}</p></Card>;
  return (
    <Card title={`Pareto frontier: ${r.primary} vs ${r.secondary}`} subtitle="Epsilon-constraint sweep from the least-cost plan to the minimum-emission plan">
      <ShareLines unit="MUSD" rows={pts.map((p, i) => ({ year: i + 1, cost: p.objective_values!.min_cost }))} series={[{ key: "cost", name: "NPV cost at each emission cap", color: "#171717" }]} />
      <table className="mt-4 w-full">
        <thead><tr><th className="th">#</th><th className="th text-right">Emission cap (Mt)</th><th className="th text-right">CO2 (Mt)</th><th className="th text-right">NPV cost (MUSD)</th><th className="th text-right">Investment</th><th className="th text-right">RE final</th></tr></thead>
        <tbody>{pts.map((p, i) => (
          <tr key={i}><td className="td">{i + 1}</td><td className="td num text-right">{fmt.num(p.epsilon, 2)}</td><td className="td num text-right">{fmt.num(p.objective_values!.min_emissions, 2)}</td><td className="td num text-right">{fmt.num(p.objective_values!.min_cost)}</td><td className="td num text-right">{fmt.num(p.total_investment_musd)}</td><td className="td num text-right">{fmt.pct(p.renewable_share_final_year)}</td></tr>
        ))}</tbody>
      </table>
    </Card>
  );
}
