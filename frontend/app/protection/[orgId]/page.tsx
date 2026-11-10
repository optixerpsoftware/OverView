'use client';
import { use, useEffect, useState } from 'react';
import Breadcrumb from '../../../components/Breadcrumb';
import ConfirmDialog from '../../../components/ConfirmDialog';
import Loading from '../../../components/Loading';
import { api } from '../../../lib/api';
import { ago } from '../../../lib/alerts';
import { useApi, useApiMutation } from '../../../lib/useApi';

type Mode = 'off' | 'monitor' | 'enforce';
interface Policy {
  mode: Mode; devices_per_user_per_day: number; devices_per_org_per_day: number; failed_signins_per_user_per_hour: number;
  disable_on_device_limit: boolean; disable_on_signin_limit: boolean; exclusions: string[];
  emails_per_user_window: number; email_window_minutes: number; disable_on_email_limit: boolean;
  neutralize_rules: boolean; neutralize_forwarding: boolean; block_risky_consents: boolean;
  updated_by: string; updated_at: string | null; configured: boolean;
}
interface ProtectionEvent {
  id: string; rule: string; upn: string; count: number; limit: number; detail: string; items: string[];
  action: 'alerted' | 'disabled' | 'disable_failed' | 'blocked' | 'neutralized' | 'neutralize_failed'; action_detail: string; at: string;
  reenabled_at: string | null; reenabled_by: string;
}
interface Payload { policy: Policy; events: ProtectionEvent[]; reenable_word: string }

const MODES: { value: Mode; label: string; hint: string }[] = [
  { value: 'off', label: 'Désactivée', hint: 'Aucune surveillance.' },
  { value: 'monitor', label: 'Surveillance', hint: 'Alerte (et notifie) quand une limite est dépassée ; ne modifie rien.' },
  { value: 'enforce', label: 'Protection active', hint: 'Alerte ET désactive le compte en cause (sessions révoquées).' },
];
const RULE: Record<string, string> = {
  devices_user: 'Appareils par compte / jour', emails_user: 'Courriels envoyés / fenêtre',
  malicious_rule: 'Règle Outlook vers l’extérieur', mailbox_forwarding: 'Transfert SMTP de boîte', app_consent: 'Nouvelle appli tierce à risque', devices_org: 'Appareils pour le tenant / jour', signin_failures: 'Échecs de connexion / heure',
};
const ACTION: Record<ProtectionEvent['action'], { label: string; cls: string }> = {
  disabled: { label: 'Compte désactivé', cls: 'err' }, disable_failed: { label: 'Désactivation échouée', cls: 'err' },
  blocked: { label: 'Compte protégé', cls: 'warn' }, alerted: { label: 'Alerte', cls: 'warn' },
  neutralized: { label: 'Neutralisé', cls: 'ok' }, neutralize_failed: { label: 'Neutralisation échouée', cls: 'err' },
};

