import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

export interface CrossScopeMoveDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  documentName: string                           // e.g. 'budget.pdf'
  sourceScope: 'user' | 'global'
  targetScope: 'user' | 'global'
}

/**
 * D-01 (locked): cross-scope drag-move triggers a BLOCKING informational dialog.
 * This component NEVER calls a backend mutation endpoint. The supported workflow is
 * "delete + admin re-upload from the target scope". Migration 015's
 * `forbid_scope_mutation` trigger is the DB-level source of truth for scope immutability;
 * this UI is the friendly explanation.
 */
export function CrossScopeMoveDialog({
  open,
  onOpenChange,
  documentName,
  sourceScope,
  targetScope,
}: CrossScopeMoveDialogProps) {
  const targetLabel = targetScope === 'global' ? 'Shared' : 'My Files'
  const sourceLabel = sourceScope === 'global' ? 'Shared' : 'My Files'

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Cannot move across scopes</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div>
              <p>
                Scope is permanent for security. To move <span className="font-mono">{documentName}</span> from {sourceLabel} to {targetLabel},
                an admin must re-upload it from the {targetLabel} section.
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                (Scope changes would let private content cross the access boundary; the database trigger blocks it at the row level.)
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Got it</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
