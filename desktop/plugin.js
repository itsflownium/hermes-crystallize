// Crystallize: Crystallize Cron (a top-bar button and panel) and Crystallize Chat (a sidebar page).
// Plain ESM, loaded uncompiled by Hermes Desktop (no JSX, no build step).
import { Codicon, ROUTES_AREA, SIDEBAR_NAV_AREA, host, useValue } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const REFRESH_MS = 10000
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
  if (v > 0 && v < 1) return `$${v.toFixed(4)}`
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

// ---------------------------------------------------------------- bubbles

const bubble = (children, key, extra) =>
  jsx('div', {
    key,
    style: {
      background: C.tile, borderRadius: 14, padding: '12px 12px 11px', display: 'flex',
      flexDirection: 'column', gap: 6, ...(extra || {})
    },
    children
  })

const chip = (text, color) =>
  jsx('span', {
    style: {
      fontSize: '0.625rem', letterSpacing: '0.04em', padding: '1px 7px', borderRadius: 999,
      border: `0.5px solid ${color}`, color, whiteSpace: 'nowrap'
    },
    children: text
  })

const big = (text, color) =>
  jsx('div', { style: { fontSize: '1.25rem', lineHeight: 1.15, color: color || C.text, fontVariantNumeric: 'tabular-nums' }, children: text })

function secs(v) {
  if (v === null || v === undefined) return '—'
  return v >= 90 ? `${Math.round(v / 60)} min` : `${Math.round(v)}s`
}

const perWeek = v => (v ? `${money(v)}/wk` : '—')
const perMonth = v => (v ? `≈ ${money(v * 4.33)} a month` : '')

function when(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const sameDay = d.toDateString() === new Date().toDateString()
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return sameDay ? `Today ${time}` : `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`
}

// Cost of each recent run. Hover (or tap) a bar for that run's details.
function Bars({ timeline }) {
  const [hover, setHover] = useState(null)
  if (!timeline || timeline.length < 2) return null
  // Real cost, else API price for a plan, else tokens for a local model: never a flat line.
  const size = t => t.cost_usd || t.api_cost_usd || (timeline.some(x => x.cost_usd || x.api_cost_usd) ? 0 : t.tokens || 0)
  const max = Math.max(...timeline.map(size), 1e-9)
  const t = hover === null ? null : timeline[hover]
  const tip = t && jsxs('div', {
    style: {
      position: 'absolute', bottom: 'calc(100% + 6px)', zIndex: 3, pointerEvents: 'none',
      left: `${Math.min(70, Math.max(0, (hover / timeline.length) * 100 - 15))}%`,
      background: 'var(--ui-bg-elevated, var(--ui-bg-tertiary))', border: `0.5px solid ${C.stroke}`,
      borderRadius: 8, padding: '6px 8px', minWidth: 132, boxShadow: '0 4px 14px rgba(0,0,0,0.25)'
    },
    children: [
      jsxs('div', {
        style: { display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 2 },
        children: [
          jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: when(t.ts) }),
          jsx('span', { style: { fontSize: '0.625rem', color: t.after ? C.green : C.muted }, children: t.skipped ? 'Skipped: no change' : t.no_ai ? 'No AI' : t.after ? 'Routine' : 'Full run' })
        ]
      }),
      jsx('div', { style: { fontSize: '0.8125rem', color: C.text, fontVariantNumeric: 'tabular-nums' }, children: money(t.cost_usd) }),
      t.api_cost_usd != null ? jsx('div', { style: { fontSize: '0.625rem', color: C.soft }, children: t.included ? `Included in your plan · ${money(t.api_cost_usd)} at API prices` : `No price from this route · about ${money(t.api_cost_usd)} at API prices` }) : null,
      jsx('div', {
        style: { fontSize: '0.625rem', color: C.soft, fontVariantNumeric: 'tabular-nums' },
        children: t.skipped || t.no_ai ? 'The AI never started' : [secs(t.seconds), `${t.tool_calls} tool call${t.tool_calls === 1 ? '' : 's'}`,
          t.ai_calls != null ? `${t.ai_calls} AI call${t.ai_calls === 1 ? '' : 's'}` : null,
          t.tokens != null ? `${Math.round(t.tokens).toLocaleString()} tokens` : null].filter(Boolean).join(' · ')
      })
    ]
  })
  return jsxs('div', {
    style: { position: 'relative', marginTop: 2 },
    onMouseLeave: () => setHover(null),
    children: [
      tip,
      jsx('div', {
        style: { display: 'flex', alignItems: 'flex-end', gap: 2, height: 32 },
        children: timeline.map((r, i) =>
          jsx('span', {
            key: i,
            onMouseEnter: () => setHover(i),
            onClick: () => setHover(h => (h === i ? null : i)),
            style: {
              flex: 1, borderRadius: 2, minHeight: 2, cursor: 'pointer',
              height: `${Math.max(8, Math.round((size(r) / max) * 100))}%`,
              background: r.skipped ? 'transparent' : r.after ? C.green : C.stroke,
              border: r.skipped ? `1px dashed ${C.green}` : 'none', boxSizing: 'border-box',
              opacity: hover === null || hover === i ? 1 : 0.45,
              outline: hover === i ? `1px solid ${C.text}` : 'none',
              transition: 'opacity 120ms'
            }
          }))
      })
    ]
  })
}

function Compare({ before, after }) {
  if (!before) return null
  const cell = (title, b, a) =>
    jsxs('div', {
      style: { flex: 1, minWidth: 0 },
      children: [
        jsx('div', { style: { fontSize: '0.625rem', color: C.muted }, children: title }),
        jsxs('div', {
          style: { fontSize: '0.75rem', color: C.text, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' },
          children: [b, a !== null ? jsx('span', { style: { color: C.green }, children: ` → ${a}` }) : null]
        })
      ]
    })
  const has = after && after.runs
  const plan = !before.cost_usd && before.api_cost_usd != null
  return jsxs('div', {
    style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 8 },
    children: [
      plan
        ? cell('At API prices', money(before.api_cost_usd), has ? money(after.api_cost_usd) : null)
        : cell('Cost a run', money(before.cost_usd), has ? money(after.cost_usd) : null),
      cell('Time', secs(before.seconds), has ? secs(after.seconds) : null),
      cell('Tool calls', before.tool_calls == null ? '—' : before.tool_calls.toFixed(1), has ? after.tool_calls.toFixed(1) : null)
    ]
  })
}

