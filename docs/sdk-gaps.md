# Public SDK boundaries

This revision removes the private Hermes pricing, model-inventory, cron-update
and tool-dispatch imports. Missing public capabilities are explicit unsupported
features, with work left to the normal host agent. A private adapter, raw registry
call, hidden import alias or guessed future method is not a fallback.

The independent static audit reads the **published plugin API facts** from
`hermes_cli/data/plugin_api.json` at upstream commit
`90d6b98fd86d20981ee06ddd5b7d93e4320f26a4`. The 5,044-byte snapshot contains 89
public targets; SHA256 is
`aeafa3b0333bfbe01746774e3ffa74d46eee32478b398c38144956bd5be9b98b`.
`catalog/public-api.json` contains those facts, not copied private validator code.
`catalog/sdk-policy.json` records provenance. CI verifies the digest and exercises
our own static tripwires. A policy update requires deliberate source review.

## Supported behavior and explicit limits

| Area | Public surface used | Limit/fallback |
| --- | --- | --- |
| Model calls | `ctx.llm.complete`, host auxiliary slots and trust policy | Hermes owns authentication and served-route selection; unknown usage stays unknown |
| Provider metadata | Public provider declarations/capabilities | No private model catalog or guessed reasoning vocabulary |
| Route selection | Sanitized explicit main/auxiliary/cron routes, public declarations and user-added routes | Configured/declared does not prove sign-in, entitlement or current availability; no credential probing |
| Pricing | Public `ProviderProfile.get_usage_cost` totals plus explicit exact-route user rate cards | Quotes/cards are estimates; a total cannot establish separate token rates; missing rates/fees/tiers stay unknown |
| Tool schema discovery | Public registration/metadata surfaces | Metadata access does not authorize execution |
| Nested tools and automatic routines | Currently unsupported | Public `ctx.dispatch_tool` documentation promises guards, but the audited implementation directly reaches registry dispatch. Presence/version/config cannot certify those guards. Return a normal-host-tool plan; do not dispatch, promote or replay automatically |
| Requested file inputs/outputs | Non-executing ordinary-host read/write plans and owned text placeholders | A plan is not a completed read/write. The normal agent performs tools with normal approvals |
| Cron edits | Documented public `hermes cron edit` fields | Unsupported toolset changes become an explicit normal-agent plan; no `cron.jobs` mutation |
| Legacy recovery | Saved recovery records, supported CLI restoration and explicit host-tool guidance | Pending/unsupported restoration retains its record; user edits are preserved |
| CLI children | Profile-scoped explicit environment with verified YOLO bypass removed | No auto-approving/noninteractive tool children or automatic nested replay |

The minimum manifest floor remains `>=0.22.7`. This is a load/compatibility floor,
not a promise that every optional host capability exists there. Optional behavior
is inspected/gated; no unavailable capability is silently replaced by a private
implementation. A complete minimum-version/platform/provider matrix is not
claimed. The authoritative validator must check the floor at the exact pin and
must not be pointed at a future release number to silence failures.

## Technical checks and admission are separate

`hermes crystal compatibility` reports the actual local public-source audit and
unsupported capabilities. It must not equate unsupported automatic routines with
private imports, or equate a clean static result with catalog admission.

The source audit checks direct and aliased/computed imports, private attributes
on core handles, module/function overrides, credential-store/env reads, unsafe
child flags/environments and dynamic code execution. It permits only provably
plugin-owned module-cache namespaces. It is a review tripwire, not a sandbox or
full data-flow proof. Production Python/dashboard are its scope; standalone
operator validation scripts and tests are separately disclosed. The host's
validator/security/Desktop checks, execution tests and manual review remain
required at the feature commit.

No private implementation was vendored to obtain a clean result. Public API
facts cannot grant a missing guard contract. A future guarded-dispatch integration
needs a documented public contract **and** verified guard/approval/hook/middleware
semantics before it can enable nested execution.

The catalog generator reports static technical results, exact-pin host evidence,
current offline UI evidence and human admission separately. Technical checks can
pass while publication, an upstream catalog PR and maintainer merge are pending.
See [submission workflow](marketplace.md).

## Evidence from the previous build

The preserved 16-attempt Nous evidence under `docs/evidence/` belongs to the prior
implementation, which still had private dependencies. It records frozen tasks,
helper smoke calls and interrupted attempts with their original costs/unknowns.
The analysis checker correction remains separately labelled and original failures
remain intact. The isolated scheduled no-agent smoke tests only scheduling/local
snapshot behavior. These records do not validate this public-SDK-only revision,
current UI, automatic AI cron execution, actual billing or all providers. No paid
rerun was performed to replace them during this migration.
