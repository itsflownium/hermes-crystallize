<p align="center">
  <img src="assets/crystallize-logo.svg" alt="Crystallize" width="380">
</p>

**Spend less on repeated work in Hermes chats and cron jobs, without making your model think less.**

Most of what an AI agent pays for is re-reading: every call re-sends the chat, and a scheduled job
does the same work on every run. Crystallize finds that repetition and removes it. Your model and
reasoning stay exactly as you set them, so a lower bill never comes from a weaker answer.

## What it does for you

- **Skip cron runs when nothing changed.** A small script reads the job's data, and the AI only runs
  when that data changes. Crystallize can draft the script from what the job has already done, and
  you review it before it's attached.
- **See what a chat keeps paying to re-read.** Context Rent shows what each old tool result costs on
  every call, and lets you remove it. The AI can still read the original.
- **Tidy tool results before the AI reads them.** Clean removes wasted formatting with no AI call.
- **Test lower-cost settings on a cron job safely.** A trial only stays if its checks pass and it used
  less, beyond normal variation. Otherwise your earlier settings come back.
- **Stop runaway cron runs.** Runaway Brake stops a run far past that job's own normal use.
- **Compare models and schedules on your own evidence**, with Blind Taste Test, Model Auction and Rhythm.

## Quick start

```bash
hermes plugins install itsflownium/hermes-crystallize
hermes plugins enable crystallize
```

It works the same in the classic CLI (`hermes chat`), the TUI (`hermes chat --tui`) and Hermes Desktop.

### In the CLI and TUI

Type `/crystallize` in any chat. Everything the Desktop panel shows is there as text:

```text
/crystallize                      what this chat and your cron jobs have saved
/crystallize chat on|off          turn Crystallize on or off for this chat
/crystallize chat default on      start every new chat with Crystallize on
/crystallize rent                 what this chat keeps paying to re-read
/crystallize rent remove N        stop sending result N (the AI can still read it back)
/crystallize jobs                 each cron job's cost, and whether skipping would help
/crystallize brake on|off         Runaway Brake for cron runs
/crystallize prices               this chat's model price
```

After each turn, a short Crystallize line shows what it's doing, for example `◆ −3.2k tok/call · /crystallize`.
In the TUI it sits in the status line and updates in place; the classic CLI prints it after the turn.
It uses Hermes's own `post_message`, never changes the AI's answer, and `/crystallize line off` hides it.
Messaging chats don't get it, so Crystallize never sends anyone a message.
With `plugins.isolation: host` Hermes can't hand plugins a live message, so the line is skipped there; `/crystallize` still works.

### In Hermes Desktop

Open the Crystallize panel (**Capabilities → Plugins**, then the Crystallize button in the status bar).

### From a shell

```text
hermes crystal tune my-job                    # trial lower-cost settings on a cron job
hermes crystal native-cron JOB_ID --draft     # draft a script that skips unchanged runs
hermes crystal rent SESSION_ID                # what a chat keeps paying to re-read
hermes crystal brake on                       # stop runaway cron runs
```

## What to expect

Savings depend on the work. In live paired tests at unchanged reasoning, working chats of 11 messages
cost 20 to 23% less on two of three models, and 23-message chats cost 32% less on one. Short tasks saved
about 6%. On Codex, working chats saved 13 to 19%. A cron job whose data changed once in 4 checks cost 60% less on Nous and 36% less over 5 pairs on Codex. A job whose data changes on nearly every
run gains nothing and can cost a little more, so each job shows its own verdict and real cost. See [when it saves, and when it can cost more](docs/when-it-saves.md).

A saving only counts between runs with the same model, provider and reasoning, taken from what each
request actually sent. Prices come from public model lists or your own rate card, so they're
estimates, not your bill. See [what has been measured](docs/validation-results.md) for live results
and their limits.

## Chat and Cron