function untilText(iso) {
  const mins = Math.round((new Date(iso).getTime() - Date.now()) / 60000)
  if (Number.isNaN(mins)) return null
  if (mins <= 0) return 'any moment'
  if (mins < 60) return `in ${mins} min`
  const hours = Math.round(mins / 60)
  return hours < 48 ? `in ${hours} h` : `in ${Math.round(hours / 24)} days`
}

function agoText(iso) {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (Number.isNaN(mins)) return null
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hours = Math.round(mins / 60)
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`
}

// What the job is doing right now, straight from Hermes.
function cronLine(cron) {
  if (!cron) return null
  const count = cron.runs_total ? `${cron.runs_done || 0} of ${cron.runs_total} runs` : null
  const last = cron.last_run_at ? `last run ${agoText(cron.last_run_at)}` : null
  if (cron.status === 'running') return ['Running now', cron.schedule, count, last].filter(Boolean).join(' · ')
  if (cron.status === 'paused') return ['Paused', count, last].filter(Boolean).join(' · ')
  if (cron.status === 'finished') return cron.gone ? 'No longer scheduled' : [count, last].filter(Boolean).join(' · ') || 'Finished'
  const next = cron.next_run_at ? untilText(cron.next_run_at) : null
  return [next && `Next run ${next}`, cron.schedule, count, last].filter(Boolean).join(' · ')
}

// The same short timing Hermes shows in its own cron list.
function timing(cron) {
  if (!cron) return null
  if (cron.status === 'running') return 'running'
  if (cron.status === 'paused') return 'paused'
  if (cron.status === 'finished') return cron.gone ? 'removed' : 'completed'
  return cron.next_run_at ? untilText(cron.next_run_at) : null
}

function settingsLine(settings) {
  if (!settings) return null
  const tools = settings.toolsets == null ? null : settings.toolsets.length ? `tools: ${settings.toolsets.join(', ')}` : 'no tools'
  return [settings.model && settings.model.split('/').pop(), settings.reasoning && `reasoning ${settings.reasoning}`, tools]
    .filter(Boolean).join(' · ')
}

function explain(item, status) {
  const n = item.fetch_steps || 0
  if (!n) return null
  const what = n === 1 ? 'the fetch step' : `${n} fetch steps`
  if (status === 'live' && item.mode === 'no_ai') return `The routine runs ${what} and its output is the result. No AI is used.`
  if (status === 'live' && item.mode !== 'tool') {
    return `The routine runs ${what} before the AI starts, so the AI only writes the result.` +
      ({ quiet: ' When nothing has changed, no AI runs and no message goes out, so the run costs nothing.',
         repeat: ' When nothing has changed, one short AI call repeats the last answer.' }[item.when_unchanged] || '')
  }
  return `The routine runs ${what} for Hermes, so the AI only has to read the data and write the result.`
}

// Live job options: how the answer is written, and whether unchanged runs skip the AI.
function LiveOptions({ item, act }) {
  const id = { routine_id: item.routine_id }
  const opts = []
  if (item.mode === 'tool') {
    opts.push(pill('Use pre-run', () => act('/mode', { ...id, mode: 'prerun' }), C.accent))
  } else {
    if (item.mode !== 'no_ai') {
      const next = { repeat: 'quiet', quiet: 'run', run: 'repeat' }[item.when_unchanged] || 'repeat'
      const words = { repeat: 'repeat last answer', quiet: 'stay quiet', run: 'run the AI' }
      opts.push(pill(`If unchanged: ${words[item.when_unchanged] || words.repeat}`,
        () => act('/skip', { ...id, when: next }), item.when_unchanged === 'run' ? C.muted : C.green))
    }
    if (item.mode === 'no_ai') opts.push(pill('Let AI write it', () => act('/mode', { ...id, mode: 'prerun' }), C.muted))
    else if (item.no_ai_fit) opts.push(pill('Run with no AI', () => act('/mode', { ...id, mode: 'no_ai' }), C.accent))
  }
  return jsxs('div', {
    key: 'opts',
    style: { display: 'flex', flexDirection: 'column', gap: 4, marginTop: 2 },
    children: [
      jsx('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 6 }, children: opts }),
      item.mode !== 'no_ai' && item.no_ai_fit
        ? small(`No AI needed: ${item.no_ai_why}.`, C.soft) : null
    ]
  })
}

function JobBubble({ item, status, trialRuns, act, exp }) {
  const tone = status === 'live' ? C.green : status === 'testing' ? C.yellow : C.accent
  let headline, sub, action
  if (status === 'live') {
    const measured = item.after && item.after.runs
    const plan = item.plan
    const limited = item.cron && item.cron.runs_total
    const week = plan ? plan.per_week : item.saving_per_week
    const total = plan ? plan.saved : item.saved_usd
    headline = !measured ? big('Measuring…', C.soft)
      : limited ? big(`${money(total)} saved`, C.green)
        : big(perWeek(week), C.green)
    const skipped = (item.after && item.after.skipped) || 0
    sub = measured
      ? [limited ? null : perMonth(week), limited ? null : `saved ${money(total)} so far`,
        `over ${item.after.runs} runs`, plan ? (plan.included ? 'at API prices, included in your plan' : 'estimated at API prices') : null,
        skipped ? `${skipped} skipped, nothing changed` : null].filter(Boolean).join(' · ')
      : 'Savings show after its first live run.'
    action = pill('Undo', () => act('/undo', { routine_id: item.routine_id }), C.muted)
  } else if (status === 'testing') {
    headline = big(perWeek(item.saving_per_week))
    sub = `estimated · ${perMonth(item.saving_per_week)}`
    action = item.ready
      ? pill('Promote', () => act('/promote', { routine_id: item.routine_id }), C.green)
      : jsx('span', { style: { fontSize: '0.6875rem', color: C.muted }, children: `${item.matches} of ${item.needed} tests` })
  } else {
    headline = big(perWeek(item.saving_per_week))
    sub = `estimated · ${perMonth(item.saving_per_week)}`
    action = pill('Try', () => act('/try', { job_id: item.job_id }))
  }
  const open = isOpen(exp, item.job_id, item.cron)
  const running = item.cron && item.cron.status === 'running'
  const header = jsx(JobHeader, {
    item, open, toggle: exp.toggle, action, hide: exp.hide,
    tag: chip(running ? 'Running' : status === 'live' ? 'Live routine' : status === 'testing' ? (item.ready ? 'Ready' : 'Testing') : 'Spotted', tone)
  })
  const line = small(cronLine(item.cron), running ? C.green : C.soft)
  if (!open) return bubble([header, line, headline, small(sub)], item.job_id)
  return bubble([
    header,
    line,
    item.cron && item.cron.settings && !item.cron.no_ai ? small(settingsLine(item.cron.settings), C.soft) : null,
    item.cron && item.cron.last_error ? small(`Last run failed: ${item.cron.last_error}`, C.yellow) : null,
    headline,
    small(sub),
    status === 'testing' && !item.ready ? jsx(Progress, { key: 'pr', value: Math.min(item.matches, item.needed), total: item.needed }) : null,
    jsx(Compare, { key: 'c', before: item.before, after: status === 'live' ? item.after : null }),
    jsx(Periods, { key: 'p', periods: item.periods, timeline: item.timeline }),
    explain(item, status) ? small(explain(item, status), C.soft) : null,
    status === 'live' ? jsx(LiveOptions, { key: 'o', item, act }) : null,
    item.schedule_hint ? small(item.schedule_hint, C.soft) : null,
    jsx(SavingsMap, { key: 'm', item }),
    jsx(TuneRow, { key: 't', tune: item.tune, trialRuns, act }),
    status === 'testing' && item.last_test && !item.last_test.ok ? small(`Last test: ${item.last_test.why}`, C.soft) : null
  ], item.job_id)
}

function Hero({ data, act }) {
  const ready = (data.quick || []).filter(q => q.can_try).length
  const scoreBar = jsx('div', {
    style: { height: 4, borderRadius: 2, background: C.stroke, overflow: 'hidden' },
    children: jsx('div', { style: { width: `${data.score || 0}%`, height: '100%', background: C.green } })
  })
  const stat = (title, value, color) =>
    jsxs('div', {
      style: { flex: 1, minWidth: 0 },
      children: [
        jsx('div', { style: { fontSize: '0.625rem', color: C.muted }, children: title }),
        jsx('div', { style: { fontSize: '0.8125rem', color: color || C.text, fontVariantNumeric: 'tabular-nums' }, children: value })
      ]
    })
  return bubble([
    jsxs('div', {
      key: 'score',
      style: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' },
      children: [
        label('Savings score'),
        jsxs('div', {
          style: { fontSize: '1.75rem', lineHeight: 1, color: C.text, fontVariantNumeric: 'tabular-nums' },
          children: [data.score === null ? '—' : data.score, jsx('span', { style: { fontSize: '0.6875rem', color: C.muted }, children: '/100' })]
        })
      ]
    }),
    scoreBar,
    small(data.score === null
      ? 'No active scheduled jobs to score right now.'
      : 'How much of your active scheduled work already runs on cheaper, proven settings.'),
    jsxs('div', {
      key: 'stats',
      style: { display: 'flex', gap: 8, marginTop: 4 },
      children: [
        stat('Saved so far', money(data.saved_usd), C.green),
        stat('Saving now', perWeek(data.live_saving_usd_per_week), C.green),
        stat('Could still save', perWeek(data.could_save_usd_per_week))
      ]
    }),
    data.saved_api_usd != null ? small(`On plans: ${money(data.saved_api_usd)} less at API prices so far` +
      (data.api_saving_usd_per_week != null ? `, ${perWeek(data.api_saving_usd_per_week)} at this rate` : ''), C.soft) : null,
    ready > 0 ? jsx('div', { key: 'all', children: pill(`Make ${ready === 1 ? '1 job' : `all ${ready} jobs`} cheaper`, () => act('/tune-all', {}), C.accent) }) : null,
    jsx(Periods, { key: 'periods', periods: data.periods })
  ], 'hero')
}

function HowItWorks({ onHide }) {
  return bubble([
    row('How Crystallize Cron saves', pill('Got it', onHide, C.muted)),
    small('A cron job costs roughly its prompt size times its AI calls, plus reasoning. Crystallize shows where each job\'s money goes and works on all of it.', C.soft),
    small('Make cheaper uses fewer AI calls, keeps only the tools a job uses, cleans tool results and lowers reasoning. It stays only if the answers hold up and the job really got cheaper. Otherwise the old settings come back by themselves.', C.soft),
    small('A job that repeats the same steps can also get a routine that fetches its data before the AI starts, after matching test runs. If nothing changed, the run can skip the AI, or repeat its last answer if someone expects a message.', C.soft)
  ], 'how')
}

function Jobs({ data, act, exp }) {
  const items = [
    ...data.testing.filter(x => x.ready).map(x => ['testing', x]),
    ...data.live.map(x => ['live', x]),
    ...data.testing.filter(x => !x.ready).map(x => ['testing', x]),
    ...data.spotted.map(x => ['spotted', x])
  ]
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 8 },
    children: [
      label(`Scheduled jobs · ${items.length + (data.watching || []).length}`),
      ...[...items, ...(data.watching || []).map(x => ['waiting', x])]
        .map((pair, i) => ({ pair, i, rank: pair[1].cron && pair[1].cron.status === 'running' ? 0 : 1 }))
        .sort((a, b) => a.rank - b.rank || a.i - b.i)  // running jobs first
        .map(({ pair: [status, item] }) => status === 'waiting'
          ? jsx(WaitingBubble, { key: item.job_id || item.name, item, trialRuns: data.trial_runs, act, exp })
          : jsx(JobBubble, { key: item.job_id, item, status, trialRuns: data.trial_runs, act, exp })),
      ...(items.length || (data.watching || []).length ? [] : [small('No active scheduled jobs. New ones show up here as soon as they are created.')])
    ]
  })
}

const tokensText = v => (v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}k` : String(Math.round(v)))

