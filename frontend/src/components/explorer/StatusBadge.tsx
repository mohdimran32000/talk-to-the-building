interface StatusBadgeProps {
  status?: string                 // ingestion status: 'pending' | 'processing' | 'ready' | 'failed'
  contentMarkdownStatus?: string  // 'ready' | 'pending' | 'failed' | 'requires_user_reupload' | null
}

const INGEST_COLORS: Record<string, string> = {
  ready: 'bg-green-100 text-green-800',
  processing: 'bg-yellow-100 text-yellow-800',
  pending: 'bg-blue-100 text-blue-800',
  failed: 'bg-red-100 text-red-800',
}

const REINDEX_COLORS: Record<string, { label: string; className: string }> = {
  pending: { label: 'Re-index pending', className: 'bg-orange-100 text-orange-800' },
  failed: { label: 'Re-index failed', className: 'bg-red-100 text-red-800' },
  requires_user_reupload: { label: 'Re-upload required', className: 'bg-red-200 text-red-900' },
  // 'ready' renders no badge — the default healthy state is silent
}

export function StatusBadge({ status, contentMarkdownStatus }: StatusBadgeProps) {
  const ingestPill = status ? (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${INGEST_COLORS[status] || 'bg-gray-100 text-gray-800'}`}
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
