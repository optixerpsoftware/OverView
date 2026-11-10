'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, type FormEvent } from 'react';
import ConfirmDialog from '../../../components/ConfirmDialog';
import Menu from '../../../components/Menu';
import { api, errorMessage } from '../../../lib/api';
import type { Connector, ConnectorTestResult } from '../../../lib/types';

interface Props {
  connector: Connector;
  onChanged: () => void;
  onMessage: (text: string, error?: boolean) => void;
}

// Ligne de connecteur : accès aux vues (gouvernance, partage) + menu d'actions (test, secret, suppression).
export default function ConnectorRow({ connector: c, onChanged, onMessage }: Props) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);

  async function test() {
    try {
      const r = await api<ConnectorTestResult>(`/connectors/${c.id}/test/`, { method: 'POST' });
      onMessage(r.ok ? `${c.name} : connexion réussie` : `${c.name} : ${r.error}`, !r.ok);
      onChanged();
    } catch (e) {
      onMessage(errorMessage(e), true);
    }
  }

  async function changeSecret(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const secret = String(new FormData(e.currentTarget).get('client_secret'));
    try {
      await api<Connector>(`/connectors/${c.id}/`, { method: 'PATCH', body: { client_secret: secret } });
      setEditing(false);
      onMessage(`${c.name} : secret mis à jour. Lance un test pour le valider.`);
      onChanged();
    } catch (err) {
      onMessage(errorMessage(err), true);
    }
  }

  async function remove() {
    try {
      await api<null>(`/connectors/${c.id}/`, { method: 'DELETE' });
      onMessage(`Connecteur « ${c.name} » supprimé`);
      onChanged();
    } catch (e) {
      onMessage(errorMessage(e), true);
    } finally {
      setConfirming(false);
    }
  }

  return (
    <div className="row" style={{ flexWrap: 'wrap' }}>
      <div className="grow">
        <strong>{c.name}</strong>{' '}
        {c.last_checked_at
          ? (c.last_error ? <span className="badge err" title={c.last_error}>Échec</span>
              : <span className="badge ok">Connecté</span>)
          : <span className="badge off">Non testé</span>}
        <div className="sub">Tenant {c.tenant_id}</div>
      </div>
      <Link href={`/compliance/${c.id}`} className="btn">Gouvernance</Link>
      <Link href={`/sharing/${c.id}`} className="btn">Partage</Link>
      <Link href={`/mailflow/${c.id}`} className="btn">Messagerie</Link>
      <Menu items={[
        { label: 'Tester la connexion', onClick: () => void test() },
        { label: 'Accès conditionnel', onClick: () => router.push(`/conditional-access/${c.id}`) },
        { label: 'Hygiène des identités', onClick: () => router.push(`/identity-hygiene/${c.id}`) },
        { label: 'Inscription des appareils', onClick: () => router.push(`/device-registration/${c.id}`) },
        { label: 'Changer le secret', onClick: () => setEditing(!editing) },
        { label: 'Supprimer', danger: true, onClick: () => setConfirming(true) },
      ]} />
      {editing && (
        <form onSubmit={changeSecret} className="form" style={{ width: '100%', gridTemplateColumns: '1fr auto', maxWidth: 'none' }}>
          <input name="client_secret" type="password" placeholder="Nouveau secret client (valeur)" autoComplete="off" required />
          <button type="submit" className="primary">Enregistrer</button>
        </form>
      )}
      {confirming && (
        <ConfirmDialog title="Supprimer le connecteur" confirmLabel="Supprimer le connecteur"
          onConfirm={() => void remove()} onCancel={() => setConfirming(false)}>
          <p>
            Le connecteur <strong>{c.name}</strong> sera supprimé, ainsi que ses boîtes importées et leurs règles
            enregistrées dans l&apos;app. Rien n&apos;est supprimé dans Microsoft 365. Action irréversible.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
