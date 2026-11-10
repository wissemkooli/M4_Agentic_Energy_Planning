import { useState, type ReactNode } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import type { AnnualResult } from "../api/types";
import { fmt } from "./ui";

/**
 * Technology palette (validated for colour-vision deficiency and normal-vision separation;
 * gold and aqua are below 3:1 contrast on white, so every chart has a legend and a table view).
 * Colours follow the technology, never its rank.
 */
export const TECH_COLORS: Record<string, string> = {
  gas: "#a0563a",
  solar: "#c6a04a",
  wind: "#2a78d6",
  hydro: "#1baf7a",
  imports: "#7c64c8",
};
export const TECH_ORDER = ["gas", "solar", "wind", "hydro", "imports"];
export const SERIES_COLORS = ["#171717", "#c6a04a", "#2a78d6", "#a0563a", "#1baf7a", "#7c64c8"];
export const techColor = (t: string) => TECH_COLORS[t] ?? "#737373";
const label = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

const axis = { stroke: "#a3a3a3", fontSize: 11, tickLine: false, axisLine: { stroke: "#E5E5E5" } } as const;
const grid = <CartesianGrid stroke="#F0F0F0" vertical={false} />;

function TooltipBox({ active, payload, title, unit, digits = 0 }: { active?: boolean; payload?: { name: string; value: number; color: string }[]; title?: ReactNode; unit: string; digits?: number }) {
  if (!active || !payload?.length) return null;
  const total = payload.reduce((s, p) => s + (typeof p.value === "number" ? p.value : 0), 0);
  return (
    <div className="rounded-lg border border-line bg-white px-3 py-2 text-xs shadow-sm">
      <div className="mb-1 font-semibold text-ink">{title}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center justify-between gap-6">
          <span className="flex items-center gap-1.5 text-ink-soft"><span className="h-2 w-2 rounded-full" style={{ background: p.color }} />{p.name}</span>
          <span className="num text-ink">{fmt.num(p.value, digits)} {unit}</span>
        </div>
      ))}
      {payload.length > 1 && unit !== "%" && (
        <div className="mt-1 flex justify-between gap-6 border-t border-line pt-1 font-medium"><span>Total</span><span className="num">{fmt.num(total, digits)} {unit}</span></div>
      )}
    </div>
  );
}

/** Chart wrapper with a "Table" toggle so values are never conveyed by colour alone. */
export function ChartFrame({ height = 260, table, children }: { height?: number; table: { columns: string[]; rows: (string | number)[][] }; children: ReactNode }) {
  const [showTable, setShowTable] = useState(false);
  return (
    <div>
      <div className="mb-1 flex justify-end">
        <button className="text-xs text-ink-soft underline-offset-2 hover:text-ink hover:underline" onClick={() => setShowTable((v) => !v)}>
          {showTable ? "Show chart" : "Show table"}
        </button>
      </div>
      {showTable ? (
        <div className="max-h-[320px] overflow-auto">
          <table className="w-full">
            <thead><tr>{table.columns.map((c) => <th key={c} className="th">{c}</th>)}</tr></thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i}>{r.map((v, j) => <td key={j} className="td num">{typeof v === "number" ? fmt.num(v, Math.abs(v) < 10 ? 2 : 0) : v}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ height }}><ResponsiveContainer width="100%" height="100%">{children as React.ReactElement}</ResponsiveContainer></div>
      )}
    </div>
  );
}

function techsOf(rows: Record<string, number>[], xKey = "year"): string[] {
  const present = new Set<string>();
  rows.forEach((r) => Object.keys(r).forEach((k) => k !== xKey && present.add(k)));
  return TECH_ORDER.filter((t) => present.has(t)).concat([...present].filter((t) => !TECH_ORDER.includes(t)));
}