// Numbers for one time range. A figure with nothing behind it isn't shown.
function PeriodFigures({ row }) {
  const cells = [
    ['Runs', String(row.runs)],
    row.spent_usd != null && ['Spent', money(row.spent_usd)],
    row.api_cost_usd != null && ['At API prices', money(row.api_cost_usd)],
    row.saved_usd != null && ['Saved', money(row.saved_usd), C.green],
    row.tokens != null && ['Tokens', tokensText(row.tokens)],
    row.seconds != null && ['Typical run', secs(row.seconds)],
    row.free_runs != null && ['Free runs', String(row.free_runs), C.green]
  ].filter(Boolean)
  return jsx('div', {
    style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(68px, 1fr))', gap: 8 },
    children: cells.map(([title, value, color]) => jsxs('div', {
      key: title,
      style: { minWidth: 0 },
      children: [
        jsx('div', { style: { fontSize: '0.625rem', color: C.muted }, children: title }),
        jsx('div', { style: { fontSize: '0.75rem', color: color || C.text, fontVariantNumeric: 'tabular-nums' }, children: value })
      ]
    }))
  })
}

// Switch between time ranges. Only ranges that have their own data are offered.
function Periods({ periods, timeline }) {
  const [picked, setPicked] = useState(null)
  if (!periods || !periods.length) return timeline ? jsx(Bars, { timeline }) : null
  const row = periods.find(p => p.key === picked) || periods[0]
  const bars = timeline && row.since != null ? timeline.filter(t => t.ts >= row.since) : timeline
  return jsxs('div', {
    key: 'periods',
    style: { display: 'flex', flexDirection: 'column', gap: 6, marginTop: 2 },
    children: [
      periods.length > 1 ? jsx('div', {
        role: 'tablist',
        style: { display: 'flex', gap: 2, flexWrap: 'wrap' },
        children: periods.map(p => jsx('button', {
          key: p.key,
          type: 'button',
          role: 'tab',
          'aria-selected': p.key === row.key,
          onClick: () => setPicked(p.key),
          style: {
            padding: '1px 7px', borderRadius: 5, border: 'none', cursor: 'pointer', fontSize: '0.6875rem', lineHeight: 1.6,
            background: p.key === row.key ? C.stroke : 'none', color: p.key === row.key ? C.text : C.muted
          },
          children: p.label
        }))
      }) : jsx('div', { style: { fontSize: '0.625rem', color: C.muted }, children: `Last ${row.label}`.replace('Last All time', 'All time') }),
      jsx(PeriodFigures, { row }),
      bars ? jsx(Bars, { timeline: bars }) : null
    ]
  })
}

