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
    <div className="glass flex h-full w-64 flex-col overflow-hidden rounded-2xl">
      <div className="p-3">
        <Button onClick={onNewThread} className="w-full" variant="outline">
          + New Chat
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
        {threads.length === 0 ? (
          <p className="p-3 text-center text-sm text-muted-foreground">No conversations yet</p>
        ) : (
          threads.map((thread) => (
            <div
              key={thread.id}
              className={`group relative flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-sm transition-all duration-150 hover:bg-primary/5 dark:hover:bg-primary/10 ${
                thread.id === activeThreadId
                  ? 'bg-primary/10 font-medium dark:bg-primary/20 before:absolute before:left-0 before:top-1/2 before:h-4 before:w-0.5 before:-translate-y-1/2 before:rounded-full before:bg-primary'
                  : ''
              }`}
              onClick={() => onSelectThread(thread.id)}
            >
              <span className="truncate">{thread.title}</span>
              <button
                className="ml-2 hidden text-muted-foreground transition-colors duration-150 hover:text-destructive group-hover:inline"
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

      <div className="border-t border-border/60 p-3 space-y-2">
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
