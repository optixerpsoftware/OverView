'use client';
import { useState, type FormEvent } from 'react';
import { errorMessage, login, verifyMfa } from '../../lib/api';

export default function LoginPage() {
  const [error, setError] = useState('');
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onPassword(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError('');
    try {
      const r = await login(String(f.get('username')), String(f.get('password')));
      if (r.mfaToken) setMfaToken(r.mfaToken);
      else window.location.href = '/';
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function onCode(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError('');
    try {
      await verifyMfa(mfaToken as string, String(f.get('code')));
      window.location.href = '/';
    } catch (err) {
      setError(errorMessage(err));
      if (/expir/i.test(errorMessage(err))) setMfaToken(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="signin">
      <div className="box">
        <div className="logo">▦ OverView</div>
        {!mfaToken ? (
          <>
            <h1>Se connecter</h1>
            <form onSubmit={onPassword} className="form" style={{ maxWidth: 'none' }}>
              <input name="username" placeholder="Nom d'utilisateur" autoComplete="username" required />
              <input name="password" type="password" placeholder="Mot de passe" autoComplete="current-password" required />
              {error && <div className="messagebar error">{error}</div>}
              <div><button type="submit" className="primary" style={{ minWidth: 108 }} disabled={busy}>Suivant</button></div>
            </form>
          </>
        ) : (
          <>
            <h1>Vérification en deux étapes</h1>
            <p className="sub" style={{ marginTop: -8 }}>Saisis le code à 6 chiffres de ton application d’authentification.</p>
            <form onSubmit={onCode} className="form" style={{ maxWidth: 'none' }}>
              <input name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7}
                placeholder="123 456" required autoFocus style={{ fontSize: 22, letterSpacing: '.2em', textAlign: 'center' }} />
              {error && <div className="messagebar error">{error}</div>}
              <div style={{ display: 'flex', gap: 8 }}>
                <button type="submit" className="primary" disabled={busy}>Vérifier</button>
                <button type="button" className="ghost" onClick={() => { setMfaToken(null); setError(''); }}>Retour</button>
              </div>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
