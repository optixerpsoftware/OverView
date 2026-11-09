'use client';
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { api, errorMessage } from '../lib/api';
import Modal from './Modal';

interface DirUser { id: string; name: string; upn: string }
export interface CreatedGroup { id: string; name: string }

const MAX = 20;

// Crée un groupe de sécurité Entra ID (ex. « TI – jonction d'appareils ») avec ses premiers membres.
export default function CreateGroupModal({ connectorId, onCreated, onClose }: {
  connectorId: string; onCreated: (g: CreatedGroup) => void; onClose: () => void;
}) {
  const [users, setUsers] = useState<DirUser[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<DirUser[]>(`/identity/connectors/${connectorId}/directory-users/`)
      .then(setUsers)
      .catch((e) => { setUsers([]); setError(errorMessage(e)); });
  }, [connectorId]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (users ?? []).filter((u) => !q || `${u.name} ${u.upn}`.toLowerCase().includes(q));
  }, [users, query]);

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < MAX) next.add(id);
      return next;
    });

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const name = String(new FormData(e.currentTarget).get('name') ?? '');
    setBusy(true);
    setError('');
    try {
      const g = await api<CreatedGroup>(`/identity/connectors/${connectorId}/groups/`, {
        method: 'POST', body: { name, member_ids: [...picked] },
      });
      onCreated(g);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Modal title="Créer un groupe de sécurité" onClose={onClose} wide>
      <form onSubmit={submit} className="form" style={{ maxWidth: 'none' }}>
        <input name="name" defaultValue="TI - jonction d'appareils" minLength={3} maxLength={64} required autoFocus />
        <div className="sub">
          Choisis les membres (1 à {MAX}) : ce sont les seuls comptes qui pourront joindre un PC au tenant. Tu pourras en ajouter d’autres dans Entra.
          Inclus ton propre compte pour ne pas te bloquer.
        </div>
        <input placeholder="Filtrer les comptes…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <ul className="picklist">
          {!users && <li className="muted">Chargement des comptes…</li>}
          {users && shown.length === 0 && <li className="muted">Aucun compte.</li>}
          {shown.map((u) => (
            <li key={u.id}>
              <label>
                <input type="checkbox" checked={picked.has(u.id)} onChange={() => toggle(u.id)}
                  disabled={!picked.has(u.id) && picked.size >= MAX} />
                <span>{u.name} <span className="muted">&lt;{u.upn}&gt;</span></span>
              </label>
            </li>
          ))}
        </ul>
        {error && <div className="messagebar error">{error}</div>}
        <div className="dialog-actions">
          <span className="sub" style={{ marginRight: 'auto' }}>{picked.size} / {MAX} membre{picked.size > 1 ? 's' : ''}</span>
          <button type="button" onClick={onClose}>Annuler</button>
          <button type="submit" className="primary" disabled={busy || picked.size === 0}>{busy ? 'Création…' : 'Créer le groupe'}</button>
        </div>
      </form>
    </Modal>
  );
}
