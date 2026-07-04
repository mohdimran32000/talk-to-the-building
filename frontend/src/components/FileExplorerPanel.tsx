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
  // onUpload now returns a Promise<UploadedFile | null> so the panel can
  // track in-flight uploads, render an optimistic "uploading…" banner, and
  // trigger a folder refetch the instant each upload settles (resolved or
  // rejected) — including the skipped/updated paths which don't change
  // files.length.
  onUpload: (file: File, folder_path: string, scope: 'user' | 'global') => Promise<UploadedFile | null>
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

  // Bump RootSection's key on every upload completion (resolved or rejected,
  // including skipped/updated paths that don't change files.length). The
  // remount forces FolderNode to drop its cached listFolder() result and
  // re-fetch with the new document visible.
  const [uploadRefreshKey, setUploadRefreshKey] = useState(0)

  // Optimistic in-flight upload tracking. Each entry corresponds to a file
  // dispatched to onUpload that hasn't settled yet. Rendered as a banner
  // under the panel header so the user sees an immediate response on drop /
  // file-pick rather than 1-3 seconds of dead air while the backend round-
  // trip completes.
  type GhostUpload = {
    tempId: string
    name: string
    folder_path: string
    scope: 'user' | 'global'
  }
  const [ghostUploads, setGhostUploads] = useState<GhostUpload[]>([])

  // Shared upload sink — used by both the file-picker (multi-select via `multiple`)
  // and native OS drag-and-drop on folder rows. Applies the UI-11 non-admin
  // downgrade to ('user', '/') once for the batch, opens the target in
  // localStorage, and dispatches each file to onUpload sequentially. The backend
  // semaphore (Semaphore(2) in files.py) handles the queueing.
  const uploadFilesTo = (
    files: FileList | File[],
    targetPath: string,
    targetScope: 'user' | 'global',
  ) => {
    const list = Array.from(files)
    if (list.length === 0) return

    const safeScope = targetScope === 'global' && !isAdmin ? 'user' : targetScope
    const safePath = safeScope === targetScope ? targetPath : '/'

    if (safeScope !== targetScope) {
      toast.warning(
        'Cannot upload to Shared without admin rights — uploaded to My Files root instead'
      )
    }

    if (user?.id && safePath !== '/') {
      try {
        const key = `fileExplorer:open:${user.id}`
        const raw = window.localStorage.getItem(key)
        const parsed = raw ? JSON.parse(raw) : { user: [], global: [] }
        const opened: string[] = Array.isArray(parsed[safeScope]) ? parsed[safeScope] : []
        if (!opened.includes(safePath)) {
          opened.push(safePath)
          parsed[safeScope] = opened
          window.localStorage.setItem(key, JSON.stringify(parsed))
        }
      } catch { /* localStorage disabled — non-fatal */ }
    }

    const newGhosts: GhostUpload[] = list.map((f) => ({
      tempId: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}-${f.name}`,
      name: f.name,
      folder_path: safePath,
      scope: safeScope,
    }))
    setGhostUploads((prev) => [...prev, ...newGhosts])

    list.forEach((f, i) => {
      const ghostId = newGhosts[i].tempId
      onUpload(f, safePath, safeScope).finally(() => {
        setGhostUploads((prev) => prev.filter((g) => g.tempId !== ghostId))
        // Bump on every settle (resolved/rejected/skipped/updated). The
        // length-watching effect previously missed skipped+updated paths
        // because they don't grow files.length.
        setUploadRefreshKey((k) => k + 1)
      })
    })
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files
    if (fileList && fileList.length > 0) {
      uploadFilesTo(fileList, selectedFolder.path, selectedFolder.scope)
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
    <div className="glass flex flex-col shrink-0 max-h-[40vh] overflow-hidden rounded-2xl">
      <div className="border-b border-border/60 px-3 py-2 flex items-center justify-between">
        <Breadcrumbs
          path={selectedFolder.path}
          scopeLabel={selectedFolder.scope === 'global' ? 'Shared' : 'My Files'}
          onNavigate={(path) => setSelectedFolder({ scope: selectedFolder.scope, path })}
        />
        <div className="flex items-center gap-1">
          <input ref={inputRef} type="file" multiple hidden onChange={handleFileChange} />
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
      {ghostUploads.length > 0 && (
        <div
          className="border-b border-border/60 bg-primary/5 dark:bg-primary/10 px-3 py-1.5 text-xs flex flex-col gap-1"
          role="status"
          aria-live="polite"
        >
          <div className="flex items-center gap-2 text-primary dark:text-primary font-medium">
            <svg
              className="animate-spin h-3.5 w-3.5"
              viewBox="0 0 24 24"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
              <path
                d="M4 12a8 8 0 018-8"
                stroke="currentColor"
                strokeWidth="4"
                strokeLinecap="round"
              />
            </svg>
            Uploading {ghostUploads.length} file{ghostUploads.length === 1 ? '' : 's'}…
          </div>
          {ghostUploads.map((g) => (
            <div key={g.tempId} className="pl-5 text-muted-foreground truncate" title={`${g.scope === 'global' ? 'Shared' : 'My Files'} ${g.folder_path}`}>
              {g.name}
              <span className="ml-2 text-[10px] opacity-70">
                → {g.scope === 'global' ? 'Shared' : 'My Files'} {g.folder_path}
              </span>
            </div>
          ))}
        </div>
      )}
      <DndContext sensors={sensors} onDragEnd={onDragEnd}>
        <div className="flex-1 overflow-y-auto" data-testid="file-explorer-body" onClick={onPanelClick}>
          <RootSection
            scope="global"
            onDeleteDocument={onDelete}
            onRenameDocument={onRename}
            externalRefreshKey={uploadRefreshKey}
            onDropFiles={uploadFilesTo}
          />
          <RootSection
            scope="user"
            onDeleteDocument={onDelete}
            onRenameDocument={onRename}
            externalRefreshKey={uploadRefreshKey}
            onDropFiles={uploadFilesTo}
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
