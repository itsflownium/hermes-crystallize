# Catalog submission workflow

The local `catalog/` directory is a submission bundle. **It is not a listing or
maintainer approval.** Technical readiness is derived from exact-pin evidence;
human admission remains a separate gate.

The published upstream catalog rules and plugin API were reviewed at commit
`90d6b98fd86d20981ee06ddd5b7d93e4320f26a4`. Re-read the
[canonical rules](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md)
before submission. They require a real full SHA, capabilities matching the pinned
code, public surfaces, install/security/Desktop validation, truthful disclosures
and a maintainer-merged PR to `NousResearch/hermes-agent`. An owner or major
contributor submits this plugin lineage; a plugin feature PR is not admission.

## Public-only implementation

This revision removes private imports rather than concealing them. Public provider
quotes/user rate cards replace private price tables; configured/declared routes
replace private connected inventory. Unsupported nested dispatch and cron toolset
mutations become ordinary-host-tool plans/normal-agent fallback. No invented SDK
capability, raw registry execution or vendored private implementation is used.
See [public boundaries](sdk-gaps.md) for the functionality that is intentionally
unavailable on the audited host.

`catalog/public_api_audit.py` is our independent static tripwire. Its public API
fact snapshot is pinned by upstream commit and SHA256. Tests reject hidden private
imports/attributes, core overrides, credential readers, unsafe child execution and
policy drift. A clean result establishes that check's scope, not universal safety
or a human catalog decision. Run the authoritative complete host validator and
review warnings at the exact proposed commit.

## Commit features before generating the entry

Finish source, manifest, documentation and current screenshots, then commit them.
Only after that feature commit exists, generate:

```bash
python catalog/generate_entry.py \
  --sha FULL_40_CHARACTER_EXISTING_FEATURE_COMMIT \
  --screenshot assets/review/chat-fixture.png \
  --screenshot assets/review/cron-fixture.png \
  --verification /absolute/exact-pin-validation.json \
  --output catalog
```

Screenshot paths must be real tracked PNG/JPEG/WebP assets at that pin, not URLs
or files outside its tree. Use current real UI captures with visible/documented
**offline fixture/simulated backend** labels; never present mocked metrics as live
results. URLs are built on permitted GitHub hosts and pin the source SHA. Captions
and the submission description must preserve their fixture provenance.

The generator reads the pinned tree, rejects refs/short/fake SHAs, compares literal
capabilities, runs the public-source tripwire and checks tracked payload headers/
source archives. No runtime binaries/bytecode or concealed executable archive is
permitted. It does not fetch, read credentials, mutate Git, run dependencies or
claim it executed the host validator. Its archive bounds are conservative review
limits; the admission scanner remains authoritative.

Optional `--verification` is an operator-supplied JSON record of the actual
exact-pin host validation, with these keys:

```json
{
  "source_kind": "hermes_plugins_validate",
  "pinned_commit": "FULL_EXISTING_FEATURE_COMMIT",
  "host_source_commit": "ACTUAL_FULL_40_CHARACTER_VALIDATED_HOST_COMMIT",
  "ok": true,
  "checks_passed": 16,
  "errors": [],
  "warnings": [],
  "warnings_reviewed": true,
  "private_import_findings": [],
  "private_attribute_findings": [],
  "security_status": "safe"
}
```

Use the real count/results and actual validated host source commit, not the
example's numbers or the public-policy commit. The policy snapshot stays at
`90d6b98fd86d20981ee06ddd5b7d93e4320f26a4`; a different actual host SHA is recorded
separately rather than relabelled. Keep the full original
validator output alongside this record. Any warnings need review, not removal.
The generator checks revision/schema consistency and records the evidence hash;
it explicitly cannot authenticate an operator's report. Without matching evidence,
its host-validation status remains pending. A previous build's validation cannot
be relabelled as this revision's result.

The outputs distinguish:

- `technical_static_ready`: capability, public-source and payload tripwires pass.
- `technical_ready_for_submission`: those checks plus matching exact-pin host
  validation and current tracked UI evidence are supplied.
- `admission.listed`: false until the separate upstream maintainer merge; local
  generation never claims listing or independently authenticated admission.

`crystallize.yaml` contains the actual SHA and matching manifest version/floor;
`readiness.json` retains findings, provenance and pending gates; `submission.md`
is the reviewable disclosure draft. The manifest floor must be a truthful SemVer
minimum no newer than the released host. Capability gating does not excuse a
false floor. The host validator verifies release compatibility; the generator
only checks its format and pinned-manifest consistency. Runtime dependencies are
host-provided/stdlib; development dependency bounds are separate.

Commit the generated catalog-only bundle afterwards. Its pin keeps naming the
feature commit; do not invent the bundle's future hash or attempt self-reference.
Publish and verify an unauthenticated clone can resolve the feature commit. The
generator does not verify public availability.

## Human admission

Submit the entry as `plugin-catalog/crystallize.yaml` in an upstream
`NousResearch/hermes-agent` PR. Attach exact-pin host/security/Desktop output,
reviewed disclosures, current fixture-labelled UI screenshots and scoped tests.
Disclose history/config/data reads, paid host-facade model calls, optional public
provider metadata, local originals/caches/receipts, CLI children and unsupported
features. No self-updater, telemetry or independent credential store is introduced.

CI and a maintainer inspect the pinned code. Only their merge establishes listing;
future SHA bumps require new reviewed PRs. Remaining human steps must never be
reported as source defects, and source checks must never be reported as approval.

The prior 16-attempt Nous and no-agent scheduler records stay preserved under
`docs/evidence/`. They concern the earlier private-dependency build, with unknown
billing and a separate checker correction; they are not current public-SDK-build
revalidation. [Validation limits](validation.md) still apply.
