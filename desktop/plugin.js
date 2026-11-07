// Crystallize: Crystallize Cron (a top-bar button and panel) and Crystallize Chat (a sidebar page).
// Plain ESM, loaded uncompiled by Hermes Desktop (no JSX, no build step).
import { Codicon, ROUTES_AREA, SIDEBAR_NAV_AREA, host, useValue } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'
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
  if (typeof v !== 'number' || !Number.isFinite(v)) return 'Unknown'
  if (v < 0) return `-${money(-v)}`
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


// A request may finish after focus changes or a newer refresh completes.
function useRequestScope(scope) {
  const ref = useRef({ scope, version: 0 })
  if (ref.current.scope !== scope) ref.current = { scope, version: ref.current.version + 1 }
  useEffect(() => {
    ref.current.scope = scope
    return () => { ref.current.scope = null; ref.current.version += 1 }
  }, [scope])
  return ref
}

const fieldStyle = {
  width: '100%', boxSizing: 'border-box', padding: '5px 7px', borderRadius: 6,
  border: `0.5px solid ${C.stroke}`, background: 'none', color: C.text, fontSize: '0.75rem'
}

const field = (title, input) => jsxs('label', {
  style: { display: 'flex', flexDirection: 'column', gap: 3, fontSize: '0.6875rem', color: C.soft },
  children: [title, input]
})

const actionButton = (text, onClick, disabled = false) => jsx('button', {
  type: 'button', onClick, disabled,
  style: { ...fieldStyle, width: 'auto', cursor: disabled ? 'not-allowed' : 'pointer', color: disabled ? C.muted : C.accent },
  children: text
})

const rawDetails = (title, value) => value ? jsxs('details', {
  style: { fontSize: '0.6875rem', color: C.soft },
  children: [jsx('summary', { style: { cursor: 'pointer' }, children: title }),
    jsx('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 260, overflowY: 'auto', fontSize: '0.625rem' }, children: JSON.stringify(value, null, 2) })]
}) : null

// All completion controls use public React and the plugin REST bridge.
const panelStyle = { display: 'flex', flexDirection: 'column', gap: 7 }
const buttonsStyle = { display: 'flex', flexWrap: 'wrap', gap: 6 }
const statusLine = (text, color = C.soft) => jsx('div', { role: 'status', 'aria-live': 'polite', children: small(text, color) })
const checkField = (title, checked, onChange, disabled = false, detail = null) => jsxs('label', {
  style: { display: 'flex', alignItems: 'flex-start', gap: 7, color: disabled ? C.muted : C.soft, fontSize: '0.75rem', cursor: disabled ? 'default' : 'pointer' },
  children: [jsx('input', { type: 'checkbox', checked, disabled, onChange: e => onChange(e.target.checked), style: { margin: '2px 0 0', flexShrink: 0 } }),
    jsxs('span', { children: [title, detail ? small(detail) : null] })]
})
const sectionButton = (title, open, onClick) => jsx('button', {
  type: 'button', 'aria-expanded': open, onClick,
  style: { border: 'none', background: 'none', padding: '3px 0', textAlign: 'left', color: C.accent, cursor: 'pointer', fontSize: '0.75rem' },
  children: `${open ? '▾' : '▸'} ${title}`
})
const errorText = (res, fallback) => res && (res.message || res.error || res.why) || fallback
const blindValue = res => res && (res.comparison || res.blind || res)
const intervalText = minutes => minutes >= 1440 && minutes % 1440 === 0 ? `${minutes / 1440} day${minutes === 1440 ? '' : 's'}`
  : minutes >= 60 && minutes % 60 === 0 ? `${minutes / 60} hour${minutes === 60 ? '' : 's'}` : `${minutes} min`
const costText = cost => cost && cost.actual_usd != null ? `${money(cost.actual_usd)} recorded`
  : cost && cost.estimated_usd != null ? `${money(cost.estimated_usd)} estimated` : 'Unknown cost'
function localTimezone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' } catch { return 'UTC' }
}

// Never derive A/B identities from the original/candidate preview. The backend
// persists the randomized mapping and sends only anonymous fields until reveal.
function BlindTasteTest({ ctx, comparison: incoming, onChanged = () => {} }) {
  const id = incoming && incoming.id
  const [comparison, setComparison] = useState(incoming)
  const [pending, setPending] = useState(null)
  const [error, setError] = useState(null)
  const scope = useRequestScope(id)
  const running = useRef(false)
  useEffect(() => { setComparison(incoming); setError(null) }, [incoming])
  if (!comparison || !id) return null
  const save = (path, choice = null) => {
    if (running.current) return
    running.current = true
    setPending(choice || 'reveal')
    setError(null)
    ctx.rest(path, { method: 'POST', body: { comparison_id: id, ...(choice ? { choice } : {}) } }).then(res => {
      if (scope.current.scope !== id) return
      const next = blindValue(res)
      if (!res || res.ok === false || !next || next.id !== id) setError(errorText(res, 'Could not save this review.'))
      else { setComparison(next); onChanged(next) }
    }).catch(() => { if (scope.current.scope === id) setError('Could not save this review. Try again when the backend is available.') })
      .finally(() => { running.current = false; if (scope.current.scope === id) setPending(null) })
  }
  const choices = [['A', 'Prefer A'], ['B', 'Prefer B'], ['tie', 'About equal'], ['neither', 'Neither works']]
  const selected = choices.find(([key]) => key === comparison.choice)
  const canChoose = comparison.can_choose === true && !comparison.revealed && !comparison.choice
  const reveal = comparison.revealed === true ? comparison.reveal : null
  return jsxs('div', {
    style: { ...panelStyle, padding: 9, border: `0.5px solid ${C.stroke}`, borderRadius: 9 },
    children: [
      row('Side-by-side taste test', chip(comparison.revealed ? 'Revealed' : 'Anonymous A / B', comparison.revealed ? C.muted : C.accent)),
      small('Read both answers from the same captured input, then choose what works better for your task. A preference is your judgment; recorded checks assess specific requirements.', C.soft),
      ...(comparison.samples || []).map((sample, i) => jsxs('div', { key: sample.sample_index ?? i, style: panelStyle, children: [
        (comparison.samples || []).length > 1 ? label(`Sample ${i + 1}`) : null,
        jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 7 }, children: ['A', 'B'].map(side => {
          const answer = sample[side] || {}
          return jsxs('article', { key: side, 'aria-label': `Answer ${side}, sample ${i + 1}`, style: { minWidth: 0, border: `0.5px solid ${C.stroke}`, borderRadius: 7, padding: 8, display: 'flex', flexDirection: 'column', gap: 5 }, children: [
            jsx('strong', { style: { color: C.text, fontSize: '0.8125rem' }, children: side }),
            jsx('pre', { tabIndex: 0, 'aria-label': `Full answer ${side}, sample ${i + 1}`, style: { margin: 0, color: C.text, fontFamily: 'inherit', fontSize: '0.75rem', lineHeight: 1.55, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 360, overflowY: 'auto' }, children: answer.answer || `Answer unavailable${answer.status ? ` (${answer.status})` : ''}.` })
          ] })
        }) })
      ] })),
      selected ? statusLine(`Your choice: ${selected[1]}. ${comparison.revealed ? '' : 'Reveal when you are ready.'}`, C.green) : null,
      canChoose ? jsx('div', { style: buttonsStyle, children: choices.map(([choice, title]) => jsx('button', {
        key: choice, type: 'button', onClick: () => save('/blind-choice', choice), disabled: !!pending,
        style: { ...fieldStyle, width: 'auto', cursor: pending ? 'wait' : 'pointer', color: C.accent }, children: pending === choice ? 'Saving choice…' : title
      })) }) : null,
      !comparison.revealed ? actionButton(pending === 'reveal' ? 'Revealing…' : 'Reveal models, costs and checks', () => save('/blind-reveal'), !!pending) : null,
      !comparison.choice && !comparison.revealed ? small('Revealing before a choice records this as an unblinded review.') : null,
      comparison.revealed && !comparison.choice ? small('Revealed before a choice. This review does not count as a blind preference.', C.yellow) : null,
      comparison.notice ? small(comparison.notice) : null,
      reveal ? jsxs('div', { style: { ...panelStyle, borderTop: `0.5px solid ${C.stroke}`, paddingTop: 6 }, children: [
        label('Revealed evidence'),
        reveal.experiment_label ? small(reveal.experiment_label, C.soft) : reveal.experiment_kind ? small(String(reveal.experiment_kind).replaceAll('_', ' '), C.soft) : null,
        ...(reveal.samples || []).map((sample, i) => jsx('div', { key: sample.sample_index ?? i, style: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 7 }, children: ['A', 'B'].map(side => {
          const entry = sample[side] || {}
          const requested = entry.requested || {}
          const served = entry.answered_by || {}
          const route = [served.provider || requested.provider, served.model || requested.model].filter(Boolean).join(' / ') || 'Route identity unavailable'
          const validation = entry.validation || {}
          return jsxs('div', { key: side, style: panelStyle, children: [
            small(`${side}: ${route}`, C.text), small(costText(entry.cost), C.soft),
            small(validation.task_checks_configured === false || validation.configured === false || validation.check_count === 0 ? 'No task-specific checks configured' : validation.ok === true || validation.pass === true ? 'Recorded structural checks passed' : validation.ok === false || validation.pass === false ? 'Recorded checks failed' : 'Check outcome unavailable', validation.ok === false || validation.pass === false ? C.yellow : C.soft)
          ] })
        }) })),
        small('Answer-only evidence. A taste test does not verify tool execution or every future input.'),
        rawDetails('Full revealed receipt and audit', reveal)
      ] }) : null,
      error ? statusLine(error, C.yellow) : null
    ]
  })
}

function RhythmHeatmap({ heatmap = [], timezone }) {
  const [picked, setPicked] = useState(null)
  const [focusedCell, setFocusedCell] = useState(0)
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const cells = new Map(heatmap.map(cell => [`${cell.weekday}:${cell.hour}`, cell]))
  const max = Math.max(1, ...heatmap.map(cell => Number(cell.changes) || 0))
  const describe = (cell, day, hour) => `${days[day]} ${String(hour).padStart(2, '0')}:00: ${cell.observations || 0} observations, ${cell.comparisons || 0} comparisons, ${cell.changes || 0} changes`
  const inspect = index => {
    setFocusedCell(index)
    const day = Math.floor(index / 24), hour = index % 24
    setPicked(describe(cells.get(`${day}:${hour}`) || {}, day, hour))
  }
  const navigate = event => {
    const next = { ArrowLeft: Math.max(0, focusedCell - 1), ArrowRight: Math.min(167, focusedCell + 1),
      ArrowUp: Math.max(0, focusedCell - 24), ArrowDown: Math.min(167, focusedCell + 24),
      Home: Math.floor(focusedCell / 24) * 24, End: Math.floor(focusedCell / 24) * 24 + 23 }[event.key]
    if (next != null) { event.preventDefault(); inspect(next) }
  }
  return jsxs('div', { style: panelStyle, children: [
    small(`Recorded change activity · ${timezone || 'UTC'}`, C.soft),
    jsx('div', { style: { overflowX: 'auto' }, children: jsxs('div', { role: 'group', tabIndex: 0, 'aria-label': 'Change activity by weekday and hour. Use arrow keys to inspect cells; Home and End move within a day.', onKeyDown: navigate, onFocus: event => { if (event.target === event.currentTarget) inspect(focusedCell) }, style: { display: 'grid', gridTemplateColumns: '28px repeat(24, minmax(8px, 1fr))', gap: 2, minWidth: 286 }, children: [
      jsx('span', {}), ...Array.from({ length: 24 }, (_, hour) => jsx('span', { key: `hour-${hour}`, 'aria-hidden': true, style: { color: C.muted, fontSize: '0.5rem', textAlign: 'center' }, children: hour % 6 === 0 ? String(hour) : '' })),
      ...days.flatMap((day, index) => [jsx('span', { key: day, style: { color: C.muted, fontSize: '0.5625rem', lineHeight: '12px' }, children: day }),
        ...Array.from({ length: 24 }, (_, hour) => {
          const cell = cells.get(`${index}:${hour}`) || {}
          const observed = cell.observations > 0
          const text = describe(cell, index, hour)
          const changed = cell.changes > 0
          return jsx('button', { key: `${day}-${hour}`, type: 'button', tabIndex: -1, title: text, 'aria-label': text,
            onFocus: () => inspect(index * 24 + hour), onMouseEnter: () => inspect(index * 24 + hour), onClick: () => inspect(index * 24 + hour),
            style: { padding: 0, height: 12, minWidth: 8, border: `0.5px ${observed ? 'solid' : 'dashed'} ${C.stroke}`, borderRadius: 2, background: changed ? C.green : observed ? C.stroke : 'transparent', opacity: changed ? 0.4 + 0.6 * (cell.changes / max) : observed ? 0.65 : 0.35, cursor: 'help' } })
        })])
    ] }) }),
    statusLine(picked || 'Use arrow keys on the grid, or hover a cell, for its recorded counts.'),
    small('Dashed: no observations · Gray: observed · Green: changes. Empty cells provide no evidence about activity.')
  ] })
}

