'use client';
import { useEffect, useState, type FormEvent } from 'react';
import Loading from '../../../components/Loading';
import Modal from '../../../components/Modal';
import QuotaHint from '../../../components/QuotaHint';
import { useSubscription } from '../../../lib/subscription';
import { api } from '../../../lib/api';
import { ago } from '../../../lib/alerts';
import type { Organization } from '../../../lib/types';
import { useApi, useApiMutation } from '../../../lib/useApi';

type Role = 'client' | 'viewer' | 'operator' | 'admin';
interface User { id: number; username: string; email: string; active: boolean; role: Role; mfa_enabled: boolean; superuser: boolean; last_login: string | null; all_orgs: boolean; org_ids: string[] }
const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: 'client', label: 'Client (portail)', hint: 'Un client de ton MSP : voit uniquement les alertes et rapports de son organisation, en lecture seule.' },
  { value: 'viewer', label: 'Lecture seule', hint: 'Consulte tout, ne modifie rien.' },
  { value: 'operator', label: 'Technicien', hint: 'Agit sur les comptes et partages (urgence, révocations, quarantaine…), sans toucher aux réglages des tenants.' },
  { value: 'admin', label: 'Administrateur', hint: 'Tout, y compris organisations, connecteurs, réglages des tenants et comptes OverView.' },
];

