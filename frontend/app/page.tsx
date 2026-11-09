'use client';
import Link from 'next/link';
import { useState, type FormEvent } from 'react';
import ConfirmDialog from '../components/ConfirmDialog';
import { api } from '../lib/api';
import type { Organization } from '../lib/types';
import { useApi, useApiMutation } from '../lib/useApi';

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
      { onSuccess: () => form.reset() },
    );
  }

  return (
    <>
      <div className="pagehead"><h1>Organisations</h1></div>
      {error && <div className="messagebar error">{error}</div>}

      <section className="card">
        <h2>Vos organisations</h2>
        {loading && <div className="empty">Chargement…</div>}
        {orgs && orgs.length === 0 && <div className="empty">Aucune organisation pour le moment.</div>}
        {(orgs ?? []).map((o) => (
          <div className="row" key={o.id}>
            <div className="grow">
              <Link href={`/organizations/${o.id}`}><strong>{o.name}</strong></Link>
              {o.description && <div className="sub">{o.description}</div>}
            </div>
            <button className="danger" onClick={() => setToDelete(o)}>Supprimer</button>
          </div>
        ))}
      </section>

      <section className="card">
        <h2>Nouvelle organisation</h2>
        <form onSubmit={onCreate} className="form">
          <input name="name" placeholder="Nom" required />
          <input name="description" placeholder="Description (facultatif)" />
          <div><button type="submit" className="primary" disabled={create.isPending}>{create.isPending ? 'Création…' : 'Créer'}</button></div>
        </form>
      </section>

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
