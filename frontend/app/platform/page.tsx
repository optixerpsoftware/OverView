'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import Loading from '../../components/Loading';
import Modal from '../../components/Modal';
import UsageBar from '../../components/UsageBar';
import { api } from '../../lib/api';
import { ago } from '../../lib/alerts';
import { ACTION_LABEL, type PlatformMsp, type Plans } from '../../lib/platformTypes';
import { useApi, useApiMutation } from '../../lib/useApi';

interface Overview {
  msps: PlatformMsp[];
  admins: { id: number; username: string; email: string; active: boolean; created_at: string; last_login: string | null }[];
  journal: { at: string; actor: string; action: string; target: string; detail: string }[];
  plans: Plans;
}
type Tab = 'msps' | 'admins' | 'journal';
type Filter = 'all' | 'attention' | 'suspended';

export default function PlatformConsole() {
  const router = useRouter();
  const ov = useApi<Overview>('/platform/overview/', { refetchInterval: () => 60_000 });
  const [tab, setTab] = useState<Tab>('msps');
  const [filter, setFilter] = useState<Filter>('all');
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState(false);
  const [plan, setPlan] = useState('essentiel');
  const [newAdmin, setNewAdmin] = useState(false);
  const [secret, setSecret] = useState<{ username: string; secret: string; otpauth: string } | null>(null);
  const [qr, setQr] = useState('');
  useEffect(() => { document.title = 'Console — Plateforme OverView'; }, []);
  useEffect(() => {
    if (!secret) { setQr(''); return; }
    import('qrcode').then((m) => m.toDataURL(secret.otpauth, { width: 192, margin: 1 })).then(setQr).catch(() => setQr(''));
  }, [secret]);

  const inv = ['/platform/'];
  const createMsp = useApiMutation((b: Record<string, unknown>) => api<{ domain: string; name: string }>('/platform/msps/', { method: 'POST', body: b }),
    { invalidate: inv, success: (r) => `Espace « ${r.name} » créé : ${r.domain}`, onSuccess: () => setCreating(false) });
  const createAdmin = useApiMutation((b: Record<string, unknown>) => api<{ username: string; secret: string; otpauth: string }>('/platform/admins/', { method: 'POST', body: b }),
    { invalidate: inv, onSuccess: (r) => { setNewAdmin(false); setSecret(r); } });
  const toggleAdmin = useApiMutation((v: { id: number; active: boolean }) => api(`/platform/admins/${v.id}/`, { method: 'PATCH', body: { active: v.active } }),
    { invalidate: inv, success: (_, v) => (v.active ? 'Réactivé.' : 'Désactivé.') });
  const resetAdminMfa = useApiMutation((id: number) => api<{ username: string; secret: string; otpauth: string }>(`/platform/admins/${id}/reset-mfa/`, { method: 'POST' }),
    { invalidate: inv, onSuccess: (r) => setSecret(r) });

  const d = ov.data;
  const all = useMemo(() => d?.msps ?? [], [d]);
  const shown = all.filter((m) =>
    (filter === 'all' || (filter === 'attention' ? m.alerts.length > 0 : !m.active || m.expired))
    && (!q.trim() || `${m.name} ${m.slug} ${m.contact_name} ${m.domains.map((x) => x.domain).join(' ')}`.toLowerCase().includes(q.trim().toLowerCase())));
  const t = all.reduce((acc, m) => ({
    tenants: acc.tenants + m.usage.tenants, orgs: acc.orgs + m.usage.organizations, users: acc.users + m.usage.users,
    alerts: acc.alerts + (m.stats.open_alerts ?? 0), attention: acc.attention + (m.alerts.length ? 1 : 0),
  }), { tenants: 0, orgs: 0, users: 0, alerts: 0, attention: 0 });

  function submitMsp(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget).entries()) as Record<string, string>;
    const limits = { tenants: f.tenants ?? '', organizations: f.organizations ?? '', users: f.users ?? '' };
    createMsp.mutate({
      name: f.name, slug: f.slug, admin_username: f.admin_username, admin_password: f.admin_password, admin_email: f.admin_email,
      subscription: { plan, limits: plan === 'personnalise' ? limits : undefined, expires_at: f.expires_at || '',
        contact_name: f.contact_name || '', contact_email: f.contact_email || '' },
    });
  }

  return (
    <>
      <div className="pagehead">
        <div><h1>Console de la plateforme</h1><div className="sub">MSP hébergés, abonnements et accès · un MSP n’est créé que d’ici</div></div>
        <button className="primary" onClick={() => setCreating(true)}>+ Nouveau MSP</button>
      </div>
      {ov.error && <div className="messagebar error">{ov.error}</div>}
      {ov.loading && <Loading lines={6} />}
      {d && (
        <>
          <div className="tiles">
            <div className="tile static"><span className="tile-label">MSP</span><span className="tile-value">{all.length}</span>
              <span className="tile-hint">{all.filter((m) => !m.active || m.expired).length} suspendu(s) ou échu(s)</span></div>
            <button className={`tile ${t.attention ? 'warn' : ''}`} onClick={() => { setTab('msps'); setFilter('attention'); }}>
              <span className="tile-label">À surveiller</span><span className="tile-value">{t.attention}</span><span className="tile-hint">quota ou échéance</span></button>
            <div className="tile static"><span className="tile-label">Tenants Microsoft 365</span><span className="tile-value">{t.tenants}</span>
              <span className="tile-hint">{t.orgs} organisations clientes</span></div>
            <div className="tile static"><span className="tile-label">Comptes actifs</span><span className="tile-value">{t.users}</span>
              <span className="tile-hint">{t.alerts} alerte(s) ouverte(s)</span></div>
          </div>
          <nav className="tabs" role="tablist">
            {([['msps', 'MSP'], ['admins', 'Super-administrateurs'], ['journal', 'Journal']] as [Tab, string][]).map(([k, l]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>{l}</button>
            ))}
          </nav>
          <section className="panel">
            {tab === 'msps' && (
              <>
                <div className="filters">
                  <div className="chip-group">
                    {([['all', 'Tous'], ['attention', 'À surveiller'], ['suspended', 'Suspendus / échus']] as [Filter, string][]).map(([k, l]) => (
                      <button key={k} className={`chip-btn ${filter === k ? 'on' : ''}`} onClick={() => setFilter(k)}>{l}</button>
                    ))}
                  </div>
                  <input type="search" className="searchbox" placeholder="Rechercher (nom, adresse, contact)" value={q} onChange={(e) => setQ(e.target.value)} />
                </div>
                <table className="grid">
                  <thead><tr><th>MSP</th><th>Forfait</th><th>Tenants</th><th>Organisations</th><th>Comptes</th><th>Échéance</th><th>État</th></tr></thead>
                  <tbody>
                    {shown.map((m) => (
                      <tr key={m.id} className="clickable" onClick={() => router.push(`/platform/msps/${m.id}`)}>
                        <td><Link href={`/platform/msps/${m.id}`} onClick={(e) => e.stopPropagation()}><strong>{m.name}</strong></Link>
                          <div className="sub">{m.domains.find((x) => x.primary)?.domain ?? m.slug}</div>
                          {m.alerts.map((a, i) => <div key={i}><span className={`badge ${a.level}`}>{a.text}</span></div>)}</td>
                        <td>{m.plan_label}</td>
                        <td><UsageBar used={m.usage.tenants} limit={m.limits.tenants} /></td>
                        <td><UsageBar used={m.usage.organizations} limit={m.limits.organizations} /></td>
                        <td><UsageBar used={m.usage.users} limit={m.limits.users} /></td>
                        <td>{m.expires_at ? new Date(m.expires_at).toLocaleDateString() : <span className="sub">aucune</span>}</td>
                        <td>{!m.active ? <span className="badge err">Suspendu</span> : m.expired ? <span className="badge err">Échu</span> : <span className="badge ok">Actif</span>}</td>
                      </tr>
                    ))}
                    {shown.length === 0 && <tr><td colSpan={7} className="sub">Aucun MSP pour ce filtre.</td></tr>}
                  </tbody>
                </table>
              </>
            )}
            {tab === 'admins' && (
              <>
                <div className="filters"><button className="primary" onClick={() => setNewAdmin(true)}>+ Super-administrateur</button></div>
                <table className="grid">
                  <thead><tr><th>Compte</th><th>Créé</th><th>Dernière connexion</th><th>État</th><th /></tr></thead>
                  <tbody>{d.admins.map((a) => (
                    <tr key={a.id}>
                      <td><strong>{a.username}</strong><div className="sub">{a.email}</div></td>
                      <td>{new Date(a.created_at).toLocaleDateString()}</td>
                      <td>{a.last_login ? ago(a.last_login) : 'jamais'}</td>
                      <td>{a.active ? <span className="badge ok">Actif</span> : <span className="badge off">Désactivé</span>}</td>
                      <td align="right">
                        <button className="ghost" onClick={() => resetAdminMfa.mutate(a.id)}>Nouvelle clé MFA</button>
                        <button className={a.active ? 'ghost danger' : 'ghost'} onClick={() => toggleAdmin.mutate({ id: a.id, active: !a.active })}>
                          {a.active ? 'Désactiver' : 'Réactiver'}</button>
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </>
            )}
            {tab === 'journal' && (
              <table className="grid">
                <thead><tr><th>Quand</th><th>Qui</th><th>Action</th><th>Cible</th><th>Détail</th></tr></thead>
                <tbody>{d.journal.map((j, i) => (
                  <tr key={i}><td title={new Date(j.at).toLocaleString()}>{ago(j.at)}</td><td>{j.actor}</td><td>{ACTION_LABEL[j.action] ?? j.action}</td><td>{j.target}</td><td>{j.detail}</td></tr>
                ))}</tbody>
              </table>
            )}
          </section>
        </>
      )}

      {creating && d && (
        <Modal title="Nouveau MSP" onClose={() => setCreating(false)} wide>
          <form onSubmit={submitMsp} className="form" style={{ maxWidth: 'none' }}>
            <input name="name" placeholder="Nom du MSP (ex. Acme TI)" required autoFocus />
            <input name="slug" placeholder="Identifiant (ex. acme) → acme.<domaine>" required pattern="[a-z][a-z0-9-]{1,30}[a-z0-9]" />
            <div className="field"><label htmlFor="plan">Forfait</label>
              <select id="plan" value={plan} onChange={(e) => setPlan(e.target.value)}>
                {Object.entries(d.plans).map(([k, p]) => (
                  <option key={k} value={k}>{p.label}{k !== 'personnalise' ? ` — ${p.max_tenants ?? '∞'} tenants, ${p.max_organizations ?? '∞'} organisations, ${p.max_users ?? '∞'} comptes` : ''}</option>
                ))}
              </select></div>
            {plan === 'personnalise' && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
                <input name="tenants" type="number" min={0} placeholder="Tenants (vide = ∞)" />
                <input name="organizations" type="number" min={0} placeholder="Organisations (∞)" />
                <input name="users" type="number" min={0} placeholder="Comptes (∞)" />
              </div>
            )}
            <div className="field"><label htmlFor="exp">Échéance (facultatif)</label><input id="exp" name="expires_at" type="date" /></div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <input name="contact_name" placeholder="Contact (nom)" />
              <input name="contact_email" type="email" placeholder="Contact (courriel)" />
            </div>
            <hr style={{ border: 0, borderTop: '1px solid var(--border)', width: '100%' }} />
            <input name="admin_username" placeholder="Premier administrateur du MSP (nom d'utilisateur)" required />
            <input name="admin_email" type="email" placeholder="Son courriel (facultatif)" />
            <input name="admin_password" type="password" placeholder="Mot de passe initial (12 caractères ou plus)" required minLength={12} autoComplete="new-password" />
            <div className="dialog-actions">
              <button type="button" onClick={() => setCreating(false)}>Annuler</button>
              <button type="submit" className="primary" disabled={createMsp.isPending}>{createMsp.isPending ? 'Création de l’espace…' : 'Créer'}</button>
            </div>
          </form>
        </Modal>
      )}
      {newAdmin && (
        <Modal title="Nouveau super-administrateur" onClose={() => setNewAdmin(false)}>
          <form onSubmit={(e) => { e.preventDefault(); createAdmin.mutate(Object.fromEntries(new FormData(e.currentTarget).entries())); }} className="form" style={{ maxWidth: 'none' }}>
            <input name="username" placeholder="Nom d'utilisateur" required autoFocus />
            <input name="email" type="email" placeholder="Courriel (facultatif)" />
            <input name="password" type="password" placeholder="Mot de passe (12 caractères ou plus)" required minLength={12} autoComplete="new-password" />
            <div className="dialog-actions">
              <button type="button" onClick={() => setNewAdmin(false)}>Annuler</button>
              <button type="submit" className="primary" disabled={createAdmin.isPending}>Créer</button>
            </div>
          </form>
        </Modal>
      )}
      {secret && (
        <Modal title={`Clé MFA de ${secret.username}`} onClose={() => setSecret(null)}>
          <p><strong>Affichée une seule fois.</strong> La personne scanne ce QR code dans son application d’authentification ; l’ancienne clé ne fonctionne plus.</p>
          {qr && <img src={qr} alt="QR code" width={192} height={192} style={{ background: '#fff', borderRadius: 6 }} />}
          <p className="sub">Clé : <code style={{ userSelect: 'all' }}>{secret.secret}</code></p>
          <div className="dialog-actions"><button className="primary" onClick={() => setSecret(null)}>C’est fait</button></div>
        </Modal>
      )}
    </>
  );
}
