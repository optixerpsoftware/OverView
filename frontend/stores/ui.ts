import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

export type Theme = 'auto' | 'light' | 'dark';

interface UiState {
  theme: Theme;
  /** Navigation latérale ouverte (petits écrans : tiroir). */
  navOpen: boolean;
  setTheme: (t: Theme) => void;
  setNavOpen: (open: boolean) => void;
}

/** Préférences d'affichage : thème (persisté) et tiroir de navigation (non persisté). */
export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      theme: 'auto',
      navOpen: false,
      setTheme: (theme) => set({ theme }),
      setNavOpen: (navOpen) => set({ navOpen }),
    }),
    {
      name: 'overview-ui',
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ theme: s.theme }),
    },
  ),
);