function RhythmControl({ ctx, jobId, onChanged = () => {} }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [interval, setIntervalMinutes] = useState(60)
  const [staleness, setStaleness] = useState('60')
  const [noncritical, setNoncritical] = useState(false)
  const [preview, setPreview] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState(null)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)
  const [timezone] = useState(localTimezone)
  const scope = useRequestScope(jobId)
  const running = useRef(false)
  const load = useCallback((preserveError = false) => {
    if (!open || !jobId) return
    const version = ++scope.current.version
    setPreview(null); setConfirmed(false)
    return ctx.rest(`/rhythm?job_id=${encodeURIComponent(jobId)}&timezone=${encodeURIComponent(timezone)}`).then(res => {
      if (scope.current.scope !== jobId || scope.current.version !== version) return
      if (!res || res.ok === false || res.error) { setData(null); setError(errorText(res, 'Could not load Rhythm.')); return }
      setData(res); if (!preserveError) setError(null)
      const initial = res.recommendation && res.recommendation.interval_minutes || res.current_interval_minutes || 60
      setIntervalMinutes(initial); setStaleness(String(initial))
    }).catch(() => { if (scope.current.scope === jobId && scope.current.version === version) { setData(null); setError('Could not load Rhythm. Retry when the backend is available.') } })
  }, [ctx, open, jobId, timezone])
  useEffect(() => { load(); return () => { scope.current.version += 1 } }, [load])
  const invalidate = () => { setPreview(null); setConfirmed(false); setMessage(null); scope.current.version += 1 }
  const submit = path => {
    if (running.current || !data) return
    if (path === '/rhythm' && (!preview || !preview.can_apply || !confirmed)) return
    const minutes = Number(staleness)
    if (!Number.isFinite(minutes) || minutes <= 0) { setError('Enter the maximum acceptable delay in minutes.'); return }
    running.current = true
    scope.current.version += 1
    const version = scope.current.version
    const fingerprint = preview && preview.config_fingerprint || data.config_fingerprint
    const body = { job_id: jobId, interval_minutes: interval, timezone, noncritical,
      allowed_staleness_minutes: minutes, ...(path === '/rhythm-preview' ? {} : { confirmed: true, expected_config_fingerprint: fingerprint }) }
    setPending(path); setError(null); setMessage(null)
    ctx.rest(path, { method: 'POST', body }).then(res => {
      if (scope.current.scope !== jobId || scope.current.version !== version) return
      if (!res || res.ok === false || res.error) { setError(errorText(res, 'Could not update this schedule.')); return }
      if (path === '/rhythm-preview') { setData(res); setPreview(res); setConfirmed(false) }
      else { setMessage(res.message || 'Schedule updated.'); setPreview(null); setConfirmed(false); onChanged(); return load() }
    }).catch(() => { if (scope.current.scope === jobId && scope.current.version === version) setError('Could not finish this request. Refresh the schedule before trying again.') })
      .finally(() => { running.current = false; if (scope.current.scope === jobId) setPending(null) })
  }
  const undo = () => {
    if (running.current || !data || !data.control || !data.control.can_undo) return
    running.current = true; setPending('/rhythm-undo'); setError(null)
    const version = ++scope.current.version
    ctx.rest('/rhythm-undo', { method: 'POST', body: { job_id: jobId, confirmed: true, expected_config_fingerprint: data.config_fingerprint } }).then(res => {
      if (scope.current.scope !== jobId || scope.current.version !== version) return
      if (!res || res.ok === false) { setError(errorText(res, 'Could not restore this schedule.')); load(true) }
      else { setMessage(res.message || 'Previous schedule restored.'); setPreview(null); setConfirmed(false); onChanged(); return load() }
    }).catch(() => { if (scope.current.scope === jobId && scope.current.version === version) setError('Could not restore the schedule. Refresh before trying again.') })
      .finally(() => { running.current = false; if (scope.current.scope === jobId) setPending(null) })
  }
  const choices = [...new Set([5, 10, 15, 30, 60, 120, 240, 360, 720, 1440, 10080, interval, data && data.current_interval_minutes].filter(v => Number.isFinite(v) && v > 0))].sort((a, b) => a - b)
  const projection = preview && preview.projection
  const control = data && data.control || {}
  return jsxs('div', { style: { ...panelStyle, borderTop: `0.5px solid ${C.stroke}`, paddingTop: 5 }, children: [
    sectionButton('Rhythm · schedule frequency', open, () => setOpen(v => !v)),
    ...(open ? [
      small('Use observed changes to propose how often this job runs. Fewer checks can delay new information; the projection does not predict future changes.', C.soft),
      !data && !error ? statusLine('Loading recorded activity…') : null,
      data ? jsx(RhythmHeatmap, { heatmap: data.heatmap || [], timezone: data.timezone }) : null,
      data ? small(`${data.evidence && data.evidence.complete_observations || 0} complete observations · ${data.evidence && data.evidence.comparisons || 0} comparisons · ${data.evidence && data.evidence.span_days || 0} days`, C.soft) : null,
      data && data.recommendation ? small(`Suggested: every ${intervalText(data.recommendation.interval_minutes)}. ${data.recommendation.reason || ''}`, C.accent) : data ? small('No automatic frequency suggestion for this job. Review the evidence and any restrictions below.') : null,
      ...(data && data.eligibility && data.eligibility.blockers || []).map((text, i) => jsx('div', { key: `eligibility-${i}`, children: small(text, C.yellow) })),
      data ? field(`Proposed frequency: every ${intervalText(interval)}`, jsx('input', { type: 'range', min: 0, max: choices.length - 1, step: 1, value: choices.indexOf(interval), disabled: !!pending,
        'aria-label': 'Proposed schedule frequency', 'aria-valuetext': `Every ${intervalText(interval)}`, onChange: e => { invalidate(); setIntervalMinutes(choices[Number(e.target.value)]) }, style: { width: '100%', accentColor: C.accent } })) : null,
      data && data.current_interval_minutes ? small(`Current schedule: every ${intervalText(data.current_interval_minutes)}`) : null,
      data ? checkField(data.acknowledgement || 'This job is a noncritical latest-state snapshot. It does not require every transient event, notification, deadline or action, and the maximum delay below is acceptable.', noncritical, value => { invalidate(); setNoncritical(value) }, !!pending) : null,
      data ? field('Maximum acceptable delay (minutes)', jsx('input', { type: 'number', min: 1, max: 525600, step: 1, value: staleness, disabled: !!pending, style: fieldStyle, onChange: e => { invalidate(); setStaleness(e.target.value) } })) : null,
      data ? actionButton(pending === '/rhythm-preview' ? 'Calculating projection…' : 'Preview frequency change', () => submit('/rhythm-preview'), !!pending) : null,
      projection ? jsxs('div', { style: { ...panelStyle, padding: 8, borderRadius: 7, background: C.tile }, children: [
        label(`Projection · next ${projection.horizon_days || 30} days`),
        small(`${projection.current_scheduled_invocations ?? 'Unknown'} → ${projection.proposed_scheduled_invocations ?? 'Unknown'} scheduled checks`, C.text),
        projection.avoided_scheduled_invocations != null ? small(`${Math.abs(projection.avoided_scheduled_invocations)} ${projection.avoided_scheduled_invocations < 0 ? 'more' : 'fewer'} scheduled invocations in this projection.`, C.soft) : null,
        small(`${money(projection.estimated_avoided_usd)} estimated cost reduction. Actual changes, run costs and savings may differ.`, C.soft),
        ...(preview.blockers || []).map((text, i) => jsx('div', { key: i, children: small(text, C.yellow) })),
        preview.can_apply ? checkField(`I approve running this job every ${intervalText(interval)} with up to ${staleness} minutes of delay`, confirmed, setConfirmed, !!pending) : null,
        preview.can_apply ? actionButton(pending === '/rhythm' ? 'Applying…' : 'Apply this schedule', () => submit('/rhythm'), !!pending || !confirmed) : null,
        !preview.can_apply && preview.can_export === true && preview.export && preview.export.argv ? jsxs('div', { style: panelStyle, children: [
          small('Schedule suggestion for review in Hermes. This preview has not changed the job.', C.soft),
          jsx('pre', { tabIndex: 0, 'aria-label': 'Exported schedule command', style: { ...fieldStyle, margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: '0.6875rem' }, children: preview.export.argv.map(arg => /^[A-Za-z0-9_./:=@-]+$/.test(String(arg)) ? arg : `'${String(arg).replaceAll("'", "'\\''")}'`).join(' ') })
        ] }) : null,
        control.concurrency_limit ? small(control.concurrency_limit, C.yellow) : null
      ] }) : null,
      control.reason && !control.supported ? small(control.reason, C.soft) : null,
      control.can_undo ? actionButton(pending === '/rhythm-undo' ? 'Restoring…' : 'Undo Rhythm schedule change', undo, !!pending) : null,
      data && data.evidence ? rawDetails('Activity evidence and limitations', data.evidence) : null,
      error ? jsxs('div', { style: panelStyle, children: [statusLine(error, C.yellow), actionButton('Refresh Rhythm', load, !!pending)] }) : null,
      message ? statusLine(message, C.green) : null
    ] : [])
  ] })
}

function AuctionResult({ auction, ctx }) {
  const [blind, setBlind] = useState(null)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)
  const scope = useRequestScope(auction.id)
  const status = String(auction.status || 'unknown').replaceAll('_', ' ')
  const review = id => {
    if (pending) return
    setPending(true); setError(null)
    ctx.rest(`/blind?comparison_id=${encodeURIComponent(id)}`).then(res => {
      if (scope.current.scope !== auction.id) return
      const value = blindValue(res)
      if (!res || res.ok === false || !value || !value.id) setError(errorText(res, 'Could not load the anonymous review.'))
      else setBlind(value)
    }).catch(() => { if (scope.current.scope === auction.id) setError('Could not load the anonymous review.') })
      .finally(() => { if (scope.current.scope === auction.id) setPending(false) })
  }
  return jsxs('div', { style: { ...panelStyle, border: `0.5px solid ${C.stroke}`, borderRadius: 8, padding: 8 }, children: [
    row('Auction receipt', chip(status, auction.status === 'completed' ? C.green : C.yellow)),
    auction.status === 'stopped_unknown_spend' ? statusLine('Stopped because billed spend is unknown. Calls already made may still be charged; remaining routes were skipped.', C.yellow) : null,
    auction.budget ? small(`Budget mode: ${String(auction.budget.cap_mode || auction.cap_mode || 'recorded in receipt').replaceAll('_', ' ')} · ${auction.budget.actual_spend_complete === true ? 'actual spend complete' : 'actual spend may be incomplete'}`, C.soft) : null,
    small('The scoreboard includes failed and skipped routes. Recorded checks describe specific requirements; answer quality still needs your review.', C.soft),
    jsx('div', { style: { overflowX: 'auto' }, children: jsxs('table', { 'aria-label': 'Model auction scoreboard', style: { width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.6875rem' }, children: [
      jsx('thead', { children: jsx('tr', { children: ['Candidate', 'State / checks', 'Cost'].map(title => jsx('th', { key: title, scope: 'col', style: { padding: '4px 5px', color: C.muted, fontWeight: 500, borderBottom: `0.5px solid ${C.stroke}` }, children: title })) }) }),
      jsx('tbody', { children: (auction.scoreboard || []).map((score, i) => {
        const call = (auction.calls || []).find(call => call.index === score.index) || {}
        const route = call.requested || score.requested || score
        const name = route.model || `Candidate ${(score.index ?? i) + 1}`
        const checks = score.structural_checks
        const noChecks = checks && (checks.task_checks_configured === false || checks.configured === false || checks.check_count === 0)
        const pass = checks === true || checks && !noChecks && (checks.ok === true || checks.pass === true)
        const fail = checks === false || checks && (checks.ok === false || checks.pass === false)
        return jsxs('tr', { key: score.index ?? i, children: [
          jsxs('td', { style: { padding: '6px 5px', borderBottom: `0.5px solid ${C.stroke}`, maxWidth: 150, overflowWrap: 'anywhere' }, children: [small(name, C.text), route.provider ? small(route.provider) : null] }),
          jsxs('td', { style: { padding: '6px 5px', borderBottom: `0.5px solid ${C.stroke}` }, children: [small(String(score.status || call.status || 'unknown').replaceAll('_', ' '), C.soft), small(noChecks ? 'No task checks' : pass ? 'Checks passed' : fail ? 'Checks failed' : 'Checks unavailable', fail ? C.yellow : C.muted)] }),
          jsx('td', { style: { padding: '6px 5px', borderBottom: `0.5px solid ${C.stroke}`, fontVariantNumeric: 'tabular-nums' }, children: small(costText(score.cost || call.cost), C.soft) })
        ] })
      }) })
    ] }) }),
    ...(auction.blind_pairs || []).map((pair, i) => jsx('div', { key: pair.comparison_id || i, children: actionButton(`Taste-test candidates ${(pair.left_index ?? 0) + 1} and ${(pair.right_index ?? 1) + 1}`, () => review(pair.comparison_id), pending) })),
    blind ? jsx(BlindTasteTest, { key: blind.id, ctx, comparison: blind, onChanged: setBlind }) : null,
    error ? statusLine(error, C.yellow) : null,
    rawDetails('Full auction outputs, failed calls and cost provenance', auction)
  ] })
}

function ModelAuction({ ctx, jobId, onChanged = () => {} }) {
  const [open, setOpen] = useState(false)
  const [providers, setProviders] = useState(null)
  const [inventoryMetadata, setInventoryMetadata] = useState(null)
  const [providerId, setProviderId] = useState('')
  const [search, setSearch] = useState('')
  const [models, setModels] = useState([])
  const [cap, setCap] = useState('')
  const [acceptEstimatedCap, setAcceptEstimatedCap] = useState(false)
  const [maxTokens, setMaxTokens] = useState('1024')
  const [preview, setPreview] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [auctions, setAuctions] = useState([])
  const [pending, setPending] = useState(null)
  const [error, setError] = useState(null)
  const [inventoryError, setInventoryError] = useState(null)
  const scope = useRequestScope(jobId)
  const running = useRef(false)
  const load = useCallback(() => {
    if (!open || !jobId) return
    const version = ++scope.current.version
    setPreview(null); setConfirmed(false)
    Promise.allSettled([ctx.rest('/chat-models'), ctx.rest(`/auction?job_id=${encodeURIComponent(jobId)}`)]).then(results => {
      if (scope.current.scope !== jobId || scope.current.version !== version) return
      const [inventory, history] = results.map(result => result.status === 'fulfilled' ? result.value : null)
      if (!inventory || inventory.error || inventory.ok === false) setInventoryError(errorText(inventory, 'Configured-route discovery failed.'))
      else { setProviders(inventory.providers || []); setInventoryMetadata(inventory); setInventoryError(null) }
      if (!history || history.error || history.ok === false) setError(errorText(history, 'Could not load auction history.'))
      else { setAuctions(history.auctions || []); setError(null) }
    })
  }, [ctx, open, jobId])
  useEffect(() => { load(); return () => { scope.current.version += 1 } }, [load])
  const invalidate = () => { setPreview(null); setConfirmed(false); setError(null) }
  const selected = route => models.some(model => model.provider === route.provider && model.model === route.model)
  const toggle = route => { invalidate(); setModels(old => selected(route) ? old.filter(model => model.provider !== route.provider || model.model !== route.model) : old.length < 6 ? [...old, route] : old) }
  const budget = Number(cap)
  const tokens = Number(maxTokens)
  const valid = models.length >= 2 && models.length <= 6 && Number.isFinite(budget) && budget > 0 && budget <= 100 && Number.isInteger(tokens) && tokens >= 1 && tokens <= 4096
  const request = path => {
    if (running.current || !valid) return
    if (path === '/auction' && (!preview || !preview.available || !confirmed)) return
    running.current = true; setPending(path); setError(null)
    const version = ++scope.current.version
    const body = { job_id: jobId, models, cost_cap_usd: budget, max_tokens: tokens, accept_estimated_cap: acceptEstimatedCap,
      ...(path === '/auction' ? { confirmed: true, execution_id: preview.execution_id, expected_input_sha256: preview.input_sha256 } : {}) }
    ctx.rest(path, { method: 'POST', body }).then(res => {
      if (scope.current.scope !== jobId || scope.current.version !== version) return
      if (path === '/auction-preview') {
        if (!res || res.ok === false || res.error) setError(errorText(res, 'Could not preview this auction.'))
        else { setPreview(res); setConfirmed(false) }
      } else {
        if (res && res.auction) setAuctions(old => [res.auction, ...old.filter(auction => auction.id !== res.auction.id)])
        if (!res || !res.ok) setError(errorText(res, 'Auction stopped. Review its receipt before trying again.'))
        setPreview(null); setConfirmed(false); onChanged()
      }
    }).catch(() => { if (scope.current.scope === jobId && scope.current.version === version) setError(path === '/auction' ? 'Auction connection lost. Calls may have been charged. Refresh its receipt before starting again.' : 'Could not preview this auction.') })
      .finally(() => { running.current = false; if (scope.current.scope === jobId) setPending(null) })
  }
  const provider = (providers || []).find(provider => provider.slug === providerId) || (providers || [])[0]
  const term = search.trim().toLowerCase()
  const matching = (provider && provider.models || []).filter(model => !term || model.id.toLowerCase().includes(term))
  return jsxs('div', { style: { ...panelStyle, borderTop: `0.5px solid ${C.stroke}`, paddingTop: 5 }, children: [
    sectionButton('Model auction · compare declared routes', open, () => setOpen(v => !v)),
    ...(open ? [
      small('Choose 2–6 configured or declared routes to answer the same captured input. This is a paid, answer-only comparison; review quality and spend before choosing any model for future work.', C.soft),
      small(inventoryNotice, C.soft),
      small('Budgeting requires an explicit user rate card for each exact route. Missing rates remain unknown.', C.soft),
      ...metadataWarnings(inventoryMetadata).map((message, index) => jsx('div', { key: `auction-inventory-${index}`, children: small(message, C.soft) })),
      !providers && !inventoryError ? statusLine('Loading configured and declared models…') : null,
      inventoryError ? jsxs('div', { style: panelStyle, children: [statusLine(inventoryError, C.yellow), actionButton('Retry route discovery', load, !!pending)] }) : null,
      providers && !providers.length ? small('No configured or declared model choices were returned. Add an exact provider and model route below, then refresh.', C.soft) : null,
      provider ? field('Configured or declared provider', jsx('select', { style: fieldStyle, value: provider.slug, disabled: !!pending, onChange: e => { setProviderId(e.target.value); setSearch('') }, children: providers.map(provider => jsx('option', { key: provider.slug, value: provider.slug, children: provider.name || provider.slug })) })) : null,
      provider ? field('Find a model', jsx('input', { style: fieldStyle, value: search, disabled: !!pending, onChange: e => setSearch(e.target.value), placeholder: `Search ${provider.name || provider.slug}`, type: 'search' })) : null,
      provider ? jsx('div', { role: 'group', 'aria-label': 'Declared candidate models', style: { ...panelStyle, maxHeight: 180, overflowY: 'auto', padding: 7, border: `0.5px solid ${C.stroke}`, borderRadius: 7 }, children: matching.length ? matching.slice(0, 80).map(model => {
        const route = { provider: provider.slug, model: model.id }
        const checked = selected(route)
        return jsx('div', { key: model.id, children: checkField(model.id, checked, () => toggle(route), !!pending || !checked && models.length >= 6) })
      }) : small('No models match this search.') }) : null,
      matching.length > 80 ? small(`${matching.length} matches. Refine your search to see more.`) : null,
      label(`Selected candidates · ${models.length} / 6`),
      ...models.map(route => jsx('div', { key: `${route.provider}:${route.model}`, children: row(`${route.provider} / ${route.model}`, actionButton('Remove', () => toggle(route), !!pending)) })),
      !models.length ? small('Select candidate route choices above. Add another provider or model below if needed.') : null,
      jsx(RouteEntry, { key: 'auction-route-entry', ctx, onAdded: load }),
      jsx(PricingCard, { key: 'auction-pricing', ctx, route: models[0] || null, onSaved: load }),
      jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 7 }, children: [
        field('Total cost cap (USD)', jsx('input', { type: 'number', min: 0.0001, max: 100, step: 'any', value: cap, disabled: !!pending, placeholder: 'Set a cap', style: fieldStyle, onChange: e => { invalidate(); setCap(e.target.value) } })),
        field('Max output tokens per route', jsx('input', { type: 'number', min: 1, max: 4096, step: 1, value: maxTokens, disabled: !!pending, style: fieldStyle, onChange: e => { invalidate(); setMaxTokens(e.target.value) } }))
      ] }),
      checkField('Use an estimated cap when actual billed cost is unavailable', acceptEstimatedCap, value => { invalidate(); setAcceptEstimatedCap(value) }, !!pending, 'Optional. Uses supplied exact-route prices and recorded token usage; it cannot guarantee your provider bill.'),
      small(acceptEstimatedCap ? 'Estimated-cap mode: calls may continue using supplied exact-route prices and recorded usage. Missing prices or usage stop further calls.' : 'Strict mode: unknown billed spend stops further calls. The cap reserves estimated cost before each call; it is not a provider billing limit.'),
      actionButton(pending === '/auction-preview' ? 'Checking routes and budget…' : 'Preview auction budget', () => request('/auction-preview'), !!pending || !valid),
      preview ? jsxs('div', { style: { ...panelStyle, background: C.tile, borderRadius: 7, padding: 8 }, children: [
        label('Auction preview · no calls made'),
        small(`${(preview.models || []).length} routes · ${money(preview.estimated_reservation_usd)} estimated reservation · ${money(preview.cost_cap_usd)} cap`, C.text),
        preview.cap_mode ? small(`Budget mode: ${String(preview.cap_mode).replaceAll('_', ' ')}`, C.soft) : null,
        preview.cap_notice ? small(preview.cap_notice, C.soft) : null,
        !preview.available ? small(preview.why || 'This auction cannot start with the selected routes and budget.', C.yellow) : null,
        preview.available ? checkField(`I approve paid calls for these ${models.length} routes within the ${money(budget)} estimated budget`, confirmed, setConfirmed, !!pending) : null,
        preview.available ? actionButton(pending === '/auction' ? 'Running paid comparison…' : 'Run paid model auction', () => request('/auction'), !!pending || !confirmed) : null,
        rawDetails('Budget assumptions and input fingerprint', preview)
      ] }) : null,
      pending === '/auction' ? statusLine('Paid calls are in progress. A failed or interrupted call may still incur cost. The receipt retains its state.', C.soft) : null,
      error ? statusLine(error, C.yellow) : null,
      auctions.length ? label('Recorded auctions') : null,
      ...auctions.slice(0, 5).map(auction => jsx(AuctionResult, { key: auction.id, auction, ctx })),
      actionButton('Refresh auction receipts', load, !!pending)
    ] : [])
  ] })
}

