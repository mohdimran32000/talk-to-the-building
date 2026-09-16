import type { MetadataFieldDefinition, Document } from '@/lib/api'

interface MetadataFilterBarProps {
  schema: MetadataFieldDefinition[]
  documents: Document[]
  filters: Record<string, any>
  onFilterChange: (filters: Record<string, any>) => void
}

export default function MetadataFilterBar({
  schema,
  documents,
  filters,
  onFilterChange,
}: MetadataFilterBarProps) {
  const readyDocs = documents.filter((d) => d.status === 'ready' && d.metadata)

  if (readyDocs.length === 0) return null

  // Collect distinct values for text fields from ready documents
  const distinctValues: Record<string, string[]> = {}
  for (const field of schema) {
    if (field.type === 'text' && field.name !== 'summary') {
      const values = new Set<string>()
      for (const doc of readyDocs) {
        const val = doc.metadata?.[field.name]
        if (val && typeof val === 'string') values.add(val)
      }
      if (values.size > 0) {
        distinctValues[field.name] = Array.from(values).sort()
      }
    }
  }

  // Collect boolean fields that have values
  const booleanFields = schema.filter(
    (f) => f.type === 'boolean' && readyDocs.some((d) => d.metadata?.[f.name] !== null && d.metadata?.[f.name] !== undefined)
  )

  const hasFilters = Object.keys(filters).length > 0
  const filterableFields = Object.keys(distinctValues).length + booleanFields.length

  if (filterableFields === 0) return null

  const handleTextFilterChange = (fieldName: string, value: string) => {
    const newFilters = { ...filters }
    if (value === '') {
      delete newFilters[fieldName]
    } else {
      newFilters[fieldName] = value
    }
    onFilterChange(newFilters)
  }

  const handleBooleanFilterChange = (fieldName: string, checked: boolean) => {
    const newFilters = { ...filters }
    if (!checked) {
      delete newFilters[fieldName]
    } else {
      newFilters[fieldName] = true
    }
    onFilterChange(newFilters)
  }

  const activeCount = Object.keys(filters).length

  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-border/60 text-xs">
      <span className="font-medium text-muted-foreground">
        Filters{activeCount > 0 && <span className="ml-1 inline-block rounded-full bg-primary/15 text-primary px-1.5">{activeCount}</span>}:
      </span>

      {Object.entries(distinctValues).map(([fieldName, values]) => (
        <select
          key={fieldName}
          className="rounded-lg border border-border/70 bg-white/50 dark:bg-white/5 text-foreground px-2 py-1 text-xs backdrop-blur-sm transition-all duration-150 hover:border-primary/30 focus:outline-none focus:ring-2 focus:ring-ring"
          value={filters[fieldName] || ''}
          onChange={(e) => handleTextFilterChange(fieldName, e.target.value)}
        >
          <option value="">{fieldName}</option>
          {values.map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
      ))}

      {booleanFields.map((field) => (
        <label key={field.name} className="flex items-center gap-1 cursor-pointer">
          <input
            type="checkbox"
            className="rounded accent-primary"
            checked={!!filters[field.name]}
            onChange={(e) => handleBooleanFilterChange(field.name, e.target.checked)}
          />
          <span>{field.name}</span>
        </label>
      ))}

      {hasFilters && (
        <button
          className="text-xs text-muted-foreground hover:text-foreground underline transition-colors duration-150"
          onClick={() => onFilterChange({})}
        >
          Clear
        </button>
      )}
    </div>
  )
}
