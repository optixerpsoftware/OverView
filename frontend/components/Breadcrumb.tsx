'use client';
import Link from 'next/link';
import { Fragment, useEffect } from 'react';
import { useTenantContext } from '../lib/useTenantContext';

/** Fil d'Ariane complet : Organisations › organisation › page. Met aussi à jour le titre de l'onglet. */
export default function Breadcrumb({ current }: { current?: string }) {
  const ctx = useTenantContext();
  const parts: { label: string; href?: string }[] = [{ label: 'Organisations', href: '/organizations' }];
  if (ctx.orgId && ctx.section !== 'organizations') {
    parts.push({ label: ctx.org?.name ?? '…', href: `/organizations/${ctx.orgId}` });
  }
  if (ctx.section === 'organizations') parts.push({ label: ctx.org?.name ?? '…' });
  if (current) parts.push({ label: current });

  const title = [current, ctx.section === 'organizations' ? ctx.org?.name : null, ctx.org && ctx.section !== 'organizations' ? ctx.org.name : null]
    .filter(Boolean).join(' · ');
  useEffect(() => { document.title = title ? `${title} — OverView` : 'OverView'; }, [title]);

  return (
    <nav className="breadcrumb" aria-label="Fil d’Ariane">
      {parts.map((p, i) => (
        <Fragment key={i}>
          {i > 0 && <span className="sep" aria-hidden="true">›</span>}
          {p.href && i < parts.length - 1 ? <Link href={p.href}>{p.label}</Link> : <span aria-current={i === parts.length - 1 ? 'page' : undefined}>{p.label}</span>}
        </Fragment>
      ))}
    </nav>
  );
}
