<p align="center">
  <img src="assets/crystallize-logo.svg" alt="Crystallize" width="380">
</p>

**Hermes cron jobs that cost a lot less, on any cloud model, with proof.**

A cron job costs roughly **its prompt size × its AI calls, plus reasoning**. Crystallize shows where
each job's money goes, works on all of it, and keeps a change only if the job's answers hold up and its
cost really fell. If a change doesn't pay off, the old settings come back by themselves.

It works with every cloud provider in Hermes: API keys, OAuth and subscriptions such as Codex. Levels
and prices are read from Hermes, so new models and providers work without an update.

### Measured on real cron jobs

The same feed-digest job per model, comparing its first 2 normal runs with its latest runs on full savings
(1–2 runs each so far, so treat these as early numbers). Codex runs are included in the plan, so their cost
is shown at API prices.

| Model | Route | AI calls | Tokens | Time | Cost per run | Saved |
|---|---|---|---|---|---|---|
| DeepSeek V4.1 Flash | Nous Portal | 6.5 → 1 | 359k → 11k | 364s → 9s | $0.0247 → $0.0010 | **96%** |
| GPT-6 Sol | Codex OAuth | 8 → 1 | 275k → 12k | 109s → 12s | $0.2418 → $0.0230 | **90%** |
| GLM 5.3 Flash | Nous Portal | 4.5 → 1 | 90k → 6k | 79s → 16s | $0.0073 → $0.0008 | **89%** |
| GPT-6 Luna | Codex OAuth | 5 → 1 | 141k → 11k | 107s → 12s | $0.0073 → $0.0012 | **84%** |
| Kimi K3 | Nous Portal | 3 → 1 | 54k → 9k | 47s → 10s | $0.0408 → $0.0118 | **71%** |

Trials on more models also kept their savings, with the same job. These were judged on 2 or more runs without routines:

| Model | Route | Cost per run | Saved |
|---|---|---|---|
| Claude Haiku 5.5 | Nous Portal | $0.0228 → $0.0021 | **91%** |
| Gemini 3.8 Flash | Nous Portal | $0.0942 → $0.0150 | **84%** |
| GPT-5 Nano | Nous Portal | $0.0043 → $0.0009 | **80%** |
| MiniMax M3 | Nous Portal | $0.0207 → $0.0052 | **75%** |
| Mistral Small 3.2 | Nous Portal | $0.0034 → $0.0012 | **65%** |
| Granite 4.2 8B | Nous Portal | $0.0026 → $0.0010 | **62%** |
| DeepSeek V4 Flash | Nous Portal | $0.0015 → $0.0006 | **58%** |
| Gemma 4 26B | Nous Portal | $0.0035 → $0.0015 | **57%** |
| Solar Mini 4 | Nous Portal | $0.0008 → $0.0004 | **49%** |
| Mercury 3 | Nous Portal | $0.0015 → $0.0014 | **12%** |

Qwen 3.8 Flash also passed. Hermes has no price for its runs, so its saving is an estimate at API prices: about 59%.
At API-price estimates, its runs went from about $0.0046 to $0.0019.
Llama 4 Scout and Nova Lite didn't complete their normal runs on this job (an empty answer and an HTTP 400 error), and
Step 3.5 Flash was rejected by its provider, so none of the three could start a trial.

Two other kinds of job also passed: one that writes to a file on every run saved 63% at API prices, and one with its
own pre-run script saved 67%.

Savings vary by job and model. Cloud providers other than Nous Portal and Codex use the same lookups but haven't had a real run. If a
job doesn't get cheaper on yours, it goes back to its old settings by itself.

## The savings ladder

Each job uses every step it can, from the biggest saving down. The panel shows each job's ladder.

| Step | What it does | Works on |
|---|---|---|
| **No AI at all** | The routine's output is the result, at $0 | Status-style jobs |
| **Nothing changed** | Stays quiet at $0, or one short call repeats the last answer if someone expects a message | Jobs with a routine |
| **Fetch before the AI starts** | A tested routine collects the data, so the AI often needs one call | Jobs with stable steps |
| **Fewer AI calls** | A short instruction to use only the tools the task needs, once each | Any AI job |
| **Cleaned tool results** | Removes markup and API link fields before the AI reads them, so no later call pays for them. Uses no AI and never touches file contents or errors | Any job using web or shell tools |
| **New items only** | Sends what changed, plus the last answer to update | Live routines |
| **Only the tools it uses** | Starts with the toolsets its cheapest good run used, for a smaller prompt on every call | Any AI job |
| **Lower reasoning (optional)** | Off by default, so jobs keep your setting. Turn it on and it tries the weakest level the job's model takes | Models with reasoning settings |

## How it decides

1. **Measure.** Real cost per run. A subscription run costs $0 and some routes have no price, so those are estimated from their tokens and the same model's listed API price. A model on no price list compares tokens, then time.
2. **Trial.** Click **Make cheaper** (or **Make all jobs cheaper**), or run `hermes crystal tune <job>`. Earlier runs from Hermes's own records count, so a slow job doesn't wait weeks to start.
3. **Check.** After 2 answers: did any run fail, did it stop an action it normally takes, are answers much shorter or missing items or links, and did the cost really fall? Close results get more runs.
4. **Keep or adjust.** If answers suffer, tools and then reasoning come back a step at a time. If nothing works, or it wasn't cheaper, the old settings return. Your global settings never change.
5. **Keep watching.** Settings that later fail 3 runs in a row go back by themselves.

**What the checks can't catch:** subtle mistakes in an answer, or an action or tool a job only uses now and
then. `hermes crystal tune <job> --status` shows an answer from before and after to read yourself.

