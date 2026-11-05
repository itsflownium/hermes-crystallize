// Crystallize: a top-bar button (and ⌘K command) that opens the savings panel.
// Plain ESM, loaded uncompiled by Hermes Desktop (no JSX, no build step).
import { Codicon, host, useValue } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const REFRESH_MS = 20000
const PANE_ID = 'crystallize:panel' // the host namespaces ids as '<pluginId>:<localId>'

// Icon buttons beside the app's own top-right tools are DATA contributions in
// `titleBar.tools.right` (a TitlebarTool). `titleBar.right` is a different slot:
// it renders components and is meant for pages that project their own chrome.
const TITLEBAR_TOOLS_RIGHT = 'titleBar.tools.right'
const PALETTE = 'palette'

const C = {
  text: 'var(--ui-text-primary)',
  soft: 'var(--ui-text-secondary)',
  muted: 'var(--ui-text-tertiary)',
  stroke: 'var(--ui-stroke-tertiary)',
  tile: 'var(--ui-bg-tertiary)',
  accent: 'var(--ui-accent)',
  green: 'var(--ui-green)',
  yellow: 'var(--ui-yellow)'
}

// Desktop only writes warnings and errors to ~/.hermes/logs/desktop.log, so the
// diagnostics use warn to be readable there while the panel is being shaken out.
const log = (...args) => console.warn('[crystallize]', ...args)

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

const small = (text, color) => jsx('div', { style: { fontSize: '0.6875rem', color: color || C.muted, lineHeight: 1.45 }, children: text })

function Tile({ title, value, color }) {
  return jsxs('div', {
    style: { flex: 1, background: C.tile, borderRadius: 6, padding: '6px 8px', minWidth: 0 },
    children: [
      jsx('div', { style: { fontSize: '0.625rem', color: C.muted }, children: title }),
      jsx('div', { style: { fontSize: '0.8125rem', color: color || C.text, fontVariantNumeric: 'tabular-nums' }, children: value })
    ]
  })
}

const box = (children, border, key) =>
  jsx('div', {
    key,
    style: { border: `0.5px solid ${border || C.stroke}`, borderRadius: 6, padding: '6px 8px', display: 'flex', flexDirection: 'column', gap: 2 },
    children
  })

const linkButton = (text, onClick) =>
  jsx('button', {
    type: 'button',
    onClick,
    style: { background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: '0.6875rem', color: C.accent },
    children: text
  })

const row = (left, right) =>
  jsxs('div', {
    style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 },
    children: [
      jsx('span', { style: { fontSize: '0.75rem', color: C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: left }),
      right
    ]
  })

const comingSoon = name => () =>
  host.notify({ kind: 'info', message: `Test runs for ${name} arrive in the next build.` })

// ---------------------------------------------------------------- chat view

function ChatSection({ chat }) {
  if (!chat) {
    return jsxs('div', {
      style: { display: 'flex', flexDirection: 'column', gap: 6 },
      children: [
        label('This chat'),
        box([
          jsx('div', { key: 't', style: { fontSize: '0.75rem', color: C.text }, children: 'Open a chat to analyse it' }),
          small('Crystallize looks at what Hermes did in the chat and points out steps it keeps repeating.', null)
        ])
      ]
    })
  }

  const items = []
  for (const [i, r] of (chat.repeats || []).entries()) {
    items.push(box([
      row(`${r.steps.map(s => s.tool).join(' → ')}`, jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: `${r.times}×` })),
      small(r.steps[0].args),
      small(r.has_side_effects
        ? 'Sends or changes something, so Hermes keeps doing this part itself.'
        : `Saving this as one routine would skip ${r.calls_saved} tool calls.`, r.has_side_effects ? C.soft : C.green)
    ], null, `r${i}`))
  }
  for (const [i, p] of (chat.patterns || []).entries()) {
    const what = p.kind === 'streak' ? `${p.tools[0]} ${p.times}× in a row` : `${p.tools.join(' → ')} ${p.times}×`
    items.push(box([
      row(what, jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: 'pattern' })),
      p.example ? small(p.example) : null,
      small('Same kind of step with different inputs each time. Worth watching if this task comes up again.')
    ], C.stroke, `p${i}`))
  }

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 6 },
    children: [
      label('This chat'),
      jsxs('div', {
        style: { display: 'flex', gap: 6 },
        children: [
          jsx(Tile, { title: 'Spent', value: money(chat.spent_usd) }),
          jsx(Tile, { title: 'Tool calls', value: String(chat.tool_calls) }),
          jsx(Tile, { title: 'Could skip', value: `${chat.calls_saveable} calls`, color: chat.calls_saveable ? C.green : undefined })
        ]
      }),
      ...(items.length
        ? items
        : [small(chat.tool_calls ? 'No repeated steps in this chat.' : 'No tool calls in this chat yet.')])
    ]
  })
}

// ---------------------------------------------------------------- jobs view

