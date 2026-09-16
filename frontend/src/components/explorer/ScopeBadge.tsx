interface ScopeBadgeProps {
  scope: 'user' | 'global'
}

export function ScopeBadge({ scope }: ScopeBadgeProps) {
  const config = scope === 'global'
    ? { label: 'Shared', className: 'bg-primary/15 text-primary dark:bg-primary/20 dark:text-primary' }
    : { label: 'Private', className: 'bg-foreground/8 text-muted-foreground' }
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${config.className}`}
      title={`Scope: ${scope}`}
    >
      {config.label}
    </span>
  )
}
