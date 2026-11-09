'use client';
import { useState, type ReactNode } from 'react';

interface Props {
  title: string;
  children: ReactNode;
  /** Si fourni : texte exact à retaper pour activer le bouton (actions très destructrices). */
  requireText?: string;
  confirmLabel: string;
  /** Bouton de confirmation rouge (action destructrice). */
  danger?: boolean;
  busy?: boolean;
  onConfirm: (typed: string) => void;
  onCancel: () => void;
}

// Boîte de dialogue de confirmation dans l'app. Remplace window.confirm, que certains
// navigateurs intégrés bloquent silencieusement (le bouton semblait alors « ne rien faire »).
export default function ConfirmDialog({
  title, children, requireText, confirmLabel, danger = true, busy, onConfirm, onCancel,
}: Props) {
  const [typed, setTyped] = useState('');
  const ok = !requireText || typed.trim().toLowerCase() === requireText.toLowerCase();

  return (
    <div className="overlay" role="dialog" aria-modal="true" onKeyDown={(e) => e.key === 'Escape' && onCancel()}>
      <div className="dialog">
        <h2>{title}</h2>
        <div className="dialog-body">{children}</div>
        {requireText && (
          <label className="field">
            <span>Pour confirmer, saisis <strong>{requireText}</strong></span>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus autoComplete="off" />
          </label>
        )}
        <div className="dialog-actions">
          <button onClick={onCancel} disabled={busy}>Annuler</button>
          <button
            className={`primary ${danger ? 'danger-solid' : ''}`}
            disabled={!ok || busy}
            autoFocus={!requireText}
            onClick={() => onConfirm(typed)}
          >
            {busy ? 'En cours…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
