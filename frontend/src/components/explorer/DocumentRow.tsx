import { useState } from 'react'
import { File as FileIcon } from 'lucide-react'
import { toast } from 'sonner'
import { renameDocument, type UploadedFile } from '@/lib/api'
import { ContextMenu, ContextMenuTrigger } from '@/components/ui/context-menu'
import { ScopeBadge } from './ScopeBadge'
import { StatusBadge } from './StatusBadge'
import { DocumentContextMenuActions } from './ContextMenuActions'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

interface DocumentRowProps {
  doc: UploadedFile
  depth: number                           // for indent: marginLeft = depth * 16px
  onDelete?: (id: string) => void
  onRename?: (id: string, newName: string) => void
  // Plan 06-10 wraps this in @dnd-kit useDraggable
}

export function DocumentRow({ doc, depth, onDelete, onRename }: DocumentRowProps) {
  const [renameMode, setRenameMode] = useState(false)
  const [renameValue, setRenameValue] = useState(doc.file_name)

  const submitRename = async () => {
    const trimmed = renameValue.trim()
    if (!trimmed || trimmed === doc.file_name) {
      setRenameMode(false)
      setRenameValue(doc.file_name)
      return
    }
    try {
      const updated = await renameDocument(doc.id, trimmed)
      onRename?.(doc.id, updated.file_name)
      toast.success('Renamed')
      setRenameMode(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Rename failed')
      setRenameValue(doc.file_name)
    }
  }

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div
          data-document-id={doc.id}
          data-scope={doc.scope}
          className="flex items-center justify-between rounded-md bg-muted/30 px-3 py-1.5 text-sm"
          style={{ marginLeft: `${depth * 16}px` }}
          role="treeitem"
          aria-level={depth + 1}
          tabIndex={-1}
        >
          <div className="flex items-center gap-2 min-w-0 flex-wrap">
            <FileIcon className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
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
                    setRenameValue(doc.file_name)
                  }
                }}
                className="bg-transparent border border-border rounded px-1 text-sm min-w-0 flex-1"
              />
            ) : (
              <span
                className="truncate cursor-text"
                title={doc.file_name}
                onClick={(e) => {
                  // UI-07: click name → input swaps in for inline rename
                  e.stopPropagation()
                  setRenameMode(true)
                }}
              >
                {doc.file_name}
              </span>
            )}
            <span className="shrink-0 text-xs text-muted-foreground">{formatSize(doc.file_size)}</span>
            <StatusBadge status={doc.status} contentMarkdownStatus={doc.content_markdown_status ?? undefined} />
            <ScopeBadge scope={doc.scope} />
          </div>
          {onDelete && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                onDelete(doc.id)
              }}
              className="ml-2 shrink-0 text-muted-foreground hover:text-destructive text-xs"
              title="Delete file"
            >
              ✕
            </button>
          )}
        </div>
      </ContextMenuTrigger>
      <DocumentContextMenuActions
        scope={doc.scope}
        onRename={() => setRenameMode(true)}
        onDelete={() => onDelete?.(doc.id)}
      />
    </ContextMenu>
  )
}
