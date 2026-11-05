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


def _handle(args: Any) -> None:
    sub = getattr(args, "crystal_command", None)
    if sub == "history":
        job = getattr(args, "job", None)
        _print_job(job) if job else _print_summaries()
    else:
        print("Usage: hermes crystal history [job]")


def _setup(subparser: Any) -> None:
    subs = subparser.add_subparsers(dest="crystal_command")
    h = subs.add_parser("history", help="Show recorded cron runs and what they cost")
    h.add_argument("job", nargs="?", help="Job name or id to show run by run")
    subparser.set_defaults(func=_handle)


def register(ctx: Any) -> None:
    ctx.register_cli_command(
        name="crystal",
        help="Crystallize: inspect recorded runs and saved routines",
        setup_fn=_setup,
        handler_fn=_handle,
    )
