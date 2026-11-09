import { create } from 'zustand';

export interface Toast {
  id: number;
  text: string;
  error: boolean;
}

interface ToastState {
  toasts: Toast[];
  push: (text: string, error?: boolean) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;
const DURATION = { ok: 5000, error: 9000 };

/** Notifications globales (affichées par <Toasts/>). Les erreurs restent plus longtemps. */
export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  push: (text, error = false) => {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, text, error }] }));   // 4 messages au maximum
    setTimeout(() => get().dismiss(id), error ? DURATION.error : DURATION.ok);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Raccourcis utilisables partout, y compris hors composants. */
export const notify = {
  ok: (text: string) => useToastStore.getState().push(text, false),
  error: (text: string) => useToastStore.getState().push(text, true),
};
