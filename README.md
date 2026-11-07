<p align="center">
  <img src="assets/crystallize-logo.svg" alt="Crystallize" width="380">
</p>

**Reduce repeated context in Hermes chats and cron jobs while preserving your main model and reasoning settings.**

Crystallize targets repeated context, duplicated file text, unnecessary tool
schemas and stable read steps. It keeps originals recoverable, records helper
overhead and shows when evidence is incomplete. It does not reduce the main
model's reasoning or reuse an earlier reasoning transcript as a shortcut.

The architecture uses Hermes's host-owned model facade and provider declarations
across cloud routes. This is a compatibility design, not a claim that every
provider, model, operating system or task has been tested. Missing prices,
capabilities, usage and guarded execution stay unknown or fail closed.

**Current evidence:** this public-SDK candidate has no verified live savings
percentage at unchanged reasoning. Research, writing, data, analysis, coding and
cron coverage comes from offline fixtures; it does not prove every mainstream
task or provider works live. Automatic zero-AI/pre-run routines are unavailable
on the reviewed SDK. Its older cron measurements cannot be carried over to this
implementation. See [submission status](docs/submission-status.md) for the
measurement, marketplace and local activation boundaries.

## Chat and Cron

| Control | What it does | Boundary |
| --- | --- | --- |
| Chat Exact/Clean/Summary | Keeps results exact, losslessly compacts JSON whitespace, or explicitly condenses large results with the selected helper | Summary can lose meaning; file contents and errors stay exact |
| Complete task handoffs | Sends supplied text to a helper; requested file reads/writes return ordinary-host-tool plans; owned placeholders avoid retyping | Main route stays fixed; plans are pending work, not completed I/O |
| Re-read savings | Opt-in projections of eligible replayed text, with recovery and a cache-aware payback check | Reasoning and the current thinking pass are untouched; projections are not semantic proofs |
| Tool discovery | Opt-in schema deferral with host-supported discovery | Unsupported/required tool paths retain normal schemas |
| Cron input tuning | Reduces repeated instructions/tool schemas at the same model and reasoning; restores failed trials | Changed settings and uncertain evidence do not become savings claims |
| Read recipes | Learns complete read plans from traces and returns them to the normal agent | Automatic nested replay/promotion is unavailable without a verified public guard contract |
| Blind review | Presents anonymous A/B answers before model/cost reveal | A preference and structural checks do not establish correctness |
| Model Auction | Explicit bounded model comparisons on frozen recorded evidence | Different-model selection is not same-model efficiency proof |
| Rhythm | Explains observed changes and previews an explicit noncritical schedule adjustment | Confirmation, sufficient observations and declared slack are required |

Chat controls apply to the chat where you enable them, after its first prompt.
Choose a configured, provider-declared or explicitly added Hermes route. Its
authentication and current availability are unverified until a requested call.
Hermes owns authentication;
Crystallize does not read keys or OAuth stores. Helper calls incur provider
usage, including unsuccessful attempts. The main model keeps its chosen route
and reasoning.

Start with **Clean**, which compacts redundant formatting without a model call.
Optional summaries are checked for structured numeric values, recognizable IDs,
dates, amounts and URLs. Failed or incomplete results stay exact. A summary that
fails those checks is rejected, while its attempt cost remains in the receipt.
These checks cannot prove factual correctness or semantic equivalence.

Originals belong to their originating chat and expire after seven days.
`crystal_full_result` retrieves exact text; asking a helper about it requires
chat opt-in. Requested file reads/writes return non-executing plans for the normal
agent to perform through its ordinary guarded tools.
Placeholders expand only in eligible text fields before policy checks, and
cannot become commands, URLs or file paths.
Unavailable or expired reply results produce a recovery notice containing their
result ID, while the rest of the reply stays intact. Recovery never starts a paid
helper call, and only successfully recovered text receives savings credit.
On hosts exposing the public output-buffering option, replies are delivered after
placeholder expansion; this can defer streaming for the profile while the plugin
is loaded. Older hosts retain their supported output-hook contract.

See [controls and recipes](docs/controls-and-recipes.md), [Rhythm](docs/rhythm.md),
[Model Auction](docs/model-auction.md) and [validation](docs/validation.md) for
exact controls and limitations.

## Evidence and cost

