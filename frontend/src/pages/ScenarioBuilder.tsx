import { Play, Plus, Save, ShieldCheck, Trash2, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Formulation, RunDetail, Scenario, ScenarioConfig, YearValue } from "../api/types";
import { useApi } from "../components/hooks";
import { Card, ErrorState, Loading, Notice, PageHeader } from "../components/ui";

interface Meta {
  objectives: Record<string, string>;
  technologies: string[];
  demand_bases: string[];
  methods: string[];
  m1_forecast_years: number[];
  has_intervals: boolean;
  defaults: ScenarioConfig;
}

/** Client-side checks (the backend re-validates everything). */
export function validateScenario(c: ScenarioConfig): string[] {
  const e: string[] = [];
  if (!c.name || c.name.trim().length < 2) e.push("Scenario name must have at least 2 characters.");
  if (c.start_year < 2025) e.push("Start year must be after the 2024 reference year.");
  if (c.end_year < c.start_year) e.push("End year must be greater than or equal to the start year.");
  if (c.end_year > 2060) e.push("End year must be 2060 or earlier.");
  const pctLists: [string, YearValue[]][] = [
    ["Renewable target", c.constraints.renewable_share_targets],
    ["Emission reduction target", c.constraints.emission_reduction_targets],
    ["Gas share limit", c.constraints.max_gas_share],
  ];
  for (const [label, list] of pctLists)
    for (const t of list) {
      if (t.value < 0 || t.value > 100) e.push(`${label} ${t.year}: value must be within 0-100 %.`);
      if (t.year < c.start_year || t.year > c.end_year) e.push(`${label} year ${t.year} is outside the horizon.`);
    }
  const b = c.constraints;
  if (b.budget_total_musd !== null && b.budget_total_musd < 0) e.push("Budget cannot be negative.");
  if (b.annual_budget_musd !== null && b.budget_total_musd !== null && b.annual_budget_musd > b.budget_total_musd)
    e.push("Annual budget cannot exceed the total budget.");
  for (const k of ["reserve_margin_pct", "max_import_share_pct", "max_vre_share_monthly_pct"] as const)
    if (b[k] !== null && (b[k]! < 0 || b[k]! > 100)) e.push(`${k} must be within 0-100.`);
  const o = c.objective;
  if (o.method === "weighted_sum" && Object.values(o.weights).filter((w) => w > 0).length < 2) e.push("Weighted sum needs at least two positive weights.");
  if (o.method === "epsilon_constraint" && Object.keys(o.epsilon_bounds).length === 0) e.push("Epsilon-constraint needs at least one bounded objective.");
  if (o.method === "epsilon_constraint" && o.primary in o.epsilon_bounds) e.push("The primary objective cannot be epsilon-bounded.");
  if (o.method === "lexicographic" && new Set(o.priorities).size < 2) e.push("Lexicographic needs at least two distinct priorities.");
  return e;
}

const numOrNull = (s: string) => (s.trim() === "" ? null : Number(s));

function NumField({ label, value, onChange, unit, step = "any", placeholder }: { label: string; value: number | null; onChange: (v: number | null) => void; unit?: string; step?: string; placeholder?: string }) {
  return (
    <label className="block">
      <span className="label">{label}{unit && <span className="normal-case text-ink-soft"> ({unit})</span>}</span>
      <input type="number" step={step} className="input" value={value ?? ""} placeholder={placeholder ?? "none"} onChange={(e) => onChange(numOrNull(e.target.value))} />
    </label>
  );
}

