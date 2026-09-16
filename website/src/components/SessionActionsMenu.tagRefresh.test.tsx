import { screen, fireEvent, waitFor } from '@testing-library/react'
import { renderWithProviders, createTestStore } from '../test/helpers'
import SessionActionsMenu from './SessionActionsMenu'
import { sseSlots } from '../store/dashboardSlice'
import { api } from '../api/client'
import type { ChatSlot } from '../types'

/**
 * Regression: a tag write MUST invalidate BOTH the ['coordination-projects']
 * list AND the ['chat-slots'] list. The slot list is what carries
 * project_group_id, and the Coordination side-panel tab is withheld until
 * currentSlot.project_group_id is set. If afterTagWrite invalidates only the
 * projects list (trusting the slot stream to push project_group_id), the tab
 * never appears after tagging and an untag never visibly clears — the exact
 * pair of bugs a local pod drive surfaced. Pin both invalidations here.
 */

// happy-dom cannot drive Radix menus, so items collapse to buttons.
const { Item, Separator } = vi.hoisted(() => ({
  Item: ({ children, onSelect, disabled }: {
    children?: React.ReactNode
    onSelect?: () => void
    disabled?: boolean
  }) => (
    <button type="button" disabled={disabled} onClick={() => onSelect?.()}>{children}</button>
  ),
  Separator: () => <hr data-testid="zzq-sep" />,
}))

vi.mock('./ui/dropdown-menu', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  DropdownMenuItem: Item,
  DropdownMenuSeparator: Separator,
}))
vi.mock('./ui/context-menu', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ContextMenuItem: Item,
  ContextMenuSeparator: Separator,
}))

// Stub sibling sections; this file exercises only the tag-write refresh.
vi.mock('./FolderMoveSubmenu', () => ({ default: () => <div>zzq-folders</div> }))
vi.mock('./SendToInstanceSubmenu', () => ({ default: () => <div>zzq-send</div> }))
vi.mock('./SessionColorSwatches', () => ({ default: () => <div>zzq-colors</div> }))
vi.mock('./LinkedSurfacesSection', () => ({ default: () => <div>zzq-links</div> }))
// Stub the tag submenu to plain buttons that fire the callbacks SessionActionsMenu
// passes it — the wiring under test is afterTagWrite, not the submenu's Radix UI.
vi.mock('./ProjectTagSubmenu', () => ({
  default: ({ onCreate, onUntag, onPick, currentProjectGroupId }: {
    onCreate: (n: string) => void
    onUntag: () => void
    onPick: (id: string) => void
    currentProjectGroupId?: string
  }) => (
    <div>
      <button type="button" onClick={() => onCreate('aidlc-migration')}>New project…</button>
      <button type="button" onClick={() => onPick('grp-1')}>Pick project</button>
      {currentProjectGroupId ? <button type="button" onClick={() => onUntag()}>Untag</button> : null}
    </div>
  ),
}))

const actions = vi.hoisted(() => ({
  toggleRead: vi.fn(), togglePin: vi.fn(), toggleMode: vi.fn(),
  copyLink: vi.fn(), move: vi.fn(), reload: vi.fn(), close: vi.fn(),
}))
const popouts = vi.hoisted(() => ({
  isPoppedOut: vi.fn(() => false), isSelfPopout: vi.fn(() => false),
  open: vi.fn(), focus: vi.fn(), bringBack: vi.fn(), returnSelfToMain: vi.fn(),
}))
vi.mock('../hooks/useSessionActions', () => ({ useSessionActions: () => actions }))
vi.mock('../hooks/useChatPopouts', () => ({ useChatPopouts: () => popouts }))
vi.mock('../hooks/useTagPopover', () => ({ useTagPopover: () => ({ open: vi.fn() }) }))
vi.mock('../api/client', async importOriginal => {
  const mod = await importOriginal<typeof import('../api/client')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      chatFolders: vi.fn().mockResolvedValue({ folders: [] }),
      listCoordinationProjects: vi.fn().mockResolvedValue({ projects: [{ id: 'grp-1', name: 'aidlc-migration' }] }),
      setSlotProjectGroup: vi.fn().mockResolvedValue({ ok: true }),
    },
  }
})

const setSlotProjectGroup = vi.mocked(api.setSlotProjectGroup)

function renderMenu(slotOverrides: Partial<ChatSlot> = {}) {
  const store = createTestStore()
  const slot = { key: 'slot-a', title: 'Conductor', messages: 0, running: false, ...slotOverrides } as ChatSlot
  store.dispatch(sseSlots([slot]))
  const utils = renderWithProviders(
    <SessionActionsMenu slotKey="slot-a" variant="dropdown" />,
    { store },
  )
  return utils
}

describe('SessionActionsMenu tag write refresh', () => {
  beforeEach(() => setSlotProjectGroup.mockClear())

  it('a create tag write invalidates BOTH coordination-projects and chat-slots', async () => {
    const { queryClient } = renderMenu()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    fireEvent.click(await screen.findByText('New project…'))

    await waitFor(() => expect(setSlotProjectGroup).toHaveBeenCalledWith('slot-a', { name: 'aidlc-migration' }))
    await waitFor(() => {
      const keys = spy.mock.calls.map(c => JSON.stringify((c[0] as { queryKey: unknown }).queryKey))
      expect(keys).toContain(JSON.stringify(['coordination-projects']))
      expect(keys).toContain(JSON.stringify(['chat-slots']))
    })
  })

  it('an untag write invalidates BOTH lists so the tab guard and the check clear', async () => {
    // A tagged slot renders the "Untag" item.
    const { queryClient } = renderMenu({ project_group_id: 'grp-1' } as Partial<ChatSlot>)
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    fireEvent.click(await screen.findByText('Untag'))

    await waitFor(() => expect(setSlotProjectGroup).toHaveBeenCalledWith('slot-a', null))
    await waitFor(() => {
      const keys = spy.mock.calls.map(c => JSON.stringify((c[0] as { queryKey: unknown }).queryKey))
      expect(keys).toContain(JSON.stringify(['coordination-projects']))
      expect(keys).toContain(JSON.stringify(['chat-slots']))
    })
  })
})
