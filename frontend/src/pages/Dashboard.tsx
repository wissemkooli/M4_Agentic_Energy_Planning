import { Link } from "react-router-dom";
import type { DataStatus, Provenance, RunSummary, StrategySummary } from "../api/types";
import { DemandChart, StackedTechBars, techColor } from "../components/charts";
import { useApi } from "../components/hooks";
import { Card, DataStatusBadge, ErrorState, KindBadge, Loading, Notice, PageHeader, Stat, StatusBadge, fmt } from "../components/ui";

interface DashboardData {
  data_status: DataStatus;
  provenance: Provenance[];
  reference_year: number;
  reference: { technologies: { technology: string; installed_capacity_mw: number; avg_available_capacity_mw: number; unavailable_capacity_mw: number; generation_gwh: number; observed_capacity_factor: number }[] };
  kpis: Record<string, number>;
  demand: { annual_forecast: { year: number; forecast_gwh: number; lower_80: number; upper_80: number; peak_avg_mw: number }[]; annual_actual: { year: number; actual_gwh: number }[]; model_label: string };
  benchmark: { indicator: string; unit: string; window: string; tunisia_mean: number; tunisia_rank: number; n_countries: number; peer_median: number; peer_max: number; best_country: string }[];
  data_quality: { n_errors: number; n_warnings: number; issues: { level: string; source: string; message: string }[] };
  recent_runs: RunSummary[];
  approved_reference_strategy: StrategySummary | null;
}

