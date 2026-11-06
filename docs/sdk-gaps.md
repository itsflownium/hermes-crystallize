# Public SDK compatibility and upstream proposal

Crystallize is **not yet a public SDK only catalog submission**. Its guarded
replay, rate cards, legacy model catalogs, connected-model inventory and cron
toolset edits still depend on private host modules. These imports remain literal
and visible to `hermes plugins validate`; moving them behind an alias would not
resolve the dependency. The read-only `hermes crystal compatibility` command and
`GET /api/plugins/crystallize/compatibility` report known gaps and current import
sites without running a model, tool, provider probe or CLI child.

This audit used the installed Hermes source and its documented SDK. The relevant
host references are `hermes_cli/plugins.py::PluginContext.dispatch_tool`,
`hermes_cli/plugin_validate_private_imports.py::_PUBLIC`,
`hermes_cli/subcommands/cron.py`, `providers/base.py::ProviderProfile`, and
`website/docs/developer-guide/plugins/index.md`. No installed host files were
changed. These findings establish source compatibility boundaries; they do not
establish a tested provider/operating-system matrix or catalog admission.

## Boundaries implemented here

| Area | Current behavior | Remaining dependency |
| --- | --- | --- |
| Reasoning | Provider-declared effort vocabulary and capability declarations only; unknown returns no declared levels. No reasoning reduction is proposed or applied by the model adapter. | Public `providers` and `hermes_constants`; no private reasoning catalogs or guessed low/medium/high fallback. |
| Configured route | A real cron job uses job pins, then `cron.model` / `cron.model_provider`, then main configuration. Empty-job queries retain the main-chat route. Runtime evidence identifies the actually served model/provider. | Public `hermes_cli.config`; this adapter does not resolve auth or fallback. |
| Guarded nested tools | `model_tools.handle_function_call` preserves the host's guard and middleware path. If unavailable, dispatch fails closed. | Private `model_tools`; no raw registry fallback. |
| Cron prompt, script, mode, interpreter | Documented `hermes cron edit` flags. Interpreter is changed/restored in the same CLI invocation as the prompt/script. | Public CLI, with a profile-scoped child environment. |
| Cron toolsets | `None` restores cron defaults; `[]` means no toolsets. | Private `cron.jobs.update_job`; the audited CLI has no toolset edit flag. |
| Exact rate cards | `modelinfo.exact_token_prices` uses the requested provider/model/base URL only, preserving zero, missing cache rates, context tiers, request fees, source and version. | Private `agent.usage_pricing.get_pricing_entry`. |
| API equivalent estimates | A separate path can price an included/unpriced model at a listed API rate. Profile identity and declared model data are preferred; ambiguous vendor/model leaves are rejected. | Private `CanonicalUsage`, `estimate_usage_cost`, legacy `hermes_cli.models.provider_model_ids` catalogs. The Codex → OpenAI API-vendor mapping remains explicit legacy compatibility. |
| Connected model inventory | Hermes supplies connected routes; the plugin does not read credential stores. | Private `hermes_cli.inventory`. |

The public `ctx.dispatch_tool` method in the audited host directly invokes
`registry.dispatch`. The documentation describes inherited parent context, but
that is insufficient evidence that the method traverses the ordinary
`pre_tool_call`, approval, availability and tool-execution middleware pipeline.
The plugin therefore does not treat method presence as a guarded-dispatch
capability. There is no documented public guarded-dispatch capability in the
audited SDK to feature-detect. A future method must be adopted against its exact
documented contract; guessing a name or forwarding an undocumented keyword to
the raw dispatcher is unsafe.

Prices are estimates unless runtime evidence labels them otherwise. Runtime
helper observations can inform routing decisions and overhead accounting, but
one total cost divided by total tokens cannot establish separate input, output,
cache, tier or request-fee rates. The exact-rate API never fills unknown prices
with another provider's rate card. API equivalent values are reported separately
and must not authorize a budgeted experiment. Older host catalog/price lookups
may fetch metadata through Hermes; this plugin has no independent credential
reader or model-price table. The legacy Nous/OpenRouter catalog fallback remains
a real provider-specific compatibility boundary, not universal discovery.

