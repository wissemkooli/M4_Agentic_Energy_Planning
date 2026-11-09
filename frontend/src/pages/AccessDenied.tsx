import { ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";

export default function AccessDenied() {
  return (
    <div className="card mx-auto mt-16 max-w-lg p-10 text-center">
      <ShieldAlert className="mx-auto mb-4 text-gold" size={36} />
      <h1 className="text-xl font-semibold">Access denied</h1>
      <p className="mt-2 text-sm text-ink-soft">This area is reserved for administrators. Permissions are enforced by the backend.</p>
      <Link to="/dashboard" className="btn-primary mt-6">Back to dashboard</Link>
    </div>
  );
}
