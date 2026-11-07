# Reviewed native Cron sources

Native sources automate a deliberately chosen snapshot using Hermes's public
`cron edit --monitor-script` or `--script --no-agent` controls. They do not replay
an observed tool plan or call the unguarded nested dispatcher. Model/provider
selection, reasoning, toolsets, interpreter, workdir and the task prompt are not
edited. The initial integration supports local delivery and Python sources.

**Monitor:** the host runs the model on first or changed source output. The main
model keeps its configured reasoning and ordinary tool pipeline. A failed,
unknown, cancelled or missed answer changes the next source hash so that failure
cannot make an unchanged source suppress its retry. Missing ledger evidence,
concurrent attempts, changed configuration or source errors disable the shortcut
and wake a fresh normal agent run.

**Verbatim:** the approved source's nonempty stdout is the complete answer,
and the host delivers its text without inference. The reviewed host trims outer
whitespace and applies secret redaction, so byte-identical delivery is not
promised. This requires a separate explicit
declaration. Jobs with outcome contracts, skills or required actions are not
eligible for this mode. Failures remain visible failures, not invented answers.
This mode suits deterministic reports whose script already produces the entire
requested result; tasks that require interpretation retain the main agent.

## Review and apply

Place a Python snapshot source inside this profile's `scripts/` directory. It
must provide the complete, read-only latest-state input for this particular job.
It must not depend on mandatory repeated delivery, a reminder, a deadline or an
action on each run. The script runs with ordinary cron permissions; this is not
a sandbox or an automatic code-safety proof. Imports and external resources are
not frozen. Its code is copied into a private owned file for this approval, with
the job's existing interpreter/workdir (or the original source directory when no
workdir was configured). Scripts using `__file__`-relative assets
must account for that copied location.

From **Evidence and controls → Automatic source snapshots**, enter the relative
Python filename, select the behavior, declare the snapshot scope, and preview
the complete source. Preview executes nothing and calls no model. Inspect the
code and SHA, then explicitly approve the attachment. Editing a field clears
the preview/approval. A changed file, job or runtime invalidates a stale preview.

The same workflow is available in the CLI:

```bash
hermes crystal native-cron JOB_ID --source-script snapshot.py --snapshot-only
# Inspect the complete source and copy preview_sha256 from that response:
hermes crystal native-cron JOB_ID --source-script snapshot.py --snapshot-only \
  --apply --confirm --fingerprint FULL_PREVIEW_SHA256
```

For exact script output, add `--mode verbatim --verbatim-output` to both commands.
An existing native source must be undone before another is attached. The public
host flags are checked through read-only CLI help; an unsupported host cannot
enable the feature by a plugin setting or version-string guess.

## Limits and recovery

Sources are regular UTF-8 `.py` files under the selected profile's script root;
traversal, absolute paths and symlinks are refused. The approved code is limited
to 64 KiB and source execution has a 30-second child timeout. Source output must
be complete UTF-8 and at most 6,000 characters; the monitor's complete serialized
payload must fit within 7,000 characters, below the reviewed host's 8,000-character
context cap. The source is not silently trimmed to fit. Verbatim mode refuses an
empty output and the host's reserved final `wakeAgent=false` silent-control line.

Source SHA, native-field ownership and task configuration are checked at runtime.
The profile config's byte hash is included in monitor data, so a setting/model
change forces a fresh answer and can then stabilize without another approval.
Verbatim text output does not depend on the global model selection. Scheduler claims, run counters,
failure stamps and completion IDs are bookkeeping and do not invalidate a source.
Changing an outcome contract revokes the existing approval before saving it.
Monitor mode falls back to a fresh normal run; verbatim mode produces a visible
error and requires review/Undo. No source self-updates or pulls new code.

Undo restores only the fields this attachment owns and preserves newer external
edits. Timeouts retain pending recovery. Copied sources, wrappers and approval
records remain under `scripts/crystallize-native/` for review; this may include
sensitive source text. Do not delete an artifact while a job still refers to it.

```bash
hermes crystal native-cron JOB_ID
# Copy config_fingerprint from that fresh status:
hermes crystal native-cron JOB_ID --undo --confirm --fingerprint CURRENT_CONFIG_FINGERPRINT
```

The public CLI has no atomic compare-and-swap operation. Crystallize rechecks
configuration immediately before edits, but another process can still edit in
the short check/call window. Only the listed native fields are sent; model and
reasoning edits are never part of the command.

This is provider-independent scheduling functionality, not evidence of a cash
saving on every task. Zero-inference ticks and fixed-input token reductions must
be reported separately from variable reasoning/output usage and actual billing.

## Reproduce the scheduled check

```bash
python scripts/validation_native_cron.py --model YOUR_EXACT_MODEL \
  --report work/native-scheduled.json
```

This creates one temporary local profile, uses public CLI commands, waits for a
real due schedule and verifies the native execution/output ledger. It never asks
for inference, copies credentials, changes a gateway or edits production jobs.
`--hermes-command` accepts a JSON argv array for a nonstandard launcher; no shell
command is evaluated. A native monitor's paid model turn needs a separate bounded
Nous authorization; this check alone does not establish that end-to-end result.
