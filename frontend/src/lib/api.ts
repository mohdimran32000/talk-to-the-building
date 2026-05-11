import { supabase } from './supabase'

async function getToken(): Promise<string> {
  const { data: { session } } = await supabase.auth.getSession()
  if (!session) throw new Error('Not authenticated')
  return session.access_token
}

async function fetchApi(path: string, options: RequestInit = {}) {
  const token = await getToken()
  const res = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res.json()
}

export interface Thread {
  id: string
  user_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface Message {
  id: string
  thread_id: string
  role: 'user' | 'assistant'
  content: string
  tool_metadata?: {
    tools_used: Array<{
      tool: string                              // 'analyze_document' | 'explore_knowledge_base' | future
      document_name?: string                    // analyze_document only (legacy)
      question?: string                         // Phase 5 NEW — explore_knowledge_base only
      sub_agent_id?: string                     // Phase 5 NEW — server-generated UUID
      tool_calls?: Array<{                      // Phase 5 NEW — Explorer's nested tool trace
        tool: string                            // 'tree' | 'glob' | 'grep' | 'list_files' | 'read_document'
        args?: Record<string, any>
        result_preview?: string
        turn?: number
      }>
      sub_agent_result?: string
    }>
  } | null
  created_at: string
}

// ── Profile & Settings ──

export interface Profile {
  id: string
  email: string
  is_admin: boolean
  created_at: string
  updated_at: string
}

export interface MetadataFieldDefinition {
  name: string
  type: string
  required: boolean
  description: string
}

export interface GlobalSettings {
  llm_model: string | null
  langsmith_project: string | null
  langsmith_tracing: boolean
  llm_api_key_set: boolean
  langsmith_api_key_set: boolean
  metadata_schema: MetadataFieldDefinition[] | null
  hybrid_search_enabled: boolean
  reranking_enabled: boolean
  reranking_provider: string
  cohere_api_key_set: boolean
  text_to_sql_enabled: boolean
  web_search_enabled: boolean
  tavily_api_key_set: boolean
  updated_at: string | null
}

export interface GlobalSettingsUpdate {
  llm_api_key?: string
  llm_model?: string
  langsmith_api_key?: string
  langsmith_project?: string
  langsmith_tracing?: boolean
  metadata_schema?: MetadataFieldDefinition[]
  hybrid_search_enabled?: boolean
  reranking_enabled?: boolean
  reranking_provider?: string
  cohere_api_key?: string
  text_to_sql_enabled?: boolean
  web_search_enabled?: boolean
  tavily_api_key?: string
}

export async function getProfile(): Promise<Profile> {
  return fetchApi('/api/settings/profile')
}

export async function getSettings(): Promise<GlobalSettings> {
  return fetchApi('/api/settings')
}

export interface GeminiModel {
  id: string
  name: string
}

export async function getModels(): Promise<GeminiModel[]> {
  return fetchApi('/api/settings/models')
}

export async function updateSettings(data: GlobalSettingsUpdate): Promise<GlobalSettings> {
  return fetchApi('/api/settings', {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

// ── Threads ──

export async function getThreads(): Promise<Thread[]> {
  return fetchApi('/api/threads')
}

export async function createThread(title?: string): Promise<Thread> {
  return fetchApi('/api/threads', {
    method: 'POST',
    body: JSON.stringify({ title: title || null }),
  })
}

export async function deleteThread(id: string): Promise<void> {
  await fetchApi(`/api/threads/${id}`, { method: 'DELETE' })
}

export async function getMessages(threadId: string): Promise<Message[]> {
  return fetchApi(`/api/threads/${threadId}/messages`)
}

// ── Documents (Module 2: BYO Retrieval) ──

export interface Document {
  id: string
  user_id: string
  file_name: string
  file_size: number
  mime_type: string
  status: 'pending' | 'processing' | 'ready' | 'failed'
  error_message: string | null
  metadata: Record<string, any> | null
  content_hash: string | null
  action?: 'created' | 'skipped' | 'updated'
  created_at: string
  updated_at: string
}

// Alias so existing consumers don't need to change
export type UploadedFile = Document

export async function getUploadedFiles(): Promise<Document[]> {
  return fetchApi('/api/files')
}

export async function uploadFile(file: File): Promise<Document> {
  const token = await getToken()
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch('/api/files/upload', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Upload failed: ${res.status}`)
  }
  return res.json()
}

export async function deleteFile(fileId: string): Promise<void> {
  await fetchApi(`/api/files/${fileId}`, { method: 'DELETE' })
}

// --- Messages ---

export interface ToolThinkingEvent {
  available_tools: string[]
}

export interface ToolStartEvent {
  tool: string
  args?: Record<string, any>
}

export interface ToolDoneEvent {
  tool: string
  detail?: string
}

export async function sendMessage(
  threadId: string,
  content: string,
  onToken: (token: string) => void,
  onDone: (responseId: string) => void,
  signal?: AbortSignal,
  metadataFilter?: Record<string, any>,
  onSubAgentStart?: (data: {
    document_name?: string                    // analyze_document (legacy)
    question?: string                         // Phase 5 NEW — explore_knowledge_base
    agent_name?: string                       // Phase 5 NEW — discriminator
    sub_agent_id?: string                     // Phase 5 NEW
  }) => void,
  onSubAgentToken?: (token: string) => void,
  onSubAgentDone?: () => void,
  onError?: (message: string) => void,
  onToolThinking?: (data: ToolThinkingEvent) => void,
  onToolStart?: (data: ToolStartEvent) => void,
  onToolDone?: (data: ToolDoneEvent) => void,
  // Phase 5 NEW callbacks (positional-compat: appended at end)
  onSubAgentToolStart?: (data: {
    tool: string
    args?: Record<string, any>
    turn?: number
  }) => void,
  onSubAgentToolDone?: (data: {
    tool: string
    result_preview?: string
    turn?: number
  }) => void,
) {
  const token = await getToken()
  const body: Record<string, any> = { content }
  if (metadataFilter && Object.keys(metadataFilter).length > 0) {
    body.metadata_filter = metadataFilter
  }
  const res = await fetch(`/api/threads/${threadId}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error('No response body')

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      // Flush remaining bytes from the decoder
      buffer += decoder.decode()
    } else {
      buffer += decoder.decode(value, { stream: true })
    }

    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed || !trimmed.startsWith('data:')) continue
      const jsonStr = trimmed.slice(5).trim()
      if (!jsonStr || jsonStr === '[DONE]') continue

      let parsed: any
      try {
        parsed = JSON.parse(jsonStr)
      } catch {
        continue // skip malformed JSON
      }

      if (parsed.type === 'token') {
        onToken(parsed.content)
      } else if (parsed.type === 'tool_thinking') {
        onToolThinking?.(parsed as ToolThinkingEvent)
      } else if (parsed.type === 'tool_start') {
        onToolStart?.(parsed as ToolStartEvent)
      } else if (parsed.type === 'tool_done') {
        onToolDone?.(parsed as ToolDoneEvent)
      } else if (parsed.type === 'error') {
        const msg = parsed.content || 'An error occurred while generating the response'
        onError?.(msg)
        onToken(`\n\n**Error:** ${msg}`)
      } else if (parsed.type === 'sub_agent') {
        // Phase 6 (UI-10): generalized SSE envelope for all 5 sub-agent events.
        // Backend emits `{type: 'sub_agent', agent_name, event, payload}` — the
        // legacy `sub_agent_*` shapes (with trailing underscore) were removed
        // in 06-04 when the Phase 5 dual-emit window closed.
        switch (parsed.event) {
          case 'start':
            onSubAgentStart?.({ ...parsed.payload, agent_name: parsed.agent_name })
            break
          case 'token':
            onSubAgentToken?.(parsed.payload.content)
            break
          case 'tool_start':
            onSubAgentToolStart?.(parsed.payload)
            break
          case 'tool_done':
            onSubAgentToolDone?.(parsed.payload)
            break
          case 'done':
            onSubAgentDone?.()
            break
        }
      } else if (parsed.type === 'done') {
        onDone(parsed.response_id)
      }
    }

    if (done) break
  }
}
