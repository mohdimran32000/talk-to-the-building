import { useState, useEffect, useCallback, useRef } from 'react'
import { useAuth } from '@/contexts/AuthContext'
import { toast } from 'sonner'
import ThreadSidebar from '@/components/ThreadSidebar'
import MessageList from '@/components/MessageList'
import MessageInput from '@/components/MessageInput'
import FileUploadPanel from '@/components/FileUploadPanel'
import {
  getThreads,
  createThread,
  deleteThread,
  getMessages,
  sendMessage,
  getUploadedFiles,
  uploadFile,
  deleteFile,
  type Thread,
  type Message,
  type UploadedFile,
} from '@/lib/api'

export default function Chat() {
  const { signOut } = useAuth()
  const [threads, setThreads] = useState<Thread[]>([])
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [streamingContent, setStreamingContent] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [loadingThreads, setLoadingThreads] = useState(true)
  const [files, setFiles] = useState<UploadedFile[]>([])
  const [isUploading, setIsUploading] = useState(false)
  const abortControllerRef = useRef<AbortController | null>(null)

  const loadFiles = useCallback(async () => {
    try {
      const data = await getUploadedFiles()
      setFiles(data)
    } catch {
      // Silently fail — files panel is supplementary
    }
  }, [])

  const handleUploadFile = async (file: File) => {
    setIsUploading(true)
    try {
      const uploaded = await uploadFile(file)

      if (uploaded.action === 'skipped') {
        toast.info('File already uploaded with identical content — skipped')
      } else if (uploaded.action === 'updated') {
        toast.success('File content changed — re-ingesting updated content')
        setFiles((prev) => prev.map((f) => f.id === uploaded.id ? uploaded : f))
      } else {
        setFiles((prev) => [uploaded, ...prev])
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to upload file')
    } finally {
      setIsUploading(false)
    }
  }

  const handleDeleteFile = async (fileId: string) => {
    try {
      await deleteFile(fileId)
      setFiles((prev) => prev.filter((f) => f.id !== fileId))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete file')
    }
  }

  const handleStatusUpdate = useCallback((documentId: string, status: string, errorMessage?: string) => {
    setFiles((prev) =>
      prev.map((f) => (f.id === documentId ? { ...f, status: status as UploadedFile['status'], error_message: errorMessage || f.error_message } : f))
    )
  }, [])

  const loadThreads = useCallback(async () => {
    try {
      const data = await getThreads()
      setThreads(data)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to load threads')
    } finally {
      setLoadingThreads(false)
    }
  }, [])

  useEffect(() => {
    loadThreads()
    loadFiles()
  }, [loadThreads, loadFiles])

  const loadMessages = useCallback(async (threadId: string) => {
    try {
      const data = await getMessages(threadId)
      setMessages(data)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to load messages')
    }
  }, [])

  useEffect(() => {
    if (activeThreadId) {
      loadMessages(activeThreadId)
    } else {
      setMessages([])
    }
  }, [activeThreadId, loadMessages])

  const handleNewThread = async () => {
    try {
      const thread = await createThread()
      setThreads((prev) => [thread, ...prev])
      setActiveThreadId(thread.id)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to create thread')
    }
  }

  const handleDeleteThread = async (id: string) => {
    try {
      await deleteThread(id)
      setThreads((prev) => prev.filter((t) => t.id !== id))
      if (activeThreadId === id) {
        setActiveThreadId(null)
        setMessages([])
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete thread')
    }
  }

  const handleSendMessage = async (content: string) => {
    let threadId = activeThreadId

    // Auto-create thread if none selected
    if (!threadId) {
      try {
        const title = content.length > 40 ? content.slice(0, 40) + '...' : content
        const thread = await createThread(title)
        setThreads((prev) => [thread, ...prev])
        setActiveThreadId(thread.id)
        threadId = thread.id
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to create thread')
        return
      }
    }

    // Optimistically add user message
    const tempUserMsg: Message = {
      id: `temp-${Date.now()}`,
      thread_id: threadId,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, tempUserMsg])
    setIsStreaming(true)
    setStreamingContent('')

    const controller = new AbortController()
    abortControllerRef.current = controller

    try {
      let fullResponse = ''
      await sendMessage(
        threadId,
        content,
        (token) => {
          fullResponse += token
          setStreamingContent(fullResponse)
        },
        () => {
          // On done: immediately add assistant message from accumulated stream
          // so it displays without waiting for DB round-trip
          const assistantMsg: Message = {
            id: `temp-assistant-${Date.now()}`,
            thread_id: threadId!,
            role: 'assistant',
            content: fullResponse,
            created_at: new Date().toISOString(),
          }
          setMessages((prev) => [...prev, assistantMsg])
          setIsStreaming(false)
          setStreamingContent('')
          abortControllerRef.current = null
          // Reload from DB in background to get server-persisted IDs
          setTimeout(() => {
            loadMessages(threadId!)
            loadThreads()
          }, 500)
        },
        controller.signal,
      )
    } catch (err) {
      setIsStreaming(false)
      setStreamingContent('')
      abortControllerRef.current = null
      if ((err as Error).name === 'AbortError') return
      toast.error(err instanceof Error ? err.message : 'Failed to send message')
    }
  }

  const handleStopStreaming = () => {
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    setIsStreaming(false)
    setStreamingContent('')
    // Reload messages to get whatever was persisted server-side
    if (activeThreadId) loadMessages(activeThreadId)
  }

  const handleSignOut = async () => {
    try {
      await signOut()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to sign out')
    }
  }

  return (
    <div className="flex h-screen">
      <ThreadSidebar
        threads={threads}
        activeThreadId={activeThreadId}
        onSelectThread={setActiveThreadId}
        onNewThread={handleNewThread}
        onDeleteThread={handleDeleteThread}
        onSignOut={handleSignOut}
      />

      <div className="flex flex-1 flex-col">
        {loadingThreads ? (
          <div className="flex flex-1 items-center justify-center text-muted-foreground">
            Loading...
          </div>
        ) : (
          <>
            <FileUploadPanel
              files={files}
              isUploading={isUploading}
              onUpload={handleUploadFile}
              onDelete={handleDeleteFile}
              onStatusUpdate={handleStatusUpdate}
            />
            <MessageList
              messages={messages}
              streamingContent={streamingContent}
              isStreaming={isStreaming}
            />
            <MessageInput onSend={handleSendMessage} onStop={handleStopStreaming} disabled={isStreaming} isStreaming={isStreaming} />
          </>
        )}
      </div>
    </div>
  )
}