function RereadControls({ ctx, sessionId, chatEnabled }) {
  const [data, setData] = useState(null)
  const [draft, setDraft] = useState(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)
  const scope = useRequestScope(sessionId)
  const running = useRef(false)
  const load = useCallback(() => {
    const version = ++scope.current.version
    return ctx.rest(`/reread?session_id=${encodeURIComponent(sessionId)}`).then(res => {
      if (scope.current.scope !== sessionId || scope.current.version !== version) return
      if (!res || res.ok === false || res.error || !res.policy) { setError(errorText(res, 'Could not load re-read controls.')); return }
      setData(res); setDraft(res.policy); setError(null)
    }).catch(() => { if (scope.current.scope === sessionId && scope.current.version === version) setError('Could not load re-read controls.') })
  }, [ctx, sessionId])
  useEffect(() => { load(); return () => { scope.current.version += 1 } }, [load])
  const edit = (key, value) => { setDraft(old => ({ ...old, [key]: value })); setMessage(null) }
  const save = () => {
    if (running.current || !draft) return
    running.current = true; setPending(true); setError(null)
    const version = ++scope.current.version
    const body = { session_id: sessionId, enabled: !!draft.enabled, writes: !!draft.writes, results: !!draft.results, batching: !!draft.batching }
    ctx.rest('/reread', { method: 'POST', body }).then(res => {
      if (scope.current.scope !== sessionId || scope.current.version !== version) return
      if (!res || !res.ok || !res.policy) setError(errorText(res, 'Could not save re-read controls.'))
      else { setData(res); setDraft(res.policy); setMessage('Re-read choices saved for this chat.'); return load() }
    }).catch(() => { if (scope.current.scope === sessionId && scope.current.version === version) setError('Could not save re-read controls.') })
      .finally(() => { running.current = false; if (scope.current.scope === sessionId) setPending(false) })
  }
  const dirty = draft && data && ['enabled', 'writes', 'results', 'batching'].some(key => !!draft[key] !== !!data.policy[key])
  const summary = data && (data.summary || data)
  return bubble([
    row('Re-read controls', chip('This chat', C.muted)),
    small('Opt in to smaller, recoverable views of newly repeated text. Existing history stays fixed; reductions apply only when the cost estimate pays back.', C.soft),
    small('Main model and reasoning stay unchanged. Reasoning text is preserved in full.', C.text),
    !chatEnabled ? small('These choices take effect after you turn on Crystallize Chat for this chat.') : null,
    !draft && !error ? statusLine('Loading re-read choices…') : null,
    ...(draft ? [
      checkField('Enable re-read savings for this chat', !!draft.enabled, value => edit('enabled', value), pending),
      checkField('Completed file-write payloads', !!draft.writes, value => edit('writes', value), pending, 'Keep recoverable excerpts when completed write payloads are read again. Future file reads stay exact.'),
      checkField('Eligible text tool results', !!draft.results, value => edit('results', value), pending, 'Use recoverable excerpts for eligible repeated text. File reads, errors, small results and first reads stay exact.'),
      checkField('Suggest batching independent reads', !!draft.batching, value => edit('batching', value), pending, 'Adds guidance for future independent reads; dependencies and approvals still apply.'),
      !draft.enabled ? small('Re-read savings are off. Individual choices are saved but inactive.') : !draft.writes && !draft.results && !draft.batching ? small('Choose at least one option to make this opt-in useful.') : null,
      actionButton(pending ? 'Saving…' : 'Save re-read choices', save, pending || !dirty),
      summary && summary.active_projections != null ? small(`${summary.active_projections} active recoverable projections · ${tokensText(summary.estimated_avoided_input_tokens || 0)} estimated input tokens avoided per read`, C.soft) : null,
      rawDetails('Re-read evidence and recovery details', summary && { projected_messages: summary.projected_messages, active_projections: summary.active_projections, expired_projections: summary.expired_projections, reduced_chars: summary.reduced_chars, estimated_avoided_input_tokens: summary.estimated_avoided_input_tokens, estimate_scope: summary.estimate_scope, observed_requests: summary.observed_requests, recovery: summary.recovery })
    ] : []),
    error ? jsxs('div', { style: panelStyle, children: [statusLine(error, C.yellow), actionButton('Retry re-read controls', load, pending)] }) : null,
    message ? statusLine(message, C.green) : null
  ].filter(Boolean), 'reread')
}

// Delegation consent is deliberately separate from the chat's local controls.
function DelegationControls({ ctx, picked }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [enabled, setEnabled] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)
  const scope = useRequestScope('delegation')
  const running = useRef(false)
  const provider = picked && picked.provider
  const model = picked && picked.model
  useEffect(() => { setConfirmed(false); setMessage(null) }, [provider, model])
  const load = useCallback(() => {
    if (!open) return
    const version = ++scope.current.version
    ctx.rest('/delegation').then(res => {
      if (scope.current.scope !== 'delegation' || scope.current.version !== version) return
      if (!res || res.ok === false || res.error) { setError(errorText(res, 'Could not load delegation consent.')); return }
      setData(res); setEnabled(res.enabled === true); setConfirmed(false); setError(null)
    }).catch(() => { if (scope.current.scope === 'delegation' && scope.current.version === version) setError('Could not load delegation consent.') })
  }, [ctx, open])
  useEffect(() => { load(); return () => { scope.current.version += 1 } }, [load])
  const save = () => {
    if (running.current || !confirmed || enabled && (!provider || !model)) return
    running.current = true; setPending(true); setError(null)
    const version = ++scope.current.version
    ctx.rest('/delegation', { method: 'POST', body: { enabled, provider: provider || data && data.provider || '', model: model || data && data.model || '', confirmed: true } }).then(res => {
      if (scope.current.scope !== 'delegation' || scope.current.version !== version) return
      if (!res || !res.ok) setError(errorText(res, 'Could not save delegation consent.'))
      else { setData(res); setEnabled(res.enabled === true); setConfirmed(false); setMessage('Profile consent saved. Choose the delegation backend in Hermes separately when you want to use it.') }
    }).catch(() => { if (scope.current.scope === 'delegation' && scope.current.version === version) setError('Could not save delegation consent.') })
      .finally(() => { running.current = false; if (scope.current.scope === 'delegation') setPending(false) })
  }
  return bubble([
    sectionButton('Optional delegation backend', open, () => setOpen(v => !v)),
    ...(open ? [
      chip('Profile-wide consent', C.yellow),
      small('This separate setting permits a helper route for explicit delegated subtasks across this Hermes profile. Each task must request the helper. Regular tasks inherit their configured route and reasoning.', C.soft),
      !data && !error ? statusLine('Loading profile consent…') : null,
      data ? small(data.enabled ? `Saved helper: ${data.provider} / ${data.model}` : 'Profile helper consent is off.', C.soft) : null,
      provider && model ? small(`Selected helper to save: ${provider} / ${model}`, C.text) : small('Choose a configured helper route above before enabling this option. Hermes verifies authentication on an explicit call.'),
      data ? checkField('Permit this selected helper for explicit delegated subtasks', enabled, value => { setEnabled(value); setConfirmed(false); setMessage(null) }, pending || !model && !data.enabled) : null,
      data ? checkField('I approve saving this profile-wide delegation consent', confirmed, setConfirmed, pending) : null,
      data ? actionButton(pending ? 'Saving…' : 'Save profile delegation consent', save, pending || !confirmed || enabled && (!provider || !model)) : null,
      small('After saving, select /delegation crystallize yourself in Hermes for the sessions where you want it. Saving consent does not select the backend.'),
      small('Use the helper for a complete, self-contained subtask when its quality and cost fit. A model switch is compared separately from efficiency savings.'),
      error ? jsxs('div', { style: panelStyle, children: [statusLine(error, C.yellow), actionButton('Retry profile consent', load, pending)] }) : null,
      message ? statusLine(message, C.green) : null
    ] : [])
  ].filter(Boolean), 'delegation')
}

// Public metadata describes configured choices, never credential verification.
const inventoryNotice = 'Choices come from configured and user-declared routes. The public SDK does not expose a verified authenticated-model inventory; Hermes checks authentication when an explicit model call runs.'
const routeLabel = route => route && [route.provider, route.model].filter(Boolean).join(' / ') || 'No route selected'
const metadataWarnings = inventory => Array.isArray(inventory && inventory.warnings) ? inventory.warnings.filter(value => typeof value === 'string') : []

function RouteEntry({ ctx, onAdded = () => {} }) {
  const [open, setOpen] = useState(false)
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)
  const scope = useRequestScope('route-entry')
  const running = useRef(false)
  const save = () => {
    if (running.current || !provider.trim() || !model.trim()) return
    running.current = true; setPending(true); setError(null); setMessage(null)
    ctx.rest('/model-route', { method: 'POST', body: { provider: provider.trim(), model: model.trim() } }).then(res => {
      if (scope.current.scope !== 'route-entry') return
      if (!res || !res.ok) setError(errorText(res, 'Could not add this route choice.'))
      else { setMessage('Route choice added. Select it separately when you want to use it as the helper. Authentication is checked by Hermes on an explicit call.'); onAdded(res) }
    }).catch(() => { if (scope.current.scope === 'route-entry') setError('Could not add this route choice.') })
      .finally(() => { running.current = false; if (scope.current.scope === 'route-entry') setPending(false) })
  }
  const edit = (setter, value) => { setter(value); setMessage(null); setError(null) }
  return jsxs('div', { style: panelStyle, children: [
    sectionButton('Add a provider / model route', open, () => setOpen(value => !value)),
    ...(open ? [
      small('Enter the exact identifiers accepted by your Hermes setup. This adds a choice for this profile; selecting a helper is a separate action. Credentials stay with Hermes.', C.soft),
      field('Provider identifier', jsx('input', { style: fieldStyle, type: 'text', value: provider, maxLength: 200, autoComplete: 'off', spellCheck: false, disabled: pending, onChange: event => edit(setProvider, event.target.value) })),
      field('Model identifier', jsx('input', { style: fieldStyle, type: 'text', value: model, maxLength: 400, autoComplete: 'off', spellCheck: false, disabled: pending, onChange: event => edit(setModel, event.target.value) })),
      actionButton(pending ? 'Adding route choice…' : 'Add route choice', save, pending || !provider.trim() || !model.trim()),
      error ? statusLine(error, C.yellow) : null,
      message ? statusLine(message, C.green) : null
    ] : [])
  ] })
}

const newRateCard = route => ({ schema_version: 1, provider: route && route.provider || '', model: route && route.model || '', base_url: null,
  currency: 'USD', unit: 'per_million_tokens', input: null, output: null, cache_read: null, cache_write: null, request_cost: null, pricing_version: null, source_url: null })