// Active jobs start open and finished ones closed, until you choose otherwise.
function isOpen(exp, id, cron) {
  const chosen = exp.open[id]
  return chosen === undefined ? !(cron && cron.status === 'finished') : chosen
}

// The line that opens and closes a job. Buttons sit beside it, not inside it.
function JobHeader({ item, open, toggle, tag, action, muted, hide }) {
  const flip = () => toggle(item.job_id || item.name, !open)
  const when = timing(item.cron)
  return jsxs('div', {
    key: 'top',
    style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 },
    children: [
      jsxs('div', {
        role: 'button',
        tabIndex: 0,
        'aria-expanded': open,
        onClick: flip,
        onKeyDown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flip() } },
        style: { display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, flex: 1, cursor: 'pointer' },
        children: [
          jsx('span', { style: { fontSize: '0.8125rem', color: C.soft, width: 12, flexShrink: 0 }, children: open ? '▾' : '▸' }),
          jsx('span', { style: { fontSize: muted ? '0.75rem' : '0.8125rem', color: muted ? C.soft : C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: item.name }),
          tag
        ]
      }),
      action || (when ? jsx('span', { style: { fontSize: '0.6875rem', color: when === 'running' ? C.green : C.muted, flexShrink: 0 }, children: when }) : null),
      hide ? jsx('button', {
        type: 'button',
        title: 'Remove from this panel. The job itself keeps running.',
        'aria-label': `Remove ${item.name} from this panel`,
        onClick: () => hide(item.job_id || item.name),
        style: { flexShrink: 0, border: 'none', background: 'none', color: C.muted, cursor: 'pointer', fontSize: '0.875rem', lineHeight: 1, padding: '0 2px' },
        children: '×'
      }) : null
    ]
  })
}

// Cheaper settings for any AI job, routine or not.
function TuneRow({ tune, trialRuns, act }) {
  if (!tune) return null
  const notes = [
    tune.status_text !== 'Not tried yet' ? tune.status_text : null,
    tune.api_cost_now != null && tune.status !== 'kept' ? `${tune.included_now ? 'in your plan, ' : ''}about ${money(tune.api_cost_now)} a run at API prices` : null,
    tune.tokens_now != null ? `${Math.round(tune.tokens_now).toLocaleString()} tokens a run` : null,
    tune.cleaned_chars != null ? `${Math.round(tune.cleaned_chars).toLocaleString()} characters cleaned from tool results` : null,
    tune.reasoning_share != null && tune.status !== 'kept' ? `reasoning is about ${Math.round(tune.reasoning_share * 100)}% of its cost` : null
  ].filter(Boolean)
  return jsxs('div', {
    key: 'tune',
    style: { display: 'flex', flexDirection: 'column', gap: 4, marginTop: 2 },
    children: [
      notes.length ? small(notes.join(' · '), tune.status === 'kept' ? C.green : C.soft) : null,
      tune.can_try ? jsxs('div', {
        style: { display: 'flex', flexDirection: 'column', gap: 2, alignItems: 'flex-start' },
        children: [
          pill('Make cheaper', () => act('/tune', { job: tune.name }), C.accent),
          small(`Uses fewer tools and AI calls, cleans tool results and reasons less. After ${trialRuns} answers it stays only if they hold up and it got cheaper.`, C.muted)
        ]
      }) : null
    ]
  })
}

