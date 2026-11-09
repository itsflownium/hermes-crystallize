# Demo script

## Recorded demo

[`assets/demo/crystallize-demo.mp4`](../assets/demo/crystallize-demo.mp4) (3 min 32 s, narrated) was recorded live in Hermes Desktop
(the Webapp, v0.23.0) on Nous Portal on 9 November 2026. Nothing in it is mocked: the cron job, chat and
panel figures are real runs, sped up where the AI was working. The voice-over is macOS text to speech.

| What | Route | Result |
|---|---|---|
| Cron job skipping unchanged checks | `tencent/hy3`, reasoning low | 6 checks since attaching cost $0.0053 against an expected $0.0134 (60% less): 4 skipped, plus 2 AI runs (the first data and a real billing failure, which it reported) |
| Working chats, Crystallize on vs off | `tencent/hy3`, low | 33% cheaper (2 pairs: 39%, 27%); automatic Context Rent removed 4 old results, none read back |
| | `stepfun/step-3.7-flash`, low | 13% cheaper (1 valid pair; the other was invalid because a run errored) |
| | `deepseek/deepseek-v4.1-flash`, low | 5% cheaper (pairs: +0.7%, −10%); cached reads cost almost nothing there, so only Clean applied |

The pair reports are in `docs/evidence/demo-chat-*.json`. Costs are the portal's list prices for the exact
tokens each side used, with the same model, provider and reasoning on both sides. These are small samples.

The video also shows a case that costs more: asking for a detail from a removed file makes the AI read it
back, which turned that short chat's net slightly negative (−$0.0009). The panel shows that figure too.

## Recording it yourself

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
