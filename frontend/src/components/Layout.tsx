import {
  BarChart3, Bot, ClipboardCheck, Database, GitCompare, History, LayoutDashboard, LogOut, Menu, Route as RouteIcon,
  ScrollText, Settings2, ShieldCheck, Users, X, Zap,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const MAIN = [
  { to: "/dashboard", label: "Energy dashboard", icon: LayoutDashboard },
  { to: "/agent", label: "Planning agent", icon: Bot },
  { to: "/scenarios", label: "Scenario builder", icon: Settings2 },
  { to: "/runs", label: "Optimization results", icon: BarChart3 },
  { to: "/strategies", label: "Strategies", icon: RouteIcon },
  { to: "/compare", label: "Comparison", icon: GitCompare },
  { to: "/history", label: "History", icon: History },
  { to: "/data", label: "Data & assumptions", icon: Database },
];
const ADMIN = [
  { to: "/admin", label: "Admin overview", icon: ShieldCheck, end: true },
  { to: "/admin/review", label: "Review queue", icon: ClipboardCheck },
  { to: "/admin/decisions", label: "Decision history", icon: ScrollText },
  { to: "/admin/users", label: "Users", icon: Users },
];

function NavItem({ to, label, icon: Icon, end, onClick }: { to: string; label: string; icon: typeof Zap; end?: boolean; onClick?: () => void }) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onClick}
      className={({ isActive }) =>
        `group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
          isActive ? "bg-white/10 text-white" : "text-neutral-400 hover:bg-white/5 hover:text-white"
        }`
      }
    >
      {({ isActive }) => (
        <>
          {isActive && <span className="absolute left-0 top-1.5 h-6 w-[3px] rounded-r bg-gold" />}
          <Icon size={17} className={isActive ? "text-gold" : ""} />
          {label}
        </>
      )}
    </NavLink>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [confirmLogout, setConfirmLogout] = useState(false);
  const close = () => setOpen(false);

  const sidebar = (
    <aside className="flex h-full w-64 flex-col bg-ink px-4 py-5 text-white">
      <div className="mb-8 flex items-center gap-2.5 px-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gold/15 ring-1 ring-gold/40"><Zap size={18} className="text-gold" /></div>
        <div>
          <div className="text-sm font-semibold tracking-wide">M4 Energy Planning</div>
          <div className="text-[11px] text-neutral-400">Tunisia · 2025–2040</div>
        </div>
      </div>
      <nav className="flex-1 space-y-1 overflow-y-auto">
        {MAIN.map((i) => <NavItem key={i.to} {...i} onClick={close} />)}
        {user?.role === "ADMIN" && (
          <>
            <div className="px-3 pb-1 pt-5 text-[10px] font-semibold uppercase tracking-[0.18em] text-gold/80">Administration</div>
            {ADMIN.map((i) => <NavItem key={i.to} {...i} onClick={close} />)}
          </>
        )}
      </nav>
      <div className="mt-4 border-t border-white/10 pt-4">
        <div className="px-2 text-sm font-medium">{user?.full_name || user?.username}</div>
        <div className="mb-3 flex items-center gap-2 px-2 text-xs text-neutral-400">
          {user?.email} <span className="rounded bg-gold/20 px-1.5 py-0.5 text-[10px] font-semibold text-gold">{user?.role}</span>
        </div>
        {confirmLogout ? (
          <div className="flex gap-2 px-2">
            <button className="flex-1 rounded-md bg-gold px-2 py-1.5 text-xs font-semibold text-ink" onClick={async () => { await logout(); navigate("/login"); }}>Sign out</button>
            <button className="flex-1 rounded-md border border-white/20 px-2 py-1.5 text-xs" onClick={() => setConfirmLogout(false)}>Cancel</button>
          </div>
        ) : (
          <button className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-neutral-400 hover:bg-white/5 hover:text-white" onClick={() => setConfirmLogout(true)}>
            <LogOut size={16} /> Sign out
          </button>
        )}
      </div>
    </aside>
  );

  return (
    <div className="flex h-full">
      <div className="hidden lg:block">{sidebar}</div>
      {open && (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="h-full">{sidebar}</div>
          <button aria-label="Close menu" className="flex-1 bg-black/40" onClick={close} />
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-line bg-white px-4 py-3 lg:hidden">
          <button aria-label="Open menu" onClick={() => setOpen(true)}>{open ? <X size={20} /> : <Menu size={20} />}</button>
          <span className="text-sm font-semibold">M4 Energy Planning</span>
        </header>
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-7xl px-4 py-6 md:px-8 md:py-8"><Outlet /></div>
        </main>
      </div>
    </div>
  );
}