// Where the job's money goes, and each way it can save.
function SavingsMap({ item }) {
  const parts = item.cost_parts
  const rungs = (item.ladder || []).filter(r => r.state !== 'n/a')
  if (!parts && !rungs.length) return null
  const pct = v => `${Math.round(v * 100)}%`
  const mark = { on: '✓', trying: '…', available: '○' }
  const tone = { on: C.green, trying: C.yellow, available: C.muted }
  return jsxs('div', {
    key: 'map',
    style: { display: 'flex', flexDirection: 'column', gap: 3, marginTop: 2 },
    children: [
      parts ? small(`Its cost: prompt and data ${pct(parts.prompt)} · thinking ${pct(parts.thinking)} · answer ${pct(parts.answer)}`, C.soft) : null,
      rungs.length ? jsx('div', {
        style: { display: 'flex', flexDirection: 'column', gap: 1 },
        children: rungs.map(r => jsx('div', {
          key: r.key,
          style: { fontSize: '0.6875rem', color: tone[r.state] },
          children: `${mark[r.state]} ${r.label}${r.state === 'trying' ? ' (trying)' : ''}`
        }))
      }) : null
    ]
  })
}

// What a job's cheaper settings have measured so far.
function tunedHeadline(item) {
  const tuned = item.tuned
  if (!tuned) return null
  const plan = tuned.metric === 'api cost'
  const show = v => (tuned.metric === 'tokens' ? `${tokensText(v)} tokens` : tuned.metric === 'seconds' ? secs(v) : money(v))
  return jsxs('div', {
    key: 'tuned',
    style: { display: 'flex', flexDirection: 'column', gap: 2 },
    children: [
      tuned.per_week && !(item.cron && item.cron.runs_total) ? big(perWeek(tuned.per_week), C.green)
        : tuned.saved ? big(`${money(tuned.saved)} saved`, C.green) : null,
      small(`${show(tuned.before)} → ${show(tuned.after)} a run${plan ? ' at API prices' : ''} over ${tuned.runs} run${tuned.runs === 1 ? '' : 's'}`, C.green)
    ]
  })
}

// An active cron job with no routine yet: what it's doing, and what it can do.
function WaitingBubble({ item, trialRuns, act, exp }) {
  const cron = item.cron || {}
  const id = item.job_id || item.name
  const open = isOpen(exp, id, cron)
  const running = cron.status === 'running'
  const header = jsx(JobHeader, { item, open, toggle: exp.toggle, hide: exp.hide, tag: chip(running ? 'Running' : cron.status === 'paused' ? 'Paused' : 'Live cron', cron.status === 'paused' ? C.muted : C.green) })
  if (!open) return bubble([header, small(cronLine(cron), running ? C.green : C.soft), tunedHeadline(item)], id)
  return bubble([
    header,
    small(cronLine(cron), running ? C.green : C.soft),
    cron.settings && !cron.no_ai ? small(settingsLine(cron.settings), C.soft) : null,
    cron.last_error ? small(`Last run failed: ${cron.last_error}`, C.yellow) : null,
    tunedHeadline(item),
    jsx(Periods, { key: 'p', periods: item.periods, timeline: item.timeline }),
    jsx(SavingsMap, { key: 'm', item }),
    jsx(TuneRow, { key: 't', tune: item.tune, trialRuns, act }),
    small(item.reason, C.muted)
  ], id)
}

// A finished job has no actions left, so it has no buttons, but it opens like any other job.
function FinishedRow({ item, exp }) {
  const cron = item.cron || {}
  const open = isOpen(exp, item.job_id, { status: 'finished' })
  const header = jsx(JobHeader, { item, open, toggle: exp.toggle, hide: exp.hide, muted: true })
  const summary = [item.was_live && item.saved_usd ? `saved ${money(item.saved_usd)}` : null,
    item.plan_saved_usd ? `${money(item.plan_saved_usd)} less at API prices` : null, cronLine(cron)].filter(Boolean).join(' · ')
  if (!open) return bubble([header, small(summary)], item.job_id)
  return bubble([
    header,
    small(summary),
    cron.settings && !cron.no_ai ? small(settingsLine(cron.settings), C.soft) : null,
    cron.last_error ? small(`Last run failed: ${cron.last_error}`, C.yellow) : null,
    item.before && item.before.runs ? jsx(Compare, { key: 'c', before: item.before, after: item.was_live ? item.after : null }) : null,
    jsx(Periods, { key: 'p', periods: item.periods, timeline: item.timeline }),
    item.was_live ? small(explain(item, 'live'), C.soft) : small(item.reason, C.soft)
  ], item.job_id)
}

function Finished({ items, exp }) {
  if (!items || !items.length) return null
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 6 },
    children: [
      label(`Finished · ${items.length}`),
      ...items.map(it => jsx(FinishedRow, { key: it.job_id, item: it, exp }))
    ]
  })
}

function Settings({ prefs, setPref }) {
  const opt = (key, text) =>
    jsxs('label', {
      key,
      style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.6875rem', color: C.soft, cursor: 'pointer' },
      children: [jsx('input', { type: 'checkbox', checked: prefs[key] !== false, onChange: e => setPref(key, e.target.checked) }), text]
    })
  return bubble([
    label('Show'),
    opt('explain', 'How Crystallize Cron saves'),
    opt('jobs', 'Scheduled jobs'),
    opt('popups', 'Pop-ups when a job is ready to promote')
  ], 'settings')
}

