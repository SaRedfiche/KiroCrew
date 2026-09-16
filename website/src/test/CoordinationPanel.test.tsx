import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ProjectPanel } from '../types'
import { i18nT } from '../i18n/t'

const mockApi = vi.hoisted(() => ({
  getProjectPanel: vi.fn(),
}))
vi.mock('../api/client', () => ({ api: mockApi }))

import CoordinationPanel from '../components/CoordinationPanel'

/** A populated snapshot: a coordinator + two workers, two work items (one with a
 *  PR, one open), and a same-worktree collision between the two workers. */
const panel: ProjectPanel = {
  project: { id: 'grp-1', name: 'aidlc-migration' },
  sessions: [
    { session: 'sess-conductor', title: 'Conductor', agent: 'kirocrew', branch: 'feature/plan', is_coordinator: true },
    { session: 'sess-a', title: 'Worker A', agent: 'kirocrew', branch: 'feature/api' },
    { session: 'sess-b', title: 'Worker B', agent: 'kirocrew', branch: 'feature/ui' },
  ],
  work: [
    { coordinator: 'sess-conductor', item_id: 'w1', title: 'Wire the API layer', state: 'accepted', status: 'done', summary: '', pr: 42, round: 1, worker: 'sess-a' },
    { coordinator: 'sess-conductor', item_id: 'w2', title: 'Build the panel UI', state: 'open', status: 'in progress', summary: '', pr: null, round: 1, worker: 'sess-b' },
  ],
  collisions: [
    { signal: 'same-worktree', sessions: ['sess-a', 'sess-b'] },
  ],
}

function renderPanel(id = 'grp-1', onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    client,
    onClose,
    ...render(
      <QueryClientProvider client={client}>
        <CoordinationPanel projectGroupId={id} onClose={onClose} />
      </QueryClientProvider>,
    ),
  }
}

beforeEach(() => {
  mockApi.getProjectPanel.mockReset()
  mockApi.getProjectPanel.mockResolvedValue(panel)
})

