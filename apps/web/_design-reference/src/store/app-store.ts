import { create } from "zustand";

interface AppState {
  navCollapsed: boolean;
  toggleNav: () => void;
  activePersona: string;
  setPersona: (p: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  navCollapsed: false,
  toggleNav: () => set((s) => ({ navCollapsed: !s.navCollapsed })),
  activePersona: "romance",
  setPersona: (p) => set({ activePersona: p }),
}));
