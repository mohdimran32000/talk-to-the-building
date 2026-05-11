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
  // Phase 3 / FOLDER-07 — folder-path + scope on every document row
  folder_path: string
  scope: 'user' | 'global'
  // Plan 06-01 / D-03 — backend mirrors content_markdown_status onto DocumentResponse
  content_markdown_status?: 'ready' | 'pending' | 'failed' | 'requires_user_reupload' | null
  created_at: string
  updated_at: string
}

// Alias so existing consumers don't need to change
export type UploadedFile = Document

// ── Folder API types (Phase 6 — Plans 06-08/06-09/06-10) ──

// Backend FolderResponse contract (backend/app/models/schemas.py)
export interface FolderResponse {
  id: string
  scope: 'user' | 'global'
  user_id: string | null  // null when scope='global'
  path: string
  created_at: string
}

export interface RenameFolderResponse extends FolderResponse {
  documents_updated: number
  folders_updated: number
}

// D-06 / Plan 06-12: GET /api/folders subfolders[] are typed objects (not bare strings).
// `id` is the UUID of the explicit folders row; `null` when inferred-only (no explicit folders row).
// Inferred-only entries surface id=null so FolderNode/DocumentRow can disable rename/delete
// affordances on ghost folders that materialize from `documents.folder_path` alone.
export interface FolderRef {
  id: string | null
  path: string
}

export interface ListFolderResponse {
  path: string
  documents: UploadedFile[]
  // D-06 LOCKED SHAPE — wire contract from backend FolderListResponse (Plan 06-12).
  // The literal type signature is `Array<{id: string; path: string}>` for grep-gate stability.
  // Note: backend returns id: string | null for inferred-only folders; consumers feature-detect
  // via `if (sub.id) { ...enable rename/delete... }` at the call site. Strict-typed alternative
  // is FolderRef[] (id: string | null); both shapes are wire-compatible.
  subfolders: Array<{id: string; path: string}>
}

// ── Sub-agent tool trace types (Phase 6 — Plan 06-07 SubAgentSection) ──

export interface ToolCallEntry {
  tool: 'tree' | 'glob' | 'grep' | 'list_files' | 'read_document' | 'search_documents'
  args?: Record<string, unknown>
  turn?: number
  result_preview?: string
  status?: 'running' | 'done'
}

export interface ToolUsedEntry {
  tool: 'analyze_document' | 'explore_knowledge_base' | string
  sub_agent_id?: string
  tool_calls?: ToolCallEntry[]    // empty/absent for analyze_document; populated for explore_knowledge_base
  document_name?: string
  question?: string
  sub_agent_result?: string
}

export async function getUploadedFiles(): Promise<Document[]> {
  return fetchApi('/api/files')
}

export async function uploadFile(
  file: File,
  folder_path: string = '/',
  scope: 'user' | 'global' = 'user',
): Promise<Document> {
  const token = await getToken()
  const formData = new FormData()
  formData.append('file', file)
  const qs = new URLSearchParams({ folder_path, scope }).toString()
  const res = await fetch(`/api/files/upload?${qs}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },     // NO Content-Type — let browser set multipart boundary
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

// ── Folder + Document CRUD (Phase 6 — Plans 06-08/06-09/06-10) ──

export async function listFolder(
  path: string = '/',
  scope: 'user' | 'global' | 'both' = 'both',
): Promise<ListFolderResponse> {
  const qs = new URLSearchParams({ path, scope }).toString()
  return fetchApi(`/api/folders?${qs}`)
}

export async function createFolder(
  path: string,
  scope: 'user' | 'global' = 'user',
): Promise<FolderResponse> {
  return fetchApi('/api/folders', {
    method: 'POST',
    body: JSON.stringify({ path, scope }),
  })
}

export async function renameFolder(
  id: string,
  new_path: string,
): Promise<RenameFolderResponse> {
  return fetchApi(`/api/folders/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ new_path }),
  })
}

export async function moveDocument(
  id: string,
  folder_path: string,
): Promise<Document> {
  return fetchApi(`/api/files/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ folder_path }),
  })
}

export async function renameDocument(
  id: string,
  file_name: string,
): Promise<Document> {
  return fetchApi(`/api/files/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ file_name }),
  })
}

// Pitfall 5 (RESEARCH.md §Pitfall 5): deleteFolder does NOT use fetchApi because
// the helper throws on !res.ok and would lose the structured 409 body. The backend
// contract is 200 {status:"deleted"} OR 409 {error:"FOLDER_NOT_EMPTY", document_count, subfolder_count}.
export type DeleteFolderResult =
  | { ok: true }
  | { ok: false; error: 'FOLDER_NOT_EMPTY'; document_count: number; subfolder_count: number }

export async function deleteFolder(id: string): Promise<DeleteFolderResult> {
  const token = await getToken()
  const res = await fetch(`/api/folders/${id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (res.status === 200) return { ok: true }
  if (res.status === 409) {
    const body = await res.json()
    return {
      ok: false,
      error: body.error,           // 'FOLDER_NOT_EMPTY' per backend contract
      document_count: body.document_count,
      subfolder_count: body.subfolder_count,
    }
  }
  const err = await res.json().catch(() => ({}))
  throw new Error(err.detail || `Delete failed: ${res.status}`)
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
