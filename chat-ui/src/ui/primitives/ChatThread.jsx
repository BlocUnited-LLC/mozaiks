/**
 * ChatThread — reusable chat/DM/support thread primitives.
 *
 * Exported components:
 *   ChatMessageBubble  — renders a single message (user / assistant / operator / system)
 *   ChatInput          — composer textarea with send button
 *   ChatThread         — full thread: scrolling messages + input at bottom
 *
 * Designed for:
 *   - Support inbox operator view (factory_app/app/admin)
 *   - Generated apps that include a DM or in-app messaging module
 *   - Any workflow UI that surfaces a conversation transcript
 *
 * Usage:
 *   import { ChatThread, ChatMessageBubble, ChatInput } from '@mozaiks/chat-ui/ui'
 */

import { useEffect, useRef, useState } from 'react'

// ─── Single message bubble ────────────────────────────────────────────────────

/**
 * role: 'user' | 'assistant' | 'operator' | 'system'
 * content: string
 * senderLabel: optional override label shown above the bubble
 */
/**
 * role:
 *   'user'      — outgoing (right, primary bg) — all contexts
 *   'assistant' — AI response (left, card bg, AI avatar)
 *   'operator'  — support agent reply (left, amber bg + Support badge)
 *   'peer'      — DM from another person (left, muted bg + initials avatar)
 *   'system'    — status pill (centered)
 *
 * senderLabel — overrides the label shown above the bubble
 * avatarText  — initials shown in the peer avatar (DMs)
 */
export function ChatMessageBubble({ role, content, senderLabel, avatarText }) {
  const isUser      = role === 'user'
  const isOperator  = role === 'operator'
  const isPeer      = role === 'peer'
  const isSystem    = role === 'system'
  const isAssistant = role === 'assistant'
  const alignRight  = isUser

  if (isSystem) {
    return (
      <div className="mx-auto w-full max-w-sm rounded-xl border border-muted bg-muted/40 px-3 py-2 text-center text-[11px] text-muted-foreground">
        {content}
      </div>
    )
  }

  // Left-side avatar
  let avatar = null
  if (isOperator) {
    avatar = (
      <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-warning/20 text-[9px] font-bold text-warning ring-1 ring-warning/30">
        OPS
      </span>
    )
  } else if (isPeer) {
    avatar = (
      <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/30 text-[10px] font-bold text-accent-foreground ring-1 ring-border/30">
        {avatarText || '?'}
      </span>
    )
  } else if (isAssistant) {
    avatar = (
      <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/20 text-[10px] font-bold text-primary">
        AI
      </span>
    )
  }

  const label = senderLabel ?? (isOperator ? 'Support' : isPeer ? (senderLabel || null) : null)

  let bubbleClass = 'w-fit rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed break-words '
  if (isUser) {
    bubbleClass += 'rounded-tr-sm bg-primary text-primary-foreground'
  } else if (isOperator) {
    bubbleClass += 'rounded-tl-sm border border-warning/30 bg-warning/10 text-foreground'
  } else if (isPeer) {
    bubbleClass += 'rounded-tl-sm border border-border/40 bg-muted/60 text-foreground'
  } else {
    // assistant
    bubbleClass += 'rounded-tl-sm border border-border/30 bg-card/80 text-foreground'
  }

  return (
    <div className={`flex w-full gap-2.5 ${alignRight ? 'flex-row-reverse' : 'flex-row'}`}>
      {!alignRight && avatar}

      <div className={`flex max-w-[72%] flex-col gap-0.5 ${alignRight ? 'items-end' : 'items-start'}`}>
        {label && (
          <span className={`px-1 text-[10px] font-semibold ${isOperator ? 'text-warning/70' : 'text-muted-foreground/50'}`}>
            {label}
          </span>
        )}
        <div className={bubbleClass}>{content}</div>
      </div>
    </div>
  )
}

// ─── Composer input ───────────────────────────────────────────────────────────

/**
 * onSend(text: string) — called when the user submits a message
 * placeholder — input placeholder text
 * disabled — disables input and send button
 */
export function ChatInput({ onSend, placeholder = 'Type a message…', disabled = false }) {
  const [value, setValue] = useState('')

  function handleSend() {
    const text = value.trim()
    if (!text || disabled) return
    onSend(text)
    setValue('')
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="px-3 pb-3 pt-2">
      <div className="flex items-end gap-2 rounded-xl border border-border/60 bg-background px-3 py-2 transition-all focus-within:border-primary/50 focus-within:ring-1 focus-within:ring-primary/20">
        <textarea
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          className="flex-1 resize-none bg-transparent text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none disabled:opacity-50"
          style={{ maxHeight: '96px', overflowY: 'auto' }}
        />
        <button
          type="button"
          onClick={handleSend}
          disabled={!value.trim() || disabled}
          className="mb-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-30"
          aria-label="Send"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5" aria-hidden="true">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
      <p className="mt-1 px-1 text-[10px] text-muted-foreground/50">Enter to send · Shift+Enter for new line</p>
    </div>
  )
}

// ─── Full thread ──────────────────────────────────────────────────────────────

/**
 * messages: Array<{ role, content, senderLabel?, avatarText? }>
 * onSend(text): called when user sends — omit to hide composer
 * inputPlaceholder: placeholder for the composer
 * emptyText: shown when messages array is empty
 * className: extra classes on the outer container
 * variant: 'default' | 'support' | 'dm'
 *   - 'default'  — neutral (AI transcripts, admin inbox)
 *   - 'support'  — amber accent header bar, ticket feel
 *   - 'dm'       — teal accent, social messenger feel
 */
export function ChatThread({
  messages = [],
  onSend,
  inputPlaceholder = 'Type a message…',
  emptyText = 'No messages yet.',
  className = '',
  variant = 'default',
}) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length])

  // Variant-specific composer accent
  const composerAccent =
    variant === 'support'
      ? 'border-t border-warning/30 bg-card/80'
      : variant === 'dm'
        ? 'border-t border-border/40 bg-card/80'
        : 'border-t border-border/30 bg-card/60'

  return (
    <div className={`flex flex-col overflow-hidden ${className}`}>
      {/* Scrolling transcript */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {messages.length > 0 ? (
          <>
            {messages.map((msg, i) => (
              <ChatMessageBubble
                key={i}
                role={msg.role}
                content={msg.content}
                senderLabel={msg.senderLabel}
                avatarText={msg.avatarText}
              />
            ))}
            <div ref={bottomRef} />
          </>
        ) : (
          <div className="flex items-center justify-center py-10">
            <p className="text-sm text-muted-foreground/50">{emptyText}</p>
          </div>
        )}
      </div>

      {/* Composer — only rendered when onSend is provided */}
      {onSend && (
        <div className={composerAccent}>
          <ChatInput onSend={onSend} placeholder={inputPlaceholder} />
        </div>
      )}
    </div>
  )
}
