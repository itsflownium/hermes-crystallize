# When Crystallize saves, and when it can cost more

These come from live paired runs on Nous, with the same model and the same reasoning sent on both sides. Costs are list prices applied to real usage, not bills. Samples are small and the test files are synthetic, so treat the percentages as a guide. The raw results are in [`docs/evidence/`](evidence/).

## Where it can cost more

| Situation | What happened | What to do |
|---|---|---|
| **Removing a result by hand in a short chat, when the AI still needs it** | Typically 1.2 to 1.7 times the cost across 3 of 4 task types, because the AI had to get it back with extra calls | Remove results you're done with, or leave it to automatic removal, which weighs this risk first |
| **A cron job whose data changes on every run** | Each AI run cost about 15% more with a script attached, because the snapshot is added to the prompt, and nothing gets skipped | Don't attach a script to it |
| **A snapshot script that prints something that always changes**, such as a timestamp | The job looks changed every time, so it never skips | Print only the data that matters |
| **A single unlucky run** | One short task cost 1.5 times as much because the provider's cache missed, even with fewer tokens sent | Nothing to fix. Judge by several runs, because the other side also misses sometimes |
| **Hand-offs to a helper model on small tasks** | Not live-tested. The main model has to type the work out for the helper, at its own output price | Crystallize estimates this first and refuses a hand-off that won't pay. A helper with no known price stays off |
| **Trying things out** | Trials, Blind Taste Test and Model Auction make real model runs | Worth it once for a job you'll keep running. They're always your choice, except a trial's normal scheduled runs |

## Where it saves little

- **Short chats with small results.** Clean alone saved about 6% overall across 14 pairs, and a few task types were 1 to 3% dearer, which is within noise.
- **Plain text, logs and code.** Clean only removes wasted JSON formatting and color codes, because spacing can matter elsewhere. These tasks came out about even, except code, which varied too much to call.
- **Models with near-free cached reads.** Re-reading costs so little that removing results doesn't pay, so Crystallize doesn't remove them. DeepSeek saved about 7%.

## Ideal conditions for chat

- **The chat runs for many messages.** Working chats of 11 messages saved 23% on `tencent/hy3` and 20% on Kimi. Savings ranged from 5% to 34% per pair.
- **It reads large tool results early and moves on,** such as files, API responses and web pages.
- **Tools return JSON.** Clean cuts the most there.
- **Cached reads still cost something on your model.** The further they are from free, the more removal pays.

Turn it on after your first message with `/crystallize chat on`. It needs a known price, which it normally finds by itself.

## Ideal conditions for cron

- **The data is often unchanged.** With a script attached, an unchanged check made no AI call. One job that changed once in 4 checks cost 43% less, and one whose data stayed the same across its checks cost about 80% less.
- **It checks often for something that rarely changes,** such as a status page, a queue or a report feed.
- **It only reads,** with no skills or actions on its runs. Crystallize won't attach a script to a job with either.
- **Silence is fine when nothing changed.** An unchanged check sends nothing, and a job that delivers somewhere needs you to confirm this.
- **Its reads are simple,** so a script can cover them. Crystallize drafts one and names anything it couldn't cover.

As a rough rule, an attached job costs less when its data changes on fewer than about 8 in 10 runs, based on about 15% more per AI run. That comes from a small sample, so check the job's own results.
