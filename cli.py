"""`hermes crystal ...` terminal commands."""

from __future__ import annotations

from typing import Any, Optional

from . import history


def _money(v: Optional[float]) -> str:
    return "—" if v is None else f"${v:.4f}"


def _num(v: Optional[float], fmt: str = "{:.1f}") -> str:
    return "—" if v is None else fmt.format(v)


def _short(text: Any, limit: int = 70) -> str:
    s = text if isinstance(text, str) else repr(text)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _print_summaries() -> None:
    rows = history.job_summaries()
    if not rows:
        print("No cron runs recorded yet. Runs appear here after a scheduled job fires.")
        return
    print(f"{'job':<20} {'runs':>5} {'usable':>7} {'tools/run':>10} {'cost/run':>10} {'secs/run':>9}")
    for r in rows:
        print(f"{_short(r['job_name'] or r['job_id'], 20):<20} {r['runs']:>5} {r['usable_runs']:>7} "
              f"{_num(r['avg_tool_calls']):>10} {_money(r['avg_cost_usd']):>10} "
              f"{_num(r['avg_duration_s']):>9}")
    print("\nUsable = on schedule, finished cleanly, called at least one tool.")


def _run_flag(run: history.Run) -> str:
    """Why a run isn't used for learning. Making no tool calls isn't a problem: it's how
    a live routine's run should look, so it gets a plain note instead."""
    if not run.scheduled:
        return "  (not used for learning: manual run)"
    if run.outcome not in (None, "completed"):
        return f"  (not used for learning: {run.outcome})"
    return "  (wrote the answer straight from its data)" if not run.steps else ""


def _print_job(job: str) -> None:
    runs = history.load_runs(job)
    if not runs:
        print(f"No recorded runs for {job!r}.")
        return
    for i, run in enumerate(runs, 1):
        flag = _run_flag(run)
        tokens = "" if run.total_tokens is None else f"  {run.total_tokens:,} tokens"
        api = "" if run.api_cost_usd is None else f" (included; {_money(run.api_cost_usd)} at API prices)"
        print(f"Run {i}  {run.execution_id[:8]}  {_money(run.cost_usd)}{api}{tokens}  "
              f"{_num(run.duration_s)}s  {len(run.steps)} tool calls{flag}")
        for s in run.steps:
            print(f"    {s.tool_name:<16} {_short(s.args)}")


_KIND_LABEL = {
    "fixed": "fixed    ",
    "fill-in": "fill-in  ",
    "varies": "varies   ",
    "setup": "setup    ",
    "side": "agent    ",
}


def _print_analysis(job: str, as_json: bool) -> None:
    import json

    from . import analyze

    a = analyze.analyze_job(job)
    if as_json:
        print(json.dumps(a.to_dict(), indent=2, default=str))
        return
    name = a.job_name or job
    print(f"{name}: {a.runs_used} usable runs. {a.reason}")
    if not a.steps:
        return
    print(f"Same shape in {a.same_shape_share:.0%} of runs; "
          f"{a.extra_steps_per_run:.1f} extra calls per run on average.\n")
    for p in a.steps:
        extra = f"  blanks: {', '.join(p.fill_ins)}" if p.fill_ins else ""
        print(f"  {p.position + 1:>2}. {_KIND_LABEL.get(p.kind, p.kind)} {p.tool_name:<14} "
              f"{_short(p.args, 60)}{extra}")
        if p.note:
            print(f"      {p.note}")
    print(f"\nNow:       {_num(a.avg_api_calls)} AI calls, {_money(a.avg_cost_usd)} per run")
    print(f"Estimated: {_num(a.est_api_calls_after)} AI calls, {_money(a.est_cost_after_usd)} per run "
          "(rough until test runs measure it)")
    if a.agent_free:
        print("Every step is fixed: this job could run with no AI at all.")


def _routine_for(job: str):
    from . import store

    for r in store.list_routines():
        if r["source"] == "cron" and job in (r["name"], r["source_ref"], r["id"]):
            return store.get_routine(r["id"])
    return None


