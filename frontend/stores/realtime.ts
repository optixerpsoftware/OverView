import { create } from 'zustand';

export type RealtimeStatus = 'idle' | 'connecting' | 'open' | 'closed';

interface RealtimeState {
  status: RealtimeStatus;
  lastEventAt: number | null;
  set: (patch: Partial<Pick<RealtimeState, 'status' | 'lastEventAt'>>) => void;
}

/** État de la connexion WebSocket (Daphne/Channels) : utilisé par l'indicateur « En direct » et par `useApi`
 *  pour ralentir le sondage tant que le temps réel fonctionne. */
export const useRealtimeStore = create<RealtimeState>((set) => ({
  status: 'idle',
  lastEventAt: null,
  set: (patch) => set(patch),
}));