function PricingCard({ ctx, route, onSaved = () => {} }) {
  const [open, setOpen] = useState(false)
  const [cards, setCards] = useState(null)
  const [draft, setDraft] = useState(() => JSON.stringify(newRateCard(route), null, 2))
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)
  const scope = useRequestScope('pricing-card')
  const running = useRef(false)
  const load = useCallback(() => {
    if (!open) return
    const version = ++scope.current.version
    ctx.rest('/pricing-cards').then(res => {
      if (scope.current.scope !== 'pricing-card' || scope.current.version !== version) return
      if (!res || !res.ok || !Array.isArray(res.cards)) setError(errorText(res, 'Could not load saved rate cards.'))
      else { setCards(res.cards); setError(null) }
    }).catch(() => { if (scope.current.scope === 'pricing-card' && scope.current.version === version) setError('Could not load saved rate cards.') })
  }, [ctx, open])
  useEffect(() => { load(); return () => { scope.current.version += 1 } }, [load])
  const card = (() => { try { const value = JSON.parse(draft); return value && typeof value === 'object' && !Array.isArray(value) ? value : null } catch { return null } })()
  const edit = value => { setDraft(value); setConfirmed(false); setError(null); setMessage(null) }
  const patch = value => { if (card) edit(JSON.stringify({ ...card, ...value }, null, 2)) }
  const rate = key => card && card[key] != null ? card[key] : ''
  const changeRate = (key, value) => patch({ [key]: value === '' ? null : Number(value) })
  const valid = card && typeof card.provider === 'string' && card.provider.trim() && typeof card.model === 'string' && card.model.trim()
  const save = () => {
    if (running.current || !valid || !confirmed) return
    running.current = true; setPending(true); setError(null); setMessage(null)
    const version = ++scope.current.version
    ctx.rest('/pricing-card', { method: 'POST', body: { card, confirmed: true } }).then(res => {
      if (scope.current.scope !== 'pricing-card' || scope.current.version !== version) return
      if (!res || !res.ok) setError(errorText(res, 'Could not save this user rate card.'))
      else { setConfirmed(false); setMessage('User rate card saved for this exact route. Future cost estimates use these supplied values; recorded bills remain separate.'); load(); onSaved(res) }
    }).catch(() => { if (scope.current.scope === 'pricing-card' && scope.current.version === version) setError('Could not save this user rate card.') })
      .finally(() => { running.current = false; if (scope.current.scope === 'pricing-card') setPending(false) })
  }
  const per = card && card.unit === 'per_token' ? 'token' : 'million tokens'
  return jsxs('div', { style: panelStyle, children: [
    sectionButton('User-supplied pricing card', open, () => setOpen(value => !value)),
    ...(open ? [
      small('Enter rates you obtained for this exact billing route. This profile’s cost estimates use saved cards. Prices are user-supplied estimates. Blank or null values remain unknown; enter 0 only when you know the price is zero.', C.soft),
      cards && cards.length ? field('Load a saved rate card', jsx('select', { style: fieldStyle, value: '', disabled: pending, onChange: event => { const value = cards[Number(event.target.value)]; if (value) edit(JSON.stringify(value, null, 2)) }, children: [jsx('option', { value: '', children: 'Choose a saved card…' }), ...cards.map((saved, index) => jsx('option', { key: `${saved.provider}:${saved.model}:${saved.base_url || ''}`, value: index, children: `${routeLabel(saved)}${saved.base_url ? ` · ${saved.base_url}` : ''}` }))] })) : null,
      card ? jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 7 }, children: [
        field('Rate-card provider', jsx('input', { style: fieldStyle, value: card.provider || '', maxLength: 200, autoComplete: 'off', spellCheck: false, disabled: pending, onChange: event => patch({ provider: event.target.value }) })),
        field('Rate-card model', jsx('input', { style: fieldStyle, value: card.model || '', maxLength: 400, autoComplete: 'off', spellCheck: false, disabled: pending, onChange: event => patch({ model: event.target.value }) }))
      ] }) : null,
      card ? field('Token rate units (USD)', jsx('select', { style: fieldStyle, value: card.unit || 'per_million_tokens', disabled: pending, onChange: event => patch({ unit: event.target.value }), children: [jsx('option', { value: 'per_million_tokens', children: 'USD per million tokens' }), jsx('option', { value: 'per_token', children: 'USD per token' })] })) : null,
      card ? jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 7 }, children: [
        field(`Input USD per ${per}`, jsx('input', { type: 'number', min: 0, step: 'any', value: rate('input'), disabled: pending, style: fieldStyle, placeholder: 'Unknown', onChange: event => changeRate('input', event.target.value) })),
        field(`Output USD per ${per}`, jsx('input', { type: 'number', min: 0, step: 'any', value: rate('output'), disabled: pending, style: fieldStyle, placeholder: 'Unknown', onChange: event => changeRate('output', event.target.value) }))
      ] }) : null,
      card ? field('Fixed fee (USD per request)', jsx('input', { type: 'number', min: 0, step: 'any', value: rate('request_cost'), disabled: pending, style: fieldStyle, placeholder: 'Unknown', onChange: event => changeRate('request_cost', event.target.value) })) : null,
      card ? checkField('I know this rate card has no volume tiers', card.tiers_known !== false && Object.prototype.hasOwnProperty.call(card, 'tier_threshold_tokens') && card.tier_threshold_tokens === null, value => { if (value) patch({ tier_threshold_tokens: null, tiers_known: true }); else { const next = { ...card, tiers_known: false }; delete next.tier_threshold_tokens; edit(JSON.stringify(next, null, 2)) } }, pending, 'Leave unchecked when tier rules are unknown. Use the advanced JSON for a known threshold and above-threshold rates.') : null,
      jsxs('details', { children: [jsx('summary', { style: { fontSize: '0.6875rem', color: C.soft, cursor: 'pointer' }, children: 'Advanced rate card (JSON)' }),
        small('Cache rates, volume tiers, base_url, source_url and pricing_version can be entered here. Credentials are not part of a pricing card.'),
        field('Rate-card JSON', jsx('textarea', { rows: 11, maxLength: 16000, value: draft, disabled: pending, onChange: event => edit(event.target.value), style: { ...fieldStyle, marginTop: 5, fontFamily: 'monospace' } }))] }),
      !card ? statusLine('Use one JSON object for the rate card.', C.yellow) : null,
      checkField('I confirm these are human-provided rates for this exact route and should be used only as estimates', confirmed, setConfirmed, pending),
      actionButton(pending ? 'Saving rate card…' : 'Save user rate card', save, pending || !valid || !confirmed),
      error ? jsxs('div', { style: panelStyle, children: [statusLine(error, C.yellow), actionButton('Reload saved rate cards', load, pending)] }) : null,
      message ? statusLine(message, C.green) : null
    ] : [])
  ] })
}

function useCompatibility(ctx) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const scope = useRequestScope('host-capabilities')
  const load = useCallback(() => {
    const version = ++scope.current.version
    ctx.rest('/compatibility').then(res => {
      if (scope.current.scope !== 'host-capabilities' || scope.current.version !== version) return
      if (!res || res.ok === false || res.error) { setData(null); setError(errorText(res, 'Could not read host capabilities.')); return }
      setData(res); setError(null)
    }).catch(() => { if (scope.current.scope === 'host-capabilities' && scope.current.version === version) { setData(null); setError('Could not read host capabilities.') } })
  }, [ctx])
  useEffect(() => { load(); return () => { scope.current.version += 1 } }, [load])
  return { data, error, load }
}
const dispatchCapability = report => report && (report.dispatch || report.dispatch_support) || {}
const guardedReplay = report => {
  const dispatch = dispatchCapability(report)
  return dispatch.guarded_dispatch_available === true && dispatch.automatic_nested_tools === true
}
function CompatibilityCard({ state }) {
  const [open, setOpen] = useState(false)
  const report = state.data
  const dispatch = dispatchCapability(report)
  const guarded = guardedReplay(report)
  return bubble([
    row('What this install supports', chip(!report ? state.error ? 'Unavailable' : 'Checking' : guarded ? 'Automatic steps' : 'Normal Hermes tools', report ? C.soft : C.muted)),
    !report ? statusLine(state.error || 'Checking supported execution paths…', state.error ? C.yellow : C.soft) : small(guarded ? 'The host reports guarded nested-tool execution. Each routine still needs its own readiness checks.' : 'Tool reads and writes use Hermes’s regular tool flow. Automatic pre-run replay and zero-AI execution need a verified guarded-dispatch capability.', C.soft),
    sectionButton('Capability details', open, () => setOpen(value => !value)),
    ...(open && report ? [
      dispatch.reason ? small(dispatch.reason, C.soft) : null,
      small(`File inputs: ${dispatch.input_files_mode === 'normal_host_tool_plan' ? 'explicit host-tool read plan' : 'host capability required'} · Saved output: ${dispatch.save_to_mode === 'normal_host_tool_plan' ? 'explicit host-tool write plan' : 'host capability required'}`),
      small('Model and pricing choices are declarations until checked on the actual route. Compatibility details describe support, not a paid-call test.'),
      report.output_delivery && report.output_delivery.public_buffering_supported ? small('This host buffers replies before expanding helper placeholders. Streaming may wait for the final reply while this plugin is loaded.') : null,
      rawDetails('Public capability report', report)
    ] : []),
    state.error ? actionButton('Retry capability check', state.load) : null
  ].filter(Boolean), 'host-capabilities')
}

const promotionPolicy = item => item && item.promotion_availability || {}
const modePolicy = (item, mode) => promotionPolicy(item).modes && promotionPolicy(item).modes[mode] || {}
const canUseMode = (item, mode, compatibility) => modePolicy(item, mode).available === true && (mode === 'tool' || guardedReplay(compatibility))
const unavailableMode = (item, mode) => modePolicy(item, mode).reason || promotionPolicy(item).reason || 'Readiness and the required public host capability have not been verified for this mode.'
const promotionTarget = (item, compatibility) => !item || !item.ready ? null : canUseMode(item, 'prerun', compatibility) ? { label: 'Promote', mode: 'prerun' } : canUseMode(item, 'tool', compatibility) ? { label: 'Use host-tool plan', mode: 'tool' } : null

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

