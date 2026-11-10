'use client';
import { useState, type FormEvent } from 'react';
import { API_BASE } from '../../../lib/api';
import { queryClient } from '../../../lib/queryClient';
import { useAuthStore } from '../../../stores/auth';

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_BASE}/api/platform/auth/${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ?? `Erreur ${r.status}`);
  return data as T;
}

// Connexion à la console de la plateforme : mot de passe puis code TOTP (toujours exigé).
export default function PlatformLogin() {
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function step1(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true); setError('');
    try {
      const r = await post<{ mfa_token: string }>('login/', { username: f.get('username'), password: f.get('password') });
      setToken(r.mfa_token);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); } finally { setBusy(false); }
  }

  async function step2(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true); setError('');
    try {
      const r = await post<{ access: string; refresh: string }>('verify/', { mfa_token: token, code: f.get('code') });
      queryClient.clear();
      useAuthStore.getState().setTokens(r.access, r.refresh);
      window.location.href = '/platform';
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      if (/expir/i.test(String(err))) setToken(null);
    } finally { setBusy(false); }
  }

  return (
    <div className="signin">
      <div className="box">
        <div className="logo">▦ OverView · Plateforme</div>
        {!token ? (
          <>
            <h1>Console de la plateforme</h1>
            <p className="sub" style={{ marginTop: -8 }}>Réservée aux administrateurs de la plateforme. Les comptes des MSP se connectent sur l’adresse de leur MSP.</p>
            <form onSubmit={step1} className="form" style={{ maxWidth: 'none' }}>
              <input name="username" placeholder="Nom d'utilisateur" autoComplete="username" required />
              <input name="password" type="password" placeholder="Mot de passe" autoComplete="current-password" required />
              {error && <div className="messagebar error">{error}</div>}
              <div><button type="submit" className="primary" disabled={busy}>Suivant</button></div>
            </form>
          </>
        ) : (
          <>
            <h1>Vérification en deux étapes</h1>
            <form onSubmit={step2} className="form" style={{ maxWidth: 'none' }}>
              <input name="code" inputMode="numeric" autoComplete="one-time-code" maxLength={7} placeholder="123 456" required autoFocus
                style={{ fontSize: 22, letterSpacing: '.2em', textAlign: 'center' }} />
              {error && <div className="messagebar error">{error}</div>}
              <div style={{ display: 'flex', gap: 8 }}>
                <button type="submit" className="primary" disabled={busy}>Vérifier</button>
                <button type="button" className="ghost" onClick={() => { setToken(null); setError(''); }}>Retour</button>
              </div>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
