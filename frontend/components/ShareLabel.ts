import type { Risk, ShareEntry } from '../lib/types';

export const RISK_LABEL: Record<Risk, { label: string; cls: string }> = {
  critical: { label: 'Critique', cls: 'err' },
  high: { label: 'Élevé', cls: 'err' },
  medium: { label: 'Moyen', cls: 'warn' },
  low: { label: 'Faible', cls: 'off' },
  info: { label: 'Info', cls: 'off' },
};

export const RISK_ORDER: Risk[] = ['critical', 'high', 'medium', 'low', 'info'];

// Libellé lisible d'un partage : « Lien anonyme · modification », « Invité externe · lecture »…
export function shareLabel(e: Pick<ShareEntry, 'kind' | 'scope' | 'link_type' | 'roles' | 'external'>): string {
  const rights = e.link_type === 'edit' || e.roles.includes('write') ? 'modification' : 'lecture';
  if (e.kind === 'direct') return `${e.external ? 'Accès externe' : 'Accès direct'} · ${rights}`;
  const scope = e.scope === 'anonymous' ? 'Lien anonyme'
    : e.scope === 'organization' ? "Lien organisation" : 'Lien personnes précises';
  return `${scope} · ${rights}`;
}
