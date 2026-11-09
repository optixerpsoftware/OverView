// Anneau de score (0–100) en SVG : couleur selon le niveau, sans dépendance.
export default function ScoreRing({ score, size = 132 }: { score: number | null; size?: number }) {
  const r = (size - 16) / 2;
  const c = 2 * Math.PI * r;
  const v = score ?? 0;
  const color = score === null ? '#a0a0a0' : v >= 80 ? '#107c10' : v >= 60 ? '#c19c00' : v >= 40 ? '#bc4b09' : '#b10e1c';
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`Score ${score ?? 'indisponible'}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#ececec" strokeWidth="10" />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
        strokeDasharray={`${(c * v) / 100} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central" fontSize={size * 0.27} fontWeight="600" fill="currentColor">
        {score === null ? '—' : Math.round(v)}
      </text>
    </svg>
  );
}
