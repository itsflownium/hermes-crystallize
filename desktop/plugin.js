// Crystallize: a top-bar button (and ⌘K command) that opens the savings panel.
// Plain ESM, loaded uncompiled by Hermes Desktop (no JSX, no build step).
import { Codicon, host, useValue } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const REFRESH_MS = 20000
const PANE_ID = 'crystallize:panel' // the host namespaces ids as '<pluginId>:<localId>'

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

// Also report to our own backend, which writes plugin-data/crystallize/desktop-diag.log.
let diagCtx = null
const diag = (event, detail) => {
  log(event, detail || '')
  if (!diagCtx) return
  diagCtx.rest('/diag', { method: 'POST', body: { event, detail: detail || null } }).catch(err => log('diag failed', String(err)))
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


// ---------------------------------------------------------------- pieces

const pill = (text, onClick, tone) =>
  jsx('button', {
    type: 'button',
    onClick,
    style: {
      flexShrink: 0,
      padding: '1px 8px',
      borderRadius: 5,
      border: `0.5px solid ${tone || C.accent}`,
      background: 'none',
      color: tone || C.accent,
      fontSize: '0.6875rem',
      lineHeight: 1.6,
      cursor: 'pointer'
    },
    children: text
  })

const iconButton = (name, title, onClick, active) =>
  jsx('button', {
    type: 'button',
    title,
    'aria-label': title,
    onClick,
    style: {
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: 22, height: 22, borderRadius: 5, border: 'none', cursor: 'pointer',
      background: active ? C.tile : 'none', color: active ? C.text : C.muted
    },
    children: jsx(Codicon, { name, size: '0.8125rem' })
  })

function Progress({ value, total }) {
  return jsx('div', {
    style: { display: 'flex', gap: 3, marginTop: 4 },
    children: Array.from({ length: total }, (_, i) =>
      jsx('span', { key: i, style: { flex: 1, height: 3, borderRadius: 2, background: i < value ? C.green : C.stroke } }))
  })
}

function Card({ item, border, action, children }) {
  const saving = item.cost_now != null && item.cost_after != null
    ? `${money(item.cost_now)} → ${money(item.cost_after)} a run`
    : `${item.runs} runs`
  return box([
    row(item.name, action),
    small(`${item.scriptable_steps} of ${item.steps} steps without AI · ${saving}`),
    ...(children || [])
  ], border, item.job_id)
}

function Column({ title, items, empty, render }) {
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 6 },
    children: [
      label(`${title} · ${items.length}`),
      ...(items.length ? items.map(render) : [small(empty)])
    ]
  })
}

// ---------------------------------------------------------------- sections

function Hero({ data }) {
  const live = data.live.length
  const testing = data.testing.length
  const status = live || testing
    ? [live && `${live} live`, testing && `${testing} testing`].filter(Boolean).join(' · ')
    : data.spotted.length ? `${data.spotted.length} ready to try` : 'Watching for repeated work'
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 8 },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between' },
        children: [
          jsxs('div', {
            children: [
              label('Savings score'),
              jsxs('div', {
                style: { fontSize: '2rem', lineHeight: 1.1, color: C.text, fontVariantNumeric: 'tabular-nums' },
                children: [
                  data.score === null ? '—' : data.score,
                  jsx('span', { style: { fontSize: '0.75rem', color: C.muted, marginLeft: 2 }, children: '/100' })
                ]
              })
            ]
          }),
          jsx('div', { style: { fontSize: '0.6875rem', color: C.muted, textAlign: 'right', paddingBottom: 4 }, children: status })
        ]
      }),
      jsxs('div', {
        style: { display: 'flex', gap: 6 },
        children: [
          jsx(Tile, { title: 'Saved', value: money(data.saved_usd), color: C.green }),
          jsx(Tile, { title: 'Could save', value: `${money(data.could_save_usd_per_week)}/wk` })
        ]
      })
    ]
  })
}

