import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import ThemeToggle from '@/components/ThemeToggle'
import { useAuth } from '@/contexts/AuthContext'
import type { Thread } from '@/lib/api'

interface ThreadSidebarProps {
  threads: Thread[]
  activeThreadId: string | null
  onSelectThread: (id: string) => void
  onNewThread: () => void
  onDeleteThread: (id: string) => void
  onSignOut: () => void
}

export default function ThreadSidebar({
  threads,
  activeThreadId,
  onSelectThread,
  onNewThread,
  onDeleteThread,
  onSignOut,
}: ThreadSidebarProps) {
  const { isAdmin } = useAuth()

  return (
    <div className="flex h-full w-64 flex-col border-r bg-muted/30">
      <div className="p-3">
        <Button onClick={onNewThread} className="w-full" variant="outline">
          + New Chat
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {threads.length === 0 ? (
          <p className="p-3 text-center text-sm text-muted-foreground">No conversations yet</p>
        ) : (
          threads.map((thread) => (
            <div
              key={thread.id}
              className={`group flex cursor-pointer items-center justify-between px-3 py-2 text-sm hover:bg-muted ${
                thread.id === activeThreadId ? 'bg-muted font-medium' : ''
              }`}
              onClick={() => onSelectThread(thread.id)}
            >
              <span className="truncate">{thread.title}</span>
              <button
                className="ml-2 hidden text-muted-foreground hover:text-destructive group-hover:inline"
                onClick={(e) => {
                  e.stopPropagation()
                  onDeleteThread(thread.id)
                }}
              >
                x
              </button>
            </div>
          ))
        )}
      </div>

      <div className="border-t p-3 space-y-2">
        {isAdmin && (
          <Link to="/settings">
            <Button variant="outline" className="w-full text-sm" data-testid="settings-link">
              Settings
            </Button>
          </Link>
        )}
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Button onClick={onSignOut} variant="ghost" className="flex-1 text-sm">
            Sign Out
          </Button>
        </div>
      </div>
    </div>
  )
}
