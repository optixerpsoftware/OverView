'use client';
import Link from 'next/link';
import type { LimitKind } from '../lib/platformTypes';
import { useSubscription } from '../lib/subscription';

const NOUN: Record<LimitKind, string> = { tenants: 'tenants Microsoft 365', organizations: 'organisations', users: 'comptes' };

/** Rappel discret « 2 / 3 tenants Microsoft 365 » à côté d'un bouton de création (rien si illimité). */
export default function QuotaHint({ kind }: { kind: LimitKind }) {
  const { sub } = useSubscription();
  if (!sub || sub.limits[kind] === null) return null;
  const used = sub.usage[kind];
  const limit = sub.limits[kind] as number;
  const level = used >= limit ? 'err' : limit && used / limit >= 0.8 ? 'warn' : 'ok';
  return (
    <Link href="/settings/subscription" className={`quota-hint quota-${level}`} title="Voir mon abonnement">
      {used} / {limit} {NOUN[kind]}{used >= limit ? ' · limite atteinte' : ''}
    </Link>
  );
}
