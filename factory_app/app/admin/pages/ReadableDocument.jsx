import { useState } from 'react'

import { ActionButton } from '../../ui/components/StudioShared.jsx'
import {
  humanizeKey,
  isEmptyValue,
  isHexColor,
  isHiddenKey,
  itemTitle,
} from './projectRoomModel.js'

const MAX_DEPTH = 5

function Heading({ depth, children }) {
  const className = depth <= 1
    ? 'text-base font-semibold text-foreground'
    : depth === 2
      ? 'text-sm font-semibold text-foreground'
      : 'text-sm font-medium text-foreground/90'
  if (depth <= 1) return <h3 className={className}>{children}</h3>
  if (depth === 2) return <h4 className={className}>{children}</h4>
  return <h5 className={className}>{children}</h5>
}

function Swatch({ value }) {
  return (
    <span className="inline-flex items-center gap-2">
      {/* The colour is the document's own value, shown as data. */}
      <span
        aria-hidden="true"
        className="inline-block h-4 w-4 shrink-0 rounded border border-border"
        style={{ backgroundColor: value.trim() }}
      />
      <span className="font-mono text-xs">{value.trim()}</span>
    </span>
  )
}

function Scalar({ value }) {
  if (typeof value === 'boolean') return <span>{value ? 'Yes' : 'No'}</span>
  if (isHexColor(value)) return <Swatch value={value} />
  return <span className="whitespace-pre-wrap break-words">{String(value)}</span>
}

function isScalar(value) {
  return value === null || ['string', 'number', 'boolean'].includes(typeof value)
}

function ValueBlock({ value, depth }) {
  if (isScalar(value)) {
    return <p className="text-sm leading-6 text-muted-foreground"><Scalar value={value} /></p>
  }
  if (Array.isArray(value)) {
    const items = value.filter((item) => !isEmptyValue(item))
    if (items.every(isScalar)) {
      return (
        <ul className="list-disc space-y-1 pl-5 text-sm leading-6 text-muted-foreground">
          {items.map((item, index) => <li key={index}><Scalar value={item} /></li>)}
        </ul>
      )
    }
    return (
      <div className="space-y-3">
        {items.map((item, index) => (
          <div key={index} className="border-l-2 border-border/60 pl-3">
            {isScalar(item) || Array.isArray(item)
              ? <ValueBlock value={item} depth={depth + 1} />
              : <Section title={itemTitle(item, index)} value={item} depth={depth + 1} />}
          </div>
        ))}
      </div>
    )
  }
  if (depth >= MAX_DEPTH) {
    return <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 text-xs text-muted-foreground">{JSON.stringify(value, null, 2)}</pre>
  }
  return (
    <div className="space-y-4">
      {Object.entries(value)
        .filter(([key, item]) => !isHiddenKey(key) && !isEmptyValue(item))
        .map(([key, item]) => <Section key={key} title={humanizeKey(key)} value={item} depth={depth + 1} />)}
    </div>
  )
}

function Section({ title, value, depth }) {
  return (
    <section className="space-y-2">
      <Heading depth={depth}>{title}</Heading>
      <ValueBlock value={value} depth={depth} />
    </section>
  )
}

/** A saved JSON document shown as headings, paragraphs and lists. */
export default function ReadableDocument({ content }) {
  const [raw, setRaw] = useState(false)
  if (isEmptyValue(content)) {
    return <p className="text-sm text-muted-foreground">This version has no readable content saved.</p>
  }
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <ActionButton variant="ghost" size="sm" onClick={() => setRaw((value) => !value)}>
          {raw ? 'Show readable' : 'Show raw'}
        </ActionButton>
      </div>
      {raw ? (
        <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-muted/40 p-3 text-xs text-foreground">
          {JSON.stringify(content, null, 2)}
        </pre>
      ) : (
        <ValueBlock value={content} depth={0} />
      )}
    </div>
  )
}
