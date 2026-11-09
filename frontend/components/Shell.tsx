'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { logout } from '../lib/api';
import { useRealtimeStore } from '../stores/realtime';

// Cadre façon Microsoft 365 : barre supérieure bleue + navigation latérale.
export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname() ?? '/';
  const live = useRealtimeStore((s) => s.status);

  if (path === '/login') return <div className="content bare">{children}</div>;

  return (
    <>
      <header className="topbar">
        <span className="waffle" aria-hidden="true">
          {Array.from({ length: 9 }).map((_, i) => <i key={i} />)}
        </span>
        <span className="brand">OverView</span>
        <span className="spacer" />
        <span className={`live ${live}`} title={live === 'open' ? 'Mises à jour en direct (WebSocket)' : 'Temps réel indisponible : rechargement périodique'}>
          <i /> {live === 'open' ? 'En direct' : live === 'connecting' ? 'Connexion…' : 'Hors ligne'}
        </span>
        <button
          className="ghost"
          onClick={() => { logout(); window.location.href = '/login'; }}
        >
          Se déconnecter
        </button>
        <span className="avatar" title="Compte">OV</span>
      </header>
      <nav className="sidenav">
        <div className="section">Administration</div>
        <Link href="/" className={path === '/' || path.startsWith('/organizations') ? 'active' : ''}>
          🏢 Organisations
        </Link>
      </nav>
      <main className="content">{children}</main>
    </>
  );
}