export default function Dashboard() {
  const { data, loading, error, reload } = useApi<DashboardData>("/api/dashboard");
  if (loading) return <Loading />;
  if (error || !data) return <ErrorState message={error ?? "No data"} onRetry={reload} />;
  const k = data.kpis;
  const techs = data.reference.technologies;
  const demandRows = [
    ...data.demand.annual_actual.map((a) => ({ year: a.year, actual_gwh: a.actual_gwh })),
    ...data.demand.annual_forecast.map((f) => ({ year: f.year, forecast_gwh: f.forecast_gwh, lower_80: f.lower_80, upper_80: f.upper_80 })),
  ];
  const capRows = [
    { year: "Installed" as unknown as number, ...Object.fromEntries(techs.map((t) => [t.technology, t.installed_capacity_mw])) },
    { year: "Available" as unknown as number, ...Object.fromEntries(techs.map((t) => [t.technology, t.avg_available_capacity_mw])) },
  ];
  const genTotal = techs.reduce((s, t) => s + t.generation_gwh, 0);

  return (
    <>
      <PageHeader
        eyebrow={`Reference year ${data.reference_year}`}
        title="Energy dashboard"
        subtitle="Initial state of the electricity system, M1 demand outlook and M3 international context. All values come from the backend input adapters."
        actions={<DataStatusBadge status={data.data_status} />}
      />
      {data.data_status !== "REAL" && (
        <div className="mb-6"><Notice tone="warn">These inputs are <b>synthetic / mock</b> (fictional reference system, synthetic M1 forecasts, fictional M3 comparators). They demonstrate the method and must not be quoted as Tunisian statistics.</Notice></div>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Installed capacity" value={fmt.num(k.installed_capacity_mw)} unit="MW" hint={`${fmt.num(k.available_capacity_mw)} MW available · ${fmt.num(k.unavailable_capacity_mw)} MW unavailable`} />
        <Stat label="Generation 2024" value={fmt.num(k.generation_gwh)} unit="GWh" hint={`Emission intensity ${fmt.num(k.emission_intensity_g_per_kwh)} gCO2/kWh`} />
        <Stat label="Renewable share" value={fmt.pct(k.renewable_share_generation)} hint={`Gas dependence ${fmt.pct(k.gas_share_generation)} of generation`} accent />
        <Stat label="CO2 emissions 2024" value={fmt.num(k.emissions_mt, 2)} unit="Mt" hint="Reference generation × emission factors" />
        <Stat label={`Demand ${k.first_forecast_year}`} value={fmt.num(k.first_forecast_year_demand_gwh)} unit="GWh" hint={`M1 forecast · model label ${data.demand.model_label}`} />
        <Stat label="Peak monthly average" value={fmt.num(k.first_forecast_year_peak_avg_mw)} unit="MW" hint="Highest monthly average demand (GWh→MW)" />
        <Stat label="Demand growth" value={k.forecast_cagr_pct !== null ? `${k.forecast_cagr_pct.toFixed(2)} %` : "—"} hint="CAGR over the M1 forecast horizon" />
        <Stat label="Data quality" value={`${data.data_quality.n_warnings} warnings`} hint={`${data.data_quality.n_errors} errors · see Data & assumptions`} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Electricity demand outlook (GWh/yr)" subtitle="Synthetic history and M1 forecast with 80 % prediction interval" actions={<DataStatusBadge status={data.provenance[0]?.data_status} compact />}>
          <DemandChart rows={demandRows} />
        </Card>
        <Card title={`Generation mix ${data.reference_year}`} subtitle={`${fmt.num(genTotal)} GWh`}>
          <div className="space-y-3">
            {techs.map((t) => (
              <div key={t.technology}>
                <div className="mb-1 flex justify-between text-sm"><span className="capitalize">{t.technology}</span><span className="num text-ink-soft">{fmt.num(t.generation_gwh)} GWh · {fmt.pct(t.generation_gwh / genTotal)}</span></div>
                <div className="h-2 rounded-full bg-paper-soft"><div className="h-2 rounded-full" style={{ width: `${(100 * t.generation_gwh) / genTotal}%`, background: techColor(t.technology) }} /></div>
              </div>
            ))}
          </div>
          <table className="mt-5 w-full">
            <thead><tr><th className="th">Tech</th><th className="th text-right">MW</th><th className="th text-right">CF</th></tr></thead>
            <tbody>{techs.map((t) => <tr key={t.technology}><td className="td capitalize">{t.technology}</td><td className="td num text-right">{fmt.num(t.installed_capacity_mw)}</td><td className="td num text-right">{fmt.pct(t.observed_capacity_factor)}</td></tr>)}</tbody>
          </table>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card title="Installed vs available capacity (MW)" subtitle="Availability reflects outages; capacity factor reflects resource and dispatch">
          <StackedTechBars rows={capRows} unit="MW" height={240} />
        </Card>
        <Card title="International benchmark (M3)" subtitle="Tunisia vs comparator countries — context only, not model inputs" actions={<DataStatusBadge status={data.provenance[1]?.data_status} compact />}>
          <table className="w-full">
            <thead><tr><th className="th">Indicator</th><th className="th text-right">Tunisia</th><th className="th text-right">Rank</th><th className="th text-right">Peer median</th></tr></thead>
            <tbody>
              {data.benchmark.map((b) => (
                <tr key={b.indicator}>
                  <td className="td"><div>{b.indicator.replace(/_/g, " ")}</div><div className="text-xs text-ink-soft">{b.unit} · {b.window}</div></td>
                  <td className="td num text-right font-medium">{fmt.num(b.tunisia_mean, 1)}</td>
                  <td className="td num text-right">{b.tunisia_rank}/{b.n_countries}</td>
                  <td className="td num text-right">{fmt.num(b.peer_median, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card title="Approved planning reference" subtitle="Set by an administrator; a recommendation, not an investment authorization">
          {data.approved_reference_strategy ? (
            <Link to={`/strategies/${data.approved_reference_strategy.id}`} className="block rounded-lg border border-gold/50 bg-gold-light/40 p-4 hover:border-gold">
              <div className="font-semibold">{data.approved_reference_strategy.name} <span className="text-ink-soft">v{data.approved_reference_strategy.version}</span></div>
              {data.approved_reference_strategy.headline && !data.approved_reference_strategy.headline.kind && (
                <div className="mt-2 grid grid-cols-3 gap-2 text-sm">
                  <div><div className="text-xs text-ink-soft">Investment</div>{fmt.num(data.approved_reference_strategy.headline.total_investment_musd)} MUSD</div>
                  <div><div className="text-xs text-ink-soft">Final RE share</div>{fmt.pct(data.approved_reference_strategy.headline.renewable_share_final_year)}</div>
                  <div><div className="text-xs text-ink-soft">CO2 vs 2024</div>{fmt.num(data.approved_reference_strategy.headline.emission_change_vs_2024_pct, 1)} %</div>
                </div>
              )}
            </Link>
          ) : (
            <p className="text-sm text-ink-soft">No strategy has been approved yet. Strategies are generated by the planning agent and reviewed by an administrator.</p>
          )}
        </Card>
        <Card title="Recent runs" actions={<Link to="/runs" className="text-xs text-gold-dark hover:underline">All results</Link>}>
          {data.recent_runs.length === 0 ? (
            <p className="text-sm text-ink-soft">No optimization or simulation run yet. Start from the <Link className="text-gold-dark underline" to="/scenarios">scenario builder</Link> or the <Link className="text-gold-dark underline" to="/agent">planning agent</Link>.</p>
          ) : (
            <ul className="divide-y divide-line">
              {data.recent_runs.map((r) => (
                <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <Link to={`/runs/${r.id}`} className="truncate hover:text-gold-dark">{r.scenario_name}</Link>
                  <span className="flex shrink-0 items-center gap-2"><KindBadge kind={r.kind} /><StatusBadge status={r.status} /></span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
