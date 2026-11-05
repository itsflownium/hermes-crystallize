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

- **Recording.** Tool calls in scheduled cron runs, and in chats where you turned Crystallize on.
- **Analysis.** `hermes crystal analyze <job>` lines up a job's runs and labels each step:
  - `fixed`: the same every run.
  - `fill-in`: the same apart from dates, ids or temp paths, which are refreshed each run.
  - `varies`: same tool, different inputs. Frozen from the latest run, then tested.
  - `setup`: loads context for the agent (skills, todo lists). Skipped.
  - `agent`: sends or changes something. Always stays with Hermes.
- **Proof before switching.** After every scheduled run, the routine's steps run too
  (tool calls only, no AI) and their outputs are compared with what the real run got.
  It needs three matches in a row before it can be promoted.
- **Promote and Undo.** Promote gives the job a pre-run script (Hermes's own cron `script`
  field). The routine runs before the AI starts and its data goes straight into the prompt,
  so the AI only writes the answer. Undo restores the original prompt exactly and removes
  the script.
- **Skip when nothing changed.** If a live job's data is the same as last time (timings
  and timestamps aside), the AI is skipped and the run costs nothing. `hermes crystal skip
  <job> off` turns this off.
- **No-AI mode.** When a job's answers are just its data restated (a status check, for
  example), Crystallize offers to run it with no AI at all: `hermes crystal mode <job> no-ai`.
- **Fair numbers.** Before and after are medians, so one odd run can't hide the trend.
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
hermes crystal mode site-check no-ai   # a status check that needs no AI
hermes crystal skip morning-brief off  # run the AI even when nothing changed
hermes crystal undo morning-brief      # put the original prompt back
```

Crystallize is **off for each chat until you turn it on.** Click **◆ Crystallize** in the message box:
it puts `/crystallize on` in the box, and nothing changes until you press Enter. Click it again
for `/crystallize off`. While a chat is on, its tool calls are recorded and the panel analyses it.
Scheduled cron runs are always recorded. `/crystallize status` works in any chat, including
Telegram and Discord. The Try / Promote / Undo actions are in the panel.

## How a routine runs

Routine steps run through Hermes's own tool registry (`ctx.dispatch_tool`), with the
same approvals, redaction and budgets as if the model had called the tool. Each step has
a check (no tool error, exit code 0, output not suspiciously short). The first failing
step stops the routine and Hermes does the task itself. Two failed runs in a row undo the
routine automatically.

Before the data reaches the AI it is cleaned: HTML and RSS/Atom feeds become plain text,
JSON loses API link fields and empty values, files a step wrote are included, and every
source gets a fair share of the space, so no feed is dropped because another was long.

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
- **Edits cron jobs** only when you Promote, change mode or Undo, using `hermes cron edit`
  (prompt, `script`, and no-agent mode). Automatic undo after two failed runs also restores
  the original prompt.
- **Writes one small script per live job** in `<hermes home>/scripts/crystallize/`. It only
  runs `hermes crystal prerun <routine>`, and Undo deletes it.
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
