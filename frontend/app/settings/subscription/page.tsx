'use client';
import { useEffect } from 'react';
import Loading from '../../../components/Loading';
import UsageBar from '../../../components/UsageBar';
import { LIMIT_LABEL, type LimitKind } from '../../../lib/platformTypes';
import { useSubscription } from '../../../lib/subscription';

const HINT: Record<LimitKind, string> = {
  tenants: 'Tenants Microsoft 365 connectés, toutes organisations confondues.',
  organizations: 'Organisations clientes créées dans ton espace.',
  users: 'Comptes OverView actifs (techniciens, administrateurs, accès client).',
};

export default function SubscriptionPage() {
  const { sub } = useSubscription();
  useEffect(() => { document.title = 'Abonnement — OverView'; }, []);
  const days = sub?.expires_at ? Math.ceil((new Date(sub.expires_at).getTime() - Date.now()) / 86_400_000) : null;

  return (
    <>
      <div className="pagehead">
        <div><h1>Mon abonnement</h1><div className="sub">Forfait et quotas de ton espace OverView</div></div>
      </div>
      {!sub && <Loading lines={4} />}
      {sub && (
        <>
          {sub.alerts.map((a, i) => <div key={i} className={`messagebar ${a.level === 'err' ? 'error' : 'warn'}`}>{a.text}</div>)}
          <div className="tiles">
            <div className="tile static"><span className="tile-label">Forfait</span><span className="tile-value">{sub.plan_label}</span>
              <span className="tile-hint">{sub.name}</span></div>
            <div className={`tile static ${days !== null && days <= 30 ? 'warn' : ''}`}><span className="tile-label">Échéance</span>
              <span className="tile-value">{sub.expires_at ? new Date(sub.expires_at).toLocaleDateString() : 'Aucune'}</span>
              <span className="tile-hint">{days === null ? 'abonnement sans date de fin' : days >= 0 ? `dans ${days} jour(s)` : 'échu'}</span></div>
          </div>
          <section className="panel">
            <h2>Quotas</h2>
            <table className="grid">
              <thead><tr><th>Ressource</th><th>Consommation</th><th>Disponible</th></tr></thead>
              <tbody>
                {(Object.keys(LIMIT_LABEL) as LimitKind[]).map((k) => (
                  <tr key={k}>
                    <td><strong>{LIMIT_LABEL[k]}</strong><div className="sub">{HINT[k]}</div></td>
                    <td style={{ minWidth: 180 }}><UsageBar used={sub.usage[k]} limit={sub.limits[k]} /></td>
                    <td>{sub.remaining[k] === null ? 'Illimité' : sub.remaining[k] === 0
                      ? <span className="badge err">Aucun</span> : sub.remaining[k]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="sub" style={{ marginTop: 12 }}>
              Une fois un quota atteint, la création correspondante est refusée ; ce qui existe déjà continue de fonctionner.
              Pour augmenter ton forfait ou prolonger l’échéance, contacte l’éditeur d’OverView.
            </p>
          </section>
        </>
      )}
    </>
  );
}
