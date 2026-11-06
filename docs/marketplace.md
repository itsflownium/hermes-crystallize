# Catalog submission status

Crystallize is **not catalog-ready or listed by this bundle**. The local
`catalog/` directory prepares a separate, human-reviewed upstream PR; it cannot
admit its own plugin.

This workflow was checked against the installed host's
`plugin-catalog/README.md` and
`website/docs/developer-guide/plugins/catalog-submission.md`. The canonical
[rules](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md)
require an existing full 40-character commit pin, accurate capabilities,
successful host validation/security scanning, public SDK surfaces and a
maintainer merge into `NousResearch/hermes-agent`. Re-read the rules at actual
submission. An owner or major contributor must submit this lineage.

## Current blockers

The [SDK audit](sdk-gaps.md) identifies private dependencies for guarded nested
dispatch (`model_tools`), cron toolset edits (`cron.jobs`), exact rate cards and
estimation (`agent.usage_pricing`), legacy model catalogs (`hermes_cli.models`)
and connected inventory (`hermes_cli.inventory`). Required public seams must be
implemented and adopted before claiming public-SDK compliance. Import aliases,
suppressed warnings and raw registry fallback do not resolve these gaps.

`hermes crystal compatibility` reports the gaps without calling models or
probing credentials; its readiness is false. Offline tests or synthetic
completion fixtures cannot override catalog rules. Public clone availability,
exact-pin validation, current screenshots, an upstream catalog PR and maintainer
acceptance still need evidence. A plugin feature PR does not complete admission.

## Generate from an actual feature commit

First commit the completed plugin, manifest, README and disclosures. Then use
the **existing** full SHA:

```bash
python catalog/generate_entry.py \
  --sha FULL_40_CHARACTER_EXISTING_FEATURE_COMMIT \
  --output catalog
```

The generator reads the manifest and source from that commit, not uncommitted
files. Branches, tags, short hashes, zero placeholders, missing objects and
dynamic/unverifiable registrations are refused. Literal tools/hooks/middleware
must exactly match the pinned manifest. Mandatory environment declarations are
copied; host load/secret-scope checks remain required.

Generated files:

- `crystallize.yaml`: real pin, matching version/floor/capabilities, community
  tier and known issues, using the host catalog schema.
- `readiness.json`: exact pin, source import inventory, unresolved gates and
  explicit `catalog_ready: false`.
- `submission.md`: disclosure draft for network, history, shell, file,
  background workers, local storage and SDK gaps.

Review disclosures against the pinned tree. File-handoff disclosure depends on
that feature's presence; the report states whether the SDK-gap document exists
at the pin. Static analysis is not a security review/runtime load. Generation
does not fetch, read credentials, mutate Git, install dependencies, run the
admission scanner or open a PR.

Commit the catalog-only bundle afterwards. Its pin keeps naming the earlier
feature commit; never invent the bundle's own future hash. An entry does not
need to contain itself at its pin. Publish the feature commit, verify an
unauthenticated clone can resolve it, then run the authoritative validator in a
checkout at exactly that revision:

```bash
hermes plugins validate /absolute/pinned-checkout --install-deps
```

Retain every security, Desktop-surface and private-import warning. The generator
does not mark validation as run, and output from another revision is insufficient.

## Upstream admission

Once SDK blockers and exact-pin validation are resolved, submit
`catalog/crystallize.yaml` as `plugin-catalog/crystallize.yaml` in a PR to
`NousResearch/hermes-agent`. Include disclosures, screenshots and scoped
validation reports. CI clones/validates the pin; a maintainer accepts, requests
changes or declines. Only a maintainer merge establishes a listing. Future SHA
bumps require new reviewed PRs. Crystallize has no self-updater.

The [validation guide](validation.md) separates actual billing, estimates,
unknown cost, local fakes and live frozen fixtures. No universal provider proof
or fabricated live result is supplied by this bundle.
