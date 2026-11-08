# Validation results

These results distinguish the current follow-up and public-SDK migration from the earlier
implementation in this follow-up to PR #7. They do not establish universal model
compatibility, semantic equivalence or marketplace admission.

## Live savings at unchanged reasoning (version 0.6.0)

Paired runs through the real Hermes gateway: the same task, model and provider on both sides, with the reasoning read from each request as sent. A pair is marked invalid if those differ or a message fails. Costs are list prices applied to recorded usage, not bills: from Nous's public model list, and for Codex, which is subscription-based, from OpenAI's public price for the same model. Order is randomised per pair, and every attempt is kept. Fixtures are synthetic and samples are small. Two providers: Nous and OpenAI Codex.

| Test | Model and reasoning | Pairs | Off | On | Saving | Checks passed, off / on |
| --- | --- | --- | --- | --- | --- | --- |
| Working chat, 11 messages | `tencent/hy3`, medium | 3 | $0.0374 | $0.0286 | 23% | 3 / 3 |
| Same, repeated on the final code | `tencent/hy3`, medium | 3 | $0.0379 | $0.0293 | 23% | 3 / 3 |
| Working chat, 23 messages | `tencent/hy3`, medium | 2 | $0.0450 | $0.0308 | 32% | 2 / 2 |
| Working chat, 11 messages | `moonshotai/kimi-k3`, high | 2 | $0.1824 | $0.1462 | 20% | 2 / 2 |
| Working chat, 11 messages | `deepseek/deepseek-v4.1-flash`, high | 3 | $0.0139 | $0.0129 | 7% | 3 / 3 |
| Short tasks in 7 domains, Clean only | `tencent/hy3`, medium | 14 | $0.0203 | $0.0191 | 6% | 14 / 14 |
| Cron, drafted script, 4 checks with one change | `tencent/hy3`, medium | 1 | $0.0065 | $0.0026 | 60% | not checked |
| Working chat, 11 messages, Codex | `gpt-5.6-terra`, medium | 2 | $0.2309 | $0.2008 | 13% | 2 / 2 |
| Working chat, 11 messages, Codex | `gpt-5.6-luna`, medium | 2 valid of 3 | $0.0217 | $0.0176 | 19% | 2 / 2 |
| Cron, drafted script, 4 checks with one change, Codex | `gpt-5.6-luna`, medium | 5 | $0.0473 | $0.0303 | 36% (pairs: 49%, 21%, 49%, 50%, 8% more) | not checked |
| Hand-off to a cheaper helper, Codex | `gpt-5.6-luna` to `gpt-6-luna` | 3 | $0.0357 | $0.0440 | **23% more** | 3 / 3 |
| Same, after returning a preview instead | `gpt-5.6-luna` to `gpt-6-luna` | 3 | $0.0379 | $0.0446 | **18% more** | 3 / 3 |

Things that came out badly or unclear, which are kept in `docs/evidence/`:

