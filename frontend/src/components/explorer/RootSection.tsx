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
}

export function RootSection({ scope, onDeleteDocument, onRenameDocument }: RootSectionProps) {
  const { isAdmin } = useAuth()
  // UI-11 / Pitfall 11: the global-scope inline-create button is gated structurally —
  // canCreate = scope === 'user' || isAdmin. Non-admins on Shared see no Create affordance.
  const canCreate = scope === 'user' || isAdmin
  const [createOpen, setCreateOpen] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  const isShared = scope === 'global'
  const label = isShared ? 'Shared (global)' : 'My Files'
  const Icon = isShared ? Globe : UserIcon
  const tintClass = isShared
    ? 'bg-blue-50/50 dark:bg-blue-950/20'             // Pitfall 11 visual differentiator (PATTERNS.md line 128)
    : 'bg-zinc-50/50 dark:bg-zinc-900/30'

  return (
    <section className={`border-b ${tintClass}`} aria-label={label} data-root-scope={scope}>
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
              onCreated={() => setRefreshKey((k) => k + 1)}
            />
          </>
        )}
      </header>
      <div role="tree" aria-label={`${label} tree`}>
        <FolderTree
          key={refreshKey}
          scope={scope}
          rootPath="/"
          onDeleteDocument={onDeleteDocument}
          onRenameDocument={onRenameDocument}
        />
      </div>
    </section>
  )
}