function ChatCard({ chat, enabled, hasSession }) {
  if (hasSession && !enabled) {
    return box([
      row('This chat', jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: 'off' })),
      small('Crystallize is off for this chat. Click ◆ Crystallize in the message box and press Enter to turn it on.')
    ])
  }
  if (!chat) {
    return box([
      row('This chat', null),
      small('Open a chat to analyse it. Crystallize points out steps Hermes keeps repeating.')
    ])
  }
  const lines = []
  for (const [i, r] of (chat.repeats || []).entries()) {
    lines.push(jsxs('div', {
      key: `r${i}`,
      style: { marginTop: 4 },
      children: [
        row(r.steps.map(x => x.tool).join(' → '), jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: `${r.times}×` })),
        small(r.has_side_effects
          ? 'Sends or changes something, so Hermes keeps this step.'
          : `A routine would skip ${r.calls_saved} tool calls.`, r.has_side_effects ? C.soft : C.green)
      ]
    }))
  }
  for (const [i, p] of (chat.patterns || []).entries()) {
    lines.push(jsxs('div', {
      key: `p${i}`,
      style: { marginTop: 4 },
      children: [
        row(p.kind === 'streak' ? `${p.tools[0]} ${p.times}× in a row` : `${p.tools.join(' → ')} ${p.times}×`,
          jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: 'pattern' })),
        small('Same kind of step, different inputs each time.')
      ]
    }))
  }
  return box([
    row('This chat', jsx('span', { style: { fontSize: '0.6875rem', color: C.muted }, children: money(chat.spent_usd) })),
    small(`${chat.tool_calls} tool calls${chat.calls_saveable ? ` · ${chat.calls_saveable} could be skipped` : ''}`),
    ...(lines.length ? lines : [small(chat.tool_calls ? 'No repeated steps yet.' : 'No tool calls yet.')])
  ])
}

function Jobs({ data, act }) {
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 12 },
    children: [
      jsx(Column, {
        title: 'Spotted', items: data.spotted, empty: 'Nothing new yet.',
        render: it => jsx(Card, { item: it, action: pill('Try', () => act('/try', { job_id: it.job_id })) })
      }),
      jsx(Column, {
        title: 'Testing', items: data.testing, empty: 'Nothing being tested.',
        render: it => jsx(Card, {
          item: it,
          border: C.yellow,
          action: it.ready
            ? pill('Promote', () => act('/promote', { routine_id: it.routine_id }), C.green)
            : jsx('span', { style: { fontSize: '0.6875rem', color: C.muted }, children: `${it.matches} of ${it.needed}` }),
          children: [
            jsx(Progress, { key: 'p', value: Math.min(it.matches, it.needed), total: it.needed }),
            small(it.ready ? 'Matched every test. Ready to promote.'
              : it.last_test ? `Last test: ${it.last_test.why}` : 'Tested after its next scheduled run.',
              it.ready ? C.green : C.muted)
          ]
        })
      }),
      jsx(Column, {
        title: 'Live', items: data.live, empty: 'Nothing live yet.',
        render: it => jsx(Card, {
          item: it,
          border: C.green,
          action: pill('Undo', () => act('/undo', { routine_id: it.routine_id }), C.muted),
          children: [small(`Saved ${money(it.saved_usd)} over ${it.live_runs} runs${it.failures ? ` · ${it.failures} failed, fell back` : ''}`, C.green)]
        })
      })
    ]
  })
}

function Watching({ items }) {
  if (!items.length) return null
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 4 },
    children: [
      label(`Watching · ${items.length}`),
      ...items.map(it => jsx('div', {
        key: it.job_id,
        children: small([jsx('span', { key: 'n', style: { color: C.soft }, children: it.name }), ` · ${it.reason}`])
      }))
    ]
  })
}

function Settings({ prefs, setPref }) {
  const opt = (key, text) =>
    jsxs('label', {
      key,
      style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.6875rem', color: C.soft, cursor: 'pointer' },
      children: [
        jsx('input', { type: 'checkbox', checked: prefs[key], onChange: e => setPref(key, e.target.checked) }),
        text
      ]
    })
  return box([
    label('Show'),
    opt('chat', 'This chat'),
    opt('jobs', 'Spotted, Testing and Live'),
    opt('watching', 'Watching')
  ])
}

// ---------------------------------------------------------------- message-box toggle

