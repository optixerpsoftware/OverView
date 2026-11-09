'use client';
import { useEffect, useState } from 'react';
import type { TotpResult } from '../lib/types';

// Affiche une seule fois la clé d'une application TOTP : QR code à scanner + clé de saisie manuelle.
// La clé est aussi sensible qu'un mot de passe : rien n'est conservé côté navigateur.
export default function TotpReveal({ totp, onDismiss }: { totp: TotpResult; onDismiss: () => void }) {
  const [qr, setQr] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    import('qrcode')
      .then((m) => m.toDataURL(totp.uri, { width: 192, margin: 1, errorCorrectionLevel: 'M' }))
      .then((url) => { if (alive) setQr(url); })
      .catch(() => { if (alive) setQr(''); });
    return () => { alive = false; };
  }, [totp.uri]);

  const spaced = totp.secret.replace(/(.{4})/g, '$1 ').trim();

  async function copy() {
    try {
      await navigator.clipboard.writeText(totp.secret);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="messagebar secret" style={{ flexWrap: 'wrap', alignItems: 'flex-start' }}>
      {qr && <img src={qr} alt="QR code de l’application TOTP" width={192} height={192} style={{ background: '#fff', borderRadius: 6 }} />}
      <div style={{ flex: 1, minWidth: 260 }}>
        <strong>Application TOTP ajoutée</strong> pour {totp.account} — <strong>affichée une seule fois</strong>, la clé n’est pas conservée.
        <ol style={{ margin: '8px 0 8px 18px', padding: 0 }}>
          <li>Ouvre Microsoft Authenticator, Google Authenticator, Authy…</li>
          <li>Ajoute un compte et <strong>scanne ce QR code</strong> (ou saisis la clé ci-dessous manuellement : type « basé sur l’heure »).</li>
          <li>Le code à 6 chiffres affiché sera demandé à la connexion.</li>
        </ol>
        <div className="secret-value" style={{ fontSize: 15 }}>{spaced}</div>
        <div className="sub">Remets-la par un canal sûr, puis masque ce panneau : quiconque a cette clé peut générer les codes du compte.</div>
      </div>
      <div style={{ display: 'grid', gap: 8 }}>
        <button onClick={copy}>{copied ? 'Copiée ✓' : 'Copier la clé'}</button>
        <button onClick={onDismiss}>Masquer</button>
      </div>
    </div>
  );
}
