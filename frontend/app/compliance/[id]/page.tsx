'use client';
import Link from 'next/link';
import { use, useState } from 'react';
import ScoreRing from '../../../components/ScoreRing';
import Sparkline from '../../../components/Sparkline';
import { api, errorMessage } from '../../../lib/api';
import type {
  ComplianceSnapshot, Connector, Finding, FindingCategory, Note, ScorePoint, Severity,
} from '../../../lib/types';
import { useApi } from '../../../lib/useApi';

type Filter = 'todo' | 'pass' | 'unavailable' | 'all';

const CATEGORIES: { key: FindingCategory; label: string }[] = [
  { key: 'mfa', label: 'Authentification (MFA)' },
  { key: 'access', label: 'Accès & rôles' },
  { key: 'policies', label: 'Politiques' },
  { key: 'posture', label: 'Posture & applications' },
];

const SEVERITY: Record<Severity, { label: string; cls: string }> = {
  critical: { label: 'Critique', cls: 'err' },
  high: { label: 'Élevée', cls: 'err' },
  medium: { label: 'Moyenne', cls: 'warn' },
  low: { label: 'Faible', cls: 'off' },
  info: { label: 'Info', cls: 'off' },
};
const SEV_ORDER: Severity[] = ['critical', 'high', 'medium', 'low', 'info'];

const isTodo = (f: Finding) => f.status === 'fail';
const isNA = (f: Finding) => f.status === 'unavailable' || f.status === 'error';

function FindingRow({ f }: { f: Finding }) {
  const sev = SEVERITY[f.severity];
  const badge = f.status === 'pass' ? <span className="badge ok">Conforme</span>
    : f.status === 'fail' ? <span className={`badge ${sev.cls}`}>{sev.label}</span>
    : f.status === 'unavailable' ? <span className="badge off">Indisponible</span>
    : <span className="badge err">Erreur</span>;
  const expandable = f.details.length > 0 || f.recommendation || f.requires;
  return (
    <details className="finding" open={f.status === 'fail' && (f.severity === 'critical' || f.severity === 'high')}>
      <summary className={expandable ? '' : 'static'}>
        {badge}
        <span className="finding-title">{f.title}</span>
        <span className="sub finding-sum">{f.summary}</span>
      </summary>
      {expandable && (
        <div className="finding-body">
          {f.recommendation && <p><strong>À faire :</strong> {f.recommendation}</p>}
          {f.requires && f.status !== 'pass' && (
            <p className="note">Nécessite : <code>{f.requires}</code> (permission applicative avec consentement administrateur, ou licence).</p>
          )}
          {f.details.length > 0 && <ul>{f.details.map((d, i) => <li key={i}>{d}</li>)}</ul>}
        </div>
      )}
    </details>
  );
}

