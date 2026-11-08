# Demo script

A short walkthrough for recording, about three minutes. It needs one model route and a cron job you can run a few times.

## 1. A cron job that skips when nothing changed (about 90 seconds)

1. Create a small job that reads a file and summarises it:
   ```bash
   hermes cron create "every 2m" "Run cat ~/demo/status.json with the terminal tool, then report any failed service." --name demo-status
   ```
2. Let it run three times, then draft a script from what it did:
   ```bash
   hermes crystal native-cron JOB_ID --draft
   ```
3. In the panel, open **Skip runs when nothing changed**, show the drafted script, and attach it.
4. Show the next run calling the model, the unchanged run making no model call, and a run after you edit `status.json` calling the model again.

## 2. What a chat keeps paying to re-read (about 45 seconds)

1. In a chat, read a large file, then ask something unrelated.
2. Open **Context Rent** in the Chat tab and show what that result costs on each call.
3. Remove it, ask another question, then ask for a detail from it, so the AI reads the original back.

## 3. Keeping the comparison honest (about 45 seconds)

1. Start **Try lower-cost settings** on a job and show that its model and reasoning don't change.
2. Show a trial's result, including any that failed and restored its settings.
3. Point out that a saving only counts between runs with matching settings.