Crystallize separates actual cost evidence, host/list-price estimates, included
subscription usage and unknown values. An API equivalent is not a subscription's
cash cost. Token counts and character estimates are labelled separately. Helper
requests, retrievals, unsuccessful attempts, comparisons and cache-rewrite
overhead belong in net payback; a smaller visible message alone proves no saving.

Chat's one-read net is a **current-price projection**, including for earlier
turns. It applies the current main model's listed rates to recorded character
estimates, then subtracts recorded helper costs and estimated extra-call overhead.
The panel and API identify that route and its input/output rates. Changing the
main model or rate card reprices the projection; it does not change the recorded
events or establish historical billing. Missing helper cache counts use the
highest applicable rate for the unallocated input, explicitly marked as an
upper-bound estimate. Inconsistent counts or unavailable prices remain unknown.
At the public helper boundary, ambiguous zero counters also remain unknown:
reviewed Hermes SDK versions turn absent counts into zero without presence flags.
Positive counters are retained; reported host costs, including zero, take precedence.
Missing input/output counts leave the helper cost unknown rather than inventing a
free input or output bucket.

Cron trials require comparable recorded model/provider/reasoning and observable
outcome checks. Source, task, route, tool or contract changes require fresh
validation. The normal agent handles work while evidence is rebuilt. Contracts
catch missing fields, IDs, URLs, errors and actions; they cannot prove factual
correctness or semantic equivalence. Review answers and real side effects; a pending plan does not establish either.

The former README percentage tables were small, uncontrolled and confounded
observations. They did not establish fixed-reasoning, same-model savings for
this release and have been retired. Fresh evidence must name inputs, served
routes, requested reasoning, usage basis, failures and scope. Historical numbers
do not fill missing live results.

The reproducible harness covers research, writing, data, analysis, coding and
cron fixtures. It defaults to local checks without model calls. Optional live
calls use the public facade, fresh answer-only inputs, an explicit provider
allowlist, temporary HOME, reservations and process deadlines. Synthetic JSON
indentation makes its compression ratio unrepresentative of normal workloads.
Passing it establishes observable conditions, not whole-loop quality or every
cloud provider's compatibility.

```bash
python scripts/validation_cross_domain.py --report work/validation-plan.json
```

The preserved 16-attempt Nous evidence in `docs/evidence/` comes from the prior
private-dependency build. It retains original failures, interrupted attempts,
unknown billing and separately labelled offline checker correction. The isolated
no-agent scheduler smoke proves only that narrow path. These records are not
revalidation of this public-SDK-only revision; no paid rerun was used to replace
that evidence during migration.

## Install and use

Install from the published repository; this installs its default revision.
Catalog-name listing is not established. The manifest load floor is Hermes
`>=0.22.7`; optional capabilities remain gated and this does not promise every
feature on every host. Validate the exact pin and retain unsupported-feature
notices on the version in use.

```bash
hermes plugins install itsflownium/hermes-crystallize
hermes plugins enable crystallize
```

In Hermes Desktop, enable Crystallize under **Capabilities → Plugins**. The
panel has Cron and Chat sections. Command surfaces also work outside Desktop.
Desktop uses the plugin SDK; a complete OS/provider test matrix is not claimed.

```text
/crystallize chat on                         # enable this existing chat
hermes crystal history                       # recorded cron outcomes and usage
hermes crystal tune morning-brief             # trial input savings; reasoning stays fixed
hermes crystal tune morning-brief --status
hermes crystal tune morning-brief --undo
hermes crystal analyze morning-brief          # inspect repeated read steps
hermes crystal try morning-brief              # save/test a candidate routine
hermes crystal show morning-brief
hermes crystal promote morning-brief --mode tool  # explicit normal-host-tool plan after evidence
hermes crystal undo morning-brief
hermes crystal compatibility                  # SDK gaps; no provider probes
```

Use `hermes crystal <command> --help` for re-read controls, anonymous comparison
review, Model Auction, Rhythm and optional delegation/tool discovery. Views and
previews do not make paid model calls. Comparisons and schedule changes have
explicit actions. Legacy lower-reasoning trials are restored and require fresh
baseline evidence; reasoning reduction is refused.

## Routes and user rate cards

The model picker shows configured, public provider-declared and explicitly added
routes, with their provenance and unverified access status. Adding a route does
not sign in, activate delegation or change the main/helper model. A host-owned
call checks actual access when explicitly requested.