// Puts `/crystallize on` (or `off`) into the message box. Nothing changes until
// the user sends it, so turning Crystallize on or off is always their action.
function ComposerToggle({ ctx }) {
  const sessionId = useValue(host.state.focusedStoredSessionId)
  const [enabled, setEnabled] = useState(false)

  const refresh = useCallback(() => {
    if (!sessionId) { setEnabled(false); return }
    ctx.rest(`/chat-state?session_id=${encodeURIComponent(sessionId)}`)
      .then(res => setEnabled(Boolean(res && res.enabled)))
      .catch(() => {})
  }, [ctx, sessionId])

  useEffect(() => {
    refresh()
    return ctx.setInterval(refresh, 4000)
  }, [ctx, refresh])

  const onClick = async () => {
    const command = enabled ? '/crystallize off' : '/crystallize on'
    diag('composer toggle', { sessionId, command })
    const ok = await host.composer.insertText(null, command, { mode: 'prefix' })
    host.composer.focus(null)
    host.notify(ok
      ? { kind: 'info', message: `Press Enter to send ${command}.` }
      : { kind: 'warning', message: 'Click into the message box first, then try again.' })
  }

  return jsxs('button', {
    type: 'button',
    title: enabled ? 'Crystallize is on for this chat. Click to prepare /crystallize off.'
                   : 'Crystallize is off for this chat. Click to prepare /crystallize on.',
    onClick,
    style: {
      display: 'inline-flex', alignItems: 'center', gap: 4, height: 24, padding: '0 7px',
      borderRadius: 6, border: `0.5px solid ${enabled ? C.green : C.stroke}`, background: 'none',
      color: enabled ? C.text : C.muted, fontSize: '0.6875rem', cursor: 'pointer', whiteSpace: 'nowrap'
    },
    children: [
      jsx(Codicon, { name: 'ruby', size: '0.75rem' }),
      'Crystallize',
      jsx('span', { style: { width: 6, height: 6, borderRadius: 3, background: enabled ? C.green : C.stroke } })
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

  useEffect(() => diag('panel mounted', { sessionId }), [])

  const load = useCallback(() => {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
    ctx.rest(`/summary${q}`)
      .then(res => {
        if (res && res.error) setError(res.error)
        else { setError(null); setData(res) }
      })
      .catch(err => {
        diag('summary failed', String(err))
        setError("Couldn't reach the Crystallize backend. Is the plugin enabled?")
      })
  }, [ctx, sessionId])

  useEffect(() => {
    load()
    return ctx.setInterval(load, REFRESH_MS)
  }, [ctx, load])

  const act = useCallback((path, body) => {
    diag('action', { path, body })
    ctx.rest(path, { method: 'POST', body })
      .then(res => {
        host.notify({ kind: res && res.ok ? 'success' : 'warning', message: (res && res.message) || 'Done.' })
        load()
      })
      .catch(err => host.notify({ kind: 'error', message: `Crystallize: ${String(err)}` }))
  }, [ctx, load])

  const [prefs, setPrefs] = useState(() => ctx.storage.get('prefs', { chat: true, jobs: true, watching: true }))
  const [showSettings, setShowSettings] = useState(false)
  const setPref = (key, value) => {
    const next = { ...prefs, [key]: value }
    setPrefs(next)
    ctx.storage.set('prefs', next)
  }

  const header = jsxs('div', {
    key: 'header',
    style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 6 },
        children: [
          jsx('span', { style: { display: 'inline-flex', color: C.accent }, children: jsx(Codicon, { name: 'ruby', size: '0.875rem' }) }),
          jsx('span', { style: { fontSize: '0.6875rem', letterSpacing: '0.1em', color: C.text }, children: 'CRYSTALLIZE' })
        ]
      }),
      jsxs('div', {
        style: { display: 'flex', gap: 2 },
        children: [
          iconButton('refresh', 'Refresh', load),
          iconButton('settings', 'Choose what to show', () => setShowSettings(v => !v), showSettings)
        ]
      })
    ]
  })

  let body
  if (error) body = [small(error, C.soft)]
  else if (!data) body = [small('Loading…')]
  else {
    body = [jsx(Hero, { key: 'hero', data })]
    if (prefs.chat) body.push(jsx(ChatCard, { key: 'chat', chat: data.chat, enabled: data.chat_enabled, hasSession: Boolean(sessionId) }))
    if (prefs.jobs) body.push(jsx(Jobs, { key: 'jobs', data, act }))
    if (prefs.watching) body.push(jsx(Watching, { key: 'watching', items: data.watching }))
  }

  return jsx('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 14, padding: 12, height: '100%', overflowY: 'auto' },
    children: [header, showSettings && jsx(Settings, { key: 'settings', prefs, setPref }), ...body]
  })
}

