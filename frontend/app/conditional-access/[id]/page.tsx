'use client';
import { use, useState } from 'react';
import Breadcrumb from '../../../components/Breadcrumb';
import ConfirmDialog from '../../../components/ConfirmDialog';
import Loading from '../../../components/Loading';
import { api } from '../../../lib/api';
import { useApi, useApiMutation, SLOW } from '../../../lib/useApi';

interface BaselineItem {
  code: string; title: string; why: string; status: 'covered' | 'report_only' | 'missing' | 'not_available';
  detail: string; policies: string[]; template: string | null; requires: string;
}
interface Policy {
  id: string; name: string; state: string; state_label: string; modified: string | null;
  include: string[]; exclude: string[]; apps: string[]; apps_excluded: string[]; conditions: string[];
  grant: string; session: string[]; flags: { level: 'info' | 'warn'; text: string }[];
}
interface Overview {
  security_defaults: boolean; license: 'none' | 'p1' | 'p2'; policies_readable: boolean;
  verdict: { level: 'ok' | 'info' | 'warn' | 'danger'; text: string };
  counts: { enabled: number; enabledForReportingButNotEnforced: number; disabled: number };
  baseline: BaselineItem[]; policies: Policy[];
  words: { enable: string; disable: string; create: string };
  history: { id: string; changes: string[]; ok: boolean; detail: string; performed_by: string; create_at: string }[];
}

const STATUS: Record<BaselineItem['status'], { label: string; cls: string; icon: string }> = {
  covered: { label: 'Couvert', cls: 'ok', icon: '✅' },
  report_only: { label: 'Rapport seulement', cls: 'warn', icon: '🟡' },
  missing: { label: 'Manquant', cls: 'err', icon: '❌' },
  not_available: { label: 'Licence requise', cls: 'off', icon: '🔒' },
};
const LICENSE = { none: 'Aucune licence Entra ID P1/P2', p1: 'Entra ID P1', p2: 'Entra ID P2' };

