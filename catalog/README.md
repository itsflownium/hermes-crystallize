# Submission bundle, not a listing

This directory prepares a future Hermes catalog PR. It does not establish
admission. Public SDK migration, exact-pin host validation, public clone
availability and a maintainer-reviewed upstream PR remain required. See
[status/disclosures](../docs/marketplace.md) and [SDK gaps](../docs/sdk-gaps.md).

Generate an entry only after the actual plugin feature commit exists:

```bash
python catalog/generate_entry.py --sha FULL_40_CHARACTER_EXISTING_FEATURE_COMMIT --output catalog
```

The generator resolves that commit locally, reads manifest/source, checks
literal capabilities and emits `crystallize.yaml`, `readiness.json` and
`submission.md`. It does not fetch, guess a hash, read credentials, suppress
warnings, run the scanner or open a PR. No placeholder YAML pin is provided.
Commit generated files separately so their pin names the reviewed feature
commit instead of attempting to reference themselves.
