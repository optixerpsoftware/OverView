'use client';
import { useEffect } from 'react';
import { startRealtime, stopRealtime } from '../lib/realtime';
import { useAuthStore } from '../stores/auth';

/** Ouvre le WebSocket quand l'utilisateur est connecté, le ferme à la déconnexion. Ne rend rien. */
export default function RealtimeBridge() {
  const logged = useAuthStore((s) => !!s.access);

  useEffect(() => {
    // Relu dans l'effet (et non via `logged`) : pendant l'hydratation, le store renvoie l'état initial.
    if (useAuthStore.getState().access) startRealtime();
    else stopRealtime();
  }, [logged]);

  useEffect(() => () => stopRealtime(), []);
  return null;
}
