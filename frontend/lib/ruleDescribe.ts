// Traduit les conditions/actions d'une règle Outlook (format Microsoft Graph) en phrases lisibles, et repère
// les actions typiques d'une règle malveillante (transfert, redirection, suppression silencieuse).

type Json = Record<string, unknown> | null;
export interface Line { text: string; risk?: boolean }

interface Addr { emailAddress?: { name?: string; address?: string } }
const addr = (a: Addr) => {
  const e = a.emailAddress;
  return e?.name && e.address && e.name !== e.address ? `${e.name} <${e.address}>` : e?.address ?? e?.name ?? '?';
};
const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
const quoted = (v: unknown) => list(v).map((s) => `« ${s} »`).join(', ');
const addrs = (v: unknown) => (Array.isArray(v) ? (v as Addr[]).map(addr).join(', ') : '');

const IMPORTANCE: Record<string, string> = { low: 'basse', normal: 'normale', high: 'haute' };
const SENSITIVITY: Record<string, string> = { normal: 'normale', personal: 'personnelle', private: 'privée', confidential: 'confidentielle' };

type Handler = (v: unknown) => string | null;
const CONDITIONS: Record<string, Handler> = {
  fromAddresses: (v) => `L’expéditeur est ${addrs(v)}`,
  sentToAddresses: (v) => `Envoyé à ${addrs(v)}`,
  recipientContains: (v) => `L’adresse d’un destinataire contient ${quoted(v)}`,
  senderContains: (v) => `L’adresse de l’expéditeur contient ${quoted(v)}`,
  subjectContains: (v) => `L’objet contient ${quoted(v)}`,
  bodyContains: (v) => `Le corps du message contient ${quoted(v)}`,
  bodyOrSubjectContains: (v) => `L’objet ou le corps contient ${quoted(v)}`,
  headerContains: (v) => `L’en-tête contient ${quoted(v)}`,
  hasAttachments: (v) => (v ? 'Le message a une pièce jointe' : 'Le message n’a pas de pièce jointe'),
  importance: (v) => `Importance ${IMPORTANCE[String(v)] ?? String(v)}`,
  sensitivity: (v) => `Confidentialité ${SENSITIVITY[String(v)] ?? String(v)}`,
  isAutomaticReply: (v) => (v ? 'C’est une réponse automatique' : null),
  isAutomaticForward: (v) => (v ? 'C’est un transfert automatique' : null),
  isApprovalRequest: (v) => (v ? 'C’est une demande d’approbation' : null),
  isMeetingRequest: (v) => (v ? 'C’est une invitation à une réunion' : null),
  isMeetingResponse: (v) => (v ? 'C’est une réponse à une réunion' : null),
  isNonDeliveryReport: (v) => (v ? 'C’est un accusé de non-remise' : null),
  isPermissionControlled: (v) => (v ? 'Le message est protégé par des droits' : null),
  isReadReceipt: (v) => (v ? 'C’est un accusé de lecture' : null),
  isSigned: (v) => (v ? 'Le message est signé' : null),
  isVoicemail: (v) => (v ? 'C’est un message vocal' : null),
  sentOnlyToMe: (v) => (v ? 'Envoyé uniquement à moi' : null),
  sentToMe: (v) => (v ? 'Envoyé à moi' : null),
  sentCcMe: (v) => (v ? 'Je suis en copie' : null),
  sentToOrCcMe: (v) => (v ? 'Envoyé à moi ou en copie' : null),
  notSentToMe: (v) => (v ? 'Pas envoyé à moi' : null),
  withinSizeRange: (v) => {
    const r = v as { minimumSize?: number; maximumSize?: number };
    return `Taille entre ${r?.minimumSize ?? 0} et ${r?.maximumSize ?? '∞'} Ko`;
  },
  categories: (v) => `Catégorie ${quoted(v)}`,
  messageActionFlag: (v) => `Indicateur de suivi « ${String(v)} »`,
};

const folder = (name: unknown, id: unknown) => (name ? `« ${String(name)} »` : `(ID …${String(id).slice(-12)})`);

const ACTIONS: Record<string, (v: unknown, all?: Record<string, unknown>) => Line | null> = {
  moveToFolder: (v, a) => ({ text: `Déplacer vers le dossier ${folder(a?.moveToFolderName, v)}` }),
  copyToFolder: (v, a) => ({ text: `Copier vers le dossier ${folder(a?.copyToFolderName, v)}` }),
  moveToFolderName: () => null,
  copyToFolderName: () => null,
  delete: (v) => (v ? { text: 'Déplacer dans « Éléments supprimés »', risk: true } : null),
  permanentDelete: (v) => (v ? { text: 'Supprimer définitivement (sans possibilité de récupération)', risk: true } : null),
  markAsRead: (v) => (v ? { text: 'Marquer comme lu', risk: true } : null),
  markImportance: (v) => ({ text: `Définir l’importance : ${IMPORTANCE[String(v)] ?? String(v)}` }),
  assignCategories: (v) => ({ text: `Attribuer la catégorie ${quoted(v)}` }),
  forwardTo: (v) => ({ text: `Transférer à ${addrs(v)}`, risk: true }),
  forwardAsAttachmentTo: (v) => ({ text: `Transférer en pièce jointe à ${addrs(v)}`, risk: true }),
  redirectTo: (v) => ({ text: `Rediriger vers ${addrs(v)}`, risk: true }),
  stopProcessingRules: (v) => (v ? { text: 'Arrêter le traitement des règles suivantes' } : null),
};

function describe<T>(obj: Json, table: Record<string, (v: unknown, all?: Record<string, unknown>) => T | null>, fallback: (k: string) => T): T[] {
  const out: T[] = [];
  for (const [k, v] of Object.entries(obj ?? {})) {
    if (v === null || v === undefined || v === false || (Array.isArray(v) && v.length === 0)) continue;
    const h = table[k];
    const r = h ? h(v, obj ?? undefined) : fallback(k);
    if (r !== null) out.push(r);
  }
  return out;
}

export const describeConditions = (c: Json): string[] =>
  describe<string>(c, CONDITIONS, (k) => `Condition : ${k}`);

export const describeActions = (a: Json): Line[] =>
  describe<Line>(a, ACTIONS, (k) => ({ text: `Action : ${k}` }));

export const isSuspicious = (a: Json): boolean => describeActions(a).some((l) => l.risk);
