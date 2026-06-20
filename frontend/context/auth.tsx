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
  clearOrgId,
  clearToken,
  getMe,
  getToken,
  listMyOrganizations,
  login as apiLogin,
  setOrgId,
  setToken,
} from "@/lib/api";
import {
  getOrgSlug,
  scopeDashboardPath,
  setOrgSlug,
  stripOrgScope,
} from "@/lib/org-path";
import type { UserResponse } from "@/lib/types";

interface AuthContextValue {
  user: UserResponse | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  /** Re-fetch the current user (e.g. after a profile edit). */
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const syncOrganization = useCallback(async (currentUser: UserResponse) => {
    if (currentUser.primary_org_id) setOrgId(currentUser.primary_org_id);
    try {
      const organizations = await listMyOrganizations();
      const requestedSlug = getOrgSlug();
      const active =
        organizations.items.find((org) => org.slug === requestedSlug) ??
        organizations.items.find(
          (org) => org.id === currentUser.primary_org_id,
        ) ??
        organizations.items.find((org) => org.is_primary) ??
        organizations.items[0];
      if (active) {
        setOrgId(active.id);
        setOrgSlug(active.slug);
        if (
          requestedSlug &&
          requestedSlug !== active.slug &&
          window.location.pathname.startsWith("/o/")
        ) {
          const target = scopeDashboardPath(
            stripOrgScope(window.location.pathname),
            active.slug,
          );
          window.location.replace(`${target}${window.location.search}`);
        }
      }
    } catch {
      // Keep the primary organization id when the list is unavailable.
    }
  }, []);

  // On mount, re-hydrate from stored token
  useEffect(() => {
    const token = getToken();
    if (!token) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoading(false);
      return;
    }
    getMe()
      .then(async (u) => {
        setUser(u);
        await syncOrganization(u);
      })
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, [syncOrganization]);

  const login = useCallback(async (username: string, password: string) => {
    const resp = await apiLogin(username, password);
    setToken(resp.access_token);
    const me = await getMe();
    setUser(me);
    await syncOrganization(me);
  }, [syncOrganization]);

  const logout = useCallback(() => {
    clearToken();
    clearOrgId();
    setOrgSlug(null);
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