export default function CompliancePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const connector = useApi<Connector>(`/connectors/${id}/`);
  const latest = useApi<ComplianceSnapshot | null>(`/compliance/connectors/${id}/latest/`);
  const history = useApi<ScorePoint[]>(`/compliance/connectors/${id}/history/`);
  const [filter, setFilter] = useState<Filter>('todo');
  const [scanning, setScanning] = useState(false);
  const [note, setNote] = useState<Note | null>(null);

  async function scan() {
    setScanning(true);
    setNote(null);
    try {
      await api<ComplianceSnapshot>(`/compliance/connectors/${id}/scan/`, { method: 'POST' });
      await Promise.all([latest.reload(), history.reload()]);
    } catch (e) {
      setNote({ text: errorMessage(e), error: true });
    } finally {
      setScanning(false);
    }
  }

  const snap = latest.data;
  const findings = snap?.findings ?? [];
  const todo = findings.filter(isTodo);
  const counts = {
    todo: todo.length,
    pass: findings.filter((f) => f.status === 'pass').length,
    unavailable: findings.filter(isNA).length,
    all: findings.length,
  };
  const shown = findings
    .filter((f) => filter === 'all' || (filter === 'todo' ? isTodo(f) : filter === 'pass' ? f.status === 'pass' : isNA(f)))
    .sort((a, b) => SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity));
  const lic = snap?.licenses;

  return (
    <>
      <div className="breadcrumb"><Link href="/">Organisations</Link> › Gouvernance</div>

      <header className="hero">
        <div style={{ color: 'var(--text)' }}><ScoreRing score={snap?.score ?? null} /></div>
        <div className="hero-id">
          <h1>Gouvernance du tenant</h1>
          <div className="sub">{connector.data?.name ?? '…'}</div>
          <div className="chips">
            {snap && <span className="badge off">Analyse du {new Date(snap.created_at).toLocaleString()}</span>}
            {lic && !lic.error && (
              <span className={`badge ${lic.entra_p1 ? 'ok' : 'off'}`}>
                {lic.entra_p2 ? 'Entra ID P2' : lic.entra_p1 ? 'Entra ID P1' : 'Entra ID gratuit'}
              </span>
            )}
          </div>
        </div>
        <div className="hero-actions">
          <button className="primary" disabled={scanning} onClick={scan}>
            {scanning ? 'Analyse en cours…' : snap ? 'Relancer l’analyse' : 'Lancer l’analyse'}
          </button>
        </div>
      </header>

      {note && <div className={`messagebar ${note.error ? 'error' : ''}`}>{note.text}</div>}
      {(latest.error || connector.error) && <div className="messagebar error">{latest.error || connector.error}</div>}

      {!snap && !latest.error && !scanning && (
        <div className="panel" style={{ borderTop: '1px solid var(--border)', borderRadius: 10 }}>
          <div className="empty">Aucune analyse pour ce tenant. Lance la première (≈ 20 s) : elle est ensuite répétée chaque jour.</div>
        </div>
      )}
      {scanning && <div className="messagebar">Analyse du tenant en cours, cela peut prendre une vingtaine de secondes…</div>}

      {snap && (
        <>
          <div className="tiles">
            {CATEGORIES.map((c) => {
              const inCat = findings.filter((f) => f.category === c.key && f.status !== 'unavailable' && f.status !== 'error' && f.severity !== 'info');
              const bad = inCat.filter(isTodo).length;
              return (
                <div className={`tile static ${bad ? 'warn' : ''}`} key={c.key}>
                  <span className="tile-label">{c.label}</span>
                  <span className="tile-value">{inCat.length ? `${inCat.length - bad}/${inCat.length}` : '—'}</span>
                  <span className="tile-hint">{bad ? `${bad} à corriger` : inCat.length ? 'conforme' : 'non évalué'}</span>
                </div>
              );
            })}
          </div>

          <section className="card">
            <h2>Évolution du score</h2>
            <Sparkline points={history.data ?? []} />
          </section>

          <nav className="tabs" role="tablist">
            {([['todo', 'À corriger'], ['pass', 'Conformes'], ['unavailable', 'Indisponibles'], ['all', 'Tous']] as [Filter, string][]).map(([k, label]) => (
              <button key={k} role="tab" aria-selected={filter === k} className={`tab ${filter === k ? 'on' : ''}`} onClick={() => setFilter(k)}>
                {label} <span className="count">{counts[k]}</span>
              </button>
            ))}
          </nav>
          <section className="panel">
            {shown.length === 0 && (
              <div className="empty">{filter === 'todo' ? 'Rien à corriger sur les contrôles évalués. 🎉' : 'Aucun contrôle dans cette catégorie.'}</div>
            )}
            {CATEGORIES.map((c) => {
              const rows = shown.filter((f) => f.category === c.key);
              if (rows.length === 0) return null;
              return (
                <div key={c.key}>
                  <h3 className="panel-title">{c.label}</h3>
                  {rows.map((f) => <FindingRow key={f.code} f={f} />)}
                </div>
              );
            })}
          </section>
        </>
      )}
    </>
  );
}