/** Stacked bars per year (or per month with xKey="period") by technology (generation GWh, capacity MW, new MW). */
export function StackedTechBars({ rows, unit, height = 280, xKey = "year", xLabel = "Year" }: { rows: Record<string, number | string>[]; unit: string; height?: number; xKey?: string; xLabel?: string }) {
  const techs = techsOf(rows as Record<string, number>[], xKey);
  return (
    <ChartFrame height={height} table={{ columns: [xLabel, ...techs.map(label)], rows: rows.map((r) => [r[xKey], ...techs.map((t) => (r[t] as number) ?? 0)]) }}>
      <BarChart data={rows} barCategoryGap="22%">
        {grid}
        <XAxis dataKey={xKey} {...axis} />
        <YAxis {...axis} tickFormatter={(v) => fmt.num(v)} width={60} />
        <Tooltip cursor={{ fill: "#FAFAFA" }} content={<TooltipBox unit={unit} />} labelFormatter={(l) => l} />
        <Legend iconType="circle" iconSize={8} verticalAlign="bottom" height={36} wrapperStyle={{ fontSize: 12 }} />
        {techs.map((t, i) => (
          <Bar key={t} dataKey={t} name={label(t)} stackId="a" fill={techColor(t)} stroke="#fff" strokeWidth={1}
            radius={i === techs.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]} maxBarSize={34} />
        ))}
      </BarChart>
    </ChartFrame>
  );
}

/** Stacked area of installed capacity by technology. */
export function CapacityArea({ rows, height = 280 }: { rows: Record<string, number>[]; height?: number }) {
  const techs = techsOf(rows);
  return (
    <ChartFrame height={height} table={{ columns: ["Year", ...techs.map(label)], rows: rows.map((r) => [r.year, ...techs.map((t) => r[t] ?? 0)]) }}>
      <AreaChart data={rows}>
        {grid}
        <XAxis dataKey="year" {...axis} />
        <YAxis {...axis} tickFormatter={(v) => fmt.num(v)} width={60} />
        <Tooltip content={<TooltipBox unit="MW" />} />
        <Legend iconType="circle" iconSize={8} verticalAlign="bottom" height={36} wrapperStyle={{ fontSize: 12 }} />
        {techs.map((t) => (
          <Area key={t} type="monotone" dataKey={t} name={label(t)} stackId="1" stroke="#fff" strokeWidth={2} fill={techColor(t)} fillOpacity={0.9} />
        ))}
      </AreaChart>
    </ChartFrame>
  );
}

/** Lines of shares (0-1 values displayed as %). */
export function ShareLines({ rows, series, height = 240, unit = "%" }: { rows: Record<string, number>[]; series: { key: string; name: string; color: string }[]; height?: number; unit?: string }) {
  const pct = unit === "%";
  const data = rows.map((r) => ({ ...r, ...Object.fromEntries(series.map((s) => [s.key, pct ? +(r[s.key] * 100).toFixed(2) : r[s.key]])) }));
  return (
    <ChartFrame height={height} table={{ columns: ["Year", ...series.map((s) => `${s.name} (${unit})`)], rows: data.map((r) => [r.year, ...series.map((s) => r[s.key])]) }}>
      <LineChart data={data}>
        {grid}
        <XAxis dataKey="year" {...axis} />
        <YAxis {...axis} width={48} tickFormatter={(v) => (pct ? `${v}%` : fmt.num(v, 1))} domain={pct ? [0, "auto"] : ["auto", "auto"]} />
        <Tooltip content={<TooltipBox unit={unit} digits={pct ? 1 : 2} />} />
        {series.length > 1 && <Legend iconType="circle" iconSize={8} verticalAlign="bottom" height={36} wrapperStyle={{ fontSize: 12 }} />}
        {series.map((s) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2} dot={{ r: 3, strokeWidth: 0, fill: s.color }} activeDot={{ r: 5 }} />
        ))}
      </LineChart>
    </ChartFrame>
  );
}

