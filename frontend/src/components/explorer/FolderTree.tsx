import { useCallback, useEffect, useRef, useState, createContext, useContext } from 'react'
import { useAuth } from '@/contexts/AuthContext'
import { useOpenFoldersStorage, type Scope } from '@/hooks/useOpenFoldersStorage'
import { FolderNode } from './FolderNode'

// Internal context so FolderNode's recursive child boundary can ask
// "is folder X in scope Y currently expanded?" without prop-drilling
interface ExpansionAPI {
  isOpen: (scope: Scope, path: string) => boolean
  toggle: (scope: Scope, path: string) => void
  open: (scope: Scope, path: string) => void
  close: (scope: Scope, path: string) => void
}
const ExpansionContext = createContext<ExpansionAPI | null>(null)

export function useExpandedState(scope: Scope, path: string): boolean {
  const ctx = useContext(ExpansionContext)
  return ctx ? ctx.isOpen(scope, path) : false
}

interface FolderTreeProps {
  scope: Scope
  rootPath: string                          // typically '/' for the root section
  onDeleteDocument?: (id: string) => void
  onRenameDocument?: (id: string, newName: string) => void
  // WR-08 (Phase 6 review): converge section-header create + inline FolderNode
  // CRUD onto a single refresh mechanism. RootSection bumps this counter from
  // its section-header onCreated; FolderNode children call onAfterMutation
  // internally. Both paths now end up in the same refetchCounter increment.
  externalMutationSignal?: number
}

export function FolderTree({ scope, rootPath, onDeleteDocument, onRenameDocument, externalMutationSignal = 0 }: FolderTreeProps) {
  const { user } = useAuth()
  const userId = user?.id ?? null
  const expansion = useOpenFoldersStorage(userId)
  const containerRef = useRef<HTMLDivElement>(null)

  // Plan 06-09 Task 3d: refetch counter. After any folder CRUD mutation,
  // the root FolderNode is force-remounted via `key={refetchCounter}` so its
  // internal `contents` state is dropped and lazy-load triggers a fresh
  // listFolder() call. Drilled into recursive children as onAfterMutation.
  const [refetchCounter, setRefetchCounter] = useState(0)
  const onAfterMutation = useCallback(() => setRefetchCounter((c) => c + 1), [])

  // WR-08: bump refetchCounter when the parent signals an external mutation
  // (section-header CreateFolderDialog onCreated). This unifies the inline
  // and section-header refresh paths onto a single mechanism. Skip the
  // initial 0->0 effect run by checking against a ref of the last seen value.
  const lastSignalRef = useRef(externalMutationSignal)
  useEffect(() => {
    if (lastSignalRef.current !== externalMutationSignal) {
      lastSignalRef.current = externalMutationSignal
      setRefetchCounter((c) => c + 1)
    }
  }, [externalMutationSignal])

  // CONTEXT.md D-04 (LOCKED): keyboard nav implements EXACTLY these keys:
  //   Right     -> expand or move into first child
  //   Left      -> collapse or move to parent
  //   Up/Down   -> prev/next visible node
  //   Enter/Space -> activate / toggle
  // Home/End/typeahead are deferred to v2 per D-04 (no implementation in this plan).
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const active = document.activeElement as HTMLElement | null
      if (!active || !containerRef.current?.contains(active)) return
      const folderPath = active.closest('[data-folder-path]')?.getAttribute('data-folder-path') ?? null
      const isFolder = folderPath !== null
      switch (e.key) {
        case 'ArrowRight':
          if (isFolder && folderPath) {
            if (!expansion.isOpen(scope, folderPath)) {
              expansion.open(scope, folderPath)
              e.preventDefault()
            } else {
              // Already open — focus first visible child. Convention: first sibling button after this row.
              focusNext(active, containerRef.current)
              e.preventDefault()
            }
          }
          break
        case 'ArrowLeft':
          if (isFolder && folderPath && expansion.isOpen(scope, folderPath)) {
            expansion.close(scope, folderPath)
            e.preventDefault()
          } else if (active) {
            // Focus parent row if collapsed — find previous treeitem
            focusPrev(active, containerRef.current)
            e.preventDefault()
          }
          break
        case 'ArrowDown':
          focusNext(active, containerRef.current)
          e.preventDefault()
          break
        case 'ArrowUp':
          focusPrev(active, containerRef.current)
          e.preventDefault()
          break
        case 'Enter':
        case ' ':
          if (isFolder && folderPath) {
            expansion.toggle(scope, folderPath)
            e.preventDefault()
          }
          break
        // NOTE: D-04 explicitly excludes Home/End/typeahead — do NOT add cases for them.
      }
    },
    [expansion, scope]
  )

  return (
    <ExpansionContext.Provider value={expansion}>
      <div ref={containerRef} onKeyDown={onKeyDown}>
        <FolderNode
          key={refetchCounter}
          scope={scope}
          folderId={null}                        /* root '/' has no folders-table row (D-06); rename/delete disabled */
          path={rootPath}
          depth={0}
          isOpen={expansion.isOpen(scope, rootPath) || rootPath === '/'}
          onToggle={expansion.toggle}
          onAfterMutation={onAfterMutation}
          onDeleteDocument={onDeleteDocument}
          onRenameDocument={onRenameDocument}
        />
      </div>
    </ExpansionContext.Provider>
  )
}

function focusableTreeItems(container: HTMLElement | null): HTMLElement[] {
  if (!container) return []
  return Array.from(container.querySelectorAll<HTMLElement>('button[tabindex="0"]'))
}

function focusNext(current: HTMLElement, container: HTMLElement | null) {
  const items = focusableTreeItems(container)
  const i = items.indexOf(current)
  if (i >= 0 && i < items.length - 1) items[i + 1].focus()
}

function focusPrev(current: HTMLElement, container: HTMLElement | null) {
  const items = focusableTreeItems(container)
  const i = items.indexOf(current)
  if (i > 0) items[i - 1].focus()
}
