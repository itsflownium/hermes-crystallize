# Validation results — 6 November 2026

These results cover the implementation in this follow-up to PR #7. They do not
establish universal model compatibility, semantic equivalence or marketplace
admission.

## Offline and host checks

- Standalone Python suite: **1,015 passed** on Python 3.13.
- Desktop behavior: **19 Node checks**, plus the Hermes-bundled React build in
  Chromium at 390px and 780px, seven screenshots and no browser console errors.
- JavaScript syntax and Git whitespace checks passed.
- Hermes 0.22.7 admission validation passed all 16 checks, including isolated
  registration, declared capabilities, security scan, no core overrides and
  Desktop SDK surface. Host readiness was reported as ready. Its private-import
  warning remains; see [SDK gaps](sdk-gaps.md).

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