export default function ProtectionPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = use(params);
  const base = `/protection/organizations/${orgId}/`;
  const q = useApi<Payload>(base, { refetchInterval: () => 60_000 });
  const [form, setForm] = useState<Policy | null>(null);
  const [exclusions, setExclusions] = useState('');
  const [confirmEnforce, setConfirmEnforce] = useState(false);
  const [toReenable, setToReenable] = useState<ProtectionEvent | null>(null);

  useEffect(() => {
    if (q.data && !form) { setForm(q.data.policy); setExclusions(q.data.policy.exclusions.join(', ')); }
  }, [q.data, form]);

  const save = useApiMutation(
    (p: Policy) => api<Policy>(base, { method: 'PUT', body: { ...p, exclusions: exclusions.split(/[,;\s]+/).filter(Boolean) } }),
    { invalidate: [base], success: 'Réglages enregistrés.', onSuccess: (p) => { setForm(p); setConfirmEnforce(false); } },
  );
  const evaluate = useApiMutation(
    () => api<{ new_events?: number; breaches: number }[]>(`${base}evaluate/`, { method: 'POST' }),
    { invalidate: [base, '/insights/'], success: (r) => `Évaluation faite : ${r.reduce((n, x) => n + (x.new_events ?? 0), 0)} nouveau(x) déclenchement(s).` },
  );
  const reenable = useApiMutation(
    (e: ProtectionEvent) => api(`/protection/events/${e.id}/reenable/`, { method: 'POST', body: { confirm: q.data?.reenable_word } }),
    { invalidate: [base], success: (_, e) => `${e.upn} réactivé.`, onSuccess: () => setToReenable(null) },
  );

  const set = <K extends keyof Policy>(k: K, v: Policy[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));
  const submit = () => {
    if (!form) return;
    if (form.mode === 'enforce' && q.data?.policy.mode !== 'enforce') setConfirmEnforce(true);
    else save.mutate(form);
  };

  return (
    <div className="wide">
      <Breadcrumb current="Protection automatique" />
      <header className="hero">
        <div className="avatar-lg" aria-hidden="true">🛡️</div>
        <div className="hero-id">
          <h1>Protection automatique</h1>
          <div className="sub">Limites de fréquence par organisation, vérifiées toutes les 10 minutes sur chaque tenant</div>
        </div>
        <div className="hero-actions">
          <button disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>{evaluate.isPending ? 'Évaluation…' : 'Évaluer maintenant'}</button>
        </div>
      </header>
      {q.error && <div className="messagebar error">{q.error}</div>}
      {q.loading && <Loading lines={6} />}

      {form && (
        <section className="card">
          <h2>Réglages</h2>
          <div className="settings">
            <div className="setting">
              <div><strong>Mode</strong><div className="sub">{MODES.find((m) => m.value === form.mode)?.hint}</div></div>
              <div className="chip-group" role="radiogroup" aria-label="Mode">
                {MODES.map((m) => (
                  <button key={m.value} role="radio" aria-checked={form.mode === m.value}
                    className={`chip-btn ${form.mode === m.value ? 'on' : ''}`} onClick={() => set('mode', m.value)}>{m.label}</button>
                ))}
              </div>
            </div>
            <div className="setting">
              <div>
                <strong>Appareils joints par compte, par jour</strong>
                <div className="sub">Au-delà, sur 24 h glissantes. Un compte qui inscrit soudain de nombreux appareils est un signe classique de compromission.</div>
              </div>
              <div style={{ display: 'grid', gap: 6 }}>
                <input type="number" min={1} max={100} value={form.devices_per_user_per_day} onChange={(e) => set('devices_per_user_per_day', Number(e.target.value))} />
                <label className="switch"><input type="checkbox" checked={form.disable_on_device_limit} onChange={(e) => set('disable_on_device_limit', e.target.checked)} /> Désactiver le compte (mode protection active)</label>
              </div>
            </div>
            <div className="setting">
              <div>
                <strong>Courriels envoyés par compte, dans une courte fenêtre</strong>
                <div className="sub">
                  Nombre de destinataires sur la fenêtre glissante (suivi des messages Exchange, quelques minutes de retard).
                  Une rafale d’envois est le signe typique d’un compte compromis qui spamme ou hameçonne.
                </div>
              </div>
              <div style={{ display: 'grid', gap: 6 }}>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input type="number" min={10} max={100000} value={form.emails_per_user_window} aria-label="Nombre d’envois"
                    onChange={(e) => set('emails_per_user_window', Number(e.target.value))} />
                  <select value={form.email_window_minutes} aria-label="Fenêtre" onChange={(e) => set('email_window_minutes', Number(e.target.value))}>
                    {[15, 30, 60, 120].map((m) => <option key={m} value={m}>en {m} min</option>)}
                  </select>
                </div>
                <label className="switch"><input type="checkbox" checked={form.disable_on_email_limit} onChange={(e) => set('disable_on_email_limit', e.target.checked)} /> Désactiver le compte</label>
              </div>
            </div>
            <div className="setting">
              <div><strong>Appareils joints pour tout le tenant, par jour</strong><div className="sub">Alerte seulement : aucun compte unique à désactiver.</div></div>
              <input type="number" min={1} max={10000} value={form.devices_per_org_per_day} onChange={(e) => set('devices_per_org_per_day', Number(e.target.value))} />
            </div>
            <div className="setting">
              <div>
                <strong>Échecs de connexion par compte, par heure</strong>
                <div className="sub">Comptes suivis dans OverView. Désactiver sur ce critère est risqué : un attaquant pourrait bloquer un compte légitime en échouant exprès.</div>
              </div>
              <div style={{ display: 'grid', gap: 6 }}>
                <input type="number" min={3} max={10000} value={form.failed_signins_per_user_per_hour} onChange={(e) => set('failed_signins_per_user_per_hour', Number(e.target.value))} />
                <label className="switch"><input type="checkbox" checked={form.disable_on_signin_limit} onChange={(e) => set('disable_on_signin_limit', e.target.checked)} /> Désactiver le compte</label>
              </div>
            </div>
            <div className="setting">
              <div>
                <strong>Réponses automatiques (mode protection active)</strong>
                <div className="sub">
                  Vérifiées toutes les 10 minutes. Ces réponses ne verrouillent personne : elles s’appliquent aussi aux administrateurs,
                  et chacune est réversible.
                </div>
              </div>
              <div style={{ display: 'grid', gap: 8 }}>
                <label className="switch"><input type="checkbox" checked={form.neutralize_rules} onChange={(e) => set('neutralize_rules', e.target.checked)} />
                  Désactiver une règle Outlook qui transfère vers l’extérieur</label>
                <label className="switch"><input type="checkbox" checked={form.neutralize_forwarding} onChange={(e) => set('neutralize_forwarding', e.target.checked)} />
                  Retirer un transfert SMTP de boîte vers l’extérieur (invisible dans Outlook)</label>
                <label className="switch"><input type="checkbox" checked={form.block_risky_consents} onChange={(e) => set('block_risky_consents', e.target.checked)} />
                  Bloquer une appli tierce nouvellement autorisée avec accès au courrier, aux fichiers ou à l’annuaire</label>
              </div>
            </div>
            <div className="setting">
              <div><strong>Comptes jamais désactivés</strong><div className="sub">Compte d’urgence, comptes de service… Les administrateurs sont toujours exclus automatiquement.</div></div>
              <input value={exclusions} onChange={(e) => setExclusions(e.target.value)} placeholder="urgence@exemple.com, scanner@exemple.com" />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', paddingTop: 12, flexWrap: 'wrap' }}>
            <button className="primary" disabled={save.isPending} onClick={submit}>{save.isPending ? 'Enregistrement…' : 'Enregistrer'}</button>
            {q.data?.policy.configured && <span className="sub">Modifié par {q.data.policy.updated_by} {ago(q.data.policy.updated_at)}</span>}
          </div>
        </section>
      )}

      {q.data && (
        <section className="card">
          <h2>Déclenchements</h2>
          {q.data.events.length === 0 ? <div className="empty-state"><div className="big">✅</div>Aucune limite dépassée.</div> : (
            <table className="grid">
              <thead><tr><th>Quand</th><th>Règle</th><th>Compte</th><th>Constat</th><th>Action</th><th /></tr></thead>
              <tbody>
                {q.data.events.map((e) => (
                  <tr key={e.id}>
                    <td title={new Date(e.at).toLocaleString()}>{ago(e.at)}</td>
                    <td>{RULE[e.rule] ?? e.rule}</td>
                    <td>{e.upn || 'Tenant'}</td>
                    <td style={{ maxWidth: 420 }}>{e.detail}</td>
                    <td>
                      <span className={`badge ${ACTION[e.action].cls}`}>{ACTION[e.action].label}</span>
                      {e.reenabled_at && <div className="sub">Réactivé par {e.reenabled_by} {ago(e.reenabled_at)}</div>}
                      {e.action_detail && <div className="sub">{e.action_detail}</div>}
                    </td>
                    <td>{e.action === 'disabled' && !e.reenabled_at && <button onClick={() => setToReenable(e)}>Réactiver</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {confirmEnforce && form && (
        <ConfirmDialog title="Activer la protection automatique" confirmLabel="Activer" requireText="ACTIVER"
          busy={save.isPending} onCancel={() => setConfirmEnforce(false)} onConfirm={() => save.mutate(form)}>
          <p>
            OverView <strong>désactivera automatiquement</strong> un compte qui dépasse les limites cochées « Désactiver le compte »,
            et révoquera ses sessions. Les administrateurs et les comptes exclus ne sont jamais désactivés. Chaque désactivation crée une
            alerte critique, est notifiée et peut être annulée ici (« Réactiver »).
          </p>
          <p className="sub">Conseil : laisse d’abord quelques jours en « Surveillance » pour vérifier que les limites ne gênent pas l’activité normale.</p>
        </ConfirmDialog>
      )}
      {toReenable && (
        <ConfirmDialog title={`Réactiver ${toReenable.upn}`} confirmLabel="Réactiver" requireText={q.data?.reenable_word ?? 'REACTIVER'} danger={false}
          busy={reenable.isPending} onCancel={() => setToReenable(null)} onConfirm={() => reenable.mutate(toReenable)}>
          <p>Le compte pourra de nouveau se connecter. Vérifie d’abord les appareils inscrits et, si besoin, lance la procédure d’urgence.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
