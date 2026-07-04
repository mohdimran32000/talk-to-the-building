import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message, ToolUsedEntry } from '@/lib/api'
import ToolActivity, { type ToolStep, ToolCallRow } from './ToolActivity'

interface MessageListProps {
  messages: Message[]
  streamingContent: string
  isStreaming: boolean
  liveSubAgentTrace?: ToolUsedEntry | null
  toolSteps?: ToolStep[]
  isToolThinking?: boolean
}

// Phase 6 / Plan 06-07 — Pitfall 12 compliant SubAgentSection.
// ONE component for BOTH analyze_document and explore_knowledge_base.
// Agent-type strings appear ONLY inside `label` useMemo (presentation-string
// formatting). The recursion seam is `tool.tool_calls?.map(...)` — same shape
// for both agent types: empty array for analyze_document, populated for Explorer.
interface SubAgentSectionProps {
  tool: ToolUsedEntry
  isLive?: boolean
  defaultExpanded?: boolean
}

function SubAgentSection({ tool, isLive, defaultExpanded = false }: SubAgentSectionProps) {
  const [expanded, setExpanded] = useState(isLive ?? defaultExpanded)
  // Presentation-only string formatting via lookup map (NOT a behavior fork —
  // Pitfall 12 invariant: no if/ternary/switch on tool.tool gates JSX output.
  // The label MAP-LOOKUP below produces presentation text only; the same JSX
  // shape renders for every agent type.)
  const label = useMemo(() => {
    const LABELS: Record<string, { live: string; done: string }> = {
      analyze_document: {
        live: `Analyzing "${tool.document_name}"...`,
        done: `Analyzed "${tool.document_name}"`,
      },
      explore_knowledge_base: {
        live: `Exploring: ${tool.question}`,
        done: `Explored: ${tool.question}`,
      },
    }
    // WR-02 (Phase 6 review): for unknown tool names (anything outside the
    // LABELS lookup), sanitize before rendering. React text rendering escapes
    // HTML entities so this is not an XSS today, but a malicious or
    // accidentally mis-encoded `tool.tool` value (control chars, ANSI escapes,
    // very long strings) would otherwise render as-is. Keep only word chars,
    // whitespace, and a small punctuation allowlist; truncate to 64 chars.
    const safeName = (tool.tool ?? '')
      .replace(/[^\w\s.\-:/]/g, '')
      .slice(0, 64) || 'sub-agent'
    const entry = LABELS[tool.tool] ?? {
      live: `Running ${safeName}`,
      done: `Used ${safeName}`,
    }
    return isLive ? entry.live : entry.done
  }, [tool, isLive])

  return (
    <div className="border-l-2 border-primary/40 pl-3 ml-1 my-2">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs text-primary/90 hover:text-primary transition-colors duration-150"
      >
        <span className="font-mono">{expanded ? '▼' : '▶'}</span>
        <span>{label}</span>
        {isLive && <span className="animate-pulse ml-1">●</span>}
      </button>
      {expanded && (
        <>
          {/* Recursion seam — empty array for analyze_document, populated for explore_knowledge_base. NO if-branch on agent type. */}
          {tool.tool_calls && tool.tool_calls.length > 0 && (
            <div className="mt-1 space-y-1">
              {tool.tool_calls.map((call, i) => (
                <ToolCallRow key={i} call={call} />
              ))}
            </div>
          )}
          {tool.sub_agent_result && (
            <div className="mt-1 text-xs opacity-80">
              <MarkdownContent content={tool.sub_agent_result} />
            </div>
          )}
        </>
      )}
    </div>
  )
}

