'use client';
import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import type { Drive, DriveItem, Note, ShareEntry } from '../lib/types';
import { SLOW, useApi } from '../lib/useApi';
import ConfirmDialog from './ConfirmDialog';
import { RISK_LABEL, shareLabel } from './ShareLabel';
import Loading from './Loading';

const fmtSize = (n: number) => (n > 1e9 ? `${(n / 1e9).toFixed(1)} Go` : n > 1e6 ? `${(n / 1e6).toFixed(1)} Mo` : n > 1e3 ? `${Math.round(n / 1e3)} Ko` : `${n} o`);

interface Crumb { id: string | null; name: string }

export default function DriveExplorer({ connectorId }: { connectorId: string }) {
  const base = `/onedrive/connectors/${connectorId}`;
  const drives = useApi<Drive[]>(`${base}/drives/`, { staleTime: SLOW });
  const [driveId, setDriveId] = useState('');
  const [trail, setTrail] = useState<Crumb[]>([{ id: null, name: 'Racine' }]);
  const [items, setItems] = useState<DriveItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState<Note | null>(null);
  const [open, setOpen] = useState<string | null>(null);        // élément dont on voit les partages
  const [perms, setPerms] = useState<ShareEntry[] | null>(null);
  const [toRevoke, setToRevoke] = useState<{ item: DriveItem; perm: ShareEntry } | null>(null);
  const [revoking, setRevoking] = useState(false);

  const parent = trail[trail.length - 1].id;

  const load = useCallback(async () => {
    if (!driveId) return;
    setLoading(true);
    setItems(null);
    setOpen(null);
    try {
      const q = parent ? `?parent=${encodeURIComponent(parent)}` : '';
      setItems(await api<DriveItem[]>(`${base}/drives/${encodeURIComponent(driveId)}/items/${q}`));
      setNote(null);
    } catch (e) {
      setNote({ text: errorMessage(e), error: true });
    } finally {
      setLoading(false);
    }
  }, [base, driveId, parent]);

  useEffect(() => { void load(); }, [load]);

  function pickDrive(id: string) {
    setDriveId(id);
    setTrail([{ id: null, name: 'Racine' }]);
  }

  async function showShares(item: DriveItem) {
    if (open === item.id) { setOpen(null); return; }
    setOpen(item.id);
    setPerms(null);
    try {
      setPerms(await api<ShareEntry[]>(`${base}/drives/${encodeURIComponent(driveId)}/items/${encodeURIComponent(item.id)}/permissions/`));
    } catch (e) {
      setNote({ text: errorMessage(e), error: true });
      setOpen(null);
    }
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setNote({ text: 'Lien copié dans le presse-papiers.', error: false });
    } catch {
      setNote({ text: 'Copie impossible : ouvre le lien et copie-le depuis la barre d’adresse.', error: true });
    }
  }

  async function revoke() {
    if (!toRevoke) return;
    const { item, perm } = toRevoke;
    setRevoking(true);
    try {
      await api<null>(`${base}/drives/${encodeURIComponent(driveId)}/items/${encodeURIComponent(item.id)}/permissions/${encodeURIComponent(perm.permission_id)}/`, { method: 'DELETE' });
      setNote({ text: `Partage supprimé sur « ${item.name} ».`, error: false });
      setPerms((p) => (p ? p.filter((x) => x.permission_id !== perm.permission_id) : p));
    } catch (e) {
      setNote({ text: errorMessage(e), error: true });
    } finally {
      setRevoking(false);
      setToRevoke(null);
    }
  }

  const grouped = {
    onedrive: (drives.data ?? []).filter((d) => d.kind === 'onedrive'),
    sharepoint: (drives.data ?? []).filter((d) => d.kind === 'sharepoint'),
  };

  return (
    <>
      <div style={{ padding: '4px 0 12px', maxWidth: 520 }}>
        <select value={driveId} onChange={(e) => pickDrive(e.target.value)}>
          <option value="">{drives.data ? 'Choisir un OneDrive ou une bibliothèque SharePoint…' : 'Chargement des espaces…'}</option>
          <optgroup label="OneDrive">{grouped.onedrive.map((d) => <option key={d.id} value={d.id}>{d.label.replace('OneDrive — ', '')}</option>)}</optgroup>
          <optgroup label="SharePoint">{grouped.sharepoint.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}</optgroup>
        </select>
      </div>

      {(note || drives.error) && <div className={`messagebar ${note?.error || drives.error ? 'error' : ''}`}>{note?.text ?? drives.error}</div>}

      {driveId && (
        <section className="panel" style={{ borderTop: '1px solid var(--border)', borderRadius: 10 }}>
          <div className="breadcrumb" style={{ margin: '12px 0 4px' }}>
            {trail.map((c, i) => (
              <span key={i}>
                {i > 0 && ' › '}
                {i < trail.length - 1
                  ? <a href="#" onClick={(e) => { e.preventDefault(); setTrail(trail.slice(0, i + 1)); }}>{c.name}</a>
                  : <strong>{c.name}</strong>}
              </span>
            ))}
          </div>
          {loading && <Loading />}
          {items && items.length === 0 && <div className="empty">Dossier vide.</div>}
          {items && items.length > 0 && (
            <table className="grid">
              <thead><tr><th>Nom</th><th>Modifié</th><th>Taille</th><th /></tr></thead>
              <tbody>
                {items.map((it) => (
                  <>
                    <tr key={it.id}>
                      <td>
                        {it.is_folder ? (
                          <a href="#" onClick={(e) => { e.preventDefault(); setTrail([...trail, { id: it.id, name: it.name }]); }}>📁 <strong>{it.name}</strong></a>
                        ) : <>📄 {it.name}</>}
                        {it.shared && <> <span className="badge off">Partagé</span></>}
                      </td>
                      <td className="sub">{it.modified_at ? new Date(it.modified_at).toLocaleDateString() : '—'}</td>
                      <td className="sub">{it.is_folder ? `${it.child_count ?? 0} élément(s)` : fmtSize(it.size)}</td>
                      <td align="right" style={{ whiteSpace: 'nowrap' }}>
                        {it.web_url && <a className="btn" href={it.web_url} target="_blank" rel="noreferrer">Ouvrir</a>}{' '}
                        {it.web_url && <button onClick={() => void copy(it.web_url)}>Copier le lien</button>}{' '}
                        <button onClick={() => void showShares(it)}>{open === it.id ? 'Masquer' : 'Partages'}</button>
                      </td>
                    </tr>
                    {open === it.id && (
                      <tr key={`${it.id}-p`}>
                        <td colSpan={4} style={{ background: 'var(--subtle)' }}>
                          {!perms && <Loading lines={2} />}
                          {perms && perms.length === 0 && <div className="empty">Aucun partage (seul le propriétaire a accès).</div>}
                          {perms?.map((p) => (
                            <div className="line" key={p.permission_id}>
                              <span className={`badge ${RISK_LABEL[p.risk].cls}`}>{RISK_LABEL[p.risk].label}</span>
                              <div className="grow">{shareLabel(p)}{p.inherited && <span className="sub"> · hérité du dossier parent</span>}
                                <div className="sub">{p.grantees.join(', ') || (p.expires_at ? `expire le ${new Date(p.expires_at).toLocaleDateString()}` : p.kind === 'link' ? 'sans expiration' : '')}</div>
                              </div>
                              {p.link_url && <button onClick={() => void copy(p.link_url)}>Copier</button>}
                              <button className="ghost danger" disabled={p.inherited} title={p.inherited ? 'À révoquer sur le dossier parent' : ''}
                                onClick={() => setToRevoke({ item: it, perm: p })}>Révoquer</button>
                            </div>
                          ))}
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {toRevoke && (
        <ConfirmDialog title="Révoquer ce partage" confirmLabel="Révoquer le partage" busy={revoking}
          onConfirm={() => void revoke()} onCancel={() => setToRevoke(null)}>
          <p><strong>{shareLabel(toRevoke.perm)}</strong> sur <strong>{toRevoke.item.name}</strong> sera supprimé
            <strong> dans Microsoft 365</strong>. Toute personne qui utilisait ce lien perdra l&apos;accès. Action irréversible.</p>
        </ConfirmDialog>
      )}
    </>
  );
}