- **Removing a result by hand in a short chat cost more** when the AI needed it: 1.22, 1.74 and 1.61 times the cost for analysis, coding and research, and 0.67 times for logs. One research check failed. This is why automatic removal counts the chance of a read-back.
- **One DeepSeek pair cost 8% more.** Removal doesn't pay on near-free cached reads, so only Clean ran there.
- **A script that printed the data cost 21% more** in one cron pair, despite skipping half its checks. Scripts now print only a fingerprint. An earlier pair with that script saved 43%, so the variation is wide.
- **The first chat pilot is not a fair measure of Clean.** It ran before Clean's fix for data mentioning a failure.
- **A `tune` trial ran 27% cheaper in typical runs** on Nous ($0.00164 against $0.00224), but hadn't reached its decision when the 15-minute test ended. On Codex a trial did reach its decision: it reverted and restored the job's settings, because its 2 trial runs didn't show a saving beyond normal variation.
- **Hand-offs cost more in both live tests.** The main model often wrote the helper's answer out itself anyway, even when it only got a preview. Hand-offs are now off by default, and they stop in a chat if an answer is retyped.
- **One Codex cron pair cost 8% more.** In that run the normal job hit the provider's cache on 3 of its 4 runs, and the attached job's 2 woken runs missed it. A fast test schedule favours the normal job's cache, so this is the worst case rather than the typical one.
- **Context compression, live on Codex:** after `/compress` took a chat from about 22,000 to 10,000 tokens, Crystallize was still on, the removed result stayed removed, and the AI read the original back. This Hermes compresses a manual `/compress` in place; automatic compression into a new session is covered by offline tests. In one run the compression summary dropped the note naming the removed result, so the AI needed the result id from the panel to read it back.
- **Codex's cached reads cost a tenth of normal input,** so automatic removal didn't trigger there, and the Codex chat savings come from Clean alone.
- **Re-read on its own shows no clear saving** beyond what Clean gives: median cost ratios of 0.77, 1.00 and 1.07 across 3 task types, at 3 pairs each.

- **Crystallize's own tools used to cost every chat.** Its 4 tool definitions, about 3,300 characters, were sent on every AI call in every chat, even with Crystallize off. They're now left out unless Crystallize Chat is on in that chat, and where they're sent they count against its savings. This was found after the runs above, so those runs included them in both arms.
- **A hand-off test couldn't run.** The Nous account ran out of credits (HTTP 402), so hand-offs remain untested live.

Not measured live: providers other than Nous and Codex, Blind Taste Test, Model Auction, Rhythm, Runaway Brake on this version, automatic compression into a new session, and how a real user's work compares with these fixtures.

Reproduce with `scripts/validation_whole_loop.py`, `scripts/validation_cron_pair.py`, `scripts/validation_cron_tune.py` and `scripts/validation_compression.py`.

## Final activation and CI record

PR #18's Python 3.13 CI job passed **1,309 tests with 2 optional source-backed
cases skipped**. Separate helper SDK conversion checks passed on both reviewed
host snapshots; all six required PR checks were green. Exact-pin host validation
passed 16/16 checks with no warnings.

The [installed-build record](evidence/current-installed-build.json) confirms
v0.5.3, a clean fast-forward checkout, the reviewed feature's exact runtime bytes,
a successful public plugin reload while idle, removed validation jobs, and an
unchanged model/reasoning config file. The observed checkout commit is retained
as an as-of observation; subsequent evidence/catalog-only commits do not change
the runtime checksum or require another gateway restart.

## Completed scheduled functional review

The [read-only receipt review](evidence/current-native-functional-review.json)
verifies the separately reviewed **0.5.2** monitor's actual due schedule:

| Scheduled stage | Verified result | Model requests |
| --- | --- | ---: |
| First complete snapshot, value 42 | Correct JSON, exact source label, no action claimed | 1 |
| Unchanged complete snapshot | Local `no_change` document; no model request | 0 |
| Changed complete snapshot, value 43 | Correct JSON, exact source label, no action claimed | 1 |

The baseline retained two value-42 answers. It was held paused during the final
changed-data stage to stay inside the authorized allocation. This is a functional
first/unchanged/changed check, **not a paired three-tick cash-savings benchmark**.
Six cron model calls in total include the two baseline answers, the original
incorrect fallback answer, the superseded 0.5.1 answer and the final two answers.
Nothing is dropped from overhead to produce a saving.

The [strict controller](evidence/current-native-v052-controller.json) remains
failed on its reasoning-evidence assertion. The host session `model_config`
contained only `_usage_anchor`, without a persisted reasoning choice. The jobs
were explicitly pinned to max, and the gateway construction logs report max;
per-request wire/effective reasoning is not established by those records. The
separate review therefore passes the observable functional checks and keeps the
settings/cost comparison ineligible. It made no new model calls and did not
change the original controller's outcome.

