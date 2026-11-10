import type { OperationalHeadline, OperationalMonth, OperationalValidation, ScheduleStep } from "../api/types";
import { StackedTechBars } from "./charts";
import { Card, Check, Stat, StatusBadge, fmt } from "./ui";

/** Key indicators of a short-term operating plan (all values from the validated MILP solution). */
export function OperationalStats({ h }: { h: OperationalHeadline }) {
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
      <Stat label="Operating cost" value={fmt.num(h.operating_cost_musd, 1)} unit="MUSD" hint={h.avg_cost_usd_per_mwh != null ? `${fmt.num(h.avg_cost_usd_per_mwh, 1)} USD/MWh served` : undefined} />
      <Stat label="Unserved energy" value={fmt.num(h.unserved_gwh, 1)} unit="GWh" hint={`of ${fmt.num(h.demand_gwh)} GWh demand`} />
      <Stat label="Gas generation" value={fmt.num(h.gas_gwh)} unit="GWh" hint={`CO2 ${fmt.num(h.emissions_mt, 3)} Mt`} />
      <Stat label="Imports" value={fmt.num(h.imports_gwh)} unit="GWh" hint={`Renewables ${fmt.pct(h.renewable_share)}`} />
      <Stat label="Min reserve margin" value={fmt.pct(h.min_reserve_margin)} accent hint={`${fmt.num(h.avg_units_in_service, 2)} gas units in service on average`} />
    </div>
  );
}

export function ScheduleTimeline({ schedule }: { schedule: ScheduleStep[] }) {
  return (
    <ol className="relative border-l border-line pl-6">
      {schedule.map((r) => (
        <li key={r.period} className="mb-5">
          <span className={`absolute -left-[7px] mt-1 h-3.5 w-3.5 rounded-full border-2 border-white ${r.unserved_gwh > 0.05 ? "bg-red-500" : "bg-gold"}`} />
          <div className="flex flex-wrap items-baseline gap-x-3"><span className="font-semibold">{r.period}</span><span className="num text-xs text-ink-soft">{fmt.num(r.operating_cost_musd, 1)} MUSD · reserve {fmt.pct(r.reserve_margin)} · gas {fmt.num(r.gas_gwh)} GWh · imports {fmt.num(r.imports_gwh)} GWh</span></div>
          <ul className="text-sm text-ink">{r.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </li>
      ))}
    </ol>
  );
}

export function OperationalCharts({ monthly }: { monthly: OperationalMonth[] }) {
  const gen = monthly.map((m) => ({ period: m.period, ...m.generation_gwh, imports: m.imports_gwh }));
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <Card title="Monthly generation by technology (GWh)" subtitle="Imports shown separately; unserved energy in the table">
        <StackedTechBars rows={gen} unit="GWh" xKey="period" xLabel="Month" />
      </Card>
      <Card title="Units in service, reserve and demand" subtitle="Integer decisions of the MILP">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr><th className="th">Month</th><th className="th text-right">Demand GWh</th><th className="th text-right">Peak MW</th><th className="th text-right">Gas units</th><th className="th text-right">Firm MW</th><th className="th text-right">Reserve</th><th className="th text-right">Unserved</th><th className="th text-right">Cost MUSD</th></tr></thead>
            <tbody>
              {monthly.map((m) => (
                <tr key={m.period}>
                  <td className="td">{m.period}{m.demand_source !== "M1_forecast" && <span className="ml-1 text-[10px] text-amber-700">extrap.</span>}</td>
                  <td className="td num text-right">{fmt.num(m.demand_gwh)}</td>
                  <td className="td num text-right">{fmt.num(m.peak_mw)}</td>
                  <td className="td num text-right">{Object.entries(m.units_in_service).map(([t, v]) => `${fmt.num(v)} / ${m.units_available[t]}`).join(", ")}</td>
                  <td className="td num text-right">{fmt.num(m.firm_capacity_mw)}</td>
                  <td className="td num text-right">{fmt.pct(m.reserve_margin_achieved)}</td>
                  <td className={`td num text-right ${m.unserved_gwh > 0.05 ? "font-semibold text-red-700" : ""}`}>{fmt.num(m.unserved_gwh, 1)}</td>
                  <td className="td num text-right">{fmt.num(m.operating_cost_musd, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

export function OperationalValidationView({ v, solver }: { v: OperationalValidation | null; solver?: { name: string | null; problem_class?: string; mip_gap?: number | null; solves: { step: string; status: string; seconds: number }[] } | null }) {
  if (!v) return null;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div>
        <div className="label">Constraint satisfaction</div>
        <div className="max-h-[360px] overflow-auto">
          <table className="w-full">
            <thead><tr><th className="th">Constraint</th><th className="th">Month</th><th className="th text-right">Value</th><th className="th text-right">Limit</th><th className="th" /></tr></thead>
            <tbody>
              {v.constraints.map((c, i) => (
                <tr key={i}><td className="td">{c.constraint}</td><td className="td text-ink-soft">{c.scope}</td><td className="td num text-right">{fmt.num(c.value, 1)} {c.unit}</td><td className="td num text-right">{c.relation} {fmt.num(c.limit, 1)}</td><td className="td"><Check ok={c.satisfied} /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div>
        <div className="label">Independent validation</div>
        <ul className="space-y-2 text-sm">
          {v.physical_checks.map((c) => <li key={c.check} className="flex items-center gap-2"><Check ok={c.passed} />{c.check}</li>)}
          <li className="flex items-center gap-2"><Check ok={v.objective_consistency.every((o) => o.passed)} />Objective values recomputed outside the solver</li>
        </ul>
        {solver && (
          <>
            <div className="label mt-5">Solver log ({solver.name}{solver.problem_class ? ` · ${solver.problem_class}` : ""}{solver.mip_gap ? ` · MIP gap ${solver.mip_gap}` : ""})</div>
            <table className="w-full"><tbody>{solver.solves.map((s, i) => <tr key={i}><td className="td font-mono text-[11px]">{s.step}</td><td className="td"><StatusBadge status={s.status} /></td><td className="td num text-right text-xs">{s.seconds}s</td></tr>)}</tbody></table>
          </>
        )}
      </div>
    </div>
  );
}
