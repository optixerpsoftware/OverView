import { useApi } from './useApi';

export type Role = 'client' | 'viewer' | 'operator' | 'admin';
export interface Me { username: string; email: string; role: Role; mfa_enabled: boolean; mfa_required: boolean }

export const ROLE_LABEL: Record<Role, string> = {
  client: 'Client (portail)', viewer: 'Lecture seule', operator: 'Technicien', admin: 'Administrateur',
};

/** Compte connecté (mis en cache : un seul appel partagé par toute l'interface). */
export function useMe(enabled = true) {
  const me = useApi<Me>('/accounts/me/', { enabled });
  return { me: me.data, isClient: me.data?.role === 'client', canAct: !!me.data && ['operator', 'admin'].includes(me.data.role) };
}