// ---------------------------------------------------------------- message-box toggle

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

  const [section, setSection] = useState(() => ctx.storage.get('section', 'cron'))
  const pickSection = next => {
    setSection(next)
    ctx.storage.set('section', next)
  }
  const [opened, setOpened] = useState(() => ctx.storage.get('expanded_choices', {}))
  const exp = {
    open: opened,
    toggle: (id, value) => {
      const next = { ...opened, [id]: value }
      setOpened(next)
      ctx.storage.set('expanded_choices', next)
    }
  }
  const [hidden, setHidden] = useState(() => ctx.storage.get('hidden_jobs', {}))
  exp.hide = id => {
    const next = { ...hidden, [id]: true }
    setHidden(next)
    ctx.storage.set('hidden_jobs', next)
  }
  const showHidden = () => {
    setHidden({})
    ctx.storage.set('hidden_jobs', {})
  }
  const [prefs, setPrefs] = useState(() => ctx.storage.get('prefs', { explain: true, chat: true, jobs: true, watching: true, popups: true }))
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
    const seen = item => !hidden[item.job_id || item.name]
    const shown = { ...data, testing: data.testing.filter(seen), live: data.live.filter(seen), spotted: data.spotted.filter(seen),
      watching: (data.watching || []).filter(seen) }
    const finishedShown = (data.finished || []).filter(seen)
    const hiddenCount = [...data.testing, ...data.live, ...data.spotted, ...(data.watching || []), ...(data.finished || [])].filter(x => !seen(x)).length
    body = [jsx(Hero, { key: 'hero', data, act })]
    if (prefs.explain !== false) body.push(jsx(HowItWorks, { key: 'how', onHide: () => setPref('explain', false) }))
    if (prefs.jobs) body.push(jsx(Jobs, { key: 'jobs', data: shown, act, exp }))
    if (prefs.jobs) body.push(jsx(Finished, { key: 'finished', items: finishedShown, exp }))
    if (prefs.jobs && hiddenCount) body.push(jsx('div', { key: 'hidden', children: pill(`Show ${hiddenCount} removed job${hiddenCount === 1 ? '' : 's'}`, showHidden, C.muted) }))
  }

  return jsx('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 14, padding: 12, height: '100%', overflowY: 'auto' },
    children: section === 'notes'
      ? [header, jsx(SectionTabs, { key: 'tabs', section, onPick: pickSection }), jsx(NotesSection, { key: 'notes' })]
      : section === 'chat'
      ? [header, jsx(SectionTabs, { key: 'tabs', section, onPick: pickSection }), jsx(ChatSection, { key: 'chat', ctx, sessionId })]
      : [header, jsx(SectionTabs, { key: 'tabs', section, onPick: pickSection }), showSettings && jsx(Settings, { key: 'settings', prefs, setPref }), ...body]
  })
}

// ---------------------------------------------------------------- Crystallize Chat page

const CHAT_PATH = '/crystallize-chat'

function ModelPicker({ ctx, picked, onPicked }) {
  const [providers, setProviders] = useState(null)
  const [active, setActive] = useState(null)
  const [search, setSearch] = useState('')
  const [saving, setSaving] = useState(null)

  useEffect(() => {
    ctx.rest('/chat-models').then(res => setProviders((res && res.providers) || [])).catch(() => setProviders([]))
  }, [ctx])

  if (!providers) return bubble([label('Cheap model'), small('Loading your connected subscriptions…')], 'picker')
  if (!providers.length) {
    return bubble([label('Cheap model'), small('No connected subscriptions found. Connect one in Hermes first, and it will show up here.')], 'picker')
  }
  const slug = active || (picked && picked.provider) || providers[0].slug
  const provider = providers.find(p => p.slug === slug) || providers[0]
  const term = search.trim().toLowerCase()
  const models = provider.models.filter(m => !term || m.id.toLowerCase().includes(term))
  const choose = model => {
    setSaving(model.id)
    ctx.rest('/chat-model', { method: 'POST', body: { provider: provider.slug, model: model.id } })
      .then(res => {
        host.notify({ kind: res && res.ok ? 'success' : 'warning', message: (res && res.message) || 'Saved.' })
        if (res && res.ok) onPicked()
      })
      .catch(err => host.notify({ kind: 'error', message: `Crystallize: ${String(err)}` }))
      .finally(() => setSaving(null))
  }
  return bubble([
    row('Cheap model', picked && picked.model ? chip(picked.model.split('/').pop(), C.green) : chip('None picked', C.muted)),
    small('Your main model sends it routine work and gets the output straight back, and it reads big tool results. These come from the subscriptions connected to Hermes, which keeps your sign-ins. Models Hermes features are first, then the cheapest.', C.soft),
    jsx('div', {
      key: 'subs',
      role: 'tablist',
      style: { display: 'flex', gap: 2, flexWrap: 'wrap' },
      children: providers.map(p => jsx('button', {
        key: p.slug,
        type: 'button',
        role: 'tab',
        'aria-selected': p.slug === provider.slug,
        onClick: () => setActive(p.slug),
        style: {
          padding: '1px 7px', borderRadius: 5, border: 'none', cursor: 'pointer', fontSize: '0.6875rem', lineHeight: 1.6,
          background: p.slug === provider.slug ? C.stroke : 'none', color: p.slug === provider.slug ? C.text : C.muted
        },
        children: p.name
      }))
    }),
    jsx('input', {
      key: 'search',
      type: 'search',
      value: search,
      placeholder: `Search ${provider.name} models`,
      'aria-label': `Search ${provider.name} models`,
      onChange: e => setSearch(e.target.value),
      style: {
        padding: '4px 8px', borderRadius: 6, border: `0.5px solid ${C.stroke}`, background: 'none',
        color: C.text, fontSize: '0.75rem', width: '100%', boxSizing: 'border-box'
      }
    }),
    jsx('div', {
      key: 'models',
      style: { display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 280, overflowY: 'auto' },
      children: models.length ? models.slice(0, 60).map(m => {
        const on = picked && picked.provider === provider.slug && picked.model === m.id
        const price = m.output_per_million != null ? `$${m.input_per_million.toFixed(2)} in · $${m.output_per_million.toFixed(2)} out per million` : 'No listed price'
        return jsxs('button', {
          key: m.id,
          type: 'button',
          disabled: saving === m.id,
          onClick: () => choose(m),
          style: {
            display: 'flex', flexDirection: 'column', gap: 1, textAlign: 'left', padding: '5px 8px', borderRadius: 6, cursor: 'pointer',
            border: `0.5px solid ${on ? C.green : 'transparent'}`, background: on ? C.tile : 'none', color: C.text
          },
          children: [
            jsx('span', { style: { fontSize: '0.75rem' }, children: `${m.id}${m.featured ? ' · featured' : ''}` }),
            jsx('span', { style: { fontSize: '0.625rem', color: C.muted }, children: price })
          ]
        })
      }) : [small('No models match that search.')]
    })
  ], 'picker')
}

