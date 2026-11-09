'use client';
import Link from 'next/link';
import { use, useState, type FormEvent, type ReactNode } from 'react';
import ConfirmDialog from '../../../components/ConfirmDialog';
import DateRangeFilter, { inRange, isoDay, type Preset } from '../../../components/DateRangeFilter';
import RulesList from '../../../components/RulesList';
import SecretReveal from '../../../components/SecretReveal';
import { api, errorMessage } from '../../../lib/api';
import Modal from '../../../components/Modal';
import type {
  AuthMethod, Device, EmergencyResult, Mailbox, Note, SecurityActivity, SecurityOverview, SecurityProfile, SyncResult,
  TapResult, TotpResult,
} from '../../../lib/types';
import TotpReveal from '../../../components/TotpReveal';
import { useApi } from '../../../lib/useApi';
import { useTick } from '../../../lib/useTick';

type Ready = Omit<SecurityOverview, 'profile'> & { profile: SecurityProfile };
type Dialog = 'emergency' | 'reset' | 'revoke' | null;
type Tab = 'sessions' | 'devices' | 'mfa' | 'signins' | 'rules' | 'log';

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—');

function ago(iso: string | null): string {
  if (!iso) return '—';
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "aujourd'hui";
  if (days === 1) return 'hier';
  if (days < 60) return `il y a ${days} j`;
  return `il y a ${Math.floor(days / 30)} mois`;
}

function agoMin(iso: string | null): string {
  if (!iso) return 'jamais';
  const min = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return 'à l’instant';
  if (min < 60) return `il y a ${min} min`;
  if (min < 1440) return `il y a ${Math.floor(min / 60)} h`;
  return new Date(iso).toLocaleString();
}

const initials = (name: string) =>
  name.split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join('') || '?';

function Pane({ error, empty, children }: { error?: string; empty?: string; children: ReactNode }) {
  if (error) return <div className="messagebar error">{error}</div>;
  if (empty) return <div className="empty">{empty}</div>;
  return <>{children}</>;
}

