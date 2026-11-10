import type { LimitKind } from './platformTypes';
import { useApi } from './useApi';
import { useMe } from './useMe';

export interface Subscription {
  name: string; plan: string; plan_label: string;
  limits: Record<LimitKind, number | null>;
  usage: Record<LimitKind, number>;
  remaining: Record<LimitKind, number | null>;
  expires_at: string | null; expired: boolean;
  alerts: { level: 'warn' | 'err'; text: string }[];
}

/** Abonnement du MSP connecté (quotas et consommation). Pas chargé pour le rôle Client (portail). */
export function useSubscription() {
  const { me, isClient } = useMe();
  const sub = useApi<Subscription>('/subscription/', { enabled: !!me && !isClient });
  const full = (k: LimitKind) => sub.data?.remaining[k] === 0;
  return { sub: sub.data, full };
}
