import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, refreshAccessToken, setAccessToken, setSessionExpiredHandler } from "../api/client";
import type { User } from "../api/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  sessionExpired: boolean;
  login: (login: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

// Non-sensitive hint (no token): avoids a pointless refresh call when no session can exist.
const HINT = "m4_session_hint";
const hint = {
  get: () => { try { return localStorage.getItem(HINT) === "1"; } catch { return true; } },
  set: (v: boolean) => { try { if (v) localStorage.setItem(HINT, "1"); else localStorage.removeItem(HINT); } catch { /* storage unavailable */ } },
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionExpired, setSessionExpired] = useState(false);

  useEffect(() => {
    setSessionExpiredHandler(() => {
      setUser(null);
      setSessionExpired(true);
    });
    // Restore the session from the httpOnly refresh cookie (page reload).
    if (!hint.get()) {
      setLoading(false);
      return;
    }
    refreshAccessToken()
      .then(async (token) => {
        if (token) {
          const me = await api.get<User>("/api/auth/me");
          setUser(me.data);
        } else hint.set(false);
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (login: string, password: string) => {
    const r = await api.post("/api/auth/login", { login, password });
    setAccessToken(r.data.access_token);
    setUser(r.data.user);
    setSessionExpired(false);
    hint.set(true);
    return r.data.user as User;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout", {});
    } finally {
      setAccessToken(null);
      setUser(null);
      hint.set(false);
    }
  }, []);

  const value = useMemo(() => ({ user, loading, sessionExpired, login, logout }), [user, loading, sessionExpired, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
