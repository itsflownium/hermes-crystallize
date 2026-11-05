// Crystallize: a top-bar button that opens the savings panel on the right.
// Plain ESM, loaded uncompiled by Hermes Desktop (no JSX, no build step).
import { Codicon, TITLEBAR_AREAS, host, useValue } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const REFRESH_MS = 20000

const C = {
  text: 'var(--ui-text-primary)',
  soft: 'var(--ui-text-secondary)',
  muted: 'var(--ui-text-tertiary)',
  faint: 'var(--ui-text-quaternary)',
  stroke: 'var(--ui-stroke-tertiary)',
  tile: 'var(--ui-bg-tertiary)',
  accent: 'var(--ui-accent)',
  green: 'var(--ui-green)',
  yellow: 'var(--ui-yellow)'
}

function money(v) {
  if (v === null || v === undefined) return '—'
  if (v > 0 && v < 0.01) return `$${v.toFixed(4)}`
  return `$${v.toFixed(2)}`
}

const label = text =>
  jsx('div', {
    style: { fontSize: '0.625rem', letterSpacing: '0.08em', textTransform: 'uppercase', color: C.muted },
    children: text
  })

function Tile({ title, value, color }) {
  return jsxs('div', {
    style: { flex: 1, background: C.tile, borderRadius: 6, padding: '6px 8px', minWidth: 0 },
    children: [
      jsx('div', { style: { fontSize: '0.625rem', color: C.muted }, children: title }),
      jsx('div', { style: { fontSize: '0.8125rem', color: color || C.text, fontVariantNumeric: 'tabular-nums' }, children: value })
    ]
  })
}

function Card({ item, border, children }) {
  const saving = item.cost_now != null && item.cost_after != null
    ? `${money(item.cost_now)} → ${money(item.cost_after)} a run`
    : `${item.runs} runs`
  return jsxs('div', {
    style: { border: `0.5px solid ${border || C.stroke}`, borderRadius: 6, padding: '6px 8px' },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 },
        children: [
          jsx('span', { style: { fontSize: '0.75rem', color: C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: item.name }),
          children
        ]
      }),
      jsx('div', {
        style: { fontSize: '0.625rem', color: C.muted, marginTop: 2 },
        children: `${item.scriptable_steps} of ${item.steps} steps without AI · ${saving}`
      })
    ]
  })
}

const linkButton = (text, onClick) =>
  jsx('button', {
    type: 'button',
    onClick,
    style: { background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: '0.6875rem', color: C.accent },
    children: text
  })

function Section({ title, items, render }) {
  if (!items || items.length === 0) return null
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 6 },
    children: [label(`${title} · ${items.length}`), ...items.map(render)]
  })
}

function Panel({ ctx }) {
  const sessionId = useValue(host.state.activeSessionId)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
    ctx.rest(`/summary${q}`)
      .then(res => {
        if (res && res.error) setError(res.error)
        else { setError(null); setData(res) }
      })
      .catch(() => setError("Couldn't reach the Crystallize backend. Is the plugin enabled?"))
  }, [ctx, sessionId])

  useEffect(() => {
    load()
    const stop = ctx.setInterval(load, REFRESH_MS)
    return stop
  }, [ctx, load])

  const tryIt = item => () =>
    host.notify({ kind: 'info', message: `Test runs for ${item.name} arrive in the next build.` })

  const body = []
  if (error) {
    body.push(jsx('div', { key: 'err', style: { fontSize: '0.75rem', color: C.soft }, children: error }))
  } else if (!data) {
    body.push(jsx('div', { key: 'load', style: { fontSize: '0.75rem', color: C.muted }, children: 'Loading…' }))
  } else {
    const empty = !data.spotted.length && !data.testing.length && !data.live.length
    body.push(
      jsxs('div', {
        key: 'head',
        style: { display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' },
        children: [
          jsxs('div', {
            children: [
              label('Savings score'),
              jsxs('div', {
                style: { fontSize: '1.375rem', color: C.text, lineHeight: 1.2, fontVariantNumeric: 'tabular-nums' },
                children: [
                  data.score === null ? '—' : data.score,
                  jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: '/100' })
                ]
              })
            ]
          }),
          data.chat_spent_usd != null &&
            jsx('div', { style: { fontSize: '0.625rem', color: C.muted }, children: `This chat: ${money(data.chat_spent_usd)}` })
        ]
      }),
      jsxs('div', {
        key: 'tiles',
        style: { display: 'flex', gap: 6 },
        children: [
          jsx(Tile, { title: 'Saved', value: money(data.saved_usd), color: C.green }),
          jsx(Tile, { title: 'Could save', value: `${money(data.could_save_usd_per_week)}/wk` })
        ]
      })
    )
    if (empty) {
      body.push(jsxs('div', {
        key: 'empty',
        style: { fontSize: '0.75rem', color: C.soft, lineHeight: 1.5 },
        children: [
          jsx('div', { style: { color: C.text }, children: 'Watching' }),
          'Savings show up once Hermes repeats something. Nothing to set up.'
        ]
      }))
    }
    body.push(
      jsx(Section, {
        key: 'spotted', title: 'Spotted', items: data.spotted,
        render: item => jsx(Card, { key: item.job_id, item, children: linkButton('Try', tryIt(item)) })
      }),
      jsx(Section, {
        key: 'testing', title: 'Testing', items: data.testing,
        render: item => jsx(Card, {
          key: item.job_id, item, border: C.yellow,
          children: jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: `${item.matches} of ${item.needed}` })
        })
      }),
      jsx(Section, {
        key: 'live', title: 'Live', items: data.live,
        render: item => jsx(Card, { key: item.job_id, item, border: C.green, children: linkButton('Undo', tryIt(item)) })
      }),
      jsx(Section, {
        key: 'watching', title: 'Watching', items: data.watching,
        render: item => jsxs('div', {
          key: item.job_id,
          style: { fontSize: '0.6875rem', color: C.muted },
          children: [jsx('span', { style: { color: C.soft }, children: item.name }), ` · ${item.reason}`]
        })
      })
    )
  }

  return jsx('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 10, padding: 12, height: '100%', overflowY: 'auto' },
    children: body
  })
}

export default {
  id: 'crystallize',
  name: 'Crystallize',
  register(ctx) {
    let open = ctx.storage.get('open', false)
    let disposePane = null
    let disposeTool = null

    const showPane = () => {
      if (disposePane) return
      disposePane = ctx.register({
        id: 'panel',
        area: 'panes',
        title: 'Crystallize',
        data: { placement: 'right', width: '300px' },
        render: () => jsx(Panel, { ctx })
      })
    }

    const hidePane = () => {
      if (disposePane) disposePane()
      disposePane = null
    }

    const setTool = () => {
      if (disposeTool) disposeTool()
      disposeTool = ctx.register({
        id: 'button',
        area: TITLEBAR_AREAS.right,
        order: 5,
        data: {
          id: 'crystallize',
          label: 'Crystallize',
          title: open ? 'Hide Crystallize' : 'Show Crystallize',
          active: open,
          icon: jsx(Codicon, { name: 'ruby', size: '0.875rem' }),
          onSelect: () => {
            open = !open
            ctx.storage.set('open', open)
            open ? showPane() : hidePane()
            setTool()
          }
        }
      })
    }

    if (open) showPane()
    setTool()
    ctx.onDispose(() => {
      hidePane()
      if (disposeTool) disposeTool()
    })
  }
}
