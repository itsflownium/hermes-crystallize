# Crystallize catalog submission draft

Submission status: **blocked on public SDK migration and admission validation**.
This file and the entry in this plugin repository are a preparation bundle. No upstream
catalog PR, catalog listing, maintainer approval, or marketplace compliance is claimed.

Pin: `c2c15d8dfa62b3e075da78547669b165e2b4c523` in https://github.com/itsflownium/hermes-crystallize; version `0.4.0`;
Hermes floor `>=0.22.7`. The pin is an existing plugin feature commit;
the later catalog-only bundle commit must not replace it with a self-reference.

Crystallize reduces repeated chat and cron work through explicit per-chat/per-job
controls, guarded routines and local receipts. Main reasoning is not reduced by this
validation. Observable contracts and frozen fixtures do not establish semantic equivalence.

Disclosures for reviewer and installer:

- Hermes reads outside plugin data: session history/costs in `state.db`, cron outcomes
  in `cron/executions.db`, job definitions in `cron/jobs.json`, and public host configuration,
  pricing and inventory. These reads use current profile scope; they are not telemetry.
- Helper/compare features can make paid network requests through public `ctx.llm` to
  the user-selected host route. Public model/rate catalogs can also refresh through Hermes.
  Explicit validation makes only fresh answer-generation calls; it does not replay tools,
  old reasoning, deliveries or schedules. No universal provider test claim is made.
- Cron tuning/promotion/undo can run Hermes CLI children and edit explicitly selected
  job fields. Read routines use the host's guarded execution seam, can run shell read
  commands, and run again during routine validation. A read recognizer is not a sandbox.
- Explicit delegated file handoffs can read requested files and write a requested output
  through guarded host tools. They obey the host's tool policy and approval path.
- Local plugin data can contain captured tool arguments/results, original results retained
  for recovery, cache entries, contracts, answers, receipts and usage diagnostics. Avoid
  sensitive shared cache namespaces. Helper calls have bounded background workers;
  timeout does not establish zero cost and late usage remains part of accounting.
- The plugin does not read credential stores, own/refresh tokens or self-update. Hermes
  owns authentication. No telemetry or independent credential store is introduced.
- Public API gaps remain: guarded tool dispatch, atomic cron toolset edits, model/rate-card/
  observed-cost and inventory access. Static analysis found
  19 core import sites requiring review.
  See `docs/sdk-gaps.md` and the exact file/line/module inventory in `readiness.json`.

Validation to attach before submission: public-repository clone at this SHA; local
`hermes plugins validate /path/to/pinned-checkout --install-deps` output, including every
warning; source/SDK review; current UI screenshots; deterministic tests; and any separately
labelled live reports. The generator did not run an install scanner, test credentials,
verify publication, install dependencies, open an upstream PR, or establish admission.

The catalog's canonical rules require a human-reviewed PR adding this entry to
`NousResearch/hermes-agent/plugin-catalog/`. Only a maintainer merge lists it. An owner or
major contributor must submit it. Each future SHA change requires another reviewed PR.
