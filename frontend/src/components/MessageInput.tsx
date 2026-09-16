import { useState, useRef, type KeyboardEvent } from 'react'
import { Button } from '@/components/ui/button'

interface MessageInputProps {
  onSend: (content: string) => void
  onStop?: () => void
  disabled: boolean
  isStreaming?: boolean
}

export default function MessageInput({ onSend, onStop, disabled, isStreaming }: MessageInputProps) {
  const [content, setContent] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = () => {
    const trimmed = content.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setContent('')
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = () => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`
    }
  }

  return (
    <div className="border-t border-border/60 p-3">
      <div className="glass-strong flex gap-2 rounded-2xl p-2">
        <textarea
          ref={textareaRef}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          placeholder="Type a message..."
          disabled={disabled}
          rows={1}
          className="flex-1 resize-none rounded-lg border border-transparent bg-transparent px-3 py-2 text-sm placeholder:text-muted-foreground transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-ring"
          autoFocus
        />
        {isStreaming ? (
          <Button variant="destructive" onClick={onStop}>
            Stop
          </Button>
        ) : (
          <Button onClick={handleSend} disabled={disabled || !content.trim()}>
            Send
          </Button>
        )}
      </div>
    </div>
  )
}
