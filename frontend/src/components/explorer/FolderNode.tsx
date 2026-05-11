import { useState, useEffect } from 'react'
import { ChevronRight, ChevronDown, Folder, FolderOpen, Plus, MoreVertical } from 'lucide-react'
import { toast } from 'sonner'
import { useDroppable } from '@dnd-kit/core'
import { listFolder, renameFolder, type ListFolderResponse, type UploadedFile } from '@/lib/api'
import { useAuth } from '@/contexts/AuthContext'
import { ContextMenu, ContextMenuTrigger } from '@/components/ui/context-menu'
import { DocumentRow } from './DocumentRow'
import { useExpandedState } from './FolderTree'
import { FolderContextMenuActions } from './ContextMenuActions'
import { CreateFolderDialog } from './CreateFolderDialog'
import { DeleteFolderDialog } from './DeleteFolderDialog'

export interface FolderNodeProps {
  scope: 'user' | 'global'
  folderId: string | null                            // D-06 / Plan 06-12: UUID of the explicit folders row;
                                                     // null when this is a root '/' OR an inferred-only folder.
                                                     // Plan 06-09 disables Rename/Delete affordances when folderId is null.
  path: string                                       // canonical (e.g. '/' or '/projects/2025')
  depth: number                                      // 0 for root section's immediate children
  isOpen: boolean                                    // controlled by parent (FolderTree → useOpenFoldersStorage)
  onToggle: (scope: 'user' | 'global', path: string) => void
  onDeleteDocument?: (id: string) => void
  onRenameDocument?: (id: string, newName: string) => void
  onAfterMutation?: () => void                       // Plan 06-09 Task 3d: bubble CRUD completion up to FolderTree
                                                     // so it can force-remount the tree and re-fetch contents.
  // DnD wiring lands in Plan 06-10 (useDroppable on the header div)
}

