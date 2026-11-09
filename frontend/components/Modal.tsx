'use client';
import type { ReactNode } from 'react';

// Fenêtre modale pour les formulaires (ajout de connecteur, import de boîtes…).
export default function Modal({ title, onClose, children, wide }: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean;
}) {
  return (
    <div className="overlay" role="dialog" aria-modal="true" onKeyDown={(e) => e.key === 'Escape' && onClose()}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="dialog" style={wide ? { maxWidth: 680 } : undefined}>
        <div className="modal-head">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="ghost" aria-label="Fermer" onClick={onClose}>✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}
