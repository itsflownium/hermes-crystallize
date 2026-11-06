# Chat and Cron controls

## Chat result policies

Open the Chat panel to choose **Exact**, **Clean** or **Summary**, change a tool
override, and supply the facts a summary must retain. Saving a policy never
enables Chat savings. Clean is the default and does not call a model. Files and
failed results remain exact. Summary is opt-in and keeps a session-owned original.

```bash
hermes crystal chat-policy SESSION_ID
hermes crystal chat-policy SESSION_ID --mode summary --focus "Keep incident IDs, severity, status and source URLs"
hermes crystal chat-policy SESSION_ID --tool terminal --mode exact
```

The main model stays unchanged. Helper calls record their cost, latency and
accepted/rejected/failed outcome by turn. Routing uses explicit user rate cards/public provider quotes and
observed helper behavior, without decomposing a quote into inferred rates. Unknown prices remain unknown, and generated output
is counted as avoided only when its placeholder actually expands. These are
estimates, not provider bills or guaranteed future savings.

## Outcome contracts

A contract is an explicit requirement for one immutable cron job ID. Configure
it from **Evidence and controls → Validation requirements**, or import JSON:

```json
{
  "version": 1,
  "required_urls": ["https://example.com/status"],
  "required_fields": ["incidents.*.id", "incidents.*.status"],
  "minimum_items": 1,
  "item_path": "incidents",
  "preserve_errors": true,
  "preserve_status": true
}
```

Use only requirements appropriate to the task. `required_fields` and `item_path`
refer to a JSON answer; a prose digest can use item counts, URLs and source rules.
`required_sources` accepts `{ "tool": "web_extract", "target": "https://example.com/status" }`.
`required_actions` additionally accepts `min_count` and an optional `target_field`,
for example `{ "tool": "send_message", "target": "#alerts", "target_field": "to", "min_count": 1 }`.
Action checks require affirmative success evidence, not just a matching tool name.

```bash
hermes crystal contract JOB --file contract.json
hermes crystal contract JOB
hermes crystal contract JOB --clear
```

Checks compare against current source/action evidence. Incomplete evidence fails
configured checks instead of asserting success. They do not establish that every
statement in an answer is correct. A configured failure prevents Keep, and
meaningful configuration/source drift disables the old optimization for revalidation.

## Same-input comparisons and receipts

```bash
hermes crystal compare JOB --preview
hermes crystal compare JOB --samples 1
hermes crystal receipt JOB
```

Preview and receipt do not call a model. Compare is an explicit paid action:
each sample makes two bounded answer-generation calls using the same frozen,
complete read observations. The default baseline uses the current Hermes main
route; the candidate uses the chosen Crystallize helper slot. Actual served
routes are recorded. Explicit candidate JSON (`--candidate file.json`) can
change prompt/model settings supported by Hermes's public LLM API; route
overrides require host permission. Reasoning/tool-loop changes are not measured
by this answer-only experiment.

No tool or mutation is replayed. Truncated or mismatched observations are rejected
before calling a model. Output differences and contract checks remain visible.
Comparison costs are overhead, not claimed historical savings.

Receipts preserve signed losses, failed runs, trial/helper/comparison overhead,
baseline execution IDs, known prices and uncertainty. Actual spend, estimates
and API-equivalent prices stay separate. Undo closes an optimization interval
without deleting history. Payback is unavailable when required costs are unknown;
an estimated break-even assumes future successful runs resemble observed ones.
Unlinked timestamp-only skips are not credited as proven completed executions.

## Optional fetch caching

```bash
hermes crystal cache JOB --seconds 120
hermes crystal cache JOB --seconds 120 --namespace public-release-feeds
hermes crystal cache JOB --seconds 0
hermes crystal cache --clear
```

Caching is off by default. Successful `web_extract`, `web_search` and `web_crawl`
results can be reused within a TTL of at most one hour. The default namespace
isolates each job; an explicit namespace allows jobs in the same profile to share
matching permitted reads. Credential-bearing arguments and local file/shell
reads are excluded. Unknown result types or cache errors use the normal tool.

The cache runs in public `tool_execution` middleware, after normal host tool
availability/approval/pre-tool checks. A cache hit does not bypass those checks.
Profiles have separate databases; expiry and a bounded entry count limit retention.

## Explicit parameterized read recipes

An advanced recipe lets an earlier result supply a later argument instead of
freezing a changing ID. JSON expressions are data, never Python or shell source.

```json
{
  "version": 2,
  "leaves_to_ai": true,
  "steps": [
    {
      "id": "latest",
      "tool": "web_extract",
      "args": {"urls": ["https://example.com/latest.json"]}
    },
    {
      "tool": "web_extract",
      "args": {
        "urls": [{"$url": {
          "template": "https://example.com/items/{id}",
          "values": {"id": {"$result": {"step": "latest", "path": ["id"]}}}
        }}]
      }
    }
  ]
}
```

The path must match the tool's actual JSON result wrapper. It is not inferred:
for a nested object use a path such as `["results", 0, "id"]`.

```bash
hermes crystal recipe JOB recipe.json
hermes crystal test JOB
hermes crystal promote JOB --mode tool
```

Import starts a new observation period and never overwrites a live routine.
On the audited public SDK, `try`/`test` inspect fresh normal-host scheduled runs;
they do not execute a shadow recipe or make extra model calls. Matching complete
observations, current configuration and safe explicit bindings are required
before explicit `promote --mode tool`. That mode installs recipe guidance: the
main agent calls `crystal_run` to obtain a **pending, non-executing host-tool plan**,
then makes/validates each ordinary tool call under normal host approvals.

No read prefix is automatically replayed. The default pre-run/script and no-AI
modes are unsupported because the public dispatch implementation does not verify
the full guard pipeline. `leaves_to_ai: true` preserves all remaining task work
and reasoning. Plans do not constitute results or successful actions.

Other binding values:

- `{"$date": {"offset_days": -7, "timezone": "Australia/Sydney"}}` preserves
  relative date windows in the specified timezone. `format` can be `date` or `datetime`.
- `{"$artifact": "feed.json"}` names one file in a fresh run-local directory.
  The normal agent must choose an authorized scratch location and use the same
  resolved path for every producer/consumer. The public-only plugin does not
  create, read, snapshot or remove artifacts automatically. Inspect complete
  ordinary tool results; missing/oversized/binary/linked artifacts require the
  normal agent to handle the task, not an invented successful snapshot.
- `{"$argv": ["curl", "--output", {"$artifact": "feed.json"}, "https://example.com/feed"]}`
  describes quoted shell arguments. Executables stay literal; the normal agent
  must resolve bindings from fresh evidence and use its ordinary guarded tool.
  The plugin does not launch this command. Arbitrary bound code and literal
  scratch writes are rejected as recipe candidates.

Never pass unresolved binding objects directly as actual host-tool arguments.
The plan marks required fresh bindings; the main agent resolves and verifies them
or finishes the original task itself.
Legacy implicit date/ID/temp substitution is rejected at execution; reimport
an explicit recipe rather than trusting a stale saved plan.

## Public route and pricing controls

`hermes crystal model-route` lists configured/declared routes with provenance, not
verified sign-ins. `--provider ID --model EXACT_ID` explicitly adds a route without
activating it. `hermes crystal rate-card` lists user estimates; `--file card.json
--confirm` saves a card with explicit currency, units and exact-route identity.
No credential store or private inventory is read. Unknown fees/cache/tier rates
remain unknown. See [Model Auction](model-auction.md) for budget admission and
[public boundaries](sdk-gaps.md) for unsupported features.
