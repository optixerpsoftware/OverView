import { API_BASE, refreshAccessToken } from './api';
import { queryClient } from './queryClient';
import { useAuthStore } from '../stores/auth';
import { useRealtimeStore } from '../stores/realtime';
import { notify } from '../stores/toasts';

/**
 * Client WebSocket (Daphne + Channels) : le serveur signale « ce compte vient d'être synchronisé », « l'audit de
 * partage est terminé »… et on invalide les lectures concernées dans le cache TanStack Query, qui les recharge.
 * Fini le sondage toutes les 3 s : la page se met à jour à l'instant où le worker termine.
 *
 * Robustesse : reconnexion automatique (attente exponentielle avec aléa), jeton renouvelé quand le serveur
 * ferme avec le code 4401 (expiration), battement de cœur, et rechargement des lectures actives à chaque
 * (re)connexion pour rattraper les événements manqués.
 */

const AUTH_FAILED = 4401;
const HEARTBEAT_MS = 25_000;
const MAX_BACKOFF_MS = 30_000;

let socket: WebSocket | null = null;
let retryTimer: ReturnType<typeof setTimeout> | undefined;
let heartbeatTimer: ReturnType<typeof setInterval> | undefined;
let attempts = 0;
let running = false;

const wsUrl = (token: string) => `${API_BASE.replace(/^http/, 'ws')}/ws/events/?token=${encodeURIComponent(token)}`;
const setStatus = (status: 'idle' | 'connecting' | 'open' | 'closed') => useRealtimeStore.getState().set({ status });

/** Invalide (donc recharge, si affichées) les lectures dont le chemin commence par `prefix`. */
function invalidate(prefix: string) {
  void queryClient.invalidateQueries({
    predicate: (q) => q.queryKey[0] === 'api' && String(q.queryKey[1]).startsWith(prefix),
  });
}

interface ServerEvent {
  topic: string;
  data: Record<string, unknown>;
}

export function handleEvent(ev: ServerEvent): void {
  const d = ev.data;
  switch (ev.topic) {
    case 'security.sync':
      invalidate(`/identity/mailboxes/${String(d.mailbox_id)}/security`);
      if (d.status === 'failed') notify.error('La synchronisation d’un compte a échoué.');
      break;
    case 'sharing.scan':
      invalidate(`/onedrive/connectors/${String(d.connector_id)}/sharing`);
      if (d.status === 'done') { notify.ok('Audit des liens de partage terminé.'); invalidate('/insights/'); }
      if (d.status === 'failed') notify.error('L’audit des liens de partage a échoué.');
      break;
    case 'compliance.scan':
      invalidate(`/compliance/connectors/${String(d.connector_id)}`);
      break;
    case 'hygiene.scan':
      invalidate(`/identity/connectors/${String(d.connector_id)}/hygiene`);
      break;
    case 'protection.event':
      invalidate('/protection/');
      break;
    case 'alerts.updated':
      invalidate('/insights/');
      break;
    case 'mailflow.quarantine':
      invalidate(`/outlook/connectors/${String(d.connector_id)}/quarantine`);
      break;
    default:
      break;   // ready, pong, auth.expired : sans effet sur le cache
  }
}

function clearTimers() {
  clearTimeout(retryTimer);
  clearInterval(heartbeatTimer);
}

function scheduleReconnect() {
  if (!running) return;
  attempts += 1;
  const base = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** (attempts - 1));
  retryTimer = setTimeout(connect, base / 2 + Math.random() * (base / 2));   // aléa : évite les reconnexions en masse
}

function connect() {
  if (!running) return;
  const token = useAuthStore.getState().access;
  if (!token) {
    setStatus('idle');
    return;
  }
  setStatus('connecting');
  const ws = new WebSocket(wsUrl(token));
  socket = ws;

  ws.onopen = () => {
    attempts = 0;
    setStatus('open');
    // Les événements publiés pendant une coupure sont perdus : on recharge les lectures actives.
    void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'api' });
    clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
    }, HEARTBEAT_MS);
  };

  ws.onmessage = (msg) => {
    try {
      const ev = JSON.parse(String(msg.data)) as ServerEvent;
      useRealtimeStore.getState().set({ lastEventAt: Date.now() });
      handleEvent(ev);
    } catch {
      /* message illisible : ignoré */
    }
  };

  ws.onclose = async (e) => {
    clearInterval(heartbeatTimer);
    if (socket === ws) socket = null;
    if (!running) return;
    setStatus('closed');
    if (e.code === AUTH_FAILED) {
      // Jeton expiré ou refusé : on en obtient un nouveau, puis on se reconnecte tout de suite.
      if (await refreshAccessToken()) connect();
      else scheduleReconnect();     // session expirée : la prochaine requête API ramènera à /login
      return;
    }
    scheduleReconnect();
  };

  ws.onerror = () => ws.close();
}

export function startRealtime(): void {
  if (running) return;
  running = true;
  attempts = 0;
  connect();
}

export function stopRealtime(): void {
  running = false;
  clearTimers();
  const ws = socket;
  socket = null;
  ws?.close(1000);
  setStatus('idle');
}
