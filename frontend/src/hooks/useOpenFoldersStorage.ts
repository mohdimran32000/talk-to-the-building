import { useEffect, useState, useCallback, useRef } from 'react'

export type Scope = 'user' | 'global'

export interface OpenFoldersByScope {
  user: string[]
  global: string[]
}

const EMPTY: OpenFoldersByScope = { user: [], global: [] }

function storageKey(userId: string): string {
  return `fileExplorer:open:${userId}`
}

function readStorage(userId: string): OpenFoldersByScope {
  try {
    const raw = window.localStorage.getItem(storageKey(userId))
    if (!raw) return { ...EMPTY }
    const parsed = JSON.parse(raw) as Partial<OpenFoldersByScope>
    return {
      user: Array.isArray(parsed.user) ? parsed.user : [],
      global: Array.isArray(parsed.global) ? parsed.global : [],
    }
  } catch {
    return { ...EMPTY }
  }
}

/**
 * useOpenFoldersStorage — UI-03 contract.
 * Persists open-folder paths per user under key `fileExplorer:open:{userId}`.
 * Per-user keying prevents leakage on shared machines (CONTEXT.md §localStorage persistence).
 * Debounced 250ms write to avoid synchronous IO on every chevron click.
 */
export function useOpenFoldersStorage(userId: string | null) {
  const [openFolders, setOpenFolders] = useState<OpenFoldersByScope>(() =>
    userId ? readStorage(userId) : { ...EMPTY }
  )
  const writeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Re-read on user change (sign-out + sign-in into different account)
  useEffect(() => {
    if (!userId) {
      setOpenFolders({ ...EMPTY })
      return
    }
    setOpenFolders(readStorage(userId))
  }, [userId])

  // Debounced persistence (250ms per CONTEXT.md / RESEARCH.md) +
  // synchronous flush on pagehide so the write survives a navigation/reload
  // that fires before the debounce timer (UI-03 persistence contract).
  useEffect(() => {
    if (!userId) return
    const flush = () => {
      try {
        window.localStorage.setItem(storageKey(userId), JSON.stringify(openFolders))
      } catch {
        // Quota exceeded or storage disabled — non-fatal
      }
    }
    if (writeTimer.current) clearTimeout(writeTimer.current)
    writeTimer.current = setTimeout(flush, 250)

    const onPageHide = () => {
      if (writeTimer.current) {
        clearTimeout(writeTimer.current)
        writeTimer.current = null
      }
      flush()
    }
    window.addEventListener('pagehide', onPageHide)
    window.addEventListener('beforeunload', onPageHide)

    return () => {
      window.removeEventListener('pagehide', onPageHide)
      window.removeEventListener('beforeunload', onPageHide)
      // Flush any pending debounced write so a remount within the same JS
      // context (e.g. user-id change) does not drop the latest state.
      if (writeTimer.current) {
        clearTimeout(writeTimer.current)
        writeTimer.current = null
        flush()
      }
    }
  }, [openFolders, userId])

  const isOpen = useCallback(
    (scope: Scope, path: string) => openFolders[scope].includes(path),
    [openFolders]
  )

  const toggle = useCallback((scope: Scope, path: string) => {
    setOpenFolders((prev) => {
      const set = new Set(prev[scope])
      if (set.has(path)) set.delete(path)
      else set.add(path)
      return { ...prev, [scope]: Array.from(set) }
    })
  }, [])

  const open = useCallback((scope: Scope, path: string) => {
    setOpenFolders((prev) => {
      if (prev[scope].includes(path)) return prev
      return { ...prev, [scope]: [...prev[scope], path] }
    })
  }, [])

  const close = useCallback((scope: Scope, path: string) => {
    setOpenFolders((prev) => {
      if (!prev[scope].includes(path)) return prev
      return { ...prev, [scope]: prev[scope].filter((p) => p !== path) }
    })
  }, [])

  return { isOpen, toggle, open, close }
}
