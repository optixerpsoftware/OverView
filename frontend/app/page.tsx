'use client';
import Link from 'next/link';
import { useEffect } from 'react';
import { AlertTrendChart, Donut, HBars, SEV_COLORS, Spark } from '../components/Charts';
import Loading from '../components/Loading';
import ScoreRing from '../components/ScoreRing';
import { ago, CATEGORY, SEVERITY, SEVERITY_ORDER } from '../lib/alerts';
import type { AlertCategory, Dashboard, DashboardTenant } from '../lib/types';
import { useApi } from '../lib/useApi';

// Tableau de bord global : l'état de sécurité de tous les tenants en un coup d'œil, les urgences en premier.
export default function DashboardPage() {
  const dash = useApi<Dashboard>('/insights/dashboard/', { refetchInterval: () => 60_000 });
  useEffect(() => { document.title = 'Tableau de bord — OverView'; }, []);
  const d = dash.data;

  return (
    <>
      <div className="pagehead">
        <div>
          <h1>Tableau de bord</h1>
          <div className="sub">Tous les tenants · données des dernières synchronisations</div>
        </div>
        <Link href="/alerts" className="btn">Centre d’alertes{d?.alerts.total ? ` (${d.alerts.total})` : ''}</Link>
      </div>
      {dash.error && <div className="messagebar error">{dash.error}</div>}
      {dash.loading && <Loading lines={6} />}

      {d && (
        <>
          <div className="tiles">
            <Link href="/alerts?severity=critical" className={`tile ${d.alerts.critical ? 'warn' : ''}`}>
              <span className="tile-label">Alertes critiques</span><span className="tile-value">{d.alerts.critical}</span>
              <span className="tile-hint">à traiter en priorité</span>
            </Link>
            <Link href="/alerts?severity=high" className={`tile ${d.alerts.high ? 'warn' : ''}`}>
              <span className="tile-label">Alertes élevées</span><span className="tile-value">{d.alerts.high}</span>
              <span className="tile-hint">{d.alerts.medium} moyenne(s)</span>
            </Link>
            <Link href="/alerts?category=sharing" className={`tile ${d.totals.anonymous_links + d.totals.external_shares ? 'warn' : ''}`}>
              <span className="tile-label">Partages vers l’extérieur</span>
              <span className="tile-value">{d.totals.external_shares + d.totals.anonymous_links}</span>
              <span className="tile-hint">{d.totals.anonymous_links} lien(s) anonyme(s) · {d.totals.anonymous_edit} modifiable(s)</span>
            </Link>
            <Link href="/alerts?category=identity" className={`tile ${d.totals.no_mfa ? 'warn' : ''}`}>
              <span className="tile-label">Comptes sans MFA</span><span className="tile-value">{d.totals.no_mfa}</span>
              <span className="tile-hint">sur {d.totals.mailboxes} compte(s) suivi(s)</span>
            </Link>
            <Link href="/alerts?category=rule" className={`tile ${d.totals.suspicious_rules ? 'warn' : ''}`}>
              <span className="tile-label">Règles suspectes</span><span className="tile-value">{d.totals.suspicious_rules}</span>
              <span className="tile-hint">transfert externe ou suppression</span>
            </Link>
          </div>

          <section className="card chart-card">
            <div className="card-head">
              <div>
                <h2>Évolution des alertes (30 jours)</h2>
                <div className="sub">
                  Nouvelles alertes par jour et alertes actives
                  {d.charts.mttr_hours !== null && ` · délai moyen de résolution : ${d.charts.mttr_hours < 48 ? `${d.charts.mttr_hours} h` : `${Math.round(d.charts.mttr_hours / 24)} j`}`}
                </div>
              </div>
            </div>
            <div className="chart-row">
              <div className="chart-main"><AlertTrendChart data={d.charts.trend} /></div>
              <div className="chart-side">
                <Donut label="ouvertes" parts={SEVERITY_ORDER.map((s) => ({ key: s, value: d.charts.by_severity[s] ?? 0, color: SEV_COLORS[s], label: SEVERITY[s].label }))} />
                <HBars rows={Object.entries(d.charts.by_category).map(([k, v]) => ({
                  label: `${CATEGORY[k as AlertCategory]?.icon ?? ''} ${CATEGORY[k as AlertCategory]?.label ?? k}`, value: v ?? 0,
                }))} />
              </div>
            </div>
          </section>

          <div className="dash-grid">
            <section className="card">
              <div className="card-head">
                <h2>Urgences</h2>
                <Link href="/alerts">Tout voir</Link>
              </div>
              {d.top_alerts.length === 0 ? (
                <div className="empty-state"><div className="big" aria-hidden="true">✅</div>Aucune alerte ouverte.</div>
              ) : d.top_alerts.map((a) => (
                <Link key={a.id} href={a.link || '/alerts'} className="alert-row">
                  <span className={`badge ${SEVERITY[a.severity].cls}`}>{SEVERITY[a.severity].label}</span>
                  <span className="grow">
                    <span className="ellipsis" style={{ display: 'block' }}>{CATEGORY[a.category]?.icon} {a.title}</span>
                    <span className="sub">{a.connector_name} · {ago(a.first_seen)}</span>
                  </span>
                </Link>
              ))}
            </section>

            <section className="card">
              <div className="card-head"><h2>Tenants</h2><Link href="/organizations">Organisations</Link></div>
              {d.tenants.length === 0 && (
                <div className="empty-state">
                  <div className="big" aria-hidden="true">🔌</div>
                  <p>Aucun tenant connecté.</p>
                  <Link href="/organizations" className="btn">Connecter Microsoft 365</Link>
                </div>
              )}
              {d.tenants.map((t) => <TenantRow key={t.connector_id} t={t} />)}
            </section>
          </div>
        </>
      )}
    </>
  );
}

function TenantRow({ t }: { t: DashboardTenant }) {
  return (
    <div className="tenant-row">
      <div className="tenant-score">
        <ScoreRing score={t.score} size={56} />
        <Spark values={t.score_trend} width={56} height={18} />
      </div>
      <div className="grow">
        <Link href={`/organizations/${t.org_id}`}><strong>{t.name}</strong></Link>
        <div className="sub">{t.org_name} · {t.mailboxes} compte(s){t.connection_error ? ' · connexion en échec' : ''}</div>
        <div className="chip-group" style={{ marginTop: 6 }}>
          {t.alerts.critical > 0 && <span className="badge err">{t.alerts.critical} critique(s)</span>}
          {t.alerts.high > 0 && <span className="badge err">{t.alerts.high} élevée(s)</span>}
          {t.sharing.anonymous + t.sharing.external > 0 && (
            <span className="badge warn">{t.sharing.anonymous + t.sharing.external} partage(s) externe(s)</span>
          )}
          {t.no_mfa > 0 && <span className="badge warn">{t.no_mfa} sans MFA</span>}
          {t.alerts.total === 0 && <span className="badge ok">Aucune alerte</span>}
        </div>
      </div>
      <div className="tenant-links">
        <Link href={`/compliance/${t.connector_id}`} className="btn">Gouvernance</Link>
        <Link href={`/sharing/${t.connector_id}`} className="btn">Partage</Link>
        <Link href={`/alerts?connector=${t.connector_id}`} className="btn">Alertes</Link>
      </div>
    </div>
  );
}
