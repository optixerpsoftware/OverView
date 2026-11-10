export type LimitKind = 'tenants' | 'organizations' | 'users';

export interface PlatformMsp {
  id: number; name: string; slug: string; schema: string; active: boolean; created_at: string;
  domains: { id: number; domain: string; primary: boolean }[];
  stats: { organizations?: number; connectors?: number; mailboxes?: number; users?: number; open_alerts?: number; critical_alerts?: number; error?: string };
  plan: string; plan_label: string;
  limits: Record<LimitKind, number | null>;
  usage: Record<LimitKind, number>;
  expires_at: string | null; expired: boolean;
  contact_name: string; contact_email: string; notes: string;
  alerts: { level: 'warn' | 'err'; text: string }[];
}

export interface PlatformMspDetail extends PlatformMsp {
  users: { id: number; username: string; email: string; active: boolean; role: string; mfa: boolean; last_login: string | null }[];
  organizations: { id: string; name: string; tenants: { name: string; active: boolean; error: boolean }[] }[];
  history: { at: string; actor: string; action: string; detail: string }[];
  detail_error?: string;
}

export type Plans = Record<string, { label: string; max_tenants?: number | null; max_organizations?: number | null; max_users?: number | null }>;

export const LIMIT_LABEL: Record<LimitKind, string> = { tenants: 'Tenants M365', organizations: 'Organisations', users: 'Comptes' };

export const ACTION_LABEL: Record<string, string> = {
  login: 'Connexion', msp_create: 'MSP créé', msp_update: 'MSP modifié', domain_add: 'Adresse ajoutée', domain_remove: 'Adresse retirée',
  rescue_admin: 'Administrateur de secours', admin_create: 'Super-administrateur créé', subscription: 'Abonnement modifié',
  msp_user_mfa_reset: 'MFA d’un compte réinitialisé', admin_disable: 'Super-administrateur désactivé',
  admin_active: 'Super-administrateur réactivé', admin_mfa_reset: 'MFA d’un super-administrateur réinitialisé',
};

/** Barre « utilisé / limite » (illimité si la limite est nulle). */
export function usageLevel(used: number, limit: number | null): 'ok' | 'warn' | 'err' {
  if (limit === null) return 'ok';
  if (used >= limit) return 'err';
  return limit > 0 && used / limit >= 0.8 ? 'warn' : 'ok';
}
