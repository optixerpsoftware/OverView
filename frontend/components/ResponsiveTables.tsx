'use client';
import { useEffect } from 'react';

/**
 * Sur mobile, les tableaux `table.grid` s'affichent en cartes (une ligne = une carte) : chaque cellule a besoin du
 * titre de sa colonne pour rester compréhensible. Ce composant recopie le texte des en-têtes dans `data-label`
 * sur chaque cellule, pour toutes les pages, y compris quand les tableaux changent (filtres, rechargement).
 */
export default function ResponsiveTables() {
  useEffect(() => {
    let scheduled = false;

    const label = () => {
      scheduled = false;
      document.querySelectorAll<HTMLTableElement>('table.grid').forEach((table) => {
        const heads = Array.from(table.querySelectorAll('thead th')).map((th) => (th.textContent ?? '').trim());
        if (!heads.length) return;
        table.querySelectorAll<HTMLTableRowElement>('tbody tr').forEach((tr) => {
          Array.from(tr.children).forEach((td, i) => {
            const h = heads[i] ?? '';
            if (td.getAttribute('data-label') !== h) td.setAttribute('data-label', h);
          });
        });
      });
    };

    const schedule = () => {
      if (!scheduled) {
        scheduled = true;
        requestAnimationFrame(label);
      }
    };

    label();
    const obs = new MutationObserver(schedule);
    obs.observe(document.body, { childList: true, subtree: true });
    return () => obs.disconnect();
  }, []);
  return null;
}
