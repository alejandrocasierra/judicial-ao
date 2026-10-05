"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface User {
  id: string;
  email: string;
  full_name: string;
  org_role: string;
  locale: string;
  organization_id: string;
  permissions: string[];
}

interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isAuthenticated: boolean;
  hasHydrated: boolean;
  setHasHydrated: (v: boolean) => void;
  login: (email: string, password: string) => Promise<void>;
  setTokens: (accessToken: string, refreshToken: string) => void;
  logout: () => void;
  setUser: (user: User) => void;
}

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      accessToken: null,
      refreshToken: null,
      isAuthenticated: false,
      hasHydrated: false,

      setHasHydrated: (v) => set({ hasHydrated: v }),

      login: async (email: string, password: string) => {
        const { api } = await import("@/lib/api");
        const res = await api.post<{
          access_token: string;
          refresh_token: string;
          token_type: string;
          expires_in: number;
        }>("/auth/login", { email, password });

        localStorage.setItem("access_token", res.access_token);
        localStorage.setItem("refresh_token", res.refresh_token);

        const user = await api.get<User>("/auth/me");
        set({
          user,
          accessToken: res.access_token,
          refreshToken: res.refresh_token,
          isAuthenticated: true,
        });
      },

      setTokens: (accessToken, refreshToken) => {
        localStorage.setItem("access_token", accessToken);
        localStorage.setItem("refresh_token", refreshToken);
        set({ accessToken, refreshToken });
      },

      logout: () => {
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
        set({ user: null, accessToken: null, refreshToken: null, isAuthenticated: false });
      },

      setUser: (user) => set({ user }),
    }),
    {
      name: "auth-storage",
      // No persistir hasHydrated: siempre arranca en false y onRehydrateStorage lo pone en true.
      partialize: (s) => ({
        user: s.user,
        accessToken: s.accessToken,
        refreshToken: s.refreshToken,
        isAuthenticated: s.isAuthenticated,
      }),
      // Marca la hidratación completa para no redirigir antes de restaurar la sesión.
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
      },
    },
  ),
);
