'use client';
import { useEffect, useState, type FormEvent } from 'react';
import { API_BASE } from '../../lib/api';

interface Info { signup_open: boolean; base_domain: string }

// Inscription d'un MSP (adresse de la plateforme, ex. platform.overview.ca) : crée son espace OverView isolé.
export default function SignupPage() {
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [slug, setSlug] = useState('');
  const [done, setDone] = useState<{ domain: string; name: string } | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/platform/info/`).then((r) => (r.ok ? r.json() : null)).then(setInfo).catch(() => setInfo(null));
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError('');
    try {
      const r = await fetch(`${API_BASE}/api/platform/signup/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.fromEntries(f.entries())),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail ?? `Erreur ${r.status}`);
      setDone({ domain: data.domain, name: data.name });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const port = typeof window !== 'undefined' && window.location.port ? `:${window.location.port}` : '';
  const proto = typeof window !== 'undefined' ? window.location.protocol : 'https:';
  return (
    <div className="signin">
      <div className="box">
        <div className="logo">▦ OverView</div>
        {done ? (
          <>
            <h1>Espace créé</h1>
            <p>L’espace <strong>{done.name}</strong> est prêt.</p>
            <a className="btn" href={`${proto}//${done.domain}${port}/login`}>Se connecter à {done.domain}</a>
          </>
        ) : info && !info.signup_open ? (
          <>
            <h1>Inscriptions fermées</h1>
            <p className="sub">Contacte l’éditeur d’OverView pour ouvrir un espace MSP.</p>
          </>
        ) : (
          <>
            <h1>Créer l’espace de ton MSP</h1>
            <form onSubmit={onSubmit} className="form" style={{ maxWidth: 'none' }}>
              <input name="name" placeholder="Nom du MSP (ex. Acme TI)" required />
              <input name="slug" placeholder="Identifiant (ex. acme)" required pattern="[a-z][a-z0-9-]{1,30}[a-z0-9]"
                value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} />
              <div className="sub" style={{ marginTop: -6 }}>Ton adresse : {slug || 'acme'}.{info?.base_domain ?? '…'}</div>
              <input name="admin_username" placeholder="Nom d’utilisateur de l’administrateur" autoComplete="username" required />
              <input name="admin_email" type="email" placeholder="Courriel (facultatif)" />
              <input name="admin_password" type="password" placeholder="Mot de passe (12 caractères ou plus)" autoComplete="new-password" required minLength={12} />
              {error && <div className="messagebar error">{error}</div>}
              <div><button type="submit" className="primary" disabled={busy}>{busy ? 'Création de l’espace… (quelques secondes)' : 'Créer l’espace'}</button></div>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
