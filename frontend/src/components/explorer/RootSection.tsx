import { useState } from 'react'
import { Globe, User as UserIcon, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/contexts/AuthContext'
import { FolderTree } from './FolderTree'
import { CreateFolderDialog } from './CreateFolderDialog'

interface RootSectionProps {
  scope: 'user' | 'global'
  // CRUD callbacks wired in Plan 06-09; this component composes the tree with section chrome
  onDeleteDocument?: (id: string) => void
  onRenameDocument?: (id: string, newName: string) => void
  // External refresh signal (Phase 6 fix): when the panel uploads a document
  // it bumps this counter so the FolderTree remounts and re-fetches contents.
  // FolderNode caches listFolder() results per-mount, so without this signal
  // the new document does not appear in its target folder until the user
  // collapses + re-expands the folder.
  externalRefreshKey?: number
  onDropFiles?: (files: FileList | File[], path: string, scope: 'user' | 'global') => void
}

export function RootSection({ scope, onDeleteDocument, onRenameDocument, externalRefreshKey = 0, onDropFiles }: RootSectionProps) {
  const { isAdmin } = useAuth()
  // UI-11 / Pitfall 11: the global-scope inline-create button is gated structurally —
  // canCreate = scope === 'user' || isAdmin. Non-admins on Shared see no Create affordance.
  const canCreate = scope === 'user' || isAdmin
  const [createOpen, setCreateOpen] = useState(false)
  // WR-08 (Phase 6 review): unify the section-header create flow with the
  // inline FolderNode CRUD onto a SINGLE refresh mechanism. We bump
  // headerMutationSignal on onCreated; FolderTree re-uses its existing
  // refetchCounter (driven by inline mutations via onAfterMutation) to
  // remount FolderNode children. Previously a `key=` remount path coexisted
  // with FolderTree's internal refetchCounter — two parallel mechanisms that
  // were easy to drift out of sync.
  const [headerMutationSignal, setHeaderMutationSignal] = useState(0)

  const isShared = scope === 'global'
  const label = isShared ? 'Shared (global)' : 'My Files'
  const Icon = isShared ? Globe : UserIcon
  const tintClass = isShared
    ? 'bg-primary/4 dark:bg-primary/8'                // Pitfall 11 visual differentiator (PATTERNS.md line 128)
    : 'bg-transparent'

  return (
    <section className={`border-b border-border/60 last:border-b-0 ${tintClass}`} aria-label={label} data-root-scope={scope}>
      <header className="flex items-center gap-2 px-3 py-2 text-sm font-medium text-muted-foreground">
        <Icon className="w-4 h-4 shrink-0" />
        <span>{label}</span>
        {/* D-05 + UI-11 LOCKED: inline-create button at section header level.
            For Shared (global), structurally gated behind isAdmin via canCreate. */}
        {canCreate && (
          <>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="ml-auto h-6 px-2 text-xs"
              onClick={() => setCreateOpen(true)}
              title={`New folder in ${label}`}
            >
              <Plus className="w-3 h-3 mr-1" /> New folder
            </Button>
            <CreateFolderDialog
              open={createOpen}
              onOpenChange={setCreateOpen}
              parentPath="/"
              scope={scope}
              onCreated={() => setHeaderMutationSignal((k) => k + 1)}
            />
          </>
        )}
      </header>
      <div role="tree" aria-label={`${label} tree`}>
        <FolderTree
          // WR-08: `key` only handles outer parent-driven remounts (uploads
          // from FileExplorerPanel). Folder CRUD (both inline and section-
          // header) flows through externalMutationSignal -> FolderTree's
          // internal refetchCounter — a single source of truth for refresh.
          key={externalRefreshKey}
          scope={scope}
          rootPath="/"
          externalMutationSignal={headerMutationSignal}
          onDeleteDocument={onDeleteDocument}
          onRenameDocument={onRenameDocument}
          onDropFiles={onDropFiles}
        />
      </div>
    </section>
  )
}
