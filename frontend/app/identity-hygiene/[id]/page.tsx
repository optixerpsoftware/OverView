'use client';
import { use, useState } from 'react';
import Breadcrumb from '../../../components/Breadcrumb';
import ConfirmDialog from '../../../components/ConfirmDialog';
import Loading from '../../../components/Loading';
import { api } from '../../../lib/api';
import { ago } from '../../../lib/alerts';
import { useApi, useApiMutation } from '../../../lib/useApi';

interface Account { id: string; upn: string; name: string; type: string; created: string | null; last_activity: string | null; licensed: boolean; reason?: string; state?: string; flags?: string[] }
interface Admin { upn: string; name: string; roles: string[]; mfa: boolean | null; flags: string[] }
interface App {
  id: string; name: string; publisher: string; verified: boolean; enabled: boolean; created: string | null;
  risk: 'high' | 'medium' | 'low' | 'info'; high_risk_scopes: string[]; unverified_warning?: boolean;
  delegated: { resource: string; scopes: string[]; who: string; all_users: boolean }[];
  application: { resource: string; permission: string }[];
}
interface Snapshot {
  scanned_at: string; activity_source: string; errors: string[];
  inactive: Account[] | null; guests: Account[];
  admins: { people: Admin[]; global_admins: number; notes: { level: string; text: string }[] };
  apps: App[];
  summary: { inactive: number | null; guests: number; guests_flagged: number; admins: number; global_admins: number; admins_no_mfa: number; apps: number; apps_high: number };
}
interface Payload { snapshot: Snapshot | null; history: { id: string; changes: string[]; ok: boolean; detail: string; performed_by: string; create_at: string }[] }
type Tab = 'inactive' | 'guests' | 'admins' | 'apps';
const RISK = { high: { label: 'Élevé', cls: 'err' }, medium: { label: 'Moyen', cls: 'warn' }, low: { label: 'Faible', cls: 'off' }, info: { label: 'Info', cls: 'off' } };