def _print_routine(r: dict) -> None:
    from . import steplist

    meta = r.get("meta") or {}
    print(f"{r['name']}  [{r['status']}]  id {r['id']}")
    if r["status"] == "testing":
        print(f"  Test runs matching in a row: {meta.get('matches', 0)} of {meta.get('needed', 3)}"
              + ("  → ready to promote" if meta.get("ready") else ""))
        for t in (meta.get("tests") or [])[-3:]:
            print(f"    {'match' if t['ok'] else 'no match'} ({t['source']}): {t['why']}")
    if r["status"] == "live":
        mode = {"no_ai": "no AI", "prerun": "pre-run script"}.get(meta.get("mode"), "crystal_run tool")
        print(f"  Live runs: {meta.get('live_runs', 0)}, skipped (no change): {len(meta.get('skips') or [])}, "
              f"failures in a row: {meta.get('failures', 0)}  [{mode}]")
        from . import prerun

        print(f"  When nothing has changed: {prerun.unchanged_action(r)}")
    steps = r["steps"] if isinstance(r["steps"], dict) else {}
    for line in steplist.describe(steps):
        print(f"  {line}")


def _lifecycle_action(sub: str, job: str, args: Any = None) -> None:
    from . import lifecycle, tools

    try:
        if sub == "try":
            r = lifecycle.try_job(job)
            print("Saved as a routine. It's now being tested after every scheduled run.")
            _print_routine(r)
            return
        r = _routine_for(job)
        if sub == "test":
            meta = lifecycle.test_now(job, tools.dispatch)
            print(f"Test run: {meta['tests'][-1]['why']}. Matching in a row: {meta['matches']} of {meta['needed']}.")
        elif sub == "promote":
            if not r:
                print(f"No routine for {job!r}. Run `hermes crystal try {job}` first.")
                return
            no_ai = bool(getattr(args, "no_ai", False))
            lifecycle.promote(r["id"], mode="no_ai" if no_ai else "prerun")
            if no_ai:
                print(f"{r['name']} is live with no AI: the routine's output is the job's result.")
            else:
                print(f"{r['name']} is live. A pre-run script now collects the data before the AI starts; "
                      "see what it does when nothing changed with `hermes crystal show`.")
        elif sub == "mode":
            if not r:
                print(f"No routine for {job!r}.")
                return
            mode = {"ai": "prerun", "no-ai": "no_ai"}[args.mode]
            lifecycle.set_mode(r["id"], mode)
            print(f"{r['name']} now runs " + ("with no AI." if mode == "no_ai" else
                  "with the AI writing the answer from the pre-run data."))
        elif sub == "skip":
            if not r:
                print(f"No routine for {job!r}.")
                return
            choice = {"off": "run"}.get(args.state, args.state)
            lifecycle.set_when_unchanged(r["id"], choice)
            print({"repeat": "When nothing has changed, one short AI call repeats the last answer.",
                   "quiet": "When nothing has changed, no AI runs and no message goes out.",
                   "run": "The AI will run every time, even when the data hasn't changed.",
                   "auto": "Crystallize will choose: quiet if the job only saves locally or already "
                           "stays silent at times, otherwise repeat the last answer."}[choice])
        elif sub == "undo":
            if not r:
                print(f"No routine for {job!r}.")
                return
            lifecycle.undo(r["id"])
            print(f"Undone. {r['name']} is back to its original prompt.")
        elif sub == "show":
            _print_routine(r) if r else print(f"No routine for {job!r}.")
    except lifecycle.LifecycleError as exc:
        print(str(exc))


def _prerun(routine_id: str) -> None:
    import sys

    from . import prerun, tools

    try:
        text, code = prerun.run(routine_id, tools.dispatch)
    except Exception as exc:  # noqa: BLE001 — the AI must still get to do the job
        text, code = f"Crystallize: the routine couldn't run ({type(exc).__name__}: {exc}). Do the whole task yourself.", 0
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    if code:
        sys.exit(code)


def _print_tune_list() -> None:
    from . import tuning

    tuning.refresh()
    cards = tuning.cards()
    if not cards:
        print("No AI cron jobs yet.")
        return
    print(f"{'job':<20} {'reasoning':>10} {'of cost':>8} {'cost/run':>10} {'API price':>10} {'tokens':>8}  status")
    for c in cards:
        share = "—" if c["reasoning_share"] is None else f"{c['reasoning_share']:.0%}"
        tokens = "—" if c["tokens_now"] is None else f"{c['tokens_now']:,.0f}"
        print(f"{_short(c['name'], 20):<20} {c['effort'] or 'default':>10} {share:>8} "
              f"{_money(c['cost_now']):>10} {_money(c['api_cost_now']):>10} {tokens:>8}  {c['status_text']}")


def _print_tune_job(name: str) -> None:
    from . import tuning

    t = tuning.status(name)
    if not t:
        print(f"{name} hasn't been tuned. Try it with: hermes crystal tune {name}")
        return
    print(f"{name}: {tuning.status_text(t)}")
    answers = tuning.sample_answers(name, t)
    for label, text in (("Before", answers.get("before")), ("With lower reasoning", answers.get("after"))):
        if text:
            print(f"\n--- {label} ---\n{text[:1500]}")


