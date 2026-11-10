'use client';
import Link from 'next/link';
import { use, useEffect, useState } from 'react';
import ConfirmDialog from '../../../components/ConfirmDialog';
import CreateGroupModal from '../../../components/CreateGroupModal';
import { api, errorMessage } from '../../../lib/api';
import type {
  Connector, DeviceRegistrationSettings as Settings, DirectoryGroup, Note, SettingChange,
} from '../../../lib/types';
import { SLOW, useApi, useApiMutation } from '../../../lib/useApi';
import Breadcrumb from '../../../components/Breadcrumb';

const SCOPE_FR = { all: 'Tous les utilisateurs', none: 'Personne', selected: 'Une sélection de groupes' } as const;
const GUID = /^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/;

type ChangeBody = Partial<{
  user_device_quota: number; mfa_required: boolean; join_scope: 'all' | 'none' | 'selected';
  join_groups: string[]; register_scope: 'all' | 'none'; laps_enabled: boolean;
}>;

const fmt = (iso: string) => new Date(iso).toLocaleString();
const groupLabel = (g: DirectoryGroup) => g.name ?? `${g.id.slice(0, 8)}… (nom illisible)`;

export default function DeviceRegistrationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const base = `/identity/connectors/${id}/device-registration/`;
  const connector = useApi<Connector>(`/connectors/${id}/`);
  const current = useApi<Settings>(base);
  const history = useApi<SettingChange[]>(`${base}history/`);
  const directory = useApi<DirectoryGroup[]>(`/identity/connectors/${id}/groups/`, { staleTime: SLOW });  // 502 si Group.Read.All manque
  const [form, setForm] = useState<Settings | null>(null);
  const [groups, setGroups] = useState<DirectoryGroup[]>([]);       // groupes autorisés à joindre (sélection)
  const [guidInput, setGuidInput] = useState('');
  const [note, setNote] = useState<Note | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [creatingGroup, setCreatingGroup] = useState(false);

  // Le formulaire repart des réglages lus chez Microsoft à chaque rechargement.
  useEffect(() => {
    if (current.data) {
      setForm(current.data);
      setGroups(current.data.join_groups_detail ?? []);
    }
  }, [current.data]);

  const cur = current.data;
  const curGroupIds = (cur?.join_group_ids ?? []).slice().sort().join(',');
  const newGroupIds = groups.map((g) => g.id).sort().join(',');
  const selectionEmpty = form?.join_scope === 'selected' && groups.length === 0;

  const changes: ChangeBody = {};
  const lines: string[] = [];
  if (cur && form) {
    if (form.user_device_quota !== cur.user_device_quota && form.user_device_quota !== null) {
      changes.user_device_quota = form.user_device_quota;
      lines.push(`Quota d'appareils par utilisateur : ${cur.user_device_quota} → ${form.user_device_quota}`);
    }
    if (form.mfa_required !== cur.mfa_required) {
      changes.mfa_required = form.mfa_required;
      lines.push(`MFA exigé à l'inscription : ${cur.mfa_required ? 'oui' : 'non'} → ${form.mfa_required ? 'oui' : 'non'}`);
    }
    if (form.join_scope === 'selected') {
      if (cur.join_scope !== 'selected' || curGroupIds !== newGroupIds) {
        changes.join_scope = 'selected';
        changes.join_groups = groups.map((g) => g.id);
        lines.push(`Qui peut joindre des appareils : ${SCOPE_FR[cur.join_scope]} → seulement ${groups.map(groupLabel).join(', ') || '(aucun groupe)'}`);
      }
    } else if (form.join_scope !== cur.join_scope) {
      changes.join_scope = form.join_scope;
      lines.push(`Qui peut joindre des appareils : ${SCOPE_FR[cur.join_scope]} → ${SCOPE_FR[form.join_scope]}`);
    }
    if (form.laps_enabled !== cur.laps_enabled) {
      changes.laps_enabled = form.laps_enabled;
      lines.push(`Mot de passe administrateur local (LAPS) : ${cur.laps_enabled ? 'oui' : 'non'} → ${form.laps_enabled ? 'oui' : 'non'}`);
    }
  }
  const dirty = lines.length > 0 && !selectionEmpty;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  function addGroup(g: DirectoryGroup) {
    if (!groups.some((x) => x.id === g.id.toLowerCase())) setGroups([...groups, { id: g.id.toLowerCase(), name: g.name }]);
  }

  function addGuid() {
    const v = guidInput.trim();
    if (!GUID.test(v)) { setNote({ text: 'Identifiant de groupe invalide (format GUID attendu).', error: true }); return; }
    setNote(null);
    addGroup({ id: v, name: directory.data?.find((g) => g.id.toLowerCase() === v.toLowerCase())?.name ?? null });
    setGuidInput('');
  }

  // Mutation TanStack : invalide la politique et l'historique après écriture ; l'erreur (permission, validation)
  // est affichée par la page et l'historique est rechargé car l'échec y est aussi journalisé.
  const saveMutation = useApiMutation(
    (confirm: string) => api<{ settings: Settings; changes: string[] }>(base, { method: 'PUT', body: { changes, confirm } }),
    {
      invalidate: [base],
      silent: true,
      onSuccess: (r) => {
        setNote({ text: r.changes.length ? `Réglages enregistrés chez Microsoft : ${r.changes.length} modification(s).` : 'Aucune modification.', error: false });
        setConfirming(false);
      },
    },
  );
  const saving = saveMutation.isPending;

  function save(confirm: string) {
    saveMutation.mutate(confirm, {
      onError: (e) => {
        setNote({ text: errorMessage(e), error: true });
        setConfirming(false);
        void history.reload();
      },
    });
  }

  const available = (directory.data ?? []).filter((g) => !groups.some((x) => x.id === g.id.toLowerCase()));

  const groupEditor = form?.join_scope === 'selected' && (
    <div className="group-editor">
      <div className="sub" style={{ marginBottom: 6 }}>
        Seuls les membres de ces groupes pourront joindre un appareil. Tous les autres seront bloqués.
      </div>
      <div className="chip-group" style={{ marginBottom: 8 }}>
        {groups.length === 0 && <span className="badge err">Aucun groupe : ajoutes-en au moins un</span>}
        {groups.map((g) => (
          <span key={g.id} className="badge ok" style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            {groupLabel(g)}
            <button className="ghost" style={{ minHeight: 0, padding: 0, lineHeight: 1 }} aria-label={`Retirer ${groupLabel(g)}`}
              onClick={() => setGroups(groups.filter((x) => x.id !== g.id))}>✕</button>
          </span>
        ))}
      </div>
      {directory.data ? (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <select style={{ flex: 1, minWidth: 220 }} value=""
            onChange={(e) => { const g = available.find((x) => x.id === e.target.value); if (g) addGroup(g); }}>
            <option value="">{available.length ? 'Ajouter un groupe de sécurité…' : 'Aucun groupe de sécurité disponible'}</option>
            {available.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
          <button onClick={() => setCreatingGroup(true)}>Créer un groupe…</button>
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 8 }}>
            <input placeholder="ID de l’objet du groupe (GUID)" value={guidInput} onChange={(e) => setGuidInput(e.target.value)} />
            <button onClick={addGuid}>Ajouter</button>
          </div>
          <div className="sub" style={{ marginTop: 4 }}>
            Colle l’<em>ID de l’objet</em> du groupe (centre d’administration Entra, Groupes). Pour choisir dans une liste, ajoute la permission
            applicative <code>Group.Read.All</code>.
          </div>
        </>
      )}
    </div>
  );

  return (
    <>
      <Breadcrumb current="Inscription des appareils" />
      <header className="hero">
        <div className="avatar-lg" aria-hidden="true">💻</div>
        <div className="hero-id">
          <h1>Inscription des appareils</h1>
          <div className="sub">{connector.data?.name ?? '…'} · politique d’appairement Microsoft Entra ID</div>
          {cur && (
            <div className="chips">
              <span className={`badge ${cur.join_scope === 'none' ? 'ok' : cur.join_scope === 'all' ? 'err' : 'warn'}`}>
                Jonction par les utilisateurs : {cur.join_scope === 'none' ? 'bloquée' : cur.join_scope === 'all' ? 'ouverte à tous'
                  : `limitée à ${cur.join_groups} groupe${cur.join_groups > 1 ? 's' : ''}${cur.join_users ? ` et ${cur.join_users} utilisateur${cur.join_users > 1 ? 's' : ''}` : ''}`}
              </span>
              <span className={`badge ${cur.mfa_required ? 'ok' : 'off'}`}>MFA à l’appairement : {cur.mfa_required ? 'exigé' : 'non exigé'}</span>
            </div>
          )}
        </div>
        <div className="hero-actions">
          <button className="primary" disabled={!dirty || saving} onClick={() => setConfirming(true)}>
            Enregistrer{dirty ? ` (${lines.length})` : ''}
          </button>
        </div>
      </header>

      {note && <div className={`messagebar ${note.error ? 'error' : ''}`}>{note.text}</div>}
      {current.error && <div className="messagebar error">{current.error}</div>}

      <section className="card">
        <h2>Réglages du tenant</h2>
        {!form && !current.error && <div className="empty">Lecture de la politique chez Microsoft…</div>}
        {form && cur && (
          <div className="messagebar" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 260 }}>
              <strong>Verrouillage rapide</strong> — empêche les utilisateurs de joindre eux-mêmes un PC au tenant (Paramètres,
              lien de jonction, première configuration de Windows) et exige le MFA. Les appareils déjà joints continuent de fonctionner.
            </div>
            <button disabled={!cur.join_configurable}
              onClick={() => { setForm({ ...form, join_scope: 'selected', mfa_required: true }); }}>
              Bloquer sauf un groupe TI
            </button>
            <button disabled={!cur.join_configurable}
              onClick={() => { setForm({ ...form, join_scope: 'none', mfa_required: true }); }}>
              Bloquer pour tous
            </button>
          </div>
        )}
        {form && cur && (
          <div className="settings">
            <div className="setting">
              <div><strong>Qui peut joindre des appareils à Entra ID</strong>
                <div className="sub">Jonction d’un PC Windows au tenant : « Accéder au Travail ou à l’École », lien de jonction, première
                  configuration. « Personne » bloque tout le monde, administrateurs compris ; « Une sélection de groupes » laisse passer
                  seulement les groupes choisis (ex. le groupe TI).</div></div>
              <div>
                <select value={form.join_scope} disabled={!cur.join_configurable}
                  onChange={(e) => set('join_scope', e.target.value as Settings['join_scope'])}>
                  <option value="all">{SCOPE_FR.all}</option>
                  <option value="none">{SCOPE_FR.none}</option>
                  <option value="selected">{SCOPE_FR.selected}</option>
                </select>
                {!cur.join_configurable && <div className="sub">Non modifiable par un administrateur sur ce tenant.</div>}
                {cur.join_users > 0 && form.join_scope === 'selected' && (
                  <div className="sub">{cur.join_users} utilisateur(s) nommé(s) dans la sélection actuelle sont conservés.</div>
                )}
              </div>
            </div>
            {form.join_scope === 'selected' && <div className="setting" style={{ gridTemplateColumns: '1fr' }}>{groupEditor}</div>}
            <div className="setting">
              <div><strong>Qui peut enregistrer des appareils</strong>
                <div className="sub">Appareils personnels enregistrés (BYOD) sans être joints. Sur les tenants récents, Microsoft garde ce réglage
                  toujours ouvert : pour le restreindre, il faut l’accès conditionnel ou des restrictions d’inscription Intune (licence supérieure).</div></div>
              <div>
                <select value={form.register_scope} disabled>
                  <option value="all">{SCOPE_FR.all}</option>
                  <option value="none">{SCOPE_FR.none}</option>
                  <option value="selected">Une sélection</option>
                </select>
                <div className="sub">Non modifiable par un administrateur sur ce tenant.</div>
              </div>
            </div>
            <div className="setting">
              <div><strong>Exiger le MFA pour joindre ou enregistrer</strong>
                <div className="sub">Une seconde vérification est demandée lors de l’appairement. Avec Security Defaults activé, le MFA est
                  déjà imposé aux connexions ; ce réglage le rend explicite pour la jonction et l’enregistrement.</div></div>
              <label className="switch"><input type="checkbox" checked={form.mfa_required}
                onChange={(e) => set('mfa_required', e.target.checked)} /> {form.mfa_required ? 'Oui' : 'Non'}</label>
            </div>
            <div className="setting">
              <div><strong>Quota d’appareils par utilisateur</strong>
                <div className="sub">Nombre maximal d’appareils joints ou enregistrés par utilisateur (1 à 100).</div></div>
              <input type="number" min={1} max={100} style={{ maxWidth: 120 }} value={form.user_device_quota ?? ''}
                onChange={(e) => set('user_device_quota', e.target.value === '' ? null : Number(e.target.value))} />
            </div>
            <div className="setting">
              <div><strong>Mot de passe administrateur local (LAPS)</strong>
                <div className="sub">Entra ID gère et fait tourner le mot de passe de l’administrateur local des appareils joints.</div></div>
              <label className="switch"><input type="checkbox" checked={form.laps_enabled}
                onChange={(e) => set('laps_enabled', e.target.checked)} /> {form.laps_enabled ? 'Oui' : 'Non'}</label>
            </div>
          </div>
        )}
        <div className="sub" style={{ marginTop: 12 }}>
          Ces réglages s’appliquent aux <strong>nouveaux</strong> appairements ; les appareils déjà joints ne sont pas modifiés.
          La modification demande la permission <code>Policy.ReadWrite.DeviceConfiguration</code> (consentement administrateur).
        </div>
      </section>

      <section className="card">
        <h2>Historique des modifications</h2>
        {history.data && history.data.length === 0 && <div className="empty">Aucune modification faite depuis l’app.</div>}
        {(history.data ?? []).map((h) => (
          <div className="line" key={h.id}>
            <span className={`badge ${h.ok ? 'ok' : 'err'}`}>{h.ok ? 'Appliqué' : 'Échec'}</span>
            <div className="grow">
              {h.changes.map((c, i) => <div key={i}>{c}</div>)}
              {h.detail && <div className="sub">{h.detail}</div>}
            </div>
            <div className="sub">{h.performed_by} · {fmt(h.create_at)}</div>
          </div>
        ))}
      </section>

      {creatingGroup && (
        <CreateGroupModal
          connectorId={id}
          onClose={() => setCreatingGroup(false)}
          onCreated={(g) => {
            setCreatingGroup(false);
            addGroup({ id: g.id, name: g.name });
            setNote({ text: `Groupe « ${g.name} » créé et ajouté à la sélection. Enregistre pour l'appliquer à la politique.`, error: false });
            void directory.reload();
            void history.reload();
          }}
        />
      )}
      {confirming && (
        <ConfirmDialog
          title="Modifier la politique d’inscription des appareils"
          requireText="MODIFIER"
          confirmLabel="Appliquer chez Microsoft"
          busy={saving}
          onConfirm={(typed) => save(typed.trim())}
          onCancel={() => setConfirming(false)}
        >
          <p>Ces changements seront appliqués <strong>immédiatement au tenant</strong> :</p>
          <ul>{lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
          <p>Ils affectent tous les nouveaux appairements d’appareils de l’organisation.</p>
        </ConfirmDialog>
      )}
    </>
  );
}
