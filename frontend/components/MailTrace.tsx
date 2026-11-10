'use client';
import { useState } from 'react';
import type { TraceResult, TraceState } from '../lib/types';
import { useApi, SLOW } from '../lib/useApi';
import Loading from './Loading';

const STATE: Record<TraceState, { label: string; cls: string; color: string }> = {
  delivered: { label: 'Remis', cls: 'ok', color: 'var(--success)' },
  failed: { label: 'Échec', cls: 'err', color: 'var(--danger)' },
  quarantined: { label: 'Quarantaine', cls: 'warn', color: 'var(--warning)' },
  spam: { label: 'Courrier indésirable', cls: 'warn', color: '#c19c00' },
  pending: { label: 'En cours', cls: 'off', color: '#8a8886' },
  expanded: { label: 'Développé', cls: 'off', color: '#b3b0ad' },
  other: { label: 'Autre', cls: 'off', color: '#b3b0ad' },
};
const ORDER: TraceState[] = ['delivered', 'failed', 'quarantined', 'spam', 'pending', 'expanded', 'other'];
const STATUS_FILTER: [string, string][] = [
  ['', 'Tous les statuts'], ['Delivered', 'Remis'], ['Failed', 'Échec'], ['Quarantined', 'Quarantaine'],
  ['FilteredAsSpam', 'Indésirable'], ['Pending', 'En cours'], ['Expanded', 'Développé'],
];
const PERIODS: [number, string][] = [[6, '6 heures'], [24, '24 heures'], [72, '3 jours'], [168, '7 jours'], [240, '10 jours']];
const PAGE = 100;

const stateOf = (status: string): TraceState => {
  const s = status.toLowerCase();
  return s === 'delivered' ? 'delivered' : s === 'failed' ? 'failed' : s === 'quarantined' ? 'quarantined'
    : s === 'filteredasspam' ? 'spam' : s === 'pending' || s === 'gettingstatus' ? 'pending' : s === 'expanded' || s === 'resolved' ? 'expanded' : 'other';
};

function HourlyChart({ data }: { data: TraceResult['summary']['per_hour'] }) {
  const totals = data.map((h) => ORDER.reduce((n, k) => n + (h[k] ?? 0), 0));
  const max = Math.max(1, ...totals);
  const W = 720, H = 120, bw = W / Math.max(data.length, 1);
  return (
    <svg viewBox={`0 0 ${W} ${H + 18}`} role="img" aria-label="Messages par heure" style={{ width: '100%', height: 150 }}>
      {data.map((h, i) => {
        let y = H;
        return (
          <g key={h.hour}>
            <title>{`${new Date(h.hour).toLocaleString()} : ${totals[i]} message(s)`}</title>
            {ORDER.map((k) => {
              const n = h[k] ?? 0;
              if (!n) return null;
              const bh = (n / max) * H;
              y -= bh;
              return <rect key={k} x={i * bw + 1} y={y} width={Math.max(bw - 2, 1)} height={bh} fill={STATE[k].color} />;
            })}
          </g>
        );
      })}
      <line x1="0" y1={H} x2={W} y2={H} stroke="var(--border)" />
      <text x="0" y={H + 13} fontSize="10" fill="var(--text-2)">{data.length ? new Date(data[0].hour).toLocaleString() : ''}</text>
      <text x={W} y={H + 13} fontSize="10" fill="var(--text-2)" textAnchor="end">
        {data.length ? new Date(data[data.length - 1].hour).toLocaleString() : ''}
      </text>
    </svg>
  );
}

