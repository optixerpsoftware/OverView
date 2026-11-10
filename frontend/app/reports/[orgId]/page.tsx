'use client';
import { use, useEffect, useMemo, useState } from 'react';
import Breadcrumb from '../../../components/Breadcrumb';
import { AlertTrendChart, HBars } from '../../../components/Charts';
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
const SEV_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

// Dates en français, quel que soit le réglage du navigateur.
const fmtDate = (iso: string) => new Date(iso).toLocaleDateString('fr-CA', { day: 'numeric', month: 'long', year: 'numeric' });
const fmtDateTime = (iso: string) => new Date(iso).toLocaleString('fr-CA', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });

/** « gparent_innovetre.com#EXT#@tenant.onmicrosoft.com » → « gparent@innovetre.com (invité) ». */
function readable(text: string): string {
  return text.replace(/([A-Za-z0-9._%+-]+)_([A-Za-z0-9.-]+\.[A-Za-z]{2,})#EXT#@[A-Za-z0-9.-]+/g, '$1@$2 (invité)');
}

/** Une alerte d'appli : « Éditeur … — scope, scope… » → éditeur à part, permissions en petit. */
function splitDetail(detail: string): { lead: string; scopes: string } {
  const i = detail.indexOf(' — ');
  return i > 0 && /[A-Z][a-z]+\.[A-Z]/.test(detail.slice(i)) ? { lead: detail.slice(0, i), scopes: detail.slice(i + 3) } : { lead: readable(detail), scopes: '' };
}

export default function ReportPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  const [days, setDays] = useState(30);
  const r = useApi<Report>(`/insights/organizations/${orgId}/report/?days=${days}`);
  const d = r.data;
  useEffect(() => { if (d) document.title = `Rapport de sécurité — ${d.organization.name}`; }, [d]);

  // Synthèse de l'organisation (tous tenants confondus) pour la première page.
  const summary = useMemo(() => {
    if (!d) return null;
    const ts = d.tenants;
    const scores = ts.map((t) => t.score).filter((s): s is number => s !== null);
    const sum = (f: (t: ReportTenant) => number) => ts.reduce((n, t) => n + f(t), 0);
    const priorities = ts
      .flatMap((t) => t.findings.filter((f) => SEV_RANK[f.severity] <= 1).map((f) => ({ ...f, tenant: t.name })))
      .sort((a, b) => SEV_RANK[a.severity] - SEV_RANK[b.severity]);
    const extra: { title: string; recommendation: string; tenant: string; severity: string }[] = [];
    ts.forEach((t) => {
      if (t.hygiene.apps_high) extra.push({ severity: 'high', tenant: t.name, title: `${t.hygiene.apps_high} application(s) tierce(s) à haut risque`,
        recommendation: 'Vérifier chaque application et bloquer celles qui ne sont plus utilisées (page Hygiène des identités).' });
      if (t.sharing.external + t.sharing.anonymous) extra.push({ severity: 'medium', tenant: t.name, title: `${t.sharing.external + t.sharing.anonymous} partage(s) vers l’extérieur`,
        recommendation: 'Revoir les partages externes et retirer ceux qui ne sont plus nécessaires (page Partage).' });
      if (t.hygiene.inactive) extra.push({ severity: 'medium', tenant: t.name, title: `${t.hygiene.inactive} compte(s) inactif(s) depuis 90 jours`,
        recommendation: 'Désactiver les comptes inutilisés (page Hygiène des identités).' });
    });
    return {
      score: scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null,
      graves: sum((t) => t.alerts.open_by_severity.critical + t.alerts.open_by_severity.high),
      opened: sum((t) => t.alerts.opened), resolved: sum((t) => t.alerts.resolved),
      external: sum((t) => t.sharing.external + t.sharing.anonymous),
      apps: sum((t) => t.hygiene.apps_high ?? 0), actions: sum((t) => t.actions.length),
      priorities: [...priorities, ...extra].slice(0, 5),
    };
  }, [d]);

  return (
    <div className="report">
      <div className="no-print">
        <Breadcrumb current="Rapport" />
        <div className="filters">
          <div className="chip-group" role="group" aria-label="Période">
            {[7, 30, 90].map((n) => <button key={n} className={`chip-btn ${days === n ? 'on' : ''}`} onClick={() => setDays(n)}>{n} jours</button>)}
          </div>
          <button className="primary" onClick={() => window.print()} disabled={!d}>Imprimer / Exporter en PDF</button>
          <span className="sub">Astuce : dans la fenêtre d’impression, décoche « En-têtes et pieds de page » pour un PDF plus propre.</span>
        </div>
      </div>
      {r.error && <div className="messagebar error">{r.error}</div>}
      {r.loading && <Loading lines={8} />}

      {d && summary && (
        <>
          <header className="report-head">
            <div>
              <div className="report-brand">OVERVIEW · RAPPORT DE SÉCURITÉ MICROSOFT 365</div>
              <h1>{d.organization.name}</h1>
              {d.organization.description && <div className="sub">{d.organization.description}</div>}
            </div>
            <div className="report-meta">
              Période : {fmtDate(d.since)} au {fmtDate(d.generated_at)} ({d.period_days} jours)<br />
              {d.tenants.length} tenant(s) · Généré le {fmtDateTime(d.generated_at)}
            </div>
          </header>

          <div className="report-section-title">Synthèse</div>
          <section className="report-summary">
            <div className="report-grade">
              <ScoreRing score={summary.score} size={110} />
              <span className="sub">Score de gouvernance{d.tenants.length > 1 ? ' moyen' : ''}</span>
            </div>
            <div>
              <strong>Actions prioritaires</strong>
              {summary.priorities.length === 0 ? <p className="sub">Aucun point grave à traiter sur la période.</p> : (
                <ol className="report-priorities" style={{ marginTop: 8 }}>
                  {summary.priorities.map((p, i) => (
                    <li key={i}>
                      <span className="n">{i + 1}</span>
                      <span><strong>{p.title}</strong>{d.tenants.length > 1 && <span className="sub"> · {p.tenant}</span>}
                        {p.recommendation && <div className="sub">{p.recommendation}</div>}</span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </section>
          <div className="tiles">
            <div className={`tile static ${summary.graves ? 'warn' : ''}`}><span className="tile-label">Alertes graves ouvertes</span>
              <span className="tile-value">{summary.graves}</span><span className="tile-hint">critiques et élevées</span></div>
            <div className="tile static"><span className="tile-label">Alertes sur la période</span>
              <span className="tile-value">{summary.opened}</span><span className="tile-hint">{summary.resolved} résolue(s)</span></div>
            <div className={`tile static ${summary.external ? 'warn' : ''}`}><span className="tile-label">Partages vers l’extérieur</span>
              <span className="tile-value">{summary.external}</span><span className="tile-hint">liens anonymes et externes</span></div>
            <div className={`tile static ${summary.apps ? 'warn' : ''}`}><span className="tile-label">Applis tierces à risque</span>
              <span className="tile-value">{summary.apps}</span><span className="tile-hint">{summary.actions} remédiation(s) faite(s)</span></div>
          </div>

          {d.tenants.length === 0 && <div className="empty">Aucun tenant connecté pour cette organisation.</div>}
          {d.tenants.map((t) => (
            <section className="report-tenant" key={t.connector_id}>
              <h2>Tenant : {t.name}</h2>
              <div className="report-tenant-top">
                <div className="report-grade">
                  <ScoreRing score={t.score} size={96} />
                  <span className="sub">
                    {t.score_delta === null ? 'Score de gouvernance' : t.score_delta === 0 ? 'Stable sur la période'
                      : `${t.score_delta > 0 ? '+' : ''}${t.score_delta.toLocaleString('fr-CA')} pts sur la période`}
                  </span>
                </div>
                <ul className="report-highlights">{t.highlights.map((h, i) => <li key={i}>{h}</li>)}</ul>
              </div>

              <div className="tiles">
                <div className="tile static"><span className="tile-label">Alertes nouvelles</span><span className="tile-value">{t.alerts.opened}</span>
                  <span className="tile-hint">{t.alerts.resolved} résolue(s){t.alerts.mttr_hours !== null ? ` · ${t.alerts.mttr_hours} h en moyenne` : ''}</span></div>
                <div className={`tile static ${t.alerts.open_by_severity.critical + t.alerts.open_by_severity.high ? 'warn' : ''}`}>
                  <span className="tile-label">Graves ouvertes</span><span className="tile-value">{t.alerts.open_by_severity.critical + t.alerts.open_by_severity.high}</span>
                  <span className="tile-hint">{t.alerts.open_by_severity.critical} critique(s) · {t.alerts.open_by_severity.high} élevée(s)</span></div>
                <div className={`tile static ${t.sharing.external + t.sharing.anonymous ? 'warn' : ''}`}>
                  <span className="tile-label">Partages externes</span><span className="tile-value">{t.sharing.external + t.sharing.anonymous}</span>
                  <span className="tile-hint">{t.sharing.anonymous} anonyme(s) · {t.sharing.anonymous_edit} modifiable(s)</span></div>
                <div className={`tile static ${t.hygiene.apps_high ? 'warn' : ''}`}>
                  <span className="tile-label">Applis à risque</span><span className="tile-value">{t.hygiene.apps_high ?? '—'}</span>
                  <span className="tile-hint">{t.hygiene.inactive ?? '—'} inactif(s) · {t.hygiene.admins_no_mfa ?? 0} admin(s) sans MFA</span></div>
              </div>

              <div className="card">
                <h3 style={{ marginTop: 0 }}>Évolution des alertes</h3>
                <AlertTrendChart data={t.trend} height={140} />
                {Object.keys(t.alerts.by_category).length > 0 && (
                  <>
                    <h3>Alertes ouvertes par catégorie</h3>
                    <HBars rows={Object.entries(t.alerts.by_category).map(([k, v]) => ({ label: CATEGORY[k as AlertCategory]?.label ?? k, value: v }))} />
                  </>
                )}
              </div>

              {t.findings.length > 0 && (
                <>
                  <h3>Recommandations</h3>
                  <table className="grid">
                    <thead><tr><th style={{ width: 90 }}>Priorité</th><th>Constat</th><th>Que faire</th></tr></thead>
                    <tbody>
                      {t.findings.map((f, i) => (
                        <tr key={i}>
                          <td><span className={`badge ${FINDING_SEV[f.severity] ?? 'off'}`}>{SEVERITY[f.severity as AlertSeverity]?.label ?? 'Info'}</span></td>
                          <td><strong>{f.title}</strong><div className="sub">{f.summary}</div></td>
                          <td>{f.recommendation || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}

              {t.alerts.top.length > 0 && (
                <>
                  <h3>Alertes ouvertes prioritaires</h3>
                  <table className="grid">
                    <thead><tr><th style={{ width: 90 }}>Gravité</th><th>Alerte</th></tr></thead>
                    <tbody>
                      {t.alerts.top.map((a) => {
                        const det = splitDetail(a.detail);
                        return (
                          <tr key={a.id}>
                            <td><span className={`badge ${SEVERITY[a.severity].cls}`}>{SEVERITY[a.severity].label}</span></td>
                            <td><strong>{readable(a.title)}</strong><div className="sub">{det.lead}</div>
                              {det.scopes && <div className="scopes">Permissions : {det.scopes}</div>}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </>
              )}

              <h3>Remédiations effectuées</h3>
              {t.actions.length === 0 ? <div className="sub">Aucune action sur la période.</div> : (
                <table className="grid">
                  <thead><tr><th style={{ width: 120 }}>Date</th><th style={{ width: 140 }}>Action</th><th>Détail</th><th style={{ width: 90 }}>Par</th></tr></thead>
                  <tbody>
                    {t.actions.map((a, i) => (
                      <tr key={i}><td>{fmtDate(a.at)}</td><td>{a.kind}</td><td>{readable(a.detail)}</td><td>{a.by}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          ))}
          <div className="report-foot">
            Généré par OverView à partir des dernières synchronisations Microsoft 365. Gravités : {SEVERITY_ORDER.map((s) => SEVERITY[s].label).join(' > ')}.
            Les données partage et identités proviennent des analyses quotidiennes ; les alertes sont réévaluées toutes les 10 minutes.
          </div>
        </>
      )}
    </div>
  );
}
