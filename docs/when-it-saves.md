# When Crystallize saves, and when it can cost more

These come from live paired runs on Nous, with the same model and the same reasoning sent on both sides. Costs are list prices applied to real usage, not bills. Samples are small and the test files are synthetic, so treat the percentages as a guide. The raw results are in [`docs/evidence/`](evidence/).

## Where it can cost more

| Situation | What happened | What to do |
|---|---|---|
| **Removing a result by hand in a short chat, when the AI still needs it** | Typically 1.2 to 1.7 times the cost across 3 of 4 task types, because the AI had to get it back with extra calls | Remove results you're done with, or leave it to automatic removal, which weighs this risk first |
| **A cron job whose data changes nearly every run** | With a drafted script, a woken run cost about 5% more than a normal one in one live pair, and almost nothing gets skipped | Check the job's verdict first. After attaching, its card shows its real cost and warns you if that's more |
| **A hand-written script that prints the data** | The AI paid for the data twice, and each woken run missed the cache. One job cost 21% more overall despite skipping half its checks | Use a drafted script, which prints only a fingerprint, or print little yourself |
| **A snapshot script that prints something that always changes**, such as a timestamp | The job looks changed every time, so it never skips | Print only the data that matters |
| **A single unlucky run** | One short task cost 1.5 times as much because the provider's cache missed, even with fewer tokens sent | Nothing to fix. Judge by several runs, because the other side also misses sometimes |
| **Hand-offs to a helper model** | In two live tests on Codex they cost 18 to 23% more, because the main model often wrote the helper's answer out itself anyway | They're off by default. If you turn them on, they stop in a chat once an answer is retyped |
| **Trying things out** | Trials, Blind Taste Test and Model Auction make real model runs | Worth it once for a job you'll keep running. They're always your choice, except a trial's normal scheduled runs |

## What it no longer costs

Crystallize's own tools used to be sent on every AI call in every chat, about 3,300 characters, even where it was off. They're now left out unless Crystallize Chat is on in that chat, so having it installed costs nothing where you don't use it.

## Where it saves little

- **Short chats with small results.** Clean alone saved about 6% overall across 14 pairs, and a few task types were 1 to 3% dearer, which is within noise.
- **Plain text, logs and code.** Clean only removes wasted JSON formatting and color codes, because spacing can matter elsewhere. These tasks came out about even, except code, which varied too much to call.
- **Models with near-free cached reads.** Re-reading costs so little that removing results doesn't pay, so Crystallize doesn't remove them. DeepSeek saved about 7%, and one of its three pairs cost 8% more.

## Ideal conditions for chat

- **The chat runs for many messages.** Working chats of 11 messages saved 23% on `tencent/hy3` in two separate sets of 3 pairs, and 20% on Kimi, and 23-message chats saved 32% on `tencent/hy3`. Savings grow with length. Per pair they ranged from 5% to 37%, and none of these chats cost more on `tencent/hy3` or Kimi. On Codex, the same chats saved 13% on `gpt-5.6-terra` and 19% on `gpt-5.6-luna`, from Clean alone.
- **It reads large tool results early and moves on,** such as files, API responses and web pages.
- **Tools return JSON.** Clean cuts the most there.
- **Cached reads still cost something on your model.** The further they are from free, the more removal pays.

Turn it on after your first message with `/crystallize chat on`. It needs a known price, which it normally finds by itself.

## Ideal conditions for cron

- **The data is usually unchanged.** An unchanged check makes no AI call. With the drafted script, a job whose data changed once in 4 checks cost 60% less in one live pair on Nous, or about 47% less leaving out a cache miss on the normal job's side, and 49% less on Codex.
- **It checks often for something that rarely changes,** such as a status page, a queue or a report feed.
- **It only reads,** with no skills or actions on its runs. Crystallize won't attach a script to a job with either.
- **Silence is fine when nothing changed.** An unchanged check sends nothing, and a job that delivers somewhere needs you to confirm this.
- **Its reads are simple,** so a script can cover them. Crystallize drafts one and names anything it couldn't cover.

As a rough rule, attach a script when the data changes on fewer than about 8 in 10 runs. That comes from one pair on a fast schedule, so check the job's own card after attaching.
