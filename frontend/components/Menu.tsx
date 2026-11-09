'use client';
import { useEffect, useRef, useState } from 'react';

export interface MenuItem {
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}

// Menu « ⋯ » : regroupe les actions secondaires d'une ligne. Se ferme au clic extérieur / Échap.
export default function Menu({ items, label = 'Plus d’actions' }: { items: MenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  return (
    <div className="menu" ref={ref}>
      <button className="ghost menu-btn" aria-haspopup="menu" aria-expanded={open} aria-label={label} title={label}
        onClick={() => setOpen(!open)}>⋯</button>
      {open && (
        <div className="menu-list" role="menu">
          {items.map((it) => (
            <button key={it.label} role="menuitem" className={`menu-item ${it.danger ? 'danger' : ''}`} disabled={it.disabled}
              onClick={() => { setOpen(false); it.onClick(); }}>
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
