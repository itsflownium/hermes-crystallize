<p align="center">
  <img src="assets/crystallize-logo.svg" alt="Crystallize" width="380">
</p>

**Hermes learns your routines, proves them, then runs them for almost nothing.**

Hermes re-thinks the same steps every time a scheduled job runs: fetch these feeds,
call this API, check these sites. Crystallize watches those runs, finds the steps that
never really change, and saves them as a readable **routine**. After the routine has
matched the real job three runs in a row, one click switches the job over: the
routine collects the data with no AI reasoning, and Hermes makes one short call to
write the result. If a routine ever fails, Hermes falls back to the original task, and
two failures in a row undo the switch automatically.

## What you get

- **Recording.** Every tool call Hermes makes is recorded with its chat or cron run.
- **Analysis.** `hermes crystal analyze <job>` lines up a job's runs and labels each step:
  - `fixed`: the same every run.
  - `fill-in`: the same apart from dates, ids or temp paths, which are refreshed each run.
  - `varies`: same tool, different inputs. Frozen from the latest run, then tested.
  - `setup`: loads context for the agent (skills, todo lists). Skipped.
  - `agent`: sends or changes something. Always stays with Hermes.
- **Proof before switching.** After every scheduled run, the routine's steps run too
  (tool calls only, no AI) and their outputs are compared with what the real run got.
  It needs three matches in a row before it can be promoted.
- **Promote and Undo.** Promote rewrites the job's prompt to call `crystal_run` first and
  keeps the original prompt underneath as the fallback. Undo restores it exactly.
- **Chat analysis.** For any chat, see what it cost, how many tool calls it made, exact
  repeated steps, and patterns (the same kind of step many times in a row).
- **Desktop panel.** A Crystallize button in the top bar and the status bar (or ⌘K →
  "Crystallize: Open savings panel") opens a panel beside the chat with the chat's
  analysis, a savings score, and Spotted / Testing / Live / Watching jobs.

## Install

```bash
hermes plugins install itsflownium/hermes-crystallize
hermes plugins enable crystallize
```

Then in Hermes Desktop: **Capabilities → Plugins → Crystallize**, turn on the Desktop
switch. Works on macOS, Windows and Linux. Requires Hermes 0.22.7 or newer.

## Use it

```bash
hermes crystal history                 # every cron job: runs, cost, time
hermes crystal analyze morning-brief   # which steps repeat
hermes crystal try morning-brief       # save the routine and start testing it
hermes crystal test morning-brief      # run one test now
hermes crystal show morning-brief      # test results so far
hermes crystal promote morning-brief   # switch over (after 3 matching tests)
hermes crystal undo morning-brief      # put the original prompt back
```

In chat: `/crystallize status`. The same Try / Promote / Undo actions are in the panel.

## How a routine runs

Routine steps run through Hermes's own tool registry (`ctx.dispatch_tool`), with the
same approvals, redaction and budgets as if the model had called the tool. Each step has
a check (no tool error, exit code 0, output not suspiciously short). The first failing
step stops the routine and Hermes does the task itself.

## Disclosures

- **Reads your Hermes history (read-only):** session costs and tool calls from
  `state.db`, run outcomes from `cron/executions.db`, and job definitions from
  `cron/jobs.json`. Nothing is modified there.
- **Stores its own data** in `<hermes home>/plugin-data/crystallize/`: recorded tool calls
  (inputs and the first 2,000 characters of each result, adjustable in settings),
  routines, and a Desktop diagnostics log.
- **Runs tool calls during test runs:** after each scheduled run of a job that is being
  tested, its routine's steps run once more through Hermes's tool registry. Routines
  never contain steps that send or change things (messages, file writes, `git push`,
  POST requests), and any step that needed approval stays with Hermes.
- **Edits cron job prompts** only when you Promote or Undo, using `hermes cron edit`.
  Automatic undo after two failed runs also restores the original prompt.
- **No network calls of its own, no telemetry, no credentials.**

## Develop

```bash
git clone https://github.com/itsflownium/hermes-crystallize
ln -s "$PWD/hermes-crystallize" ~/.hermes/plugins/crystallize
hermes plugins enable crystallize
hermes plugins validate hermes-crystallize --install-deps
python -m pytest -c tests/pytest.ini --rootdir tests tests
```

MIT licensed.
