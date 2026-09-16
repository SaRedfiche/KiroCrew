import { useQuery } from '@tanstack/react-query'
import { Users, AlertTriangle, RefreshCw } from 'lucide-react'
import { api } from '../api/client'
import DetailPanel from './DetailPanel'
import ErrorNotice from './ErrorNotice'
import { errMessage } from '../utils/thunkError'
import { i18nT } from '../i18n/t'
import type { ProjectPanelWorkItem } from '../types'

/** Theme class for a work item's state. */
function stateClass(state: string): string {
  switch (state) {
    case 'accepted':
      return 'bg-ok/15 text-ok'
    case 'rejected':
    case 'abandoned':
      return 'bg-danger/15 text-danger'
    default: // 'open'
      return 'bg-warn/15 text-warn'
  }
}

/** Resolve an effective session key to its display title within this panel's
 *  own session set; falls back to the raw key (a worker of THIS group is always
 *  present, but a coordinator id or a defensive miss shows the key). */
function label(key: string, titles: Map<string, string>): string {
  return titles.get(key) || key
}

interface CoordinationPanelProps {
  projectGroupId: string
  onClose: () => void
}

export default function CoordinationPanel({ projectGroupId, onClose }: CoordinationPanelProps) {
  const { data, refetch, isLoading, error } = useQuery({
    queryKey: ['project-panel', projectGroupId],
    queryFn: () => api.getProjectPanel(projectGroupId),
    enabled: !!projectGroupId,
    refetchInterval: 5000,
    // Collisions/work are LIVE — poll like git-status — but only while the tab
    // is visible (a background dashboard tab pays no poll tax).
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    retry: 1,
  })

  const sessions = data?.sessions ?? []
  const work = data?.work ?? []
  const collisions = data?.collisions ?? []
  const titles = new Map(sessions.map(s => [s.session, s.title || s.session]))
  const isEmpty = sessions.length === 0 && work.length === 0 && collisions.length === 0

  return (
    <DetailPanel
      embedded
      title={i18nT('components.coordinationPanel.title')}
      onClose={onClose}
      noPadding
      customHeader={
        <div className="flex items-center gap-2 h-[38px] px-3 shrink-0 border-b border-border">
          <Users size={14} className="text-accent shrink-0" />
          <span className="text-[12px] font-medium text-text truncate">
            {data?.project.name || i18nT('components.coordinationPanel.loading')}
          </span>
          {sessions.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-bg-hover text-muted font-mono shrink-0">
              {i18nT('components.coordinationPanel.session_count', { count: sessions.length })}
            </span>
          )}
          {collisions.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 bg-warn/15 text-warn">
              {i18nT('components.coordinationPanel.collision_count', { count: collisions.length })}
            </span>
          )}
          <span className="flex-1" />
          <button
            onClick={() => refetch()}
            className="flex items-center justify-center w-[26px] h-[26px] rounded-md cursor-pointer transition-colors text-muted hover:text-text hover:bg-bg-hover bg-transparent border-none"
            title={i18nT('components.coordinationPanel.refresh')}
            aria-label={i18nT('components.coordinationPanel.refresh')}
          >
            <RefreshCw size={13} />
          </button>
        </div>
      }
    >
      <div className="overflow-y-auto flex-1 text-[12px]">
        {error && (
          <div className="flex flex-col gap-2 p-3">
            <ErrorNotice
              title={errMessage(error) ? i18nT('components.coordinationPanel.load_failed') : undefined}
              message={errMessage(error) || i18nT('components.coordinationPanel.load_failed')}
              askAgent
              testId="coordination-panel-error"
            />
          </div>
        )}

        {/* ── MEMBERS section ── */}
        {sessions.length > 0 && (
          <section className="py-2">
            <div className="px-3 pb-1.5 flex items-center gap-1.5">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">
                {i18nT('components.coordinationPanel.members')}
              </span>
              <span className="text-[10px] text-muted">{sessions.length}</span>
            </div>
            <div>
              {sessions.map(s => (
                <div
                  key={s.session}
                  className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-bg-hover transition-colors"
                  title={s.session}
                >
                  <span className="truncate text-text">{s.title || s.session}</span>
                  {s.is_coordinator && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-accent/15 text-accent shrink-0 capitalize">
                      {i18nT('components.coordinationPanel.coordinator')}
                    </span>
                  )}
                  <span className="flex-1" />
                  {s.agent && <span className="text-[11px] text-muted shrink-0">{s.agent}</span>}
                  {s.branch && (
                    <span className="font-mono text-[11px] text-muted shrink-0 truncate max-w-[40%]">{s.branch}</span>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ── WORK section (the prominent one) ── */}
        {work.length > 0 && (
          <section className="py-2 border-t border-border">
            <div className="px-3 pb-1.5 flex items-center gap-1.5">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">
                {i18nT('components.coordinationPanel.work')}
              </span>
              <span className="text-[10px] text-muted">{work.length}</span>
            </div>
            <div>
              {work.map((w: ProjectPanelWorkItem) => (
                <div key={w.item_id} className="px-3 py-1.5 hover:bg-bg-hover transition-colors">
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0 capitalize ${stateClass(w.state)}`}>
                      {w.state}
                    </span>
                    <span className="truncate text-text flex-1">{w.title || i18nT('components.coordinationPanel.untitled_item')}</span>
                    {w.pr != null && (
                      <span className="font-mono text-[11px] text-accent shrink-0">#{w.pr}</span>
                    )}
                  </div>
                  <div className="text-[11px] text-muted mt-0.5 flex items-center gap-1.5 truncate">
                    {w.worker && <span className="shrink-0">{label(w.worker, titles)}</span>}
                    {w.worker && w.status && <span>-</span>}
                    {w.status && <span className="truncate">{w.status}</span>}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ── COLLISIONS section (exception footer, bottom) ── */}
        {collisions.length > 0 && (
          <section className="py-2 border-t border-border">
            <div className="px-3 pb-1.5">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">
                {i18nT('components.coordinationPanel.collisions')}
              </span>
            </div>
            <div>
              {collisions.map((c, i) => (
                <div key={`${c.signal}:${c.repo_rel_path ?? ''}:${i}`} className="flex items-start gap-2 px-3 py-1.5">
                  <AlertTriangle size={13} className="text-warn shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div className="text-text">
                      {c.signal === 'same-file'
                        ? i18nT('components.coordinationPanel.same_file')
                        : i18nT('components.coordinationPanel.same_worktree')}
                      {c.repo_rel_path && (
                        <span className="font-mono text-[11px] text-muted ml-1.5 break-all">{c.repo_rel_path}</span>
                      )}
                    </div>
                    <div className="text-[11px] text-muted truncate">
                      {c.sessions.map(k => label(k, titles)).join(', ')}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Empty state */}
        {isEmpty && !isLoading && !error && (
          <div className="px-3 py-8 text-center text-muted text-[12px]">
            {i18nT('components.coordinationPanel.empty_state')}
          </div>
        )}
      </div>
    </DetailPanel>
  )
}
