'use client';
import { useState } from 'react';
import { api } from '../lib/api';
import type { QuarantineAction, QuarantinedMessage, QuarantineResult } from '../lib/types';
import { useApi, useApiMutation, SLOW } from '../lib/useApi';
import ConfirmDialog from './ConfirmDialog';

const TYPES: [string, string][] = [
  ['', 'Tous les types'], ['Spam', 'Courrier indésirable'], ['Phish', 'Hameçonnage'], ['HighConfPhish', 'Hameçonnage (haute confiance)'],
  ['Malware', 'Logiciel malveillant'], ['Bulk', 'Envoi en masse'], ['TransportRule', 'Règle de transport'],
  ['DataLossPrevention', 'Protection contre la perte de données'],
];
const TYPE_LABEL = Object.fromEntries(TYPES.filter(([k]) => k));
const RISKY = new Set(['Malware', 'HighConfPhish']);
const WORD = 'LIBERER';

export default function Quarantine({ connectorId }: { connectorId: string }) {
  const base = `/outlook/connectors/${connectorId}/quarantine/`;
  const [days, setDays] = useState(7);
  const [type, setType] = useState('');
  const [query, setQuery] = useState('');
  const [target, setTarget] = useState<QuarantinedMessage | null>(null);
  const [showLog, setShowLog] = useState(false);

  const qs = new URLSearchParams({ days: String(days) });
  if (type) qs.set('type', type);
  const list = useApi<QuarantineResult>(`${base}?${qs}`, { staleTime: SLOW });
  const log = useApi<QuarantineAction[]>(`${base}actions/`, { enabled: showLog });

  const release = useApiMutation(
    (v: { identity: string; confirm: string }) =>
      api(`${base}release/`, { method: 'POST', body: { ...v, release_to_all: true } }),
    {
      invalidate: [base],
      success: 'Message libéré : il est remis aux destinataires.',
      onSuccess: () => setTarget(null),
    },
  );

  const r = list.data;
  const q = query.trim().toLowerCase();
  const shown = (r?.messages ?? []).filter((m) =>
    !q || `${m.sender} ${m.recipients.join(' ')} ${m.subject}`.toLowerCase().includes(q));
  const risky = target ? RISKY.has(target.type) : false;

  return (
    <>
      <div className="filters">
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Période">
          {[[1, '24 heures'], [7, '7 jours'], [14, '14 jours'], [30, '30 jours']].map(([d, l]) => <option key={d} value={d}>{l}</option>)}
        </select>
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
          {TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <input placeholder="Filtrer (expéditeur, destinataire, objet)" value={query} onChange={(e) => setQuery(e.target.value)} className="grow" />
        <button onClick={() => void list.reload()}>Actualiser</button>
        <button onClick={() => setShowLog(!showLog)}>{showLog ? 'Masquer le journal' : 'Journal des libérations'}</button>
      </div>

      {list.error && <div className="messagebar error">{list.error}</div>}
      {list.loading && <div className="sub">Interrogation d’Exchange Online…</div>}

      {showLog && (
        <table className="grid">
          <thead><tr><th>Date</th><th>Par</th><th>Message</th><th>Type</th><th>Résultat</th></tr></thead>
          <tbody>
            {(log.data ?? []).map((a) => (
              <tr key={a.id}>
                <td>{new Date(a.create_at).toLocaleString()}</td><td>{a.performed_by}</td><td>{a.subject || a.identity}</td>
                <td>{TYPE_LABEL[a.quarantine_type] ?? a.quarantine_type}</td>
                <td><span className={`badge ${a.ok ? 'ok' : 'err'}`} title={a.detail}>{a.ok ? 'Libéré' : 'Échec'}</span></td>
              </tr>
            ))}
            {log.data?.length === 0 && <tr><td colSpan={5} className="sub">Aucune libération enregistrée.</td></tr>}
          </tbody>
        </table>
      )}

      {r && (
        <>
          <div className="tiles">
            <div className="tile static"><span className="tile-label">En quarantaine</span><span className="tile-value">{r.summary.total}</span>
              <span className="tile-hint">sur la période</span></div>
            <div className="tile static"><span className="tile-label">À traiter</span><span className="tile-value">{r.summary.pending_release}</span>
              <span className="tile-hint">pas encore libérés</span></div>
            <div className={`tile static ${r.summary.risky ? 'warn' : ''}`}><span className="tile-label">À risque</span>
              <span className="tile-value">{r.summary.risky}</span><span className="tile-hint">malveillant / hameçonnage</span></div>
          </div>
          {r.truncated && <div className="messagebar">Limité aux 500 messages les plus récents ; filtre par type ou période.</div>}
          <table className="grid">
            <thead><tr><th>Reçu</th><th>Expéditeur</th><th>Destinataires</th><th>Objet</th><th>Type</th><th>Expire</th><th /></tr></thead>
            <tbody>
              {shown.map((m) => (
                <tr key={m.identity}>
                  <td>{m.received ? new Date(m.received).toLocaleString() : '—'}</td>
                  <td>{m.sender}</td>
                  <td>{m.recipients.slice(0, 2).join(', ')}{m.recipients.length > 2 ? ` +${m.recipients.length - 2}` : ''}</td>
                  <td>{m.subject || '(sans objet)'}</td>
                  <td><span className={`badge ${RISKY.has(m.type) ? 'err' : 'warn'}`}>{TYPE_LABEL[m.type] ?? (m.type || '?')}</span></td>
                  <td>{m.expires ? new Date(m.expires).toLocaleDateString() : '—'}</td>
                  <td>
                    {m.released
                      ? <span className="badge ok">Libéré</span>
                      : <button onClick={() => setTarget(m)}>Distribuer</button>}
                  </td>
                </tr>
              ))}
              {shown.length === 0 && <tr><td colSpan={7} className="sub">Aucun message en quarantaine.</td></tr>}
            </tbody>
          </table>
        </>
      )}

      {target && (
        <ConfirmDialog
          title="Distribuer le message en quarantaine"
          confirmLabel="Distribuer"
          danger={risky}
          busy={release.isPending}
          requireText={risky ? WORD : undefined}
          onCancel={() => setTarget(null)}
          onConfirm={() => release.mutate({ identity: target.identity, confirm: risky ? WORD : 'OUI' })}
        >
          <p>
            Le message « <strong>{target.subject || '(sans objet)'}</strong> » de {target.sender} sera remis à ses destinataires
            ({target.recipients.join(', ') || 'inconnus'}). L’action est journalisée.
          </p>
          {risky && (
            <p style={{ color: 'var(--danger)' }}>
              Exchange l’a classé « {TYPE_LABEL[target.type] ?? target.type} ». Le distribuer expose les destinataires à un risque réel :
              retape <strong>{WORD}</strong> pour confirmer.
            </p>
          )}
        </ConfirmDialog>
      )}
    </>
  );
}
