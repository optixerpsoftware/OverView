'use client';
import { useEffect, useState } from 'react';

/** Re-rend le composant toutes les `ms` : garde à jour les libellés relatifs (« il y a 3 min ») sans recharger de données. */
export function useTick(ms = 30_000): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setN((x) => x + 1), ms);
    return () => clearInterval(t);
  }, [ms]);
  return n;
}
