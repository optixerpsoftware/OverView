'use client';
import { useState } from 'react';
import { ago } from '../lib/alerts';
import { useApi } from '../lib/useApi';

interface Traffic {
  days: number; error: string; note: string;
  app: { activity?: { last: string | null; delegated: string | null; app_only: string | null } };
  summary: { total: number; failures: number; per_day: { day: string; count: number }[]; users: [string, number][];
    countries: [string, number][]; ips: [string, number][] };
  recent: { at: string; user: string; ip: string; country: string; ok: boolean; client: string }[];
}

/** Trafic d'une appli tierce : chargé à la demande (le journal Microsoft met jusqu'à une minute à répondre). */
export default function AppTraffic({ connectorId, spId }: { connectorId: string; spId: string }) {
  const [days, setDays] = useState(30);
  const t = useApi<Traffic>(`/identity/connectors/${connectorId}/hygiene/apps/${spId}/traffic/?days=${days}`, { staleTime: 5 * 60_000 });
  const d = t.data;
  const max = Math.max(1, ...(d?.summary.per_day.map((p) => p.count) ?? [0]));
  return (
    <div className="app-traffic">
      <div className="chip-group" style={{ marginBottom: 8 }}>
        {[7, 30].map((n) => <button key={n} className={`chip-btn ${days === n ? 'on' : ''}`} onClick={() => setDays(n)}>{n} jours</button>)}
      </div>
      {t.loading && <div className="sub">Lecture du journal des connexions chez Microsoft (jusqu’à une minute)…</div>}
      {t.error && <div className="messagebar error">{t.error}</div>}
      {d && (
        <>
          {d.error && <div className="messagebar error">Journal des connexions indisponible : {d.error}</div>}
          <div className="tiles" style={{ marginBottom: 10 }}>
            <div className="tile static"><span className="tile-label">Connexions ({d.days} j)</span><span className="tile-value">{d.summary.total}</span>
              <span className="tile-hint">{d.summary.failures} échec(s)</span></div>
            <div className="tile static"><span className="tile-label">Au nom d’un utilisateur</span>
              <span className="tile-value" style={{ fontSize: 16 }}>{d.app.activity?.delegated ? ago(d.app.activity.delegated) : 'aucune connue'}</span>
              <span className="tile-hint">dernière utilisation</span></div>
            <div className="tile static"><span className="tile-label">Sans utilisateur</span>
              <span className="tile-value" style={{ fontSize: 16 }}>{d.app.activity?.app_only ? ago(d.app.activity.app_only) : 'aucune connue'}</span>
              <span className="tile-hint">permissions d’application</span></div>
          </div>
          {d.summary.total > 0 && (
            <>
              <div className="traffic-bars" role="img" aria-label="Connexions par jour">
                {d.summary.per_day.map((p) => (
                  <span key={p.day} title={`${p.day} : ${p.count}`} style={{ height: `${(p.count / max) * 100}%` }} />
                ))}
              </div>
              <div className="traffic-cols">
                <div><strong>Utilisateurs</strong>{d.summary.users.map(([u, n]) => <div key={u} className="sub">{u} · {n}</div>)}</div>
                <div><strong>Pays</strong>{d.summary.countries.map(([c, n]) => <div key={c} className="sub">{c} · {n}</div>)}</div>
                <div><strong>Adresses IP</strong>{d.summary.ips.map(([ip, n]) => <div key={ip} className="sub">{ip} · {n}</div>)}</div>
              </div>
              <table className="grid">
                <thead><tr><th>Quand</th><th>Utilisateur</th><th>IP</th><th>Pays</th><th>Résultat</th></tr></thead>
                <tbody>
                  {d.recent.map((r, i) => (
                    <tr key={i}><td>{new Date(r.at).toLocaleString()}</td><td>{r.user}</td><td>{r.ip}</td><td>{r.country}</td>
                      <td>{r.ok ? <span className="badge ok">Réussie</span> : <span className="badge err">Échec</span>}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          <p className="sub">{d.note}</p>
        </>
      )}
    </div>
  );
}
