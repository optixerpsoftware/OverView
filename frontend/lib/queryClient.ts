import { QueryClient } from '@tanstack/react-query';

/**
 * Client TanStack Query unique (singleton navigateur), partagé par <Providers/> et par `logout()`
 * qui doit pouvoir vider le cache.
 *
 * Réglages choisis pour des appels Microsoft Graph lents et coûteux (7 à 25 s) :
 * - pas de rechargement automatique quand la fenêtre reprend le focus ;
 * - données considérées fraîches 60 s (naviguer d'une page à l'autre est instantané) ;
 * - un seul nouvel essai, et jamais sur une erreur 4xx (droits, validation : réessayer ne servirait à rien).
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        const status = (error as { status?: number }).status;
        if (status !== undefined && status < 500) return false;
        return failureCount < 1;
      },
    },
  },
});
