'use client';
import { usePathname } from 'next/navigation';
import type { Connector, Mailbox, Organization } from './types';
import { useApi } from './useApi';

/** Pages rattachées à un connecteur (tenant M365) : la navigation latérale propose les autres outils du même tenant. */
export const TENANT_TOOLS = [
  { prefix: 'compliance', label: 'Gouvernance', icon: '🛡️' },
  { prefix: 'sharing', label: 'Partage', icon: '🔗' },
  { prefix: 'conditional-access', label: 'Accès conditionnel', icon: '🔐' },
  { prefix: 'identity-hygiene', label: 'Hygiène des identités', icon: '🧹' },
  { prefix: 'mailflow', label: 'Messagerie', icon: '✉️' },
  { prefix: 'device-registration', label: 'Inscription des appareils', icon: '💻' },
] as const;

export interface TenantContext {
  section: string;               // premier segment de l'URL (organizations, compliance, mailboxes…)
  orgId: string | null;
  connectorId: string | null;
  org: Organization | null;
  connector: Connector | null;
  mailbox: Mailbox | null;
}

const UUID = /^[0-9a-f-]{36}$/i;

/** Déduit l'organisation / le tenant / la boîte de l'URL courante. Les lectures sont mises en cache (TanStack Query) :
 *  la page et la navigation partagent les mêmes requêtes. */
export function useTenantContext(): TenantContext {
  const path = usePathname() ?? '/';
  const [section = '', rawId = ''] = path.split('/').filter(Boolean);
  const id = UUID.test(rawId) ? rawId : '';
  const isTool = TENANT_TOOLS.some((t) => t.prefix === section);

  const mailbox = useApi<Mailbox>(`/outlook/mailboxes/${id}/`, { enabled: section === 'mailboxes' && !!id });
  const connectorId = isTool ? id : section === 'mailboxes' ? mailbox.data?.connector_id ?? '' : '';
  const connector = useApi<Connector>(`/connectors/${connectorId}/`, { enabled: !!connectorId });
  const orgId = section === 'organizations' || section === 'reports' || section === 'protection' ? id : connector.data?.org_id ?? mailbox.data?.org_id ?? '';
  const org = useApi<Organization>(`/organizations/${orgId}/`, { enabled: !!orgId });

  return {
    section,
    orgId: orgId || null,
    connectorId: connectorId || null,
    org: org.data,
    connector: connector.data,
    mailbox: mailbox.data,
  };
}
