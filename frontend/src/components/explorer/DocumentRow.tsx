import { File as FileIcon } from 'lucide-react'
import type { UploadedFile } from '@/lib/api'
import { ScopeBadge } from './ScopeBadge'
import { StatusBadge } from './StatusBadge'

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
  // Plan 06-10 wraps this in @dnd-kit useDraggable; Plan 06-09 wires CRUD callbacks
}

export function DocumentRow({ doc, depth, onDelete }: DocumentRowProps) {
  return (
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
        <span className="truncate" title={doc.file_name}>{doc.file_name}</span>
        <span className="shrink-0 text-xs text-muted-foreground">{formatSize(doc.file_size)}</span>
        <StatusBadge status={doc.status} contentMarkdownStatus={doc.content_markdown_status ?? undefined} />
        <ScopeBadge scope={doc.scope} />
      </div>
      {onDelete && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onDelete(doc.id) }}
          className="ml-2 shrink-0 text-muted-foreground hover:text-destructive text-xs"
          title="Delete file"
        >✕</button>
      )}
    </div>
  )
}
