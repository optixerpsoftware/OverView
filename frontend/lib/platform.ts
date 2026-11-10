/** Adresse de la plateforme (console des MSP) : platform.<domaine>. */
export const isPlatformHost = (): boolean =>
  typeof window !== 'undefined' && window.location.hostname.split('.')[0] === 'platform';

/** Page de connexion adaptée : console de la plateforme ou espace d'un MSP. */
export const loginPath = (): string => (isPlatformHost() ? '/platform/login' : '/login');
