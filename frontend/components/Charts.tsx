'use client';
import { useState } from 'react';

/* Graphiques SVG légers, sans bibliothèque, lisibles en thème clair et sombre (couleurs = variables CSS). */

export const SEV_COLORS: Record<string, string> = {
  critical: 'var(--chart-critical)', high: 'var(--chart-high)', medium: 'var(--chart-medium)', low: 'var(--chart-low)',
};
const SEV_LABEL: Record<string, string> = { critical: 'Critique', high: 'Élevée', medium: 'Moyenne', low: 'Faible' };

export interface TrendDay { day: string; critical: number; high: number; medium: number; low: number; resolved: number; active: number }

const fmtDay = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });

/** Barres empilées (nouvelles alertes par gravité) + courbe des alertes actives, avec infobulle au survol. */
export function AlertTrendChart({ data, height = 180 }: { data: TrendDay[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640, H = height, PAD_B = 22, PAD_T = 8, PAD_L = 28;
  const sev = ['critical', 'high', 'medium', 'low'] as const;
  const maxBar = Math.max(1, ...data.map((d) => sev.reduce((n, s) => n + d[s], 0)));
  const maxLine = Math.max(1, ...data.map((d) => d.active));
  const max = Math.max(maxBar, maxLine);
  const plotH = H - PAD_B - PAD_T;
  const bw = (W - PAD_L) / Math.max(data.length, 1);
  const y = (v: number) => PAD_T + plotH - (v / max) * plotH;
  const line = data.map((d, i) => `${i ? 'L' : 'M'}${(PAD_L + i * bw + bw / 2).toFixed(1)},${y(d.active).toFixed(1)}`).join(' ');
  const ticks = [0, Math.round(max / 2), max];
  const h = hover !== null ? data[hover] : null;

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Évolution des alertes sur 30 jours" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD_L} x2={W} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeDasharray={t ? '3 3' : undefined} />
            <text x={PAD_L - 6} y={y(t) + 4} textAnchor="end" fontSize="10" fill="var(--text-2)">{t}</text>
          </g>
        ))}
        {data.map((d, i) => {
          let top = PAD_T + plotH;
          return (
            <g key={d.day} onMouseEnter={() => setHover(i)}>
              <rect x={PAD_L + i * bw} y={PAD_T} width={bw} height={plotH} fill={hover === i ? 'var(--hover)' : 'transparent'} />
              {sev.map((s) => {
                if (!d[s]) return null;
                const hh = (d[s] / max) * plotH;
                top -= hh;
                return <rect key={s} x={PAD_L + i * bw + bw * 0.18} y={top} width={bw * 0.64} height={hh} rx="1.5" fill={SEV_COLORS[s]} />;
              })}
            </g>
          );
        })}
        <path d={line} fill="none" stroke="var(--brand)" strokeWidth="2" strokeLinejoin="round" pointerEvents="none" />
        {data.map((d, i) => ((i % 7 === 0 && data.length - 1 - i >= 4) || i === data.length - 1) && (
          <text key={d.day} x={PAD_L + i * bw + bw / 2} y={H - 6} textAnchor="middle" fontSize="10" fill="var(--text-2)">{fmtDay(d.day)}</text>
        ))}
      </svg>
      <div className="chart-legend">
        {sev.map((s) => <span key={s}><i style={{ background: SEV_COLORS[s] }} />{SEV_LABEL[s]}</span>)}
        <span><i className="line" />Alertes actives</span>
      </div>
      <div className="chart-tip" aria-live="polite">
        {h ? (
          <>
            <strong>{fmtDay(h.day)}</strong> · {sev.reduce((n, s) => n + h[s], 0)} nouvelle(s)
            {sev.filter((s) => h[s]).map((s) => ` · ${h[s]} ${SEV_LABEL[s].toLowerCase()}`).join('')}
            {' · '}{h.resolved} résolue(s) · {h.active} active(s)
          </>
        ) : <span className="sub">Survole un jour pour le détail.</span>}
      </div>
    </div>
  );
}

/** Anneau de répartition (ex. alertes ouvertes par gravité), total au centre. */
export function Donut({ parts, size = 140, label }: { parts: { key: string; value: number; color: string; label: string }[]; size?: number; label: string }) {
  const total = parts.reduce((n, p) => n + p.value, 0);
  const r = size / 2 - 12, c = 2 * Math.PI * r;
  let acc = 0;
  return (
    <div className="donut">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label} : ${total}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--hover)" strokeWidth="14" />
        {total > 0 && parts.filter((p) => p.value).map((p) => {
          const len = (p.value / total) * c;
          const el = (
            <circle key={p.key} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={p.color} strokeWidth="14"
              strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-acc} transform={`rotate(-90 ${size / 2} ${size / 2})`}>
              <title>{`${p.label} : ${p.value}`}</title>
            </circle>
          );
          acc += len;
          return el;
        })}
        <text x="50%" y="47%" textAnchor="middle" dominantBaseline="central" fontSize="26" fontWeight="600" fill="currentColor">{total}</text>
        <text x="50%" y="64%" textAnchor="middle" fontSize="10" fill="var(--text-2)">{label}</text>
      </svg>
      <ul className="donut-legend">
        {parts.map((p) => <li key={p.key}><i style={{ background: p.color }} />{p.label}<b>{p.value}</b></li>)}
      </ul>
    </div>
  );
}

/** Barres horizontales (ex. alertes par catégorie). */
export function HBars({ rows }: { rows: { label: string; value: number; color?: string }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length) return <div className="sub">Aucune donnée.</div>;
  return (
    <div className="hbars">
      {rows.map((r) => (
        <div className="hbar" key={r.label}>
          <span className="hbar-label">{r.label}</span>
          <span className="hbar-track"><span className="hbar-fill" style={{ width: `${(r.value / max) * 100}%`, background: r.color ?? 'var(--brand)' }} /></span>
          <b>{r.value}</b>
        </div>
      ))}
    </div>
  );
}

/** Mini-courbe (ex. tendance du score), couleur selon la direction. */
export function Spark({ values, width = 90, height = 26 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) return <span className="sub">—</span>;
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * (width - 4) + 2).toFixed(1)},${(height - 3 - ((v - min) / span) * (height - 6)).toFixed(1)}`);
  const up = values[values.length - 1] >= values[0];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-label={`Tendance : ${values[0]} → ${values[values.length - 1]}`}>
      <polyline points={pts.join(' ')} fill="none" stroke={up ? 'var(--success)' : 'var(--danger)'} strokeWidth="2" strokeLinejoin="round" />
    </svg>
  );
}