```bash
hermes crystal model-route
hermes crystal model-route --provider your-provider --model exact-model-id
hermes crystal rate-card
hermes crystal rate-card --file exact-route-rates.json --confirm
```

Rate cards are explicit user estimates with declared currency/units and exact
provider/model/endpoint identity. Public provider total quotes remain separate;
unknown cache rates, request fees and tiers are not inferred. Cards never prove
actual billing. See [Model Auction pricing](docs/model-auction.md) and
[public SDK boundaries](docs/sdk-gaps.md) before authorizing a budgeted comparison.

## Routines and recovery

A routine contains only the conservative read subset before the first action.
The current public dispatch implementation does not verify the complete guard
pipeline, so automatic nested execution, routine shadow replay and pre-run/no-AI promotion
are unavailable. Explicit `promote --mode tool` installs normal-agent recipe
guidance after matching normal-run observations. Read recipes return pending guidance for the main agent's ordinary
tools. Method presence or a user setting does not enable raw dispatch. A read
recognizer remains a conservative check, not a sandbox.

Complete recorded evidence, source order, nested JSON, errors and timestamps
remain available. Oversized/unrecognized evidence falls back to the normal
agent rather than silently truncating it. Parameter bindings are explicit;
task/endpoint/schema drift requires fresh normal-agent evidence. Legacy wrapper
attachments are restored through supported public CLI fields. Unsupported toolset
restoration retains recovery records and requests an ordinary host-tool action.
Schedule changes remain explicit choices.

## Disclosures and catalog readiness

- Reads profile-scoped session history/costs (`state.db`), cron outcomes
  (`cron/executions.db`), job definitions (`cron/jobs.json`), configuration,
  public provider declarations and explicit route/rate-card configuration.
  History reads are read-only; route lists do not probe credentials.
- Stores captured arguments/results, originals, caches, contracts, comparison
  answers, receipts and diagnostics under
  `<hermes home>/plugin-data/crystallize/`. Originals can contain sensitive text.
  Complete Cron evidence is additionally bounded to 256 payloads of at most
  500,000 UTF-8 bytes each; previews remain separate, and evicted evidence fails
  completeness checks.
  Receipt export redacts common credential forms; arbitrary sensitive content
  is not guaranteed to be removed.
- Helper/comparison features send supplied data through a host-owned model
  route on the relevant opt-in/explicit action. Public provider quote logic may
  consult host-owned metadata; explicit rate cards remain estimates. There is no independent credential
  reader or telemetry service.
- Automatic nested read replay and file execution are disabled on the audited
  host. Plans leave these calls to the normal agent's ordinary approved tools.
  Background text helpers are bounded; timeouts can still incur provider usage.
- Explicit tuning/recovery edits supported cron fields through public CLI
  children scoped to the profile and stripped of the verified YOLO bypass.
  Unsupported toolset edits become normal-agent plans; automatic pre-run/no-AI
  promotion is refused. Rhythm changes schedules only through its confirmed path.
- Fetch caching is opt-in, profile-scoped, time-limited and applied after normal
  tool policy checks. Shared namespaces need matching authorization/freshness.
  Credential-bearing arguments are not cached.
- Private Hermes pricing/inventory/dispatch/cron-update dependencies have been
  removed. Public provider total quotes and explicitly supplied exact-route user
  cards preserve unknown prices; no rates are inferred from totals or another
  provider. [Public boundaries](docs/sdk-gaps.md) explain unsupported features and
  the independently audited, hash-pinned public API contract.
- No self-updater, token-refresh implementation, credential store or runtime
  patch of Hermes core is introduced. The [catalog bundle](catalog/README.md)
  is preparation only. [Admission](docs/marketplace.md) needs a separate
  upstream PR, exact-pin validation and maintainer merge; no listing or
  marketplace compliance is claimed.

## Develop

Standalone tests use isolated SQLite storage and local fakes, without a
signed-in Hermes profile:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -c tests/pytest.ini --rootdir tests tests
node --check desktop/plugin.js
```

Host checks can skip when Hermes modules are absent; skipped checks are not
provider passes. Validate a reviewed exact commit with
`hermes plugins validate /absolute/pinned-checkout --install-deps` and retain all
warnings. The [validation guide](docs/validation.md) documents the separate
root-authorized live workflow and billing/reasoning limits.

MIT licensed.
