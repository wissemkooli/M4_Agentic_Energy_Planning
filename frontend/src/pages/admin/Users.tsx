import { useState, type FormEvent } from "react";
import { api, errorMessage } from "../../api/client";
import type { User } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { useApi } from "../../components/hooks";
import { Card, ErrorState, Loading, Notice, PageHeader } from "../../components/ui";

export function validateNewUser(f: { email: string; username: string; password: string }): string | null {
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email)) return "Enter a valid email address.";
  if (!/^[a-zA-Z0-9_.-]{3,80}$/.test(f.username)) return "Username: 3-80 letters, digits, '.', '_' or '-'.";
  if (f.password.length < 10 || !/\d/.test(f.password) || !/[a-zA-Z]/.test(f.password)) return "Password: at least 10 characters with letters and digits.";
  return null;
}

export default function UsersPage() {
  const { user: me } = useAuth();
  const { data, loading, error, reload } = useApi<User[]>("/api/admin/users");
  const [form, setForm] = useState({ email: "", username: "", full_name: "", password: "", role: "USER" });
  const [msg, setMsg] = useState<{ tone: "ok" | "warn"; text: string } | null>(null);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    const v = validateNewUser(form);
    if (v) return setMsg({ tone: "warn", text: v });
    try {
      await api.post("/api/admin/users", form);
      setMsg({ tone: "ok", text: `User ${form.email} created.` });
      setForm({ email: "", username: "", full_name: "", password: "", role: "USER" });
      reload();
    } catch (err) {
      setMsg({ tone: "warn", text: errorMessage(err) });
    }
  };
  const update = async (u: User, patch: Partial<User>) => {
    try {
      await api.patch(`/api/admin/users/${u.id}`, patch);
      reload();
    } catch (err) {
      setMsg({ tone: "warn", text: errorMessage(err) });
    }
  };

  return (
    <>
      <PageHeader eyebrow="Administration" title="Users and roles" subtitle="USER: submit planning requests and view own results. ADMIN: review, approve or reject strategies and manage accounts." />
      {msg && <div className="mb-4"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card title="Accounts">
          {loading ? <Loading /> : error ? <ErrorState message={error} /> : (
            <table className="w-full">
              <thead><tr><th className="th">User</th><th className="th">Role</th><th className="th">Active</th></tr></thead>
              <tbody>{data!.map((u) => (
                <tr key={u.id}>
                  <td className="td"><div className="font-medium">{u.username}</div><div className="text-xs text-ink-soft">{u.email}</div></td>
                  <td className="td">
                    <select className="input w-28" value={u.role} disabled={u.id === me?.id} onChange={(e) => update(u, { role: e.target.value as User["role"] })}>
                      <option>USER</option><option>ADMIN</option>
                    </select>
                  </td>
                  <td className="td"><input type="checkbox" className="accent-[#C6A04A]" checked={u.is_active} disabled={u.id === me?.id} onChange={(e) => update(u, { is_active: e.target.checked })} /></td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Card>
        <Card title="Create account">
          <form onSubmit={create} className="space-y-3" noValidate>
            <input className="input" placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            <input className="input" placeholder="Username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
            <input className="input" placeholder="Full name (optional)" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            <input className="input" type="password" placeholder="Initial password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            <select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}><option>USER</option><option>ADMIN</option></select>
            <button className="btn-primary w-full">Create</button>
          </form>
        </Card>
      </div>
    </>
  );
}
