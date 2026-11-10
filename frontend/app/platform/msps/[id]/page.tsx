'use client';
import Link from 'next/link';
import { use, useEffect, useState, type FormEvent } from 'react';
import ConfirmDialog from '../../../../components/ConfirmDialog';
import Loading from '../../../../components/Loading';
import Modal from '../../../../components/Modal';
import UsageBar from '../../../../components/UsageBar';
import { api } from '../../../../lib/api';
import { ago } from '../../../../lib/alerts';
import { ACTION_LABEL, LIMIT_LABEL, type LimitKind, type PlatformMspDetail, type Plans } from '../../../../lib/platformTypes';
import { useApi, useApiMutation } from '../../../../lib/useApi';

type Tab = 'subscription' | 'users' | 'orgs' | 'domains' | 'history';
const KINDS: LimitKind[] = ['tenants', 'organizations', 'users'];
const ROLE: Record<string, string> = { admin: 'Administrateur', operator: 'Technicien', viewer: 'Lecture seule', client: 'Client' };

export default function MspDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const base = `/platform/msps/${id}/`;
  const q = useApi<PlatformMspDetail>(base);
  const plans = useApi<{ plans: Plans }>('/platform/overview/');
  const [tab, setTab] = useState<Tab>('subscription');
  const [suspend, setSuspend] = useState(false);
  const [rescue, setRescue] = useState(false);
  const [form, setForm] = useState<{ plan: string; limits: Record<LimitKind, string>; expires_at: string; contact_name: string; contact_email: string; notes: string } | null>(null);
  const m = q.data;

  useEffect(() => {
    if (!m) return;
    document.title = `${m.name} — Plateforme`;
    setForm({
      plan: m.plan, expires_at: m.expires_at ?? '', contact_name: m.contact_name, contact_email: m.contact_email, notes: m.notes,
      limits: { tenants: m.limits.tenants?.toString() ?? '', organizations: m.limits.organizations?.toString() ?? '', users: m.limits.users?.toString() ?? '' },
    });
  }, [m]);

  const inv = ['/platform/'];
  const patch = useApiMutation((body: Record<string, unknown>) => api(base, { method: 'PATCH', body }),
    { invalidate: inv, success: 'Enregistré.', onSuccess: () => setSuspend(false) });
  const addDomain = useApiMutation((domain: string) => api(`${base}domains/`, { method: 'POST', body: { domain } }), { invalidate: inv, success: 'Adresse ajoutée.' });
  const delDomain = useApiMutation((domain_id: number) => api(`${base}domains/`, { method: 'DELETE', body: { domain_id } }), { invalidate: inv, success: 'Adresse retirée.' });
  const doRescue = useApiMutation((b: Record<string, unknown>) => api(`${base}rescue-admin/`, { method: 'POST', body: b }),
    { invalidate: inv, success: 'Administrateur créé dans l’espace du MSP.', onSuccess: () => setRescue(false) });
  const resetMfa = useApiMutation((userId: number) => api(`${base}users/${userId}/reset-mfa/`, { method: 'POST' }),
    { invalidate: inv, success: 'MFA réinitialisé : le compte devra le réactiver à sa prochaine connexion.' });

  const planList = plans.data?.plans ?? {};
  const preset = form ? planList[form.plan] : undefined;
  const custom = form?.plan === 'personnalise';
  const port = typeof window !== 'undefined' && window.location.port ? `:${window.location.port}` : '';
  const proto = typeof window !== 'undefined' ? window.location.protocol : 'https:';
  const primary = m?.domains.find((d) => d.primary)?.domain;

  function save(e: FormEvent) {
    e.preventDefault();
    if (!form) return;
    patch.mutate({ subscription: { ...form, limits: custom ? form.limits : undefined } });
  }

  return (
    <>
      <div className="breadcrumb"><Link href="/platform">Console</Link><span className="sep">›</span><span>{m?.name ?? '…'}</span></div>
      {q.error && <div className="messagebar error">{q.error}</div>}
      {q.loading && <Loading lines={6} />}
      {m && form && (
        <>
          <header className="hero">
            <div className="avatar-lg" aria-hidden="true">{m.name.slice(0, 2).toUpperCase()}</div>
            <div className="hero-id">
              <h1>{m.name}</h1>
              <div className="sub">{primary ?? m.slug} · forfait {m.plan_label} · créé le {new Date(m.created_at).toLocaleDateString()}</div>
              <div className="chips">
                {m.active && !m.expired ? <span className="badge ok">Actif</span> : <span className="badge err">{m.expired ? 'Abonnement échu' : 'Suspendu'}</span>}
                {m.expires_at && <span className="badge off">Échéance {new Date(m.expires_at).toLocaleDateString()}</span>}
              </div>
            </div>
            <div className="hero-actions">
              {primary && m.active && <a className="btn" href={`${proto}//${primary}${port}/login`} target="_blank" rel="noreferrer">Ouvrir l’espace</a>}
              <button onClick={() => setRescue(true)}>Admin de secours</button>
              {m.active ? <button className="danger" onClick={() => setSuspend(true)}>Suspendre</button>
                : <button className="primary" onClick={() => patch.mutate({ active: true })}>Réactiver</button>}
            </div>
          </header>

          {m.alerts.map((a, i) => <div key={i} className={`messagebar ${a.level === 'err' ? 'error' : ''}`}>{a.text}</div>)}

          <div className="tiles">
            {KINDS.map((k) => (
              <div key={k} className="tile static"><span className="tile-label">{LIMIT_LABEL[k]}</span><UsageBar used={m.usage[k]} limit={m.limits[k]} /></div>
            ))}
            <div className={`tile static ${m.stats.critical_alerts ? 'warn' : ''}`}><span className="tile-label">Alertes ouvertes</span>
              <span className="tile-value">{m.stats.open_alerts ?? 0}</span><span className="tile-hint">{m.stats.critical_alerts ?? 0} critique(s) · {m.stats.mailboxes ?? 0} boîte(s)</span></div>
          </div>

          <nav className="tabs" role="tablist">
            {([['subscription', 'Abonnement'], ['users', `Comptes (${m.users.length})`], ['orgs', `Organisations (${m.organizations.length})`],
              ['domains', 'Adresses'], ['history', 'Historique']] as [Tab, string][]).map(([k, l]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>{l}</button>
            ))}
          </nav>
          <section className="panel">
            {tab === 'subscription' && (
              <form className="settings" onSubmit={save}>
                <div className="setting">
                  <div><strong>Forfait</strong><div className="sub">Les forfaits fixent les limites ; « Personnalisé » permet de les choisir.</div></div>
                  <select value={form.plan} onChange={(e) => setForm({ ...form, plan: e.target.value })}>
                    {Object.entries(planList).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
                  </select>
                </div>
                {KINDS.map((k) => {
                  const presetVal = preset?.[`max_${k}` as 'max_tenants'];
                  return (
                    <div className="setting" key={k}>
                      <div><strong>{LIMIT_LABEL[k]} maximum</strong><div className="sub">Utilisés : {m.usage[k]}. Vide = illimité.</div></div>
                      {custom
                        ? <input type="number" min={0} value={form.limits[k]} placeholder="Illimité"
                            onChange={(e) => setForm({ ...form, limits: { ...form.limits, [k]: e.target.value } })} />
                        : <span>{presetVal === null || presetVal === undefined ? 'Illimité' : presetVal}</span>}
                    </div>
                  );
                })}
                <div className="setting">
                  <div><strong>Échéance de l’abonnement</strong><div className="sub">Au lendemain de cette date, l’espace devient inaccessible et ses tâches s’arrêtent (données conservées).</div></div>
                  <input type="date" value={form.expires_at} onChange={(e) => setForm({ ...form, expires_at: e.target.value })} />
                </div>
                <div className="setting">
                  <div><strong>Contact</strong></div>
                  <div style={{ display: 'grid', gap: 6 }}>
                    <input placeholder="Nom" value={form.contact_name} onChange={(e) => setForm({ ...form, contact_name: e.target.value })} />
                    <input type="email" placeholder="Courriel" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} />
                  </div>
                </div>
                <div className="setting">
                  <div><strong>Notes internes</strong><div className="sub">Visibles uniquement par les super-administrateurs.</div></div>
                  <textarea rows={3} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
                </div>
                <div style={{ padding: '12px 0' }}><button type="submit" className="primary" disabled={patch.isPending}>{patch.isPending ? 'Enregistrement…' : 'Enregistrer'}</button></div>
              </form>
            )}
            {tab === 'users' && (
              <table className="grid">
                <thead><tr><th>Compte</th><th>Rôle</th><th>MFA</th><th>Dernière connexion</th><th>État</th><th /></tr></thead>
                <tbody>{m.users.map((u) => (
                  <tr key={u.id}>
                    <td><strong>{u.username}</strong><div className="sub">{u.email}</div></td>
                    <td>{ROLE[u.role] ?? u.role}</td>
                    <td>{u.mfa ? <span className="badge ok">Activé</span> : <span className="badge err">Non</span>}</td>
                    <td>{u.last_login ? ago(u.last_login) : 'jamais'}</td>
                    <td>{u.active ? <span className="badge ok">Actif</span> : <span className="badge off">Désactivé</span>}</td>
                    <td align="right">{u.mfa && <button className="ghost" onClick={() => resetMfa.mutate(u.id)}>Réinitialiser le MFA</button>}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
            {tab === 'orgs' && (
              <table className="grid">
                <thead><tr><th>Organisation</th><th>Tenants Microsoft 365</th></tr></thead>
                <tbody>{m.organizations.map((o) => (
                  <tr key={o.id}><td><strong>{o.name}</strong></td>
                    <td>{o.tenants.length === 0 ? <span className="sub">aucun</span> : o.tenants.map((t, i) => (
                      <div key={i}>{t.name} {!t.active && <span className="badge off">inactif</span>} {t.error && <span className="badge err">connexion en échec</span>}</div>
                    ))}</td></tr>
                ))}</tbody>
              </table>
            )}
            {tab === 'domains' && (
              <>
                {m.domains.map((d) => (
                  <div className="line" key={d.id}>
                    <span className="grow">{d.domain} {d.primary && <span className="badge ok">Principale</span>}</span>
                    {!d.primary && <button className="ghost danger" onClick={() => delDomain.mutate(d.id)}>Retirer</button>}
                  </div>
                ))}
                <form onSubmit={(e) => { e.preventDefault(); addDomain.mutate(String(new FormData(e.currentTarget).get('domain'))); e.currentTarget.reset(); }}
                  className="filters">
                  <input name="domain" placeholder="ex. securite.client.ca" required />
                  <button type="submit" className="primary">Ajouter l’adresse</button>
                </form>
                <p className="sub">L’adresse doit pointer (DNS) vers ce serveur.</p>
              </>
            )}
            {tab === 'history' && (
              <table className="grid">
                <thead><tr><th>Quand</th><th>Qui</th><th>Action</th><th>Détail</th></tr></thead>
                <tbody>{m.history.map((h, i) => (
                  <tr key={i}><td title={new Date(h.at).toLocaleString()}>{ago(h.at)}</td><td>{h.actor}</td><td>{ACTION_LABEL[h.action] ?? h.action}</td><td>{h.detail}</td></tr>
                ))}</tbody>
              </table>
            )}
          </section>

          {suspend && (
            <ConfirmDialog title={`Suspendre « ${m.name} »`} confirmLabel="Suspendre" requireText={m.slug} busy={patch.isPending}
              onCancel={() => setSuspend(false)} onConfirm={() => patch.mutate({ active: false })}>
              <p>Plus personne ne pourra accéder à l’espace de ce MSP et ses tâches de fond s’arrêtent. Ses données sont conservées : la réactivation rétablit tout.</p>
            </ConfirmDialog>
          )}
          {rescue && (
            <Modal title={`Administrateur de secours — ${m.name}`} onClose={() => setRescue(false)}>
              <p className="sub">Crée un compte administrateur dans l’espace du MSP (support, accès perdu). Action journalisée.</p>
              <form onSubmit={(e) => { e.preventDefault(); doRescue.mutate(Object.fromEntries(new FormData(e.currentTarget).entries())); }}
                className="form" style={{ maxWidth: 'none' }}>
                <input name="username" placeholder="Nom d'utilisateur" required autoFocus />
                <input name="password" type="password" placeholder="Mot de passe (12 caractères ou plus)" required minLength={12} autoComplete="new-password" />
                <div className="dialog-actions">
                  <button type="button" onClick={() => setRescue(false)}>Annuler</button>
                  <button type="submit" className="primary" disabled={doRescue.isPending}>Créer</button>
                </div>
              </form>
            </Modal>
          )}
        </>
      )}
    </>
  );
}
