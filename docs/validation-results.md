# Validation results

These results distinguish the current follow-up and public-SDK migration from the earlier
implementation in this follow-up to PR #7. They do not establish universal model
compatibility, semantic equivalence or marketplace admission.

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
