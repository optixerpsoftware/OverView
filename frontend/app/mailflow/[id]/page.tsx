'use client';
import Link from 'next/link';
import { use, useState } from 'react';
import MailTrace from '../../../components/MailTrace';
import Quarantine from '../../../components/Quarantine';
import type { Connector } from '../../../lib/types';
import { useApi } from '../../../lib/useApi';
import Breadcrumb from '../../../components/Breadcrumb';

type Tab = 'flow' | 'quarantine';

export default function MailflowPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const connector = useApi<Connector>(`/connectors/${id}/`);
  const [tab, setTab] = useState<Tab>('flow');

  return (
    <div className="wide">
      <Breadcrumb current="Flux de messagerie & quarantaine" />
      <header className="hero">
        <div className="avatar-lg" aria-hidden="true">✉️</div>
        <div className="hero-id">
          <h1>Flux de messagerie &amp; quarantaine</h1>
          <div className="sub">{connector.data?.name ?? '…'}</div>
        </div>
      </header>

      <nav className="tabs" role="tablist">
        {([['flow', 'Flux de messagerie'], ['quarantine', 'Quarantaine']] as [Tab, string][]).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </nav>
      <section className="panel">
        {tab === 'flow' ? <MailTrace connectorId={id} /> : <Quarantine connectorId={id} />}
      </section>
    </div>
  );
}
