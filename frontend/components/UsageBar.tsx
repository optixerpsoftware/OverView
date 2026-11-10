import { usageLevel } from '../lib/platformTypes';

/** Consommation d'une limite d'abonnement : « 2 / 3 » avec une barre colorée (illimité : barre neutre). */
export default function UsageBar({ used, limit, label }: { used: number; limit: number | null; label?: string }) {
  const level = usageLevel(used, limit);
  const pct = limit === null ? 0 : limit === 0 ? 100 : Math.min(100, (used / limit) * 100);
  return (
    <div className={`usage usage-${level}`} title={label}>
      <div className="usage-text">{label && <span className="sub">{label} </span>}<b>{used}</b><span className="sub"> / {limit === null ? '∞' : limit}</span></div>
      <div className="usage-track"><span style={{ width: `${limit === null ? 4 : pct}%` }} /></div>
    </div>
  );
}
