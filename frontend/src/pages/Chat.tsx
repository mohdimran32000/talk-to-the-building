import { useState, useEffect, useCallback, useRef } from 'react'
import { useAuth } from '@/contexts/AuthContext'
import { toast } from 'sonner'
import ThreadSidebar from '@/components/ThreadSidebar'
import MessageList from '@/components/MessageList'
import MessageInput from '@/components/MessageInput'
import FileExplorerPanel from '@/components/FileExplorerPanel'
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
  type ToolUsedEntry,
  type ToolCallEntry,
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
  // Phase 6 / Plan 06-07 — single typed state slot replaces the Phase 5
  // minimum-viable shape (flat sub-agent fields + boolean discriminator on
  // toolSteps). Structural separation via own state slot replaces the boolean.
  const [liveSubAgentTrace, setLiveSubAgentTrace] = useState<ToolUsedEntry | null>(null)
  const [toolSteps, setToolSteps] = useState<ToolStep[]>([])
  const [isToolThinking, setIsToolThinking] = useState(false)
  const abortControllerRef = useRef<AbortController | null>(null)
  // WR-09 (Phase 6 review): track the deferred file-reload timeout so it can
  // be cancelled on unmount. Without this, rapid status updates spawned many
  // outstanding setTimeouts; users navigating away while uploads were
  // processing leaked network requests after the component unmounted.
  const reloadTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

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

  const handleUploadFile = async (file: File, folder_path: string = '/', scope: 'user' | 'global' = 'user') => {
    setIsUploading(true)
    try {
      const uploaded = await uploadFile(file, folder_path, scope)

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

  // Phase 6 / Plan 06-08 LOCKED signature (checker WARNING #3): fourth optional contentMarkdownStatus
  // propagates content_markdown_status from FileExplorerPanel polling into the row.
  const handleStatusUpdate = useCallback((
    documentId: string,
    status: string,
    errorMessage?: string,
    contentMarkdownStatus?: string,
  ) => {
    setFiles((prev) =>
      prev.map((f) => {
        if (f.id !== documentId) return f
        return {
          ...f,
          status: status as UploadedFile['status'],
          error_message: errorMessage ?? f.error_message,
          // Only overwrite content_markdown_status if the polling callback supplied a non-null value
          content_markdown_status:
            contentMarkdownStatus !== undefined
              ? (contentMarkdownStatus as UploadedFile['content_markdown_status'])
              : f.content_markdown_status,
        }
      })
    )
    // Reload files when a document becomes ready to get its metadata.
    // WR-09: cancel any pending reload before scheduling a new one so rapid
    // status flips collapse to a single trailing reload, and store the handle
    // so the unmount effect below can clear it.
    if (status === 'ready') {
      if (reloadTimeoutRef.current) clearTimeout(reloadTimeoutRef.current)
      reloadTimeoutRef.current = setTimeout(() => {
        reloadTimeoutRef.current = null
        getUploadedFiles().then(setFiles).catch(() => {})
      }, 500)
    }
  }, [])

  // WR-09: unmount cleanup — cancel the deferred reload if the component
  // unmounts before the 500ms timer fires.
  useEffect(() => {
    return () => {
      if (reloadTimeoutRef.current) {
        clearTimeout(reloadTimeoutRef.current)
        reloadTimeoutRef.current = null
      }
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
          setLiveSubAgentTrace(null)
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
        // Phase 6 / Plan 06-07 — Sub-agent SSE callbacks now mutate the typed
        // liveSubAgentTrace slot (ToolUsedEntry) instead of separate flat fields.
        // Structural separation (own state slot) replaces the Phase 5 boolean
        // discriminator on toolSteps per RESEARCH.md migration.
        (data) => {
          setLiveSubAgentTrace({
            tool: data.agent_name ?? (data.document_name ? 'analyze_document' : 'explore_knowledge_base'),
            sub_agent_id: data.sub_agent_id,
            tool_calls: [],
            document_name: data.document_name,
            question: data.question,
            sub_agent_result: '',
          })
        },
        (token) => {
          setLiveSubAgentTrace((prev) =>
            prev ? { ...prev, sub_agent_result: (prev.sub_agent_result ?? '') + token } : prev
          )
        },
        () => {
          // CR-03 (Phase 6 review): do NOT clear liveSubAgentTrace here.
          // Trailing sub_agent_tool_done events can arrive interleaved with
          // sub_agent_done (the agent-level "done" can race with the inner-most
          // tool's finalize). Clearing here would make those tool_done updates
          // no-op against prev=null and the last in-flight tool would stay
          // 'running' until the assistant message rehydrates from DB ~500ms
          // later, producing a visible flicker.
          //
          // The outer onDone callback (above, where the assistant message is
          // materialized) is the SOLE owner of the clear. Keeping the trace
          // alive across the sub-agent done window avoids the race.
          // handleStopStreaming and the catch block also clear as safety nets.
        },
        undefined, // onError
        // Tool activity callbacks — main-agent tools (toolSteps[]) stay unchanged
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
        // Phase 6 / Plan 06-07 — onSubAgentToolStart: Explorer's per-turn inner
        // tool dispatch flows into liveSubAgentTrace.tool_calls (nested), NOT
        // into the flat toolSteps[] array. SubAgentSection renders them as
        // ToolCallRow under the parent agent banner during streaming.
        (data) => {
          setLiveSubAgentTrace((prev) => {
            if (!prev) return prev
            const newCall: ToolCallEntry = {
              tool: data.tool as ToolCallEntry['tool'],
              args: data.args,
              turn: data.turn,
              status: 'running',
            }
            return { ...prev, tool_calls: [...(prev.tool_calls ?? []), newCall] }
          })
        },
        // Phase 6 / Plan 06-07 — onSubAgentToolDone: flip the matching in-flight
        // nested tool call to 'done' with result_preview. Match by (tool name AND
        // status 'running' AND same turn) for disambiguation defense-in-depth.
        (data) => {
          setLiveSubAgentTrace((prev) => {
            if (!prev) return prev
            const updated = (prev.tool_calls ?? []).map((c) =>
              c.tool === data.tool && c.status === 'running' && c.turn === data.turn
                ? { ...c, result_preview: data.result_preview, status: 'done' as const }
                : c
            )
            return { ...prev, tool_calls: updated }
          })
        },
      )
    } catch (err) {
      setIsStreaming(false)
      setStreamingContent('')
      setLiveSubAgentTrace(null)
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
    setLiveSubAgentTrace(null)
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
            <FileExplorerPanel
              files={files}
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
              liveSubAgentTrace={liveSubAgentTrace}
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