const perWeek = v => (v != null ? `${money(v)}/wk` : 'Unknown')
const perMonth = v => (v != null ? `≈ ${money(v * 4.33)} a month` : '')

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
  // An unpriced run is visibly unknown; an observed zero remains a real zero.
  const price = t => t.cost_usd ?? t.api_cost_usd
  const priced = timeline.some(t => price(t) != null)
  const size = t => priced ? price(t) : t.tokens
  const max = Math.max(...timeline.map(t => size(t) ?? 0), 1e-9)
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
      jsx('div', { style: { fontSize: '0.8125rem', color: C.text, fontVariantNumeric: 'tabular-nums' }, children: t.cost_usd != null ? money(t.cost_usd) : t.api_cost_usd != null ? `${money(t.api_cost_usd)} at API prices` : 'Cost unknown' }),
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
      priced && timeline.some(t => price(t) == null) ? small('Striped bars: cost unknown') : !priced ? small('Bar height: tokens; costs unknown') : null,
      jsx('div', {
        style: { display: 'flex', alignItems: 'flex-end', gap: 2, height: 32 },
        children: timeline.map((r, i) =>
          jsx('button', {
            key: i,
            type: 'button',
            'aria-label': `${when(r.ts)}: ${price(r) == null ? 'cost unknown' : money(price(r))}${r.after ? ', routine' : ', full run'}`,
            'aria-pressed': hover === i,
            onFocus: () => setHover(i),
            onBlur: () => setHover(null),
            onMouseEnter: () => setHover(i),
            onClick: () => setHover(h => (h === i ? null : i)),
            style: {
              flex: 1, borderRadius: 2, minWidth: 0, padding: 0, minHeight: 3, cursor: 'pointer',
              height: size(r) == null ? '100%' : `${Math.max(8, Math.round((size(r) / max) * 100))}%`,
              background: size(r) == null ? `repeating-linear-gradient(135deg, transparent, transparent 3px, ${C.stroke} 3px, ${C.stroke} 5px)` : r.skipped ? 'transparent' : r.after ? C.green : C.stroke,
              border: size(r) == null ? `1px dashed ${C.muted}` : r.skipped ? `1px dashed ${C.green}` : 'none', boxSizing: 'border-box',
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
  const plan = before.cost_usd == null && before.api_cost_usd != null
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
  if (item.mode === 'tool') return 'The learned read plan guides the main agent. Hermes executes each tool through its normal permissions and tool flow; your main model and reasoning stay configured.'
  if (promotionPolicy(item).automatic_replay_available !== true) return 'Automatic replay is unavailable on this host. Use the explicit host-tool plan or normal Hermes execution; tool permissions and approvals stay with Hermes.'
  const what = n === 1 ? 'the fetch step' : `${n} fetch steps`
  if (status === 'live' && item.mode === 'no_ai') return `The verified routine runs ${what} and returns its output in the approved no-AI mode.`
  return `The verified routine runs ${what} before the AI writes the result. Review the capability report and per-job requirements.`
}

// Every execution mode needs its explicit public capability and per-job readiness.
function LiveOptions({ item, act, compatibility }) {
  const id = { routine_id: item.routine_id }
  const opts = []
  const prerun = canUseMode(item, 'prerun', compatibility)
  const tool = canUseMode(item, 'tool', compatibility)
  const noAI = canUseMode(item, 'no_ai', compatibility)
  const unchangedAvailable = item.mode === 'prerun' && prerun
  const savedSkip = ['repeat', 'quiet', 'auto'].includes(item.when_unchanged)
  const savedSkipLabel = { repeat: 'repeat last answer', quiet: 'stay quiet', auto: 'automatic skip' }[item.when_unchanged]
  if (item.mode !== 'tool' && tool) opts.push(pill('Use host-tool plan', () => act('/mode', { ...id, mode: 'tool' }), C.accent))
  if (item.mode === 'tool' && prerun) opts.push(pill('Use verified pre-run', () => act('/mode', { ...id, mode: 'prerun' }), C.accent))
  if (unchangedAvailable) {
    const next = { repeat: 'quiet', quiet: 'run', run: 'repeat' }[item.when_unchanged] || 'repeat'
    const words = { repeat: 'repeat last answer', quiet: 'stay quiet', run: 'run the AI' }
    opts.push(pill(`If unchanged: ${words[item.when_unchanged] || words.repeat}`, () => act('/skip', { ...id, when: next }), C.soft))
  }
  if (!unchangedAvailable && savedSkip) opts.push(pill('Run always / clear saved skip', () => act('/skip', { ...id, when: 'run' }), C.accent))
  if (item.mode === 'no_ai' && prerun) opts.push(pill('Let AI write it', () => act('/mode', { ...id, mode: 'prerun' }), C.muted))
  if (item.mode !== 'no_ai' && item.no_ai_fit && noAI) opts.push(pill('Run with no AI', () => act('/mode', { ...id, mode: 'no_ai' }), C.accent))
  return jsxs('div', {
    key: 'opts', style: { display: 'flex', flexDirection: 'column', gap: 4, marginTop: 2 }, children: [
      opts.length ? jsx('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 6 }, children: opts }) : null,
      !unchangedAvailable && savedSkip ? small(`Saved unchanged-input choice “${savedSkipLabel}” is unavailable in this mode. Clear it to keep the normal scheduled execution policy.`, C.yellow) : null,
      item.mode === 'tool' ? small('Host-tool plan active. The main agent executes its tools normally.', C.soft) : null
    ]
  })
}

function JobBubble({ item, status, trialRuns, act, exp, ctx, compatibility }) {
  const tone = status === 'live' ? C.green : status === 'testing' ? C.yellow : C.accent
  const projectionAvailable = guardedReplay(compatibility) && promotionPolicy(item).automatic_replay_available === true && item.projection && item.projection.status === 'hypothetical'
  let headline, sub, action
  if (status === 'live') {
    const measured = item.after && item.after.runs
    const plan = item.plan
    const limited = item.cron && item.cron.runs_total
    const week = plan ? plan.per_week : item.saving_per_week
    const total = plan ? plan.saved : item.saved_usd
    headline = !measured ? big('Measuring…', C.soft)
      : limited ? big(money(total), C.soft)
        : big(perWeek(week), C.soft)
    const skipped = (item.after && item.after.skipped) || 0
    sub = measured
      ? ['Historical comparison estimate', limited ? null : perMonth(week), limited ? null : `${money(total)} reduction before overhead`,
        `over ${item.after.runs} runs`, plan ? (plan.included ? 'at API prices, included in your plan' : 'estimated at API prices') : null,
        skipped ? `${skipped} skipped, nothing changed` : null].filter(Boolean).join(' · ')
      : 'Historical comparisons appear after its first live run; qualified totals are in the receipt.'
    action = pill('Undo', () => act('/undo', { routine_id: item.routine_id }), C.muted)
  } else if (status === 'testing') {
    headline = projectionAvailable ? big(perWeek(item.saving_per_week)) : big('Needs measurement', C.soft)
    sub = projectionAvailable ? `Unmeasured automatic-replay projection · ${perMonth(item.saving_per_week)}` : (item.skip_result ? item.skip_result.message : item.skip_fit ? item.skip_fit.message : 'No saving measured yet.')
    const target = promotionTarget(item, compatibility)
    action = item.ready
      ? target ? pill(target.label, () => act('/promote', { routine_id: item.routine_id, mode: target.mode }), C.accent) : chip('Capability unavailable', C.muted)
      : jsx('span', { style: { fontSize: '0.6875rem', color: C.muted }, children: `${item.matches} of ${item.needed} observations` })
  } else {
    headline = projectionAvailable ? big(perWeek(item.saving_per_week)) : big('Needs measurement', C.soft)
    sub = projectionAvailable ? `Unmeasured automatic-replay projection · ${perMonth(item.saving_per_week)}` : (item.skip_result ? item.skip_result.message : item.skip_fit ? item.skip_fit.message : 'No saving measured yet.')
    action = promotionPolicy(item).try_available === true
      ? pill('Observe next runs', () => act('/try', { job_id: item.job_id }))
      : chip('Still watching', C.muted)
  }
  const open = isOpen(exp, item.job_id, item.cron)
  const running = item.cron && item.cron.status === 'running'
  const header = jsx(JobHeader, {
    item, open, toggle: exp.toggle, action, hide: exp.hide,
    tag: chip(running ? 'Running' : status === 'live' ? (item.mode === 'tool' ? 'Host-tool plan' : 'Live routine') : status === 'testing' ? (item.ready ? 'Ready' : 'Observing') : 'Spotted', tone)
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
    status === 'live' ? jsx(LiveOptions, { key: 'o', item, act, compatibility }) : null,
    status !== 'live' && promotionPolicy(item).test_execution === 'observe_normal_host_runs' ? small('Learns from fresh, normally executed Hermes runs. Background tool replay is unavailable on this host.', C.soft) : null,
    status === 'testing' && item.ready && !canUseMode(item, 'prerun', compatibility) ? small(`Automatic Promote unavailable: ${unavailableMode(item, 'prerun')}`, C.soft) : null,
    item.schedule_hint ? small(item.schedule_hint, C.soft) : null,
    jsx(SavingsMap, { key: 'm', item }),
    jsx(TuneRow, { key: 't', tune: item.tune, trialRuns, act }),
    jsx(JobEvidence, { key: 'evidence', item, ctx, onChanged: () => act(null) }),
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
        label('Jobs on lower-cost settings'),
        jsxs('div', {
          style: { fontSize: '1.75rem', lineHeight: 1, color: C.text, fontVariantNumeric: 'tabular-nums' },
          children: [data.score === null ? '—' : data.score, jsx('span', { style: { fontSize: '0.6875rem', color: C.muted }, children: '/100' })]
        })
      ]
    }),
    scoreBar,
    small(data.score === null
      ? 'No scheduled jobs yet. Create one with hermes cron create, and after a few runs it appears here with what it costs. To lower the cost of a chat, turn it on in the Chat tab.'
      : 'How much of your scheduled work uses lower-cost settings. Each job shows what its trial found.'),
    jsxs('div', {
      key: 'stats',
      style: { display: 'flex', gap: 8, marginTop: 4 },
      children: [
        stat('Proven saving', money(data.qualified_efficiency_savings_usd), C.soft),
        stat('After costs', money(data.qualified_net_payback_usd), C.soft),
        stat('Could save (estimate)', perWeek(data.could_save_usd_per_week))
      ]
    }),
    small('A saving only counts when runs used the same model, provider and reasoning, so thinking less never shows as a saving. Estimates are projections, not bills.', C.soft),
    ready > 0 ? jsx('div', { key: 'all', children: pill(`Try lower-cost settings on ${ready === 1 ? '1 job' : `all ${ready} jobs`}`, () => act('/tune-all', {}), C.accent) }) : null,
    jsx(Periods, { key: 'periods', periods: data.periods })
  ], 'hero')
}

// Runaway Brake: a limit for each job, worked out from its own recent runs.
function RunawayBrake({ ctx, act, refresh }) {
  const [data, setData] = useState(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    ctx.rest('/brake').then(res => setData(res && !res.error ? res : null)).catch(() => setData(null))
  }, [ctx, refresh])
  if (!data) return null
  const limited = data.jobs.filter(j => j.limit)
  const toggle = () => {
    act('/brake', { on: !data.on })
    setData({ ...data, on: !data.on })
  }
  return bubble([
    row('Runaway Brake', pill(data.on ? 'Turn off' : 'Turn on', toggle, data.on ? C.muted : C.accent)),
    small(data.on
      ? `On. If a run goes far past what its job normally uses, it's told to stop using tools and answer with what it has. ${limited.length} of ${data.jobs.length} jobs have a limit.`
      : 'Off. When on, a run that goes far past what its job normally uses is told to stop using tools and answer with what it has.', C.soft),
    ...data.stops.slice(0, 3).map(s => small(`Stopped ${s.job_name} at ${tokensText(s.used)} tokens. It normally uses about ${tokensText(s.normal)}.`, C.yellow)),
    data.jobs.length ? jsx('div', { key: 'more', children: pill(open ? 'Hide limits' : 'Show limits', () => setOpen(!open), C.muted) }) : null,
    ...(open ? data.jobs.map(j => jsxs('div', {
      key: j.name,
      style: { display: 'flex', flexDirection: 'column', gap: 1 },
      children: [
        jsx('div', { style: { fontSize: '0.75rem', color: C.text }, children: j.name }),
        small(j.limit ? `Normally about ${tokensText(j.normal)} tokens. Limit ${tokensText(j.limit)}.` : j.why)
      ]
    })) : [])
  ].filter(Boolean), 'brake')
}

function HowItWorks({ onHide }) {
  return bubble([
    row('How Crystallize Cron saves', pill('Got it', onHide, C.muted)),
    small('Saves most on jobs that check often for something that rarely changes, only read, and can stay quiet when nothing changed. It can cost more on a job whose data changes every run, so each job shows how often its data changes.'),
    small('A cron job costs roughly its prompt size times its AI calls, plus reasoning. Crystallize shows where each job\'s money goes and works on all of it.', C.soft),
    small('Trials use supported controls and recorded cost and outcome checks. Your main reasoning setting is preserved. A change stays only when its checks pass and its cost, or its tokens where no price is known, falls beyond normal variation. Otherwise the earlier settings come back.', C.soft),
    small('Repeated jobs can learn a read plan from normal runs. When guarded nested execution is unavailable, the main agent follows the plan through normal Hermes tools. Automatic pre-run and no-AI modes stay unavailable until their host capability is verified.', C.soft)
  ], 'how')
}

function Jobs({ data, act, exp, ctx, compatibility }) {
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
          ? jsx(WaitingBubble, { key: item.job_id || item.name, item, trialRuns: data.trial_runs, act, exp, ctx, compatibility })
          : jsx(JobBubble, { key: item.job_id, item, status, trialRuns: data.trial_runs, act, exp, ctx, compatibility })),
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
    row.saved_usd != null && ['Reduction estimate', money(row.saved_usd), C.soft],
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

// Load evidence only when expanded. GET /compare is a read-only preview.
function NativeCronControl({ ctx, jobId, onChanged = () => {} }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [source, setSource] = useState('')
  const [mode, setMode] = useState('monitor')
  const [snapshot, setSnapshot] = useState(false)
  const [verbatim, setVerbatim] = useState(false)
  const [preview, setPreview] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [undoConfirmed, setUndoConfirmed] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)
  const scope = useRequestScope(jobId)
  const running = useRef(false)
  const [draftNote, setDraftNote] = useState(null)
  const [skippedDelivery, setSkippedDelivery] = useState(false)
  const edit = update => { update(); scope.current.version += 1; setPreview(null); setConfirmed(false); setMessage(null) }
  useEffect(() => { setData(null); setPreview(null); setConfirmed(false); setUndoConfirmed(false); setMessage(null); setSource(''); setSnapshot(false); setVerbatim(false); setSkippedDelivery(false); setDraftNote(null) }, [jobId])
  const load = useCallback((preserveError = false) => {
    if (!open) return
    const version = ++scope.current.version
    ctx.rest(`/native-cron?job_id=${encodeURIComponent(jobId)}`).then(res => {
      if (scope.current.scope !== jobId || scope.current.version !== version) return
      if (!res || res.ok === false) setError(errorText(res, 'Could not inspect this source attachment.'))
      else { setData(res); if (!preserveError) setError(null) }
    }).catch(() => { if (scope.current.scope === jobId && scope.current.version === version) setError('Could not inspect this source attachment.') })
  }, [ctx, jobId, open])
  useEffect(() => { load(); return () => { scope.current.version += 1 } }, [load])
  const request = (path, body, mutation = false) => {
    if (running.current) return
    running.current = true; setPending(true); setError(null)
    const version = ++scope.current.version
    ctx.rest(path, { method: 'POST', body: { job_id: jobId, ...body } }).then(res => {
      if (scope.current.scope !== jobId || scope.current.version !== version) return
      if (!res || res.ok === false) { setError(errorText(res, 'Could not complete this source action.')); if (mutation) load(true) }
      else if (!mutation) { setPreview(res); setConfirmed(false) }
      else { setData(res); setPreview(null); setConfirmed(false); setUndoConfirmed(false); setMessage(res.message || 'Source fields updated.'); onChanged() }
    }).catch(() => { if (scope.current.scope === jobId && scope.current.version === version) setError('Could not complete this source action.') })
      .finally(() => { running.current = false; if (scope.current.scope === jobId) setPending(false) })
  }
  const values = { source_script: source, mode, snapshot_only: snapshot, verbatim_output: verbatim, skipped_delivery_ok: skippedDelivery }
  const draft = () => {
    if (running.current) return
    running.current = true; setPending(true); setError(null); setDraftNote(null)
    const version = ++scope.current.version
    ctx.rest('/native-cron-draft', { method: 'POST', body: { job_id: jobId } }).then(res => {
      if (scope.current.scope !== jobId || scope.current.version !== version) return
      if (!res || res.ok === false) setError(errorText(res, res && res.why || 'Could not draft a script for this job.'))
      else {
        edit(() => setSource(res.source_script))
        const missing = (res.left_out || []).map(step => `${step.tool}: ${step.why}`).join('; ')
        setDraftNote(missing ? `${res.message} Left out: ${missing}` : res.message)
      }
    }).catch(() => { if (scope.current.scope === jobId && scope.current.version === version) setError('Could not draft a script for this job.') })
      .finally(() => { running.current = false; if (scope.current.scope === jobId) setPending(false) })
  }
  const attached = data && data.state && ['pending', 'applied'].includes(data.state.status)
  return jsxs('div', { style: panelStyle, children: [
    sectionButton('Skip runs when nothing changed', open, () => setOpen(value => !value)),
    ...(open ? [
      small('A small script reads the job\'s data. When the data hasn\'t changed, the AI doesn\'t run. You review the script before it\'s attached. Crystallize can draft it from what this job has already done.', C.soft),
      data && data.since_attached ? small(data.since_attached.message, data.since_attached.costing_more ? C.yellow : C.green) : null,
      data && data.fit && !attached ? small(data.fit.message, data.fit.verdict === 'poor' ? C.yellow : data.fit.verdict === 'good' ? C.green : C.soft) : null,
      !attached ? actionButton('Draft a script from this job', () => draft(), pending) : null,
      draftNote ? small(draftNote, C.soft) : null,
      attached ? small(`Attached: ${data.state.mode} · ${data.state.approval_revoked ? 'requirements changed; approval revoked' : data.state.status}`, C.soft) : null,
      attached && data.runtime_review_required ? small('This source uses an older or unverified runtime. Undo it, then review and attach the source again to use the current fixes.', C.yellow) : null,
      !attached ? field('Python source under this profile’s scripts directory', jsx('input', { value: source, maxLength: 400, placeholder: 'snapshot.py', disabled: pending, style: fieldStyle, onChange: e => edit(() => setSource(e.target.value)) })) : null,
      !attached ? field('Snapshot behavior', jsx('select', { value: mode, disabled: pending, style: fieldStyle, onChange: e => edit(() => setMode(e.target.value)), children: [jsx('option', { value: 'monitor', children: 'Wake the model when data changes' }), jsx('option', { value: 'verbatim', children: 'Use script output, no model' })] })) : null,
      !attached ? checkField('This source is the complete read-only snapshot; this job has no per-run action, deadline or required repeated delivery', snapshot, value => edit(() => setSnapshot(value)), pending) : null,
      !attached && mode === 'monitor' ? checkField('If this job sends its answer somewhere, nothing should be sent when the data hasn\'t changed', skippedDelivery, value => edit(() => setSkippedDelivery(value)), pending) : null,
      !attached && mode === 'verbatim' ? checkField('The script output is the complete requested final answer', verbatim, value => edit(() => setVerbatim(value)), pending) : null,
      !attached ? actionButton('Preview source · no execution', () => request('/native-cron-preview', values), pending || !source) : null,
      preview ? jsxs('div', { children: [
        small(preview.behavior || '', C.soft),
        preview.source ? jsx('pre', { tabIndex: 0, 'aria-label': 'Complete reviewed snapshot source', style: { ...fieldStyle, maxHeight: 240, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: preview.source.text }) : null,
        preview.source ? jsx('div', { style: { overflowWrap: 'anywhere' }, children: small(`Source SHA256: ${preview.source.sha256}`, C.soft) }) : null,
        small('The copied source runs with ordinary cron permissions. Imports and external resources are not frozen. Reviewing this preview does not execute the source or call a model.', C.soft),
        mode === 'verbatim' ? small('Hermes trims outer whitespace and applies its normal secret redaction. This mode does not promise byte-identical delivery.', C.soft) : null,
        ...(preview.blockers || []).map((text, i) => jsx('div', { key: `native-blocker-${i}`, children: small(text, C.yellow) })),
        preview.can_apply ? checkField('I approve attaching this reviewed source and its snapshot behavior', confirmed, setConfirmed, pending) : null,
        preview.can_apply ? actionButton('Attach reviewed source', () => { if (confirmed) request('/native-cron', { ...values, expected_preview_sha256: preview.preview_sha256, confirmed: true }, true) }, pending || !confirmed) : null
      ] }) : null,
      attached ? checkField('I approve restoring the previous native source fields', undoConfirmed, setUndoConfirmed, pending) : null,
      attached ? actionButton('Undo native source', () => { if (undoConfirmed) request('/native-cron-undo', { expected_config_fingerprint: data.config_fingerprint, confirmed: true }, true) }, pending || !undoConfirmed) : null,
      small('Changed data uses the configured main model and reasoning. Unknown evidence or a failed answer forces a fresh run; no cash-saving percentage is inferred.', C.soft),
      error ? statusLine(error, C.yellow) : null,
      message ? statusLine(message, C.green) : null
    ] : [])
  ] })
}

function JobEvidence({ item, ctx, onChanged }) {
  const jobId = item.job_id
  const [open, setOpen] = useState(false)
  const [receipt, setReceipt] = useState(null)
  const [preview, setPreview] = useState(null)
  const [contract, setContract] = useState('{}')
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(null)
  const [comparison, setComparison] = useState(null)
  const scope = useRequestScope(jobId)
  const running = useRef(false)
  const load = useCallback((preserveError = false) => {
    if (!open || !jobId) return
    const version = ++scope.current.version
    const active = () => scope.current.scope === jobId && scope.current.version === version
    Promise.allSettled([
      ctx.rest(`/receipt?job_id=${encodeURIComponent(jobId)}`),
      ctx.rest(`/compare?job_id=${encodeURIComponent(jobId)}`),
      ctx.rest(`/contract?job_id=${encodeURIComponent(jobId)}`)
    ]).then(results => {
      if (!active()) return
      const [r, p, c] = results.map(result => result.status === 'fulfilled' ? result.value : { error: "Couldn't load this part of the job's evidence." })
      setReceipt(r && !r.error ? r : null)
      setPreview(p && !p.error ? p : { available: false, why: p && p.error })
      if (p && p.latest_blind) setComparison(p.latest_blind)
      if (c && !c.error) setContract(JSON.stringify(c.contract || {}, null, 2))
      if (c && c.error || r && r.error) setError(c && c.error || r && r.error)
      else if (!preserveError) setError(null)
    }).catch(() => { if (active()) setError("Couldn't load this job's evidence.") })
  }, [ctx, jobId, open])
  useEffect(() => { load(); return () => { scope.current.version += 1 } }, [load])
  const save = () => {
    let spec
    try { spec = JSON.parse(contract); if (!spec || typeof spec !== 'object' || Array.isArray(spec)) throw new Error() }
    catch { setError('Use a JSON object for the validation requirements.'); return }
    if (running.current) return
    running.current = true
    scope.current.version += 1
    setPending('save')
    ctx.rest('/contract', { method: 'POST', body: { job_id: jobId, contract: Object.keys(spec).length ? spec : null } })
      .then(res => {
        if (scope.current.scope !== jobId) return
        if (!res || !res.ok) setError(res && (res.message || res.error) || "Couldn't save requirements.")
        else { setError(null); setContract(JSON.stringify(res.contract || {}, null, 2)); load(); onChanged() }
      }).catch(() => { if (scope.current.scope === jobId) setError("Couldn't save requirements.") })
      .finally(() => { running.current = false; if (scope.current.scope === jobId) setPending(null) })
  }
  const compare = () => {
    if (running.current || !preview || !preview.available) return
    running.current = true
    setPending('compare')
    setError(null)
    ctx.rest('/compare', { method: 'POST', body: { job_id: jobId, execution_id: preview.execution_id || null, confirmed: true } })
      .then(res => {
        if (scope.current.scope !== jobId) return
        if (!res || !res.ok) setError(res && (res.message || res.error) || 'Comparison could not finish.')
        if (res && res.comparison) setComparison(res.comparison)
        load(!res || !res.ok)
        onChanged()
      }).catch(() => { if (scope.current.scope === jobId) setError('Comparison failed. Its calls may still have incurred model cost; review the receipt before trying again.') })
      .finally(() => { running.current = false; if (scope.current.scope === jobId) setPending(null) })
  }
  const draft = (() => { try { const value = JSON.parse(contract); return value && typeof value === 'object' && !Array.isArray(value) ? value : null } catch { return null } })()
  const edit = patch => { if (draft) setContract(JSON.stringify({ ...draft, ...patch }, null, 2)) }
  const lines = text => text.split('\n').map(v => v.trim()).filter(Boolean)
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 6, borderTop: `0.5px solid ${C.stroke}`, paddingTop: 5 },
    children: [
      jsx('button', { type: 'button', 'aria-expanded': open, onClick: () => setOpen(v => !v),
        style: { background: 'none', border: 'none', padding: 0, textAlign: 'left', color: C.accent, cursor: 'pointer', fontSize: '0.6875rem' },
        children: `${open ? '▾' : '▸'} Evidence and controls` }),
      ...(open ? [
        error ? jsx('div', { role: 'status', children: small(error, C.yellow) }) : null,
        receipt ? jsx(ReceiptSummary, { receipt }) : small('Loading evidence…'),
        preview ? jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 4 }, children: [
          label('Same-input taste test'),
          small(preview.cost_notice || 'Runs two answer-only model calls using the same saved input. Both routes may charge.', C.soft),
          small('Uses frozen recorded evidence for two answers. It does not replay tools or run the scheduled job.', C.soft),
          !preview.available ? small(preview.why || 'Record a complete run and choose a helper model first.', C.yellow) : null,
          actionButton(pending === 'compare' ? 'Comparing two answers…' : 'Compare same input', compare, !!pending || !preview.available),
          pending === 'compare' ? jsx('div', { role: 'status', 'aria-live': 'polite', children: small('Two model calls are in progress. Their cost is included in the receipt; the job settings stay as they are.', C.soft) }) : null,
          comparison ? jsx(BlindTasteTest, { key: comparison.id, ctx, comparison, onChanged: setComparison }) : null
        ] }) : null,
        jsxs('details', { children: [
          jsx('summary', { style: { color: C.soft, fontSize: '0.6875rem', cursor: 'pointer' }, children: 'Validation requirements' }),
          jsx('div', { style: { display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }, children: [
            small('Choose what every answer must contain. Changing requirements needs new trial evidence. Structural checks do not prove answer quality.', C.soft),
            draft ? field('Required URLs (one per line)', jsx('textarea', { rows: 2, style: fieldStyle, value: (Array.isArray(draft.required_urls) ? draft.required_urls : []).join('\n'), onChange: e => edit({ required_urls: lines(e.target.value) }) })) : null,
            draft ? field('Required JSON fields (one path per line)', jsx('textarea', { rows: 2, style: fieldStyle, value: (Array.isArray(draft.required_fields) ? draft.required_fields : []).join('\n'), onChange: e => edit({ required_fields: lines(e.target.value) }), placeholder: 'items.0.title' })) : null,
            draft ? field('Minimum items', jsx('input', { type: 'number', min: 0, max: 100000, style: fieldStyle, value: draft.minimum_items || 0, onChange: e => edit({ minimum_items: Number(e.target.value) }) })) : null,
            jsxs('details', { children: [jsx('summary', { style: { fontSize: '0.6875rem', color: C.muted, cursor: 'pointer' }, children: 'Advanced sources, actions and status rules' }),
              field('Validation contract (JSON)', jsx('textarea', { rows: 7, value: contract, onChange: e => setContract(e.target.value), style: { ...fieldStyle, fontFamily: 'monospace', marginTop: 5 }, maxLength: 32000 }))] }),
            actionButton(pending === 'save' ? 'Saving…' : 'Save requirements', save, !!pending)
          ] })
        ] }),
        rawDetails('Full receipt and cost provenance', receipt),
        jsx(RhythmControl, { key: 'rhythm', ctx, jobId, onChanged }),
        jsx(NativeCronControl, { key: 'native-cron', ctx, jobId, onChanged }),
        jsx(ModelAuction, { key: 'auction', ctx, jobId, onChanged })
      ] : [])
    ]
  })
}

