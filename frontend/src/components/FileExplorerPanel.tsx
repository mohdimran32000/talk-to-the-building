import { useEffect, useRef, useState } from 'react'
import { useAuth } from '@/contexts/AuthContext'
import { Button } from '@/components/ui/button'
import { supabase } from '@/lib/supabase'
import { Upload } from 'lucide-react'
import { toast } from 'sonner'
import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { moveDocument, type UploadedFile, type MetadataFieldDefinition } from '@/lib/api'
import { RootSection } from './explorer/RootSection'
import { Breadcrumbs } from './explorer/Breadcrumbs'
import { CrossScopeMoveDialog } from './explorer/CrossScopeMoveDialog'

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
  const { isAdmin, user } = useAuth()
  const inputRef = useRef<HTMLInputElement>(null)
  const [selectedFolder, setSelectedFolder] = useState<SelectedFolder>({ scope: 'user', path: '/' })

  // Plan 06-10: DnD state for cross-scope informational dialog (D-01)
  const [crossScopePending, setCrossScopePending] = useState<{
    documentName: string
    sourceScope: 'user' | 'global'
    targetScope: 'user' | 'global'
  } | null>(null)

  // 5px activation distance disambiguates click vs drag — without it the existing
  // onPanelClick handler would fire on every mousedown-mouseup on a row.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } })
  )

  const onDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event
    if (!over) return
    const sourceData = active.data.current as
      | { type: 'document'; doc: UploadedFile }
      | undefined
    const targetData = over.data.current as
      | { type: 'folder'; scope: 'user' | 'global'; path: string }
      | undefined
    if (!sourceData || sourceData.type !== 'document') return
    if (!targetData || targetData.type !== 'folder') return
    const { doc } = sourceData

    // Same-scope move — call API (UI-06 happy path)
    if (doc.scope === targetData.scope) {
      if (doc.folder_path === targetData.path) return                  // no-op
      try {
        await moveDocument(doc.id, targetData.path)
        toast.success(`Moved "${doc.file_name}" to ${targetData.path}`)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Move failed')
      }
      return
    }

    // Cross-scope: open the BLOCKING informational dialog (D-01 LOCKED).
    // CRITICAL: do NOT call moveDocument here — Migration 015's trigger forbids
    // scope mutation at the DB level; this dialog is the friendly explanation.
    setCrossScopePending({
      documentName: doc.file_name,
      sourceScope: doc.scope,
      targetScope: targetData.scope,
    })
  }

  // Polling pattern — ALSO poll content_markdown_status (D-03 / UI-08).
  //
  // WR-06 (Phase 6 review): the prior dep array `[files, onStatusUpdate]`
  // tore down + recreated the interval on every files state change (which
  // happens every poll cycle), restarting the 2000ms cadence and producing
  // a thundering-herd pattern with multiple in-flight files. Use a ref so
  // the interval body always reads the latest files but the setup runs
  // once. The `hasPending` check moves INSIDE the interval body so we skip
  // the network call on cycles with nothing pending instead of tearing
  // down the interval entirely.
  const filesRef = useRef(files)
  useEffect(() => {
    filesRef.current = files
  }, [files])
  useEffect(() => {
    const interval = setInterval(async () => {
      const f = filesRef.current
      const pending = f.filter(
        (x) => x.status === 'pending' || x.status === 'processing' || x.content_markdown_status === 'pending'
      )
      if (pending.length === 0) return
      try {
        const { data } = await supabase
          .from('documents')
          .select('id, status, error_message, content_markdown_status')
          .in('id', pending.map((x) => x.id))
        if (data) {
          for (const doc of data) {
            const current = f.find((x) => x.id === doc.id)
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
  }, [onStatusUpdate])

  // Phase 6 fix: bump on every upload so RootSection remounts FolderTree and
  // re-fetches contents. Without this, FolderNode's cached listFolder() result
  // hides the newly-uploaded doc until the user manually collapses + re-expands
  // the target folder.
  const [uploadRefreshKey, setUploadRefreshKey] = useState(0)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      // UI-05: upload defaults into the currently-selected folder; if user is non-admin and selected scope is 'global',
      // fall back to root user (UI-11 defense — non-admin can never write to global)
      const targetScope = selectedFolder.scope
      const targetPath = selectedFolder.path
      const safeScope = targetScope === 'global' && !isAdmin ? 'user' : targetScope
      const safePath = safeScope === targetScope ? targetPath : '/'

      // CR-01 (Phase 6 review): surface the security override to the user. The
      // collapse to ('user', '/') is intentional (Pitfall 11 — non-admins cannot
      // write to Shared), but a silent downgrade meant the file landed in My Files
      // root with no UI signal, leading to misplaced documents.
      if (safeScope !== targetScope) {
        toast.warning(
          'Cannot upload to Shared without admin rights — uploaded to My Files root instead'
        )
      }

      // Auto-open the target folder before remounting the tree, so the user sees
      // the new file land. Writes directly to the useOpenFoldersStorage key
      // (`fileExplorer:open:{userId}`); the next FolderTree mount reads it.
      if (user?.id && safePath !== '/') {
        try {
          const key = `fileExplorer:open:${user.id}`
          const raw = window.localStorage.getItem(key)
          const parsed = raw ? JSON.parse(raw) : { user: [], global: [] }
          const list: string[] = Array.isArray(parsed[safeScope]) ? parsed[safeScope] : []
          if (!list.includes(safePath)) {
            list.push(safePath)
            parsed[safeScope] = list
            window.localStorage.setItem(key, JSON.stringify(parsed))
          }
        } catch { /* localStorage disabled — non-fatal */ }
      }

      onUpload(file, safePath, safeScope)
      setUploadRefreshKey((k) => k + 1)
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
      <DndContext sensors={sensors} onDragEnd={onDragEnd}>
        <div className="flex-1 overflow-y-auto" data-testid="file-explorer-body" onClick={onPanelClick}>
          <RootSection
            scope="global"
            onDeleteDocument={onDelete}
            onRenameDocument={onRename}
            externalRefreshKey={uploadRefreshKey}
          />
          <RootSection
            scope="user"
            onDeleteDocument={onDelete}
            onRenameDocument={onRename}
            externalRefreshKey={uploadRefreshKey}
          />
        </div>
      </DndContext>
      {crossScopePending && (
        <CrossScopeMoveDialog
          open={true}
          onOpenChange={(o) => {
            if (!o) setCrossScopePending(null)
          }}
          documentName={crossScopePending.documentName}
          sourceScope={crossScopePending.sourceScope}
          targetScope={crossScopePending.targetScope}
        />
      )}
    </div>
  )
}
