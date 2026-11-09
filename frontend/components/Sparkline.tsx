import type { ScorePoint } from '../lib/types';

// Évolution du score : courbe SVG simple (0–100).
export default function Sparkline({ points, width = 320, height = 56 }: { points: ScorePoint[]; width?: number; height?: number }) {
  const data = points.filter((p) => p.score !== null);
  if (data.length < 2) return <div className="sub">Pas encore assez d&apos;historique (1 analyse par jour).</div>;
  const x = (i: number) => (i / (data.length - 1)) * (width - 8) + 4;
  const y = (v: number) => height - 6 - (v / 100) * (height - 12);
  const path = data.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.score as number).toFixed(1)}`).join(' ');
  const last = data[data.length - 1];
  const first = data[0];
  const delta = (last.score as number) - (first.score as number);
  return (
    <div>
      <svg width="100%" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ maxWidth: width, display: 'block' }}>
        <path d={path} fill="none" stroke="#0f6cbd" strokeWidth="2" strokeLinejoin="round" />
        <circle cx={x(data.length - 1)} cy={y(last.score as number)} r="3.5" fill="#0f6cbd" />
      </svg>
      <div className="sub">
        {data.length} analyses · {delta === 0 ? 'stable' : `${delta > 0 ? '+' : ''}${delta.toFixed(1)} pts depuis le début`}
      </div>
    </div>
  );
}
