import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { Loading } from "../components/ui";

/** Frontend guards improve navigation only; the backend enforces every permission. */
export function RequireAuth() {
  const { user, loading, sessionExpired } = useAuth();
  const loc = useLocation();
  if (loading) return <Loading label="Restoring session…" full />;
  if (!user) return <Navigate to={`/login${sessionExpired ? "?expired=1" : ""}`} state={{ from: loc.pathname }} replace />;
  return <Outlet />;
}

export function RequireAdmin() {
  const { user } = useAuth();
  if (user?.role !== "ADMIN") return <Navigate to="/access-denied" replace />;
  return <Outlet />;
}

export function HomeRedirect() {
  const { user } = useAuth();
  return <Navigate to={user?.role === "ADMIN" ? "/admin" : "/dashboard"} replace />;
}