/** Demand forecast: point forecast line with 80 % band (and optional actuals). */
export function DemandChart({ rows, height = 280 }: { rows: { year: number; forecast_gwh?: number; lower_80?: number; upper_80?: number; actual_gwh?: number }[]; height?: number }) {
  const data = rows.map((r) => ({ ...r, band: r.lower_80 !== undefined && r.upper_80 !== undefined ? [r.lower_80, r.upper_80] : undefined }));
  return (
    <ChartFrame height={height} table={{ columns: ["Year", "Actual (GWh)", "Forecast (GWh)", "Lower 80", "Upper 80"], rows: rows.map((r) => [r.year, r.actual_gwh ?? "—", r.forecast_gwh ?? "—", r.lower_80 ?? "—", r.upper_80 ?? "—"]) }}>
      <ComposedChart data={data}>
        {grid}
        <XAxis dataKey="year" {...axis} />
        <YAxis {...axis} width={60} tickFormatter={(v) => fmt.num(v)} domain={["auto", "auto"]} />
        <Tooltip
          content={({ active, payload, label: l }) =>
            active && payload?.length ? (
              <div className="rounded-lg border border-line bg-white px-3 py-2 text-xs shadow-sm">
                <div className="mb-1 font-semibold">{l}</div>
                {payload.filter((p) => p.dataKey !== "band").map((p) => (
                  <div key={String(p.dataKey)} className="flex justify-between gap-6"><span className="text-ink-soft">{p.name}</span><span className="num">{fmt.num(p.value as number)} GWh</span></div>
                ))}
                {(payload[0].payload as { band?: number[] }).band && (
                  <div className="flex justify-between gap-6"><span className="text-ink-soft">80 % interval</span><span className="num">{fmt.num((payload[0].payload as { band: number[] }).band[0])}–{fmt.num((payload[0].payload as { band: number[] }).band[1])}</span></div>
                )}
              </div>
            ) : null
          }
        />
        <Legend iconType="circle" iconSize={8} verticalAlign="bottom" height={36} wrapperStyle={{ fontSize: 12 }} />
        <Area dataKey="band" name="80 % interval" stroke="none" fill="#C6A04A" fillOpacity={0.18} />
        <Line dataKey="actual_gwh" name="Actual (synthetic history)" stroke="#737373" strokeWidth={2} dot={{ r: 2.5 }} connectNulls={false} />
        <Line dataKey="forecast_gwh" name="Forecast" stroke="#171717" strokeWidth={2} dot={{ r: 2.5 }} />
      </ComposedChart>
    </ChartFrame>
  );
}

export const annualToTech = (annual: AnnualResult[], key: "generation_gwh" | "capacity_mw" | "new_capacity_mw", withImports = false) =>
  annual.map((a) => ({ year: a.year, ...a[key], ...(withImports ? { imports: a.imports_gwh } : {}) }));

/** Grouped bars comparing one metric across several runs / strategies. */
export function CompareBars({ rows, keys, unit, height = 260 }: { rows: Record<string, string | number>[]; keys: { key: string; name: string }[]; unit: string; height?: number }) {
  return (
    <ChartFrame height={height} table={{ columns: ["Option", ...keys.map((k) => `${k.name} (${unit})`)], rows: rows.map((r) => [String(r.name), ...keys.map((k) => Number(r[k.key]))]) }}>
      <BarChart data={rows} barCategoryGap="25%">
        {grid}
        <XAxis dataKey="name" {...axis} interval={0} tick={{ fontSize: 11 }} />
        <YAxis {...axis} width={60} tickFormatter={(v) => fmt.num(v)} />
        <Tooltip cursor={{ fill: "#FAFAFA" }} content={<TooltipBox unit={unit} digits={1} />} />
        {keys.length > 1 && <Legend iconType="circle" iconSize={8} verticalAlign="bottom" height={36} wrapperStyle={{ fontSize: 12 }} />}
        {keys.map((k, i) => <Bar key={k.key} dataKey={k.key} name={k.name} fill={SERIES_COLORS[i % SERIES_COLORS.length]} radius={[4, 4, 0, 0]} maxBarSize={40} />)}
      </BarChart>
    </ChartFrame>
  );
}
