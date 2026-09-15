import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

/**
 * Tests for CoordinationPanel — the human-facing project-coordination side-panel.
 *   - untagged active session -> empty state (no fetch)
 *   - tagged session -> renders project name, member sessions (with the
 *     is_coordinator badge), collision flags, and the work rollup
 *
 * The store selector, the api client, and i18nT are mocked so the panel renders
 * without Redux / the real HTTP client. i18nT returns the key verbatim, so the
 * empty-state assertion keys off the stable i18n key rather than English copy.
 */

const H = vi.hoisted(() => ({
  projectGroupId: undefined as string | undefined,
  getProjectPanel: vi.fn(),
}))

vi.mock('../../store', () => ({
  useAppSelector: (sel: (s: unknown) => unknown) =>
    sel({ dashboard: { slots: [{ key: 'dashboard:s1', project_group_id: H.projectGroupId }] } }),
}))
vi.mock('../../api/client', () => ({ api: { getProjectPanel: H.getProjectPanel } }))
vi.mock('../../i18n/t', () => ({ i18nT: (k: string) => k }))

import CoordinationPanel from './CoordinationPanel'

const wrapper = ({ children }: { children: ReactNode }) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

afterEach(() => { cleanup(); vi.clearAllMocks() })
beforeEach(() => { H.projectGroupId = undefined })

describe('CoordinationPanel', () => {
  it('shows the empty state and does not fetch when the session is untagged', () => {
    H.projectGroupId = undefined
    render(<CoordinationPanel slot="dashboard:s1" />, { wrapper })
    expect(screen.getByText('pages.chat.coordination.untagged')).toBeInTheDocument()
    expect(H.getProjectPanel).not.toHaveBeenCalled()
  })

  it('renders project, member sessions (coordinator badge), collisions and work when tagged', async () => {
    H.projectGroupId = 'grp-1'
    H.getProjectPanel.mockResolvedValue({
      project: { id: 'grp-1', name: 'Acme Coordination', repos: ['acme/api', 'acme/web'] },
      sessions: [
        { session: 'dashboard:s1', title: 'Conductor', agent: 'kirocrew', branch: 'feature/x', is_coordinator: true },
        { session: 'dashboard:s2', title: 'Worker A', agent: 'kirocrew', branch: 'feature/y' },
      ],
      collisions: [
        { signal: 'same-file', repo_rel_path: 'src/app.ts', sessions: ['dashboard:s1', 'dashboard:s2'] },
      ],
      work: [
        { coordinator: 'dashboard:s1', item_id: 'w1', title: 'Add pagination', state: 'in_progress', status: 'progress', summary: 'wiring the query', pr: 42, round: 1, worker: 'dashboard:s2' },
      ],
    })

    render(<CoordinationPanel slot="dashboard:s1" />, { wrapper })

    await waitFor(() => expect(screen.getByText('Acme Coordination')).toBeInTheDocument())
    expect(H.getProjectPanel).toHaveBeenCalledWith('grp-1')
    // member sessions + coordinator badge
    expect(screen.getByText('Conductor')).toBeInTheDocument()
    expect(screen.getByText('Worker A')).toBeInTheDocument()
    expect(screen.getByText('pages.chat.coordination.coordinator')).toBeInTheDocument()
    // collision flag (label + path)
    expect(screen.getByText(/pages\.chat\.coordination\.same_file/)).toBeInTheDocument()
    // collision session ids resolved to member titles (not raw dashboard:sN keys)
    expect(screen.getByText('Conductor, Worker A')).toBeInTheDocument()
    // work rollup
    expect(screen.getByText('Add pagination')).toBeInTheDocument()
    expect(screen.getByText(/PR #42/)).toBeInTheDocument()
  })

  it('shows the loading state while the panel query is pending', () => {
    H.projectGroupId = 'grp-1'
    // never-resolving promise keeps the query in its pending/loading state
    H.getProjectPanel.mockReturnValue(new Promise(() => {}))
    render(<CoordinationPanel slot="dashboard:s1" />, { wrapper })
    expect(screen.getByText('pages.chat.coordination.loading')).toBeInTheDocument()
  })

  it('shows the error state when the panel query rejects', async () => {
    H.projectGroupId = 'grp-1'
    H.getProjectPanel.mockRejectedValue(new Error('boom'))
    render(<CoordinationPanel slot="dashboard:s1" />, { wrapper })
    await waitFor(() =>
      expect(screen.getByText('pages.chat.coordination.error')).toBeInTheDocument(),
    )
    expect(screen.getByText('pages.chat.coordination.error_hint')).toBeInTheDocument()
  })

  it('renders a same-worktree collision without appending a repo path, and empty work/collisions arms', async () => {
    H.projectGroupId = 'grp-1'
    H.getProjectPanel.mockResolvedValue({
      project: { id: 'grp-1', name: 'Beta', repos: [] },
      sessions: [{ session: 'dashboard:s1', title: 'Solo' }],
      // same-worktree carries NO repo_rel_path — label must not append one
      collisions: [{ signal: 'same-worktree', sessions: ['dashboard:s1', 'dashboard:s2'] }],
      work: [],
    })

    render(<CoordinationPanel slot="dashboard:s1" />, { wrapper })

    await waitFor(() => expect(screen.getByText('Beta')).toBeInTheDocument())
    const label = screen.getByText('pages.chat.coordination.same_worktree')
    // the same-worktree label renders verbatim (no ": <path>" suffix)
    expect(label.textContent).toBe('pages.chat.coordination.same_worktree')
    // empty repos + empty work arrays render nothing extra (no throw, no work header)
    expect(screen.queryByText(/pages\.chat\.coordination\.work/)).not.toBeInTheDocument()
  })
})
