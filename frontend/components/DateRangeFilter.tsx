'use client';
import type { ReactNode } from 'react';

export interface Preset { label: string; from: string; to: string }

/** Date locale AAAA-MM-JJ d'il y a `daysAgo` jours. */
export function isoDay(daysAgo: number): string {
  const t = new Date();
  t.setDate(t.getDate() - daysAgo);
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}

/** `iso` est-il dans [from, to] (bornes AAAA-MM-JJ incluses, heure locale) ? Sans date : faux si un filtre est actif. */
export function inRange(iso: string | null, from: string, to: string): boolean {
  if (!from && !to) return true;
  if (!iso) return false;
  const t = new Date(iso).getTime();
  if (from && t < new Date(`${from}T00:00:00`).getTime()) return false;
  if (to && t > new Date(`${to}T23:59:59.999`).getTime()) return false;
  return true;
}

interface Props {
  presets: Preset[];
  from: string;
  to: string;
  onChange: (from: string, to: string) => void;
  /** Libellé du premier champ date (ex. « Créé du », « Du »). */
  label?: string;
  shown: number;
  total: number;
  noun: string;                 // « appareil », « connexion »…
  extra?: ReactNode;            // filtres supplémentaires (ex. statut)
  onReset?: () => void;
  canReset?: boolean;
}

// Barre de filtre par période : raccourcis + dates libres + compteur.
export default function DateRangeFilter({
  presets, from, to, onChange, label = 'Du', shown, total, noun, extra, onReset, canReset,
}: Props) {
  return (
    <div className="filterbar">
      <div className="chip-group" role="group" aria-label="Période">
        {presets.map((p) => (
          <button key={p.label} className={`chip-btn ${from === p.from && to === p.to ? 'on' : ''}`}
            onClick={() => onChange(p.from, p.to)}>{p.label}</button>
        ))}
      </div>
      <label className="date-field">{label}
        <input type="date" value={from} max={to || undefined} onChange={(e) => onChange(e.target.value, to)} />
      </label>
      <label className="date-field">au
        <input type="date" value={to} min={from || undefined} onChange={(e) => onChange(from, e.target.value)} />
      </label>
      {extra}
      <span className="sub">{shown} / {total} {noun}{total > 1 ? 's' : ''}</span>
      {canReset && onReset && <button className="ghost" onClick={onReset}>Réinitialiser</button>}
    </div>
  );
}
