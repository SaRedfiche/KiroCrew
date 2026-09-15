import { useQuery } from '@tanstack/react-query'
import { FolderGit2, GitBranch, AlertTriangle, Users } from 'lucide-react'
import { api } from '../../api/client'
import { useAppSelector } from '../../store'
import { i18nT } from '../../i18n/t'

/** Shapes from GET /api/projects/{id}/panel (P2.2/P2.3 backend). */
interface PanelSession {
  session: string
  title?: string
  agent?: string
  branch?: string
  is_coordinator?: boolean
}
interface PanelCollision {
  signal: 'same-file' | 'same-worktree'
  repo_rel_path?: string
  sessions: string[]
}
interface PanelWorkItem {
  coordinator: string
  item_id: string
  title?: string
  state?: string
  status?: string | null
  summary?: string
  pr?: number | null
  round?: number
  worker?: string | null
}
interface ProjectPanel {
  project: { id: string; name: string; repos: string[] }
  sessions: PanelSession[]
  collisions: PanelCollision[]
  work: PanelWorkItem[]
}

/**
 * Coordination side-panel: the human-facing progress/collision/work view for
 * the project group the active session is tagged into. Data source is the P2.2
 * panel endpoint (api.getProjectPanel); this is the rendered surface the user
 * consumes, NOT the raw JSON. Per-slot, keyed on the active slot like the
 * Summary tab. Empty state when the session carries no project_group_id.
 */
export default function CoordinationPanel({ slot }: { slot: string }) {
  const projectGroupId = useAppSelector(
    s => s.dashboard.slots.find(x => x.key === slot)?.project_group_id,
  )

  const { data, isLoading, isError } = useQuery<ProjectPanel>({
    queryKey: ['project-panel', projectGroupId],
    queryFn: () => api.getProjectPanel(projectGroupId as string),
    enabled: !!projectGroupId,
    // Live view: refetch periodically so collisions/progress stay current while
    // the panel is open (mounts only in the open panel, like the tag submenu).
    // Do NOT poll while the browser tab is hidden — a backgrounded panel would
    // otherwise keep hitting the gateway with no user-visible benefit.
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  })

  if (!projectGroupId) {
    return (
      <div className="flex-1 min-h-0 overflow-auto p-4 text-sm" style={{ color: 'var(--muted)' }}>
        <div className="flex items-center gap-2">
          <FolderGit2 size={14} className="shrink-0" />
          <span>{i18nT('pages.chat.coordination.untagged')}</span>
        </div>
        <p className="mt-2 text-xs" style={{ color: 'var(--muted)' }}>
          {i18nT('pages.chat.coordination.untagged_hint')}
        </p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="flex-1 min-h-0 overflow-auto p-4 text-sm" style={{ color: 'var(--muted)' }}>
        {i18nT('pages.chat.coordination.loading')}
      </div>
    )
  }
  if (isError || !data) {
    return (
      <div className="flex-1 min-h-0 overflow-auto p-4 text-sm" style={{ color: 'var(--danger)' }}>
        <div>{i18nT('pages.chat.coordination.error')}</div>
        <div className="mt-1 text-xs" style={{ color: 'var(--muted)' }}>
          {i18nT('pages.chat.coordination.error_hint')}
        </div>
      </div>
    )
  }

  const collisionLabel = (c: PanelCollision) =>
    c.signal === 'same-file'
      ? i18nT('pages.chat.coordination.same_file') + (c.repo_rel_path ? `: ${c.repo_rel_path}` : '')
      : i18nT('pages.chat.coordination.same_worktree')

  // Resolve a member session id to its human title for collision rows, so the
  // user reads "Conductor, Worker A" rather than raw "dashboard:sN" keys; falls
  // back to the raw key when the collision names a session not in the member list.
  const titleByKey = new Map(data.sessions.map(s => [s.session, s.title]))
  const sessionLabel = (key: string) => titleByKey.get(key) || key

  return (
    <div className="flex-1 min-h-0 overflow-auto p-3 text-sm" style={{ color: 'var(--text)' }}>
      {/* Project header */}
      <div className="flex items-center gap-2 font-semibold">
        <FolderGit2 size={15} className="shrink-0" style={{ color: 'var(--accent)' }} />
        <span className="truncate">{data.project.name}</span>
      </div>
      {data.project.repos.length > 0 && (
        <div className="mt-1 text-xs" style={{ color: 'var(--muted)' }}>
          {data.project.repos.join(', ')}
        </div>
      )}

      {/* Member sessions */}
      <div className="mt-4">
        <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--muted)' }}>
          <Users size={13} /> {i18nT('pages.chat.coordination.sessions')} ({data.sessions.length})
        </div>
        <ul className="mt-1.5 space-y-1">
          {data.sessions.length === 0 && (
            <li className="px-2 py-1 text-xs" style={{ color: 'var(--muted)' }}>
              {i18nT('pages.chat.coordination.no_sessions')}
            </li>
          )}
          {data.sessions.map(sn => (
            <li key={sn.session} className="flex items-center gap-2 rounded-md px-2 py-1"
                style={{ background: 'var(--card)', border: '1px solid var(--border)' }}>
              <span className="truncate flex-1">{sn.title || sn.session}</span>
              {sn.is_coordinator && (
                <span className="text-[10px] px-1.5 py-0.5 rounded"
                      style={{ background: 'var(--accent-subtle)', color: 'var(--accent)', textTransform: 'capitalize' }}>
                  {i18nT('pages.chat.coordination.coordinator')}
                </span>
              )}
              {sn.branch && (
                <span className="flex items-center gap-1 text-[11px]" style={{ color: 'var(--muted)' }}>
                  <GitBranch size={11} /> {sn.branch}
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>

      {/* Collisions */}
      {data.collisions.length > 0 && (
        <div className="mt-4">
          <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--warn)' }}>
            <AlertTriangle size={13} /> {i18nT('pages.chat.coordination.collisions')} ({data.collisions.length})
          </div>
          <ul className="mt-1.5 space-y-1">
            {data.collisions.map((c, i) => (
              <li key={i} className="rounded-md px-2 py-1 text-xs"
                  style={{ background: 'var(--warn-subtle)', color: 'var(--text)' }}>
                <div className="font-medium">{collisionLabel(c)}</div>
                <div style={{ color: 'var(--muted)' }}>{c.sessions.map(sessionLabel).join(', ')}</div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Work-ledger rollup */}
      {data.work.length > 0 && (
        <div className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--muted)' }}>
            {i18nT('pages.chat.coordination.work')} ({data.work.length})
          </div>
          <ul className="mt-1.5 space-y-1">
            {data.work.map(w => (
              <li key={w.item_id} className="rounded-md px-2 py-1 text-xs"
                  style={{ background: 'var(--card)', border: '1px solid var(--border)' }}>
                <div className="flex items-center gap-2">
                  <span className="truncate flex-1">{w.title || w.item_id}</span>
                  {w.state && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded"
                          style={{ background: 'var(--accent-subtle)', color: 'var(--accent)' }}>
                      {w.state}
                    </span>
                  )}
                </div>
                {(w.status || w.summary || w.pr != null) && (
                  <div className="mt-0.5" style={{ color: 'var(--muted)' }}>
                    {w.status ? `${w.status}` : ''}{w.summary ? ` — ${w.summary}` : ''}
                    {w.pr != null ? ` (PR #${w.pr})` : ''}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