The [cumulative reservation ledger](evidence/current-validation-budget.json)
retains **US$2 original + US$3 additional reservations**, entirely Nous-only.
Actual billing remains unknown. No further paid test is funded by the remaining
allocation. The test jobs were separately identified, finite and local, with no
existing production job or config edit; cleanup is recorded independently.

## Failure diagnostics — version 0.5.3

The original failed answer also showed why fallback metadata must be distinct
from task input: a numeric diagnostic nonce was returned as the requested value.
The final error-path guard labels the payload `diagnostic_only`, sets
`snapshot_available: false`, uses a nonnumeric nonce, and tells the normal agent
to verify the task or state that verification is unavailable. It does not claim
to prove semantic correctness of arbitrary model answers.

This diagnostic-only change is covered by offline source-failure regressions.
The paid successful monitor path below was exercised on version 0.5.2, whose
complete-source success path is unchanged. It is not relabelled as a paid rerun
of 0.5.3. The [exact-pin verification](evidence/release-v053-verification.json) passed all
16 host checks with no warnings and zero public-source audit findings. Its own
[scheduled script-only check](evidence/release-v053-native-verbatim.json) passed
with zero provider calls. Anonymous checks verified the manifest bytes and four
screenshots at the exact pin. Both validation CLIs refuse existing report paths before
starting any job or model call.

## Catch-up correction — version 0.5.2

Hermes records missed schedule slots during paused/late catch-up without making
a model call. The first 0.5.1 controller treated that receipt as a failed attempt
and stopped, even though the fixed source's scheduled model execution completed.
That [controller record](evidence/current-native-v051-controller.json) remains
failed; it is distinct from the underlying job outcome.

The monitor also included missed slots in its retry hash, which could force
inference solely because the schedule was late or paused. PR #17 removes those
slots from the retry anchor for explicitly approved complete latest-state
snapshots with no per-run delivery/deadline/action requirement. Actual failed,
unknown and cancelled executions still change the hash and force a fresh run;
missing ledger evidence still disables suppression. Regression checks and all
six CI checks passed. A new copied runtime requires a new explicit review.

## Pause/resume correction — version 0.5.1

The first live monitor run exposed a real lifecycle defect. A job created paused
has `paused_reason: "Created paused; awaiting operator approval."`; resume clears
that field. Version 0.5.0 mistakenly protected it as task configuration, so the
wrapper emitted a verification fallback instead of the requested snapshot. The
model then answered with the fallback nonce as the value, which failed the task.
The [reproduction record](evidence/native-pause-resume-reproduction.json) confirms
that restoring only the original pause reason reproduced the approved hash.

PR #16 treats this field as scheduler bookkeeping while continuing to protect
prompt, model, provider, reasoning and source ownership. Its **66 focused tests**
and six CI checks passed, including the full suite and both pinned host admission
checks. Copied runtimes remain
immutable: their approval now records a runtime SHA, and older/unverified
wrappers show an explicit Undo/review/attach requirement.

The [original controller result](evidence/current-native-initial-deadline.json)
remains failed on its deadline. The scheduled model request eventually completed
with an incorrect answer and reported 18,043 output tokens, including 18,011
reasoning tokens. That cost/latency overhead is retained. A separate continuation
preflight failed before making any calls because it expected `model_provider`
instead of the host job record's `provider` field; its
[zero-call failure](evidence/current-native-continuation-preflight-failure.json)
is preserved too. These outcomes are not discarded or converted into successful
savings evidence.

The installed copy was fast-forwarded cleanly, and the public gateway plugin
reload confirmed Crystallize's hooks/commands were registered while idle.
[Activation](evidence/current-plugin-activation.json) used no gateway restart.
The version 0.5.1 scheduled follow-up requires a fresh explicit source review;
it does not rewrite the old wrapper or infer a cash-saving percentage.

