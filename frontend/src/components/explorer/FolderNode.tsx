import { useState, useEffect } from 'react'
import { ChevronRight, ChevronDown, Folder, FolderOpen } from 'lucide-react'
import { listFolder, type ListFolderResponse, type UploadedFile } from '@/lib/api'
import { DocumentRow } from './DocumentRow'
import { useExpandedState } from './FolderTree'

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
  // CRUD wiring (createChild, rename, delete) lands in Plan 06-09 via the ContextMenu
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
}: FolderNodeProps) {
  const [contents, setContents] = useState<ListFolderResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  const folderName = path === '/' ? '/' : path.slice(path.lastIndexOf('/') + 1)
  const childCount = contents ? contents.documents.length + contents.subfolders.length : null

  return (
    <div role="treeitem" aria-level={depth + 1} aria-expanded={isOpen} data-folder-path={path} data-folder-id={folderId ?? ''} data-scope={scope}>
      <button
        type="button"
        tabIndex={0}
        onClick={() => onToggle(scope, path)}
        className="flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-sm hover:bg-muted/50 transition-colors text-left"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {isOpen ? <ChevronDown className="w-3.5 h-3.5 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 shrink-0" />}
        {isOpen ? <FolderOpen className="w-4 h-4 shrink-0 text-blue-600" /> : <Folder className="w-4 h-4 shrink-0 text-blue-600" />}
        <span className="truncate font-medium">{folderName}</span>
        {childCount !== null && (
          <span className="ml-auto shrink-0 text-xs text-muted-foreground">{childCount}</span>
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
  )
}

// Child boundary: receives `isOpen` from the FolderTree's open-folders state via the
// internal ExpansionContext. We extract it so the parent FolderNode doesn't need to
// know how isOpen is computed.
function FolderNodeChildBoundary(props: Omit<FolderNodeProps, 'isOpen'>) {
  const isOpen = useExpandedState(props.scope, props.path)
  return <FolderNode {...props} isOpen={isOpen} />
}