## Routines

`hermes crystal analyze <job>` lines up a job's runs and labels each step: `fixed`, `fill-in` (dates, ids and
temp paths are refreshed each run), `varies`, `setup` (skipped) or `agent` (always stays with the AI).
After a routine matches real runs three times in a row, one click promotes it. Hermes's cron `script` field
then runs it before the AI starts. If it fails, Hermes does the whole task itself, and two failures in a row
undo the switch.

- **Jobs that act.** A routine only fetches data that comes before a job's first action. The AI still sends or changes what the job is meant to.
- **Jobs with their own script.** Your script still runs first, can still stop the run, and its output stays in the prompt.

## The panel

A Crystallize button in the top bar and the status bar opens a panel beside the chat, with Cron and Chat sections. It updates from your real cron jobs: running jobs first, with their schedule, model,
reasoning and tools; a cost graph for each job; time ranges that appear only when they have data; and
finished jobs collapsed with their full history. Totals keep real money and plan API prices apart.

## Install

```bash
hermes plugins install itsflownium/hermes-crystallize
hermes plugins enable crystallize
```

Then in Hermes Desktop: **Capabilities → Plugins → Crystallize**, turn on the Desktop
switch. Works on macOS, Windows and Linux. Requires Hermes 0.22.7 or newer.

## Use it

```bash
hermes crystal tune                    # every AI job: reasoning share, cost, API price, tokens
hermes crystal tune morning-brief      # make this job cheaper (or --all for every job)
hermes crystal tune morning-brief --status   # the result, with an answer from before and after
hermes crystal tune morning-brief --undo     # put its old settings back
hermes crystal history                 # every cron job: runs, cost, time
hermes crystal analyze morning-brief   # which steps repeat
hermes crystal try morning-brief       # save the routine and start testing it
hermes crystal test morning-brief      # run one test now
hermes crystal show morning-brief      # test results so far
hermes crystal promote morning-brief   # switch over (after 3 matching tests)
hermes crystal mode site-check no-ai   # a status check that needs no AI
hermes crystal skip morning-brief repeat   # when nothing changed: repeat, quiet, off or auto
hermes crystal undo morning-brief      # put the original prompt back
```

## Crystallize Chat

| | Crystallize Cron | Crystallize Chat |
|---|---|---|
| **For** | Scheduled jobs | The chat you're in |
| **Turned on** | Per job, with **Make cheaper** | Per chat, after your first prompt |
| **How it saves** | Fewer AI calls and tools, cleaned results, lower reasoning, routines | Routine work sent to a cheap model, cleaned results, big ones condensed |
| **Proof** | Each job's cost is measured, and changes that don't help are undone | Savings shown are estimates |

For long or expensive chats, your main model keeps the thinking. For routine work, it sends an input to a cheap
model you pick with `crystal_cheap` and gets the output straight back, deciding by itself when that's worth it.
The cheap model also reads big tool results first. A result is paid for again on every later message, so reading less adds up. Open **Crystallize
Chat** in the sidebar, or the **Chat** section of the panel, and pick a model from a subscription you've connected to Hermes. Hermes keeps your
sign-ins; Crystallize never sees a key.

Turn it on per chat in the panel's **Chat** section, or with `/crystallize chat on` on platforms without the panel.

**You can't start a chat with it.** Send your first prompt, then turn it on. Hermes only creates the chat when
that prompt is sent, and savings come from tool results, which don't exist until the work starts.

- Tool results are cleaned with no AI. Big ones (8,000+ characters) are condensed if you've picked a model.
- When a result is noticeably reduced, the exact original is kept for 7 days, and your main model can read it with `crystal_full_result`.
- File contents, errors and small results always stay exact.
- Your main model isn't switched mid-chat, because that would lose its prompt cache.
- If your picked model isn't cheaper than your main one, results are only cleaned.
- The panel has two sections, Cron and Chat. Chat shows savings for the chat you're in. The figures are estimates.
- A summary can still leave out something that matters, so it's off by default.

`/crystallize` shows what's happening with your scheduled jobs and this chat. It works in any chat, including Telegram and Discord.

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
- **Crystallize Chat sends tool results to the model you pick,** only in chats where you turned it on, and keeps originals in its data folder for 7 days.
- **Stores its own data** in `<hermes home>/plugin-data/crystallize/`: recorded tool calls
  (inputs and the first 2,000 characters of each result, adjustable in settings),
  routines, and a Desktop diagnostics log.
- **Runs tool calls during test runs:** after each scheduled run of a job that is being
  tested, its routine's steps run once more through Hermes's tool registry. Routines
  never contain steps that send or change things (messages, file writes, `git push`,
  POST requests), and any step that needed approval stays with Hermes.
- **Edits cron jobs** only when you Make cheaper, Promote, change mode or Undo: prompt, `script`,
  no-agent mode and reasoning through `hermes cron edit`, and toolsets through Hermes's job update.
  A trial that doesn't pass, and automatic undo after two failed runs, restore the earlier settings.
- **Changes tool results for jobs on cheaper settings.** Web and shell results are cleaned before
  the AI reads them (markup, whitespace and API link fields). File contents, errors and small results are untouched.
- **Writes one small script per live job** in `<hermes home>/scripts/crystallize/`. It runs the
  job's own pre-run script if it has one, then `hermes crystal prerun <routine>`. Undo deletes it.
- **Uses some Hermes internals** for prices, reasoning levels and toolsets, which public plugin
  APIs don't cover yet. Each falls back safely if a Hermes update moves it.
- **Local models aren't a focus yet.** Crystallize is built and tested for cloud providers.
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