function ReceiptSummary({ receipt }) {
  const basis = receipt.preferred_basis
  const costs = basis && receipt.cost_bases && receipt.cost_bases[basis]
  const net = receipt.qualified_net_payback_usd ?? (costs && costs.qualified_net_payback_usd) ?? null
  const qualified = receipt.qualified_efficiency_savings_usd ?? (costs && costs.qualified_efficiency_savings_usd) ?? null
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 3 }, children: [
    label('Receipt'),
    small(`${receipt.sample_count || 0} recorded runs · ${receipt.comparison_call_count || 0} comparison calls · ${receipt.helper_call_count || 0} helper calls`, C.soft),
    jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 5 }, children: [
      jsx(Tile, { title: 'Qualified reduction', value: money(qualified) }),
      jsx(Tile, { title: 'Recorded overhead', value: money(costs && costs.overhead_usd) }),
      jsx(Tile, { title: 'Qualified net', value: money(net), color: net == null ? C.soft : net >= 0 ? C.green : C.yellow })
    ] }),
    small(basis === 'actual' ? 'Uses recorded actual costs with matching comparison settings.' : basis === 'estimated' ? 'Uses cost estimates with matching comparison settings.' : 'Qualified net is unknown until comparable costs and settings are recorded.'),
    receipt.efficiency_savings_proven !== true ? small('Efficiency savings are not established by the available evidence. A model switch or missing reasoning identity cannot qualify as same-settings efficiency savings.', C.yellow) : null,
    costs && (costs.unproven_settings_comparison_count || costs.excluded_settings_comparison_count) ? small(`${costs.unproven_settings_comparison_count || 0} comparisons lack complete settings evidence · ${costs.excluded_settings_comparison_count || 0} excluded from matching-settings savings.`) : null,
    costs && costs.historical_savings_usd != null && qualified == null ? small(`Historical counterfactual: ${money(costs.historical_savings_usd)} reduction before overhead. This unqualified estimate is separate from the qualified totals.`) : null,
    net != null && receipt.estimated_break_even_remaining_runs != null ? small(`About ${receipt.estimated_break_even_remaining_runs} more comparable runs to repay recorded overhead.`) : null,
    Array.isArray(receipt.uncertainty) && receipt.uncertainty.length ? small(receipt.uncertainty.join(' '), C.yellow) : null
  ] })
}

// Cheaper settings for any AI job, routine or not.
function TuneRow({ tune, trialRuns, act }) {
  if (!tune) return null
  const trial = ['trial', 'trying', 'testing', 'running'].includes(tune.status)
  const kept = tune.status === 'kept'
  const notes = [
    tune.status_text !== 'Not tried yet' ? tune.status_text : null,
    tune.api_cost_now != null && !kept ? `${tune.included_now ? 'in your plan, ' : ''}about ${money(tune.api_cost_now)} a run at API prices` : null,
    tune.tokens_now != null ? `${Math.round(tune.tokens_now).toLocaleString()} tokens a run` : null,
    tune.reasoning_share != null && !kept ? `reasoning is about ${Math.round(tune.reasoning_share * 100)}% of its cost` : null
  ].filter(Boolean)
  const job = tune.job_id || tune.name
  return jsxs('div', {
    key: 'tune',
    style: { display: 'flex', flexDirection: 'column', gap: 4, marginTop: 2 },
    children: [
      row('Settings', jsxs('div', {
        style: { display: 'flex', gap: 6, alignItems: 'center' },
        children: [
          kept ? chip('Kept', C.green) : trial ? chip('Trial', C.yellow) : tune.status === 'revalidation' ? chip('Needs new evidence', C.yellow) : null,
          tune.can_undo ? pill('Undo settings', () => act('/tune', { job, undo: true }), C.muted) : null
        ]
      })),
      notes.length ? small(notes.join(' · '), kept ? C.green : C.soft) : null,
      tune.can_try ? jsxs('div', {
        style: { display: 'flex', flexDirection: 'column', gap: 2, alignItems: 'flex-start' },
        children: [
          pill('Try cheaper settings', () => act('/tune', { job }), C.accent),
          small(`Tests cleaned tool results and a short efficiency note, with the same model and reasoning. After ${trialRuns} answers it stays only if its checks pass and it used less.`)
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
      tuned.per_week && !(item.cron && item.cron.runs_total) ? big(perWeek(tuned.per_week), C.soft)
        : tuned.saved != null ? big(money(tuned.saved), C.soft) : null,
      small(`Historical comparison estimate · ${show(tuned.before)} → ${show(tuned.after)} a run${plan ? ' at API prices' : ''} over ${tuned.runs} run${tuned.runs === 1 ? '' : 's'}. Review the receipt for matching-settings evidence.`, C.soft)
    ]
  })
}

// An active cron job with no routine yet: what it's doing, and what it can do.
function WaitingBubble({ item, trialRuns, act, exp, ctx, compatibility }) {
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
    jsx(JobEvidence, { key: 'evidence', item, ctx, onChanged: () => act(null) }),
    small(item.reason, C.muted)
  ], id)
}

// A finished job has no actions left, so it has no buttons, but it opens like any other job.
function FinishedRow({ item, exp }) {
  const cron = item.cron || {}
  const open = isOpen(exp, item.job_id, { status: 'finished' })
  const header = jsx(JobHeader, { item, open, toggle: exp.toggle, hide: exp.hide, muted: true })
  const summary = [item.was_live && item.saved_usd ? `historical reduction estimate ${money(item.saved_usd)}` : null,
    item.plan_saved_usd ? `${money(item.plan_saved_usd)} estimated reduction at API prices` : null, cronLine(cron)].filter(Boolean).join(' · ')
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
    opt('popups', 'Pop-ups when a read plan is ready for review')
  ], 'settings')
}

// ---------------------------------------------------------------- message-box toggle

