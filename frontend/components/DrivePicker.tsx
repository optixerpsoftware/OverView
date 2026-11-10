'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ago } from '../lib/alerts';
import type { Drive } from '../lib/types';

const RECENT_MAX = 5;
const recentKey = (connectorId: string) => `overview-recent-drives-${connectorId}`;

function readRecent(connectorId: string): string[] {
  try { return JSON.parse(localStorage.getItem(recentKey(connectorId)) || '[]'); } catch { return []; }
}
function pushRecent(connectorId: string, id: string) {
  try {
    const list = [id, ...readRecent(connectorId).filter((x) => x !== id)].slice(0, RECENT_MAX);
    localStorage.setItem(recentKey(connectorId), JSON.stringify(list));
  } catch { /* stockage indisponible : sans importance */ }
}

const nameOf = (d: Drive) => d.label.replace(/^OneDrive — /, '');
const subOf = (d: Drive) => (d.kind === 'onedrive' ? d.owner || 'OneDrive' : (d.web_url || '').replace(/^https?:\/\//, '').split('/').slice(0, 3).join('/'));

interface Props {
  connectorId: string;
  drives: Drive[] | null;
  value: string;
  onChange: (id: string) => void;
  fetchedAt?: string | null;
  refreshing?: boolean;
  onRefresh?: () => void;
}

/** Sélecteur d'espace (OneDrive / bibliothèque SharePoint) : recherche instantanée, récents, clavier. */
export default function DrivePicker({ connectorId, drives, value, onChange, fetchedAt, refreshing, onRefresh }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const [recent, setRecent] = useState<string[]>([]);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { setRecent(readRecent(connectorId)); }, [connectorId]);
  useEffect(() => {
    if (!open) return;
    setTimeout(() => input.current?.focus(), 0);
    const onDoc = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const all = drives ?? [];
  const current = all.find((d) => d.id === value) ?? null;
  const term = q.trim().toLowerCase();
  const match = (d: Drive) => !term || `${d.label} ${d.owner} ${d.web_url}`.toLowerCase().includes(term);

  // Liste à plat (pour le clavier) avec ses en-têtes de groupe.
  const sections = useMemo(() => {
    const out: { title: string; items: Drive[] }[] = [];
    if (!term) {
      const rec = recent.map((id) => all.find((d) => d.id === id)).filter((d): d is Drive => !!d);
      if (rec.length) out.push({ title: 'Récents', items: rec });
    }
    out.push({ title: 'OneDrive', items: all.filter((d) => d.kind === 'onedrive' && match(d)) });
    out.push({ title: 'SharePoint', items: all.filter((d) => d.kind === 'sharepoint' && match(d)) });
    return out.filter((s) => s.items.length);
  }, [all, term, recent]); // eslint-disable-line react-hooks/exhaustive-deps
  const flat = sections.flatMap((s) => s.items);
  useEffect(() => { setActive(0); }, [term]);

  function choose(d: Drive) {
    pushRecent(connectorId, d.id);
    setRecent(readRecent(connectorId));
    onChange(d.id);
    setOpen(false);
    setQ('');
  }

  let idx = -1;
  return (
    <div className="drive-picker" ref={box}>
      <button type="button" className="drive-picker-button" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)}>
        {current ? (
          <>
            <span aria-hidden="true">{current.kind === 'onedrive' ? '👤' : '🏢'}</span>
            <span className="grow"><span className="ellipsis" style={{ display: 'block' }}>{nameOf(current)}</span>
              <span className="sub ellipsis" style={{ display: 'block' }}>{subOf(current)}</span></span>
          </>
        ) : (
          <span className="grow sub">{drives ? `Choisir parmi ${all.length} espaces (OneDrive, SharePoint)…` : 'Chargement des espaces…'}</span>
        )}
        <span aria-hidden="true">▾</span>
      </button>

      {open && (
        <div className="drive-picker-panel">
          <input ref={input} type="search" value={q} placeholder="Rechercher un utilisateur, un site, une bibliothèque…"
            aria-label="Rechercher un espace" onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setOpen(false);
              else if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, flat.length - 1)); }
              else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
              else if (e.key === 'Enter' && flat[active]) { e.preventDefault(); choose(flat[active]); }
            }} />
          <div className="drive-picker-list" role="listbox">
            {sections.map((s) => (
              <div key={s.title}>
                <div className="drive-picker-group">{s.title} <span className="sub">({s.items.length})</span></div>
                {s.items.map((d) => {
                  idx += 1;
                  const i = idx;
                  return (
                    <div key={`${s.title}-${d.id}`} role="option" aria-selected={d.id === value}
                      className={`drive-picker-item ${i === active ? 'on' : ''} ${d.id === value ? 'current' : ''}`}
                      onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); choose(d); }}>
                      <span aria-hidden="true">{d.kind === 'onedrive' ? '👤' : '🏢'}</span>
                      <span className="grow"><span className="ellipsis" style={{ display: 'block' }}>{nameOf(d)}</span>
                        <span className="sub ellipsis" style={{ display: 'block' }}>{subOf(d)}</span></span>
                    </div>
                  );
                })}
              </div>
            ))}
            {flat.length === 0 && <div className="sub" style={{ padding: 12 }}>Aucun espace ne correspond à « {q} ».</div>}
          </div>
          <div className="drive-picker-foot sub">
            <span>{fetchedAt ? `Liste mise à jour ${ago(fetchedAt)}` : ''}</span>
            {onRefresh && <button type="button" className="ghost" disabled={refreshing} onClick={onRefresh}>{refreshing ? 'Actualisation…' : 'Actualiser'}</button>}
          </div>
        </div>
      )}
    </div>
  );
}
