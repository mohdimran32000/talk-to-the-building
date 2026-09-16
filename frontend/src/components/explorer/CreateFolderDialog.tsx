import { useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { createFolder } from '@/lib/api'
import { toast } from 'sonner'

interface CreateFolderDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  parentPath: string                                  // canonical parent (e.g. '/' or '/projects')
  scope: 'user' | 'global'
  onCreated?: (newPath: string) => void               // parent can refetch / expand
}

function joinPath(parent: string, name: string): string {
  const safe = name.trim().replace(/^\/+|\/+$/g, '')
  if (!safe) return ''
  return parent === '/' ? `/${safe}` : `${parent}/${safe}`
}

export function CreateFolderDialog({
  open,
  onOpenChange,
  parentPath,
  scope,
  onCreated,
}: CreateFolderDialogProps) {
  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const path = joinPath(parentPath, name)
    if (!path) {
      toast.error('Folder name cannot be empty')
      return
    }
    setSubmitting(true)
    try {
      const res = await createFolder(path, scope)
      toast.success(`Created ${res.path}`)
      onCreated?.(res.path)
      setName('')
      onOpenChange(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to create folder')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New folder</DialogTitle>
          <DialogDescription>
            Creating under <span className="font-mono">{parentPath}</span> in {scope === 'global' ? 'Shared' : 'My Files'}.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="folder-name">Name</Label>
            <Input
              id="folder-name"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-folder"
              pattern="[^/]+"
              title="Folder name cannot contain a forward slash"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || !name.trim()}>
              {submitting ? 'Creating…' : 'Create'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