export function FolderNode({
  scope,
  folderId,
  path,
  depth,
  isOpen,
  onToggle,
  onDeleteDocument,
  onRenameDocument,
  onAfterMutation,
}: FolderNodeProps) {
  const { isAdmin } = useAuth()
  const canWrite = scope === 'user' || isAdmin
  const hasFolderId = folderId !== null
  const isRoot = path === '/'

  const [contents, setContents] = useState<ListFolderResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const folderName = isRoot ? '/' : path.slice(path.lastIndexOf('/') + 1)

  // CRUD state
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [renameMode, setRenameMode] = useState(false)
  const [renameValue, setRenameValue] = useState(folderName)

  // Plan 06-10: register folder header as a @dnd-kit drop target. The "into-folder"
  // highlight ring shows only while a document drag is hovering over us.
  const dropId = `folder:${scope}:${path}`
  const { setNodeRef: setDropRef, isOver, active } = useDroppable({
    id: dropId,
    data: { type: 'folder', scope, path },
  })
  const isHotTarget = isOver && (active?.data.current as { type?: string } | undefined)?.type === 'document'

  // Lazy-load children on first expand (per RESEARCH.md §FileExplorerPanel "lazy loading prevents loading the entire tree upfront for a 200-folder corpus")
  useEffect(() => {
    if (!isOpen || contents !== null || loading) return
    setLoading(true)
    setError(null)
    listFolder(path, scope)
      .then((res) => setContents(res))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load folder'))
      .finally(() => setLoading(false))
  }, [isOpen, contents, loading, path, scope])

  const childCount = contents ? contents.documents.length + contents.subfolders.length : null

  const submitRename = async () => {
    if (!folderId) {
      setRenameMode(false)
      return
    }
    const trimmed = renameValue.trim().replace(/^\/+|\/+$/g, '')
    if (!trimmed || trimmed === folderName) {
      setRenameMode(false)
      setRenameValue(folderName)
      return
    }
    // D-06: folderId is in props — no path→id round-trip needed
    const newPath = path.replace(/[^/]+$/, trimmed)
    try {
      await renameFolder(folderId, newPath)
      toast.success(`Renamed to ${newPath}`)
      setRenameMode(false)
      onAfterMutation?.()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Rename failed')
      setRenameValue(folderName)
    }
  }

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div
          ref={setDropRef}
          role="treeitem"
          aria-level={depth + 1}
          aria-expanded={isOpen}
          data-folder-path={path}
          data-folder-id={folderId ?? ''}
          data-scope={scope}
          data-drop-active={isHotTarget || undefined}
          className={`group ${isHotTarget ? 'ring-1 ring-blue-400/40 bg-blue-400/10 rounded-md' : ''}`}
        >
          <button
            type="button"
            tabIndex={0}
            onClick={() => onToggle(scope, path)}
            className="flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-sm hover:bg-muted/50 transition-colors text-left"
            style={{ paddingLeft: `${depth * 16 + 8}px` }}
          >
            {isOpen ? <ChevronDown className="w-3.5 h-3.5 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 shrink-0" />}
            {isOpen ? <FolderOpen className="w-4 h-4 shrink-0 text-blue-600" /> : <Folder className="w-4 h-4 shrink-0 text-blue-600" />}
            {renameMode ? (
              <input
                autoFocus
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                onBlur={submitRename}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') submitRename()
                  if (e.key === 'Escape') {
                    setRenameMode(false)
                    setRenameValue(folderName)
                  }
                }}
                className="bg-transparent border border-border rounded px-1 text-sm flex-1 min-w-0"
              />
            ) : (
              <span className="truncate font-medium">{folderName}</span>
            )}
            {childCount !== null && !renameMode && (
              <span className="ml-auto shrink-0 text-xs text-muted-foreground">{childCount}</span>
            )}

            {/* D-05 LOCKED inline buttons — both `+` and `⋯` visible on hover when user has write access */}
            {canWrite && !renameMode && (
              <span className="ml-2 shrink-0 opacity-0 group-hover:opacity-100 flex items-center gap-1 transition-opacity">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    setCreateDialogOpen(true)
                  }}
                  className="rounded p-0.5 hover:bg-muted text-muted-foreground hover:text-foreground"
                  title="New folder"
                  aria-label={`Create child folder under ${folderName}`}
                >
                  <Plus className="w-3 h-3" />
                </button>
                {hasFolderId && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      // D-05: ⋯ opens the SAME ContextMenu as right-click (Create / Rename / Delete),
                      // not a rename shortcut. Dispatch a synthetic contextmenu event on the trigger
                      // so Radix's ContextMenuTrigger opens its menu.
                      const target = e.currentTarget as HTMLElement
                      const rect = target.getBoundingClientRect()
                      target.dispatchEvent(
                        new MouseEvent('contextmenu', {
                          bubbles: true,
                          cancelable: true,
                          clientX: rect.left + rect.width / 2,
                          clientY: rect.bottom,
                        })
                      )
                    }}
                    className="rounded p-0.5 hover:bg-muted text-muted-foreground hover:text-foreground"
                    title="Folder actions"
                    aria-label={`Open ${folderName} folder menu`}
                  >
                    <MoreVertical className="w-3 h-3" />
                  </button>
                )}
              </span>
            )}
          </button>
          {isOpen && (
            <div role="group">
              {loading && (
                <div className="text-xs text-muted-foreground px-2 py-1" style={{ paddingLeft: `${(depth + 1) * 16 + 8}px` }}>Loading…</div>
              )}
              {error && (
                <div className="text-xs text-red-600 px-2 py-1" style={{ paddingLeft: `${(depth + 1) * 16 + 8}px` }}>{error}</div>
              )}
              {contents && (
                <>
                  {/* Subfolders first (alpha order is server's responsibility). D-06: each item is {id, path}. */}
                  {contents.subfolders.map((sub) => (
                    <FolderNodeChildBoundary
                      key={`folder:${sub.path}`}
                      scope={scope}
                      folderId={sub.id ?? null}                /* D-06: thread UUID through; null for inferred-only */
                      path={sub.path}
                      depth={depth + 1}
                      onToggle={onToggle}
                      onDeleteDocument={onDeleteDocument}
                      onRenameDocument={onRenameDocument}
                      onAfterMutation={onAfterMutation}
                    />
                  ))}
                  {/* Then documents */}
                  {contents.documents.map((doc: UploadedFile) => (
                    <DocumentRow
                      key={`doc:${doc.id}`}
                      doc={doc}
                      depth={depth + 1}
                      onDelete={onDeleteDocument}
                      onRename={onRenameDocument}
                    />
                  ))}
                </>
              )}
            </div>
          )}
        </div>
      </ContextMenuTrigger>
      <FolderContextMenuActions
        scope={scope}
        isRoot={isRoot}
        hasFolderId={hasFolderId}
        onCreateChild={() => setCreateDialogOpen(true)}
        onRename={hasFolderId ? () => setRenameMode(true) : undefined}
        onDelete={hasFolderId ? () => setDeleteDialogOpen(true) : undefined}
      />
      <CreateFolderDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
        parentPath={path}
        scope={scope}
        onCreated={() => onAfterMutation?.()}
      />
      {folderId && (
        <DeleteFolderDialog
          open={deleteDialogOpen}
          onOpenChange={setDeleteDialogOpen}
          folderId={folderId}                       /* D-06: UUID from props, no path→id resolution */
          folderPath={path}
          onDeleted={() => onAfterMutation?.()}
        />
      )}
    </ContextMenu>
  )
}

// Child boundary: receives `isOpen` from the FolderTree's open-folders state via the
// internal ExpansionContext. We extract it so the parent FolderNode doesn't need to
// know how isOpen is computed.
function FolderNodeChildBoundary(props: Omit<FolderNodeProps, 'isOpen'>) {
  const isOpen = useExpandedState(props.scope, props.path)
  return <FolderNode {...props} isOpen={isOpen} />
}
