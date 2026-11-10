'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState, type ReactNode } from 'react';
import CommandPalette from './CommandPalette';
import { isPlatformHost } from '../lib/platform';
import ResponsiveTables from './ResponsiveTables';
import { logout } from '../lib/api';
import type { AlertCounts } from '../lib/types';
import { useApi } from '../lib/useApi';
import { ROLE_LABEL } from '../lib/useMe';
import type { Organization } from '../lib/types';
import { TENANT_TOOLS, useTenantContext } from '../lib/useTenantContext';
import { useRealtimeStore } from '../stores/realtime';
import { useUiStore, type Theme } from '../stores/ui';


const THEMES: { value: Theme; label: string; icon: string }[] = [
  { value: 'auto', label: 'Thème du système', icon: '🖥️' },
  { value: 'light', label: 'Thème clair', icon: '☀️' },
  { value: 'dark', label: 'Thème sombre', icon: '🌙' },
];

// Cadre façon Microsoft 365 : barre supérieure + navigation latérale qui suit l'organisation / le tenant ouverts.
export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname() ?? '/';
  const live = useRealtimeStore((s) => s.status);
  const { theme, setTheme, navOpen, setNavOpen } = useUiStore();
  const [searching, setSearching] = useState(false);
  const me = useApi<{ username: string; role: 'client' | 'viewer' | 'operator' | 'admin'; mfa_enabled: boolean }>('/accounts/me/', { enabled: path !== '/login' && !path.startsWith('/platform') });
  const isClient = me.data?.role === 'client';

  // Ctrl+K (ou ⌘K) ouvre la recherche globale depuis n'importe quelle page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setSearching(true); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Thème : attribut sur <html>, lu par la feuille de style (« auto » suit le système).
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'auto') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', theme);
  }, [theme]);

  useEffect(() => {
    if (isPlatformHost() && !path.startsWith('/platform')) window.location.replace('/platform');
  }, [path]);

  // Le tiroir de navigation (petits écrans) se referme à chaque changement de page.
  useEffect(() => { setNavOpen(false); }, [path, setNavOpen]);

  if (path === '/login' || path === '/platform/login') return <div className="content bare">{children}</div>;
  if (path.startsWith('/platform')) {
    return (
      <>
        <header className="topbar platform-bar">
          <Link href="/platform" className="brand">OverView · Plateforme</Link>
          <span className="spacer" />
          <button className="ghost logout" onClick={() => { logout(); window.location.href = '/platform/login'; }} aria-label="Se déconnecter">
            <span className="logout-text">Se déconnecter</span><span className="logout-icon" aria-hidden="true">⎋</span>
          </button>
        </header>
        <main id="main" className="content platform-content">{children}</main>
      </>
    );
  }

  const next = THEMES[(THEMES.findIndex((t) => t.value === theme) + 1) % THEMES.length];
  const current = THEMES.find((t) => t.value === theme) ?? THEMES[0];

  return (
    <>
      <a href="#main" className="skip-link">Aller au contenu</a>
      <header className="topbar">
        <button className="waffle" aria-label="Ouvrir la navigation" aria-expanded={navOpen}
          onClick={() => setNavOpen(!navOpen)}>
          {Array.from({ length: 9 }).map((_, i) => <i key={i} />)}
        </button>
        <Link href="/" className="brand">OverView</Link>
        {!isClient && (
          <button className="searchbtn" onClick={() => setSearching(true)} aria-label="Recherche globale (Ctrl+K)">
            <span aria-hidden="true">🔍</span> <span className="searchbtn-text">Rechercher</span> <kbd>Ctrl K</kbd>
          </button>
        )}
        <span className="spacer" />
        <span className={`live ${live}`} title={live === 'open' ? 'Mises à jour en direct (WebSocket)' : 'Temps réel indisponible : rechargement périodique'}>
          <i /> {live === 'open' ? 'En direct' : live === 'connecting' ? 'Connexion…' : 'Hors ligne'}
        </span>
        <button className="ghost icon" title={`${current.label} — passer au ${next.label.toLowerCase()}`}
          aria-label={`${current.label}. Passer au ${next.label.toLowerCase()}`} onClick={() => setTheme(next.value)}>
          {current.icon}
        </button>
        <button className="ghost logout" onClick={() => { logout(); window.location.href = '/login'; }} aria-label="Se déconnecter">
          <span className="logout-text">Se déconnecter</span><span className="logout-icon" aria-hidden="true">⎋</span>
        </button>
        <Link href="/account" className="avatar" title={me.data ? `${me.data.username} — ${ROLE_LABEL[me.data.role]}${me.data.mfa_enabled ? '' : ' (MFA non activé)'}` : 'Mon compte'}>
          {(me.data?.username ?? 'OV').slice(0, 2).toUpperCase()}
          {me.data && !me.data.mfa_enabled && <i className="avatar-warn" aria-label="MFA non activé" />}
        </Link>
      </header>
      {navOpen && <div className="nav-backdrop" onClick={() => setNavOpen(false)} aria-hidden="true" />}
      {isClient ? <ClientNav open={navOpen} path={path} /> : <SideNav open={navOpen} path={path} me={me} />}
      <main id="main" className="content">{children}</main>
      <CommandPalette open={searching} onClose={() => setSearching(false)} />
      <ResponsiveTables />
    </>
  );
}

