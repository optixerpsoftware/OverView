'use client';
import { useMemo, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import type { Note, Risk, ShareRecord, SharingScan, SharingSummary } from '../lib/types';
import { useApi } from '../lib/useApi';
import ConfirmDialog from './ConfirmDialog';
import { RISK_LABEL, RISK_ORDER, shareLabel } from './ShareLabel';

const PAGE = 100;

export default function SharingAudit({ connectorId }: { connectorId: string }) {
  const base = `/onedrive/connectors/${connectorId}`;
  const latest = useApi<SharingScan | null>(`${base}/sharing/latest/`, {
    // Tant qu'un audit tourne dans le worker, on interroge son état toutes les 4 s.
    refetchInterval: (d) => (d?.status === 'running' ? 4000 : false),
  });
  const [note, setNote] = useState<Note | null>(null);
  const [risk, setRisk] = useState<Risk | 'all'>('all');
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(PAGE);
  const [starting, setStarting] = useState(false);
  const [toRevoke, setToRevoke] = useState<ShareRecord | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [removed, setRemoved] = useState<Set<string>>(new Set());

  const scan = latest.data;
  const running = scan?.status === 'running';

  async function start() {
    setStarting(true);
    setNote(null);
    try {
      await api(`${base}/sharing/scan/`, { method: 'POST' });
      await latest.reload();
    } catch (e) {
      setNote({ text: errorMessage(e), error: true });
    } finally {
      setStarting(false);
    }
  }

  const key = (r: ShareRecord) => `${r.drive_id}|${r.item_id}|${r.permission_id}`;

  async function revoke() {
    if (!toRevoke) return;
    const r = toRevoke;
    setRevoking(true);
    try {
      await api<null>(
        `${base}/drives/${encodeURIComponent(r.drive_id)}/items/${encodeURIComponent(r.item_id)}/permissions/${encodeURIComponent(r.permission_id)}/`,
        { method: 'DELETE' },
      );
      setRemoved((prev) => new Set(prev).add(key(r)));
      setNote({ text: `Partage supprimé sur « ${r.name} ».`, error: false });
    } catch (e) {
      setNote({ text: errorMessage(e), error: true });
    } finally {
      setRevoking(false);
      setToRevoke(null);
    }
  }

  const records = useMemo(() => (scan?.records ?? []).filter((r) => !removed.has(key(r))), [scan, removed]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return records.filter((r) =>
      (risk === 'all' || r.risk === risk) &&
      (!q || `${r.name} ${r.path} ${r.drive_label} ${r.grantees.join(' ')}`.toLowerCase().includes(q)));
  }, [records, risk, query]);

  const s = scan?.summary as SharingSummary | undefined;
  const counts = RISK_ORDER.reduce((acc, k) => ({ ...acc, [k]: records.filter((r) => r.risk === k).length }), {} as Record<Risk, number>);
  const anonymous = records.filter((r) => r.scope === 'anonymous').length;
  const anonEdit = records.filter((r) => r.scope === 'anonymous' && (r.link_type === 'edit' || r.roles.includes('write'))).length;
  const org = records.filter((r) => r.scope === 'organization').length;
  const external = records.filter((r) => r.external).length;

  return (
    <>
      <div className="line" style={{ justifyContent: 'space-between', padding: '4px 0 12px' }}>
        <div className="sub">
          {scan?.status === 'done' && scan.finished_at
            ? `Dernier audit : ${new Date(scan.finished_at).toLocaleString()} · ${s?.total ?? 0} partage(s)`
            : running ? 'Audit en cours…' : scan?.status === 'failed' ? 'Dernier audit en échec' : 'Aucun audit pour ce tenant.'}
        </div>
        <button className="primary" disabled={starting || running} onClick={start}>
          {running ? 'Audit en cours…' : scan ? 'Relancer l’audit' : 'Lancer l’audit'}
        </button>
      </div>

      {note && <div className={`messagebar ${note.error ? 'error' : ''}`}>{note.text}</div>}
      {latest.error && <div className="messagebar error">{latest.error}</div>}
      {running && (
        <div className="messagebar">
          L&apos;audit parcourt tous les OneDrive et bibliothèques SharePoint ; cela peut prendre quelques minutes. Tu peux quitter la page : il continue dans le worker.
        </div>
      )}
      {scan?.status === 'failed' && <div className="messagebar error">Échec de l&apos;audit : {scan.error}</div>}
      {scan?.status === 'done' && scan.errors.length > 0 && (
        <div className="messagebar error">
          Certains espaces n&apos;ont pas pu être analysés (résultat incomplet) :
          <ul style={{ margin: '4px 0 0 18px' }}>{scan.errors.slice(0, 5).map((e, i) => <li key={i}>{e}</li>)}</ul>
        </div>
      )}
      {scan?.status === 'done' && scan.truncated.length > 0 && (
        <div className="messagebar">Parcours limité pour : {scan.truncated.join(', ')}.</div>
      )}

      {scan?.status === 'done' && (
        <>
          <div className="tiles">
            <div className={`tile static ${anonymous ? 'warn' : ''}`}>
              <span className="tile-label">Liens anonymes</span><span className="tile-value">{anonymous}</span>
              <span className="tile-hint">« Tout le monde » avec le lien</span>
            </div>
            <div className={`tile static ${anonEdit ? 'warn' : ''}`}>
              <span className="tile-label">Anonymes modifiables</span><span className="tile-value">{anonEdit}</span>
              <span className="tile-hint">édition sans connexion</span>
            </div>
            <div className="tile static">
              <span className="tile-label">Toute l&apos;organisation</span><span className="tile-value">{org}</span>
              <span className="tile-hint">liens internes</span>
            </div>
            <div className={`tile static ${external ? 'warn' : ''}`}>
              <span className="tile-label">Partages externes</span><span className="tile-value">{external}</span>
              <span className="tile-hint">invités / hors domaine</span>
            </div>
          </div>

          <nav className="tabs" role="tablist">
            {(['all', ...RISK_ORDER] as (Risk | 'all')[]).map((k) => (
              <button key={k} role="tab" aria-selected={risk === k} className={`tab ${risk === k ? 'on' : ''}`}
                onClick={() => { setRisk(k); setLimit(PAGE); }}>
                {k === 'all' ? 'Tous' : RISK_LABEL[k].label}
                <span className="count">{k === 'all' ? records.length : counts[k]}</span>
              </button>
            ))}
          </nav>
          <section className="panel">
            <div style={{ padding: '12px 0 4px' }}>
              <input placeholder="Filtrer par nom, chemin, espace ou destinataire…" value={query}
                onChange={(e) => { setQuery(e.target.value); setLimit(PAGE); }} />
            </div>
            {shown.length === 0 ? <div className="empty">Aucun partage ne correspond.</div> : (
              <table className="grid">
                <thead><tr><th>Risque</th><th>Élément</th><th>Partage</th><th /></tr></thead>
                <tbody>
                  {shown.slice(0, limit).map((r) => (
                    <tr key={key(r)}>
                      <td><span className={`badge ${RISK_LABEL[r.risk].cls}`}>{RISK_LABEL[r.risk].label}</span></td>
                      <td style={{ maxWidth: 360, wordBreak: 'break-word' }}>
                        {r.is_folder ? '📁 ' : '📄 '}<strong>{r.name}</strong>
                        <div className="sub">{r.drive_label.replace('OneDrive — ', 'OneDrive · ')}{r.path && ` · ${r.path}`}</div>
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>{shareLabel(r)}
                        <div className="sub">
                          {r.expires_at ? `expire le ${new Date(r.expires_at).toLocaleDateString()}` : r.kind === 'link' ? 'sans expiration' : ''}
                          {r.has_password ? ' · mot de passe' : ''}
                          {r.kind === 'link' && !r.link_url ? ' · URL non exposée par Microsoft' : ''}
                        </div>
                        {r.grantees.length > 0 && <div className="sub">{r.grantees.join(', ')}</div>}
                      </td>
                      <td align="right" style={{ whiteSpace: 'nowrap' }}>
                        {r.web_url && <a className="btn" href={r.web_url} target="_blank" rel="noreferrer">Ouvrir</a>}{' '}
                        <button className="ghost danger" onClick={() => setToRevoke(r)}>Révoquer</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {shown.length > limit && (
              <div style={{ padding: '12px 0 0' }}>
                <button onClick={() => setLimit(limit + PAGE)}>Afficher {Math.min(PAGE, shown.length - limit)} de plus ({shown.length - limit} restants)</button>
              </div>
            )}
          </section>
        </>
      )}

      {toRevoke && (
        <ConfirmDialog
          title="Révoquer ce partage"
          confirmLabel="Révoquer le partage"
          busy={revoking}
          onConfirm={() => void revoke()}
          onCancel={() => setToRevoke(null)}
        >
          <p>
            <strong>{shareLabel(toRevoke)}</strong> sur <strong>{toRevoke.name}</strong> ({toRevoke.drive_label}) sera
            supprimé <strong>dans Microsoft 365</strong>. Toute personne qui utilisait ce lien perdra l&apos;accès. Les
            autres partages du fichier ne sont pas touchés. Action irréversible.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}
