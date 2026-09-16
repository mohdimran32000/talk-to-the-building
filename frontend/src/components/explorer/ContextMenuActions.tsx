import { useAuth } from '@/contexts/AuthContext'
import {
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
} from '@/components/ui/context-menu'
import { FolderPlus, Pencil, Trash2 } from 'lucide-react'

interface FolderContextMenuActionsProps {
  scope: 'user' | 'global'
  isRoot?: boolean                                 // root '/' has no folders-table row → suppress rename/delete
  hasFolderId: boolean                             // D-06: false for inferred-only folders → disable rename/delete
  onCreateChild: () => void
  onRename?: () => void
  onDelete?: () => void
}

/**
 * UI-11 / Pitfall 11: write affordances on `scope='global'` are gated behind
 * `isAdmin === true`. Non-admin users on Shared folders see ONLY a disabled
 * "Read-only (admin required)" item — NEVER a conditional render between
 * scopes; the gating is structural (canWrite = scope === 'user' || isAdmin).
 *
 * D-06: rename/delete require a real folders-table UUID. Suppressed for root
 * '/' AND for inferred-only folders (no folders row exists to PATCH/DELETE).
 */
export function FolderContextMenuActions({
  scope,
  isRoot,
  hasFolderId,
  onCreateChild,
  onRename,
  onDelete,
}: FolderContextMenuActionsProps) {
  const { isAdmin } = useAuth()
  const canWrite = scope === 'user' || isAdmin

  if (!canWrite) {
    // Non-admin viewing global folder: render disabled explanatory item only
    return (
      <ContextMenuContent>
        <ContextMenuItem disabled>Read-only (admin required)</ContextMenuItem>
      </ContextMenuContent>
    )
  }

  const canRenameDelete = !isRoot && hasFolderId

  return (
    <ContextMenuContent>
      <ContextMenuItem onSelect={onCreateChild}>
        <FolderPlus className="w-3.5 h-3.5 mr-2" /> New folder
      </ContextMenuItem>
      {canRenameDelete && (
        <>
          <ContextMenuSeparator />
          <ContextMenuItem onSelect={onRename} disabled={!onRename}>
            <Pencil className="w-3.5 h-3.5 mr-2" /> Rename
          </ContextMenuItem>
          <ContextMenuItem
            onSelect={onDelete}
            disabled={!onDelete}
            className="text-destructive focus:text-destructive"
          >
            <Trash2 className="w-3.5 h-3.5 mr-2" /> Delete
          </ContextMenuItem>
        </>
      )}
    </ContextMenuContent>
  )
}

interface DocumentContextMenuActionsProps {
  scope: 'user' | 'global'
  onRename: () => void
  onDelete: () => void
}

export function DocumentContextMenuActions({ scope, onRename, onDelete }: DocumentContextMenuActionsProps) {
  const { isAdmin } = useAuth()
  const canWrite = scope === 'user' || isAdmin

  if (!canWrite) {
    return (
      <ContextMenuContent>
        <ContextMenuItem disabled>Read-only (admin required)</ContextMenuItem>
      </ContextMenuContent>
    )
  }

  return (
    <ContextMenuContent>
      <ContextMenuItem onSelect={onRename}>
        <Pencil className="w-3.5 h-3.5 mr-2" /> Rename
      </ContextMenuItem>
      <ContextMenuItem onSelect={onDelete} className="text-destructive focus:text-destructive">
        <Trash2 className="w-3.5 h-3.5 mr-2" /> Delete
      </ContextMenuItem>
    </ContextMenuContent>
  )
}
