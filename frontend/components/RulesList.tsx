'use client';
import { Fragment, useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import type { Mailbox, OutlookRule } from '../lib/types';
import { describeActions, describeConditions, isSuspicious } from '../lib/ruleDescribe';
import ConfirmDialog from './ConfirmDialog';

interface Props {
  mailbox: Mailbox;
  refreshKey: string | null;
  onMessage: (text: string, error?: boolean) => void;
}

// Règles Outlook synchronisées d'une boîte, avec suppression (dans Outlook puis localement).
export default function RulesList({ mailbox, refreshKey, onMessage }: Props) {
  const [rules, setRules] = useState<OutlookRule[] | null>(null);
  const [toDelete, setToDelete] = useState<OutlookRule | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRules(await api<OutlookRule[]>(`/outlook/rules/?mailbox=${mailbox.id}`));
    } catch (e) {
      onMessage(errorMessage(e), true);
    }
  }, [mailbox.id, onMessage]);

  useEffect(() => { void load(); }, [refreshKey, load]);

  async function remove() {
    if (!toDelete) return;
    const r = toDelete;
    try {
      await api<null>(`/outlook/rules/${r.id}/`, { method: 'DELETE' });
      onMessage(`Règle « ${r.display_name} » supprimée`);
      void load();
    } catch (e) {
      onMessage(errorMessage(e), true);
    } finally {
      setToDelete(null);
    }
  }

  if (!rules) return <div className="empty">Chargement…</div>;
  if (rules.length === 0) return <div className="empty">Aucune règle synchronisée. Utilise « Synchroniser » pour les récupérer depuis Outlook.</div>;

  return (
    <>
      <table className="grid">
        <thead><tr><th>#</th><th>Nom</th><th>État</th><th /></tr></thead>
        <tbody>
          {rules.map((r) => {
            const conds = describeConditions(r.conditions);
            const acts = describeActions(r.actions);
            const sus = isSuspicious(r.actions);
            const expanded = open === r.id;
            return (
              <Fragment key={r.id}>
                <tr onClick={() => setOpen(expanded ? null : r.id)} style={{ cursor: 'pointer' }}>
                  <td>{r.sequence}</td>
                  <td>
                    <strong>{r.display_name}</strong>
                    <div className="sub">
                      {conds.length ? conds[0] + (conds.length > 1 ? ` (+${conds.length - 1})` : '') : 'Tous les messages'} → {acts[0]?.text ?? 'aucune action'}
                    </div>
                  </td>
                  <td>
                    {r.has_error ? <span className="badge err">Erreur</span>
                      : r.is_enabled ? <span className="badge ok">Activée</span>
                      : <span className="badge off">Désactivée</span>}
                    {sus && <> <span className="badge err" title="Cette règle transfère, redirige ou masque des messages">À vérifier</span></>}
                    {r.is_read_only && <> <span className="badge off">Lecture seule</span></>}
                  </td>
                  <td align="right" onClick={(e) => e.stopPropagation()}>
                    <button className="ghost" onClick={() => setOpen(expanded ? null : r.id)}>{expanded ? 'Masquer' : 'Détail'}</button>
                    <button className="ghost danger" disabled={r.is_read_only} onClick={() => setToDelete(r)}>Supprimer</button>
                  </td>
                </tr>
                {expanded && (
                  <tr>
                    <td />
                    <td colSpan={3}>
                      <div className="tiles" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', margin: '4px 0 8px' }}>
                        <div className="tile static">
                          <span className="tile-label">Quand un message arrive et que…</span>
                          <ul style={{ margin: '6px 0 0 18px' }}>
                            {conds.length ? conds.map((c, i) => <li key={i}>{c}</li>) : <li>il arrive dans la boîte (aucune condition)</li>}
                          </ul>
                        </div>
                        <div className={`tile static ${sus ? 'warn' : ''}`}>
                          <span className="tile-label">Alors Outlook…</span>
                          <ul style={{ margin: '6px 0 0 18px' }}>
                            {acts.length ? acts.map((a, i) => (
                              <li key={i} style={a.risk ? { color: 'var(--danger)', fontWeight: 600 } : undefined}>{a.text}</li>
                            )) : <li>ne fait rien</li>}
                          </ul>
                        </div>
                      </div>
                      <div className="sub">Modifiée le {new Date(r.modified_at).toLocaleString()} · ID Outlook {r.rule_id}</div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {toDelete && (
        <ConfirmDialog title="Supprimer la règle" confirmLabel="Supprimer la règle"
          onConfirm={() => void remove()} onCancel={() => setToDelete(null)}>
          <p>
            La règle <strong>{toDelete.display_name}</strong> sera supprimée <strong>dans Outlook</strong> (boîte{' '}
            {mailbox.user_principal_name}), puis de l&apos;app. Action irréversible.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}
