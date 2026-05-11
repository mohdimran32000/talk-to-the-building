import { useState } from 'react'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { deleteFolder } from '@/lib/api'
import { toast } from 'sonner'

interface DeleteFolderDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  folderId: string                                    // D-06: UUID from FolderNode props (Plan 06-06/06-12). NEVER null.
  folderPath: string
  onDeleted?: (folderId: string) => void
}

/**
 * Pitfall 5 (LOCKED): when the backend returns 409 with structured counts,
 * this dialog renders document_count and subfolder_count LITERALLY from the
 * server response — never a guessed or assumed number. Branches on the typed
 * DeleteFolderResult discriminated union from api.ts; does NOT throw on 409.
 */
export function DeleteFolderDialog({
  open,
  onOpenChange,
  folderId,
  folderPath,
  onDeleted,
}: DeleteFolderDialogProps) {
  const [submitting, setSubmitting] = useState(false)
  const [blocking, setBlocking] = useState<{ document_count: number; subfolder_count: number } | null>(null)

  const handleConfirm = async () => {
    setSubmitting(true)
    setBlocking(null)
    try {
      const result = await deleteFolder(folderId)
      if (result.ok) {
        toast.success(`Deleted ${folderPath}`)
        onDeleted?.(folderId)
        onOpenChange(false)
      } else if (result.error === 'FOLDER_NOT_EMPTY') {
        // Render the server's exact counts — no guessing.
        setBlocking({
          document_count: result.document_count,
          subfolder_count: result.subfolder_count,
        })
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete folder')
    } finally {
      setSubmitting(false)
    }
  }

  // Reset blocking state when dialog closes so re-open shows fresh confirm view
  const handleOpenChange = (next: boolean) => {
    if (!next) setBlocking(null)
    onOpenChange(next)
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete folder?</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="font-mono">{folderPath}</span> will be permanently removed.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {blocking && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
            <p className="font-medium text-destructive">Cannot delete: folder is not empty.</p>
            <p className="mt-1 text-muted-foreground">
              This folder contains{' '}
              <span className="font-semibold text-foreground">
                {blocking.document_count} document{blocking.document_count === 1 ? '' : 's'}
              </span>{' '}
              and{' '}
              <span className="font-semibold text-foreground">
                {blocking.subfolder_count} subfolder{blocking.subfolder_count === 1 ? '' : 's'}
              </span>
              . Move or delete them first.
            </p>
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>Cancel</AlertDialogCancel>
          {!blocking && (
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault()
                handleConfirm()
              }}
              disabled={submitting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {submitting ? 'Deleting…' : 'Delete'}
            </AlertDialogAction>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
