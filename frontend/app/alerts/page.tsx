'use client';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import Loading from '../../components/Loading';
import { api } from '../../lib/api';
import { ago, CATEGORY, SEVERITY, SEVERITY_ORDER } from '../../lib/alerts';
import type {
  AlertCategory, AlertSeverity as Severity, Connector, InsightAlert, NotificationSettings, Organization,
} from '../../lib/types';
import { useApi, useApiMutation } from '../../lib/useApi';
import { useMe } from '../../lib/useMe';

type StatusFilter = 'active' | 'open' | 'acknowledged' | 'resolved';
const STATUS_LABEL: Record<StatusFilter, string> = {
  active: 'À traiter', open: 'Nouvelles', acknowledged: 'Prises en compte', resolved: 'Résolues',
};

export default function AlertsPage() {
  const [tab, setTab] = useState<'alerts' | 'notify'>('alerts');
  const { isClient } = useMe();
  useEffect(() => { document.title = 'Alertes — OverView'; }, []);
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>Centre d’alertes</h1>
          <div className="sub">Réévaluées toutes les 10 minutes, après les synchronisations</div>
        </div>
      </div>
      <nav className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'alerts'} className={`tab ${tab === 'alerts' ? 'on' : ''}`} onClick={() => setTab('alerts')}>Alertes</button>
        {!isClient && <button role="tab" aria-selected={tab === 'notify'} className={`tab ${tab === 'notify' ? 'on' : ''}`} onClick={() => setTab('notify')}>Notifications</button>}
      </nav>
      <section className="panel">{tab === 'alerts' ? <AlertList /> : <Notifications />}</section>
    </>
  );
}