export default function MailboxSecurityPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const sec = useApi<SecurityOverview>(`/identity/mailboxes/${id}/security/`, {
    // Lecture locale (≈ 25 ms) : toutes les 3 s tant qu'une synchro tourne, sinon chaque minute pour que la page
    // laissée ouverte reflète les synchros automatiques (et que « il y a X min » reste juste).
    refetchInterval: (d) => (d?.sync.status === 'running' ? 3000 : 60_000),
  });
  // Connexions et sessions viennent des journaux d'audit Microsoft (lents) : chargées à part.
  const act = useApi<SecurityActivity>(`/identity/mailboxes/${id}/security/activity/`, {
    refetchInterval: (d) => (d?.sync.status === 'running' ? 3000 : 60_000),
  });
  const reloadAll = () => Promise.all([sec.reload(), act.reload()]);
  useTick(30_000);   // le libellé « Synchronisé il y a X min » reste juste sans recharger de données
  const mailbox = useApi<Mailbox>(`/outlook/mailboxes/${id}/`);
  const [tab, setTab] = useState<Tab>('sessions');
  const [note, setNote] = useState<Note | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [password, setPassword] = useState<string | null>(null);
  const [steps, setSteps] = useState<EmergencyResult['steps'] | null>(null);
  const [deviceToDelete, setDeviceToDelete] = useState<Device | null>(null);
  // Entra ID est « éventuellement cohérent » : juste après une suppression, la liste peut encore
  // contenir l'appareil. On le masque localement et on recharge à retardement.
  const [removedDevices, setRemovedDevices] = useState<Set<string>>(new Set());
  // Filtre des appareils par date de création (AAAA-MM-JJ) et tri.
  const [devFrom, setDevFrom] = useState('');
  const [devTo, setDevTo] = useState('');
  const [devSort, setDevSort] = useState<'none' | 'desc' | 'asc'>('none');
  // Méthodes MFA : suppression et ajout manuels.
  const [methodToDelete, setMethodToDelete] = useState<AuthMethod | null>(null);
  const [addingMethod, setAddingMethod] = useState(false);
  const [addKind, setAddKind] = useState<'phone' | 'email' | 'tap' | 'totp'>('phone');
  const [tap, setTap] = useState<TapResult | null>(null);
  const [totp, setTotp] = useState<TotpResult | null>(null);
  // Filtres des sessions (dernière activité) et des connexions (date, résultat).
  const [sessFrom, setSessFrom] = useState('');
  const [sessTo, setSessTo] = useState('');
  const [signFrom, setSignFrom] = useState('');
  const [signTo, setSignTo] = useState('');
  const [signStatus, setSignStatus] = useState<'all' | 'ok' | 'fail'>('all');

  const upn = sec.data?.mailbox.user_principal_name ?? '';
  const base = `/identity/mailboxes/${id}`;
  const fail = (e: unknown) => setNote({ text: errorMessage(e), error: true });

  async function syncRules() {
    setSyncing(true);
    try {
      const r = await api<SyncResult>(`/outlook/mailboxes/${id}/sync/`, { method: 'POST' });
      setNote({ text: `${r.rules} règle(s) synchronisée(s) depuis Outlook.`, error: false });
      await mailbox.reload();
    } catch (e) {
      fail(e);
    } finally {
      setSyncing(false);
    }
  }

  // Synchro à la demande : asynchrone (le worker s'en charge) ; la page interroge l'état toutes les 3 s.
  async function syncNow() {
    try {
      await api(`${base}/security/sync/`, { method: 'POST' });
      await reloadAll();
    } catch (e) {
      fail(e);
    }
  }

  async function deleteMethod(confirm: string) {
    if (!methodToDelete) return;
    const m = methodToDelete;
    setBusy(true);
    try {
      await api<null>(`${base}/auth-methods/${encodeURIComponent(m.id)}/`, { method: 'DELETE', body: { confirm } });
      setNote({ text: `Méthode « ${m.label} » supprimée.`, error: false });
      void reloadAll();
    } catch (e) {
      fail(e);
    } finally {
      setMethodToDelete(null);
      setBusy(false);
    }
  }

  async function addMethod(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; tap: TapResult | null; totp: TotpResult | null }>(`${base}/auth-methods/`, {
        method: 'POST',
        body: {
          kind: addKind,
          value: String(f.get('value') ?? ''),
          phone_type: String(f.get('phone_type') ?? 'mobile'),
          lifetime_minutes: Number(f.get('lifetime_minutes') ?? 60),
          once: f.get('once') === 'on',
          confirm: String(f.get('confirm') ?? ''),
        },
      });
      setAddingMethod(false);
      if (r.tap) setTap(r.tap);
      if (r.totp) setTotp(r.totp);
      setNote({ text: addKind === 'tap' ? 'Code d’accès temporaire créé.' : addKind === 'totp' ? 'Application TOTP ajoutée.' : 'Méthode ajoutée.', error: false });
      void reloadAll();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function deleteDevice(confirm: string) {
    if (!deviceToDelete) return;
    setBusy(true);
    try {
      await api<null>(`${base}/devices/${encodeURIComponent(deviceToDelete.id)}/`, {
        method: 'DELETE', body: { confirm },
      });
      const gone = deviceToDelete;
      setRemovedDevices((prev) => new Set(prev).add(gone.id));
      setNote({ text: `Appareil « ${gone.display_name} » supprimé de l'annuaire.`, error: false });
      void reloadAll();
      setTimeout(() => void reloadAll(), 3000);
      setTimeout(() => void reloadAll(), 8000);
    } catch (e) {
      fail(e);
    } finally {
      setDeviceToDelete(null);
      setBusy(false);
    }
  }

  async function revoke() {
    setBusy(true);
    try {
      await api(`${base}/revoke-sessions/`, { method: 'POST' });
      setNote({ text: `${upn} déconnecté de toutes ses sessions.`, error: false });
      void reloadAll();
    } catch (e) {
      fail(e);
    } finally {
      setDialog(null);
      setBusy(false);
    }
  }

  async function resetPassword(confirm: string) {
    setBusy(true);
    try {
      const r = await api<{ password: string }>(`${base}/reset-password/`, {
        method: 'POST', body: { confirm, force_change: true },
      });
      setPassword(r.password);
      setSteps(null);
      setNote(null);
      void reloadAll();
    } catch (e) {
      fail(e);
    } finally {
      setDialog(null);
      setBusy(false);
    }
  }

  async function emergency(confirm: string) {
    setBusy(true);
    try {
      const r = await api<EmergencyResult>(`${base}/emergency/`, { method: 'POST', body: { confirm } });
      setSteps(r.steps);
      setPassword(r.password);
      setNote(r.ok ? null : { text: "Le pipeline d'urgence s'est terminé avec des erreurs (voir ci-dessous).", error: true });
      void reloadAll();
    } catch (e) {
      fail(e);
    } finally {
      setDialog(null);
      setBusy(false);
    }
  }

  // Tant que la première synchro n'est pas finie, `profile` est null : la page affiche l'attente, pas des données vides.
  const raw = sec.data && sec.data.profile ? (sec.data as Ready) : null;
  const sync = sec.data?.sync;
  const firstSync = !!sec.data && !sec.data.profile;
  const d = raw && {
    ...raw,
    devices: { ...raw.devices, items: raw.devices.items.filter((x) => !removedDevices.has(x.id)) },
  };
  const devicePresets: Preset[] = [
    { label: 'Tous', from: '', to: '' },
    { label: '30 derniers jours', from: isoDay(30), to: '' },
    { label: '90 derniers jours', from: isoDay(90), to: '' },
    { label: 'Moins d’un an', from: isoDay(365), to: '' },
    { label: 'Plus d’un an', from: '', to: isoDay(365) },
  ];
  const logPresets: Preset[] = [
    { label: 'Tout', from: '', to: '' },
    { label: 'Aujourd’hui', from: isoDay(0), to: '' },
    { label: '7 jours', from: isoDay(7), to: '' },
    { label: '30 jours', from: isoDay(30), to: '' },
  ];
  const filtering = devFrom !== '' || devTo !== '';
  const allDevices = d?.devices.items ?? [];
  const visibleDevices = allDevices
    .filter((x) => inRange(x.registered_at, devFrom, devTo))
    .sort((a, b) => {
      if (devSort === 'none') return 0;
      const ta = a.registered_at ? new Date(a.registered_at).getTime() : 0;
      const tb = b.registered_at ? new Date(b.registered_at).getTime() : 0;
      return devSort === 'desc' ? tb - ta : ta - tb;
    });
  const allSessions = act.data?.active_sessions.items ?? [];
  const visibleSessions = allSessions.filter((s) => inRange(s.last_seen, sessFrom, sessTo));
  const allSignIns = act.data?.sign_ins.items ?? [];
  const visibleSignIns = allSignIns.filter((s) =>
    inRange(s.at, signFrom, signTo) && (signStatus === 'all' || (signStatus === 'ok') === s.success));
  const mfa = d?.auth_methods.items.filter((m) => m.is_mfa) ?? [];
  const noMfa = !!d && !d.auth_methods.error && mfa.length === 0;
  const name = d?.profile.display_name ?? upn;

  const tiles: { key: Tab; label: string; value: string; hint: string; warn?: boolean }[] = [
    { key: 'sessions', label: 'Sessions actives', value: act.data ? String(act.data.active_sessions.items.length) : '…', hint: act.error ? 'indisponible' : 'estimation' },
    { key: 'devices', label: 'Appareils', value: d ? String(d.devices.items.length) : '…', hint: 'enregistrés' },
    {
      key: 'mfa', label: 'Méthodes MFA', warn: noMfa,
      value: d ? (d.auth_methods.error ? '?' : String(mfa.length)) : '…',
      hint: noMfa ? 'aucune protection' : 'configurées',
    },
    { key: 'signins', label: 'Mot de passe', value: d ? ago(d.profile.last_password_change) : '…', hint: 'dernier changement' },
  ];

  return (
    <>
      <div className="breadcrumb"><Link href="/">Organisations</Link> › Sécurité du compte</div>

      <header className="hero">
        <div className="avatar-lg" aria-hidden="true">{initials(name || '?')}</div>
        <div className="hero-id">
          <h1>{name || 'Compte'}</h1>
          <div className="sub">{upn}</div>
          {sync && (
            <div className="sub" style={{ marginTop: 2 }}>
              {sync.status === 'running' ? 'Synchronisation en cours…' : `Synchronisé ${agoMin(sync.last_synced_at)}`} · automatique toutes les 10 min
            </div>
          )}
          {d && (
            <div className="chips">
              {d.profile.account_enabled === false
                ? <span className="badge err">Désactivé</span> : <span className="badge ok">Actif</span>}
              {noMfa && <span className="badge err">Sans MFA</span>}
            </div>
          )}
        </div>
        <div className="hero-actions">
          <button disabled={sync?.status === 'running'} onClick={() => void syncNow()} title="Récupère maintenant les nouveautés chez Microsoft">
            {sync?.status === 'running' ? 'Synchronisation…' : '↻ Synchroniser'}
          </button>
          <button disabled={!d || busy} onClick={() => setDialog('revoke')}>Déconnecter</button>
          <button disabled={!d || busy} onClick={() => setDialog('reset')}>Mot de passe</button>
          <button className="primary danger-solid" disabled={!d || busy} onClick={() => setDialog('emergency')}>
            🚨 Urgence
          </button>
        </div>
      </header>

      {note && <div className={`messagebar ${note.error ? 'error' : ''}`}>{note.text}</div>}
      {sec.error && <div className="messagebar error">{sec.error}</div>}
      {sync?.status === 'failed' && <div className="messagebar error">La dernière synchronisation a échoué : {sync.last_error}</div>}
      {firstSync && sync?.status !== 'failed' && (
        <div className="messagebar">Première synchronisation du compte en cours (jusqu’à une minute). Elle se fait en arrière-plan : ensuite cette page s’ouvre instantanément et reste à jour toutes les 10 minutes.</div>
      )}
      {password && <SecretReveal password={password} onDismiss={() => setPassword(null)} />}
      {totp && <TotpReveal totp={totp} onDismiss={() => setTotp(null)} />}
      {tap && (
        <SecretReveal password={tap.pass} onDismiss={() => setTap(null)} title="Code d’accès temporaire"
          hint={`affiché une seule fois. Valable ${tap.lifetime_minutes} min${tap.once ? ', usage unique' : ''}. L’utilisateur s’en sert pour se connecter et enregistrer sa propre méthode MFA (Authenticator, clé FIDO2…).`} />
      )}

      {steps && (
        <section className="panel">
          <h3 className="panel-title">Pipeline d&apos;urgence</h3>
          {steps.map((s, i) => (
            <div className="line" key={i}>
              <span className={`badge ${s.ok ? 'ok' : 'err'}`}>{s.ok ? 'OK' : 'Échec'}</span>
              <div className="grow">{s.step}{s.detail && <div className="sub">{s.detail}</div>}</div>
            </div>
          ))}
        </section>
      )}

      <div className="tiles">
        {tiles.map((t) => (
          <button
            key={t.key}
            className={`tile ${tab === t.key ? 'on' : ''} ${t.warn ? 'warn' : ''}`}
            onClick={() => setTab(t.key)}
          >
            <span className="tile-label">{t.label}</span>
            <span className="tile-value">{t.value}</span>
            <span className="tile-hint">{t.hint}</span>
          </button>
        ))}
      </div>

      <nav className="tabs" role="tablist">
        {([
          ['sessions', 'Sessions'], ['devices', 'Appareils'], ['mfa', 'MFA'],
          ['signins', 'Connexions'], ['rules', 'Règles Outlook'], ['log', 'Journal'],
        ] as [Tab, string][]).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </nav>

      <section className="panel">
        {!d && tab !== 'rules' && <div className="empty">{firstSync ? 'Synchronisation en cours…' : 'Chargement…'}</div>}

        {tab === 'rules' && (
          <>
            <div className="line" style={{ justifyContent: 'space-between', borderBottom: 0 }}>
              <div className="sub">
                {mailbox.data?.last_synced_at ? `Dernière synchronisation : ${fmt(mailbox.data.last_synced_at)}` : 'Jamais synchronisées'}
              </div>
              <button disabled={syncing} onClick={() => void syncRules()}>{syncing ? 'Synchronisation…' : 'Synchroniser'}</button>
            </div>
            {mailbox.data && (
              <RulesList mailbox={mailbox.data} refreshKey={mailbox.data.last_synced_at}
                onMessage={(text, error = false) => setNote({ text, error })} />
            )}
          </>
        )}

        {d && !act.data && (tab === 'sessions' || tab === 'signins') && (
          act.error ? <div className="messagebar error">{act.error}</div>
            : <div className="empty">Analyse des connexions en cours (journaux Microsoft, jusqu’à 30 s)…</div>
        )}

        {d && act.data && tab === 'sessions' && (
          <Pane error={act.data.active_sessions.error} empty={act.data.active_sessions.items.length === 0 ? 'Aucune session active probable.' : undefined}>
            <p className="note">
              Estimation : Microsoft ne liste pas les sessions. Ce sont les connexions réussies postérieures à la
              dernière déconnexion forcée ({fmt(d.profile.sessions_valid_from)}), regroupées par application, IP et appareil.
            </p>
            <DateRangeFilter presets={logPresets} from={sessFrom} to={sessTo} label="Activité du" noun="session"
              shown={visibleSessions.length} total={allSessions.length}
              onChange={(f, t) => { setSessFrom(f); setSessTo(t); }}
              canReset={sessFrom !== '' || sessTo !== ''} onReset={() => { setSessFrom(''); setSessTo(''); }} />
            {visibleSessions.length === 0 && <div className="empty">Aucune session sur cette période.</div>}
            {visibleSessions.length > 0 && (
            <table className="grid">
              <thead><tr><th>Application</th><th>Lieu / IP</th><th>Appareil</th><th>Dernière activité</th><th /></tr></thead>
              <tbody>
                {visibleSessions.map((s, i) => (
                  <tr key={i}>
                    <td>{s.app}<div className="sub">{s.client}</div></td>
                    <td>{[s.city, s.country].filter(Boolean).join(', ') || '—'}<div className="sub">{s.ip}</div></td>
                    <td>{[s.os, s.browser].filter(Boolean).join(' · ') || '—'}</td>
                    <td>{ago(s.last_seen)}<div className="sub">{fmt(s.last_seen)} · {s.count} connexion{s.count > 1 ? 's' : ''}</div></td>
                    <td />
                  </tr>
                ))}
              </tbody>
            </table>
            )}
          </Pane>
        )}

        {d && tab === 'devices' && (
          <Pane error={d.devices.error} empty={d.devices.items.length === 0 ? 'Aucun appareil enregistré.' : undefined}>
            <DateRangeFilter presets={devicePresets} from={devFrom} to={devTo} label="Créé du" noun="appareil"
              shown={visibleDevices.length} total={allDevices.length}
              onChange={(f, t) => { setDevFrom(f); setDevTo(t); }}
              canReset={filtering || devSort !== 'none'}
              onReset={() => { setDevFrom(''); setDevTo(''); setDevSort('none'); }} />
            {visibleDevices.length === 0 && <div className="empty">Aucun appareil créé sur cette période.</div>}
            {visibleDevices.length > 0 && (
            <table className="grid">
              <thead><tr><th>Nom</th><th>Système</th><th>Conformité</th>
                <th>
                  <button className="th-sort" onClick={() => setDevSort(devSort === 'desc' ? 'asc' : 'desc')}
                    title="Trier par date de création">
                    Création {devSort === 'desc' ? '▼' : devSort === 'asc' ? '▲' : '↕'}
                  </button>
                </th>
                <th>Dernière connexion</th><th /></tr></thead>
              <tbody>
                {visibleDevices.map((x) => (
                  <tr key={x.id}>
                    <td><strong>{x.display_name}</strong></td>
                    <td>{x.operating_system} <span className="sub" style={{ display: 'inline' }}>{x.operating_system_version}</span></td>
                    <td>
                      {x.is_compliant === true ? <span className="badge ok">Conforme</span>
                        : x.is_compliant === false ? <span className="badge err">Non conforme</span>
                        : <span className="badge off">Inconnu</span>}
                    </td>
                    <td>
                      {x.registered_at ? new Date(x.registered_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—'}
                      {x.registered_at && <div className="sub">{ago(x.registered_at)}</div>}
                    </td>
                    <td>{ago(x.last_sign_in)}{x.last_sign_in && <div className="sub">{new Date(x.last_sign_in).toLocaleDateString()}</div>}</td>
                    <td align="right">
                      <button className="ghost danger" disabled={busy} onClick={() => setDeviceToDelete(x)}>Supprimer</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            )}
          </Pane>
        )}

        {d && tab === 'mfa' && (
          <Pane error={d.auth_methods.error}>
            {noMfa && <div className="messagebar error">Aucune méthode MFA : le compte n&apos;a pas de seconde étape de vérification.</div>}
            <div className="line" style={{ justifyContent: 'space-between', borderBottom: 0 }}>
              <div className="sub">Les changements s’appliquent immédiatement dans Microsoft Entra ID.</div>
              <button className="primary" disabled={busy} onClick={() => { setAddKind('phone'); setAddingMethod(true); }}>
                Ajouter une méthode
              </button>
            </div>
            {d.auth_methods.items.map((m) => (
              <div className="line" key={m.id}>
                <div className="grow">{m.label}{m.detail && <div className="sub">{m.detail}</div>}</div>
                <span className={`badge ${m.is_mfa ? 'ok' : 'off'}`}>{m.is_mfa ? 'MFA' : 'Mot de passe'}</span>
                {m.kind !== 'password' && m.kind !== 'other' && (
                  <button className="ghost danger" disabled={busy} onClick={() => setMethodToDelete(m)}>Supprimer</button>
                )}
              </div>
            ))}
          </Pane>
        )}

        {d && act.data && tab === 'signins' && (
          <Pane error={act.data.sign_ins.error} empty={act.data.sign_ins.items.length === 0 ? 'Aucune connexion récente.' : undefined}>
            <DateRangeFilter presets={logPresets} from={signFrom} to={signTo} label="Du" noun="connexion"
              shown={visibleSignIns.length} total={allSignIns.length}
              onChange={(f, t) => { setSignFrom(f); setSignTo(t); }}
              canReset={signFrom !== '' || signTo !== '' || signStatus !== 'all'}
              onReset={() => { setSignFrom(''); setSignTo(''); setSignStatus('all'); }}
              extra={(
                <div className="chip-group" role="group" aria-label="Résultat">
                  {([['all', 'Toutes'], ['ok', 'Réussies'], ['fail', 'Échecs']] as const).map(([k, label]) => (
                    <button key={k} className={`chip-btn ${signStatus === k ? 'on' : ''}`} onClick={() => setSignStatus(k)}>{label}</button>
                  ))}
                </div>
              )} />
            {visibleSignIns.length === 0 && <div className="empty">Aucune connexion ne correspond au filtre.</div>}
            {visibleSignIns.length > 0 && (
            <table className="grid">
              <thead><tr><th>Date</th><th>Application</th><th>Lieu / IP</th><th>Appareil</th><th /></tr></thead>
              <tbody>
                {visibleSignIns.slice(0, 100).map((s, i) => (
                  <tr key={i}>
                    <td>{fmt(s.at)}</td>
                    <td>{s.app}<div className="sub">{s.client}</div></td>
                    <td>{[s.city, s.country].filter(Boolean).join(', ') || '—'}<div className="sub">{s.ip}</div></td>
                    <td>{[s.os, s.browser].filter(Boolean).join(' · ') || '—'}</td>
                    <td>{s.success ? <span className="badge ok">Réussie</span> : <span className="badge err">Échec {s.error_code}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            )}
          </Pane>
        )}

        {d && tab === 'log' && (
          <Pane empty={d.actions.length === 0 ? 'Aucune action de sécurité enregistrée.' : undefined}>
            {d.actions.map((a) => (
              <div className="line" key={a.id}>
                <span className={`badge ${a.ok ? 'ok' : 'err'}`}>{a.ok ? 'OK' : 'Échec'}</span>
                <div className="grow">{a.action}{a.detail && <div className="sub">{a.detail}</div>}</div>
                <div className="sub">{a.performed_by} · {fmt(a.create_at)}</div>
              </div>
            ))}
          </Pane>
        )}
      </section>

      {methodToDelete && (
        <ConfirmDialog
          title="Supprimer la méthode d’authentification"
          requireText={upn}
          confirmLabel="Supprimer la méthode"
          busy={busy}
          onConfirm={deleteMethod}
          onCancel={() => setMethodToDelete(null)}
        >
          <p>
            <strong>{methodToDelete.label}</strong>{methodToDelete.detail ? ` (${methodToDelete.detail})` : ''} sera supprimée du
            compte <strong>{upn}</strong>. {mfa.length <= 1 && methodToDelete.is_mfa
              ? 'C’est sa dernière méthode MFA : le compte sera sans seconde étape de vérification tant qu’il n’en enregistre pas une nouvelle.'
              : 'L’utilisateur ne pourra plus s’en servir pour se connecter.'}
          </p>
        </ConfirmDialog>
      )}
      {addingMethod && (
        <Modal title="Ajouter une méthode d’authentification" onClose={() => setAddingMethod(false)}>
          <form onSubmit={addMethod} className="form" style={{ maxWidth: 'none' }}>
            <select value={addKind} onChange={(e) => setAddKind(e.target.value as 'phone' | 'email' | 'tap' | 'totp')}>
              <option value="phone">Téléphone (SMS / appel)</option>
              <option value="totp">Application TOTP (Authenticator, Google Authenticator, Authy…)</option>
              <option value="email">Courriel de récupération</option>
              <option value="tap">Code d’accès temporaire (TAP)</option>
            </select>
            {addKind === 'totp' && (
              <div className="sub">
                Une clé secrète est générée et enregistrée pour ce compte (jeton OATH). Elle t’est montrée <strong>une seule fois</strong>,
                sous forme de QR code à scanner dans l’application de l’utilisateur. Aucun code n’est demandé à l’utilisateur pour activer la méthode.
              </div>
            )}
            {addKind === 'phone' && (
              <>
                <input name="value" placeholder="+1 5145551234" required autoFocus />
                <select name="phone_type" defaultValue="mobile">
                  <option value="mobile">Mobile</option>
                  <option value="alternateMobile">Mobile secondaire</option>
                  <option value="office">Bureau</option>
                </select>
                <div className="sub">Format international avec un espace après l’indicatif. Un seul numéro par type : supprime l’ancien d’abord.</div>
              </>
            )}
            {addKind === 'email' && <input name="value" type="email" placeholder="adresse@exemple.com" required autoFocus />}
            {addKind === 'tap' && (
              <>
                <label className="date-field">Durée (minutes)
                  <input name="lifetime_minutes" type="number" min={10} max={43200} defaultValue={60} required />
                </label>
                <label className="date-field">
                  <input name="once" type="checkbox" defaultChecked /> Usage unique
                </label>
                <div className="sub">Le code permet à l’utilisateur de se connecter sans mot de passe pour enregistrer lui-même Authenticator ou une clé FIDO2. Il ne sera affiché qu’une fois.</div>
              </>
            )}
            <label className="field">
              <span>Pour confirmer, saisis <strong>{upn}</strong></span>
              <input name="confirm" autoComplete="off" required />
            </label>
            <div className="sub">Microsoft Authenticator (notification), FIDO2 et Windows Hello ne peuvent être enregistrés que par l’utilisateur lui-même.</div>
            <div className="dialog-actions">
              <button type="button" onClick={() => setAddingMethod(false)}>Annuler</button>
              <button type="submit" className="primary" disabled={busy}>{busy ? 'En cours…' : 'Ajouter'}</button>
            </div>
          </form>
        </Modal>
      )}
      {deviceToDelete && (
        <ConfirmDialog
          title="Supprimer l'appareil"
          requireText={deviceToDelete.display_name}
          confirmLabel="Supprimer l'appareil"
          busy={busy}
          onConfirm={deleteDevice}
          onCancel={() => setDeviceToDelete(null)}
        >
          <p>
            L&apos;appareil <strong>{deviceToDelete.display_name}</strong> sera supprimé de l&apos;annuaire Microsoft
            Entra ID. Il ne sera plus joint ni enregistré : l&apos;utilisateur perdra l&apos;accès SSO et les
            politiques liées à cet appareil, et devra le rejoindre à nouveau pour le réutiliser. Action irréversible.
          </p>
        </ConfirmDialog>
      )}
      {dialog === 'emergency' && (
        <ConfirmDialog
          title="🚨 Procédure d'urgence"
          requireText={upn}
          confirmLabel="Lancer l'urgence"
          busy={busy}
          onConfirm={emergency}
          onCancel={() => setDialog(null)}
        >
          <p>Cette procédure va, dans l&apos;ordre :</p>
          <ol>
            <li>déconnecter <strong>toutes les sessions</strong> de {upn} ;</li>
            <li>définir un <strong>nouveau mot de passe</strong> généré (changement forcé à la prochaine connexion) ;</li>
            <li>déconnecter à nouveau toutes les sessions.</li>
          </ol>
          <p>L&apos;utilisateur perdra l&apos;accès immédiatement. Le nouveau mot de passe te sera affiché une seule fois.</p>
        </ConfirmDialog>
      )}
      {dialog === 'revoke' && (
        <ConfirmDialog
          title="Déconnecter toutes les sessions"
          confirmLabel="Tout déconnecter"
          busy={busy}
          onConfirm={() => void revoke()}
          onCancel={() => setDialog(null)}
        >
          <p>
            <strong>{upn}</strong> sera déconnecté de toutes ses sessions (applications, navigateurs, appareils) et
            devra se reconnecter. Les jetons d&apos;accès déjà émis expirent d&apos;ici environ une heure.
          </p>
        </ConfirmDialog>
      )}
      {dialog === 'reset' && (
        <ConfirmDialog
          title="Changer le mot de passe"
          requireText={upn}
          confirmLabel="Changer le mot de passe"
          busy={busy}
          onConfirm={resetPassword}
          onCancel={() => setDialog(null)}
        >
          <p>Un nouveau mot de passe sera généré pour {upn} (changement forcé à la prochaine connexion).
            Les sessions existantes ne sont pas fermées ; utilise « Déconnecter » ou « Urgence » pour cela.</p>
        </ConfirmDialog>
      )}
    </>
  );
}
