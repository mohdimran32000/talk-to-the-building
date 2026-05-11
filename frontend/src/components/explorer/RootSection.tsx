import { Globe, User as UserIcon } from 'lucide-react'
import { FolderTree } from './FolderTree'

interface RootSectionProps {
  scope: 'user' | 'global'
  // CRUD callbacks land in Plan 06-09; this component just composes the tree with section chrome
  onDeleteDocument?: (id: string) => void
  onRenameDocument?: (id: string, newName: string) => void
}

export function RootSection({ scope, onDeleteDocument, onRenameDocument }: RootSectionProps) {
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
      </header>
      <div role="tree" aria-label={`${label} tree`}>
        <FolderTree
          scope={scope}
          rootPath="/"
          onDeleteDocument={onDeleteDocument}
          onRenameDocument={onRenameDocument}
        />
      </div>
    </section>
  )
}
