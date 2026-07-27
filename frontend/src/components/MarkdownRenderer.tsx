/** Very minimal Markdown renderer for headings, bold, lists.
 * Shared by the meeting detail page and the public share view. */
import { type ReactNode } from 'react'

export default function MarkdownRenderer({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <>
      {lines.map((line, i) => {
        if (line.startsWith('## ')) return (
          <h2 key={i} style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700, margin: '16px 0 8px' }}>
            {line.slice(3)}
          </h2>
        )
        if (line.startsWith('### ')) return (
          <h3 key={i} style={{ fontSize: 'var(--font-size-base)', fontWeight: 600, margin: '12px 0 4px' }}>
            {line.slice(4)}
          </h3>
        )
        if (line.startsWith('- ') || line.startsWith('* ')) return (
          <div key={i} style={{ display: 'flex', gap: 8, margin: '2px 0' }}>
            <span style={{ flexShrink: 0, color: 'var(--color-primary)' }}>•</span>
            <span>{renderInline(line.slice(2))}</span>
          </div>
        )
        if (/^\d+\. /.test(line)) return (
          <div key={i} style={{ display: 'flex', gap: 8, margin: '2px 0' }}>
            <span style={{ flexShrink: 0, minWidth: 20, color: 'var(--color-text-secondary)' }}>
              {line.match(/^(\d+)\./)?.[1]}.
            </span>
            <span>{renderInline(line.replace(/^\d+\. /, ''))}</span>
          </div>
        )
        if (line === '') return <div key={i} style={{ height: 8 }} />
        return <p key={i} style={{ margin: '2px 0' }}>{renderInline(line)}</p>
      })}
    </>
  )
}

function renderInline(s: string): ReactNode[] {
  return s.split(/(\*\*.+?\*\*)/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={i}>{part.slice(2, -2)}</strong>
    }
    return part
  })
}