export default {
  id: 'crystallize',
  name: 'Crystallize',
  register(ctx) {
    diagCtx = ctx
    // Open / close. The SDK can reveal a panel but has no "hide" call, so closing
    // removes our panel contribution from the layout and opening adds it back.
    const PANE = {
      id: 'panel',
      area: 'panes',
      title: 'Crystallize',
      // Dock on the chat's right edge so it shows even when the right side is collapsed.
      data: { placement: 'right', dock: { pane: 'workspace', pos: 'right' }, width: '300px' },
      render: () => jsx(Panel, { ctx })
    }
    let disposePane = null

    const isVisible = () => {
      if (!disposePane) return false
      if (typeof host.paneVisibility !== 'function') return true
      try { return Boolean(host.paneVisibility(PANE_ID).get()) } catch { return true }
    }

    const reveal = () => {
      if (typeof host.undismissPane === 'function') host.undismissPane(PANE_ID)
      if (typeof host.revealPane === 'function') host.revealPane(PANE_ID)
    }

    const show = () => {
      if (!disposePane) disposePane = ctx.register(PANE)
      reveal()
      // Adoption into the layout can land a moment after registering.
      ctx.setTimeout(reveal, 60)
      ctx.storage.set('open', true)
    }

    const hide = () => {
      if (disposePane) disposePane()
      disposePane = null
      ctx.storage.set('open', false)
    }

    const open = source => {
      try {
        const visible = isVisible()
        diag('toggle', { source, visible })
        visible ? hide() : show()
      } catch (err) {
        diag('toggle failed', String(err))
        host.notify({ kind: 'error', message: `Crystallize couldn't open: ${String(err)}` })
      }
    }

    if (ctx.storage.get('open', true)) disposePane = ctx.register(PANE)

    // Top-bar button, drawn by our own component so the click handler is ours.
    // It's marked no-drag: on macOS, Windows and Linux the title bar is a window
    // drag region, and anything without no-drag never receives the click.
    //
    // Pages contributed by plugins (Kanban, …) treat anything in `titleBar.right`
    // as "this page owns the title bar" and hide the app's own buttons, so the
    // button is only mounted on chat routes ('/' and '/<session id>').
    const isChatRoute = () => {
      const path = (window.location.hash || '#/').slice(1).split(/[?#]/)[0] || '/'
      if (path === '/') return true
      const seg = decodeURIComponent(path.slice(1))
      return !seg.includes('/') && seg.length >= 16 && /\d/.test(seg) && /^[\w.:-]+$/.test(seg)
    }
    let disposeTop = null
    const syncTopButton = () => {
      const want = isChatRoute()
      if (want && !disposeTop) {
        disposeTop = ctx.register({
          id: 'top-button',
          area: 'titleBar.right',
          order: 5,
          render: () =>
            jsx('button', {
              type: 'button',
              title: 'Show or hide Crystallize',
              'aria-label': 'Show or hide Crystallize',
              onPointerDown: event => { event.stopPropagation(); diag('top button pointerdown') },
              onClick: () => open('top button'),
              style: {
                WebkitAppRegion: 'no-drag',
                appRegion: 'no-drag',
                pointerEvents: 'auto',
                position: 'relative',
                zIndex: 5,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 26,
                height: 26,
                borderRadius: 6,
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: C.muted
              },
              children: jsx(Codicon, { name: 'ruby', size: '0.875rem' })
            })
        })
      } else if (!want && disposeTop) {
        disposeTop()
        disposeTop = null
      }
    }
    syncTopButton()
    ctx.addEventListener(window, 'hashchange', syncTopButton)
    ctx.setInterval(syncTopButton, 1000)

    // A status-bar button drawn by our own component, with our own click handler.
    // The top-bar icon is a host-drawn TitlebarTool; this one doesn't depend on it.
    ctx.register({
      id: 'status',
      area: 'statusBar.right',
      order: 90,
      render: () =>
        jsxs('button', {
          type: 'button',
          title: 'Show or hide Crystallize',
          onClick: () => open('statusbar'),
          onPointerDown: () => diag('statusbar pointerdown'),
          style: {
            WebkitAppRegion: 'no-drag',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            padding: '0 6px',
            height: '100%',
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: C.muted,
            fontSize: '0.6875rem'
          },
          children: [jsx(Codicon, { name: 'ruby', size: '0.75rem' }), 'Crystallize']
        })
    })

    ctx.register({
      id: 'composer-toggle',
      area: 'composer.actions',
      order: 50,
      render: () => jsx(ComposerToggle, { ctx })
    })

    ctx.register({
      id: 'open-command',
      area: PALETTE,
      data: {
        id: 'crystallize.open',
        label: 'Crystallize: Show or hide savings panel',
        keywords: ['crystallize', 'savings', 'cost', 'repeat'],
        run: () => open('palette')
      }
    })

    diag('registered')
  }
}
