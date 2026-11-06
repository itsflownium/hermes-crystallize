# Optional Model Auction

Model Auction is an explicitly started, answer-only comparison of 2–6 routes from
Hermes's current connected model inventory. The user chooses every provider and
model, the maximum output tokens, and a dollar cap. Opening a panel, previewing a
selection, reading history, or reading a receipt makes no answer-generation calls.
There is no automatic model selection or automatic winner.

Every route receives one copy of the same complete frozen benchmark: the current
job prompt, the answer-only system instruction, and one completed recorded
execution's supported read-only observations. The benchmark hash includes the
prompt. The separate `source_input_sha256` identifies the recorded observations.
Arguments and results must be complete and attributable to that execution;
truncated, unfinished, or ambiguous evidence is rejected. Recorded actions are
excluded and never replayed. The original benchmark and contract are stored in
the profile's plugin database before calls start, without export redaction.
Public results and history use the normal credential-redacting export path.

The only generation entry point is the registered public
`ctx.llm.complete(messages, provider=..., model=..., max_tokens=..., timeout=...,
purpose=...)`. Auctions do not instantiate a host agent, invoke provider clients,
retry calls, pass tools, change main/helper model settings, select a different
authentication profile, or override reasoning effort. Hermes retains its routing,
authentication, and policy gates. If the reported served route differs from the
selected route, the answer and expense remain visible as a failed comparison;
it is not attributed to the selected model. Canonical aliases therefore may need
manual review.

## Permission and cost cap

Hermes must already grant both request overrides in its normal configuration:

```yaml
plugins:
  entries:
    crystallize:
      llm:
        allow_provider_override: true
        allow_model_override: true
        allowed_providers: [your-connected-provider]
        allowed_models: [your-selected-model-one, your-selected-model-two]
```

The `allowed_providers` and `allowed_models` lists are optional Hermes restrictions.
When present, the auction honors them. An empty list allows no selections; `"*"`
has Hermes's wildcard meaning. No auction code writes these grants. The grant is
checked before inventory and pricing access and before each subsequent model
call. A lookalike `allow_request_overrides` field does not authorize routing.

Before the first call, all routes must have known exact-route input and output
prices from Hermes. API-equivalent prices borrowed from another provider do not
satisfy admission. Explicit known zero prices are valid. The reservation uses
twice the UTF-8 byte count of the full message envelope plus 1,024 estimated input
tokens, the maximum output token count, the highest known context-tier/cache
rates, and any per-request fee. The sum of every selected route's reservation
must fit the cap. This byte-based estimate is deliberately generous; it is not a
tokenizer measurement or a guarantee about hidden provider input.

The cap is a local admission and spending-ledger limit, **not a provider billing
guarantee**. Providers can add reasoning tokens, hidden input, pricing changes,
fallback routes, or billing adjustments. `max_tokens` cannot guarantee the final
bill. No reasoning override is applied to force a cheaper result.

The default mode, `accept_estimated_cap: false`, accounts for host-reported actual
billed dollars. If actual spend is unknown, it stops before another route is
called, even if a host estimate is available. The current public Hermes facade
may expose token counts without billed cost; strict mode can therefore stop after
its first answer.

An additional explicit opt-in, `accept_estimated_cap: true`, allows an **estimated
cap**. When actual cost is absent, this mode requires measured positive input and
output token counts and computes a conservative exact-route API-equivalent cost,
including reported cache/reasoning tokens. It uses the larger of that value and
any reported host estimate. Zero placeholder usage, unknown pricing, or an
unverified served route still stops later calls. Estimated amounts remain labeled
as estimates; `actual_spend_complete` stays false and `actual_usd` stays null.

After each response or failure, the ledger checks both accounted spend and the
reservation for all remaining routes. An overrun stops later calls and is retained
without clamping. Each pending and failed paid call has a durable overhead receipt;
failure does not imply zero expense. A SQLite budget lock serializes auctions in
the active profile, including across processes. Normal completed/stopped auctions
release it. An interrupted call or persistence failure retains its lock and
pending record rather than silently expiring or retrying unresolved billing.
Such records require inspection and explicit reconciliation before another
auction in that profile. Each separately confirmed auction has its own cap; the
cap is not a daily or account-wide provider spending limit.

## Scoreboard and optional blind review

Each selected route has a row showing completed, failed, or skipped status, the
host-reported served identity, cost provenance, latency, answer hash, completion
signals, and deterministic outcome-contract checks. Full bounded answers and
failure details remain in the durable calls. Output reaching its token ceiling,
reported truncation/failure, or oversized output cannot be validated as complete.
Missing finish metadata is disclosed as an observability limitation.

Checks can enforce required fields, URLs, source/error preservation, or other
configured structural requirements. Passing them does not prove factual
correctness, semantic equivalence, or live-job quality. Answer-only calls cannot
satisfy action requirements. Every row explicitly sets `quality_proven: false`
and `efficiency_savings_claim: false`.

Successful complete pairs can be reviewed through the existing blind A/B API.
Their persisted random A/B mapping, route identities, prices, and checks are
withheld by that API until a choice or explicit reveal. Each pair has its own
immutable review ID. A human preference is additional evidence, not a semantic
quality guarantee. The ordinary auction scoreboard itself contains model names;
blind review is optional and most useful before inspecting those results.

Different models constitute a model comparison. Their price difference is never
reported as same-model efficiency savings or as savings from the full live tool
loop. All auction calls count as experiment overhead in receipts.

## Python API

```python
from crystallize import auction

routes = [
    {"provider": "your-provider", "model": "connected-model-one"},
    {"provider": "your-provider", "model": "connected-model-two"},
]
preview = auction.preview(
    job_id, routes, max_tokens=1024, cost_cap_usd=0.50,
    accept_estimated_cap=False,
)

# Only from a new explicit user confirmation of this selection and cap:
result = auction.run(
    job_id, routes, max_tokens=1024, cost_cap_usd=0.50,
    execution_id=preview["execution_id"],
    expected_input_sha256=preview["input_sha256"],
    accept_estimated_cap=False, confirmed=True,
)
saved = auction.get(result["id"])
previous = auction.history(job_id, limit=20)
```

`preview` returns `available`, `why`, the frozen benchmark identity, selected
routes and prices, per-route reservations, the total reservation, cap mode, and
limitations. It does not return a synthetic price when pricing is unavailable.
`run` rejects a changed benchmark before making calls when the expected hash is
supplied. Routes are exactly `{provider, model}` objects; extra override fields
are rejected. `max_tokens` is an integer from 1–4,096. The cap must be finite,
positive, and no greater than $100. History accepts limits from 1–100.

The result includes `calls`, `scoreboard`, `blind_pairs`, `budget`, immutable input
identities, and limitations. Normal terminal statuses are `completed`,
`stopped_unknown_spend`, `stopped_cap`, or `stopped_policy`. A process interrupted
before a durable terminal update remains `running` with a pending call and its
profile budget lock. `completed` describes finishing the bounded auction; inspect
individual call statuses and contract checks before evaluating any answer.
