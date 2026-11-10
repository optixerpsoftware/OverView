'use client';
import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { useApi, useApiMutation } from '../../lib/useApi';

interface Me { username: string; email: string; role: 'viewer' | 'operator' | 'admin'; mfa_enabled: boolean; mfa_required: boolean }
const ROLE = { viewer: 'Lecture seule', operator: 'Technicien', admin: 'Administrateur' };

export default function AccountPage() {
  const me = useApi<Me>('/accounts/me/');
  const [setup, setSetup] = useState<{ secret: string; otpauth: string } | null>(null);
  const [qr, setQr] = useState('');
  const [code, setCode] = useState('');
  useEffect(() => { document.title = 'Mon compte — OverView'; }, []);
  useEffect(() => {
    if (!setup) { setQr(''); return; }
    let alive = true;
    import('qrcode').then((m) => m.toDataURL(setup.otpauth, { width: 192, margin: 1 })).then((u) => { if (alive) setQr(u); }).catch(() => {});
    return () => { alive = false; };
  }, [setup]);

  const start = useApiMutation(() => api<{ secret: string; otpauth: string }>('/accounts/me/mfa/', { method: 'POST' }),
    { onSuccess: (s) => { setSetup(s); setCode(''); } });
  const enable = useApiMutation((c: string) => api<Me>('/accounts/me/mfa/', { method: 'PUT', body: { code: c } }),
    { invalidate: ['/accounts/'], success: 'MFA activé : il sera demandé à chaque connexion.', onSuccess: () => setSetup(null) });
  const disable = useApiMutation((c: string) => api<Me>('/accounts/me/mfa/', { method: 'DELETE', body: { code: c } }),
    { invalidate: ['/accounts/'], success: 'MFA désactivé.', onSuccess: () => setCode('') });

  const m = me.data;
  return (
    <>
      <div className="pagehead"><h1>Mon compte</h1></div>
      {m && m.mfa_required && !m.mfa_enabled && (
        <div className="messagebar error">Le MFA est obligatoire sur ce serveur : active-le pour accéder au reste de l’application.</div>
      )}
      {m && (
        <section className="card">
          <div className="kv"><span>Utilisateur</span><span>{m.username}</span></div>
          <div className="kv"><span>Rôle</span><span>{ROLE[m.role]}</span></div>
          <div className="kv"><span>Vérification en deux étapes</span>
            <span>{m.mfa_enabled ? <span className="badge ok">Activée</span> : <span className="badge err">Désactivée</span>}</span></div>
        </section>
      )}

      {m && !m.mfa_enabled && (
        <section className="card">
          <h2>Activer la vérification en deux étapes</h2>
          <p className="sub">OverView a des droits étendus sur tes tenants Microsoft 365 : protège ton compte avec un code à 6 chiffres.</p>
          {!setup ? (
            <button className="primary" disabled={start.isPending} onClick={() => start.mutate()}>Commencer</button>
          ) : (
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              {qr && <img src={qr} alt="QR code à scanner" width={192} height={192} style={{ background: '#fff', borderRadius: 6 }} />}
              <div style={{ flex: 1, minWidth: 240 }}>
                <ol style={{ margin: '0 0 12px 18px', padding: 0 }}>
                  <li>Ouvre Microsoft Authenticator, Google Authenticator ou Authy.</li>
                  <li>Ajoute un compte et scanne le QR code (ou saisis la clé : <code style={{ userSelect: 'all' }}>{setup.secret}</code>).</li>
                  <li>Saisis le code affiché pour confirmer.</li>
                </ol>
                <form onSubmit={(e) => { e.preventDefault(); enable.mutate(code); }} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code"
                    placeholder="123456" maxLength={7} style={{ maxWidth: 160 }} aria-label="Code à 6 chiffres" />
                  <button type="submit" className="primary" disabled={enable.isPending}>Activer</button>
                </form>
              </div>
            </div>
          )}
        </section>
      )}

      {m && m.mfa_enabled && !m.mfa_required && (
        <section className="card">
          <h2>Désactiver la vérification en deux étapes</h2>
          <form onSubmit={(e) => { e.preventDefault(); disable.mutate(code); }} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" placeholder="Code actuel" maxLength={7}
              style={{ maxWidth: 160 }} aria-label="Code actuel" />
            <button type="submit" className="danger" disabled={disable.isPending}>Désactiver</button>
          </form>
        </section>
      )}
    </>
  );
}
