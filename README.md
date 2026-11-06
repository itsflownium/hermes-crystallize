<p align="center">
  <img src="assets/crystallize-logo.svg" alt="Crystallize" width="380">
</p>

**Spend less on repeated work in Hermes chats and cron jobs while preserving your main model and reasoning settings.**

Crystallize targets repeated context, duplicated file text, unnecessary tool
schemas and stable read steps. It keeps originals recoverable, records helper
overhead and shows when evidence is incomplete. It does not reduce the main
model's reasoning or reuse an earlier reasoning transcript as a shortcut.

The architecture uses Hermes's host-owned model facade and provider declarations
across cloud routes. This is a compatibility design, not a claim that every
provider, model, operating system or task has been tested. Missing prices,
capabilities, usage and guarded execution stay unknown or fail closed.

## Chat and Cron

| Control | What it does | Boundary |
| --- | --- | --- |
| Chat Exact/Clean/Summary | Keeps results exact, losslessly compacts JSON whitespace, or explicitly condenses large results with the selected helper | Summary can lose meaning; file contents and errors stay exact |
| Complete task handoffs | Sends supplied text or requested file inputs to a helper; guarded file output and placeholders avoid retyping | Main route stays fixed; the helper needs complete inputs and output review |
| Re-read savings | Opt-in projections of eligible replayed text, with recovery and a cache-aware payback check | Reasoning and the current thinking pass are untouched; projections are not semantic proofs |
| Tool discovery | Opt-in schema deferral with host-supported discovery | Unsupported/required tool paths retain normal schemas |
| Cron input tuning | Reduces repeated instructions/tool schemas at the same model and reasoning; restores failed trials | Changed settings and uncertain evidence do not become savings claims |
| Read routines | Reuses tested read steps before the agent, retaining complete outputs and errors | Actions remain with the agent; drift invalidates old evidence |
| Blind review | Presents anonymous A/B answers before model/cost reveal | A preference and structural checks do not establish correctness |
| Model Auction | Explicit bounded model comparisons on frozen recorded evidence | Different-model selection is not same-model efficiency proof |
| Rhythm | Explains observed changes and previews an explicit noncritical schedule adjustment | Confirmation, sufficient observations and declared slack are required |

Chat controls apply to the chat where you enable them, after its first prompt.
Choose a helper from a connected Hermes route. Hermes owns authentication;
Crystallize does not read keys or OAuth stores. Helper calls incur provider
usage, including unsuccessful attempts. The main model keeps its chosen route
and reasoning.

Originals belong to their originating chat and expire after seven days.
`crystal_full_result` retrieves exact text; asking a helper about it requires
chat opt-in. Requested file reads/writes pass through guarded host tools.
Placeholders expand only in eligible text fields before policy checks, and
cannot become commands, URLs or file paths.

See [controls and recipes](docs/controls-and-recipes.md), [Rhythm](docs/rhythm.md),
[Model Auction](docs/model-auction.md) and [validation](docs/validation.md) for
exact controls and limitations.

## Evidence and cost

Crystallize separates actual cost evidence, host/list-price estimates, included
subscription usage and unknown values. An API equivalent is not a subscription's
cash cost. Token counts and character estimates are labelled separately. Helper
requests, retrievals, unsuccessful attempts, comparisons and cache-rewrite
overhead belong in net payback; a smaller visible message alone proves no saving.

Cron trials require comparable recorded model/provider/reasoning and observable
outcome checks. Source, task, route, tool or contract changes require fresh
validation. The normal agent handles work while evidence is rebuilt. Contracts
catch missing fields, IDs, URLs, errors and actions; they cannot prove factual
correctness or semantic equivalence. Review answers and real side effects before
promotion.

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

## Install and use

Install from the published repository; this installs its default revision.
Catalog-name listing is not established. Requires Hermes `>=0.22.7` as declared
in the manifest; host seams and provider behavior still need checking on the
version in use.

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
hermes crystal promote morning-brief          # explicit promotion after evidence
hermes crystal undo morning-brief
hermes crystal compatibility                  # SDK gaps; no provider probes
```

Use `hermes crystal <command> --help` for re-read controls, anonymous comparison
review, Model Auction, Rhythm and optional delegation/tool discovery. Views and
previews do not make paid model calls. Comparisons and schedule changes have
explicit actions. Legacy lower-reasoning trials are restored and require fresh
baseline evidence; reasoning reduction is refused.

## Routines and recovery

A routine contains only the conservative read subset before the first action.
Each resolved step is checked again and uses Hermes's guarded path, including
caller identity, availability, approvals, hooks and middleware. If that seam is
unavailable, dispatch fails closed. A read-command recognizer is not a sandbox
or proof about arbitrary programs.

Complete results, source order, nested JSON, errors, timestamps and file artifacts
remain available. Oversized/unrecognized evidence falls back to the normal
agent rather than silently truncating it. Parameter bindings are explicit;
task/endpoint/schema drift requires revalidation. A job's own pre-run script
keeps its stop behavior and output. Consecutive failures restore prior routine
settings. Promotion and schedule changes remain explicit choices.

## Disclosures and catalog readiness

- Reads profile-scoped session history/costs (`state.db`), cron outcomes
  (`cron/executions.db`), job definitions (`cron/jobs.json`), configuration,
  pricing and connected inventory. History reads are read-only.
- Stores captured arguments/results, originals, caches, contracts, comparison
  answers, receipts and diagnostics under
  `<hermes home>/plugin-data/crystallize/`. Originals can contain sensitive text.
  Receipt export redacts common credential forms; arbitrary sensitive content
  is not guaranteed to be removed.
- Helper/comparison features send supplied data through a host-owned model
  route on the relevant opt-in/explicit action. Hermes metadata lookups may
  refresh remote model/price catalogs. There is no independent credential
  reader or telemetry service.
- Routine validation can run read steps again after a scheduled execution,
  including approved shell reads. Explicit file tasks can read/write requested
  paths through guarded tools. Background helpers are bounded; timeouts can
  still incur provider usage.
- Explicit tuning/promotion/undo edits selected cron prompt/script/mode/toolset
  fields and can invoke the Hermes CLI. Rhythm changes schedules only through
  its confirmed path. Promotion writes a small wrapper script in the profile's
  Hermes home.
- Fetch caching is opt-in, profile-scoped, time-limited and applied after normal
  tool policy checks. Shared namespaces need matching authorization/freshness.
  Credential-bearing arguments are not cached.
- Private Hermes couplings remain for guarded dispatch, atomic cron toolset
  edits, exact pricing/model catalogs and connected inventory. They remain
  visible to validation. [SDK readiness](docs/sdk-gaps.md) stays false until
  required public seams are adopted.
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
