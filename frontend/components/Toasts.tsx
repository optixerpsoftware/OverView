'use client';
import { useToastStore } from '../stores/toasts';

// Pile de notifications en bas à droite (alimentée par le store Zustand `toasts`).
export default function Toasts() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  if (toasts.length === 0) return null;
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.error ? 'error' : ''}`}>
          <span style={{ flex: 1 }}>{t.text}</span>
          <button className="ghost" aria-label="Fermer" onClick={() => dismiss(t.id)}>✕</button>
        </div>
      ))}
    </div>
  );
}
