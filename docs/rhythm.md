# Rhythm: optional schedule suggestions

Rhythm shows when a cron job's recorded source content changed and lets the user
review a less frequent interval. It never learns that a job is safe to slow down
merely because its inputs rarely changed. It never changes the model, provider,
reasoning level, prompt, tools, script or outcome contract.

The heatmap has one cell for every weekday and hour. Each cell reports actual
observations, consecutive comparisons, changes and unchanged comparisons. Monday
is weekday `0`. A completed, scheduled execution needs full SHA-256 result digests
and complete recorded arguments. Display previews are not used as content
fingerprints, so a change beyond a truncated preview still counts. Failed,
incomplete or unknown executions break the comparison chain. The first
observation establishes a baseline, rather than inventing a change.

Buckets use the time at which content was observed, preferring the latest recorded
tool timestamp. They do not establish when a source actually changed. Pass an
explicit IANA timezone such as `Australia/Sydney`; otherwise the report is visibly
labelled **UTC default**. No machine-local timezone is guessed. Capture gaps,
unobserved transient events, old task configurations and future changes remain
limitations of the evidence.

## Eligibility and explicit consent

Supported candidates are enabled, indefinitely recurring, plain interval jobs
whose tasks clearly request a snapshot or read. Alerts, reminders, deadlines,
monitoring, event-window/cursor queries, per-run actions, external delivery,
finite repeats, scripts, skills and unknown or executable tools are excluded.
Recorded content calls must be from the small supported read subset and pass the
existing argument-aware action guard. An active execution also blocks a change.
These are conservative gates, not a semantic guarantee about arbitrary prompts.

A proposal requires at least three complete observations and two consecutive
comparisons. A data-driven suggestion additionally needs twelve complete
observations spanning seven days. Suggestions are labelled as suggestions and
always require acknowledgement; a quiet history alone does not authorize a
schedule edit.

For every change, the user must explicitly acknowledge:

> This job is a noncritical, latest-state snapshot. It does not need every
> transient event, notification, deadline or action, and the stated maximum
> staleness is acceptable.

The allowed staleness must be at least the proposed interval. Rhythm supports
equal or longer whole-minute intervals. A task requiring every event, exact
delivery timing or an action on each invocation should keep its existing cadence.

## API

All job identifiers are exact stable IDs. Display names and ambiguous prefixes
are not accepted.

```python
report(job_id, timezone="UTC")

preview(
    job_id, interval_minutes,
    timezone="UTC", noncritical=False,
    allowed_staleness_minutes=None, horizon_days=30,
)

apply(
    job_id, interval_minutes,
    expected_config_fingerprint=preview_result["config_fingerprint"],
    noncritical=True, allowed_staleness_minutes=120,
    confirmed=True, timezone="UTC",
)

undo(
    job_id,
    expected_config_fingerprint=current_report["config_fingerprint"],
    confirmed=True,
)
```

`report` and `preview` do not save consent or change schedules. Their shared
response includes `job_id`, `job_name`, `timezone`, `timezone_source`,
`config_fingerprint`, `current_interval_minutes`, `heatmap`, `evidence`,
`eligibility`, optional `recommendation`, `acknowledgement` and `control`.

Each heatmap cell has `weekday`, `hour`, `observations`, `comparisons`, `changes`
and `unchanged`. Empty cells are explicit zeroes. `evidence` includes counts,
observation boundaries, span, fingerprint source and limitations.

`preview` also returns `interval_minutes`, `frequency_factor`, `blockers`,
`can_export`, `can_apply`, an eligible `export` object with an argv list and
shell-quoted command, and `projection`. Blocked proposals return `export: null`.
The projection separates avoided scheduled invocations
from estimated avoided dollars. Invocation counts are rate projections for the
configured intervals over the requested horizon; schedule phase, pauses, retries
and later edits are not modeled. Dollar estimates use historical actual,
estimated or subscription-included per-run costs with provenance and sample
counts. Unknown costs stay unknown. Included billing contributes zero, and API
equivalent pricing is not presented as cash saved. These fields are never
measured savings.

## Schedule ownership and host support

Rhythm checks the public host capability with read-only
`hermes cron edit --help`. If `--schedule` is advertised, explicit confirmed
application invokes only:

```text
hermes cron edit EXACT_JOB_ID --schedule 'every 120m'
```

The child uses the active Hermes profile. Rhythm never writes `cron/jobs.json`
or calls a private host job updater. If the CLI is unavailable or does not
advertise schedule editing, `control.mode` is `export_only`. An approved request
then returns the reviewed command with `applied: false` instead of pretending it
changed the job. Exported commands must run in the owning Hermes profile.

Application validates the expected fingerprint, checks eligibility and consent,
then re-reads the current job immediately before dispatch. A detected edit stops
the operation. The host CLI has no atomic compare-and-swap option: an external
schedule edit within the narrow final check/call window can still race. This
limitation is returned in `control.concurrency_limit` and successful mutation
responses. Only `--schedule` is passed, so intervening changes to other settings
are preserved by the host's field-specific updater. Host interval editing may
recompute the next invocation's phase.

The original interval and intended installed interval are saved durably in
Crystallize's profile-scoped store before the host edit. A retry for the same
still-installed interval is idempotent. Unknown command outcomes retain recovery
data; they are not reported as confirmed success.

Undo requires fresh explicit confirmation and the expected current configuration
fingerprint. It restores the original interval only while the current schedule
still matches the interval installed by Rhythm. Renaming the job or changing its
prompt, model or reasoning does not move ownership to another job and does not
cause those fields to be restored. A newer user schedule is preserved. Repeated
Rhythm changes retain the original interval until Undo, and repeated completed
Undo makes no further host call. This ownership records interval semantics, not
the original next-run phase.

Tests use in-memory plugin storage, a temporary profile and an isolated fake CLI
editor. No paid models or live cron schedule changes are required.
