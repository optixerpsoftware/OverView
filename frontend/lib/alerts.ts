import type { AlertCategory, AlertSeverity as Severity } from './types';

export const SEVERITY: Record<Severity, { label: string; cls: string }> = {
  critical: { label: 'Critique', cls: 'err' },
  high: { label: 'Élevée', cls: 'err' },
  medium: { label: 'Moyenne', cls: 'warn' },
  low: { label: 'Faible', cls: 'off' },
};
export const SEVERITY_ORDER: Severity[] = ['critical', 'high', 'medium', 'low'];

export const CATEGORY: Record<AlertCategory, { label: string; icon: string }> = {
  sharing: { label: 'Partage', icon: '🔗' },
  rule: { label: 'Règle Outlook', icon: '📨' },
  identity: { label: 'Compte', icon: '👤' },
  signin: { label: 'Connexion', icon: '🔑' },
  governance: { label: 'Gouvernance', icon: '🛡️' },
  quarantine: { label: 'Quarantaine', icon: '🧪' },
  app: { label: 'Application tierce', icon: '🧩' },
  protection: { label: 'Protection automatique', icon: '🛡️' },
};

/** « il y a 3 h », « il y a 2 j » : lecture rapide de l'ancienneté d'une alerte. */
export function ago(iso: string | null | undefined): string {
  if (!iso) return '—';
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'à l’instant';
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  return `il y a ${Math.round(s / 86400)} j`;
}