def _tune(args: Any) -> None:
    from . import lifecycle, tuning

    job = getattr(args, "job", None)
    if getattr(args, "all", False):
        result = tuning.start_all()
        print(f"Started on: {', '.join(result['started']) or 'nothing'}")
        for name, why in result["skipped"].items():
            print(f"  {name}: {why}")
        return
    if not job:
        _print_tune_list()
        return
    try:
        if getattr(args, "undo", False):
            tuning.undo(job)
            print(f"{job} is back to its previous reasoning setting.")
        elif getattr(args, "status", False):
            tuning.refresh()
            _print_tune_job(job)
        else:
            t = tuning.start(job, args.level)
            later = " If its answers suffer, it gives back tools and reasoning a step at a time." if t["stages"] else ""
            print(f"Trying {tuning._settings(t)} on {t['job_name']}. After {tuning.TRIAL_RUNS} answers it's compared "
                  f"with earlier runs and kept only if they hold up and it costs less.{later}")
    except lifecycle.LifecycleError as exc:
        print(str(exc))


def _handle(args: Any) -> None:
    sub = getattr(args, "crystal_command", None)
    if sub == "prerun":
        _prerun(args.routine_id)
        return
    if sub in ("try", "test", "promote", "undo", "show", "skip", "mode"):
        _lifecycle_action(sub, args.job, args)
        return
    if sub == "tune":
        _tune(args)
        return
    if sub == "history":
        job = getattr(args, "job", None)
        _print_job(job) if job else _print_summaries()
    elif sub == "analyze":
        _print_analysis(args.job, getattr(args, "json", False))
    else:
        print("Usage: hermes crystal history [job] | analyze <job> [--json] | try|test|promote|undo|show <job> | mode <job> ai|no-ai | skip <job> auto|repeat|quiet|off | tune [job] [--level L] [--undo]")


def _setup(subparser: Any) -> None:
    subs = subparser.add_subparsers(dest="crystal_command")
    h = subs.add_parser("history", help="Show recorded cron runs and what they cost")
    h.add_argument("job", nargs="?", help="Job name or id to show run by run")
    a = subs.add_parser("analyze", help="Find the steps a job repeats every run")
    a.add_argument("job", help="Job name or id")
    a.add_argument("--json", action="store_true", help="Print the full analysis as JSON")
    for name, text in (("try", "Save a job's steps as a routine and start testing it"),
                       ("test", "Run the routine once now and compare with the latest real run"),
                       ("promote", "Switch the job over to its routine (needs 3 matching test runs)"),
                       ("undo", "Put the job's original prompt back"),
                       ("show", "Show a job's routine and its test results")):
        p = subs.add_parser(name, help=text)
        p.add_argument("job", help="Job name or id")
        if name == "promote":
            p.add_argument("--no-ai", action="store_true",
                           help="Use the routine's output as the job's result, with no AI at all")
    sk = subs.add_parser("skip", help="What a live job does when its data hasn't changed")
    sk.add_argument("job", help="Job name or id")
    sk.add_argument("state", choices=("auto", "repeat", "quiet", "off"),
                    help="repeat the last answer, stay quiet, off = run the AI, auto = choose for me")
    md = subs.add_parser("mode", help="Switch a live job between AI-written answers and no AI")
    md.add_argument("job", help="Job name or id")
    md.add_argument("mode", choices=("ai", "no-ai"))
    tn = subs.add_parser("tune", help="Trim a job's tools and reasoning, and keep that only if its answers hold up and it costs less")
    tn.add_argument("job", nargs="?", help="Job name or id. Leave out to list every job")
    tn.add_argument("--level", help="Try only this level (default: the levels the job's model takes, weakest first)")
    tn.add_argument("--all", action="store_true", help="Start on every job that can have it")
    tn.add_argument("--undo", action="store_true", help="Put the job's previous setting back")
    tn.add_argument("--status", action="store_true", help="Show the result, with an answer from before and after")
    pr = subs.add_parser("prerun", help="(used by a promoted job's pre-run script) run a routine and print its data")
    pr.add_argument("routine_id")
    subparser.set_defaults(func=_handle)


def register(ctx: Any) -> None:
    ctx.register_cli_command(
        name="crystal",
        help="Crystallize: inspect recorded runs and saved routines",
        setup_fn=_setup,
        handler_fn=_handle,
    )
