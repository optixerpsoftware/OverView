'use client';
import { useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import type { BulkResult, Note, Risk, ShareRecord, SharingScan, SharingSummary } from '../lib/types';
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
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);

  // Lien depuis la recherche globale : ?q=nom du fichier pré-remplit le filtre.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get('q');
    if (q) setQuery(q);
  }, []);

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

  async function revokeSelected() {
    const chosen = records.filter((r) => selected.has(key(r)));
    setBulkBusy(true);
    try {
      const res = await api<BulkResult>('/insights/bulk/sharing/revoke/', {
        method: 'POST',
        body: { connector_id: connectorId, confirm: 'REVOQUER',
          items: chosen.map((r) => ({ drive_id: r.drive_id, item_id: r.item_id, permission_id: r.permission_id })) },
      });
      const ok = new Set(res.results.filter((x) => x.ok).map((x) => x.id));
      setRemoved((prev) => { const n = new Set(prev); chosen.forEach((r) => { if (ok.has(key(r))) n.add(key(r)); }); return n; });
      setSelected(new Set());
      const failures = res.results.filter((x) => !x.ok).map((x) => x.error).slice(0, 2).join(' ; ');
      setNote({ text: `${res.succeeded} partage(s) révoqué(s)${res.failed ? `, ${res.failed} en échec : ${failures}` : ''}.`, error: res.failed > 0 });
    } catch (e) {
      setNote({ text: errorMessage(e), error: true });
    } finally {
      setBulkBusy(false);
      setBulkOpen(false);
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
          {!!scan?.progress?.drives_total && (
            <div style={{ marginTop: 8 }}>
              <progress value={scan.progress.drives_done ?? 0} max={scan.progress.drives_total} style={{ width: '100%' }} />
              <div className="sub">
                {scan.progress.drives_done ?? 0} / {scan.progress.drives_total} espaces analysés · {scan.progress.records ?? 0} partage(s) trouvé(s) jusqu&apos;ici
              </div>
            </div>
          )}
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
            {selected.size > 0 && (
              <div className="bulkbar" role="region" aria-label="Actions sur la sélection">
                <strong>{selected.size} partage(s) sélectionné(s)</strong>
                <button className="danger-solid" onClick={() => setBulkOpen(true)}>Révoquer la sélection</button>
                <button className="ghost" onClick={() => setSelected(new Set())}>Annuler la sélection</button>
              </div>
            )}
            {shown.length === 0 ? <div className="empty">Aucun partage ne correspond.</div> : (
              <table className="grid">
                <thead><tr>
                  <th style={{ width: 32 }}>
                    <input type="checkbox" aria-label="Sélectionner les partages affichés (100 au maximum)"
                      checked={shown.slice(0, limit).length > 0 && shown.slice(0, Math.min(limit, 100)).every((r) => selected.has(key(r)))}
                      onChange={(e) => setSelected(() => {
                        const n = new Set<string>();
                        if (e.target.checked) shown.slice(0, Math.min(limit, 100)).forEach((r) => n.add(key(r)));
                        return n;
                      })} />
                  </th>
                  <th>Risque</th><th>Élément</th><th>Partage</th><th />
                </tr></thead>
                <tbody>
                  {shown.slice(0, limit).map((r) => (
                    <tr key={key(r)}>
                      <td><input type="checkbox" aria-label={`Sélectionner ${r.name}`} checked={selected.has(key(r))}
                        onChange={() => setSelected((prev) => {
                          const n = new Set(prev);
                          if (n.has(key(r))) n.delete(key(r)); else if (n.size < 100) n.add(key(r));
                          return n;
                        })} /></td>
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

      {bulkOpen && (
        <ConfirmDialog
          title={`Révoquer ${selected.size} partage(s)`}
          confirmLabel="Révoquer la sélection"
          requireText="REVOQUER"
          busy={bulkBusy}
          onConfirm={() => void revokeSelected()}
          onCancel={() => setBulkOpen(false)}
        >
          <p>
            Les <strong>{selected.size}</strong> partages sélectionnés seront supprimés <strong>dans Microsoft 365</strong>.
            Les personnes qui utilisaient ces liens perdront l&apos;accès. Chaque suppression est revérifiée et journalisée ;
            les partages hérités ou du propriétaire sont refusés. Action irréversible.
          </p>
        </ConfirmDialog>
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