function ThisChat({ state, onToggle }) {
  if (!state.session_id) return bubble([label('This chat'), small('Open a chat to use Crystallize Chat.')], 'this')
  const saved = state.this_chat
  const status = state.can_start || state.enabled
    ? pill(state.enabled ? 'Turn off' : 'Turn on', onToggle, state.enabled ? C.muted : C.accent)
    : chip('Off', C.muted)
  if (!state.enabled && !state.can_start) {
    return bubble([row('This chat', status), small(state.why_not, C.soft)], 'this')
  }
  if (!state.enabled) {
    return bubble([row('This chat', status), small('Off for this chat. Turn it on to start saving from your next tool result.', C.soft)], 'this')
  }
  if (!saved) {
    return bubble([row('This chat', status), small('On. Savings appear after a tool returns a big result.', C.soft)], 'this')
  }
  const cells = [
    ['Removed', `${tokensText(saved.tokens_removed)} tokens`, C.green],
    ['Results shrunk', String(saved.results)],
    saved.cheap_cost ? ['Cheap model', money(saved.cheap_cost)] : null,
    state.chat_spent_usd != null ? ['Chat so far', money(state.chat_spent_usd)] : null
  ].filter(Boolean)
  return bubble([
    row('This chat', status),
    jsx('div', {
      key: 'cells',
      style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(90px, 1fr))', gap: 8 },
      children: cells.map(([title, value, color]) => jsxs('div', {
        key: title,
        children: [
          jsx('div', { style: { fontSize: '0.625rem', color: C.muted }, children: title }),
          jsx('div', { style: { fontSize: '0.8125rem', color: color || C.text, fontVariantNumeric: 'tabular-nums' }, children: value })
        ]
      }))
    }),
    saved.net_once != null ? small(`Estimated saving: about ${money(Math.max(0, saved.net_once))} on the next message alone, after the cheap model's cost. It repeats on each later message.`, C.soft) : null,
    state.chat && state.chat.calls_saveable ? small(`Repeated steps: a saved routine could skip ${state.chat.calls_saveable} tool call${state.chat.calls_saveable === 1 ? '' : 's'} in this chat.`, C.soft) : null,
    state.not_cheaper ? small('Your cheap model costs the same or more than your main model, so results are only cleaned. Pick a cheaper one on the Crystallize Chat page.', C.yellow) : null
  ], 'this')
}

function ChatSection({ ctx, sessionId }) {
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)
  const load = useCallback(() => {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
    ctx.rest(`/chat-summary${q}`)
      .then(res => { if (res && res.error) setError(res.error); else { setState(res); setError(null) } })
      .catch(() => setError("Couldn't reach the Crystallize backend. Is the plugin enabled?"))
  }, [ctx, sessionId])
  useEffect(() => {
    load()
    return ctx.setInterval(load, REFRESH_MS)
  }, [ctx, load])
  const toggle = () => {
    ctx.rest('/chat-toggle', { method: 'POST', body: { session_id: sessionId, on: !state.enabled } })
      .then(res => {
        if (res && !res.ok) host.notify({ kind: 'warning', message: res.message })
        load()
      })
      .catch(err => host.notify({ kind: 'error', message: `Crystallize: ${String(err)}` }))
  }

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 14 },
    children: [
      error ? small(error, C.soft) : null,
      !state && !error ? small('Loading…') : null,
      state ? jsx(ThisChat, { key: 'this', state, onToggle: toggle }) : null,
      state ? jsx(ModelPicker, { key: 'picker', ctx, picked: state.model, onPicked: load }) : null,
      state ? bubble([
        label('How it saves'),
        small('Your main model keeps the thinking. For routine work like summarizing or reformatting, it sends an input to your cheap model and gets the output straight back, deciding by itself when that\'s worth doing.', C.soft),
        small('A big tool result is paid for again on every later message, so results are also cleaned, and big ones are condensed.', C.soft),
        small('Your main model is never switched mid-chat, which would lose its prompt cache. File contents, errors and small results stay exact. When a result is noticeably reduced, your main model can read the original.', C.soft),
        small(state.why_not_first, C.soft)
      ], 'how') : null
    ]
  })
}

function ChatPage({ ctx }) {
  const sessionId = useValue(host.state.focusedStoredSessionId)
  return jsx('div', {
    style: { height: '100%', overflowY: 'auto' },
    children: jsx('div', {
      style: { padding: 16, maxWidth: 640, margin: '0 auto' },
      children: jsx(ChatSection, { ctx, sessionId })
    })
  })
}