export default function IdentityHygienePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const base = `/identity/connectors/${id}/hygiene/`;
  const q = useApi<Payload>(base);
  const [tab, setTab] = useState<Tab>('apps');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmAccounts, setConfirmAccounts] = useState(false);
  const [appTarget, setAppTarget] = useState<App | null>(null);

  const scan = useApiMutation(() => api(base, { method: 'POST' }), { invalidate: [base, '/insights/'], success: 'Analyse terminée.' });
  const disable = useApiMutation(
    (ids: string[]) => api<{ succeeded: number; failed: number; results: { upn?: string; ok: boolean; error?: string }[] }>(
      `${base}accounts/`, { method: 'POST', body: { user_ids: ids, enabled: false, confirm: 'DESACTIVER' } }),
    {
      invalidate: [base],
      success: (r) => `${r.succeeded} compte(s) désactivé(s)${r.failed ? ` · ${r.failed} refusé(s) : ${r.results.filter((x) => !x.ok).map((x) => `${x.upn ?? ''} ${x.error}`).slice(0, 2).join(' ; ')}` : ''}`,
      onSuccess: () => { setSelected(new Set()); setConfirmAccounts(false); },
    },
  );
  const toggleApp = useApiMutation(
    (v: { app: App; enabled: boolean }) => api(`${base}apps/${v.app.id}/`, { method: 'POST', body: { enabled: v.enabled, confirm: v.enabled ? 'REACTIVER' : 'DESACTIVER' } }),
    { invalidate: [base, '/insights/'], success: (_, v) => `« ${v.app.name} » ${v.enabled ? 'réactivée' : 'bloquée'}.`, onSuccess: () => setAppTarget(null) },
  );

  const s = q.data?.snapshot;
  const toggle = (uid: string) => setSelected((p) => { const n = new Set(p); if (n.has(uid)) n.delete(uid); else n.add(uid); return n; });

  function accountTable(rows: Account[], guest: boolean) {
    if (rows.length === 0) return <div className="empty-state"><div className="big" aria-hidden="true">✅</div>Rien à signaler.</div>;
    return (
      <table className="grid">
        <thead><tr><th style={{ width: 32 }} /><th>Compte</th><th>{guest ? 'État' : 'Motif'}</th><th>Dernière activité</th><th>Créé</th><th>Licence</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td><input type="checkbox" aria-label={`Sélectionner ${r.upn}`} checked={selected.has(r.id)} onChange={() => toggle(r.id)} /></td>
              <td><strong>{r.name}</strong><div className="sub">{r.upn.replace(/#EXT#@.*/, ' (invité)')}</div></td>
              <td>{guest ? <>{r.state === 'PendingAcceptance' ? 'Invitation en attente' : r.state === 'Accepted' ? 'Acceptée' : r.state || '—'}{(r.flags ?? []).map((f) => <div key={f}><span className="badge warn">{f}</span></div>)}</> : <span className="badge warn">{r.reason}</span>}</td>
              <td>{r.last_activity ? new Date(r.last_activity).toLocaleDateString() : 'jamais'}</td>
              <td>{r.created ? new Date(r.created).toLocaleDateString() : '—'}</td>
              <td>{r.licensed ? <span className="badge warn" title="Licence payée pour un compte inutilisé">Oui</span> : 'Non'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  return (
    <div className="wide">
      <Breadcrumb current="Hygiène des identités" />
      <header className="hero">
        <div className="avatar-lg" aria-hidden="true">🧹</div>
        <div className="hero-id">
          <h1>Hygiène des identités</h1>
          <div className="sub">
            {s ? `Analyse ${ago(s.scanned_at)} · activité d’après : ${s.activity_source} · automatique chaque jour` : 'Comptes oubliés, invités, administrateurs et applications tierces'}
          </div>
        </div>
        <div className="hero-actions">
          <button className="primary" disabled={scan.isPending} onClick={() => scan.mutate()}>{scan.isPending ? 'Analyse en cours…' : s ? 'Relancer l’analyse' : 'Lancer l’analyse'}</button>
        </div>
      </header>
      {q.error && <div className="messagebar error">{q.error}</div>}
      {q.loading && <Loading lines={5} />}
      {scan.isPending && <Loading label="Lecture des comptes, rôles et applications dans Microsoft 365 (15 à 60 s)…" lines={3} />}
      {q.data && !s && !scan.isPending && <div className="empty-state card"><div className="big">🧹</div>Aucune analyse pour ce tenant. Lance la première.</div>}
      {s && s.errors.length > 0 && <div className="messagebar error">Analyse partielle : {s.errors.join(' · ')}</div>}

      {s && (
        <>
          <div className="tiles">
            <button className={`tile ${tab === 'apps' ? 'on' : ''} ${s.summary.apps_high ? 'warn' : ''}`} onClick={() => setTab('apps')}>
              <span className="tile-label">Applis tierces à haut risque</span><span className="tile-value">{s.summary.apps_high}</span>
              <span className="tile-hint">sur {s.summary.apps} appli(s) autorisée(s)</span>
            </button>
            <button className={`tile ${tab === 'admins' ? 'on' : ''} ${s.summary.admins_no_mfa ? 'warn' : ''}`} onClick={() => setTab('admins')}>
              <span className="tile-label">Administrateurs</span><span className="tile-value">{s.summary.admins}</span>
              <span className="tile-hint">{s.summary.global_admins} général(aux) · {s.summary.admins_no_mfa} sans MFA</span>
            </button>
            <button className={`tile ${tab === 'inactive' ? 'on' : ''} ${s.summary.inactive ? 'warn' : ''}`} onClick={() => { setTab('inactive'); setSelected(new Set()); }}>
              <span className="tile-label">Comptes inactifs (90 j)</span><span className="tile-value">{s.summary.inactive ?? '—'}</span>
              <span className="tile-hint">{s.summary.inactive === null ? 'activité indisponible' : 'actifs mais inutilisés'}</span>
            </button>
            <button className={`tile ${tab === 'guests' ? 'on' : ''} ${s.summary.guests_flagged ? 'warn' : ''}`} onClick={() => { setTab('guests'); setSelected(new Set()); }}>
              <span className="tile-label">Invités</span><span className="tile-value">{s.summary.guests}</span>
              <span className="tile-hint">{s.summary.guests_flagged} à revoir</span>
            </button>
          </div>

          {selected.size > 0 && (
            <div className="bulkbar">
              <strong>{selected.size} compte(s) sélectionné(s)</strong>
              <button className="danger-solid" onClick={() => setConfirmAccounts(true)}>Désactiver la connexion</button>
              <button className="ghost" onClick={() => setSelected(new Set())}>Annuler</button>
            </div>
          )}

          <section className="card">
            {tab === 'apps' && (
              <>
                <h2>Applications tierces autorisées</h2>
                <div className="sub" style={{ marginBottom: 8 }}>
                  Applications d’autres éditeurs auxquelles des utilisateurs ou un administrateur ont donné accès. Une permission
                  « d’application » agit sans utilisateur, sur toute l’organisation.
                </div>
                {s.apps.length === 0 && <div className="empty-state"><div className="big">✅</div>Aucune application tierce.</div>}
                {s.apps.map((a) => (
                  <details className="finding" key={a.id}>
                    <summary>
                      <span className={`badge ${RISK[a.risk].cls}`}>{RISK[a.risk].label}</span>
                      <span className="finding-title">{a.name}</span>
                      <span className="finding-sum sub">{a.verified ? `✔ ${a.publisher}` : `Éditeur non vérifié${a.publisher ? ` (${a.publisher})` : ''}`}</span>
                      {!a.enabled && <span className="badge off">Bloquée</span>}
                    </summary>
                    <div className="finding-body">
                      {a.high_risk_scopes.length > 0 && <p><strong>Permissions sensibles :</strong> {a.high_risk_scopes.join(', ')}</p>}
                      {a.application.length > 0 && (
                        <p>Permissions d’application (sans utilisateur, toute l’organisation) : {a.application.map((x) => `${x.permission} (${x.resource})`).join(', ')}</p>
                      )}
                      {a.delegated.map((d, i) => (
                        <p key={i}>Au nom de <strong>{d.who}</strong> : {d.scopes.join(', ')} <span className="sub">({d.resource})</span></p>
                      ))}
                      <div className="sub">Autorisée {a.created ? `le ${new Date(a.created).toLocaleDateString()}` : ''}</div>
                      <div style={{ marginTop: 8 }}>
                        {a.enabled
                          ? <button className="danger" onClick={() => setAppTarget(a)}>Bloquer la connexion de l’application</button>
                          : <button onClick={() => toggleApp.mutate({ app: a, enabled: true })}>Rétablir</button>}
                      </div>
                    </div>
                  </details>
                ))}
              </>
            )}
            {tab === 'admins' && (
              <>
                <h2>Administrateurs</h2>
                {s.admins.notes.map((n, i) => <div key={i} className="messagebar">{n.text}</div>)}
                <table className="grid">
                  <thead><tr><th>Compte</th><th>Rôles</th><th>MFA</th></tr></thead>
                  <tbody>
                    {s.admins.people.map((p) => (
                      <tr key={p.upn}>
                        <td><strong>{p.name || p.upn}</strong><div className="sub">{p.upn}</div></td>
                        <td>{p.roles.join(', ')}</td>
                        <td>{p.mfa === true ? <span className="badge ok">Oui</span> : p.mfa === false ? <span className="badge err">Non</span> : '—'}
                          {p.flags.filter((f) => f !== 'Sans MFA').map((f) => <span key={f} className="badge warn" style={{ marginLeft: 4 }}>{f}</span>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            {tab === 'inactive' && (
              <>
                <h2>Comptes inactifs depuis 90 jours</h2>
                <div className="sub" style={{ marginBottom: 8 }}>Un compte oublié avec un mot de passe valide est une porte d’entrée. Les comptes administrateurs sont exclus de la désactivation en lot.</div>
                {s.inactive === null ? <div className="empty">Dernière activité indisponible (rapports Microsoft 365 illisibles).</div> : accountTable(s.inactive, false)}
              </>
            )}
            {tab === 'guests' && (
              <>
                <h2>Invités</h2>
                {accountTable(s.guests, true)}
              </>
            )}
          </section>

          {q.data!.history.length > 0 && (
            <section className="card">
              <h2>Historique</h2>
              {q.data!.history.map((h) => (
                <div className="row" key={h.id}>
                  <span className={`badge ${h.ok ? 'ok' : 'err'}`}>{h.ok ? 'OK' : 'Échec'}</span>
                  <div className="grow">{h.changes.join(' · ')}<div className="sub">{h.performed_by} · {new Date(h.create_at).toLocaleString()}{h.detail ? ` · ${h.detail}` : ''}</div></div>
                </div>
              ))}
            </section>
          )}
        </>
      )}

      {confirmAccounts && (
        <ConfirmDialog title={`Désactiver ${selected.size} compte(s)`} confirmLabel="Désactiver" requireText="DESACTIVER"
          busy={disable.isPending} onCancel={() => setConfirmAccounts(false)} onConfirm={() => disable.mutate([...selected])}>
          <p>
            Ces comptes ne pourront plus se connecter. Rien n’est supprimé : boîte, fichiers et licence restent en place, et le
            compte peut être réactivé dans Microsoft 365. Les administrateurs sont refusés par sécurité.
          </p>
        </ConfirmDialog>
      )}
      {appTarget && (
        <ConfirmDialog title={`Bloquer « ${appTarget.name} »`} confirmLabel="Bloquer l’application" requireText="DESACTIVER"
          busy={toggleApp.isPending} onCancel={() => setAppTarget(null)} onConfirm={() => toggleApp.mutate({ app: appTarget, enabled: false })}>
          <p>
            Plus personne ne pourra se connecter à cette application avec un compte du tenant, et elle ne recevra plus de jeton
            d’accès. Ses permissions ne sont pas supprimées : tu peux la rétablir à tout moment.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
