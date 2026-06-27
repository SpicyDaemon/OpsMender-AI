"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import {
  clearToken,
  getMe,
  getToken,
  login as apiLogin,
  setToken,
} from "@/lib/api";
import type { UserResponse } from "@/lib/types";

interface AuthContextValue {
  user: UserResponse | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<LoginOutcome>;
  logout: () => void;
  /** Re-fetch the current user (e.g. after a profile edit). */
  refresh: () => Promise<void>;
}

export interface LoginOutcome {
  mfaRequired: boolean;
  mfaToken: string | null;
  mfaEnrollmentRequired: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);

  // On mount, re-hydrate from stored token
  useEffect(() => {
    const token = getToken();
    if (!token) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoading(false);
      return;
    }
    getMe()
      .then((u) => setUser(u))
      // Don't clear the token here. A genuine 401 is already handled inside
      // `request()` (it clears the token and redirects to /login). This catch
      // only fires for non-auth failures — a transient network error or a
      // getMe aborted because we're mid-redirect. Clearing the token in those
      // cases logged the user out spuriously; leaving it lets the page
      // re-hydrate.
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const resp = await apiLogin(username, password);
    if (resp.mfa_required) {
      return {
        mfaRequired: true,
        mfaToken: resp.mfa_token ?? null,
        mfaEnrollmentRequired: false,
      };
    }
    if (!resp.access_token) {
      throw new Error("Login did not return an access token.");
    }
    setToken(resp.access_token);
    const me = await getMe();
    setUser(me);
    return {
      mfaRequired: false,
      mfaToken: null,
      mfaEnrollmentRequired:
        resp.mfa_enrollment_required || Boolean(me.mfa_enrollment_required),
    };
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    window.location.href = "/login";
  }, []);

  const refresh = useCallback(async () => {
    if (!getToken()) return;
    try {
      setUser(await getMe());
    } catch {
      /* keep the existing user on a transient failure */
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