## Proposed host SDK additions

These are **proposals**, not APIs implemented or feature-detected by this plugin.
Each should have a documented, versioned capability whose presence confirms
semantics, and should work through the plugin host isolation boundary.

### Guarded nested tool dispatch

Expose a public service such as:

```python
ctx.tools.dispatch_guarded(name, arguments, *, caller=None) -> ToolResult
```

The host should derive the active session/profile/task/turn, permit only
documented caller fields, assign a fresh nested tool-call ID, enforce the
caller's enabled tools and approvals, invoke veto hooks, traverse normal tool
middleware, retain execution/audit identity and propagate cancellation. A
detached or unattended caller must be unable to obtain approval by omission.
Unavailable context should return a structured refusal. A capability such as
`guarded_tool_dispatch.v1` must explicitly promise this pipeline, with host
tests for veto, denied approval, disabled tools, middleware and profile isolation.
Keep raw registry dispatch separately named if it is still necessary internally.

### Exact model metadata, pricing and observed costs

Expose public, serializable route and rate-card data:

```python
ctx.models.describe(route, *, allow_network=False) -> ModelMetadata | None
ctx.pricing.rate_card(route, *, allow_network=False) -> RateCard | None
ctx.pricing.estimate(route, usage) -> CostResult
```

`route` should include provider, model, base URL and API mode. Metadata should
distinguish configured and served identities, exact IDs/declared aliases,
unknown capabilities, context bounds and supported reasoning efforts. Rate cards
need explicit currency/units, nullable token bucket rates, whole-request tier
semantics, request fees, billing mode, source URL/version and freshness. A
subscription's included cost must remain distinguishable from a free API model.
Expose the canonical usage type publicly, including request count and safe
provider usage metadata. Estimation of summed sessions must retain uncertainty
where request boundaries or service tiers are missing.

Return per-request observed cost/provenance, served route and response completion
status through public completion results and lifecycle hooks. Add a public
connected-model inventory with host-owned authentication, cached/no-network
options and explicit discovery errors. Plugins should never need auth registry
internals or direct credential access to list permitted model routes.

### Atomic cron patches and toolsets

Expose an immutable-ID, profile-scoped cron facade, for example:

```python
ctx.cron.get(job_id) -> JobSnapshot | None
ctx.cron.update(job_id, patch, *, expected_revision) -> JobSnapshot
```

Apply validated prompt/script/interpreter/workdir/no-agent/toolset changes under
one transaction and refuse revision conflicts. Preserve three-way toolset
semantics: omitted field leaves it unchanged, `None` restores platform defaults,
and `[]` explicitly permits no toolsets. Return normalized values/revision and
structured refusal details. Record the initiating plugin and user-authorized
action. Crystallize's reasoning-preservation policy must still prohibit reasoning
reductions even if a general host API supports that field.

For CLI parity, add a documented toolset edit option with an unambiguous clear
operation, or a validated JSON patch input with revision checks. Existing public
`--prompt`, `--script`, `--agent`, `--no-agent`, `--interpreter` and `--workdir`
flags should remain supported. Plugin recovery records must still be saved before
a host mutation; a host transaction cannot atomically commit the plugin's own
separate database.

## Release gate

Keep catalog readiness false while required private couplings remain. A clean
lint achieved by aliasing imports, suppressing findings or dropping guards is not
a release criterion. Implement and test the required public host seams, migrate
the plugin to those documented contracts, run the complete host validator on the
exact proposed commit, and disclose remaining warnings and platform/provider
verification limits in the catalog PR. The local compatibility report is a
diagnostic aid; the host validator and human catalog review remain authoritative.
