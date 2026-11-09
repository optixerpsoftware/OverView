'use client';
import Link from 'next/link';
import { use, useState } from 'react';
import DriveExplorer from '../../../components/DriveExplorer';
import SharingAudit from '../../../components/SharingAudit';
import type { Connector } from '../../../lib/types';
import { useApi } from '../../../lib/useApi';

type Tab = 'audit' | 'explorer';

export default function SharingPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const connector = useApi<Connector>(`/connectors/${id}/`);
  const [tab, setTab] = useState<Tab>('audit');

  return (
    <>
      <div className="breadcrumb"><Link href="/">Organisations</Link> › Partage OneDrive &amp; SharePoint</div>
      <header className="hero">
        <div className="avatar-lg" aria-hidden="true">🔗</div>
        <div className="hero-id">
          <h1>Partage OneDrive &amp; SharePoint</h1>
          <div className="sub">{connector.data?.name ?? '…'}</div>
        </div>
      </header>

      <nav className="tabs" role="tablist">
        {([['audit', 'Audit des liens'], ['explorer', 'Explorateur de fichiers']] as [Tab, string][]).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </nav>
      <section className="panel">
        {tab === 'audit' ? <SharingAudit connectorId={id} /> : <DriveExplorer connectorId={id} />}
      </section>
    </>
  );
}
