import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { DataStatus, RunSummary } from "../api/types";
import { CompareBars, SERIES_COLORS, ShareLines, TECH_ORDER } from "../components/charts";
import { useApi } from "../components/hooks";
import { Card, DataStatusBadge, Empty, ErrorState, Loading, PageHeader, fmt } from "../components/ui";

interface CompareRow {
  run_id: string;
  scenario_name: string;
  status: string;
  feasible: boolean;
  data_status: DataStatus;
  total_cost_npv_musd?: number;
  total_investment_musd?: number;
  total_emissions_mt?: number;
  renewable_share_final_year?: number;
  gas_share_final_year?: number;
  dependence_share_horizon?: number;
  final_capacity_mw?: Record<string, number>;
  annual?: { year: number; renewable_share: number; emissions_mt: number; investment_musd: number }[];
}

const short = (s: string) => (s.length > 26 ? s.slice(0, 24) + "…" : s);

export default function Comparison() {
  const [params, setParams] = useSearchParams();
  const runs = useApi<RunSummary[]>("/api/optimization?kind=optimization&limit=200");
  const [selected, setSelected] = useState<string[]>(() => (params.get("runs") ?? "").split(",").filter(Boolean));
  const [data, setData] = useState<{ runs: CompareRow[]; data_status: DataStatus } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setParams(selected.length ? { runs: selected.join(",") } : {}, { replace: true });
    if (selected.length === 0) {
      setData(null);
      return;
    }
    setLoading(true);
    api.post("/api/comparison", { run_ids: selected })
      .then((r) => { setData(r.data); setError(null); })
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoading(false));
  }, [selected, setParams]);

  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= 10 ? s : [...s, id]));
  const feasible = (data?.runs ?? []).filter((r) => r.feasible && r.total_cost_npv_musd !== undefined);
  const names = feasible.map((r, i) => `${i + 1}. ${short(r.scenario_name)}`);
  const years = feasible[0]?.annual?.map((a) => a.year) ?? [];
  const lineRows = (key: "renewable_share" | "emissions_mt") =>
    years.map((y, yi) => ({ year: y, ...Object.fromEntries(feasible.map((r, i) => [`r${i}`, r.annual?.[yi]?.[key] ?? NaN])) }));

  return (
    <>
      <PageHeader eyebrow="Side by side" title="Scenario & strategy comparison" subtitle="Select up to 10 optimization runs (scenarios or agent strategies). All values come from stored, validated solver results." actions={data && <DataStatusBadge status={data.data_status} />} />
      <div className="grid gap-6 xl:grid-cols-[300px_minmax(0,1fr)]">
        <Card title="Runs" subtitle={`${selected.length} selected`}>
          {runs.loading ? <Loading label="" /> : runs.error ? <ErrorState message={runs.error} /> : (
            <div className="max-h-[640px] space-y-1 overflow-y-auto">
              {runs.data!.filter((r) => r.feasible).map((r) => (
                <label key={r.id} className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2 py-1.5 text-sm ${selected.includes(r.id) ? "border-gold bg-gold-light/40" : "border-transparent hover:bg-paper-soft"}`}>
                  <input type="checkbox" className="mt-1 accent-[#C6A04A]" checked={selected.includes(r.id)} onChange={() => toggle(r.id)} />
                  <span><span className="block leading-tight">{r.scenario_name}</span><span className="text-[11px] text-ink-soft">{r.method} · {fmt.date(r.created_at)}</span></span>
                </label>
              ))}
              {runs.data!.filter((r) => r.feasible).length === 0 && <p className="text-sm text-ink-soft">No feasible optimization run yet.</p>}
            </div>
          )}
        </Card>
        <div className="space-y-6">
          {error && <ErrorState message={error} />}
          {loading && <Loading />}
          {!loading && feasible.length === 0 && <Empty title="Nothing to compare yet">Select at least one feasible run on the left.</Empty>}
          {feasible.length > 0 && (
            <>
              <Card title="Key indicators">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px]">
                    <thead><tr><th className="th">Option</th><th className="th text-right">NPV cost (MUSD)</th><th className="th text-right">Investment (MUSD)</th><th className="th text-right">CO2 (Mt)</th><th className="th text-right">RE final</th><th className="th text-right">Gas final</th><th className="th text-right">Dependence</th></tr></thead>
                    <tbody>{feasible.map((r, i) => {
                      const best = (k: keyof CompareRow, dir: "min" | "max") => {
                        const vals = feasible.map((x) => x[k] as number);
                        return (r[k] as number) === (dir === "min" ? Math.min(...vals) : Math.max(...vals)) && feasible.length > 1;
                      };
                      const cell = (k: keyof CompareRow, dir: "min" | "max", v: string) => <td className={`td num text-right ${best(k, dir) ? "font-semibold text-gold-dark" : ""}`}>{v}</td>;
                      return (
                        <tr key={r.run_id}>
                          <td className="td"><span className="mr-2 inline-block h-2 w-2 rounded-full" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} />{names[i]}</td>
                          {cell("total_cost_npv_musd", "min", fmt.num(r.total_cost_npv_musd))}
                          {cell("total_investment_musd", "min", fmt.num(r.total_investment_musd))}
                          {cell("total_emissions_mt", "min", fmt.num(r.total_emissions_mt, 1))}
                          {cell("renewable_share_final_year", "max", fmt.pct(r.renewable_share_final_year))}
                          {cell("gas_share_final_year", "min", fmt.pct(r.gas_share_final_year))}
                          {cell("dependence_share_horizon", "min", fmt.pct(r.dependence_share_horizon))}
                        </tr>
                      );
                    })}</tbody>
                  </table>
                </div>
                <p className="mt-2 text-xs text-ink-soft">Gold = best value among the selected options.</p>
              </Card>
              <div className="grid gap-6 xl:grid-cols-2">
                <Card title="Total system cost vs investment (MUSD)">
                  <CompareBars unit="MUSD" rows={feasible.map((r, i) => ({ name: `${i + 1}`, cost: r.total_cost_npv_musd!, inv: r.total_investment_musd! }))} keys={[{ key: "cost", name: "NPV system cost" }, { key: "inv", name: "Overnight investment" }]} />
                </Card>
                <Card title="Cumulative CO2 emissions (Mt)">
                  <CompareBars unit="Mt" rows={feasible.map((r, i) => ({ name: `${i + 1}`, em: r.total_emissions_mt! }))} keys={[{ key: "em", name: "CO2" }]} />
                </Card>
                {years.length > 0 && feasible.every((r) => r.annual?.length === years.length) && (
                  <>
                    <Card title="Renewable share trajectory"><ShareLines rows={lineRows("renewable_share")} series={feasible.map((_, i) => ({ key: `r${i}`, name: names[i], color: SERIES_COLORS[i % SERIES_COLORS.length] }))} /></Card>
                    <Card title="Annual emissions (Mt)"><ShareLines unit="Mt" rows={lineRows("emissions_mt")} series={feasible.map((_, i) => ({ key: `r${i}`, name: names[i], color: SERIES_COLORS[i % SERIES_COLORS.length] }))} /></Card>
                  </>
                )}
                <Card title="Final capacity mix (MW)" className="xl:col-span-2">
                  <CompareBars unit="MW" rows={feasible.map((r, i) => ({ name: `${i + 1}`, ...(r.final_capacity_mw ?? {}) }))} keys={TECH_ORDER.filter((t) => feasible[0].final_capacity_mw?.[t] !== undefined).map((t) => ({ key: t, name: t }))} />
                </Card>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
