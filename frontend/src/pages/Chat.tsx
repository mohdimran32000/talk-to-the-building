import { useState, useEffect, useCallback, useRef } from 'react'
import { useAuth } from '@/contexts/AuthContext'
import { toast } from 'sonner'
import ThreadSidebar from '@/components/ThreadSidebar'
import MessageList from '@/components/MessageList'
import MessageInput from '@/components/MessageInput'
import FileUploadPanel from '@/components/FileUploadPanel'
import MetadataFilterBar from '@/components/MetadataFilterBar'
import {
  getThreads,
  createThread,
  deleteThread,
  getMessages,
  sendMessage,
  getUploadedFiles,
  uploadFile,
  deleteFile,
  getSettings,
  type Thread,
  type Message,
  type UploadedFile,
  type MetadataFieldDefinition,
} from '@/lib/api'
import type { ToolStep } from '@/components/ToolActivity'

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
  const [metadataSchema, setMetadataSchema] = useState<MetadataFieldDefinition[] | null>(null)
  const [metadataFilters, setMetadataFilters] = useState<Record<string, any>>({})
  const [subAgentContent, setSubAgentContent] = useState('')
  const [isSubAgentActive, setIsSubAgentActive] = useState(false)
  const [subAgentDocName, setSubAgentDocName] = useState('')
  const [toolSteps, setToolSteps] = useState<ToolStep[]>([])
  const [isToolThinking, setIsToolThinking] = useState(false)
  const abortControllerRef = useRef<AbortController | null>(null)

  const loadFiles = useCallback(async () => {
    try {
      const data = await getUploadedFiles()
      setFiles(data)
    } catch {
      // Silently fail — files panel is supplementary
    }
  }, [])

  const loadSettings = useCallback(async () => {
    try {
      const settings = await getSettings()
      setMetadataSchema(settings.metadata_schema || null)
    } catch {
      // Silently fail
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
    // Reload files when a document becomes ready to get its metadata
    if (status === 'ready') {
      setTimeout(() => {
        getUploadedFiles().then(setFiles).catch(() => {})
      }, 500)
    }
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
    loadSettings()
  }, [loadThreads, loadFiles, loadSettings])

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
    setToolSteps([])
    setIsToolThinking(false)

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
          setSubAgentContent('')
          setIsSubAgentActive(false)
          setSubAgentDocName('')
          setToolSteps([])
          setIsToolThinking(false)
          abortControllerRef.current = null
          // Reload from DB in background to get server-persisted IDs
          setTimeout(() => {
            loadMessages(threadId!)
            loadThreads()
          }, 500)
        },
        controller.signal,
        Object.keys(metadataFilters).length > 0 ? metadataFilters : undefined,
        // Sub-agent callbacks (Phase 5: extended to handle BOTH analyze_document
        // payload — has document_name — AND explore_knowledge_base payload —
        // has question. The state slot subAgentDocName now holds whichever is
        // present; Phase 6 will refactor to a typed discriminator.)
        (data) => {
          setIsSubAgentActive(true)
          setSubAgentDocName(data.document_name || data.question || '')
          setSubAgentContent('')
        },
        (token) => {
          setSubAgentContent((prev) => prev + token)
        },
        () => {
          setIsSubAgentActive(false)
        },
        undefined, // onError
        // Tool activity callbacks
        () => {
          setIsToolThinking(true)
        },
        (data) => {
          setIsToolThinking(false)
          setToolSteps((prev) => [...prev, { tool: data.tool, args: data.args, status: 'running' }])
        },
        (data) => {
          setToolSteps((prev) =>
            prev.map((s) =>
              s.tool === data.tool && s.status === 'running'
                ? { ...s, status: 'done', detail: data.detail }
                : s
            )
          )
        },
        // Phase 5 NEW — onSubAgentToolStart: Explorer's per-turn inner tool dispatch.
        // Append a tool step with isSubAgent flag so Phase 6's UI-10 can render
        // these nested under the active sub-agent banner. For Phase 5 minimum-viable,
        // they appear in the same toolSteps array as main-agent tools (Phase 6
        // separates them visually).
        (data) => {
          setToolSteps((prev) => [...prev, {
            tool: data.tool,
            args: data.args,
            status: 'running' as const,
            isSubAgent: true,
            turn: data.turn,
          }])
        },
        // Phase 5 NEW — onSubAgentToolDone: flip the matching in-flight sub-agent
        // tool step to 'done' with result_preview as detail. Match by (isSubAgent
        // AND tool name AND status === 'running' AND same turn) to disambiguate
        // when multiple sub-agent tool calls are in flight (which the loop's
        // single-turn-at-a-time discipline shouldn't allow, but defense in depth).
        (data) => {
          setToolSteps((prev) =>
            prev.map((s) =>
              s.isSubAgent && s.tool === data.tool && s.status === 'running' && s.turn === data.turn
                ? { ...s, status: 'done' as const, detail: data.result_preview }
                : s
            )
          )
        },
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

  const hasReadyDocs = files.some((f) => f.status === 'ready' && f.metadata)

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
              metadataSchema={metadataSchema}
            />
            {hasReadyDocs && metadataSchema && (
              <MetadataFilterBar
                schema={metadataSchema}
                documents={files}
                filters={metadataFilters}
                onFilterChange={setMetadataFilters}
              />
            )}
            <MessageList
              messages={messages}
              streamingContent={streamingContent}
              isStreaming={isStreaming}
              subAgentContent={subAgentContent}
              isSubAgentActive={isSubAgentActive}
              subAgentDocName={subAgentDocName}
              toolSteps={toolSteps}
              isToolThinking={isToolThinking}
            />
            <MessageInput onSend={handleSendMessage} onStop={handleStopStreaming} disabled={isStreaming} isStreaming={isStreaming} />
          </>
        )}
      </div>
    </div>
  )
}