export default function ConditionalAccessPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const base = `/identity/connectors/${id}/conditional-access/`;
  const ov = useApi<Overview>(base, { staleTime: SLOW });
  const [sdTarget, setSdTarget] = useState<boolean | null>(null);
  const [template, setTemplate] = useState<BaselineItem | null>(null);
  const [breakGlass, setBreakGlass] = useState('');

  const setSd = useApiMutation(
    (v: { enabled: boolean; confirm: string }) => api(`${base}security-defaults/`, { method: 'PUT', body: v }),
    { invalidate: [base], success: (_, v) => `Security Defaults ${v.enabled ? 'activé' : 'désactivé'}.`, onSuccess: () => setSdTarget(null) },
  );
  const create = useApiMutation(
    (v: { template: string; confirm: string; exclude_user_ids: string[] }) => api(`${base}templates/`, { method: 'POST', body: v }),
    { invalidate: [base], success: 'Politique créée en mode « rapport seulement ».', onSuccess: () => { setTemplate(null); setBreakGlass(''); } },
  );

  const d = ov.data;
  return (
    <div className="wide">
      <Breadcrumb current="Accès conditionnel" />
      <header className="hero">
        <div className="avatar-lg" aria-hidden="true">🔐</div>
        <div className="hero-id">
          <h1>Accès conditionnel</h1>
          <div className="sub">Qui peut se connecter, d’où, et avec quelle preuve d’identité</div>
        </div>
        <div className="hero-actions"><button onClick={() => void ov.reload()}>Actualiser</button></div>
      </header>
      {ov.error && <div className="messagebar error">{ov.error}</div>}
      {ov.loading && <Loading lines={6} label="Lecture des politiques dans Microsoft Entra…" />}

      {d && (
        <>
          <div className={`messagebar ${d.verdict.level === 'danger' ? 'error' : ''}`}>
            <span aria-hidden="true">{{ ok: '✅', info: 'ℹ️', warn: '⚠️', danger: '⛔' }[d.verdict.level]}</span> {d.verdict.text}
          </div>

          <div className="tiles">
            <div className={`tile static ${d.security_defaults ? '' : 'warn'}`}>
              <span className="tile-label">Security Defaults</span>
              <span className="tile-value">{d.security_defaults ? 'Activé' : 'Désactivé'}</span>
              <span className="tile-hint">MFA et blocage de l’ancienne authentification pour tous</span>
            </div>
            <div className="tile static">
              <span className="tile-label">Licence</span><span className="tile-value" style={{ fontSize: 18 }}>{LICENSE[d.license]}</span>
              <span className="tile-hint">{d.license === 'none' ? 'L’accès conditionnel nécessite P1' : 'Accès conditionnel disponible'}</span>
            </div>
            <div className="tile static">
              <span className="tile-label">Politiques actives</span><span className="tile-value">{d.counts.enabled}</span>
              <span className="tile-hint">{d.counts.enabledForReportingButNotEnforced} en rapport seulement · {d.counts.disabled} désactivée(s)</span>
            </div>
          </div>

          <section className="card">
            <div className="card-head">
              <div>
                <h2>Security Defaults</h2>
                <div className="sub">
                  Réglage gratuit de Microsoft : MFA pour tous (inscription sous 14 jours), MFA systématique pour les admins,
                  blocage d’IMAP/POP/SMTP authentifié.
                </div>
              </div>
              {d.security_defaults
                ? <button className="danger" onClick={() => setSdTarget(false)}>Désactiver</button>
                : <button className="primary" onClick={() => setSdTarget(true)}>Activer</button>}
            </div>
          </section>

          <section className="card">
            <h2>Base recommandée</h2>
            <div className="sub" style={{ marginBottom: 8 }}>Les protections que Microsoft recommande pour tout tenant.</div>
            {d.baseline.map((b) => (
              <div className="row" key={b.code} style={{ alignItems: 'flex-start' }}>
                <span aria-hidden="true" style={{ fontSize: 18 }}>{STATUS[b.status].icon}</span>
                <div className="grow">
                  <strong>{b.title}</strong> <span className={`badge ${STATUS[b.status].cls}`}>{STATUS[b.status].label}</span>
                  <div className="sub">{b.why}</div>
                  <div className="sub">{b.detail}{b.policies.length > 0 && ` — ${b.policies.join(', ')}`}</div>
                </div>
                {b.template && b.status === 'missing' && d.policies_readable && (
                  <button onClick={() => setTemplate(b)}>Créer (rapport seulement)</button>
                )}
              </div>
            ))}
          </section>

          <section className="card">
            <h2>Politiques du tenant</h2>
            {!d.policies_readable && (
              <div className="empty">
                Politiques illisibles : le tenant n’a pas de licence Entra ID P1 (ou la permission Policy.Read.All manque).
                {d.security_defaults ? ' Security Defaults assure la protection de base.' : ''}
              </div>
            )}
            {d.policies_readable && d.policies.length === 0 && <div className="empty">Aucune politique d’accès conditionnel.</div>}
            {d.policies.map((p) => (
              <details className="finding" key={p.id}>
                <summary>
                  <span className={`badge ${p.state === 'enabled' ? 'ok' : p.state === 'disabled' ? 'off' : 'warn'}`}>{p.state_label}</span>
                  <span className="finding-title">{p.name}</span>
                  <span className="finding-sum sub">{p.grant}</span>
                  {p.flags.some((f) => f.level === 'warn') && <span className="badge warn">À vérifier</span>}
                </summary>
                <div className="finding-body">
                  <div className="kv"><span>S’applique à</span><span>{p.include.join(', ') || '—'}</span></div>
                  <div className="kv"><span>Sauf</span><span>{p.exclude.join(', ') || 'aucune exclusion'}</span></div>
                  <div className="kv"><span>Applications</span><span>{p.apps.join(', ') || '—'}{p.apps_excluded.length ? ` (sauf ${p.apps_excluded.join(', ')})` : ''}</span></div>
                  {p.conditions.map((c, i) => <div className="kv" key={i}><span>Condition</span><span>{c}</span></div>)}
                  <div className="kv"><span>Alors</span><span><strong>{p.grant}</strong></span></div>
                  {p.session.length > 0 && <div className="kv"><span>Session</span><span>{p.session.join(', ')}</span></div>}
                  {p.flags.map((f, i) => <p key={i} className="sub" style={{ color: f.level === 'warn' ? 'var(--warning-text)' : undefined }}>⚠ {f.text}</p>)}
                </div>
              </details>
            ))}
          </section>

          {d.history.length > 0 && (
            <section className="card">
              <h2>Historique des modifications</h2>
              {d.history.map((h) => (
                <div className="row" key={h.id}>
                  <span className={`badge ${h.ok ? 'ok' : 'err'}`}>{h.ok ? 'OK' : 'Échec'}</span>
                  <div className="grow">{h.changes.join(' · ')}<div className="sub">{h.performed_by} · {new Date(h.create_at).toLocaleString()}{h.detail ? ` · ${h.detail}` : ''}</div></div>
                </div>
              ))}
            </section>
          )}

          {sdTarget !== null && (
            <ConfirmDialog
              title={sdTarget ? 'Activer Security Defaults' : 'Désactiver Security Defaults'}
              confirmLabel={sdTarget ? 'Activer' : 'Désactiver'}
              danger={!sdTarget}
              requireText={sdTarget ? d.words.enable : d.words.disable}
              busy={setSd.isPending}
              onCancel={() => setSdTarget(null)}
              onConfirm={() => setSd.mutate({ enabled: sdTarget, confirm: sdTarget ? d.words.enable : d.words.disable })}
            >
              {sdTarget ? (
                <p>
                  Tous les utilisateurs devront s’inscrire au MFA (Microsoft Authenticator) sous 14 jours ; les admins devront
                  utiliser le MFA à chaque connexion ; les anciens protocoles (IMAP, POP, SMTP authentifié) seront bloqués.
                  Préviens les utilisateurs et vérifie les imprimantes ou applis qui envoient des courriels en SMTP.
                </p>
              ) : (
                <p>
                  Le tenant perdra la protection de base. OverView refusera si aucune politique d’accès conditionnelle active
                  n’exige le MFA pour tous.
                </p>
              )}
            </ConfirmDialog>
          )}

          {template && (
            <ConfirmDialog
              title={`Créer « ${template.title} »`}
              confirmLabel="Créer en rapport seulement"
              danger={false}
              requireText={d.words.create}
              busy={create.isPending}
              onCancel={() => setTemplate(null)}
              onConfirm={() => create.mutate({ template: template.template as string, confirm: d.words.create,
                exclude_user_ids: breakGlass.trim() ? [breakGlass.trim()] : [] })}
            >
              <p>
                La politique est créée en mode <strong>rapport seulement</strong> : elle n’a aucun effet sur les connexions.
                Consulte ses résultats dans Entra (journaux de connexion), puis active-la là-bas quand tu es prêt.
              </p>
              <div className="field">
                <label htmlFor="break-glass">ID d’un compte d’urgence à exclure (recommandé)</label>
                <input id="break-glass" value={breakGlass} onChange={(e) => setBreakGlass(e.target.value)} placeholder="ID d’objet Entra de l’utilisateur" />
              </div>
            </ConfirmDialog>
          )}
        </>
      )}
    </div>
  );
}
