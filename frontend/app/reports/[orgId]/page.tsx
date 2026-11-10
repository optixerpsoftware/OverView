'use client';
import { use, useEffect, useState } from 'react';
import Breadcrumb from '../../../components/Breadcrumb';
import { AlertTrendChart, HBars, SEV_COLORS, Spark } from '../../../components/Charts';
import Loading from '../../../components/Loading';
import ScoreRing from '../../../components/ScoreRing';
import { CATEGORY, SEVERITY, SEVERITY_ORDER } from '../../../lib/alerts';
import type { AlertCategory, AlertSeverity } from '../../../lib/types';
import { useApi } from '../../../lib/useApi';

interface ReportTenant {
  connector_id: string; name: string; score: number | null; score_trend: number[]; score_delta: number | null;
  alerts: { opened: number; resolved: number; open_by_severity: Record<AlertSeverity, number>; by_category: Record<string, number>;
    mttr_hours: number | null; top: { id: string; severity: AlertSeverity; category: AlertCategory; title: string; detail: string }[] };
  trend: { day: string; critical: number; high: number; medium: number; low: number; resolved: number; active: number }[];
  sharing: { external: number; anonymous: number; anonymous_edit: number };
  hygiene: { inactive?: number | null; guests?: number; guests_flagged?: number; admins?: number; admins_no_mfa?: number; apps?: number; apps_high?: number };
  findings: { title: string; severity: string; summary: string; recommendation: string }[];
  actions: { at: string; kind: string; detail: string; by: string }[];
  highlights: string[];
}
interface Report { organization: { id: string; name: string; description: string | null }; period_days: number; since: string; generated_at: string; tenants: ReportTenant[] }

const FINDING_SEV: Record<string, string> = { critical: 'err', high: 'err', medium: 'warn', low: 'off', info: 'off' };

