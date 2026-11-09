'use client';
import { useState, type FormEvent } from 'react';
import { errorMessage, login } from '../../lib/api';

export default function LoginPage() {
  const [error, setError] = useState('');

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    try {
      await login(String(f.get('username')), String(f.get('password')));
      window.location.href = '/';
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="signin">
      <div className="box">
        <div className="logo">▦ OverView</div>
        <h1>Se connecter</h1>
        <form onSubmit={onSubmit} className="form" style={{ maxWidth: 'none' }}>
          <input name="username" placeholder="Nom d'utilisateur" autoComplete="username" required />
          <input name="password" type="password" placeholder="Mot de passe" autoComplete="current-password" required />
          {error && <div className="messagebar error">{error}</div>}
          <div><button type="submit" className="primary" style={{ minWidth: 108 }}>Suivant</button></div>
        </form>
      </div>
    </div>
  );
}
