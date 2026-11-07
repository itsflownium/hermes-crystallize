# Reproducible validation

Crystallize preserves the main model and requested reasoning settings. Lower
reasoning, a changed main model, different task input or a subscription/API
price substitution do not count as efficiency improvements.

Offline tests exercise routing, ownership, policy, accounting and failure paths
with local fakes. The frozen harness makes two fresh answer-generation calls
over two representations of the same complete evidence. Neither establishes
semantic equivalence, full live-job quality or universal provider compatibility.

## Local checks: no calls or credentials

```bash
python -m pytest -c tests/pytest.ini --rootdir tests tests/test_cross_domain_completion.py
python scripts/validation_cross_domain.py --report work/validation-plan.json
```

Without `--run`, the harness never imports a context factory or constructs a
host client. The plan records hashes/lengths, preserved evidence literals,
reference-answer self-checks and limitations. A reference self-check validates
the fixture, not a model.

| Domain | Observable conditions | Semantic limit |
| --- | --- | --- |
| Research | Revised capacity, both exact URLs, date and unknown opening hour | No source-credibility or current-research assessment |
| Writing | Brief facts, bounded length and no invented links | Style, originality and editorial quality need human judgment |
| Data | CSV totals, a return and preserved invalid-row identity | No arbitrary dataset/pipeline validation |
| Analysis | Rates, percentage-point difference, sample size and causal limitation | No open-ended analysis guarantee |
| Coding | Restricted pure `clamp` AST, six cases | Tiny allowlisted interpreter; generated code is never executed |
| Cron | Delta, failed source, no silence and no action claim | No schedule, recipe, delivery or tool runs |

Inputs deliberately contain oversized JSON indentation. The candidate uses the
actual plugin's lossless whitespace compactor; every JSON string and value
remains available. Its synthetic compression ratio is not representative of
ordinary workloads. No prose, markup, file content, status or error is dropped
to manufacture a gain. Both arms receive complete fresh task text. Neither
receives earlier reasoning or a reasoning summary.

## Explicit live workflow

Only the root operator runs paid validation. Current authorization is Nous
Portal (`nous`) only, at most $2 in conservative reservations and a 900-second
deadline for the whole invocation. Internals accept provider-independent
facades; the CLI's explicit live scope refuses other providers and wildcards.
Expanding scope needs separate authorization and a deliberate guard change.

Supply an operator-owned factory **outside the repository** with this contract:

```python
def make_context(*, home, settings):
    # home is pathlib.Path; HOME and HERMES_HOME are already isolated.
    # Bind an authorized public host context and a snapshot callback here.
    return {"ctx": ctx, "snapshot": snapshot}
```

A `(ctx, snapshot)` pair also works. `ctx` supplies public `ctx.llm.complete`.
The zero-argument callback rereads sanitized public configuration before/after
each call. Context construction is host-specific and operator-supplied. The
harness does not read `.env`, auth files, keychains, browser profiles or another
CLI's tokens. Authentication stays in the operator-authorized host scope; no
credential belongs in source, settings, reports or command arguments.

The settings JSON contains **only** this shape, using the actual main route:

```json
{
  "provider": "nous",
  "model": "YOUR_EXACT_MAIN_MODEL_ID",
  "reasoning": {"effort": "max"},
  "source": "public_host_configuration"
}
```

`public_host_configuration` means the callback actually rereads host config;
writing that string does not verify a constant callback. Manually asserted
settings use `operator_assertion` and are excluded from cost attribution.

The audited public completion API has no direct reasoning argument. A call
without a task slot is not evidence that `agent.reasoning_effort` was inherited.
Register a dedicated public validation auxiliary slot, configured to the **same
main provider, model and reasoning**, and pass it identically to both arms. Do
not substitute a helper or lower-reasoning slot. Public results do not expose
effective wire reasoning, which remains explicitly unverified in the report.
Some host routes omit an ordinary `max_tokens` argument. The operator can use
documented task `extra_body` settings when appropriate for the route; source
inspection is not proof that the server enforced a limit.

```bash
python scripts/validation_cross_domain.py \
  --run \
  --context-factory /absolute/operator-validation.py:make_context \
  --settings /absolute/validation-settings.json \
  --allowed-providers nous \
  --task crystallize_validation \
  --budget-usd 2 \
  --reserve-per-call-usd 0.10 \
  --max-task-seconds 900 \
  --call-timeout 90 \
  --report work/validation-live.json
```