/** Portail client : uniquement ses alertes et ses rapports (le serveur limite de toute façon les données). */
function ClientNav({ open, path }: { open: boolean; path: string }) {
  const orgs = useApi<Organization[]>('/organizations/');
  return (
    <nav className={`sidenav ${open ? 'open' : ''}`} aria-label="Navigation principale">
      <div className="section">Mon espace</div>
      <Link href="/" className={path === '/' ? 'active' : ''}><span aria-hidden="true">📊</span> Tableau de bord</Link>
      <Link href="/alerts" className={path.startsWith('/alerts') ? 'active' : ''}><span aria-hidden="true">🚨</span> Alertes</Link>
      {(orgs.data ?? []).map((o) => (
        <Link key={o.id} href={`/reports/${o.id}`} className={path === `/reports/${o.id}` ? 'active' : ''}>
          <span aria-hidden="true">📄</span> <span className="ellipsis">Rapport — {o.name}</span>
        </Link>
      ))}
      <Link href="/account" className={path === '/account' ? 'active' : ''}><span aria-hidden="true">👤</span> Mon compte</Link>
    </nav>
  );
}

function SideNav({ open, path, me }: { open: boolean; path: string; me?: { data: { role: string } | null } }) {
  const ctx = useTenantContext();
  const counts = useApi<AlertCounts>('/insights/alerts/counts/', { refetchInterval: () => 60_000 });
  const at = (href: string) => (path === href || path.startsWith(`${href}/`) ? 'active' : '');

  return (
    <nav className={`sidenav ${open ? 'open' : ''}`} aria-label="Navigation principale">
      <div className="section">Administration</div>
      <Link href="/" className={path === '/' ? 'active' : ''}><span aria-hidden="true">📊</span> Tableau de bord</Link>
      <Link href="/queue" className={at('/queue')}><span aria-hidden="true">✅</span> À faire</Link>
      <Link href="/alerts" className={at('/alerts')}>
        <span aria-hidden="true">🚨</span> Alertes
        {!!counts.data?.total && <span className={`nav-count ${counts.data.critical + counts.data.high ? 'hot' : ''}`}>{counts.data.total}</span>}
      </Link>
      <Link href="/organizations" className={path === '/organizations' ? 'active' : ''}><span aria-hidden="true">🏢</span> Organisations</Link>
      <Link href="/account" className={path === '/account' ? 'active' : ''}><span aria-hidden="true">👤</span> Mon compte</Link>
      <Link href="/settings/subscription" className={path === '/settings/subscription' ? 'active' : ''}><span aria-hidden="true">📦</span> Abonnement</Link>
      {me?.data?.role === 'admin' && (
        <Link href="/settings/users" className={path === '/settings/users' ? 'active' : ''}><span aria-hidden="true">🔑</span> Comptes OverView</Link>
      )}

      {ctx.orgId && (
        <>
          <div className="section" title={ctx.org?.name}>{ctx.org?.name ?? 'Organisation'}</div>
          <Link href={`/organizations/${ctx.orgId}`} className={at(`/organizations/${ctx.orgId}`)}>
            <span aria-hidden="true">📋</span> Vue d’ensemble
          </Link>
          <Link href={`/reports/${ctx.orgId}`} className={at(`/reports/${ctx.orgId}`)}>
            <span aria-hidden="true">📄</span> Rapport
          </Link>
          <Link href={`/protection/${ctx.orgId}`} className={at(`/protection/${ctx.orgId}`)}>
            <span aria-hidden="true">🛡️</span> Protection automatique
          </Link>
        </>
      )}

      {ctx.connectorId && (
        <>
          <div className="section" title={ctx.connector?.name}>Tenant · {ctx.connector?.name ?? '…'}</div>
          {TENANT_TOOLS.map((t) => (
            <Link key={t.prefix} href={`/${t.prefix}/${ctx.connectorId}`} className={at(`/${t.prefix}/${ctx.connectorId}`)}>
              <span aria-hidden="true">{t.icon}</span> {t.label}
            </Link>
          ))}
        </>
      )}

      {ctx.mailbox && (
        <>
          <div className="section">Boîte</div>
          <Link href={`/mailboxes/${ctx.mailbox.id}`} className="active" title={ctx.mailbox.user_principal_name}>
            <span aria-hidden="true">👤</span> <span className="ellipsis">{ctx.mailbox.user_principal_name}</span>
          </Link>
        </>
      )}
    </nav>
  );
}