function Panel({ ctx }) {
  const compatibilityState = useCompatibility(ctx)
  // Stored (durable) id of the chat being looked at: the id Hermes keys costs
  // and messages by, and it follows focus between split tiles.
  const sessionId = useValue(host.state.focusedStoredSessionId)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const requests = useRequestScope(sessionId)

  useEffect(() => diag('panel mounted', { sessionId }), [])

  const load = useCallback(() => {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
    const version = ++requests.current.version
    const active = () => requests.current.scope === sessionId && requests.current.version === version
    ctx.rest(`/summary${q}`)
      .then(res => {
        if (!active()) return
        if (res && res.error) setError(res.error)
        else { setError(null); setData(res) }
      })
      .catch(err => {
        if (!active()) return
        diag('summary failed', String(err))
        setError("Couldn't reach the Crystallize backend. Is the plugin enabled?")
      })
  }, [ctx, sessionId])

  useEffect(() => {
    load()
    return ctx.setInterval(load, REFRESH_MS)
  }, [ctx, load])

  const act = useCallback((path, body) => {
    if (!path) { load(); return }
    diag('action', { path, body })
    return ctx.rest(path, { method: 'POST', body })
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
    body = [jsx(Hero, { key: 'hero', data, act }), jsx(RunawayBrake, { key: 'brake', ctx, act, refresh: data })]
    if (prefs.explain !== false) body.push(jsx(HowItWorks, { key: 'how', onHide: () => setPref('explain', false) }))
    if (prefs.jobs) body.push(jsx(Jobs, { key: 'jobs', data: shown, act, exp, ctx, compatibility: compatibilityState.data }))
    if (prefs.jobs) body.push(jsx(Finished, { key: 'finished', items: finishedShown, exp }))
    body.push(jsx(CompatibilityCard, { key: 'capabilities', state: compatibilityState }))
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
  const [inventory, setInventory] = useState(null)
  const [active, setActive] = useState(null)
  const [search, setSearch] = useState('')
  const [saving, setSaving] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(!picked || !picked.model)
  const [retry, setRetry] = useState(0)
  const scope = useRequestScope('helper-picker')
  const running = useRef(false)
  useEffect(() => {
    const version = ++scope.current.version
    ctx.rest('/chat-models').then(res => {
      if (scope.current.scope !== 'helper-picker' || scope.current.version !== version) return
      if (!res || res.ok === false || res.error) { setError(errorText(res, 'Could not read configured or declared routes.')); return }
      setInventory(res); setError(null)
    }).catch(() => { if (scope.current.scope === 'helper-picker' && scope.current.version === version) setError('Could not read configured or declared routes.') })
    return () => { scope.current.version += 1 }
  }, [ctx, retry])
  const providers = inventory && inventory.providers || []
  const provider = providers.find(value => value.slug === (active || picked && picked.provider)) || providers[0]
  const term = search.trim().toLowerCase()
  const models = (provider && provider.models || []).filter(model => !term || model.id.toLowerCase().includes(term))
  const refresh = () => setRetry(value => value + 1)
  const choose = model => {
    if (running.current || !provider) return
    running.current = true; setSaving(model.id); setError(null)
    ctx.rest('/chat-model', { method: 'POST', body: { provider: provider.slug, model: model.id } }).then(res => {
      if (scope.current.scope !== 'helper-picker') return
      if (!res || !res.ok) setError(errorText(res, 'Could not select this helper route.'))
      else { onPicked(); setOpen(false) }
    }).catch(() => { if (scope.current.scope === 'helper-picker') setError('Could not select this helper route.') })
      .finally(() => { running.current = false; if (scope.current.scope === 'helper-picker') setSaving(null) })
  }
  if (!open) return bubble([
    row('Helper model', actionButton('Change', () => setOpen(true))), small(routeLabel(picked), C.soft),
    small('Configured helper choice. Hermes checks authentication and route availability when a permitted call runs.')
  ], 'picker')
  return bubble([
    row('Helper model', actionButton('Close model picker', () => setOpen(false), !!saving)),
    small(picked && picked.model ? `Selected: ${routeLabel(picked)}` : 'No helper selected', C.soft),
    small('Used for explicit text subtasks and permitted summaries. Your main model and reasoning stay as configured.', C.soft),
    small(inventoryNotice, C.soft),
    ...metadataWarnings(inventory).map((message, index) => jsx('div', { key: `inventory-warning-${index}`, children: small(message, C.soft) })),
    !inventory && !error ? statusLine('Loading configured and declared route choices…') : null,
    error ? jsxs('div', { style: panelStyle, children: [statusLine(error, C.yellow), actionButton('Refresh model choices', refresh, !!saving)] }) : null,
    inventory && !providers.length ? small('No route choices were returned. Add the provider and model identifiers accepted by your Hermes setup below.') : null,
    provider ? jsx('div', {
      key: 'providers', role: 'tablist', 'aria-label': 'Declared provider routes', style: { display: 'flex', gap: 2, flexWrap: 'wrap' },
      children: providers.map(value => jsx('button', {
        key: value.slug, type: 'button', role: 'tab', 'aria-selected': value.slug === provider.slug, disabled: !!saving,
        onClick: () => { setActive(value.slug); setSearch('') },
        style: { padding: '2px 7px', borderRadius: 5, border: 'none', cursor: 'pointer', fontSize: '0.6875rem', lineHeight: 1.6, background: value.slug === provider.slug ? C.stroke : 'none', color: value.slug === provider.slug ? C.text : C.muted },
        children: value.name || value.slug
      }))
    }) : null,
    provider ? field(`Search ${provider.name || provider.slug} route choices`, jsx('input', { type: 'search', value: search, disabled: !!saving, onChange: event => setSearch(event.target.value), style: fieldStyle })) : null,
    provider ? jsx('div', { key: 'models', style: { display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 280, overflowY: 'auto' }, children: models.length ? models.slice(0, 60).map(model => {
      const selected = picked && picked.provider === provider.slug && picked.model === model.id
      const priced = typeof model.input_per_million === 'number' && typeof model.output_per_million === 'number'
      const price = priced ? `${model.input_per_million.toLocaleString(undefined, { maximumSignificantDigits: 6 })} input · ${model.output_per_million.toLocaleString(undefined, { maximumSignificantDigits: 6 })} output USD per million · supplied estimate` : 'Price unknown · add a user-supplied rate card'
      return jsxs('button', {
        key: model.id, type: 'button', disabled: !!saving, onClick: () => choose(model),
        style: { display: 'flex', flexDirection: 'column', gap: 2, textAlign: 'left', padding: '6px 8px', borderRadius: 6, cursor: saving ? 'wait' : 'pointer', border: `0.5px solid ${selected ? C.green : C.stroke}`, background: selected ? C.tile : 'none', color: C.text },
        children: [jsx('span', { style: { fontSize: '0.75rem', overflowWrap: 'anywhere' }, children: saving === model.id ? `Saving ${model.id}…` : model.id }), small(price), small('Declared route · authentication unverified')]
      })
    }) : small('No route choices match this search.') }) : null,
    models.length > 60 ? small(`${models.length} matches. Refine the search to see more.`) : null,
    jsx(RouteEntry, { key: 'route-entry', ctx, onAdded: refresh }),
    jsx(PricingCard, { key: `pricing:${picked && picked.provider || ''}:${picked && picked.model || ''}`, ctx, route: picked, onSaved: refresh }),
    actionButton('Refresh model choices', refresh, !!saving)
  ].filter(Boolean), 'picker')
}
function ThisChat({ state, onToggle, heading = 'This chat', pending = false }) {
  if (!state.session_id) return bubble([label(heading), small('Send a message in any chat, then come back here to turn it on.')], 'this')
  const saved = state.this_chat
  const projection = state.projection_pricing || saved && saved.projection_pricing || { model: state.main_model, provider: state.main_provider }
  const status = state.can_start || state.enabled
    ? actionButton(pending ? 'Saving…' : state.enabled ? 'Turn off' : 'Turn on', onToggle, pending)
    : chip('Off', C.muted)
  const cells = saved ? [
    ['Input removed', `${tokensText(saved.tokens_removed || 0)} tokens`, C.green],
    ['Avoided input, current price', money(saved.once_at_main_price)],
    ['Generation avoided, current price', money(saved.estimated_output_avoided_usd)],
    ['Delegated read, current price', money(saved.estimated_helper_read_usd)],
    [saved.upper_bound_cost_calls && !saved.unknown_cost_calls ? 'Helper cost upper bound' : 'Helper cost estimate', money(saved.cheap_cost)],
    ['Projected net, one read', money(saved.net_once), saved.net_once == null ? C.soft : saved.net_once >= 0 ? C.green : C.yellow],
    ['Helper calls', String(saved.helper_calls || 0)],
    state.chat_spent_usd != null ? ['Chat spend recorded', money(state.chat_spent_usd)] : null
  ].filter(Boolean) : []
  const turns = (state.turns || []).slice(-8)
  return bubble([
    row(heading, status),
    !state.enabled ? small(state.can_start ? 'Off for this chat. Controls apply after you turn it on.' : state.why_not, C.soft)
      : small('On for future tool results. Your main model stays the same.', C.soft),
    saved ? jsx('div', {
      key: 'cells',
      style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(105px, 1fr))', gap: 8 },
      children: cells.map(([title, value, color]) => jsxs('div', {
        key: title,
        children: [
          jsx('div', { style: { fontSize: '0.625rem', color: C.muted }, children: title }),
          jsx('div', { style: { fontSize: '0.8125rem', color: color || C.text, fontVariantNumeric: 'tabular-nums' }, children: value })
        ]
      }))
    }) : small('Estimates appear after a tool result is reduced or a helper is used.'),
    saved ? small(`Current-price projection for ${routeLabel(projection)}. All recorded turns use this main model's current rates; changing the model or rates reprices these figures. They are not historical billing or guaranteed savings.`, C.soft) : null,
    saved ? small(`Current main prices per million tokens: ${money(projection.input_usd_per_token == null ? null : projection.input_usd_per_token * 1e6)} input · ${money(projection.output_usd_per_token == null ? null : projection.output_usd_per_token * 1e6)} output.`, C.soft) : null,
    saved ? small('One-read net combines one avoided input read and generation actually delegated, less delegated output reads, recorded helper costs and extra main-call overhead. Context Rent below accounts for later reads.', C.soft) : null,
    saved && saved.upper_bound_cost_calls ? small(`${saved.upper_bound_cost_calls} helper call${saved.upper_bound_cost_calls === 1 ? ' has' : 's have'} missing or ambiguous cache details. Those calls use a conservative upper bound, pricing unallocated input at the highest applicable rate.`, C.soft) : null,
    saved && saved.unknown_cost_calls ? small(`${saved.unknown_cost_calls} helper call${saved.unknown_cost_calls === 1 ? ' has' : 's have'} unknown cost. Net savings are unknown.`, C.yellow) : null,
    saved && (saved.failed_calls || saved.rejected_calls) ? small(`${saved.failed_calls || 0} failed and ${saved.rejected_calls || 0} rejected helper calls are included in cost estimates.`) : null,
    turns.length || (state.breakdown || []).length ? jsxs('details', { key: 'turns', children: [
      jsx('summary', { style: { color: C.soft, cursor: 'pointer', fontSize: '0.6875rem' }, children: 'Per-turn and helper breakdown' }),
      ...turns.map((t, i) => jsx('div', { key: t.turn_id || i, style: { marginTop: 5 }, children: small(
        `Turn ${String(t.turn_id || i + 1)}: ${tokensText(t.tokens_removed || 0)} input tokens removed · helper ${money(t.cheap_cost)} · current-price one-read net ${money(t.net_once)}`, C.soft) })),
      ...(state.breakdown || []).map((part, i) => small(`${part.kind || part.name || i}: ${part.helper_calls || 0} helper calls · ${money(part.cheap_cost)} helper cost · ${tokensText(part.tokens_removed || 0)} tokens removed`))
    ] }) : null,
    state.not_cheaper ? small('The selected helper is not cheaper than your main model. Choose a lower-cost route to allow automatic helper work.', C.yellow) : null
  ].filter(Boolean), 'this')
}

function ChatControls({ ctx, sessionId, policy, observedTools = [], onSaved }) {
  const [focus, setFocus] = useState(policy.focus || '')
  const [tool, setTool] = useState('')
  const [mode, setMode] = useState('clean')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const scope = useRequestScope(sessionId)
  const saving = useRef(false)
  useEffect(() => { setFocus(policy.focus || ''); setError(null) }, [sessionId, policy.focus])
  const save = (name, value, includeFocus = false) => {
    if (saving.current) return
    saving.current = true
    setPending(true)
    const body = { session_id: sessionId, tool: name, mode: value, ...(includeFocus ? { focus } : {}) }
    ctx.rest('/chat-policy', { method: 'POST', body }).then(res => {
      if (scope.current.scope !== sessionId) return
      if (!res || !res.ok) setError(res && (res.message || res.error) || "Couldn't save these controls.")
      else { setError(null); setTool(''); onSaved(res.policy) }
    }).catch(() => { if (scope.current.scope === sessionId) setError("Couldn't save these controls.") })
      .finally(() => { saving.current = false; if (scope.current.scope === sessionId) setPending(false) })
  }
  const selectMode = (value, onChange, name) => jsx('select', {
    value, onChange: e => onChange(e.target.value), disabled: pending, 'aria-label': name, style: { ...fieldStyle, width: 'auto' },
    children: [['exact', 'Exact'], ['clean', 'Clean'], ['summary', 'Summary']].map(([value, title]) => jsx('option', { key: value, value, children: title }))
  })
  const tools = [...new Set([...Object.keys(policy.tools || {}), ...observedTools])].sort()
  return bubble([
    row('Tool-result controls', selectMode(policy.default_mode || 'clean', value => save('*', value), 'Default tool-result mode')),
    small('Start with Clean: it removes redundant formatting without a model call. Exact keeps a result unchanged. Summary permits a paid helper when its estimate pays back; local checks must retain observable values, IDs, dates and URLs. Files and failed results stay exact.', C.soft),
    field('Task focus (optional)', jsx('textarea', { value: focus, rows: 2, maxLength: 2000, onChange: e => setFocus(e.target.value), style: fieldStyle, placeholder: 'What information matters for this task?' })),
    actionButton(pending ? 'Saving…' : 'Save task focus', () => save('*', policy.default_mode || 'clean', true), pending),
    jsxs('details', { key: 'tools', children: [
      jsx('summary', { style: { color: C.soft, cursor: 'pointer', fontSize: '0.6875rem' }, children: `Per-tool overrides${tools.length ? ` · ${tools.length}` : ''}` }),
      ...tools.map(name => jsx('div', { key: name, style: { marginTop: 5 }, children: row(name, selectMode(policy.tools[name] || policy.default_mode || 'clean', value => save(name, value), `Mode for ${name}`)) })),
      jsx('div', { style: { display: 'flex', flexDirection: 'column', gap: 5, marginTop: 6 }, children: [
        field('Tool name', jsx('input', { value: tool, maxLength: 128, onChange: e => setTool(e.target.value), style: fieldStyle, placeholder: 'Name shown on a tool result' })),
        row('New override', selectMode(mode, setMode, 'New tool-result mode')),
        actionButton('Save tool override', () => save(tool.trim(), mode), pending || !tool.trim())
      ] })
    ] }),
    error ? jsx('div', { role: 'status', children: small(error, C.yellow) }) : null,
    small('These settings apply only to this chat after you enable it. Saved full-result links expire after seven days.')
  ].filter(Boolean), 'controls')
}

// Context Rent: a map of what the chat re-reads on every AI call, and a way to stop paying for a result.
function ContextRent({ ctx, sessionId, refresh, onTools }) {
  const [data, setData] = useState(null)
  const [picked, setPicked] = useState(null)
  const requests = useRequestScope(sessionId)
  const load = useCallback(() => {
    if (!sessionId) return
    const version = ++requests.current.version
    const active = () => requests.current.scope === sessionId && requests.current.version === version
    ctx.rest(`/chat-rent?session_id=${encodeURIComponent(sessionId)}`)
      .then(res => {
        if (!active()) return
        setData(res && !res.error ? { ...res, session_id: sessionId } : null)
        if (onTools && res && !res.error) onTools([...new Set((res.items || []).map(item => item.tool).filter(name => typeof name === 'string'))])
      })
      .catch(() => { if (active()) setData(null) })
  }, [ctx, sessionId, onTools])
  useEffect(() => { load(); return () => { requests.current.version += 1 } }, [load, refresh])
  if (!data || data.session_id !== sessionId || !data.calls) return null
  const act = (path, id) => {
    ctx.rest(path, { method: 'POST', body: { session_id: sessionId, id } })
      .then(res => {
        if (requests.current.scope !== sessionId) return
        host.notify({ kind: res && res.ok ? 'success' : 'warning', message: (res && res.message) || 'Done.' })
        load()
      })
      .catch(err => { if (requests.current.scope === sessionId) host.notify({ kind: 'error', message: `Crystallize: ${String(err)}` }) })
  }
  const results = data.items.filter(i => !i.removed)
  const tiles = [
    ...results.map(i => ({ key: i.id, name: i.tool, tokens: i.tokens, item: i })),
    ...data.groups.map(g => ({ key: g.name, name: g.name, tokens: g.tokens }))
  ].filter(t => t.tokens > 0)
  const total = tiles.reduce((sum, t) => sum + t.tokens, 0) || 1
  const chosen = data.items.find(i => i.id === picked)
  const explain = i => {
    if (i.payback_calls === null || i.payback_calls === undefined) return `${tokensText(i.tokens)} tokens. No listed price for this model.`
    const back = i.payback_calls === 0
      ? 'Removing it costs nothing extra, because nothing comes after it.'
      : `Removing it resets the provider's cache once, which pays back after ${i.payback_calls} more AI call${i.payback_calls === 1 ? '' : 's'}.`
    return `${tokensText(i.tokens)} tokens. Costs ${money(i.per_call_usd)} on every AI call, ${money(i.paid_usd)} so far. ${back}`
  }
  const itemRow = i => jsxs('div', {
    key: i.id,
    style: { display: 'flex', flexDirection: 'column', gap: 3, padding: '6px 8px', borderRadius: 6, background: i.id === picked ? C.tile : 'none', border: `0.5px solid ${C.stroke}` },
    children: [
      row(jsx('span', { style: { fontSize: '0.75rem', color: i.removed ? C.muted : C.text, wordBreak: 'break-word' }, children: i.what ? `${i.tool}: ${i.what}` : i.tool }),
        i.removed ? pill('Put back', () => act('/chat-restore', i.id), C.muted) : pill('Remove', () => act('/chat-evict', i.id), i.worth_it ? C.accent : C.muted)),
      small(i.removed ? `Removed. Saves ${tokensText(i.tokens)} tokens on every AI call.` : explain(i), C.soft),
      !i.removed && i.worth_it && i.payback_calls > 0 ? small('This chat has already made more AI calls than that, so removing it is likely to pay off.', C.green) : null
    ]
  })
  return bubble([
    row('Context Rent', data.per_call_usd != null ? chip(`${money(data.per_call_usd)} a call`, C.yellow) : chip(`${tokensText(data.tokens)} tokens`, C.muted)),
    small(data.cache_read_share != null && data.cache_read_share < 0.1
      ? 'Your model\'s cached reads are nearly free, so removing old results rarely pays and savings here will be smaller.'
      : data.calls < 8 ? 'This chat is still short, so there\'s little to save yet. Savings grow as it goes on.'
        : 'Long chats like this one save the most, because old results are re-read on every call.', C.soft),
    data.auto && data.auto.removed ? small(`Removed ${data.auto.removed} old result${data.auto.removed === 1 ? '' : 's'} automatically because the numbers showed it would pay. ${data.auto.read_backs ? `The AI read ${data.auto.read_backs} back.` : 'None were needed again.'}${data.auto.off ? ' Stopped for this chat because they kept being needed.' : ''}`, C.soft) : null,
    small(`Every AI call re-reads this chat: about ${tokensText(data.tokens)} tokens right now. The biggest blocks cost the most. Pick a tool result to see what it costs.`, C.soft),
    jsx('div', {
      key: 'map',
      role: 'list',
      'aria-label': 'What this chat re-reads, sized by tokens',
      style: { display: 'flex', flexWrap: 'wrap', gap: 2, minHeight: 72 },
      children: tiles.map(t => jsx('button', {
        key: t.key,
        type: 'button',
        role: 'listitem',
        disabled: !t.item,
        title: `${t.name}: ${tokensText(t.tokens)} tokens`,
        onClick: () => t.item && setPicked(t.key === picked ? null : t.key),
        style: {
          flex: `${t.tokens} 1 ${Math.max(36, (t.tokens / total) * 280)}px`, minHeight: 34, padding: '3px 5px', overflow: 'hidden', textAlign: 'left',
          borderRadius: 4, cursor: t.item ? 'pointer' : 'default', fontSize: '0.625rem', lineHeight: 1.3, whiteSpace: 'nowrap', textOverflow: 'ellipsis',
          background: C.tile, color: t.item ? C.text : C.muted,
          border: t.key === picked ? `1px solid ${C.accent}` : `0.5px solid ${t.item ? C.yellow : C.stroke}`
        },
        children: `${t.name} ${tokensText(t.tokens)}`
      }))
    }),
    ...(chosen ? [itemRow(chosen)] : results.filter(i => i.worth_it).slice(0, 3).map(itemRow)),
    ...data.items.filter(i => i.removed && i.id !== picked).map(itemRow),
    data.removed_tokens ? small(data.saving_per_call_usd != null ? `Removed ${tokensText(data.removed_tokens)} tokens, which saves about ${money(data.saving_per_call_usd)} on every AI call.` : `Removed ${tokensText(data.removed_tokens)} tokens from every AI call.`, C.green) : null,
    small('Removing only changes what is sent to the AI. Your chat stays the same, and the AI can read the original if it needs to. Tokens and prices are estimates.')
  ].filter(Boolean), 'rent')
}

// The sidebar page works on the chat you were last in.
function ChatSection({ ctx, sessionId: focused }) {
  const [remembered, setRemembered] = useState(() => ctx.storage.get('last_chat', null))
  useEffect(() => {
    if (focused && focused !== remembered) {
      setRemembered(focused)
      ctx.storage.set('last_chat', focused)
    }
  }, [ctx, focused, remembered])
  const sessionId = focused || remembered
  return jsx(ChatSession, { key: sessionId || 'no-chat', ctx, sessionId, focused })
}

function ChatSession({ ctx, sessionId, focused }) {
  const compatibilityState = useCompatibility(ctx)
  const [state, setState] = useState(null)
  const [observedTools, setObservedTools] = useState([])
  useEffect(() => setObservedTools([]), [sessionId])
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)
  const requests = useRequestScope(sessionId)
  const toggling = useRef(false)
  const load = useCallback(() => {
    const version = ++requests.current.version
    const active = () => requests.current.scope === sessionId && requests.current.version === version
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
    ctx.rest(`/chat-summary${q}`).then(res => {
      if (!active()) return
      if (!res || res.error) setError(res && res.error || "Couldn't read this chat.")
      else if ((res.session_id || null) !== (sessionId || null)) setError('Chat focus changed. Refresh to load its controls.')
      else { setState(res); setError(null) }
    }).catch(() => { if (active()) setError("Couldn't reach the Crystallize backend. Is the plugin enabled?") })
  }, [ctx, sessionId])
  useEffect(() => {
    load()
    const dispose = ctx.setInterval(load, REFRESH_MS)
    return () => { requests.current.version += 1; dispose() }
  }, [ctx, load])
  const toggle = () => {
    if (!state || state.session_id !== sessionId || toggling.current) return
    toggling.current = true
    setPending(true)
    ctx.rest('/chat-toggle', { method: 'POST', body: { session_id: state.session_id, on: !state.enabled } })
      .then(res => {
        if (requests.current.scope !== sessionId) return
        if (res && !res.ok) host.notify({ kind: 'warning', message: res.message })
        load()
      }).catch(err => { if (requests.current.scope === sessionId) host.notify({ kind: 'error', message: `Crystallize: ${String(err)}` }) })
      .finally(() => { toggling.current = false; if (requests.current.scope === sessionId) setPending(false) })
  }
  const current = state && state.session_id === sessionId ? state : null
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 14 },
    children: [
      error ? jsx('div', { role: 'status', children: small(error, C.soft) }) : null,
      !current && !error ? small('Loading…') : null,
      current ? jsx(ThisChat, { key: 'this', state: current, onToggle: toggle, pending, heading: focused ? 'This chat' : `Your last chat${current.title ? `: ${current.title}` : ''}` }) : null,
      current ? small(!current.enabled
        ? 'Start with lossless Clean after enabling this chat. A helper model is optional; your main model and reasoning stay unchanged.'
        : current.routing && current.routing.cost_basis === 'unknown'
          ? 'Helper cost/payback is unknown for this route. Clean needs no helper call; add an exact-route rate card to estimate paid handoffs.'
          : 'Savings shown here are estimates. Review exact originals whenever a summary or excerpt lacks a needed fact.', C.soft) : null,
      current ? small('Saves most in long chats that read large files or results early and move on. Short chats save little, and a single run can still cost more if the provider\'s cache misses.', C.soft) : null,
      current && sessionId ? jsx(ChatControls, { key: `controls:${sessionId}`, ctx, sessionId, policy: current.policy || { default_mode: 'clean', focus: '', tools: {} }, observedTools: current.tool_names || observedTools, onSaved: policy => { requests.current.version += 1; setState(old => old && old.session_id === sessionId ? { ...old, policy } : old) } }) : null,
      current && sessionId ? jsx(RereadControls, { key: `reread:${sessionId}`, ctx, sessionId, chatEnabled: current.enabled }) : null,
      current && sessionId ? jsx(ContextRent, { key: `rent:${sessionId}`, ctx, sessionId, refresh: current, onTools: setObservedTools }) : null,
      current ? jsx(ModelPicker, { key: 'picker', ctx, picked: current.model, onPicked: load }) : null,
      current ? jsx(DelegationControls, { key: 'delegation', ctx, picked: current.model }) : null,
      current ? jsxs('details', { key: 'how', children: [
        jsx('summary', { style: { color: C.soft, cursor: 'pointer', fontSize: '0.6875rem' }, children: 'How Chat saves' }),
        small('Your main model can route explicit routine tasks to the selected helper. Tool-result controls determine which results may be cleaned or summarized. Files and failed results stay exact.', C.soft),
        small('For a large artifact, let Hermes read the input through its normal tools, route one complete text subtask, and save the reviewed result through normal Hermes tools. Main reasoning and decisions stay with your chosen model.', C.soft),
        small('Re-read controls are an additional opt-in for repeated text and batching guidance. Context Rent shows later read estimates and cache payback. Global tool settings require a separate choice in Hermes.', C.soft),
        small(current.why_not_first, C.soft)
      ] }) : null,
      current ? jsx(CompatibilityCard, { key: 'capabilities', state: compatibilityState }) : null
    ]
  })
}

