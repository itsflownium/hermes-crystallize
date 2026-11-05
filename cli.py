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


def _print_job(job: str) -> None:
    runs = history.load_runs(job)
    if not runs:
        print(f"No recorded runs for {job!r}.")
        return
    for i, run in enumerate(runs, 1):
        flag = "" if run.usable else "  (skipped: " + (
            "manual run" if not run.scheduled else f"outcome {run.outcome}") + ")"
        print(f"Run {i}  {run.execution_id[:8]}  {_money(run.cost_usd)}  "
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
        print(f"  Live runs: {meta.get('live_runs', 0)}, failures in a row: {meta.get('failures', 0)}")
    steps = r["steps"] if isinstance(r["steps"], dict) else {}
    for line in steplist.describe(steps):
        print(f"  {line}")


def _lifecycle_action(sub: str, job: str, ctx: Any) -> None:
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
            lifecycle.promote(r["id"])
            print(f"{r['name']} is live. Its cron job now calls crystal_run first and falls back to the full task.")
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


def _handle(args: Any) -> None:
    sub = getattr(args, "crystal_command", None)
    if sub in ("try", "test", "promote", "undo", "show"):
        _lifecycle_action(sub, args.job, None)
        return
    if sub == "history":
        job = getattr(args, "job", None)
        _print_job(job) if job else _print_summaries()
    elif sub == "analyze":
        _print_analysis(args.job, getattr(args, "json", False))
    else:
        print("Usage: hermes crystal history [job] | analyze <job> [--json] | try|test|promote|undo|show <job>")


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
    subparser.set_defaults(func=_handle)


def register(ctx: Any) -> None:
    ctx.register_cli_command(
        name="crystal",
        help="Crystallize: inspect recorded runs and saved routines",
        setup_fn=_setup,
        handler_fn=_handle,
    )
