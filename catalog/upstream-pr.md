Adds `plugin-catalog/crystallize.yaml` for [Crystallize](https://github.com/itsflownium/hermes-crystallize), pinned at `fc34138782796e94bca9131dd12c71f2dd843031` (version `0.6.0`, `requires_hermes: ">=0.22.7"`, also declared in `plugin.yaml`). I'm the repository's owner (`itsflownium`).

## What it does

Crystallize lowers the cost of repeated work in Hermes chats and cron jobs. It does not change the model or reasoning effort the user picked.

- **Cron:** it drafts a small snapshot script from what a job already read. Once the user reviews and attaches the script, the job's AI run only happens when that data changes (Hermes monitor mode). Runaway Brake stops a run that goes far past the job's own normal use. Trials of lower-cost settings stay only if their checks pass and they cost clearly less; otherwise the old settings come back.
- **Chat:** Context Rent shows what each old tool result costs on every call and can remove it (the AI can still read the original back). Clean losslessly compacts tool output. Each chat and job gets local receipts.
- **Everywhere you chat:** `/crystallize` works the same in the classic CLI, the TUI and Desktop (savings, Context Rent with remove/restore, cron jobs, Runaway Brake, prices). In the CLI and TUI a short line after each turn shows what it's doing, through `ctx.post_message` (the TUI status line, updated in place; a line in the classic CLI). Desktop has the full panel.

Measured live at unchanged model, provider and reasoning, priced at public list prices (estimates, not billing), small samples: working chats cost 20–33% less on `tencent/hy3`, 13% less on `stepfun/step-3.7-flash` and 5% less on `deepseek/deepseek-v4.1-flash`, all on Nous Portal. Codex chats saved 13–19%. A cron job whose data changed once in 4 checks cost 60% less. Jobs whose data changes on every run gain nothing, and a chat that reads a removed result back can end slightly negative. The panel shows both cases. 

## Demo

An 87-second narrated demo, recorded live on Nous Portal: the TUI (the status line, `/crystallize rent`, removing a 14.8k-token result so the next turn's context drops from 37.7k to 16.9k tokens), the classic CLI, the Desktop panel and a cron job that skips unchanged checks: [crystallize-demo.mp4](https://github.com/itsflownium/hermes-crystallize/blob/fc34138782796e94bca9131dd12c71f2dd843031/assets/demo/crystallize-demo.mp4). A longer 3½-minute walkthrough is [crystallize-demo-full.mp4](https://github.com/itsflownium/hermes-crystallize/blob/fc34138782796e94bca9131dd12c71f2dd843031/assets/demo/crystallize-demo-full.mp4). The raw pair reports are in [`docs/evidence/`](https://github.com/itsflownium/hermes-crystallize/tree/fc34138782796e94bca9131dd12c71f2dd843031/docs/evidence), and the write-up is in [`docs/demo.md`](https://github.com/itsflownium/hermes-crystallize/blob/fc34138782796e94bca9131dd12c71f2dd843031/docs/demo.md).

## Hermes surfaces used

- Tools: `crystal_cheap`, `crystal_full_result`, `crystal_list`, `crystal_run`
- Hooks: `on_session_end`, `post_api_request`, `post_llm_call`, `post_tool_call`, `pre_llm_call`, `pre_tool_call`, `transform_llm_output`, `transform_tool_result`
- Middleware: `llm_request`, `tool_execution`
- Also: the `/crystallize` slash command, `ctx.post_message` for the CLI/TUI line, `ctx.llm` for the optional helper calls, the `hermes crystal` CLI command, a Desktop panel (`desktop/plugin.js`, plugin SDK only) and a dashboard API.
- No `requires_env`, no Python dependencies, no OAuth.

## Disclosures (rule 13)

- **Reads outside its own data:**
  - Hermes session history and costs in `state.db`.
  - Cron outcomes in `cron/executions.db` and job definitions in `cron/jobs.json`.
  - Public config, route config and the user's own rate cards.
  - All of these use the current profile.
- **Network:**
  - **Price lookup:** once a day, public model prices from the active route's `/models` endpoint and `https://models.dev/api.json`, with no credentials or chat data. Turn it off with `hermes crystal prices --lookup off`.
  - **Paid calls:** the optional helper, compare and check features make paid model calls through the user's own Hermes route via `ctx.llm`. These are off by default, and hand-offs are labelled as costing more in live tests.
- **Shell and child processes:** cron tuning, promote and undo run `hermes cron` CLI children, which edit only the fields the user chose on the selected job. No approval is bypassed, and nothing inherits YOLO mode.
- **Files written outside plugin data:** approved snapshot scripts are copied under `scripts/crystallize-native/` after the user reviews the full source. They run with ordinary cron permissions.
- **Messages:** after a turn in the CLI or TUI it posts one short status line with `ctx.post_message` and edits it in place. It never posts on Desktop or messaging platforms, never changes the AI's answer, and `/crystallize line off` hides it.
- **Local data:** captured tool arguments and results (originals kept for recovery), cache entries, receipts and usage diagnostics, all in its plugin-data folder.
- **Background work:** helper calls use bounded background workers, and late usage is still counted.
- **Not done:**
  - No telemetry.
  - No reads of credential stores, `.env` or `auth.json`.
  - No token handling.
  - No self-update.
  - No runtime overrides of Hermes core.
  - No compiled payloads.

## Validation

`hermes plugins validate <clean clone at fc34138> --install-deps` was run with this branch's Hermes (`NousResearch/hermes-agent@c93bbc0`, isolated `HERMES_HOME`):

```
✓ manifest  ✓ manifest fields  ✓ requires_hermes  ✓ config schema  ✓ requires_env  ✓ oauth
✓ loadable (__init__.py, desktop/plugin.js)  ✓ python dependencies — none declared
✓ capability probe  ✓ declared tools  ✓ declared hooks  ✓ declared middleware
✓ built-in tool collisions  ✓ security scan — safe  ✓ no core override  ✓ desktop surface
Validation passed.   (no warnings)
```

`scripts/validate_plugin_catalog.py plugin-catalog/` passes (1384 files valid), and the catalog test files pass locally on Python 3.14 (697 passed, 29 skipped).

## Screenshots

The images are the real Desktop panel rendered with sample figures (labelled as such in the repo):

![Cron and chat](https://raw.githubusercontent.com/itsflownium/hermes-crystallize/fc34138782796e94bca9131dd12c71f2dd843031/assets/review/panel-cron-and-chat.png)
![Job details](https://raw.githubusercontent.com/itsflownium/hermes-crystallize/fc34138782796e94bca9131dd12c71f2dd843031/assets/review/panel-job-details.png)

## Known limits (also in `known_issues`)

- Savings are list-price estimates on small samples, tested on Nous and Codex only.
- Automatic replay of learned tool plans isn't available on the public SDK. See `docs/sdk-gaps.md`, which lists the hooks that would help.
- Under `plugins.isolation: host`, the `hermes crystal` CLI subcommands and the CLI/TUI status line aren't available; `/crystallize`, the panel and the tools still work.