function TargetList({ label, unit, items, onChange, defaultYear }: { label: string; unit: string; items: YearValue[]; onChange: (v: YearValue[]) => void; defaultYear: number }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between"><span className="label mb-0">{label}</span>
        <button type="button" className="text-xs text-gold-dark hover:underline" onClick={() => onChange([...items, { year: defaultYear, value: 0 }])}><Plus size={12} className="inline" /> add</button></div>
      {items.length === 0 && <div className="text-xs text-ink-soft">None</div>}
      {items.map((t, i) => (
        <div key={i} className="mb-1 flex items-center gap-2">
          <input aria-label={`${label} year`} type="number" className="input w-24" value={t.year} onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, year: Number(e.target.value) } : x)))} />
          <input aria-label={`${label} value`} type="number" className="input w-24" value={t.value} onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, value: Number(e.target.value) } : x)))} />
          <span className="text-xs text-ink-soft">{unit}</span>
          <button type="button" aria-label="remove" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 size={14} className="text-ink-soft hover:text-red-600" /></button>
        </div>
      ))}
    </div>
  );
}

export default function ScenarioBuilder() {
  const navigate = useNavigate();
  const meta = useApi<Meta>("/api/scenarios/meta");
  const scenarios = useApi<Scenario[]>("/api/scenarios");
  const [cfg, setCfg] = useState<ScenarioConfig | null>(null);
  const [selected, setSelected] = useState<string>("reference");
  const [errors, setErrors] = useState<string[]>([]);
  const [serverError, setServerError] = useState<string | null>(null);
  const [formulation, setFormulation] = useState<Formulation | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    if (!cfg && scenarios.data) {
      const ref = scenarios.data.find((s) => s.id === "reference");
      if (ref) setCfg(structuredClone(ref.config));
    }
  }, [scenarios.data, cfg]);

  if (meta.loading || scenarios.loading || !cfg) return meta.error || scenarios.error ? <ErrorState message={(meta.error || scenarios.error)!} /> : <Loading />;
  const m = meta.data!;
  const set = (patch: (c: ScenarioConfig) => void) => {
    const n = structuredClone(cfg);
    patch(n);
    setCfg(n);
    setFormulation(null);
    setSaved(null);
  };
  const load = (id: string) => {
    if (id === "__new") {
      setCfg({ ...structuredClone(m.defaults), name: "Custom scenario" });
    } else {
      const s = scenarios.data!.find((x) => x.id === id);
      if (s) setCfg(structuredClone(s.config));
    }
    setSelected(id);
    setErrors([]);
    setServerError(null);
    setFormulation(null);
  };
  const limit = (t: string) => cfg.constraints.technology_limits.find((l) => l.technology === t);
  const setLimit = (t: string, key: "allow_investment" | "max_annual_build_mw" | "max_total_new_mw" | "min_total_new_mw", v: boolean | number | null) =>
    set((c) => {
      let l = c.constraints.technology_limits.find((x) => x.technology === t);
      if (!l) {
        l = { technology: t, allow_investment: true, max_annual_build_mw: null, max_total_new_mw: null, min_total_new_mw: null };
        c.constraints.technology_limits.push(l);
      }
      (l as unknown as Record<string, unknown>)[key] = v;
    });

  const act = async (kind: "validate" | "save" | "optimize" | "simulate" | "pareto") => {
    const errs = validateScenario(cfg);
    setErrors(errs);
    setServerError(null);
    if (errs.length) return;
    setBusy(kind);
    try {
      if (kind === "validate") {
        const r = await api.post<{ formulation: Formulation }>("/api/scenarios/validate", { config: cfg });
        setFormulation(r.data.formulation);
      } else if (kind === "save") {
        const r = await api.post<Scenario>("/api/scenarios", { config: cfg });
        setSaved(`Saved as scenario ${r.data.id}`);
        scenarios.reload();
        setSelected(r.data.id);
      } else {
        const url = kind === "optimize" ? "/api/optimization/run" : kind === "simulate" ? "/api/simulation/run" : "/api/optimization/pareto";
        const r = await api.post<RunDetail>(url, { scenario: cfg, ...(kind === "pareto" ? { points: 6 } : {}) });
        navigate(`/runs/${r.data.id}`);
      }
    } catch (e) {
      setServerError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const o = cfg.objective;
  const objKeys = Object.keys(m.objectives);

  return (
    <>
      <PageHeader
        eyebrow="Objectives & constraints"
        title="Scenario builder"
        subtitle="Configure predefined or custom scenarios. Objectives define what is optimized; constraints define limits that must hold. Parameters are stored with every run for reproducibility."
        actions={
          <>
            <button className="btn-outline" disabled={!!busy} onClick={() => act("validate")}><ShieldCheck size={16} /> Validate</button>
            <button className="btn-outline" disabled={!!busy} onClick={() => act("save")}><Save size={16} /> Save</button>
            <button className="btn-outline" disabled={!!busy} onClick={() => act("simulate")}><Zap size={16} /> Simulate existing fleet</button>
            <button className="btn-outline" disabled={!!busy} onClick={() => act("pareto")}>Pareto cost/CO2</button>
            <button className="btn-gold" disabled={!!busy} onClick={() => act("optimize")}><Play size={16} /> {busy === "optimize" ? "Solving…" : "Run optimization"}</button>
          </>
        }
      />
      {errors.length > 0 && <div className="mb-4"><ErrorState message={errors.join(" ")} /></div>}
      {serverError && <div className="mb-4"><ErrorState message={serverError} /></div>}
      {saved && <div className="mb-4"><Notice tone="ok">{saved}</Notice></div>}
      {busy && busy !== "validate" && busy !== "save" && <div className="mb-4"><Loading label="Running the backend engine…" /></div>}

      <div className="grid gap-6 xl:grid-cols-[260px_minmax(0,1fr)]">
        <div className="space-y-2">
          <div className="label">Scenarios</div>
          {scenarios.data!.map((s) => (
            <button key={s.id} onClick={() => load(s.id)} className={`block w-full rounded-lg border px-3 py-2 text-left text-sm ${selected === s.id ? "border-gold bg-gold-light/40" : "border-line bg-white hover:border-ink"}`}>
              <div className="font-medium">{s.name}</div>
              <div className="text-[11px] text-ink-soft">{s.is_predefined ? "Predefined" : "Custom"}</div>
            </button>
          ))}
          <button onClick={() => load("__new")} className="btn-outline w-full"><Plus size={14} /> New custom scenario</button>
        </div>

        <div className="space-y-6">
          <Card title="General">
            <div className="grid gap-4 md:grid-cols-4">
              <label className="md:col-span-2"><span className="label">Name</span><input className="input" value={cfg.name} onChange={(e) => set((c) => { c.name = e.target.value; })} /></label>
              <NumField label="Start year" value={cfg.start_year} onChange={(v) => set((c) => { c.start_year = v ?? 2025; })} step="1" />
              <NumField label="End year" value={cfg.end_year} onChange={(v) => set((c) => { c.end_year = v ?? 2040; })} step="1" />
              <label className="md:col-span-4"><span className="label">Description</span><textarea className="input" rows={2} value={cfg.description} onChange={(e) => set((c) => { c.description = e.target.value; })} /></label>
            </div>
          </Card>

          <Card title="Demand assumptions" subtitle={`M1 forecasts available for ${m.m1_forecast_years[0]}–${m.m1_forecast_years[m.m1_forecast_years.length - 1]}; later years are extrapolated by M4 and labelled as such.`}>
            <div className="grid gap-4 md:grid-cols-4">
              <label><span className="label">M1 demand basis</span>
                <select className="input" value={cfg.demand.basis} onChange={(e) => set((c) => { c.demand.basis = e.target.value; })}>
                  {m.demand_bases.map((b) => <option key={b} value={b} disabled={b !== "forecast" && !m.has_intervals}>{b}</option>)}
                </select></label>
              <NumField label="Efficiency savings by end" unit="%" value={cfg.demand.efficiency_savings_pct_by_end} onChange={(v) => set((c) => { c.demand.efficiency_savings_pct_by_end = v ?? 0; })} />
              <NumField label="Demand multiplier" value={cfg.demand.demand_multiplier} onChange={(v) => set((c) => { c.demand.demand_multiplier = v ?? 1; })} />
              <NumField label="Extrapolation growth" unit="%/yr" value={cfg.demand.extrapolation_growth_pct} placeholder="CAGR of M1" onChange={(v) => set((c) => { c.demand.extrapolation_growth_pct = v; })} />
            </div>
          </Card>

          <Card title="Policy and reliability constraints">
            <div className="grid gap-4 md:grid-cols-3">
              <NumField label="Total investment budget" unit="MUSD" value={cfg.constraints.budget_total_musd} onChange={(v) => set((c) => { c.constraints.budget_total_musd = v; })} />
              <NumField label="Annual investment limit" unit="MUSD/yr" value={cfg.constraints.annual_budget_musd} onChange={(v) => set((c) => { c.constraints.annual_budget_musd = v; })} />
              <NumField label="Reserve margin" unit="%" value={cfg.constraints.reserve_margin_pct} placeholder="default 15" onChange={(v) => set((c) => { c.constraints.reserve_margin_pct = v; })} />
              <NumField label="Max import share" unit="%" value={cfg.constraints.max_import_share_pct} placeholder="default 10" onChange={(v) => set((c) => { c.constraints.max_import_share_pct = v; })} />
              <NumField label="Max variable RE share (monthly)" unit="%" value={cfg.constraints.max_vre_share_monthly_pct} placeholder="default 55" onChange={(v) => set((c) => { c.constraints.max_vre_share_monthly_pct = v; })} />
              <label className="flex items-center gap-2 pt-6 text-sm"><input type="checkbox" className="accent-[#C6A04A]" checked={cfg.operating.allow_imports} onChange={(e) => set((c) => { c.operating.allow_imports = e.target.checked; })} /> Allow electricity imports</label>
            </div>
            <div className="mt-5 grid gap-6 md:grid-cols-3">
              <TargetList label="Min renewable share" unit="%" items={cfg.constraints.renewable_share_targets} defaultYear={2030} onChange={(v) => set((c) => { c.constraints.renewable_share_targets = v; })} />
              <TargetList label="CO2 reduction vs 2024" unit="%" items={cfg.constraints.emission_reduction_targets} defaultYear={2035} onChange={(v) => set((c) => { c.constraints.emission_reduction_targets = v; })} />
              <TargetList label="Max gas share" unit="%" items={cfg.constraints.max_gas_share} defaultYear={2035} onChange={(v) => set((c) => { c.constraints.max_gas_share = v; })} />
            </div>
          </Card>

          <Card title="Technology deployment limits" subtitle="Empty fields use the documented technology assumptions.">
            <table className="w-full">
              <thead><tr><th className="th">Technology</th><th className="th">Investment allowed</th><th className="th">Max MW/yr</th><th className="th">Max new MW total</th><th className="th">Min new MW total</th></tr></thead>
              <tbody>
                {m.technologies.map((t) => {
                  const l = limit(t);
                  return (
                    <tr key={t}>
                      <td className="td capitalize font-medium">{t}</td>
                      <td className="td"><input type="checkbox" className="accent-[#C6A04A]" checked={l?.allow_investment ?? true} onChange={(e) => setLimit(t, "allow_investment", e.target.checked)} /></td>
                      {(["max_annual_build_mw", "max_total_new_mw", "min_total_new_mw"] as const).map((k) => (
                        <td key={k} className="td"><input aria-label={`${t} ${k}`} type="number" className="input w-28" value={l?.[k] ?? ""} placeholder="default" onChange={(e) => setLimit(t, k, numOrNull(e.target.value))} /></td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>

          <Card title="Operating & economic assumptions">
            <div className="grid gap-4 md:grid-cols-3">
              <NumField label="Renewable availability multiplier" value={cfg.operating.renewable_availability_multiplier} onChange={(v) => set((c) => { c.operating.renewable_availability_multiplier = v ?? 1; })} />
              <NumField label="Gas cost multiplier" value={cfg.operating.gas_cost_multiplier} onChange={(v) => set((c) => { c.operating.gas_cost_multiplier = v ?? 1; })} />
              <NumField label="Discount rate" unit="fraction" value={cfg.operating.discount_rate} placeholder="default 0.08" onChange={(v) => set((c) => { c.operating.discount_rate = v; })} />
            </div>
          </Card>

          <Card title="Objective" subtitle="Single objective or a documented multi-objective method — never an arbitrary hidden weighting.">
            <div className="grid gap-4 md:grid-cols-3">
              <label><span className="label">Method</span>
                <select className="input" value={o.method} onChange={(e) => set((c) => {
                  c.objective.method = e.target.value;
                  if (e.target.value === "weighted_sum" && Object.keys(c.objective.weights).length < 2) c.objective.weights = { min_cost: 0.5, min_emissions: 0.5 };
                  if (e.target.value === "lexicographic" && c.objective.priorities.length < 2) c.objective.priorities = ["min_emissions", "min_cost"];
                  if (e.target.value === "epsilon_constraint" && Object.keys(c.objective.epsilon_bounds).length === 0) c.objective.epsilon_bounds = { min_emissions: { mode: "fraction", value: 0.5 } };
                })}>
                  {m.methods.map((x) => <option key={x} value={x}>{x.replace("_", " ")}</option>)}
                </select></label>
              {(o.method === "single" || o.method === "epsilon_constraint") && (
                <label className="md:col-span-2"><span className="label">Primary objective</span>
                  <select className="input" value={o.primary} onChange={(e) => set((c) => { c.objective.primary = e.target.value; delete c.objective.epsilon_bounds[e.target.value]; })}>
                    {objKeys.map((k) => <option key={k} value={k}>{m.objectives[k]}</option>)}
                  </select></label>
              )}
            </div>
            {o.method === "weighted_sum" && (
              <div className="mt-4 grid gap-3 md:grid-cols-5">
                {objKeys.map((k) => <NumField key={k} label={k} value={o.weights[k] ?? null} onChange={(v) => set((c) => { if (v === null || v === 0) delete c.objective.weights[k]; else c.objective.weights[k] = v; })} />)}
              </div>
            )}
            {o.method === "epsilon_constraint" && (
              <div className="mt-4 grid gap-3 md:grid-cols-4">
                {objKeys.filter((k) => k !== o.primary).map((k) => (
                  <NumField key={k} label={`${k} bound (0=best,1=worst)`} value={o.epsilon_bounds[k]?.value ?? null}
                    onChange={(v) => set((c) => { if (v === null) delete c.objective.epsilon_bounds[k]; else c.objective.epsilon_bounds[k] = { mode: "fraction", value: v }; })} />
                ))}
              </div>
            )}
            {o.method === "lexicographic" && (
              <div className="mt-4 grid gap-3 md:grid-cols-3">
                {[0, 1, 2].map((i) => (
                  <label key={i}><span className="label">Priority {i + 1}</span>
                    <select className="input" value={o.priorities[i] ?? ""} onChange={(e) => set((c) => { const p = [...c.objective.priorities]; p[i] = e.target.value; c.objective.priorities = p.filter(Boolean); })}>
                      <option value="">{i < 2 ? "—" : "(none)"}</option>
                      {objKeys.map((k) => <option key={k} value={k}>{k}</option>)}
                    </select></label>
                ))}
                <NumField label="Tolerance" unit="%" value={o.lexicographic_tolerance_pct} onChange={(v) => set((c) => { c.objective.lexicographic_tolerance_pct = v ?? 1; })} />
              </div>
            )}
          </Card>

          {formulation && (
            <Card title="Validated formulation" subtitle={`${formulation.problem_class} · ${formulation.horizon}`}>
              <p className="mb-3 text-sm"><b>Objective:</b> {formulation.objective}</p>
              <p className="mb-3 text-xs text-ink-soft">{formulation.method_explanation}</p>
              <table className="w-full"><tbody>{formulation.constraints.map((c) => <tr key={c.name}><td className="td font-medium">{c.name}</td><td className="td font-mono text-[11px]">{c.expression}</td><td className="td text-xs">{c.values}</td></tr>)}</tbody></table>
              <ul className="mt-3 list-disc pl-5 text-xs text-ink-soft">{formulation.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