function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
        h1: ({ children }) => <h1 className="text-lg font-bold mb-2">{children}</h1>,
        h2: ({ children }) => <h2 className="text-base font-bold mb-2">{children}</h2>,
        h3: ({ children }) => <h3 className="text-sm font-bold mb-1">{children}</h3>,
        ul: ({ children }) => <ul className="list-disc pl-4 mb-2">{children}</ul>,
        ol: ({ children }) => <ol className="list-decimal pl-4 mb-2">{children}</ol>,
        li: ({ children }) => <li className="mb-0.5">{children}</li>,
        code: ({ className, children, ...props }) => {
          const isBlock = className?.includes('language-')
          if (isBlock) {
            return (
              <pre className="bg-black/10 rounded p-2 my-2 overflow-x-auto text-xs">
                <code className={className} {...props}>{children}</code>
              </pre>
            )
          }
          return (
            <code className="bg-black/10 rounded px-1 py-0.5 text-xs" {...props}>
              {children}
            </code>
          )
        },
        pre: ({ children }) => <>{children}</>,
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-current/30 pl-3 my-2 opacity-80">{children}</blockquote>
        ),
        table: ({ children }) => (
          <div className="overflow-x-auto my-2">
            <table className="text-xs border-collapse">{children}</table>
          </div>
        ),
        th: ({ children }) => <th className="border border-current/20 px-2 py-1 font-bold">{children}</th>,
        td: ({ children }) => <td className="border border-current/20 px-2 py-1">{children}</td>,
        a: ({ children, href }) => (
          <a href={href} target="_blank" rel="noopener noreferrer" className="underline">{children}</a>
        ),
        strong: ({ children }) => <strong className="font-bold">{children}</strong>,
        em: ({ children }) => <em className="italic">{children}</em>,
        hr: () => <hr className="my-2 border-current/20" />,
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

export default function MessageList({
  messages, streamingContent, isStreaming,
  liveSubAgentTrace = null,
  toolSteps = [], isToolThinking = false,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

  if (messages.length === 0 && !isStreaming) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted-foreground">
        Send a message to start the conversation
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      {messages.map((msg) => (
        <div
          key={msg.id}
          className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
        >
          <div
            className={`max-w-[75%] rounded-2xl px-4 py-2.5 text-sm ${
              msg.role === 'user'
                ? 'bg-primary/90 text-primary-foreground shadow-md shadow-primary/20 whitespace-pre-wrap'
                : 'glass-strong text-foreground'
            }`}
          >
            {msg.role === 'assistant' && msg.tool_metadata?.tools_used?.length ? (
              <>
                {msg.tool_metadata.tools_used.map((tool, i) => (
                  <SubAgentSection key={i} tool={tool as ToolUsedEntry} />
                ))}
                <MarkdownContent content={msg.content} />
              </>
            ) : msg.role === 'assistant' ? (
              <MarkdownContent content={msg.content} />
            ) : (
              msg.content
            )}
          </div>
        </div>
      ))}

      {isStreaming && (
        <div className="flex justify-start">
          <div className="glass-strong max-w-[75%] rounded-2xl px-4 py-2.5 text-sm">
            {isToolThinking && toolSteps.length === 0 && !streamingContent && (
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="8" cy="8" r="6.5" strokeDasharray="30" strokeDashoffset="10" />
                </svg>
                <span>Searching available tools</span>
                <span className="inline-flex items-center gap-0.5 ml-0.5">
                  <span className="w-1 h-1 rounded-full bg-primary animate-[dotPulse_1.4s_ease-in-out_infinite]" />
                  <span className="w-1 h-1 rounded-full bg-primary animate-[dotPulse_1.4s_ease-in-out_0.2s_infinite]" />
                  <span className="w-1 h-1 rounded-full bg-primary animate-[dotPulse_1.4s_ease-in-out_0.4s_infinite]" />
                </span>
              </div>
            )}
            {toolSteps.length > 0 && (
              <ToolActivity steps={toolSteps} />
            )}
            {liveSubAgentTrace && (
              <SubAgentSection tool={liveSubAgentTrace} isLive defaultExpanded />
            )}
            {streamingContent ? (
              <MarkdownContent content={streamingContent} />
            ) : !isToolThinking && !liveSubAgentTrace && toolSteps.length === 0 ? (
              <span className="text-muted-foreground animate-pulse">Thinking...</span>
            ) : !streamingContent && toolSteps.length > 0 && toolSteps.every(s => s.status === 'done') && !liveSubAgentTrace ? (
              <span className="text-muted-foreground animate-pulse mt-1 block">Generating response...</span>
            ) : null}
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
