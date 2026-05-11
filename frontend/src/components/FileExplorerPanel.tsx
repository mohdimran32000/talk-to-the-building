import { useEffect, useRef, useState } from 'react'
import { useAuth } from '@/contexts/AuthContext'
import { Button } from '@/components/ui/button'
import { supabase } from '@/lib/supabase'
import { Upload } from 'lucide-react'
import type { UploadedFile, MetadataFieldDefinition } from '@/lib/api'
import { RootSection } from './explorer/RootSection'
import { Breadcrumbs } from './explorer/Breadcrumbs'

export interface SelectedFolder {
  scope: 'user' | 'global'
  path: string
}

interface FileExplorerPanelProps {
  files: UploadedFile[]
  onUpload: (file: File, folder_path: string, scope: 'user' | 'global') => void
  onDelete: (fileId: string) => void
  onRename?: (fileId: string, newName: string) => void
  // LOCKED signature (Phase 6 checker WARNING #3): fourth optional arg propagates content_markdown_status
  onStatusUpdate: (documentId: string, status: string, errorMessage?: string, contentMarkdownStatus?: string) => void
  // metadataSchema is forwarded as documentation for downstream wiring (Plan 06-09 may render in DocumentRow detail).
  metadataSchema?: MetadataFieldDefinition[] | null
}

export default function FileExplorerPanel({
  files,
  onUpload,
  onDelete,
  onRename,
  onStatusUpdate,
  metadataSchema: _metadataSchema,
}: FileExplorerPanelProps) {
  const { isAdmin } = useAuth()
  const inputRef = useRef<HTMLInputElement>(null)
  const [selectedFolder, setSelectedFolder] = useState<SelectedFolder>({ scope: 'user', path: '/' })

  // Polling pattern — verbatim from FileUploadPanel.tsx:60-85; ALSO poll content_markdown_status (D-03 / UI-08)
  useEffect(() => {
    const hasPending = files.some(
      (f) => f.status === 'pending' || f.status === 'processing' || f.content_markdown_status === 'pending'
    )
    if (!hasPending) return
    const interval = setInterval(async () => {
      try {
        const { data } = await supabase
          .from('documents')
          .select('id, status, error_message, content_markdown_status')
          .in(
            'id',
            files
              .filter((f) => f.status === 'pending' || f.status === 'processing' || f.content_markdown_status === 'pending')
              .map((f) => f.id)
          )
        if (data) {
          for (const doc of data) {
            const current = files.find((f) => f.id === doc.id)
            if (current && (current.status !== doc.status || current.content_markdown_status !== doc.content_markdown_status)) {
              // 4-arg call (LOCKED per checker WARNING #3): pass content_markdown_status as the fourth arg
              onStatusUpdate(doc.id, doc.status, doc.error_message, doc.content_markdown_status)
            }
          }
        }
      } catch {
        /* swallow */
      }
    }, 2000)
    return () => clearInterval(interval)
  }, [files, onStatusUpdate])

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      // UI-05: upload defaults into the currently-selected folder; if user is non-admin and selected scope is 'global',
      // fall back to root user (UI-11 defense — non-admin can never write to global)
      const targetScope = selectedFolder.scope
      const targetPath = selectedFolder.path
      const safeScope = targetScope === 'global' && !isAdmin ? 'user' : targetScope
      const safePath = safeScope === targetScope ? targetPath : '/'
      onUpload(file, safePath, safeScope)
      e.target.value = ''
    }
  }

  const onPanelClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = (e.target as HTMLElement).closest('[data-folder-path]') as HTMLElement | null
    if (!target) return
    const path = target.getAttribute('data-folder-path')
    const scope = target.getAttribute('data-scope') as 'user' | 'global' | null
    if (path && scope) setSelectedFolder({ scope, path })
  }

  return (
    <div className="flex flex-col h-full">
      <div className="border-b px-3 py-2 flex items-center justify-between">
        <Breadcrumbs
          path={selectedFolder.path}
          scopeLabel={selectedFolder.scope === 'global' ? 'Shared' : 'My Files'}
          onNavigate={(path) => setSelectedFolder({ scope: selectedFolder.scope, path })}
        />
        <div className="flex items-center gap-1">
          <input ref={inputRef} type="file" hidden onChange={handleFileChange} />
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => inputRef.current?.click()}
            title={`Upload into ${selectedFolder.scope === 'global' ? 'Shared' : 'My Files'} ${selectedFolder.path}`}
          >
            <Upload className="w-3.5 h-3.5 mr-1" /> Upload
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto" data-testid="file-explorer-body" onClick={onPanelClick}>
        <RootSection
          scope="global"
          onDeleteDocument={onDelete}
          onRenameDocument={onRename}
        />
        <RootSection
          scope="user"
          onDeleteDocument={onDelete}
          onRenameDocument={onRename}
        />
      </div>
    </div>
  )
}