function AlertList() {
  const [status, setStatus] = useState<StatusFilter>('active');
  const [severity, setSeverity] = useState<Severity | ''>('');
  const [category, setCategory] = useState<AlertCategory | ''>('');
  const [connector, setConnector] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const { canAct } = useMe();

  // Filtres passés dans l'URL depuis le tableau de bord (?severity=critical, ?category=sharing, ?connector=…).
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    setSeverity((p.get('severity') as Severity) || '');
    setCategory((p.get('category') as AlertCategory) || '');
    setConnector(p.get('connector') || '');
  }, []);

  const qs = new URLSearchParams({ status });
  if (severity) qs.set('severity', severity);
  if (category) qs.set('category', category);
  if (connector) qs.set('connector_id', connector);
  const list = useApi<InsightAlert[]>(`/insights/alerts/?${qs}`);
  const connectors = useApi<Connector[]>('/connectors/');
  const rows = useMemo(() => list.data ?? [], [list.data]);

  const setStatusOf = useApiMutation(
    (v: { ids: string[]; status: 'acknowledged' | 'open' }) => api<{ updated: number }>('/insights/alerts/status/', { method: 'POST', body: v }),
    { invalidate: ['/insights/'], success: (r) => `${r.updated} alerte(s) mise(s) à jour.`, onSuccess: () => setSelected(new Set()) },
  );
  const evaluate = useApiMutation(
    (connectorId: string) => api<{ created: number; resolved: number }>('/insights/alerts/evaluate/', { method: 'POST', body: { connector_id: connectorId } }),
    { invalidate: ['/insights/'], success: (r) => `Réévaluation : ${r.created} nouvelle(s), ${r.resolved} résolue(s).` },
  );

  const allChecked = rows.length > 0 && rows.every((a) => selected.has(a.id));
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  return (
    <>
      <div className="filters">
        <div className="chip-group" role="group" aria-label="Statut">
          {(Object.keys(STATUS_LABEL) as StatusFilter[]).map((k) => (
            <button key={k} className={`chip-btn ${status === k ? 'on' : ''}`} onClick={() => { setStatus(k); setSelected(new Set()); }}>{STATUS_LABEL[k]}</button>
          ))}
        </div>
        <select value={severity} onChange={(e) => setSeverity(e.target.value as Severity | '')} aria-label="Gravité">
          <option value="">Toutes gravités</option>
          {SEVERITY_ORDER.map((s) => <option key={s} value={s}>{SEVERITY[s].label}</option>)}
        </select>
        <select value={category} onChange={(e) => setCategory(e.target.value as AlertCategory | '')} aria-label="Catégorie">
          <option value="">Toutes catégories</option>
          {(Object.keys(CATEGORY) as AlertCategory[]).map((c) => <option key={c} value={c}>{CATEGORY[c].label}</option>)}
        </select>
        <select value={connector} onChange={(e) => setConnector(e.target.value)} aria-label="Tenant">
          <option value="">Tous les tenants</option>
          {(connectors.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {connector && canAct && (
          <button disabled={evaluate.isPending} onClick={() => evaluate.mutate(connector)}>
            {evaluate.isPending ? 'Réévaluation…' : 'Réévaluer maintenant'}
          </button>
        )}
      </div>

      {selected.size > 0 && (
        <div className="bulkbar" role="region" aria-label="Actions sur la sélection">
          <strong>{selected.size} sélectionnée(s)</strong>
          <button className="primary" disabled={setStatusOf.isPending}
            onClick={() => setStatusOf.mutate({ ids: [...selected], status: 'acknowledged' })}>Prendre en compte</button>
          <button disabled={setStatusOf.isPending} onClick={() => setStatusOf.mutate({ ids: [...selected], status: 'open' })}>Rouvrir</button>
          <button className="ghost" onClick={() => setSelected(new Set())}>Annuler la sélection</button>
        </div>
      )}

      {list.error && <div className="messagebar error">{list.error}</div>}
      {list.loading && <Loading lines={6} />}
      {list.data && rows.length === 0 && (
        <div className="empty-state"><div className="big" aria-hidden="true">✅</div>Aucune alerte pour ces filtres.</div>
      )}
      {rows.length > 0 && (
        <table className="grid">
          <thead>
            <tr>
              <th style={{ width: 32 }}>
                {status !== 'resolved' && canAct && (
                  <input type="checkbox" aria-label="Tout sélectionner" checked={allChecked}
                    onChange={() => setSelected(allChecked ? new Set() : new Set(rows.map((a) => a.id)))} />
                )}
              </th>
              <th>Gravité</th><th>Alerte</th><th>Tenant</th><th>Détectée</th><th>État</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id}>
                <td>{a.status !== 'resolved' && canAct && <input type="checkbox" aria-label={`Sélectionner ${a.title}`} checked={selected.has(a.id)} onChange={() => toggle(a.id)} />}</td>
                <td><span className={`badge ${SEVERITY[a.severity].cls}`}>{SEVERITY[a.severity].label}</span></td>
                <td style={{ maxWidth: 520 }}>
                  <strong>{CATEGORY[a.category]?.icon} {a.title}</strong>
                  <div className="sub" style={{ wordBreak: 'break-word' }}>{a.detail}</div>
                </td>
                <td>{a.connector_name}</td>
                <td title={new Date(a.first_seen).toLocaleString()}>{ago(a.first_seen)}</td>
                <td>
                  {a.status === 'open' && <span className="badge err">Nouvelle</span>}
                  {a.status === 'acknowledged' && <span className="badge off" title={`par ${a.acknowledged_by}`}>Prise en compte</span>}
                  {a.status === 'resolved' && <span className="badge ok" title={a.resolved_at ? new Date(a.resolved_at).toLocaleString() : ''}>Résolue</span>}
                </td>
                <td align="right">{a.link && canAct && <Link href={a.link} className="btn">Ouvrir</Link>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function Notifications() {
  const orgs = useApi<Organization[]>('/organizations/');
  const [orgId, setOrgId] = useState('');
  useEffect(() => { if (!orgId && orgs.data?.length) setOrgId(orgs.data[0].id); }, [orgs.data, orgId]);
  const path = `/insights/organizations/${orgId}/notifications/`;
  const setting = useApi<NotificationSettings>(path, { enabled: !!orgId });
  const [emails, setEmails] = useState('');
  const [minSev, setMinSev] = useState<Severity>('high');
  const [enabled, setEnabled] = useState(true);
  const [webhook, setWebhook] = useState('');

  useEffect(() => {
    if (!setting.data) return;
    setEmails(setting.data.emails.join(', '));
    setMinSev(setting.data.min_severity);
    setEnabled(setting.data.enabled);
    setWebhook('');
  }, [setting.data]);

  const save = useApiMutation(
    (body: Record<string, unknown>) => api<NotificationSettings>(path, { method: 'PUT', body }),
    { invalidate: [path], success: 'Notifications enregistrées.' },
  );

  function submit(removeWebhook = false) {
    save.mutate({
      emails: emails.split(/[,;\s]+/).filter(Boolean), min_severity: minSev, enabled,
      ...(removeWebhook ? { teams_webhook: '' } : webhook ? { teams_webhook: webhook } : {}),
    });
  }

  if (orgs.data && orgs.data.length === 0) return <div className="empty">Crée d’abord une organisation.</div>;
  return (
    <div className="settings">
      <div className="setting">
        <div><strong>Organisation</strong><div className="sub">Chaque organisation a ses propres destinataires.</div></div>
        <select value={orgId} onChange={(e) => setOrgId(e.target.value)}>
          {(orgs.data ?? []).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
      </div>
      {setting.loading && <Loading />}
      {setting.data && (
        <>
          <div className="setting">
            <div><strong>Notifications actives</strong><div className="sub">Une seule notification par nouvelle alerte (pas de répétition).</div></div>
            <label className="switch"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> {enabled ? 'Activées' : 'Désactivées'}</label>
          </div>
          <div className="setting">
            <div><strong>Gravité minimale</strong><div className="sub">Les alertes moins graves restent visibles ici, sans notification.</div></div>
            <select value={minSev} onChange={(e) => setMinSev(e.target.value as Severity)}>
              {SEVERITY_ORDER.map((s) => <option key={s} value={s}>{SEVERITY[s].label} et plus</option>)}
            </select>
          </div>
          <div className="setting">
            <div><strong>Courriels</strong><div className="sub">Séparés par des virgules. Envoyés par le serveur de messagerie configuré côté backend.</div></div>
            <input value={emails} onChange={(e) => setEmails(e.target.value)} placeholder="soc@exemple.com, ti@exemple.com" />
          </div>
          <div className="setting">
            <div>
              <strong>Microsoft Teams</strong>
              <div className="sub">
                Webhook d’un canal (Workflows Teams : « Publier dans un canal lorsqu’une demande de webhook est reçue »).
                {setting.data.teams_configured ? ' Un webhook est configuré (masqué).' : ' Aucun webhook configuré.'}
              </div>
            </div>
            <div style={{ display: 'grid', gap: 6 }}>
              <input type="url" value={webhook} onChange={(e) => setWebhook(e.target.value)}
                placeholder={setting.data.teams_configured ? 'Laisser vide pour conserver le webhook actuel' : 'https://…'} />
              {setting.data.teams_configured && <button className="ghost danger" onClick={() => submit(true)}>Retirer le webhook</button>}
            </div>
          </div>
          <div style={{ padding: '12px 0' }}>
            <button className="primary" disabled={save.isPending} onClick={() => submit()}>{save.isPending ? 'Enregistrement…' : 'Enregistrer'}</button>
          </div>
        </>
      )}
    </div>
  );
}
