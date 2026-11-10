'use client';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import ConfirmDialog from '../../components/ConfirmDialog';
import Loading from '../../components/Loading';
import { api } from '../../lib/api';
import { ago, CATEGORY, SEVERITY } from '../../lib/alerts';
import type { InsightAlert } from '../../lib/types';
import { useApi, useApiMutation } from '../../lib/useApi';

interface QuickAction {
  kind: string; label: string; danger?: boolean; method?: 'POST' | 'DELETE'; path?: string; body?: unknown;
  confirm?: string; require_text?: string; resolves?: boolean; href?: string;
}
type Item = InsightAlert & { org_name: string; actions: QuickAction[]; assigned_to: string; snoozed_until: string | null; note: string };
interface Payload {
  items: Item[]; me: string;
  stats: { todo: number; new: number; critical: number; mine: number; unassigned: number; snoozed: number; handled_today: number };
}
type Scope = 'all' | 'mine' | 'unassigned';
const SNOOZE: [number, string][] = [[1, '1 h'], [4, '4 h'], [24, 'Demain'], [72, '3 jours'], [168, '1 semaine']];

export default function QueuePage() {
  const [scope, setScope] = useState<Scope>('all');
  const [groupByOrg, setGroupByOrg] = useState(false);
  const [pending, setPending] = useState<{ item: Item; action: QuickAction } | null>(null);
  const [noteFor, setNoteFor] = useState<Item | null>(null);
  const [noteText, setNoteText] = useState('');
  const [focus, setFocus] = useState(0);
  const q = useApi<Payload>(`/insights/queue/?scope=${scope}`, { refetchInterval: () => 60_000 });
  useEffect(() => { document.title = 'À faire — OverView'; }, []);

  const inv = ['/insights/'];
  const ack = useApiMutation((ids: string[]) => api('/insights/alerts/status/', { method: 'POST', body: { ids, status: 'acknowledged' } }),
    { invalidate: inv, success: 'Pris en compte.' });
  const assign = useApiMutation((v: { ids: string[]; to: string | null }) => api('/insights/queue/assign/', { method: 'POST', body: v }),
    { invalidate: inv, success: (_, v) => (v.to ? 'Assigné.' : 'Désassigné.') });
  const snooze = useApiMutation((v: { ids: string[]; hours: number }) => api('/insights/queue/snooze/', { method: 'POST', body: v }),
    { invalidate: inv, success: (_, v) => `Reporté (${SNOOZE.find(([h]) => h === v.hours)?.[1] ?? ''}).` });
  const note = useApiMutation((v: { id: string; text: string }) => api(`/insights/alerts/${v.id}/note/`, { method: 'POST', body: { text: v.text } }),
    { invalidate: inv, success: 'Note ajoutée.', onSuccess: () => { setNoteFor(null); setNoteText(''); } });
  const run = useApiMutation(
    async (v: { item: Item; action: QuickAction }) => {
      await api(v.action.path as string, { method: v.action.method, body: v.action.body ?? undefined });
      // Réévalue tout de suite : l'alerte « état » se ferme d'elle-même ; sinon on la marque prise en compte.
      await api('/insights/alerts/evaluate/', { method: 'POST', body: { connector_id: v.item.connector_id } }).catch(() => null);
      if (!v.action.resolves) await api('/insights/alerts/status/', { method: 'POST', body: { ids: [v.item.id], status: 'acknowledged' } });
    },
    { invalidate: inv, success: (_, v) => `${v.action.label} : fait.`, onSuccess: () => setPending(null) },
  );

  const items = useMemo(() => q.data?.items ?? [], [q.data]);
  const groups = useMemo(() => {
    if (!groupByOrg) return [{ name: '', items }];
    const m = new Map<string, Item[]>();
    items.forEach((i) => m.set(i.org_name || '—', [...(m.get(i.org_name || '—') ?? []), i]));
    return [...m.entries()].map(([name, list]) => ({ name, items: list }));
  }, [items, groupByOrg]);

  // Raccourcis clavier : j / k pour naviguer, a pour prendre en compte, m pour m'assigner, o pour ouvrir.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (pending || noteFor || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || e.ctrlKey || e.metaKey) return;
      const cur = items[focus];
      if (e.key === 'j') setFocus((f) => Math.min(f + 1, items.length - 1));
      else if (e.key === 'k') setFocus((f) => Math.max(f - 1, 0));
      else if (e.key === 'a' && cur) ack.mutate([cur.id]);
      else if (e.key === 'm' && cur) assign.mutate({ ids: [cur.id], to: 'me' });
      else if (e.key === 'o' && cur?.link) window.location.href = cur.link;
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [items, focus, pending, noteFor, ack, assign]);

  useEffect(() => { document.getElementById(`q-${items[focus]?.id}`)?.scrollIntoView({ block: 'nearest' }); }, [focus, items]);

  const s = q.data?.stats;
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>À faire</h1>
          <div className="sub">Toutes les alertes de tous les clients, avec le bouton qui les règle · raccourcis : j / k, a, m, o</div>
        </div>
      </div>

      {s && (
        <div className="queue-progress">
          <div><b>{s.todo}</b> à traiter</div>
          <div className={s.critical ? 'hot' : ''}><b>{s.critical}</b> critique(s)</div>
          <div><b>{s.mine}</b> pour moi</div>
          <div><b>{s.snoozed}</b> reportée(s)</div>
          <div className="done"><b>{s.handled_today}</b> traitée(s) aujourd’hui</div>
        </div>
      )}

      <div className="filters">
        <div className="chip-group" role="group" aria-label="Filtre">
          {([['all', 'Toutes'], ['mine', 'Pour moi'], ['unassigned', 'Non assignées']] as [Scope, string][]).map(([k, l]) => (
            <button key={k} className={`chip-btn ${scope === k ? 'on' : ''}`} onClick={() => { setScope(k); setFocus(0); }}>{l}</button>
          ))}
        </div>
        <label className="switch"><input type="checkbox" checked={groupByOrg} onChange={(e) => setGroupByOrg(e.target.checked)} /> Regrouper par client</label>
      </div>

      {q.error && <div className="messagebar error">{q.error}</div>}
      {q.loading && <Loading lines={6} />}
      {q.data && items.length === 0 && (
        <div className="card empty-state"><div className="big" aria-hidden="true">🎉</div>Rien à traiter. Bon travail !</div>
      )}

      {groups.map((g) => (
        <section key={g.name || 'all'}>
          {g.name && <h2 className="queue-group">{g.name} <span className="sub">({g.items.length})</span></h2>}
          <div className="queue">
            {g.items.map((it) => {
              const idx = items.indexOf(it);
              return (
                <article key={it.id} id={`q-${it.id}`} className={`queue-item sev-${it.severity} ${idx === focus ? 'focus' : ''}`}
                  onClick={() => setFocus(idx)}>
                  <div className="queue-main">
                    <div className="queue-meta">
                      <span className={`badge ${SEVERITY[it.severity].cls}`}>{SEVERITY[it.severity].label}</span>
                      <span className="sub">{CATEGORY[it.category]?.icon} {CATEGORY[it.category]?.label} · {it.org_name || it.connector_name} · {ago(it.first_seen)}</span>
                      {it.status === 'acknowledged' && <span className="badge off">Prise en compte</span>}
                      {it.assigned_to && <span className="badge ok">👤 {it.assigned_to === q.data?.me ? 'moi' : it.assigned_to}</span>}
                    </div>
                    <div className="queue-title">{it.title}</div>
                    <div className="sub queue-detail">{it.detail}</div>
                    {it.note && <pre className="queue-note">{it.note}</pre>}
                  </div>
                  <div className="queue-actions">
                    {it.actions.filter((a) => a.method).map((a) => (
                      <button key={a.kind} className={a.danger ? 'danger-solid' : 'primary'} onClick={(e) => { e.stopPropagation(); setPending({ item: it, action: a }); }}>
                        {a.label}
                      </button>
                    ))}
                    {it.actions.filter((a) => a.href).map((a) => (
                      a.kind === 'open_external'
                        ? <a key={a.kind} className="btn" href={a.href} target="_blank" rel="noreferrer">{a.label}</a>
                        : <Link key={a.kind} className="btn" href={a.href as string}>{a.label}</Link>
                    ))}
                    <div className="queue-secondary">
                      {it.status === 'open' && <button className="ghost" onClick={() => ack.mutate([it.id])}>Prendre en compte</button>}
                      {it.assigned_to !== q.data?.me
                        ? <button className="ghost" onClick={() => assign.mutate({ ids: [it.id], to: 'me' })}>M’assigner</button>
                        : <button className="ghost" onClick={() => assign.mutate({ ids: [it.id], to: null })}>Désassigner</button>}
                      <select aria-label="Reporter" value="" onChange={(e) => e.target.value && snooze.mutate({ ids: [it.id], hours: Number(e.target.value) })}>
                        <option value="">Reporter…</option>
                        {SNOOZE.map(([h, l]) => <option key={h} value={h}>{l}</option>)}
                      </select>
                      <button className="ghost" onClick={() => { setNoteFor(it); setNoteText(''); }}>Note</button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      ))}

      {pending && (
        <ConfirmDialog title={`${pending.action.label} — ${pending.item.org_name || pending.item.connector_name}`}
          confirmLabel={pending.action.label} danger={!!pending.action.danger} requireText={pending.action.require_text}
          busy={run.isPending} onCancel={() => setPending(null)} onConfirm={() => run.mutate(pending)}>
          <p><strong>{pending.item.title}</strong></p>
          <p>{pending.action.confirm}</p>
          <p className="sub">L’action est journalisée. L’alerte se fermera d’elle-même si la situation est réglée.</p>
        </ConfirmDialog>
      )}
      {noteFor && (
        <ConfirmDialog title="Ajouter une note" confirmLabel="Ajouter" danger={false} busy={note.isPending}
          onCancel={() => setNoteFor(null)} onConfirm={() => note.mutate({ id: noteFor.id, text: noteText })}>
          <p className="sub">{noteFor.title}</p>
          <textarea rows={4} value={noteText} onChange={(e) => setNoteText(e.target.value)} autoFocus
            placeholder="Ex. client appelé, en attente de sa réponse" style={{ width: '100%' }} />
        </ConfirmDialog>
      )}
    </>
  );
}