export default function ReportPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  const [days, setDays] = useState(30);
  const r = useApi<Report>(`/insights/organizations/${orgId}/report/?days=${days}`);
  const d = r.data;
  useEffect(() => { if (d) document.title = `Rapport sécurité — ${d.organization.name}`; }, [d]);

  return (
    <div className="report">
      <div className="no-print">
        <Breadcrumb current="Rapport" />
        <div className="filters">
          <div className="chip-group" role="group" aria-label="Période">
            {[7, 30, 90].map((n) => <button key={n} className={`chip-btn ${days === n ? 'on' : ''}`} onClick={() => setDays(n)}>{n} jours</button>)}
          </div>
          <button className="primary" onClick={() => window.print()} disabled={!d}>Imprimer / Exporter en PDF</button>
        </div>
      </div>
      {r.error && <div className="messagebar error">{r.error}</div>}
      {r.loading && <Loading lines={8} />}

      {d && (
        <>
          <header className="report-head">
            <div>
              <div className="sub">Rapport de sécurité Microsoft 365</div>
              <h1>{d.organization.name}</h1>
              {d.organization.description && <div className="sub">{d.organization.description}</div>}
            </div>
            <div className="sub" style={{ textAlign: 'right' }}>
              Période : {new Date(d.since).toLocaleDateString()} → {new Date(d.generated_at).toLocaleDateString()} ({d.period_days} jours)<br />
              Généré le {new Date(d.generated_at).toLocaleString()}
            </div>
          </header>

          {d.tenants.length === 0 && <div className="empty">Aucun tenant connecté pour cette organisation.</div>}
          {d.tenants.map((t) => (
            <section className="report-tenant" key={t.connector_id}>
              <h2>Tenant : {t.name}</h2>
              <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
                <div style={{ display: 'grid', justifyItems: 'center' }}>
                  <ScoreRing score={t.score} size={96} />
                  <span className="sub">Score de gouvernance</span>
                  <Spark values={t.score_trend} width={96} height={20} />
                </div>
                <ul className="report-highlights" style={{ flex: 1, minWidth: 260 }}>
                  {t.highlights.map((h, i) => <li key={i}>{h}</li>)}
                </ul>
              </div>

              <div className="tiles">
                <div className="tile static"><span className="tile-label">Alertes nouvelles</span><span className="tile-value">{t.alerts.opened}</span><span className="tile-hint">{t.alerts.resolved} résolue(s)</span></div>
                <div className={`tile static ${t.alerts.open_by_severity.critical + t.alerts.open_by_severity.high ? 'warn' : ''}`}>
                  <span className="tile-label">Alertes ouvertes graves</span><span className="tile-value">{t.alerts.open_by_severity.critical + t.alerts.open_by_severity.high}</span>
                  <span className="tile-hint">{t.alerts.open_by_severity.critical} critique(s) · {t.alerts.open_by_severity.high} élevée(s)</span>
                </div>
                <div className={`tile static ${t.sharing.external + t.sharing.anonymous ? 'warn' : ''}`}>
                  <span className="tile-label">Partages vers l’extérieur</span><span className="tile-value">{t.sharing.external + t.sharing.anonymous}</span>
                  <span className="tile-hint">{t.sharing.anonymous} anonyme(s) · {t.sharing.anonymous_edit} modifiable(s)</span>
                </div>
                <div className={`tile static ${t.hygiene.apps_high ? 'warn' : ''}`}>
                  <span className="tile-label">Applis tierces à risque</span><span className="tile-value">{t.hygiene.apps_high ?? '—'}</span>
                  <span className="tile-hint">{t.hygiene.inactive ?? '—'} compte(s) inactif(s) · {t.hygiene.admins_no_mfa ?? 0} admin(s) sans MFA</span>
                </div>
              </div>

              <div className="card">
                <h3 style={{ marginTop: 0 }}>Évolution des alertes</h3>
                <AlertTrendChart data={t.trend} height={150} />
                {Object.keys(t.alerts.by_category).length > 0 && (
                  <>
                    <h3>Alertes ouvertes par catégorie</h3>
                    <HBars rows={Object.entries(t.alerts.by_category).map(([k, v]) => ({ label: CATEGORY[k as AlertCategory]?.label ?? k, value: v }))} />
                  </>
                )}
              </div>

              {t.alerts.top.length > 0 && (
                <>
                  <h3>Alertes ouvertes prioritaires</h3>
                  <table className="grid">
                    <thead><tr><th>Gravité</th><th>Alerte</th></tr></thead>
                    <tbody>
                      {t.alerts.top.map((a) => (
                        <tr key={a.id}>
                          <td><span className="badge" style={{ background: 'var(--hover)', color: SEV_COLORS[a.severity] }}>{SEVERITY[a.severity].label}</span></td>
                          <td><strong>{a.title}</strong><div className="sub">{a.detail}</div></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}

              {t.findings.length > 0 && (
                <>
                  <h3>Recommandations (gouvernance)</h3>
                  <table className="grid">
                    <thead><tr><th>Priorité</th><th>Constat</th><th>Recommandation</th></tr></thead>
                    <tbody>
                      {t.findings.map((f, i) => (
                        <tr key={i}>
                          <td><span className={`badge ${FINDING_SEV[f.severity] ?? 'off'}`}>{SEVERITY[f.severity as AlertSeverity]?.label ?? f.severity}</span></td>
                          <td><strong>{f.title}</strong><div className="sub">{f.summary}</div></td>
                          <td>{f.recommendation || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}

              <h3>Remédiations effectuées</h3>
              {t.actions.length === 0 ? <div className="sub">Aucune action sur la période.</div> : (
                <table className="grid">
                  <thead><tr><th>Date</th><th>Action</th><th>Détail</th><th>Par</th></tr></thead>
                  <tbody>
                    {t.actions.map((a, i) => (
                      <tr key={i}><td>{new Date(a.at).toLocaleDateString()}</td><td>{a.kind}</td><td>{a.detail}</td><td>{a.by}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          ))}
          <div className="report-foot">
            Généré par OverView à partir des dernières synchronisations Microsoft 365. Gravités : {SEVERITY_ORDER.map((s) => SEVERITY[s].label).join(' > ')}.
          </div>
        </>
      )}
    </div>
  );
}
