import { queryClient } from './queryClient';
import { useAuthStore } from '../stores/auth';

// Adresse de l'API : celle imposée à la compilation si elle existe, sinon la même machine que la page, port 8000.
// (Une adresse « localhost » figée à la compilation cassait l'accès depuis un autre poste du réseau.)
const BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.hostname}:8000` : 'http://localhost:8000');

/** URL de base de l'API (aussi utilisée pour dériver l'adresse du WebSocket). */
export const API_BASE = BASE;

/** Erreur renvoyée par l'API : garde le code HTTP (utile pour décider d'un nouvel essai). */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

export const isLoggedIn = (): boolean => !!useAuthStore.getState().access;

/** Déconnexion : efface la session ET le cache des données (rien ne doit fuiter vers le prochain utilisateur). */
export function logout(): void {
  useAuthStore.getState().clear();
  queryClient.clear();
}

async function authPost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}/api/auth/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(r.status, (data && typeof data === 'object' && 'detail' in data) ? String(data.detail) : 'Identifiants invalides');
  return data as T;
}

function startSession(data: { access: string; refresh: string }) {
  queryClient.clear();
  useAuthStore.getState().setTokens(data.access, data.refresh);
}

/** Étape 1 : mot de passe. Renvoie un jeton de défi si le compte a le MFA activé (étape 2 : `verifyMfa`). */
export async function login(username: string, password: string): Promise<{ mfaToken?: string }> {
  const data = await authPost<{ access?: string; refresh?: string; mfa_required?: boolean; mfa_token?: string }>(
    'login/', { username, password });
  if (data.mfa_required && data.mfa_token) return { mfaToken: data.mfa_token };
  startSession(data as { access: string; refresh: string });
  return {};
}

/** Étape 2 : code à 6 chiffres de l'application d'authentification. */
export async function verifyMfa(mfaToken: string, code: string): Promise<void> {
  startSession(await authPost<{ access: string; refresh: string }>('mfa/verify/', { mfa_token: mfaToken, code }));
}

/** Obtient un nouveau jeton d'accès depuis le jeton de rafraîchissement ; faux si la session est expirée. */
export async function refreshAccessToken(): Promise<boolean> {
  const token = useAuthStore.getState().refresh;
  if (!token) return false;
  const r = await fetch(`${BASE}/api/auth/token/refresh/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh: token }),
  });
  if (!r.ok) return false;
  const data: { access: string } = await r.json();
  useAuthStore.getState().setAccess(data.access);
  return true;
}

export interface ApiOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
}

export async function api<T = unknown>(
  path: string,
  { method = 'GET', body }: ApiOptions = {},
  retry = true,
): Promise<T> {
  const r = await fetch(`${BASE}/api${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${useAuthStore.getState().access ?? ''}`,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401 && retry && (await refreshAccessToken())) return api<T>(path, { method, body }, false);
  if (r.status === 401) {
    logout();
    if (typeof window !== 'undefined') window.location.href = '/login';
    throw new ApiError(401, 'Session expirée');
  }
  if (r.status === 204) return null as T;
  const data: unknown = await r.json().catch(() => null);
  if (r.status === 403 && data && typeof data === 'object' && (data as { code?: string }).code === 'mfa_enrollment_required'
      && typeof window !== 'undefined' && window.location.pathname !== '/account') {
    window.location.href = '/account';   // MFA obligatoire : inscription d'abord
  }
  if (!r.ok) {
    const detail =
      data && typeof data === 'object' && 'detail' in data
        ? String((data as { detail: unknown }).detail)
        : data
          ? JSON.stringify(data)
          : `Erreur ${r.status}`;
    throw new ApiError(r.status, detail);
  }
  return data as T;
}

export const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e));