## Native-source release — 7 November 2026, version 0.5.0

PR #15 added reviewed native snapshot sources through public Hermes cron CLI
controls. The feature suite passed **1,300 tests, 1 optional audit case skipped**;
all six PR checks passed. The installed Hermes validator passed all 16 checks
with no warnings. This is separate from the earlier migration results below.

### Current Nous calls, unchanged requested reasoning

The current cleaner was exercised on `nous / deepseek/deepseek-v4.1-flash` with
requested **max** reasoning, temperature zero, the same output limit in both
arms, and no fallback route. A public pre-request observer recorded
`reasoning: {enabled: true, effort: max}` for both arms of the retained pilot and
cron-v2 pair. This proves the constructed request setting; the server's internal
reasoning policy is not exposed.

| Retained pair | Baseline prompt / output / reasoning tokens | Candidate prompt / output / reasoning tokens | Observable checks |
| --- | --- | --- | --- |
| Research pilot | 355 / 185 / 114 | 331 / 230 / 159 | Both passed |
| Typed cron delta v2 | 336 / 219 / 170 | 312 / 406 / 357 | Both passed |

Reasoning tokens are included in the reported output tokens; do not add them
again. In both pairs the prompt was smaller but output/reasoning grew. Neither
pair establishes a net saving. Cache zeros from the public helper facade do not
establish measured cache behavior. Actual billing and qualified cost estimates
were unavailable: **no cash-saving percentage is claimed**.

See the retained [pilot](evidence/current-nous-pilot.json) and
[cron-v2 pair](evidence/current-nous-cron-v2.json). These are answer-only checks of
synthetic, heavily indented JSON, not full normal-chat benchmarks. The cleaner's
source hash is retained rather than relabelling calls as a later commit.

The first current six-domain matrix completed 12 calls across research, writing,
data, analysis, coding and cron. Its observed summary passed both arms for five
domains. The cron candidate returned `action_performed: "none"` instead of a
boolean; that failure remains a failure. The original prompt named the field
without specifying its type. Fixture `cron-delta-source-error-v2` now explicitly
requires arrays and JSON booleans; both new answers passed the unchanged strict
checker. This new fixture is not an offline regrade of the original answer.

**Evidence incident:** an operator report-name expression overwrote the full
six-domain JSON while saving the v2 rerun. The earlier summary and exact cron
answers were recovered verbatim from saved tool output; full matrix request and
usage records were not recovered. The [partial record](evidence/current-nous-matrix-partial.json)
labels that limitation. It is not a recreated full report and is excluded from
savings calculations. The harness now exclusively claims a new report path
before starting calls, preserving existing files and symlinks.

### Native cron execution

The [scheduled verbatim check](evidence/current-native-scheduled-verbatim.json)
used a temporary profile, public CLI commands and a real due schedule. The
execution ledger recorded `source: builtin`, `status: completed`, and a
`scheduled_instant`; expected local text was present and model/reasoning fields
were preserved. It requested no inference. The runtime source SHA identifies the
actual code tested. This test does not establish arbitrary learned-tool replay.

The monitor comparison, installation verification and budget ledger are recorded
in the release follow-up alongside this section. Snapshot scope requires complete
reviewed input and no per-run action, reminder, deadline or required repeated
delivery. Provider-independent implementation does not prove every provider or
task works live.

## Current candidate after PRs #11 and #12 — 7 November 2026

- The follow-up suite passed **1,253 tests, 1 optional dispatch-audit case
  skipped**, with the reviewed helper SDK source supplied. It includes 14 new
  usage-boundary/reply-recovery cases and 32 desktop behavior checks.
- Six PR checks passed: Python 3.11/3.13, admission on both reviewed host
  snapshots, and actual helper usage conversion from both SDK snapshots.
- The local validator passed 16 checks with zero warnings in an isolated home;
  the independent public API audit found no production private access.
