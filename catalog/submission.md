# Crystallize catalog submission draft

Technical state: **exact-pin technical checks passed**.
This file and the entry in this plugin repository are a preparation bundle. They do
not themselves establish an upstream PR, catalog listing, maintainer approval, or
marketplace compliance. Independently checked publication/review status is recorded
separately; only a maintainer-merged upstream catalog entry establishes listing.

Pin: `586d2b4708606dc8255437beea374aae4ddc11fe` in https://github.com/itsflownium/hermes-crystallize; version `0.4.1`;
Hermes floor `>=0.22.7`. The pin is an existing plugin feature commit;
the later catalog-only bundle commit must not replace it with a self-reference.

Crystallize targets repeated context and read planning through explicit per-chat/per-job
controls and local receipts. Main reasoning is not reduced. No live savings percentage
at unchanged reasoning is established for this public-SDK candidate. The research,
writing, data, analysis, coding and cron fixtures are offline scope, not proof of
universal live task/provider coverage or semantic equivalence. Automatic pre-run/no-AI
routines remain unavailable on the reviewed SDK; their older savings do not transfer.

Disclosures for reviewer and installer:

- Hermes reads outside plugin data: session history/costs in `state.db`, cron outcomes
  in `cron/executions.db`, job definitions in `cron/jobs.json`, and public host configuration,
  explicit route configuration and user pricing cards. These reads use current profile scope;
  they are not telemetry. Public provider profiles may supply price quotes.
- Helper/compare features can make paid network requests through public `ctx.llm` to
  the user-selected host route. Route choices are configured/declared, not credential-store probes.
  Explicit validation makes only fresh answer-generation calls; it does not replay tools,
  old reasoning, deliveries or schedules. No universal provider test claim is made.
- Cron tuning/promotion/undo can run public Hermes CLI children and edit supported selected
  fields. Unsupported toolset/dispatch operations remain with the normal host agent; automatic
  nested reads and promotion are disabled without an available documented guarded seam.
- Explicit delegated file handoffs return pending ordinary-host read/write plans.
  No raw registry/private adapter performs those operations.
- Local plugin data can contain captured tool arguments/results, original results retained
  for recovery, cache entries, contracts, answers, receipts and usage diagnostics. Avoid
  sensitive shared cache namespaces. Helper calls have bounded background workers;
  timeout does not establish zero cost and late usage remains part of accounting.
- The plugin does not read credential stores, own/refresh tokens or self-update. Hermes
  owns authentication. No telemetry or independent credential store is introduced.
- Published public API audit found
  0 private import/access sites requiring review.
  Missing public capabilities are explicit unsupported features with normal-agent fallback;
  they are not implemented by private imports or guessed future methods.
  See `docs/sdk-gaps.md` and the exact file/line/module inventory in `readiness.json`.

Validation to attach before submission: public-repository clone at this SHA; local
`hermes plugins validate /path/to/pinned-checkout --install-deps` output, including every
warning; source/SDK review; current UI screenshots; deterministic tests; and any separately
labelled live reports. The generator did not run an install scanner, test credentials,
verify publication, install dependencies, open an upstream PR, or establish admission.

The catalog's canonical rules require a human-reviewed PR adding this entry to
`NousResearch/hermes-agent/plugin-catalog/`. Only a maintainer merge lists it. An owner or
major contributor must submit it. Each future SHA change requires another reviewed PR.

## Operator verification — 7 November 2026

The exact feature pin was resolved by a fresh Git clone with credential helpers disabled, and its raw manifest returned HTTP 200 without an Authorization header. Its cloned tree passed 1,253 tests with one optional dispatch-audit skip and the official 16 host checks with zero warnings. No paid provider call was made.

Full host output: [submission-candidate-host-report.json](../docs/evidence/submission-candidate-host-report.json). Exact-pin verification and publication evidence: [submission-candidate-verification.json](../docs/evidence/submission-candidate-verification.json). Listing remains pending upstream maintainer review.