export default function UsersPage() {
  const users = useApi<User[]>('/accounts/users/');
  const orgs = useApi<Organization[]>('/organizations/');
  const orgName = (id: string) => orgs.data?.find((o) => o.id === id)?.name ?? '…';
  const [creating, setCreating] = useState(false);
  const [scoping, setScoping] = useState<User | null>(null);
  const [scopeAll, setScopeAll] = useState(true);
  const [scopeIds, setScopeIds] = useState<Set<string>>(new Set());
  const openScope = (u: User) => { setScoping(u); setScopeAll(u.all_orgs && u.role !== 'client'); setScopeIds(new Set(u.org_ids)); };
  useEffect(() => { document.title = 'Comptes OverView — OverView'; }, []);
  const { full } = useSubscription();
  const update = useApiMutation(
    (v: { id: number; body: Record<string, unknown> }) => api<User>(`/accounts/users/${v.id}/`, { method: 'PATCH', body: v.body }),
    { invalidate: ['/accounts/'], success: (u) => `Compte « ${u.username} » mis à jour.` },
  );
  const create = useApiMutation(
    (body: Record<string, unknown>) => api<User>('/accounts/users/', { method: 'POST', body }),
    { invalidate: ['/accounts/'], success: (u) => `Compte « ${u.username} » créé.`, onSuccess: () => setCreating(false) },
  );

  function onCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const role = String(f.get('role'));
    const org = String(f.get('org') || '');
    create.mutate({ username: f.get('username'), email: f.get('email'), password: f.get('password'), role,
      all_orgs: role !== 'client' && !org, org_ids: org ? [org] : [] });
  }

  return (
    <>
      <div className="pagehead">
        <div><h1>Comptes OverView</h1><div className="sub">Qui peut utiliser OverView, et avec quels droits</div></div>
        <QuotaHint kind="users" />
        <button className="primary" disabled={full('users')} title={full('users') ? 'Limite de ton abonnement atteinte : voir Mon abonnement' : undefined} onClick={() => setCreating(true)}>+ Nouveau compte</button>
      </div>
      <section className="card">
        {ROLES.map((r) => <div className="kv" key={r.value}><span>{r.label}</span><span className="sub" style={{ textAlign: 'right' }}>{r.hint}</span></div>)}
      </section>
      {users.error && <div className="messagebar error">{users.error}</div>}
      {users.loading && <Loading />}
      {users.data && (
        <section className="card">
          <table className="grid">
            <thead><tr><th>Compte</th><th>Rôle</th><th>Clients</th><th>MFA</th><th>Dernière connexion</th><th>État</th><th /></tr></thead>
            <tbody>
              {users.data.map((u) => (
                <tr key={u.id}>
                  <td><strong>{u.username}</strong>{u.email && <div className="sub">{u.email}</div>}</td>
                  <td>
                    <select value={u.role} aria-label={`Rôle de ${u.username}`} onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value } })}>
                      {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                    </select>
                  </td>
                  <td>
                    {u.role === 'admin' || u.superuser ? <span className="sub">Tous (administrateur)</span>
                      : u.all_orgs && u.role !== 'client' ? <span className="sub">Tous les clients</span>
                      : <span>{u.org_ids.map(orgName).join(', ') || <span className="badge err">Aucun</span>}</span>}
                    {u.role !== 'admin' && !u.superuser && <div><button className="ghost" onClick={() => openScope(u)}>Modifier</button></div>}
                  </td>
                  <td>{u.mfa_enabled ? <span className="badge ok">Activé</span> : <span className="badge err">Non</span>}</td>
                  <td>{u.last_login ? ago(u.last_login) : 'jamais'}</td>
                  <td>{u.active ? <span className="badge ok">Actif</span> : <span className="badge off">Désactivé</span>}</td>
                  <td align="right">
                    {u.mfa_enabled && <button className="ghost" onClick={() => update.mutate({ id: u.id, body: { reset_mfa: true } })}>Réinitialiser le MFA</button>}
                    <button className={u.active ? 'ghost danger' : 'ghost'} onClick={() => update.mutate({ id: u.id, body: { active: !u.active } })}>
                      {u.active ? 'Désactiver' : 'Réactiver'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {scoping && (
        <Modal title={`Clients de ${scoping.username}`} onClose={() => setScoping(null)}>
          {scoping.role !== 'client' && (
            <label className="switch" style={{ marginBottom: 10 }}>
              <input type="checkbox" checked={scopeAll} onChange={(e) => setScopeAll(e.target.checked)} /> Tous les clients (actuels et futurs)
            </label>
          )}
          {!scopeAll && (
            <ul className="picklist">
              {(orgs.data ?? []).map((o) => (
                <li key={o.id}><label>
                  <input type="checkbox" checked={scopeIds.has(o.id)} onChange={() => setScopeIds((p) => {
                    const n = new Set(p); if (n.has(o.id)) n.delete(o.id); else n.add(o.id); return n;
                  })} /> {o.name}
                </label></li>
              ))}
            </ul>
          )}
          <div className="dialog-actions">
            <button onClick={() => setScoping(null)}>Annuler</button>
            <button className="primary" disabled={update.isPending || (!scopeAll && scopeIds.size === 0)}
              onClick={() => update.mutate({ id: scoping.id, body: { all_orgs: scopeAll, org_ids: [...scopeIds] } }, { onSuccess: () => setScoping(null) })}>
              Enregistrer
            </button>
          </div>
        </Modal>
      )}
      {creating && (
        <Modal title="Nouveau compte OverView" onClose={() => setCreating(false)}>
          <form onSubmit={onCreate} className="form" style={{ maxWidth: 'none' }}>
            <div className="field"><label htmlFor="u-name">Nom d’utilisateur</label><input id="u-name" name="username" required autoFocus /></div>
            <div className="field"><label htmlFor="u-mail">Courriel (facultatif)</label><input id="u-mail" name="email" type="email" /></div>
            <div className="field"><label htmlFor="u-pw">Mot de passe initial</label><input id="u-pw" name="password" type="password" autoComplete="new-password" required minLength={12} /></div>
            <div className="field"><label htmlFor="u-role">Rôle</label>
              <select id="u-role" name="role" defaultValue="viewer">{ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}</select></div>
            <div className="field"><label htmlFor="u-org">Client attribué</label>
              <select id="u-org" name="org" defaultValue="">
                <option value="">Tous les clients (obligatoire de choisir pour un compte Client)</option>
                {(orgs.data ?? []).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
              <div className="sub">Plusieurs clients : crée le compte puis « Modifier » dans la colonne Clients.</div></div>
            <div className="dialog-actions">
              <button type="button" onClick={() => setCreating(false)}>Annuler</button>
              <button type="submit" className="primary" disabled={create.isPending}>Créer</button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
