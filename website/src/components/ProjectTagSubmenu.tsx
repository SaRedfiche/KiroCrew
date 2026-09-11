import { FolderGit2, Check, ChevronRight, Plus, X } from 'lucide-react'
import {
  DropdownMenuSub, DropdownMenuSubTrigger, DropdownMenuSubContent, DropdownMenuItem, DropdownMenuSeparator,
} from './ui/dropdown-menu'
import {
  ContextMenuSub, ContextMenuSubTrigger, ContextMenuSubContent, ContextMenuItem, ContextMenuSeparator,
} from './ui/context-menu'

/** One project-coordination record as the list route returns it. */
export interface CoordinationProject {
  readonly id: string
  readonly name: string
  readonly repos?: readonly string[]
}

interface ProjectTagSubmenuProps {
  readonly projects: readonly CoordinationProject[]
  /** Attach to an EXISTING project by id. */
  readonly onPick: (projectGroupId: string) => void
  /** CREATE a new project by name and attach. */
  readonly onCreate: (name: string) => void
  /** UNTAG the session (clear its project_group_id). */
  readonly onUntag: () => void
  /** The session's current project_group_id; the matching entry shows a check. */
  readonly currentProjectGroupId?: string | null
  /** Which menu family this submenu nests inside. */
  readonly variant: 'dropdown' | 'context'
  /** Trigger label (defaults to "Project"). */
  readonly label?: string
}

/**
 * "Project" as a native Radix submenu, mirroring FolderMoveSubmenu so the
 * session menus (sidebar dropdown + right-click context, and the header
 * dropdown) all tag a session into a project identically. It is the
 * interactive create-or-pick surface over the tagging API
 * (POST /api/chat/slots/{slot}/project-group): pick an existing project,
 * create a new one, or untag.
 *
 * `variant` selects the primitive family: a submenu must use the same family
 * as its parent (a DropdownMenuSub only inside a DropdownMenu, a
 * ContextMenuSub inside a ContextMenu).
 *
 * The "New project…" name is collected with a native `prompt()` — deliberately
 * minimal: this is the Phase-4-first evaluable increment, not the final modal.
 */
export default function ProjectTagSubmenu({
  projects,
  onPick,
  onCreate,
  onUntag,
  currentProjectGroupId,
  variant,
  label = 'Project',
}: ProjectTagSubmenuProps) {
  const Sub = variant === 'context' ? ContextMenuSub : DropdownMenuSub
  const SubTrigger = variant === 'context' ? ContextMenuSubTrigger : DropdownMenuSubTrigger
  const SubContent = variant === 'context' ? ContextMenuSubContent : DropdownMenuSubContent
  const Item = variant === 'context' ? ContextMenuItem : DropdownMenuItem
  const Separator = variant === 'context' ? ContextMenuSeparator : DropdownMenuSeparator

  const tagged = currentProjectGroupId != null && currentProjectGroupId !== ''

  const handleCreate = () => {
    // A native prompt keeps the increment small; the string is trimmed and a
    // blank cancel is a no-op (the backend also rejects a blank name).
    const name = window.prompt('New project name')
    if (name != null && name.trim() !== '') onCreate(name.trim())
  }

  return (
    <Sub>
      <SubTrigger>
        <FolderGit2 size={13} className="shrink-0 text-muted" />
        <span className="flex-1">{label}</span>
        <ChevronRight size={12} className="text-muted" />
      </SubTrigger>
      <SubContent className="min-w-[190px] max-h-[280px] overflow-y-auto">
        <Item title="Create a new project and tag this session into it" onSelect={handleCreate}>
          <Plus size={13} className="text-accent shrink-0" />
          <span className="truncate">New project…</span>
        </Item>
        {tagged && (
          <Item title="Remove this session from its project" onSelect={onUntag}>
            <X size={13} className="text-muted shrink-0" />
            <span className="truncate">Untag</span>
          </Item>
        )}
        {projects.length > 0 && <Separator />}
        {projects.map(p => (
          <Item key={p.id} title={p.name} onSelect={() => onPick(p.id)}>
            <FolderGit2 size={13} className="text-accent shrink-0" />
            <span className="truncate">{p.name}</span>
            {currentProjectGroupId === p.id && <Check size={13} className="ml-auto text-accent shrink-0" />}
          </Item>
        ))}
      </SubContent>
    </Sub>
  )
}