function JobsSection({ data }) {
  const card = (item, border, right, key) => {
    const saving = item.cost_now != null && item.cost_after != null
      ? `${money(item.cost_now)} → ${money(item.cost_after)} a run`
      : `${item.runs} runs`
    return box([
      row(item.name, right),
      small(`${item.scriptable_steps} of ${item.steps} steps without AI · ${saving}`)
    ], border, key)
  }
  const group = (title, items, render) =>
    items && items.length ? [label(`${title} · ${items.length}`), ...items.map(render)] : []

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 6 },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginTop: 4 },
        children: [
          label('Scheduled jobs'),
          jsxs('div', {
            style: { fontSize: '1.125rem', color: C.text, fontVariantNumeric: 'tabular-nums' },
            children: [
              data.score === null ? '—' : data.score,
              jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: '/100 score' })
            ]
          })
        ]
      }),
      jsxs('div', {
        style: { display: 'flex', gap: 6 },
        children: [
          jsx(Tile, { title: 'Saved', value: money(data.saved_usd), color: C.green }),
          jsx(Tile, { title: 'Could save', value: `${money(data.could_save_usd_per_week)}/wk` })
        ]
      }),
      ...group('Spotted', data.spotted, it => card(it, null, linkButton('Try', comingSoon(it.name)), it.job_id)),
      ...group('Testing', data.testing, it =>
        card(it, C.yellow, jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: `${it.matches} of ${it.needed}` }), it.job_id)),
      ...group('Live', data.live, it => card(it, C.green, linkButton('Undo', comingSoon(it.name)), it.job_id)),
      ...group('Watching', data.watching, it =>
        jsx('div', { key: it.job_id, children: small([jsx('span', { key: 'n', style: { color: C.soft }, children: it.name }), ` · ${it.reason}`]) }))
    ]
  })
}

// ---------------------------------------------------------------- panel

function Panel({ ctx }) {
  // Stored (durable) id of the chat being looked at: the id Hermes keys costs
  // and messages by, and it follows focus between split tiles.
  const sessionId = useValue(host.state.focusedStoredSessionId)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => log('panel mounted'), [])

  const load = useCallback(() => {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
    ctx.rest(`/summary${q}`)
      .then(res => {
        if (res && res.error) setError(res.error)
        else { setError(null); setData(res) }
      })
      .catch(err => {
        log('summary failed', String(err))
        setError("Couldn't reach the Crystallize backend. Is the plugin enabled?")
      })
  }, [ctx, sessionId])

  useEffect(() => {
    load()
    return ctx.setInterval(load, REFRESH_MS)
  }, [ctx, load])

  let body
  if (error) body = [small(error, C.soft)]
  else if (!data) body = [small('Loading…')]
  else body = [jsx(ChatSection, { key: 'chat', chat: data.chat }), jsx(JobsSection, { key: 'jobs', data })]

  return jsx('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 12, padding: 12, height: '100%', overflowY: 'auto' },
    children: body
  })
}

export default {
  id: 'crystallize',
  name: 'Crystallize',
  register(ctx) {
    const open = source => {
      log('open requested from', source, {
        reveal: typeof host.revealPane, undismiss: typeof host.undismissPane
      })
      try {
        if (typeof host.undismissPane === 'function') host.undismissPane(PANE_ID)
        if (typeof host.revealPane === 'function') host.revealPane(PANE_ID)
        const visible = typeof host.paneVisibility === 'function' ? host.paneVisibility(PANE_ID).get() : 'unknown'
        log('panel visible after reveal:', visible)
        if (visible === false) {
          host.notify({ kind: 'warning', message: "Crystallize couldn't open its panel. Try ⌘K → Reload desktop plugins." })
        }
      } catch (err) {
        log('open failed', String(err))
        host.notify({ kind: 'error', message: `Crystallize couldn't open: ${String(err)}` })
      }
    }

    // Always registered: the layout adopts it like any other panel (drag, resize
    // or close it). The button and ⌘K command bring it back and to the front.
    ctx.register({
      id: 'panel',
      area: 'panes',
      title: 'Crystallize',
      // Dock on the chat's right edge so it shows even when the right side is collapsed.
      data: { placement: 'right', dock: { pane: 'workspace', pos: 'right' }, width: '300px' },
      render: () => jsx(Panel, { ctx })
    })

    ctx.register({
      id: 'button',
      area: TITLEBAR_TOOLS_RIGHT,
      order: 5,
      data: {
        id: 'crystallize',
        label: 'Crystallize',
        title: 'Open Crystallize',
        icon: jsx(Codicon, { name: 'ruby', size: '0.875rem' }),
        onSelect: () => open('button')
      }
    })

    ctx.register({
      id: 'open-command',
      area: PALETTE,
      data: {
        id: 'crystallize.open',
        label: 'Crystallize: Open savings panel',
        keywords: ['crystallize', 'savings', 'cost', 'repeat'],
        run: () => open('palette')
      }
    })

    log('registered')
  }
}
