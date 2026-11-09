import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

interface AuthState {
  access: string | null;
  refresh: string | null;
  setTokens: (access: string, refresh: string) => void;
  setAccess: (access: string) => void;
  clear: () => void;
}

/**
 * Session de l'utilisateur (jetons JWT), persistée dans localStorage.
 * Lisible hors React via `useAuthStore.getState()` (le client API en a besoin).
 * Le stockage étant synchrone, l'état est restauré dès le chargement du module côté navigateur ;
 * côté serveur (rendu initial) `localStorage` n'existe pas et la persistance est simplement ignorée.
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      access: null,
      refresh: null,
      setTokens: (access, refresh) => set({ access, refresh }),
      setAccess: (access) => set({ access }),
      clear: () => set({ access: null, refresh: null }),
    }),
    { name: 'overview-auth', storage: createJSONStorage(() => localStorage) },
  ),
);

export const isLoggedIn = (): boolean => !!useAuthStore.getState().access;

// Nettoyage unique des anciennes clés de session (avant Zustand) : elles ne sont plus lues.
if (typeof window !== 'undefined') {
  try {
    localStorage.removeItem('access');
    localStorage.removeItem('refresh');
  } catch {
    /* stockage indisponible : sans importance */
  }
}
