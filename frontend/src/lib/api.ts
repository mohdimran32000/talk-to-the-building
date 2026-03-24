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

export async function sendMessage(
  threadId: string,
  content: string,
  onToken: (token: string) => void,
  onDone: (responseId: string) => void,
  signal?: AbortSignal,
  metadataFilter?: Record<string, any>,
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

      try {
        const event = JSON.parse(jsonStr)
        if (event.type === 'token') {
          onToken(event.content)
        } else if (event.type === 'done') {
          onDone(event.response_id)
        }
      } catch {
        // skip malformed events
      }
    }

    if (done) break
  }
}
