'use client';
import Link from 'next/link';
import { use, useCallback, useState, type FormEvent } from 'react';
import ConfirmDialog from '../../../components/ConfirmDialog';
import Menu from '../../../components/Menu';
import Modal from '../../../components/Modal';
import { api, errorMessage } from '../../../lib/api';
import type {
  AvailableMailbox, Connector, ConnectorCreate, ImportResult, Mailbox, Note, Organization, SyncResult,
} from '../../../lib/types';
import { useApi } from '../../../lib/useApi';
import ConnectorRow from './ConnectorRow';

type Dialog = 'connector' | 'import' | null;

const initials = (upn: string) => upn.split('@')[0].split(/[._-]/).slice(0, 2).map((p) => p[0]?.toUpperCase()).join('') || '?';

export default function OrganizationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const org = useApi<Organization>(`/organizations/${id}/`);
  const connectors = useApi<Connector[]>(`/connectors/?org_id=${id}`);
  const mailboxes = useApi<Mailbox[]>(`/outlook/mailboxes/?org_id=${id}`);
  const [note, setNote] = useState<Note | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const say = useCallback((text: string, error = false) => setNote(text ? { text, error } : null), []);

  async function run<T>(fn: () => Promise<T>, after?: (res: T) => void): Promise<void> {
    try {
      say('');
      const res = await fn();
      after?.(res);
    } catch (err) {
      say(errorMessage(err), true);
    }
  }

  // ---- Ajout d'un connecteur ----
  const addConnector = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body: ConnectorCreate = {
      org_id: id,
      name: String(f.get('name')),
      tenant_id: String(f.get('tenant_id')),
      client_id: String(f.get('client_id')),
      client_secret: String(f.get('client_secret')),
    };
    void run(async () => {
      await api<Connector>('/connectors/', { method: 'POST', body });
      setDialog(null);
      say(`Connecteur « ${body.name} » ajouté. Utilise « Tester la connexion » pour le valider.`);
      void connectors.reload();
    });
  };

  // ---- Import des boîtes depuis le tenant M365 ----
  const [connectorId, setConnectorId] = useState('');
  const [available, setAvailable] = useState<AvailableMailbox[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);

  const loadAvailable = async (cid: string) => {
    setConnectorId(cid);
    setAvailable(null);
    setSelected(new Set());
    if (!cid) return;
    setLoading(true);
    await run(() => api<AvailableMailbox[]>(`/outlook/connectors/${cid}/available-mailboxes/`), setAvailable);
    setLoading(false);
  };

  const openImport = () => {
    setDialog('import');
    const only = connectors.data?.length === 1 ? connectors.data[0].id : '';
    void loadAvailable(only);
  };

  const toggle = (upn: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(upn)) next.delete(upn);
      else next.add(upn);
      return next;
    });

  const importMailboxes = async (all: boolean) => {
    setImporting(true);
    await run(
      () => api<ImportResult>(`/outlook/connectors/${connectorId}/import-mailboxes/`, {
        method: 'POST', body: all ? {} : { user_principal_names: [...selected] },
      }),
      (r) => {
        setDialog(null);
        say(`${r.imported.length} boîte(s) importée(s), ${r.already_present} déjà présente(s).`);
        void mailboxes.reload();
      },
    );
    setImporting(false);
  };

  // ---- Boîtes ----
  const [mailboxToRemove, setMailboxToRemove] = useState<Mailbox | null>(null);

  const removeMailbox = async () => {
    const m = mailboxToRemove;
    if (!m) return;
    await run(() => api<null>(`/outlook/mailboxes/${m.id}/`, { method: 'DELETE' }), () => {
      say(`${m.user_principal_name} retirée.`);
      void mailboxes.reload();
    });
    setMailboxToRemove(null);
  };

  const syncMailbox = (m: Mailbox) =>
    run(() => api<SyncResult>(`/outlook/mailboxes/${m.id}/sync/`, { method: 'POST' }), (r) => {
      say(`${m.user_principal_name} : ${r.rules} règle(s) synchronisée(s).`);
      void mailboxes.reload();
    });

  const failure = connectors.error || mailboxes.error || org.error;
  const shown: Note | null = note ?? (failure ? { text: failure, error: true } : null);
  const nConnectors = connectors.data?.length ?? 0;
  const nMailboxes = mailboxes.data?.length ?? 0;

  return (
    <>
      <div className="breadcrumb"><Link href="/">Organisations</Link> › {org.data?.name ?? '…'}</div>
      <div className="pagehead" style={{ alignItems: 'flex-start' }}>
        <div>
          <h1>{org.data?.name ?? 'Organisation'}</h1>
          {org.data?.description && <div className="sub" style={{ marginTop: 2 }}>{org.data.description}</div>}
        </div>
        <div className="chips" style={{ marginTop: 4 }}>
          <span className="badge off">{nConnectors} connecteur{nConnectors > 1 ? 's' : ''}</span>
          <span className="badge off">{nMailboxes} boîte{nMailboxes > 1 ? 's' : ''}</span>
        </div>
      </div>
      {shown && <div className={`messagebar ${shown.error ? 'error' : ''}`}>{shown.text}</div>}

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Connecteurs Microsoft 365</h2>
            <div className="sub">Connexions au tenant : gouvernance, partage, sécurité des comptes.</div>
          </div>
          <button className="primary" onClick={() => setDialog('connector')}>Ajouter un connecteur</button>
        </div>
        {connectors.data && nConnectors === 0 && (
          <div className="empty-state"><div className="big">🔌</div>Aucun connecteur. Ajoute celui de ton tenant pour commencer.</div>
        )}
        {(connectors.data ?? []).map((c) => (
          <ConnectorRow key={c.id} connector={c} onMessage={say}
            onChanged={() => { void connectors.reload(); void mailboxes.reload(); }} />
        ))}
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Boîtes Outlook</h2>
            <div className="sub">Comptes surveillés : sécurité, appareils, sessions et règles.</div>
          </div>
          <button className="primary" disabled={nConnectors === 0} onClick={openImport}
            title={nConnectors === 0 ? 'Ajoute d’abord un connecteur' : ''}>Importer depuis Microsoft 365</button>
        </div>
        {mailboxes.data && nMailboxes === 0 && (
          <div className="empty-state"><div className="big">📬</div>Aucune boîte importée.</div>
        )}
        {(mailboxes.data ?? []).map((m) => (
          <div className="row" key={m.id}>
            <span className="avatar-sm" aria-hidden="true">{initials(m.user_principal_name)}</span>
            <div className="grow">
              <Link href={`/mailboxes/${m.id}`}><strong>{m.user_principal_name}</strong></Link>
              <div className="sub">
                {m.last_synced_at ? `Règles synchronisées le ${new Date(m.last_synced_at).toLocaleString()}` : 'Règles jamais synchronisées'}
              </div>
            </div>
            <Link href={`/mailboxes/${m.id}`} className="btn">Sécurité &amp; règles</Link>
            <Menu items={[
              { label: 'Synchroniser les règles', onClick: () => void syncMailbox(m) },
              { label: 'Retirer de l’app', danger: true, onClick: () => setMailboxToRemove(m) },
            ]} />
          </div>
        ))}
      </section>

      {dialog === 'connector' && (
        <Modal title="Ajouter un connecteur" onClose={() => setDialog(null)}>
          <form onSubmit={addConnector} className="form" style={{ maxWidth: 'none' }}>
            <input name="name" placeholder="Nom (ex. Cimtech.ca)" required autoFocus />
            <input name="tenant_id" placeholder="ID de l'annuaire (tenant)" required />
            <input name="client_id" placeholder="ID de l'application (client)" required />
            <input name="client_secret" type="password" placeholder="Secret client (valeur)" autoComplete="off" required />
            <div className="dialog-actions">
              <button type="button" onClick={() => setDialog(null)}>Annuler</button>
              <button type="submit" className="primary">Ajouter</button>
            </div>
          </form>
        </Modal>
      )}

      {dialog === 'import' && (
        <Modal title="Importer des boîtes" onClose={() => setDialog(null)} wide>
          <div className="form" style={{ maxWidth: 'none', marginBottom: 12 }}>
            <select value={connectorId} onChange={(e) => void loadAvailable(e.target.value)}>
              <option value="">Choisir un connecteur…</option>
              {(connectors.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          {loading && <div className="empty">Chargement des boîtes du tenant…</div>}
          {available && (
            <>
              <ul className="picklist">
                {available.map((m) => (
                  <li key={m.user_principal_name}>
                    <label>
                      <input type="checkbox" disabled={m.already_imported}
                        checked={m.already_imported || selected.has(m.user_principal_name)}
                        onChange={() => toggle(m.user_principal_name)} />
                      <span>{m.display_name} <span className="muted">&lt;{m.user_principal_name}&gt;</span></span>
                      {m.already_imported && <span className="badge off">Déjà importée</span>}
                    </label>
                  </li>
                ))}
              </ul>
              <div className="dialog-actions">
                <button disabled={importing} onClick={() => void importMailboxes(true)}>Tout importer</button>
                <button className="primary" disabled={importing || selected.size === 0} onClick={() => void importMailboxes(false)}>
                  Importer la sélection ({selected.size})
                </button>
              </div>
            </>
          )}
        </Modal>
      )}

      {mailboxToRemove && (
        <ConfirmDialog title="Retirer la boîte" confirmLabel="Retirer la boîte"
          onConfirm={() => void removeMailbox()} onCancel={() => setMailboxToRemove(null)}>
          <p>
            La boîte <strong>{mailboxToRemove.user_principal_name}</strong> sera retirée de l&apos;app, avec ses règles
            enregistrées ici. Rien n&apos;est supprimé dans Microsoft 365.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}
