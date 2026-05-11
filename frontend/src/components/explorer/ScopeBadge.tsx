interface ScopeBadgeProps {
  scope: 'user' | 'global'
}

export function ScopeBadge({ scope }: ScopeBadgeProps) {
  const config = scope === 'global'
    ? { label: 'Shared', className: 'bg-blue-100 text-blue-800' }
    : { label: 'Private', className: 'bg-zinc-100 text-zinc-800' }
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${config.className}`}
      title={`Scope: ${scope}`}
    >
      {config.label}
    </span>
  )
}
