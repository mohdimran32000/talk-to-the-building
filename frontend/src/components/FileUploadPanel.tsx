import { useRef, useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { supabase } from '@/lib/supabase'
import type { UploadedFile, MetadataFieldDefinition } from '@/lib/api'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function statusBadge(status: string) {
  const colors: Record<string, string> = {
    ready: 'bg-green-100 text-green-800',
    processing: 'bg-yellow-100 text-yellow-800',
    pending: 'bg-blue-100 text-blue-800',
    failed: 'bg-red-100 text-red-800',
  }
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${colors[status] || 'bg-gray-100 text-gray-800'}`}
    >
      {status}
    </span>
  )
}

function metadataBadge(label: string, value: string) {
  return (
    <span
      className="inline-block rounded-full bg-purple-100 text-purple-800 px-2 py-0.5 text-xs font-medium"
      title={`${label}: ${value}`}
    >
      {value}
    </span>
  )
}

interface FileUploadPanelProps {
  files: UploadedFile[]
  isUploading: boolean
  onUpload: (file: File) => void
  onDelete: (fileId: string) => void
  onStatusUpdate: (documentId: string, status: string, errorMessage?: string) => void
  metadataSchema?: MetadataFieldDefinition[] | null
}

export default function FileUploadPanel({
  files,
  isUploading,
  onUpload,
  onDelete,
  onStatusUpdate,
  metadataSchema,
}: FileUploadPanelProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [expandedFileId, setExpandedFileId] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Poll for status updates on pending/processing documents
  useEffect(() => {
    const hasPending = files.some((f) => f.status === 'pending' || f.status === 'processing')
    if (!hasPending) return

    const interval = setInterval(async () => {
      try {
        const { data } = await supabase
          .from('documents')
          .select('id, status, error_message')
          .in('id', files.filter((f) => f.status === 'pending' || f.status === 'processing').map((f) => f.id))
        if (data) {
          for (const doc of data) {
            const current = files.find((f) => f.id === doc.id)
            if (current && current.status !== doc.status) {
              onStatusUpdate(doc.id, doc.status, doc.error_message)
            }
          }
        }
      } catch {
        // Silently ignore polling errors
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [files, onStatusUpdate])

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      onUpload(file)
      e.target.value = ''
    }
  }

  const formatMetadataValue = (value: any): string => {
    if (value === null || value === undefined) return '-'
    if (Array.isArray(value)) return value.join(', ')
    if (typeof value === 'boolean') return value ? 'Yes' : 'No'
    return String(value)
  }

  return (
    <div className="border-b">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center justify-between px-4 py-2 text-sm font-medium text-muted-foreground hover:bg-muted/50 transition-colors"
      >
        <span>Documents ({files.length})</span>
        <span className="text-xs">{isOpen ? '▲' : '▼'}</span>
      </button>

      {isOpen && (
        <div className="px-4 pb-3 space-y-2">
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={isUploading}
              onClick={() => inputRef.current?.click()}
            >
              {isUploading ? 'Uploading...' : 'Upload File'}
            </Button>
            <input
              ref={inputRef}
              type="file"
              className="hidden"
              accept=".pdf,.txt,.md,.csv,.json,.html,.xml,.doc,.docx"
              onChange={handleFileChange}
            />
          </div>

          {files.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No documents uploaded. Upload a file to enable document Q&A.
            </p>
          )}

          <div className="space-y-1">
            {files.map((f) => (
              <div key={f.id}>
                <div
                  className="flex items-center justify-between rounded-md bg-muted/30 px-3 py-1.5 text-sm cursor-pointer"
                  onClick={() => setExpandedFileId(expandedFileId === f.id ? null : f.id)}
                >
                  <div className="flex items-center gap-2 min-w-0 flex-wrap">
                    <span className="truncate" title={f.file_name}>
                      {f.file_name}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {formatSize(f.file_size)}
                    </span>
                    {statusBadge(f.status)}
                    {f.status === 'failed' && f.error_message && (
                      <span className="text-xs text-red-600 truncate max-w-[200px]" title={f.error_message}>
                        {f.error_message}
                      </span>
                    )}
                    {f.status === 'ready' && f.metadata && (
                      <>
                        {f.metadata.document_type && metadataBadge('Type', f.metadata.document_type)}
                        {f.metadata.topic && metadataBadge('Topic', f.metadata.topic)}
                      </>
                    )}
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); onDelete(f.id) }}
                    className="ml-2 shrink-0 text-muted-foreground hover:text-destructive text-xs"
                    title="Delete file"
                  >
                    ✕
                  </button>
                </div>

                {expandedFileId === f.id && f.status === 'ready' && f.metadata && (
                  <div className="ml-3 mt-1 mb-2 p-2 rounded bg-muted/20 text-xs space-y-1">
                    {(metadataSchema || []).map((field) => {
                      const val = f.metadata?.[field.name]
                      if (val === null || val === undefined) return null
                      return (
                        <div key={field.name} className="flex gap-2">
                          <span className="font-medium text-muted-foreground min-w-[100px]">{field.name}:</span>
                          <span className="text-foreground">{formatMetadataValue(val)}</span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