// Two sections, like the pages of a website: one for scheduled jobs, one for the chat you're in.
// Plain explanations of everything on the panel.
const NOTES = [
  ['The difference', [
    ['Crystallize Cron', 'Makes scheduled jobs cheaper. Each change is tested on real runs and stays only if it proves cheaper with good answers.'],
    ['Crystallize Chat', 'Makes the chat you\'re in cheaper. You turn it on for each chat, and your main model sends routine work to a cheap model you pick.']
  ]],
  ['How Cron saves', [
    ['Make cheaper', 'Starts a trial: fewer AI calls, only the tools the job uses, cleaned tool results and lower reasoning. After a few answers it stays only if the job got cheaper.'],
    ['Routine', 'Steps a job repeats, saved so they run before the AI starts. It needs matching test runs first, and you choose when to promote it.'],
    ['Live routine', 'The routine is in use. If it fails, the AI does the whole task, and two failures in a row switch it off.'],
    ['Savings score', 'How much of your active scheduled work already runs on cheaper, proven settings.']
  ]],
  ['When nothing has changed', [
    ['If unchanged: stay quiet', 'The job\'s data is the same as last run, so no AI runs and no message goes out. That run costs nothing.'],
    ['If unchanged: repeat last answer', 'One short AI call repeats the last answer, so anyone expecting a message still gets one.'],
    ['If unchanged: run the AI', 'The job runs as normal every time, even when nothing changed.'],
    ['Run with no AI', 'For jobs that only report their data, like a status check. The routine\'s output is the result, at no cost.']
  ]],
  ['How Chat saves', [
    ['Cleaned results', 'Removes markup and repeated space from tool results with no AI. File contents, errors and small results stay exact.'],
    ['Routing', 'Your main model decides when to send routine work to your cheap model and takes the output straight back, such as summarizing or extracting data. It keeps decisions, anything that sends or changes something, and the final answer.'],
    ['Cheap model', 'Does routed tasks and condenses big tool results. Your main model doesn\'t change. If the cheap model is slow or isn\'t worth it, results are only cleaned.'],
    ['Full results', 'When a result is noticeably reduced, the exact original is kept for 7 days. Your main model can read it, or ask the cheap model about it.'],
    ['Why not from the start', 'Hermes creates the chat when your first prompt is sent, and savings come from tool results, which don\'t exist until the work starts.']
  ]],
  ['Reading the numbers', [
    ['Spent', 'What Hermes recorded as the real cost.'],
    ['At API prices', 'What a run would cost at listed prices. Used when it\'s included in a plan or has no price. It\'s an estimate, not money you paid.'],
    ['Tokens', 'The amount of text the AI read and wrote. More tokens usually cost more.'],
    ['Free runs', 'Runs where no AI was needed.']
  ]]
]

function NotesSection() {
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 14 },
    children: NOTES.map(([title, rows]) => bubble([
      label(title),
      ...rows.map(([term, text]) => jsxs('div', {
        key: term,
        style: { display: 'flex', flexDirection: 'column', gap: 1 },
        children: [
          jsx('div', { style: { fontSize: '0.75rem', color: C.text }, children: term }),
          small(text, C.soft)
        ]
      }))
    ], title))
  })
}

const SECTION_NOTES = {
  cron: 'Crystallize Cron makes scheduled jobs cheaper. It runs by itself, and each change stays only if it proves cheaper with good answers.',
  chat: 'Crystallize Chat makes the chat you\'re in cheaper. You turn it on for each chat, and your main model sends routine work to a cheap model you pick.'
}

function SectionTabs({ section, onPick }) {
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 6 },
    children: [jsx(SectionButtons, { section, onPick }), SECTION_NOTES[section] ? small(SECTION_NOTES[section], C.soft) : null]
  })
}

function SectionButtons({ section, onPick }) {
  return jsx('div', {
    role: 'tablist',
    style: { display: 'flex', gap: 2, padding: 2, borderRadius: 8, background: C.tile },
    children: [['cron', 'Cron'], ['chat', 'Chat'], ['notes', 'Notes']].map(([key, text]) => jsx('button', {
      key,
      type: 'button',
      role: 'tab',
      'aria-selected': section === key,
      onClick: () => onPick(key),
      style: {
        flex: 1, padding: '4px 8px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: '0.75rem',
        background: section === key ? C.stroke : 'none', color: section === key ? C.text : C.muted
      },
      children: text
    }))
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

    // App-wide "Promotion available" pop-up: works on any page, once per routine.
    const notified = new Set(ctx.storage.get('notified', []))
    const checkPromotions = () => {
      const prefs = ctx.storage.get('prefs', {})
      if (prefs.popups === false) return
      ctx.rest('/summary')
        .then(res => {
          let changed = false
          for (const it of (res && res.testing) || []) {
            if (!it.ready) {
              if (notified.delete(it.routine_id)) changed = true
              continue
            }
            if (notified.has(it.routine_id)) continue
            notified.add(it.routine_id)
            changed = true
            diag('promotion popup', { routine: it.routine_id })
            host.notify({
              kind: 'info',
              icon: 'ruby',
              title: 'Promotion available',
              message: `${it.name} matched ${it.matches} test runs in a row.`,
              meta: it.saving_per_week ? `Estimated saving ${money(it.saving_per_week)}/wk` : undefined,
              persistent: true,
              action: {
                label: 'Promote',
                onClick: () =>
                  ctx.rest('/promote', { method: 'POST', body: { routine_id: it.routine_id } })
                    .then(r => host.notify({ kind: r && r.ok ? 'success' : 'warning', message: (r && r.message) || 'Done.' }))
                    .catch(err => host.notify({ kind: 'error', message: `Crystallize: ${String(err)}` }))
              },
              secondaryAction: { label: 'Open Crystallize', onClick: () => { if (!isVisible()) show() } }
            })
          }
          if (changed) ctx.storage.set('notified', [...notified])
        })
        .catch(() => {})
    }
    ctx.setTimeout(checkPromotions, 4000)
    ctx.setInterval(checkPromotions, 60000)

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

    ctx.registerMany([
      { id: 'chat-page', area: ROUTES_AREA, data: { path: CHAT_PATH }, render: () => jsx(ChatPage, { ctx }) },
      { id: 'chat-nav', area: SIDEBAR_NAV_AREA, data: { path: CHAT_PATH, label: 'Crystallize Chat', codicon: 'ruby' } }
    ])

    ctx.register({
      id: 'open-chat-command',
      area: PALETTE,
      data: {
        id: 'crystallize.openChat',
        label: 'Crystallize Chat: Open',
        keywords: ['crystallize', 'chat', 'cheap', 'model', 'savings'],
        run: () => host.navigate(CHAT_PATH)
      }
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
