import { ChevronRight, Home } from 'lucide-react'

interface BreadcrumbsProps {
  path: string                                                        // canonical e.g. '/' or '/projects/2025'
  scopeLabel?: string                                                 // 'Shared' | 'My Files' (rendered as the leading segment)
  onNavigate?: (path: string) => void                                 // click handler — receives canonical path
}

export function Breadcrumbs({ path, scopeLabel, onNavigate }: BreadcrumbsProps) {
  // Build segments: '/' -> [], '/a/b/c' -> ['a','b','c']
  const segments = path === '/' ? [] : path.split('/').filter(Boolean)
  // Cumulative paths for click handlers: ['/a','/a/b','/a/b/c']
  const cumulative = segments.map((_, i) => '/' + segments.slice(0, i + 1).join('/'))

  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-xs text-muted-foreground">
      <button
        type="button"
        onClick={() => onNavigate?.('/')}
        className="flex items-center gap-1 hover:text-foreground transition-colors"
        title={scopeLabel ? `${scopeLabel} root` : 'Root'}
      >
        <Home className="w-3 h-3" />
        {scopeLabel && <span>{scopeLabel}</span>}
      </button>
      {segments.map((seg, i) => (
        <span key={cumulative[i]} className="flex items-center gap-1">
          <ChevronRight className="w-3 h-3 shrink-0" />
          <button
            type="button"
            onClick={() => onNavigate?.(cumulative[i])}
            className="hover:text-foreground transition-colors truncate max-w-[140px]"
            title={cumulative[i]}
          >
            {seg}
          </button>
        </span>
      ))}
    </nav>
  )
}