Workers run sequentially with temporary HOME/config/cache directories and a
restricted environment without inherited authentication variables. A process
deadline terminates the worker and retains in-flight billing as unknown. Exit
codes, `SystemExit` and worker failures are recorded without provider exception
text or tracebacks. No gateway restarts or production-job edits occur. Calls
receive fresh messages, temperature, requested output limit, timeout, purpose
and optional registered task only. No tools/actions are supplied or replayed.

## Budgets and accounting

The $0.10 example is an operator estimate, not a guaranteed price. The envelope
must cover input, output, reasoning, retries/fallbacks and omitted output caps.
Every attempt reserves the full amount; errors and reported zero cost receive
no refunds. An estimate above its reservation stops later calls. This is not a
provider-enforced billing cap. Carry earlier attempts into the same total with
`--already-reserved-usd AMOUNT`; do not reset the budget after a failed attempt.

Unknown cost stops by default. Explicit `--accept-estimated-cap` continues
against reservations where the host has no cost field. Twelve $0.10 calls
reserve $1.20, plus any earlier attempts. Actual billing stays unknown and no
verified cost-saving claim is produced. Reconcile provider billing separately.

Reports preserve sanitized `raw_token_fields` received from the public facade.
Because that facade can default missing counters to zero, normalized zero buckets
remain unknown, including cache counters on otherwise positive usage. Unknown
cache state cannot qualify a cost-reduction percentage; incomplete input/output
counts cannot qualify a comparison even when a host cost estimate is present.

Reports distinguish reported token counts, Hermes `usage.cost_usd` estimates,
actual billing (`null`, unavailable through this facade), and error/interrupted
attempts. Missing usage and ambiguous default all-zero tokens remain unknown.
An explicit zero price estimate remains an estimate; an error with zero cost is
still an error, and a timeout is not free.

Main route/reasoning changes stop the pair. Served-route mismatches or missing
attribution reject it. Unknown/invalid usage or cost, different/unknown cache
use, unverified settings snapshots and failed observable checks exclude cost
attribution. Failed results stay in the denominator. Counterbalanced ordering
does not control cache state or provider load. Even eligible deltas describe
answer-generation estimates, not full-task savings or verified wire reasoning.

## Evidence limits

A plan establishes deterministic fixture construction. A live report records
which fixtures, served routes, requested settings, usage and checks were
observed. Passing small fixtures cannot become “works on every provider,”
“same quality,” or an unqualified savings percentage. Main-loop behavior,
arbitrary file tasks, research quality, long outputs, real delivery, cache
amortization, operating systems and transport shapes need separate evidence.

Fresh provider results belong in separately dated reports. This guide does not
manufacture results or fill missing live runs with historical numbers. Earlier
README savings tables were uncontrolled, confounded observations, not proof for
this reasoning-preserving release.

## Checker correction after the first live run

Checker revision `2-analysis-rational-rates` corrects the analysis fixture's
numeric-only rate check. Its unchanged prompt asks for rates “as fractions”;
`4/5` and `3/5` are mathematically correct representations of `0.8` and `0.6`.
The revised checker accepts finite JSON numbers or bounded integer-ratio strings
using Python `Fraction`, without evaluating expressions. Wrong numerators,
zero denominators, nonfinite values, booleans and oversized ratios are rejected.
The task input and mathematical acceptance criterion have not changed.

This is a **post-hoc checker correction**, not a new live result. Original live
reports and their failed checks must remain unchanged. Publish the original
strict outcomes alongside a separately labelled offline re-evaluation of the
same recorded answers, identifying the source report/answer hashes and checker
revision. Reports without a checker revision used the original numeric-only
checker. Do not rerun paid calls to replace an unfavorable original outcome or
silently rewrite a failure as a pass. Neither version establishes semantic
equivalence or actual billing.

## Preserve each run's evidence

Use a fresh `--report` path for every invocation. The CLI claims the path with an
exclusive create before loading an operator factory or starting a model call; an
existing file or symlink is refused. Progress updates then belong to that run.
Keep failed, interrupted and superseded fixtures under separate names; changing a
prompt contract requires a new fixture version and a separately labelled rerun.
