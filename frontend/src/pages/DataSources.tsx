import type { DataStatus, Provenance } from "../api/types";
import { useApi } from "../components/hooks";
import { Card, DataStatusBadge, ErrorState, Loading, Notice, PageHeader, fmt } from "../components/ui";

interface Sources {
  overall_status: DataStatus;
  provenance: Provenance[];
  quality: {
    issues: { level: string; source: string; message: string }[];
    m1_coverage: { forecast_years: number[]; extrapolated_years: number[]; has_intervals: boolean; first_forecast_month: string; last_month: string };
    reconciliation_2024: { demand_2024_gwh: number; reference_generation_2024_gwh: number; gap_gwh: number; gap_pct_of_demand: number; treatment: string } | null;
    capacity_factor_calibration: { technology: string; observed_cf_2024: number; assumed_mean_cf: number }[];
  };
  assumptions: {
    currency: string;
    technologies: Record<string, string | number | boolean>[];
    capacity_factor_profiles: Record<string, Record<string, number>>;
    retirements: { technology: string; year: number; retired_mw: number; note?: string }[];
    system_parameters: Record<string, { value: number; unit: string; note: string }>;
  };
  integration_note: string;
}

const COLS: [string, string][] = [
  ["category", "Category"], ["capex_musd_per_mw", "Capex MUSD/MW"], ["fixed_om_musd_per_mw_yr", "FOM MUSD/MW-yr"],
  ["variable_cost_usd_per_mwh", "VC USD/MWh"], ["emission_factor_t_per_mwh", "tCO2/MWh"], ["lifetime_years", "Life (yr)"],
  ["availability_factor", "Availability"], ["capacity_credit", "Capacity credit"], ["max_annual_build_mw", "Max MW/yr"], ["max_total_new_mw", "Max new MW"],
];

