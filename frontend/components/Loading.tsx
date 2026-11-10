/** Squelette de chargement (lignes animées) : la mise en page ne « saute » pas quand les données arrivent. */
export default function Loading({ label, lines = 3 }: { label?: string; lines?: number }) {
  return (
    <div className="skeleton" role="status" aria-live="polite" aria-busy="true">
      {label && <span className="label">{label}</span>}
      {Array.from({ length: lines }).map((_, i) => <i key={i} />)}
    </div>
  );
}