- These are offline/source/admission results, with **zero new paid provider
  calls**. They establish no savings percentage, universal live task/provider
  coverage, or automatic zero-AI cron behavior.
- Workspace validation does not activate the separate installed checkout or
  restart the gateway. [Submission status](submission-status.md) records the
  separate evidence, budget, listing and activation boundaries.

## Review fixes — 7 November 2026, version 0.4.1

- **1,216 standalone tests passed, 1 optional source-audit case skipped** on
  Python 3.13. Thirty new cases reproduce and prevent the reviewed defects,
  including real default-config capture and independent-process SQLite writers.
- The new tests cover canonical main-request cache buckets, legacy/unknown usage,
  recoverable high-cache estimates, long/corrupt/oversized/evicted Cron evidence,
  failed and pending source results, rejected-summary costs and retained literals.
- Public output-buffering registration and legacy signatures are covered by
  local contract tests. These are not a native Desktop streaming transport test.
- **30 Node UI checks passed.** The refreshed React/Chromium fixture flow captured
  ten views at desktop/narrow widths, checked overflow, and had zero console
  errors. Service responses and calls were simulated; no provider was contacted.
- The local Hermes validator passed **16/16 checks with zero warnings**, using
  clean host source `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`. The independent
  public-API audit, JavaScript syntax and Git whitespace checks passed.
- CI additionally validates admission against reviewed host revisions
  `90d6b98fd86d20981ee06ddd5b7d93e4320f26a4` and
  `6b21d0564de0492e2ebd0d8f63fca86c03b5c892`; their actual results are recorded by
  the PR checks. This is not a complete SDK/platform/provider matrix.

No paid provider calls, production job changes or gateway restart were used.
Literal preservation does not establish semantic equivalence or factual quality.
Complete Cron captures are bounded and retained locally; missing evidence cannot
be promoted to success. Main model/reasoning remain unchanged.

## Previous public-SDK migration — 6 November 2026, version 0.4.0

- Standalone Python suite: **1,186 passed, 1 skipped** on Python 3.13. The skipped
  case is an optional source-backed audit; a separate invocation with the local
  Hermes source enabled passed all 151 selected checks, including that case.
- **30 Desktop behavior checks** passed. Real React/Chromium fixture captures
  verified the route/pricing and host-tool-plan surfaces, narrow-screen overflow
  and zero console errors. The tracked [review captures](../assets/review/README.md)
  visibly identify their simulated backend and contain no live savings evidence.
- The independent published-public-API source audit found **zero violations**.
  JavaScript syntax and Git whitespace checks passed.
- Local Hermes validation passed **all 16 checks with zero warnings**, including
  isolated registrations, security, no core override and Desktop surface checks.
  The reviewed public API policy is pinned to upstream `90d6b98`; the actual
  local validated host source is `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`.
  The catalog bundle retains a separate exact-feature-pin validation record;
  neither local validation nor the policy snapshot establishes maintainer approval.
- Regression coverage includes unknown/zero/endpoint/tier pricing, declared
  routes without authentication probes, no unguarded nested execution, pending
  file plans, observed host-run readiness, legacy recovery and unavailable skip
  controls. Main model/reasoning remain unchanged.

No paid provider calls, production job changes or gateway restart were performed
for this migration. Automatic nested replay/no-AI execution remain unavailable
on the audited public SDK. Ordinary host-tool plans preserve the normal agent's
tool/approval flow. The earlier live evidence below was not rerun or relabelled.

## Historical build: before public-SDK migration

The sections below record the earlier feature commit
`c2c15d8dfa62b3e075da78547669b165e2b4c523`, which retained private dependencies.
Its warning, counts and live results are preserved as historical evidence.

## Offline and host checks

- Standalone Python suite: **1,015 passed** on Python 3.13.
- Desktop behavior: **19 Node checks**, plus the Hermes-bundled React build in
  Chromium at 390px and 780px, seven screenshots and no browser console errors.
