'use client';
import { useState } from 'react';

interface Props {
  password: string;
  onDismiss: () => void;
  title?: string;
  hint?: string;
}

// Affiche un secret généré une seule fois (mot de passe, code d'accès temporaire), avec copie.
// Rien n'est persisté côté navigateur.
export default function SecretReveal({
  password, onDismiss,
  title = 'Nouveau mot de passe',
  hint = "affiché une seule fois, il n'est pas conservé. Transmets-le par un canal sûr ; l'utilisateur devra le changer à sa prochaine connexion.",
}: Props) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(password);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="messagebar secret">
      <div style={{ flex: 1 }}>
        <strong>{title}</strong> — {hint}
        <div className="secret-value">{password}</div>
      </div>
      <button onClick={copy}>{copied ? 'Copié ✓' : 'Copier'}</button>
      <button onClick={onDismiss}>Masquer</button>
    </div>
  );
}