describe('CoordinationPanel', () => {
  it('fetches the panel for the active project group and renders its name', async () => {
    renderPanel('grp-1')
    expect(await screen.findByText('aidlc-migration')).toBeInTheDocument()
    expect(mockApi.getProjectPanel).toHaveBeenCalledWith('grp-1')
  })

  it('renders members, work, and collisions with the coordinator badge and PR number', async () => {
    renderPanel()
    await screen.findByText('aidlc-migration')

    // Members: the coordinator has a unique title; the workers also appear as
    // collision participants, so their name matches more than once.
    expect(screen.getByText('Conductor')).toBeInTheDocument()
    expect(screen.getAllByText('Worker A').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Worker B').length).toBeGreaterThan(0)
    expect(screen.getByText(i18nT('components.coordinationPanel.coordinator'))).toBeInTheDocument()

    // Work: both items, with the state pill and the PR number.
    expect(screen.getByText('Wire the API layer')).toBeInTheDocument()
    expect(screen.getByText('Build the panel UI')).toBeInTheDocument()
    expect(screen.getByText('#42')).toBeInTheDocument()

    // Collision: the same-worktree flag, with worker TITLES (not raw keys)
    // resolved from the session set, joined on one line.
    expect(screen.getByText(i18nT('components.coordinationPanel.same_worktree'))).toBeInTheDocument()
    expect(screen.getByText('Worker A, Worker B')).toBeInTheDocument()

    // Header count pills use i18next plurals: 3 sessions is plural, 1 collision
    // is singular (the fix for a "1 collisions" copy bug).
    expect(screen.getByText(i18nT('components.coordinationPanel.session_count', { count: 3 }))).toBeInTheDocument()
    expect(screen.getByText(i18nT('components.coordinationPanel.collision_count', { count: 1 }))).toBeInTheDocument()
    expect(i18nT('components.coordinationPanel.collision_count', { count: 1 })).toBe('1 collision')
    expect(i18nT('components.coordinationPanel.session_count', { count: 3 })).toBe('3 sessions')
  })

  it('orders the sections header -> members -> work -> collisions', async () => {
    const { container } = renderPanel()
    await screen.findByText('aidlc-migration')

    // Match each section by its own label span (exact text), not a substring of
    // the section body — "Worker" would otherwise register as a "Work" hit.
    const labelOf = (needle: string) => {
      const sections = Array.from(container.querySelectorAll('section'))
      return sections.findIndex(s =>
        Array.from(s.querySelectorAll('span')).some(el => el.textContent === needle),
      )
    }
    const members = labelOf(i18nT('components.coordinationPanel.members'))
    const work = labelOf(i18nT('components.coordinationPanel.work'))
    const collisions = labelOf(i18nT('components.coordinationPanel.collisions'))
    // Each section must be PRESENT (index >= 0), not just relatively ordered —
    // otherwise a dropped middle section could ride on -1 arithmetic.
    expect(members).toBeGreaterThanOrEqual(0)
    expect(work).toBeGreaterThanOrEqual(0)
    expect(collisions).toBeGreaterThanOrEqual(0)
    expect(work).toBeGreaterThan(members)
    expect(collisions).toBeGreaterThan(work)
  })

  it('resolves a same-file collision path and its participants', async () => {
    mockApi.getProjectPanel.mockResolvedValue({
      ...panel,
      collisions: [{ signal: 'same-file', repo_rel_path: 'src/app.py', sessions: ['sess-a', 'sess-b'] }],
    })
    renderPanel()
    await screen.findByText('aidlc-migration')
    expect(screen.getByText(i18nT('components.coordinationPanel.same_file'))).toBeInTheDocument()
    expect(screen.getByText('src/app.py')).toBeInTheDocument()
  })

  it('shows the empty state when no sessions, work, or collisions are present', async () => {
    mockApi.getProjectPanel.mockResolvedValue({
      project: { id: 'grp-1', name: 'aidlc-migration' },
      sessions: [],
      work: [],
      collisions: [],
    })
    renderPanel()
    await screen.findByText('aidlc-migration')
    expect(screen.getByText(i18nT('components.coordinationPanel.empty_state'))).toBeInTheDocument()
  })

  it('surfaces a load error via the ask-agent notice and keeps polling data out', async () => {
    mockApi.getProjectPanel.mockRejectedValue(new Error('gateway down'))
    renderPanel()
    // The component retries once before surfacing the error, so wait past the
    // react-query retry backoff.
    const err = await screen.findByTestId('coordination-panel-error', {}, { timeout: 5000 })
    expect(err).toBeInTheDocument()
    expect(within(err).getByText('gateway down')).toBeInTheDocument()
    // "keeps data out": the section bodies must not render alongside the error,
    // even if a prior poll had populated data.
    expect(screen.queryByText(i18nT('components.coordinationPanel.members'))).toBeNull()
    expect(screen.queryByText(i18nT('components.coordinationPanel.work'))).toBeNull()
  })

  it('does not fetch when the session is not tagged into any project group', async () => {
    renderPanel('')
    // Empty id disables the query — no request, no crash.
    await waitFor(() => expect(mockApi.getProjectPanel).not.toHaveBeenCalled())
  })

  it('falls back to the raw session key when a work item worker is not in the session set', async () => {
    mockApi.getProjectPanel.mockResolvedValue({
      ...panel,
      work: [
        { coordinator: 'sess-conductor', item_id: 'w1', title: 'Orphan work', state: 'open', status: 'queued', summary: '', pr: null, round: 1, worker: 'sess-ghost' },
      ],
      collisions: [],
    })
    renderPanel()
    await screen.findByText('aidlc-migration')
    // A worker not among this group's sessions shows its key rather than crashing.
    expect(screen.getByText('sess-ghost')).toBeInTheDocument()
  })

  it('falls back to the raw key for a COLLISION participant absent from the session set', async () => {
    // The label() fallback also guards the collision row — a colliding session
    // key not present in sessions[] must render its raw key, not vanish.
    mockApi.getProjectPanel.mockResolvedValue({
      ...panel,
      collisions: [{ signal: 'same-file', repo_rel_path: 'src/app.py', sessions: ['sess-a', 'sess-ghost'] }],
    })
    renderPanel()
    await screen.findByText('aidlc-migration')
    expect(screen.getByText('Worker A, sess-ghost')).toBeInTheDocument()
  })

  it('renders the singular/plural count pills from the COMPONENT (not just the catalog)', async () => {
    // 1 session -> singular, 2 collisions -> plural, driven by the component's
    // own sessions.length / collisions.length wiring.
    mockApi.getProjectPanel.mockResolvedValue({
      project: { id: 'grp-1', name: 'aidlc-migration' },
      sessions: [{ session: 'sess-a', title: 'Worker A', agent: 'kirocrew', branch: 'feature/api' }],
      work: [],
      collisions: [
        { signal: 'same-file', repo_rel_path: 'a.py', sessions: ['sess-a'] },
        { signal: 'same-worktree', sessions: ['sess-a'] },
      ],
    })
    renderPanel()
    await screen.findByText('aidlc-migration')
    expect(screen.getByText('1 session')).toBeInTheDocument()
    expect(screen.getByText('2 collisions')).toBeInTheDocument()
  })

  it('does not crash on a 200 with an unexpected body (defensive project?.name guard)', async () => {
    // A malformed/skewed 200 (no project/sessions keys) must degrade to the
    // loading/empty affordance, never throw and blank the panel.
    mockApi.getProjectPanel.mockResolvedValue({} as never)
    renderPanel()
    // The header falls back to the loading label rather than throwing on .name.
    expect(await screen.findByText(i18nT('components.coordinationPanel.loading'))).toBeInTheDocument()
  })
})
