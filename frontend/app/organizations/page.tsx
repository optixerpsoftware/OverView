'use client';
import Link from 'next/link';
import { useEffect, useState, type FormEvent } from 'react';
import ConfirmDialog from '../../components/ConfirmDialog';
import Loading from '../../components/Loading';
import Menu from '../../components/Menu';
import Modal from '../../components/Modal';
import QuotaHint from '../../components/QuotaHint';
import { api } from '../../lib/api';
import type { Organization } from '../../lib/types';
import { useSubscription } from '../../lib/subscription';
import { useApi, useApiMutation } from '../../lib/useApi';

export default function Home() {
  const { data: orgs, error, loading } = useApi<Organization[]>('/organizations/');
  const [toDelete, setToDelete] = useState<Organization | null>(null);

  // Les mutations invalident la liste : plus besoin de « recharger à la main », et les erreurs
  // s'affichent en notification.
  const create = useApiMutation(
    (body: { name: string; description: string | null }) =>
      api<Organization>('/organizations/', { method: 'POST', body }),
    { invalidate: ['/organizations/'], success: (o) => `Organisation « ${o.name} » créée.` },
  );
  const remove = useApiMutation(
    (o: Organization) => api<null>(`/organizations/${o.id}/`, { method: 'DELETE' }),
    {
      invalidate: ['/organizations/', '/connectors/', '/outlook/'],   // la suppression est en cascade
      success: (_, o) => `Organisation « ${o.name} » supprimée.`,
      onSuccess: () => setToDelete(null),
    },
  );

  function onCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    create.mutate(
      { name: String(f.get('name')), description: String(f.get('description') ?? '') || null },
      { onSuccess: () => { form.reset(); setCreating(false); } },
    );
  }

  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const shown = (orgs ?? []).filter((o) => !q || `${o.name} ${o.description ?? ''}`.toLowerCase().includes(q));
  useEffect(() => { document.title = 'Organisations — OverView'; }, []);
  const { full } = useSubscription();

  return (
    <>
      <div className="pagehead">
        <div>
          <h1>Organisations</h1>
          {orgs && <div className="sub">{orgs.length} organisation{orgs.length > 1 ? 's' : ''}</div>}
        </div>
        <QuotaHint kind="organizations" />
        <button className="primary" disabled={full('organizations')} title={full('organizations') ? 'Limite de ton abonnement atteinte : voir Mon abonnement' : undefined} onClick={() => setCreating(true)}>+ Nouvelle organisation</button>
      </div>
      {error && <div className="messagebar error">{error}</div>}

      {orgs && orgs.length > 6 && (
        <div className="filters">
          <input className="searchbox" type="search" placeholder="Rechercher une organisation" value={query}
            onChange={(e) => setQuery(e.target.value)} aria-label="Rechercher une organisation" />
        </div>
      )}

      {loading && <Loading lines={4} />}
      {orgs && orgs.length === 0 && (
        <div className="card empty-state">
          <div className="big" aria-hidden="true">🏢</div>
          <p>Aucune organisation pour le moment.</p>
          <button className="primary" onClick={() => setCreating(true)}>Créer la première organisation</button>
        </div>
      )}
      {orgs && orgs.length > 0 && shown.length === 0 && <div className="empty">Aucune organisation ne correspond à « {query} ».</div>}

      <div className="org-grid">
        {shown.map((o) => (
          <div className="org-card" key={o.id}>
            <span className="avatar-sm" aria-hidden="true">{initials(o.name)}</span>
            <Link href={`/organizations/${o.id}`} className="org-link">
              <span className="org-name ellipsis">{o.name}</span>
              <span className="sub ellipsis">{o.description || 'Aucune description'}</span>
            </Link>
            <Menu items={[{ label: 'Supprimer', danger: true, onClick: () => setToDelete(o) }]} />
          </div>
        ))}
      </div>

      {creating && (
        <Modal title="Nouvelle organisation" onClose={() => setCreating(false)}>
          <form onSubmit={onCreate} className="form" style={{ maxWidth: 'none' }}>
            <div className="field"><label htmlFor="org-name">Nom</label><input id="org-name" name="name" required autoFocus /></div>
            <div className="field"><label htmlFor="org-desc">Description (facultatif)</label><input id="org-desc" name="description" /></div>
            <div className="dialog-actions">
              <button type="button" onClick={() => setCreating(false)}>Annuler</button>
              <button type="submit" className="primary" disabled={create.isPending}>{create.isPending ? 'Création…' : 'Créer'}</button>
            </div>
          </form>
        </Modal>
      )}

      {toDelete && (
        <ConfirmDialog
          title="Supprimer l'organisation"
          requireText={toDelete.name}
          confirmLabel="Supprimer l'organisation"
          busy={remove.isPending}
          onConfirm={() => remove.mutate(toDelete, { onError: () => setToDelete(null) })}
          onCancel={() => setToDelete(null)}
        >
          <p>
            L&apos;organisation <strong>{toDelete.name}</strong> sera supprimée, ainsi que ses connecteurs, boîtes et
            règles enregistrés dans l&apos;app. Rien n&apos;est supprimé dans Microsoft 365. Action irréversible.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? '').join('') || '?';
}
