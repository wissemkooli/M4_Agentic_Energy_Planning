import { AlertTriangle, CheckCircle2, CircleDashed, Database, Inbox, Loader2, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { DataStatus } from "../api/types";

export const fmt = {
  num: (v: number | null | undefined, d = 0) =>
    v === null || v === undefined || Number.isNaN(v) ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }),
  pct: (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)} %`),
  date: (s: string | null | undefined) => (s ? new Date(s).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "—"),
};

export function PageHeader({ title, subtitle, actions, eyebrow }: { title: string; subtitle?: ReactNode; actions?: ReactNode; eyebrow?: string }) {
  return (
    <div className="mb-6 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        {eyebrow && <div className="mb-1 text-xs font-semibold uppercase tracking-[0.18em] text-gold-dark">{eyebrow}</div>}
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-ink-soft">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, subtitle, children, actions, className = "" }: { title?: ReactNode; subtitle?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={`card p-5 ${className}`}>
      {(title || actions) && (
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-soft">{subtitle}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, unit, hint, accent = false }: { label: string; value: ReactNode; unit?: string; hint?: ReactNode; accent?: boolean }) {
  return (
    <div className={`card p-4 ${accent ? "border-gold/60 bg-gold-light/40" : ""}`}>
      <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${accent ? "text-gold-dark" : "text-ink"}`}>
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-ink-soft">{unit}</span>}
      </div>
      {hint && <div className="mt-1 text-xs text-ink-soft [overflow-wrap:anywhere]">{hint}</div>}
    </div>
  );
}

/** Visible provenance badge: MOCK data is always labelled. */
export function DataStatusBadge({ status, compact = false }: { status: DataStatus | string | undefined; compact?: boolean }) {
  const s = (status ?? "UNKNOWN") as DataStatus;
  const style: Record<string, string> = {
    MOCK: "border-amber-300 bg-amber-50 text-amber-800",
    REAL: "border-emerald-300 bg-emerald-50 text-emerald-800",
    MIXED: "border-orange-300 bg-orange-50 text-orange-800",
    UNKNOWN: "border-line bg-paper-soft text-ink-soft",
  };
  const label: Record<string, string> = {
    MOCK: compact ? "MOCK" : "MOCK data — synthetic inputs",
    REAL: compact ? "REAL" : "REAL data",
    MIXED: compact ? "MIXED" : "MIXED mock + real inputs",
    UNKNOWN: "Status unknown",
  };
  return (
    <span title="Data provenance of the inputs behind these numbers" className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${style[s] ?? style.UNKNOWN}`}>
      <Database size={11} /> {label[s] ?? s}
    </span>
  );
}

const STATUS_STYLE: Record<string, string> = {
  optimal: "bg-emerald-50 text-emerald-800 border-emerald-200",
  valid: "bg-emerald-50 text-emerald-800 border-emerald-200",
  approved: "bg-emerald-50 text-emerald-800 border-emerald-200",
  simulated: "bg-sky-50 text-sky-800 border-sky-200",
  pending_review: "bg-gold-light text-gold-dark border-gold/50",
  awaiting_approval: "bg-gold-light text-gold-dark border-gold/50",
  needs_clarification: "bg-sky-50 text-sky-800 border-sky-200",
  strategies_ready: "bg-gold-light text-gold-dark border-gold/50",
  optimizing: "bg-sky-50 text-sky-800 border-sky-200",
  revision_requested: "bg-orange-50 text-orange-800 border-orange-200",
  infeasible: "bg-red-50 text-red-800 border-red-200",
  invalid: "bg-red-50 text-red-800 border-red-200",
  rejected: "bg-red-50 text-red-800 border-red-200",
  error: "bg-red-50 text-red-800 border-red-200",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${STATUS_STYLE[status] ?? "border-line bg-paper-soft text-ink-soft"}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

export function KindBadge({ kind }: { kind: string }) {
  const label: Record<string, string> = { optimization: "Optimization output", simulation: "Simulated output", pareto: "Pareto analysis" };
  return <span className="rounded border border-line bg-white px-1.5 py-0.5 text-[11px] font-medium text-ink-soft">{label[kind] ?? kind}</span>;
}

export function Loading({ label = "Loading…", full = false }: { label?: string; full?: boolean }) {
  return (
    <div role="status" className={`flex items-center justify-center gap-2 text-sm text-ink-soft ${full ? "h-screen" : "py-16"}`}>
      <Loader2 className="animate-spin text-gold" size={18} /> {label}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="card flex items-start gap-3 border-red-200 bg-red-50/60 p-4 text-sm text-red-800">
      <XCircle size={18} className="mt-0.5 shrink-0" />
      <div className="flex-1">{message}</div>
      {onRetry && <button className="btn-outline px-3 py-1" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center px-6 py-12 text-center">
      <Inbox className="mb-3 text-gold" size={28} />
      <div className="text-sm font-semibold">{title}</div>
      {children && <div className="mt-1 max-w-md text-sm text-ink-soft">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "ok"; children: ReactNode }) {
  const s = { info: "border-line bg-paper-soft text-ink", warn: "border-amber-200 bg-amber-50 text-amber-900", ok: "border-emerald-200 bg-emerald-50 text-emerald-900" }[tone];
  const Icon = tone === "warn" ? AlertTriangle : tone === "ok" ? CheckCircle2 : CircleDashed;
  return (
    <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${s}`}>
      <Icon size={16} className="mt-0.5 shrink-0" />
      <div>{children}</div>
    </div>
  );
}

export function Check({ ok }: { ok: boolean }) {
  return ok ? <CheckCircle2 size={16} className="text-emerald-600" aria-label="satisfied" /> : <XCircle size={16} className="text-red-600" aria-label="violated" />;
}

export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: string }[]; active: string; onChange: (id: string) => void }) {
  return (
    <div role="tablist" className="mb-4 flex gap-1 border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={active === t.id}
          onClick={() => onChange(t.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${active === t.id ? "border-gold text-ink" : "border-transparent text-ink-soft hover:text-ink"}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
