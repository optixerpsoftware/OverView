'use client';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { notify } from '../stores/toasts';
import { useAuthStore } from '../stores/auth';
import { useRealtimeStore } from '../stores/realtime';
import { api, errorMessage } from './api';

export interface UseApi<T> {
  /** Données en cache (null tant que rien n'est chargé, ou si l'API répond 204). */
  data: T | null;
  /** Message d'erreur lisible, vide s'il n'y en a pas. */
  error: string;
  /** Vrai pendant le tout premier chargement (pas pendant un rechargement d'arrière-plan). */
  loading: boolean;
  /** Recharge maintenant (ignore la fraîcheur du cache). */
  reload: () => Promise<void>;
}

/** Cache plus long pour les appels Microsoft Graph lents (7 à 25 s) : revenir sur une page est instantané. */
export const SLOW = 5 * 60_000;

export interface UseApiOptions<T = unknown> {
  /** Intervalle de rechargement automatique en ms (ex. suivre un traitement en cours) ; false pour arrêter. */
  refetchInterval?: (data: T | null) => number | false;
  /** Durée (ms) pendant laquelle le cache est réputé frais. 60 s par défaut ; plus long pour les appels Graph lents. */
  staleTime?: number;
  enabled?: boolean;
}

/**
 * Lecture d'une ressource de l'API via TanStack Query : cache partagé entre pages, déduplication des
 * requêtes identiques, données précédentes conservées pendant un rechargement.
 * Redirige vers /login si l'utilisateur n'est pas connecté.
 */
export function useApi<T>(path: string, options: UseApiOptions<T> = {}): UseApi<T> {
  const logged = useAuthStore((s) => !!s.access);
  const live = useRealtimeStore((s) => s.status === 'open');

  // On relit le store au moment de l'effet : pendant l'hydratation, `logged` vaut l'état initial (non
  // connecté) même si une session existe dans localStorage ; s'y fier ferait rediriger à tort vers /login.
  useEffect(() => {
    if ((options.enabled ?? true) && !useAuthStore.getState().access) window.location.href = '/login';
  }, [logged]);

  const q = useQuery<T | null, Error>({
    queryKey: ['api', path],
    queryFn: () => api<T | null>(path),
    enabled: logged && (options.enabled ?? true),
    staleTime: options.staleTime,
    // Tant que le WebSocket est ouvert, les événements du serveur déclenchent le rechargement : le sondage ne reste
    // qu'un filet de sécurité lent (5 min). Hors ligne, il reprend à son rythme normal.
    refetchInterval: (query) => {
      const ms = options.refetchInterval?.((query.state.data as T | null | undefined) ?? null) ?? false;
      return ms === false ? false : live ? Math.max(ms, 300_000) : ms;
    },
  });

  return {
    data: q.data ?? null,
    error: q.error ? errorMessage(q.error) : '',
    loading: q.isLoading,
    reload: async () => { await q.refetch(); },
  };
}

export interface MutationOptions<TData, TVars> {
  /** Préfixes de chemins dont le cache est invalidé après succès, ex. ['/organizations/', '/connectors/']. */
  invalidate?: string[];
  /** Notification de succès (texte fixe ou calculé depuis le résultat). */
  success?: string | ((data: TData, vars: TVars) => string);
  /** N'affiche pas la notification d'erreur (la page la gère elle-même). */
  silent?: boolean;
  onSuccess?: (data: TData, vars: TVars) => void;
}

/**
 * Écriture (POST/PUT/PATCH/DELETE) avec TanStack Query : invalide automatiquement les lectures concernées
 * (plus de « recharger à la main »), notifie le succès, et notifie l'erreur par défaut.
 */
export function useApiMutation<TData = unknown, TVars = void>(
  fn: (vars: TVars) => Promise<TData>,
  { invalidate = [], success, silent = false, onSuccess }: MutationOptions<TData, TVars> = {},
) {
  const qc = useQueryClient();
  return useMutation<TData, Error, TVars>({
    mutationFn: fn,
    onSuccess: async (data, vars) => {
      if (invalidate.length) {
        await qc.invalidateQueries({
          predicate: (q) => q.queryKey[0] === 'api' && invalidate.some((p) => String(q.queryKey[1]).startsWith(p)),
        });
      }
      if (success) notify.ok(typeof success === 'function' ? success(data, vars) : success);
      onSuccess?.(data, vars);
    },
    onError: (e) => { if (!silent) notify.error(errorMessage(e)); },
  });
}