export default function DataSources() {
  const { data, loading, error, reload } = useApi<Sources>("/api/data/sources");
  if (loading) return <Loading />;
  if (error || !data) return <ErrorState message={error ?? "No data"} onRetry={reload} />;
  const q = data.quality;
  return (
    <>
      <PageHeader eyebrow="Provenance" title="Data sources & assumptions" subtitle="Every input enters M4 through an adapter that validates it and declares whether it is MOCK or REAL." actions={<DataStatusBadge status={data.overall_status} />} />
      <div className="mb-6"><Notice>{data.integration_note}</Notice></div>

      <Card title="Input datasets">
        <div className="overflow-x-auto"><table className="w-full min-w-[800px]">
          <thead><tr><th className="th">Source</th><th className="th">Status</th><th className="th">Version</th><th className="th">File</th><th className="th">Rows</th><th className="th">Producer</th><th className="th">Transformations</th></tr></thead>
          <tbody>{data.provenance.map((p) => (
            <tr key={p.source}>
              <td className="td font-medium">{p.source}<div className="text-[11px] font-normal text-ink-soft">{p.description}</div></td>
              <td className="td"><DataStatusBadge status={p.data_status} compact /></td>
              <td className="td font-mono text-[11px]">{p.dataset_version}</td>
              <td className="td font-mono text-[11px]">{p.path}<div className="text-ink-soft" title={p.sha256}>sha256 {p.sha256.slice(0, 10)}…</div></td>
              <td className="td num">{p.rows}</td>
              <td className="td text-xs">{p.producer}</td>
              <td className="td text-xs">{p.transformations.join("; ")}</td>
            </tr>))}</tbody>
        </table></div>
      </Card>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card title="Data quality report">
          <ul className="space-y-2 text-sm">
            {q.issues.map((i, k) => (
              <li key={k} className="flex gap-2"><span className={`mt-0.5 shrink-0 rounded px-1.5 text-[10px] font-semibold uppercase ${i.level === "error" ? "bg-red-100 text-red-800" : i.level === "warning" ? "bg-amber-100 text-amber-800" : "bg-paper-soft text-ink-soft"}`}>{i.level}</span><span><b className="font-medium">{i.source}</b> — {i.message}</span></li>
            ))}
          </ul>
        </Card>
        <Card title="M1 coverage and 2024 reconciliation">
          <p className="text-sm">Forecast months {q.m1_coverage.first_forecast_month} → {q.m1_coverage.last_month}; prediction intervals {q.m1_coverage.has_intervals ? "available" : "not available"}; years requiring M4 extrapolation: {q.m1_coverage.extrapolated_years.length ? q.m1_coverage.extrapolated_years.join(", ") : "none"}.</p>
          {q.reconciliation_2024 && (
            <div className="mt-4 rounded-lg border border-line p-3 text-sm">
              <div>2024 demand (M1 actuals): <b className="num">{fmt.num(q.reconciliation_2024.demand_2024_gwh)} GWh</b></div>
              <div>2024 reference generation: <b className="num">{fmt.num(q.reconciliation_2024.reference_generation_2024_gwh)} GWh</b></div>
              <div>Gap: <b className="num">{fmt.num(q.reconciliation_2024.gap_gwh)} GWh ({q.reconciliation_2024.gap_pct_of_demand} %)</b></div>
              <p className="mt-2 text-xs text-ink-soft">{q.reconciliation_2024.treatment}</p>
            </div>
          )}
          <div className="label mt-4">Capacity factor calibration</div>
          <table className="w-full"><thead><tr><th className="th">Tech</th><th className="th text-right">Observed 2024</th><th className="th text-right">Assumed mean</th></tr></thead>
            <tbody>{q.capacity_factor_calibration.map((c) => <tr key={c.technology}><td className="td capitalize">{c.technology}</td><td className="td num text-right">{fmt.pct(c.observed_cf_2024)}</td><td className="td num text-right">{fmt.pct(c.assumed_mean_cf)}</td></tr>)}</tbody></table>
        </Card>
      </div>

      <Card title="Technology assumptions" subtitle={`Separate from M3 benchmarking · currency ${data.assumptions.currency}`} className="mt-6">
        <div className="overflow-x-auto"><table className="w-full min-w-[900px]">
          <thead><tr><th className="th">Technology</th>{COLS.map(([, l]) => <th key={l} className="th text-right">{l}</th>)}<th className="th">Status</th></tr></thead>
          <tbody>{data.assumptions.technologies.map((t) => (
            <tr key={String(t.technology)}><td className="td font-medium capitalize">{String(t.technology)}<div className="max-w-xs text-[11px] font-normal text-ink-soft">{String(t.source_note ?? "")}</div></td>
              {COLS.map(([k]) => <td key={k} className="td num text-right">{String(t[k])}</td>)}
              <td className="td"><DataStatusBadge status={String(t.data_status).toUpperCase()} compact /></td></tr>
          ))}</tbody>
        </table></div>
      </Card>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card title="System parameters">
          <table className="w-full"><tbody>{Object.entries(data.assumptions.system_parameters).map(([k, v]) => (
            <tr key={k}><td className="td font-mono text-xs">{k}</td><td className="td num">{v.value} <span className="text-xs text-ink-soft">{v.unit}</span></td><td className="td text-xs text-ink-soft">{v.note}</td></tr>
          ))}</tbody></table>
        </Card>
        <Card title="Monthly capacity factor profiles & retirements">
          <div className="overflow-x-auto"><table className="w-full"><thead><tr><th className="th">Tech</th>{Array.from({ length: 12 }, (_, i) => <th key={i} className="th text-right">{i + 1}</th>)}</tr></thead>
            <tbody>{Object.entries(data.assumptions.capacity_factor_profiles).map(([t, p]) => (
              <tr key={t}><td className="td capitalize">{t}</td>{Array.from({ length: 12 }, (_, i) => <td key={i} className="td num text-right text-xs">{(p[String(i + 1)] * 100).toFixed(0)}</td>)}</tr>
            ))}</tbody></table></div>
          <div className="label mt-4">Scheduled retirements</div>
          <ul className="text-sm">{data.assumptions.retirements.map((r, i) => <li key={i}>{r.year}: {fmt.num(r.retired_mw)} MW {r.technology} <span className="text-xs text-ink-soft">{r.note}</span></li>)}</ul>
        </Card>
      </div>
    </>
  );
}
