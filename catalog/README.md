# Pinned submission bundle

This is preparation for an upstream maintainer-reviewed catalog PR, not a listing.
[Technical checks](../docs/marketplace.md) and human admission are separate.
Missing public capabilities become explicit unsupported features; they do not
justify private imports or raw dispatch. See [boundaries](../docs/sdk-gaps.md).

`public-api.json` contains the published 89-target API facts at upstream commit
`90d6b98fd86d20981ee06ddd5b7d93e4320f26a4`, verified by the digest in
`sdk-policy.json` and `public_api_audit.py`. No private validator code is vendored.
The independent audit is consumed by compatibility diagnostics and CI tests.

First commit the completed feature code, manifest, docs and actual current
fixture-labelled UI captures. Then run `generate_entry.py --sha <existing full
feature SHA> --screenshot <tracked image> --verification <exact-pin host JSON>`.
See the submission guide for the complete schema. No placeholder pin is supplied.

The generator reports capability/public-source/payload tripwires, exact-pin host
validation evidence, screenshot provenance and human gates separately. It creates
`crystallize.yaml`, `readiness.json` and `submission.md`; technical readiness can
pass while admission/listing remains pending. It never fetches, reads credentials,
mutates Git, hides warnings or fabricates validation. Commit generated files in a
later catalog-only commit, keeping their pin on the reviewed feature commit.
