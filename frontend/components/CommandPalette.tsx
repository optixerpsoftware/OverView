'use client';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import type { SearchHit } from '../lib/types';

const ICON: Record<SearchHit['type'], string> = { org: '🏢', tenant: '🔌', mailbox: '👤', rule: '📨', file: '📄' };
const TYPE_LABEL: Record<SearchHit['type'], string> = {
  org: 'Organisation', tenant: 'Tenant', mailbox: 'Compte', rule: 'Règle', file: 'Fichier partagé',
};
const PAGES: SearchHit[] = [
  { type: 'org', label: 'Tableau de bord', sub: 'Page', href: '/' },
  { type: 'org', label: 'À faire', sub: 'Page', href: '/queue' },
  { type: 'org', label: 'Centre d’alertes', sub: 'Page', href: '/alerts' },
  { type: 'org', label: 'Organisations', sub: 'Page', href: '/organizations' },
];

/** Recherche globale (Ctrl+K / ⌘K) : organisations, tenants, comptes, règles et fichiers partagés de tous les tenants. */
export default function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<SearchHit[]>(PAGES);
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) { setQ(''); setHits(PAGES); setActive(0); setTimeout(() => input.current?.focus(), 0); }
  }, [open]);

  // Recherche « au fil de la frappe », avec un court délai pour ne pas interroger l'API à chaque touche.
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setHits(PAGES.filter((p) => !term || p.label.toLowerCase().includes(term.toLowerCase()))); return; }
    let cancelled = false;
    setBusy(true);
    const t = setTimeout(async () => {
      try {
        const r = await api<SearchHit[]>(`/insights/search/?q=${encodeURIComponent(term)}`);
        if (!cancelled) { setHits(r); setActive(0); }
      } catch {
        if (!cancelled) setHits([]);
      } finally {
        if (!cancelled) setBusy(false);
      }
    }, 200);
    return () => { cancelled = true; clearTimeout(t); };
  }, [q]);

  if (!open) return null;

  function go(h: SearchHit | undefined) {
    if (!h) return;
    onClose();
    router.push(h.href);
  }

  return (
    <div className="overlay palette-overlay" role="dialog" aria-modal="true" aria-label="Recherche globale"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="palette">
        <input ref={input} type="search" value={q} placeholder="Rechercher un compte, une règle, un fichier partagé, un tenant…"
          aria-label="Recherche globale" aria-controls="palette-results" aria-activedescendant={hits[active] ? `hit-${active}` : undefined}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose();
            else if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            else if (e.key === 'Enter') { e.preventDefault(); go(hits[active]); }
          }} />
        <ul id="palette-results" role="listbox" className="palette-list">
          {hits.map((h, i) => (
            <li key={`${h.href}-${i}`} id={`hit-${i}`} role="option" aria-selected={i === active}
              className={i === active ? 'on' : ''} onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); go(h); }}>
              <span aria-hidden="true">{ICON[h.type]}</span>
              <span className="grow">
                <span className="ellipsis" style={{ display: 'block' }}>{h.label}</span>
                <span className="sub ellipsis" style={{ display: 'block' }}>{h.sub === 'Page' ? 'Page' : `${TYPE_LABEL[h.type]} · ${h.sub}`}</span>
              </span>
            </li>
          ))}
          {!busy && q.trim().length >= 2 && hits.length === 0 && <li className="sub">Aucun résultat pour « {q} ».</li>}
        </ul>
        <div className="palette-foot sub">↑ ↓ pour choisir · Entrée pour ouvrir · Échap pour fermer</div>
      </div>
    </div>
  );
}
