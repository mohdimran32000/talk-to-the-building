import { useState } from 'react'

export interface ToolStep {
  tool: string
  args?: Record<string, any>
  detail?: string
  status: 'running' | 'done'
}

interface ToolActivityProps {
  steps: ToolStep[]
}

const TOOL_LABELS: Record<string, { active: string; done: string; icon: string }> = {
  search_documents: {
    active: 'Searching documents',
    done: 'Searched documents',
    icon: 'search',
  },
  web_search: {
    active: 'Searching the web',
    done: 'Searched the web',
    icon: 'web',
  },
  query_structured_data: {
    active: 'Querying data',
    done: 'Queried data',
    icon: 'sql',
  },
  analyze_document: {
    active: 'Analyzing document',
    done: 'Analyzed document',
    icon: 'doc',
  },
}

function ToolIcon({ type }: { type: string }) {
  if (type === 'web') {
    return (
      <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="8" cy="8" r="6.5" />
        <path d="M1.5 8h13M8 1.5c-2 2.5-2 9.5 0 13M8 1.5c2 2.5 2 9.5 0 13" />
      </svg>
    )
  }
  if (type === 'search') {
    return (
      <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="7" cy="7" r="4.5" />
        <path d="M10.5 10.5L14 14" strokeLinecap="round" />
      </svg>
    )
  }
  if (type === 'sql') {
    return (
      <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
        <ellipse cx="8" cy="4" rx="6" ry="2.5" />
        <path d="M2 4v8c0 1.38 2.69 2.5 6 2.5s6-1.12 6-2.5V4" />
        <path d="M2 8c0 1.38 2.69 2.5 6 2.5s6-1.12 6-2.5" />
      </svg>
    )
  }
  // doc
  return (
    <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M4 1.5h5.5L13 5v9.5a1 1 0 01-1 1H4a1 1 0 01-1-1v-13a1 1 0 011-1z" />
      <path d="M9.5 1.5V5H13" />
      <path d="M5.5 8.5h5M5.5 11h3" strokeLinecap="round" />
    </svg>
  )
}

function DotSpinner() {
  return (
    <span className="inline-flex items-center gap-0.5 ml-1.5">
      <span className="w-1 h-1 rounded-full bg-orange-400 animate-[dotPulse_1.4s_ease-in-out_infinite]" />
      <span className="w-1 h-1 rounded-full bg-orange-400 animate-[dotPulse_1.4s_ease-in-out_0.2s_infinite]" />
      <span className="w-1 h-1 rounded-full bg-orange-400 animate-[dotPulse_1.4s_ease-in-out_0.4s_infinite]" />
    </span>
  )
}

function ToolStepRow({ step }: { step: ToolStep }) {
  const [expanded, setExpanded] = useState(false)
  const config = TOOL_LABELS[step.tool] || {
    active: `Running ${step.tool}`,
    done: `Used ${step.tool}`,
    icon: 'search',
  }

  const label = step.status === 'running' ? config.active : config.done
  const hasDetails = step.args && Object.keys(step.args).length > 0
  const queryArg = step.args?.query || step.args?.question || step.args?.document_name

  return (
    <div className="group">
      <button
        onClick={() => hasDetails && setExpanded(!expanded)}
        className={`flex items-center gap-1.5 text-xs transition-colors ${
          step.status === 'running'
            ? 'text-muted-foreground'
            : 'text-muted-foreground/70 hover:text-muted-foreground'
        } ${hasDetails ? 'cursor-pointer' : 'cursor-default'}`}
      >
        <ToolIcon type={config.icon} />
        <span>{label}</span>
        {step.status === 'running' && <DotSpinner />}
        {step.status === 'done' && hasDetails && (
          <svg
            className={`w-3 h-3 transition-transform ${expanded ? 'rotate-90' : ''}`}
            viewBox="0 0 16 16"
            fill="currentColor"
          >
            <path d="M6 3l5 5-5 5V3z" />
          </svg>
        )}
      </button>
      {expanded && queryArg && (
        <div className="ml-5 mt-1 flex items-start gap-1.5 text-xs text-muted-foreground/60">
          <svg className="w-3 h-3 mt-0.5 shrink-0" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="8" cy="8" r="6.5" />
          </svg>
          <span className="italic">{queryArg}</span>
        </div>
      )}
      {expanded && step.detail && (
        <div className="ml-5 mt-0.5 text-xs text-muted-foreground/50">
          {step.detail}
        </div>
      )}
    </div>
  )
}

export default function ToolActivity({ steps }: ToolActivityProps) {
  if (steps.length === 0) return null

  return (
    <div className="flex flex-col gap-1.5 mb-2">
      {steps.map((step, i) => (
        <ToolStepRow key={`${step.tool}-${i}`} step={step} />
      ))}
    </div>
  )
}
