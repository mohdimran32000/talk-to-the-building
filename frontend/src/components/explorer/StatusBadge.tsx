interface StatusBadgeProps {
  status?: string                 // ingestion status: 'pending' | 'processing' | 'ready' | 'failed'
  contentMarkdownStatus?: string  // 'ready' | 'pending' | 'failed' | 'requires_user_reupload' | null
}

const INGEST_COLORS: Record<string, string> = {
  ready: 'bg-green-500/15 text-green-700 dark:text-green-300',
  processing: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  pending: 'bg-blue-500/15 text-blue-700 dark:text-blue-300',
  failed: 'bg-red-500/15 text-red-700 dark:text-red-300',
}

const REINDEX_COLORS: Record<string, { label: string; className: string }> = {
  pending: { label: 'Re-index pending', className: 'bg-orange-500/15 text-orange-700 dark:text-orange-300' },
  failed: { label: 'Re-index failed', className: 'bg-red-500/15 text-red-700 dark:text-red-300' },
  requires_user_reupload: { label: 'Re-upload required', className: 'bg-red-500/25 text-red-800 dark:text-red-200' },
  // 'ready' renders no badge — the default healthy state is silent
}

export function StatusBadge({ status, contentMarkdownStatus }: StatusBadgeProps) {
  const ingestPill = status ? (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${INGEST_COLORS[status] || 'bg-foreground/10 text-muted-foreground'}`}
    >
      {status}
    </span>
  ) : null

  const reindex = contentMarkdownStatus && REINDEX_COLORS[contentMarkdownStatus]
  const reindexPill = reindex ? (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${reindex.className}`}
      title={`content_markdown_status: ${contentMarkdownStatus}`}
    >
      {reindex.label}
    </span>
  ) : null

  return (
    <>
      {ingestPill}
      {reindexPill}
    </>
  )
}