function ChatPage({ ctx }) {
  const sessionId = useValue(host.state.focusedStoredSessionId)
  return jsx('div', {
    style: { height: '100%', overflowY: 'auto' },
    children: jsx('div', {
      style: { padding: 16, maxWidth: 640, margin: '0 auto' },
      children: jsxs('div', {
        style: { display: 'flex', flexDirection: 'column', gap: 14 },
        children: [
          jsxs('div', {
            key: 'title',
            style: { display: 'flex', alignItems: 'center', gap: 6, color: C.text },
            children: [jsx(Codicon, { name: 'ruby', size: '0.875rem' }), jsx('span', { style: { fontSize: '0.8125rem', letterSpacing: '0.08em' }, children: 'CRYSTALLIZE CHAT' })]
          }),
          jsx(ChatSection, { key: 'section', ctx, sessionId })
        ]
      })
    })
  })
}

// Two sections, like the pages of a website: one for scheduled jobs, one for the chat you're in.
// Plain explanations of everything on the panel.
const NOTES = [
  ['The difference', [
    ['Crystallize Cron', 'Tests cheaper settings for scheduled jobs using recorded cost and outcome checks. Task-specific requirements make the checks more useful.'],
    ['Crystallize Chat', 'Works to lower the cost of the chat you\'re in. You turn it on for each chat, and your main model can send routine work to a cheaper model you pick.']
  ]],
  ['How Cron saves', [
    ['Try lower-cost settings', 'Starts a measured trial of cleaned tool results and a short efficiency note. Your model and reasoning stay the same. It stays only if its checks pass and it used less.'],
    ['Skip runs when nothing changed', 'A reviewed script reads the job\'s data and the AI only runs when that data changes. Crystallize can draft the script. Any failed read does a normal run instead.'],
    ['Prices', 'List prices come from public model lists for your exact provider and model, or from a rate card you enter. They are estimates, not your bill. With no price, a paid helper stays off.'],
    ['Read plan', 'Repeated read steps learned from normal Hermes runs. Use host-tool plan explicitly to guide the main agent through the regular tool pipeline. Automatic pre-run replay requires verified guarded nested dispatch.'],
    ['Host-tool plan', 'The learned plan is in use, while Hermes executes each tool through its normal permissions, hooks and approvals. Automatic execution modes remain unavailable when their public capability is missing.'],
    ['Jobs on lower-cost settings', 'How much of your scheduled work uses them. A saving only counts between runs with the same model, provider and reasoning.'],
    ['Runaway Brake', 'Off until you turn it on. Each job gets a token limit from its own recent runs: at least 3 times a normal run and 1.5 times its largest. A run past that limit is told to stop using tools and answer with what it has, and to say it stopped early. Jobs that send or change things are never stopped, and jobs with fewer than 5 finished runs have no limit yet.']
  ]],
  ['When it saves most', [
    ['Best for chat', 'Long chats that read large tool results early and move on, especially JSON. Short chats with small results save little.'],
    ['Best for cron', 'Jobs that check often for something that rarely changes, only read, and can stay quiet when nothing changed. An unchanged check makes no AI call.'],
    ['Where it can cost more', 'Removing a result by hand when the AI still needs it, and attaching a script to a job whose data changes on every run. A single run can also cost more when the provider\'s cache misses, so judge by several runs.'],
    ['Models with cheap cached reads', 'When re-reading is nearly free, removing old results doesn\'t pay, so Crystallize leaves them and savings are smaller.']
  ]],
  ['How Chat saves', [
    ['Cleaned results', 'Uses safe cleanup without an AI call when Clean is selected. Exact leaves a result unchanged; Summary permits a helper call. File contents and failed results stay exact.'],
    ['Routing', 'Your main model decides when to send routine work to your cheap model and takes the output straight back, such as summarizing or extracting data. It keeps decisions, anything that sends or changes something, and the final answer.'],
    ['Helper model', 'Handles explicit routed tasks and summaries permitted by this chat’s controls. Your main model stays the same. Unknown costs remain unknown in the breakdown.'],
    ['Full results', 'When a result is noticeably reduced, the exact original is kept for 7 days. Your main model can read it, or ask the cheap model about it.'],
    ['Context Rent', 'Every AI call re-reads the whole chat, so each old tool result costs a little on every call after it. This shows what each one costs. In a chat with Crystallize on, old results are removed automatically when real prices show it would pay, counting the chance the AI needs one back. Remove leaves a result out of what is sent to the AI, while your chat stays the same and the AI can still read the original. It works without turning on Crystallize Chat. Removing can reset the provider\'s cache once, so each result shows how many calls that takes to pay back.'],
    ['Why not from the start', 'Hermes creates the chat when your first prompt is sent, and savings come from tool results, which don\'t exist until the work starts.']
  ]],
  ['Review and control', [
    ['Taste test', 'Compare anonymous A/B answers from the same captured input. Choose before revealing models, costs and recorded checks. The preference is your judgment; answer-only evidence does not test tool execution.'],
    ['Rhythm', 'Review real observations by weekday and hour, then preview a different frequency. Savings are projections. Applying requires explicit consent to a noncritical job and an acceptable delay; unsupported hosts offer a command to review.'],
    ['Model auction', 'Choose 2–6 declared routes and an explicit cost cap, preview the estimate, then confirm paid calls. Failed calls and unknown spend remain in the receipt. No winner or future model change is automatic.'],
    ['Reasoning', 'Your main model, its reasoning setting and reasoning text are preserved. Cheaper execution comes from explicit subtasks, fewer repeated reads and measured trials.'],
    ['Whole-text subtasks', 'For large artifacts, ask for one complete text subtask. On hosts without guarded nested dispatch, input_files and save_to return explicit read/write plans for normal Hermes tools. The main model reviews the result and keeps decisions and the final answer.'],
    ['Global settings', 'Chat controls apply to one chat; Cron controls apply to the selected job. Any global tool configuration is a separate, explicit choice in Hermes.'],
    ['Compatibility', 'Model choices come from configured and user-declared routes. The public SDK does not provide authenticated inventory. Hermes checks authentication on an actual request; prices are public list prices or your own rate card, and unsupported automatic replay uses normal host-tool plans.']
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
  cron: 'Crystallize Cron tests cheaper settings for scheduled jobs. Review each trial, its checks and the cost receipt.',
  chat: 'Crystallize Chat works to lower the cost of the chat you\'re in. You turn it on for each chat, and your main model can send routine work to a cheaper model you pick.'
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
            const target = promotionTarget(it, res.compatibility)
            diag('promotion popup', { routine: it.routine_id, mode: target && target.mode })
            host.notify({
              kind: 'info',
              icon: 'ruby',
              title: target ? 'Read plan ready' : 'Read plan needs capability review',
              message: `${it.name} matched ${it.matches} normal-run observations. Review its host capabilities before choosing an execution mode.`,
              meta: it.saving_per_week ? `Estimated saving ${money(it.saving_per_week)}/wk` : undefined,
              persistent: true,
              action: target ? {
                label: target.label,
                onClick: () =>
                  ctx.rest('/promote', { method: 'POST', body: { routine_id: it.routine_id, mode: target.mode } })
                    .then(r => host.notify({ kind: r && r.ok ? 'success' : 'warning', message: (r && r.message) || 'Done.' }))
                    .catch(err => host.notify({ kind: 'error', message: `Crystallize: ${String(err)}` }))
              } : undefined,
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