- JavaScript syntax and Git whitespace checks passed.
- Hermes 0.22.7 admission validation passed all 16 checks, including isolated
  registration, declared capabilities, security scan, no core overrides and
  Desktop SDK surface. Host readiness was reported as ready. Its private-import
  warning applied to that earlier build; it has been removed by the migration above.

## Nous Portal frozen-input checks

Twelve completed answer calls covered research, writing, data, analysis, coding
and a Cron delta/error scenario. Every call was served by
`nous / deepseek/deepseek-v4.1-flash`. Both arms used the same requested **max**
reasoning, registered validation task, temperature and output limit. Only JSON
whitespace changed. Prior reasoning was neither supplied nor compressed.

| Evidence | Baseline | Compacted input |
| --- | ---: | ---: |
| Reported input tokens, all six calls | 1,950 | 1,805 |
| Reported output tokens, all six calls | 4,769 | 3,075 |
| Observable checks after separately labelled checker correction | 5 / 6 | 6 / 6 |

These are single synthetic pairs, not a measured full-agent savings rate.
Output variation, cache effects and effective wire reasoning cannot be isolated
from these observations. Actual billed cost and host cost estimates were not
returned. **No dollar saving or quality advantage is claimed.**

The [original report](evidence/nous-frozen-original.json) is unchanged: its
original checker passed 9 of 12 answers. That checker incorrectly required
decimal numbers even though the analysis prompt asked for fractions; both arms
correctly returned `4/5` and `3/5`. Revision `2-analysis-rational-rates` accepts
bounded exact fractions. The [separate offline re-evaluation](evidence/nous-offline-reevaluation.json)
records the original checks, corrected checks, source-report hash and answer
hashes. It passes 11 of 12 answers and made no additional provider calls.
The remaining Cron baseline returned `action_performed: "none"` instead of the
expected boolean false; that type failure remains visible. No actions ran.

## Real handoff path

Four additional calls compared a Nous Kimi K3 baseline with the actual
`crystal_cheap` handoff path using Nous `tencent/hy3`, for writing and a restricted
coding task. Main and helper both requested max reasoning. Both baselines and
both helper outputs passed their observable checks; placeholder expansion
recovered the exact helper output, and worker shutdown completed normally.
See the [handoff smoke report](evidence/nous-handoff-smoke.json).

This checks real routing/input/timeout/recovery behavior. It is a **model-switch
smoke test**, not same-model efficiency proof or a complete coding-agent loop.
The generated pure function was checked by the fixture's restricted interpreter;
generated code and real actions were not executed. Live file-writing and native
Codex/Anthropic routes were not exercised. Their policy/provider-shape coverage
is offline, consistent with the Nous-only live authorization.

## Scheduled execution and budget

An isolated no-agent job fired through the real scheduler with a recorded
`source: builtin`, a real `scheduled_instant` and `status: completed`. It wrote a
local synthetic snapshot and used no model or external delivery. This checks
scheduled admission, not an AI cron job, production recipe or delivery chain.
See the [scheduled smoke report](evidence/scheduled-no-agent-smoke.json).

Two earlier isolated runtime attempts exited without usable responses while the
host attempted dependency preparation. They remain
[recorded as interrupted, billing unknown](evidence/nous-interrupted-attempts.json).
The operator then used the installed dependency runtime read-only with lazy
installation disabled. No gateway restart or production-job edit occurred.

The entire run reserved **US$1.80 against the authorized US$2 ceiling**, including
US$0.20 retained for those interrupted attempts. This is a conservative request
reservation, **not measured money spent or a provider-enforced billing limit**.
Both tests configured an explicit output cap through documented task
`extra_body` because this host omits an ordinary `max_tokens` argument on some
Nous routes. Public results still cannot prove the provider's effective cap or
wire reasoning. Each task remained within its 15-minute deadline.

No other providers, production jobs or real external actions were used.