| Control | What it does | Boundary |
| --- | --- | --- |
| Chat Exact/Clean/Summary | Keeps results exact, losslessly compacts JSON whitespace, or explicitly condenses large results with the selected helper | Summary can lose meaning; file contents and errors stay exact |
| Complete task handoffs | Sends supplied text to a helper; requested file reads/writes return ordinary-host-tool plans; owned placeholders avoid retyping | Main route stays fixed; plans are pending work, not completed I/O |
| Re-read savings | Opt-in projections of eligible replayed text, with recovery and a cache-aware payback check | Reasoning and the current thinking pass are untouched; projections are not semantic proofs |
| Tool discovery | Opt-in schema deferral with host-supported discovery | Unsupported/required tool paths retain normal schemas |
| Cron input tuning | Reduces repeated instructions/tool schemas at the same model and reasoning; restores failed trials | Changed settings and uncertain evidence do not become savings claims |
| Read recipes | Learns complete read plans from traces and returns them to the normal agent | Automatic nested replay/promotion is unavailable without a verified public guard contract |
| Native cron sources | Runs a reviewed Python snapshot through public host cron controls; unchanged data can avoid inference | Explicit complete-source approval, local delivery, failure retries and configuration checks; [scope and setup](docs/native-cron.md) |
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
cash cost. Token counts and character estimates are labelled separately. Chat net
subtracts helper requests, unsuccessful attempts, the main model typing hand-off input, read-backs
of originals and the routing note. It does not yet include tool schemas or the one-time cache
rewrite after removing a result, which Context Rent shows separately as a payback count. A smaller
visible message alone proves no saving.

Chat's one-read net is a **current-price projection**, including for earlier
turns. It applies the current main model's listed rates to recorded token
estimates, which follow words and symbols and calibrate from that model's real usage, then subtracts recorded helper costs and estimated extra-call overhead.
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
allowlist, isolated profile data, reservations and process deadlines. Synthetic JSON
indentation makes its compression ratio unrepresentative of normal workloads.
Passing it establishes observable conditions, not whole-loop quality or every
cloud provider's compatibility.

```bash
python scripts/validation_cross_domain.py --report work/validation-plan.json
```

The [current results](docs/validation-results.md) retain the same-route research
and cron-v2 pairs, public max-reasoning request observations and all known
failures. Smaller inputs did not establish a net saving: output/reasoning grew.
Use a new report filename for each run; existing reports are refused.

The preserved 16-attempt Nous evidence in `docs/evidence/` comes from the prior
private-dependency build. It retains original failures, interrupted attempts,
unknown billing and separately labelled offline checker correction. The historical isolated
no-agent scheduler smoke proves only that narrow path. Current results add a
real reviewed monitor sequence with first/changed answers and an unchanged tick
without a model request; billing and strict reasoning comparison remain unknown. These records are not
revalidation of this public-SDK-only revision; no paid rerun was used to replace
that evidence during migration.

## Commands

The manifest load floor is Hermes `>=0.22.7`; optional capabilities stay gated and this doesn't
promise every feature on every host. In Hermes Desktop the panel has Cron and Chat sections, and
command surfaces also work outside Desktop.

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
hermes crystal compatibility                  # what this install supports; no provider probes
hermes crystal prices --provider p --model m  # the list price Crystallize found for a route
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

Crystallize looks up list prices for your exact provider and model from the route's own public
model list, then from a public catalog ([models.dev](https://models.dev)). It uses a plain request
with no credentials or chat data, caches for a day, and can be turned off with
`hermes crystal prices --lookup off`. Codex (`openai-codex`) bills by subscription and publishes
no per-token price, so it uses the same model's OpenAI API list price; that lets automatic Context
Rent run there with no setup, but it isn't what your plan charges. With no known price, a paid helper stays off unless you allow
it with `hermes crystal prices --allow-unpriced-helper on`.

Rate cards are explicit user estimates and always win over a list price. They use declared currency/units and exact
provider/model/endpoint identity. Public provider total quotes remain separate;
unknown cache rates, request fees and tiers are not inferred. Cards never prove
actual billing. See [Model Auction pricing](docs/model-auction.md) and
[public SDK boundaries](docs/sdk-gaps.md) before authorizing a budgeted comparison.

## Routines and recovery

A routine contains only the conservative read subset before the first action.
The current public dispatch implementation does not verify the complete guard
pipeline, so automatic nested execution, routine shadow replay and pre-run/no-AI promotion
are unavailable for learned tool plans. The separate [native source](docs/native-cron.md)
workflow uses a user-reviewed standalone script through documented cron controls.
Explicit `promote --mode tool` installs normal-agent recipe
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
- Price lookup makes plain HTTPS GET requests to public model lists (the route's own `/models` and
  `models.dev`), sending no credentials or chat data. Turn it off with `hermes crystal prices --lookup off`.
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
  removed. Prices come from provider quotes, public list prices for the exact route and explicit
  user cards. No rates are inferred from totals or another provider. [Public boundaries](docs/sdk-gaps.md) explain unsupported features and
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