export default function MailTrace({ connectorId }: { connectorId: string }) {
  const [hours, setHours] = useState(24);
  const [status, setStatus] = useState('');
  const [sender, setSender] = useState('');
  const [recipient, setRecipient] = useState('');
  const [applied, setApplied] = useState({ sender: '', recipient: '' });
  const [limit, setLimit] = useState(PAGE);

  const qs = new URLSearchParams({ hours: String(hours) });
  if (status) qs.set('status', status);
  if (applied.sender) qs.set('sender', applied.sender);
  if (applied.recipient) qs.set('recipient', applied.recipient);
  const trace = useApi<TraceResult>(`/outlook/connectors/${connectorId}/mailflow/trace/?${qs}`, { staleTime: SLOW });
  const r = trace.data;
  const s = r?.summary;

  return (
    <>
      <form className="filters"
        onSubmit={(e) => { e.preventDefault(); setApplied({ sender: sender.trim(), recipient: recipient.trim() }); setLimit(PAGE); }}>
        <select value={hours} onChange={(e) => { setHours(Number(e.target.value)); setLimit(PAGE); }} aria-label="Période">
          {PERIODS.map(([h, l]) => <option key={h} value={h}>{l}</option>)}
        </select>
        <select value={status} onChange={(e) => { setStatus(e.target.value); setLimit(PAGE); }} aria-label="Statut">
          {STATUS_FILTER.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <input placeholder="Expéditeur (adresse)" value={sender} onChange={(e) => setSender(e.target.value)} type="email" />
        <input placeholder="Destinataire (adresse)" value={recipient} onChange={(e) => setRecipient(e.target.value)} type="email" />
        <button type="submit" className="primary">Rechercher</button>
        <button type="button" onClick={() => void trace.reload()}>Actualiser</button>
      </form>

      {trace.error && <div className="messagebar error">{trace.error}</div>}
      {trace.loading && <Loading label="Interrogation d’Exchange Online…" lines={5} />}

      {s && r && (
        <>
          <div className="tiles">
            <div className="tile static"><span className="tile-label">Messages</span><span className="tile-value">{s.total}</span>
              <span className="tile-hint">sur la période</span></div>
            <div className="tile static"><span className="tile-label">Remis</span><span className="tile-value">{s.by_state.delivered ?? 0}</span>
              <span className="tile-hint">livrés aux destinataires</span></div>
            <div className={`tile static ${s.by_state.failed ? 'warn' : ''}`}><span className="tile-label">Échecs</span>
              <span className="tile-value">{s.by_state.failed ?? 0}</span><span className="tile-hint">{s.failure_rate} % des messages</span></div>
            <div className={`tile static ${(s.by_state.quarantined ?? 0) + (s.by_state.spam ?? 0) ? 'warn' : ''}`}>
              <span className="tile-label">Filtrés</span>
              <span className="tile-value">{(s.by_state.quarantined ?? 0) + (s.by_state.spam ?? 0)}</span>
              <span className="tile-hint">quarantaine ou indésirables</span></div>
          </div>
          <HourlyChart data={s.per_hour} />
          <div className="chip-group" style={{ margin: '4px 0 12px' }}>
            {ORDER.filter((k) => s.by_state[k]).map((k) => (
              <span key={k} className="sub"><i style={{ display: 'inline-block', width: 10, height: 10, background: STATE[k].color, marginRight: 4 }} />
                {STATE[k].label} ({s.by_state[k]})</span>
            ))}
          </div>

          {r.truncated && <div className="messagebar">Résultat limité aux 500 messages les plus récents ; affine la recherche.</div>}
          <table className="grid">
            <thead><tr><th>Reçu</th><th>Expéditeur</th><th>Destinataire</th><th>Objet</th><th>Statut</th></tr></thead>
            <tbody>
              {r.messages.slice(0, limit).map((m) => {
                const st = STATE[stateOf(m.status)];
                return (
                  <tr key={`${m.trace_id}|${m.recipient}`}>
                    <td>{m.received ? new Date(m.received).toLocaleString() : '—'}</td>
                    <td>{m.sender}</td><td>{m.recipient}</td><td>{m.subject || '(sans objet)'}</td>
                    <td><span className={`badge ${st.cls}`}>{st.label}</span></td>
                  </tr>
                );
              })}
              {r.messages.length === 0 && <tr><td colSpan={5} className="sub">Aucun message pour ces critères.</td></tr>}
            </tbody>
          </table>
          {r.messages.length > limit && (
            <div style={{ padding: 12 }}><button onClick={() => setLimit(limit + PAGE)}>Afficher plus ({r.messages.length - limit})</button></div>
          )}
        </>
      )}
    </>
  );
}
