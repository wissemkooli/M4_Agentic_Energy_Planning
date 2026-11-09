import { Lock, Zap } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Notice } from "../components/ui";

export function validateLogin(login: string, password: string): string | null {
  if (login.trim().length < 3) return "Enter your username or email.";
  if (!password) return "Enter your password.";
  return null;
}

export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const [form, setForm] = useState({ login: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={user.role === "ADMIN" ? "/admin" : "/dashboard"} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const v = validateLogin(form.login, form.password);
    if (v) return setError(v);
    setBusy(true);
    setError(null);
    try {
      const u = await login(form.login.trim(), form.password);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== "/" ? from : u.role === "ADMIN" ? "/admin" : "/dashboard", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-full lg:grid-cols-2">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-ink p-12 text-white lg:flex">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gold/15 ring-1 ring-gold/40"><Zap className="text-gold" size={20} /></div>
          <span className="font-semibold tracking-wide">M4 Energy Planning</span>
        </div>
        <div>
          <div className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-gold">Decision support · Tunisia 2025–2040</div>
          <h1 className="max-w-md text-4xl font-semibold leading-tight">Agentic planning and optimization of the electricity system.</h1>
          <p className="mt-4 max-w-md text-sm text-neutral-400">
            The agent proposes, the mathematical optimizer calculates, the administrator decides.
          </p>
          <div className="mt-10 grid max-w-md grid-cols-3 gap-4 text-xs text-neutral-400">
            <div><div className="text-lg font-semibold text-white">Pyomo</div>LP formulation</div>
            <div><div className="text-lg font-semibold text-white">HiGHS</div>solver</div>
            <div><div className="text-lg font-semibold text-white">LangGraph</div>agent workflow</div>
          </div>
        </div>
        <div className="text-xs text-neutral-500">Module M4 · integrates M1 demand forecasts and M3 benchmarks</div>
        <div className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full border border-gold/20" />
        <div className="pointer-events-none absolute -right-10 -top-10 h-52 w-52 rounded-full border border-gold/30" />
      </div>
      <div className="flex items-center justify-center bg-white p-6">
        <form onSubmit={submit} className="w-full max-w-sm" noValidate>
          <div className="mb-8">
            <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-gold-light"><Lock size={20} className="text-gold-dark" /></div>
            <h2 className="text-2xl font-semibold">Sign in</h2>
            <p className="mt-1 text-sm text-ink-soft">Use your M4 account (username or email).</p>
          </div>
          {params.get("expired") && <div className="mb-4"><Notice tone="warn">Your session expired. Please sign in again.</Notice></div>}
          <label className="label" htmlFor="login">Username or email</label>
          <input id="login" className="input mb-4" autoComplete="username" value={form.login} onChange={(e) => setForm({ ...form, login: e.target.value })} />
          <label className="label" htmlFor="password">Password</label>
          <input id="password" type="password" className="input mb-5" autoComplete="current-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          {error && <p role="alert" className="mb-4 text-sm text-red-700">{error}</p>}
          <button className="btn-primary w-full py-2.5" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
          <p className="mt-6 text-xs text-ink-soft">Accounts are created by an administrator. Demo accounts for local development are described in the README.</p>
        </form>
      </div>
    </div>
  );
}
