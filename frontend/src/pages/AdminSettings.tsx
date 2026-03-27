import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { toast } from 'sonner'
import { getSettings, updateSettings, getModels, type GlobalSettings, type GlobalSettingsUpdate, type GeminiModel } from '@/lib/api'

export default function AdminSettings() {
  const navigate = useNavigate()
  const [settings, setSettings] = useState<GlobalSettings | null>(null)
  const [llmApiKey, setLlmApiKey] = useState('')
  const [llmModel, setLlmModel] = useState('')
  const [langsmithApiKey, setLangsmithApiKey] = useState('')
  const [langsmithProject, setLangsmithProject] = useState('')
  const [langsmithTracing, setLangsmithTracing] = useState(true)
  const [hybridSearchEnabled, setHybridSearchEnabled] = useState(true)
  const [rerankingEnabled, setRerankingEnabled] = useState(false)
  const [rerankingProvider, setRerankingProvider] = useState('gemini')
  const [cohereApiKey, setCohereApiKey] = useState('')
  const [textToSqlEnabled, setTextToSqlEnabled] = useState(false)
  const [webSearchEnabled, setWebSearchEnabled] = useState(false)
  const [tavilyApiKey, setTavilyApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [models, setModels] = useState<GeminiModel[]>([])
  const [loadingModels, setLoadingModels] = useState(true)

  useEffect(() => {
    getSettings().then((s) => {
      setSettings(s)
      setLlmModel(s.llm_model || '')
      setLangsmithProject(s.langsmith_project || '')
      setLangsmithTracing(s.langsmith_tracing)
      setHybridSearchEnabled(s.hybrid_search_enabled)
      setRerankingEnabled(s.reranking_enabled)
      setRerankingProvider(s.reranking_provider)
      setTextToSqlEnabled(s.text_to_sql_enabled)
      setWebSearchEnabled(s.web_search_enabled)
    }).catch(() => toast.error('Failed to load settings'))

    getModels().then(setModels).catch(() => {
      setModels([{ id: 'gemini-3-flash-preview', name: 'Gemini 3 Flash Preview' }])
    }).finally(() => setLoadingModels(false))
  }, [])

  async function handleSave() {
    if (!settings) return
    setSaving(true)
    try {
      const data: GlobalSettingsUpdate = {}
      if (llmApiKey) data.llm_api_key = llmApiKey
      if (llmModel !== (settings.llm_model || '')) data.llm_model = llmModel
      if (langsmithApiKey) data.langsmith_api_key = langsmithApiKey
      if (langsmithProject !== (settings.langsmith_project || '')) data.langsmith_project = langsmithProject
      if (langsmithTracing !== settings.langsmith_tracing) data.langsmith_tracing = langsmithTracing
      if (hybridSearchEnabled !== settings.hybrid_search_enabled) data.hybrid_search_enabled = hybridSearchEnabled
      if (rerankingEnabled !== settings.reranking_enabled) data.reranking_enabled = rerankingEnabled
      if (rerankingProvider !== settings.reranking_provider) data.reranking_provider = rerankingProvider
      if (cohereApiKey) data.cohere_api_key = cohereApiKey
      if (textToSqlEnabled !== settings.text_to_sql_enabled) data.text_to_sql_enabled = textToSqlEnabled
      if (webSearchEnabled !== settings.web_search_enabled) data.web_search_enabled = webSearchEnabled
      if (tavilyApiKey) data.tavily_api_key = tavilyApiKey

      const updated = await updateSettings(data)
      setSettings(updated)
      setLlmApiKey('')
      setLangsmithApiKey('')
      setCohereApiKey('')
      setTavilyApiKey('')
      toast.success('Settings saved')
    } catch (e: any) {
      toast.error(e.message || 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  if (!settings) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-muted-foreground">Loading settings...</div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Admin Settings</h1>
        <Button variant="outline" onClick={() => navigate('/')}>Back to Chat</Button>
      </div>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>LLM Configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="llm-api-key">API Key</Label>
            <Input
              id="llm-api-key"
              type="password"
              placeholder={settings.llm_api_key_set ? 'Key is set (leave blank to keep)' : 'Enter API key'}
              value={llmApiKey}
              onChange={(e) => setLlmApiKey(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="llm-model">Model</Label>
            <select
              id="llm-model"
              value={llmModel}
              onChange={(e) => setLlmModel(e.target.value)}
              disabled={loadingModels}
              className="flex h-9 w-full rounded-md border border-input bg-background text-foreground px-3 py-1 text-sm shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
            >
              {loadingModels ? (
                <option>Loading models...</option>
              ) : (
                models.map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))
              )}
            </select>
          </div>
        </CardContent>
      </Card>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>LangSmith Observability</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="langsmith-api-key">API Key</Label>
            <Input
              id="langsmith-api-key"
              type="password"
              placeholder={settings.langsmith_api_key_set ? 'Key is set (leave blank to keep)' : 'Enter API key'}
              value={langsmithApiKey}
              onChange={(e) => setLangsmithApiKey(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="langsmith-project">Project Name</Label>
            <Input
              id="langsmith-project"
              value={langsmithProject}
              onChange={(e) => setLangsmithProject(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-2">
            <input
              id="langsmith-tracing"
              type="checkbox"
              checked={langsmithTracing}
              onChange={(e) => setLangsmithTracing(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300"
            />
            <Label htmlFor="langsmith-tracing">Enable Tracing</Label>
          </div>
        </CardContent>
      </Card>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Retrieval Settings</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <input
              id="hybrid-search"
              type="checkbox"
              checked={hybridSearchEnabled}
              onChange={(e) => setHybridSearchEnabled(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300"
            />
            <Label htmlFor="hybrid-search">Enable Hybrid Search (Vector + Keyword)</Label>
          </div>
          <p className="text-sm text-muted-foreground">
            Combines semantic vector search with PostgreSQL full-text keyword search using Reciprocal Rank Fusion.
          </p>
          <div className="flex items-center gap-2">
            <input
              id="reranking"
              type="checkbox"
              checked={rerankingEnabled}
              onChange={(e) => setRerankingEnabled(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300"
            />
            <Label htmlFor="reranking">Enable LLM Reranking</Label>
          </div>
          <p className="text-sm text-muted-foreground">
            Re-scores and reorders results after retrieval. Improves relevance but adds latency.
          </p>
          {rerankingEnabled && (
            <>
              <div className="space-y-2">
                <Label htmlFor="reranking-provider">Reranking Provider</Label>
                <select
                  id="reranking-provider"
                  value={rerankingProvider}
                  onChange={(e) => setRerankingProvider(e.target.value)}
                  className="flex h-9 w-full rounded-md border border-input bg-background text-foreground px-3 py-1 text-sm shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
                >
                  <option value="gemini">Gemini (LLM-as-Judge)</option>
                  <option value="cohere">Cohere (Rerank API)</option>
                </select>
              </div>
              {rerankingProvider === 'cohere' && (
                <div className="space-y-2">
                  <Label htmlFor="cohere-api-key">Cohere API Key</Label>
                  <Input
                    id="cohere-api-key"
                    type="password"
                    placeholder={settings.cohere_api_key_set ? 'Key is set (leave blank to keep)' : 'Enter Cohere API key'}
                    value={cohereApiKey}
                    onChange={(e) => setCohereApiKey(e.target.value)}
                  />
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Additional Tools</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <input
              id="text-to-sql"
              type="checkbox"
              checked={textToSqlEnabled}
              onChange={(e) => setTextToSqlEnabled(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300"
            />
            <Label htmlFor="text-to-sql">Enable Text-to-SQL</Label>
          </div>
          <p className="text-sm text-muted-foreground">
            Lets the LLM write SQL queries against tabular data from uploaded CSV/XLSX files using DuckDB.
          </p>
          <div className="flex items-center gap-2">
            <input
              id="web-search"
              type="checkbox"
              checked={webSearchEnabled}
              onChange={(e) => setWebSearchEnabled(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300"
            />
            <Label htmlFor="web-search">Enable Web Search</Label>
          </div>
          <p className="text-sm text-muted-foreground">
            Falls back to web search when your documents don't have the answer. Uses Tavily API.
          </p>
          {webSearchEnabled && (
            <div className="space-y-2">
              <Label htmlFor="tavily-api-key">Tavily API Key</Label>
              <Input
                id="tavily-api-key"
                type="password"
                placeholder={settings.tavily_api_key_set ? 'Key is set (leave blank to keep)' : 'Enter Tavily API key'}
                value={tavilyApiKey}
                onChange={(e) => setTavilyApiKey(e.target.value)}
              />
            </div>
          )}
        </CardContent>
      </Card>

      <Button onClick={handleSave} disabled={saving} className="w-full">
        {saving ? 'Saving...' : 'Save Settings'}
      </Button>
    </div>
  )
}
