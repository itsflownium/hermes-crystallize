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
        api = "" if run.api_cost_usd is None else f" ({'included; ' if run.included else 'estimated: '}{_money(run.api_cost_usd)} at API prices)"
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

    from . import analyze, lifecycle

    a = analyze.analyze_job(job)
    projection = lifecycle.analysis_projection(a)
    if as_json:
        data = {**a.to_dict(), "projection": projection,
                "est_api_calls_after": projection["api_calls_after"],
                "est_cost_after_usd": projection["cost_after_usd"],
                "agent_free": a.agent_free and projection["status"] != "unavailable"}
        print(json.dumps(data, indent=2, default=str))
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
    if projection["status"] == "unavailable":
        print(projection["reason"])
        print("After matching observations, use `hermes crystal promote <job> --mode tool` for normal host-tool guidance.")
    else:
        print(f"Hypothetical: {_num(projection['api_calls_after'])} AI calls, "
              f"{_money(projection['cost_after_usd'])} per run. {projection['reason']}")
        if a.agent_free:
            print("The read recipe is fixed; no-AI eligibility still requires the job's output and action checks.")


def _routine_for(job: str):
    from . import lifecycle, store

    found = lifecycle.cron_job(job)
    if found:
        return store.routine_for_job(found["id"])
    for r in store.list_routines():
        if r["source"] == "cron" and job in (r["source_ref"], r["id"]):
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
        mode = {"no_ai": "no AI", "prerun": "pre-run script"}.get(meta.get("mode"), "ordinary host-tool plan")
        print(f"  Live runs: {meta.get('live_runs', 0)}, skipped (no change): {len(meta.get('skips') or [])}, "
              f"failures in a row: {meta.get('failures', 0)}  [{mode}]")
        from . import prerun

        if meta.get("mode", "tool") != "tool":
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
            tests = meta.get("tests") or []
            reason = meta.get("why") or (tests[-1]["why"] if tests else "Waiting for completed normal host runs.")
            label = "Observation" if meta.get("executed") is False else "Test run"
            print(f"{label}: {reason}. Matching in a row: {meta.get('matches', 0)} of {meta.get('needed', lifecycle.MATCHES_NEEDED)}.")
        elif sub == "promote":
            if not r:
                print(f"No routine for {job!r}. Run `hermes crystal try {job}` first.")
                return
            no_ai = bool(getattr(args, "no_ai", False))
            mode = "no_ai" if no_ai else getattr(args, "mode", "tool")
            lifecycle.promote(r["id"], mode=mode)
            if no_ai:
                print(f"{r['name']} is live with no AI: the routine's output is the job's result.")
            elif mode == "tool":
                print(f"{r['name']} now provides recipe guidance. The main agent runs and verifies its normal host tools; nothing is automatically replayed.")
            else:
                print(f"{r['name']} is live. A pre-run script now collects the data before the AI starts; "
                      "see what it does when nothing changed with `hermes crystal show`.")
        elif sub == "mode":
            if not r:
                print(f"No routine for {job!r}.")
                return
            mode = {"ai": "prerun", "no-ai": "no_ai", "tool": "tool"}[args.mode]
            lifecycle.set_mode(r["id"], mode)
            message = {"no_ai": "with no AI.", "tool": "with recipe guidance and ordinary host tools; no automatic replay.",
                       "prerun": "with the AI writing the answer from the pre-run data."}[mode]
            print(f"{r['name']} now runs {message}")
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
        raise SystemExit(str(exc)) from exc


def _prerun(routine_id: str) -> None:
    import sys

    from . import lifecycle, prerun, store, tools

    try:
        text, code = prerun.run(routine_id, tools.dispatch)
    except Exception as exc:  # noqa: BLE001 — the AI must still get to do the job
        code = 1
        try:
            row = store.get_routine(routine_id)
            code = int(bool(row and (row.get("meta") or {}).get("mode") == "no_ai"))
            lifecycle.live_result(routine_id, False, f"{type(exc).__name__}: {exc}")
        except Exception:
            pass
        text = f"Crystallize: the routine couldn't run ({type(exc).__name__}: {exc}). Do the whole task yourself."
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
    for label, text in (("Before", answers.get("before")), ("With input savings", answers.get("after"))):
        if text:
            print(f"\n--- {label} ---\n{text[:1500]}")


def _tune(args: Any) -> None:
    from . import lifecycle, tuning

    job = getattr(args, "job", None)
    if getattr(args, "reasoning", None):
        from . import store
        if args.reasoning == "lower":
            raise SystemExit("Crystallize preserves reasoning. Lowering thinking is not an efficiency saving.")
        store.set_setting("lower_reasoning", False)
        print("Jobs keep your reasoning setting.")
        return
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
            print(f"{job} is back to its previous settings.")
        elif getattr(args, "status", False):
            tuning.refresh()
            _print_tune_job(job)
        else:
            t = tuning.start(job, args.level)
            later = " If its checks fail, previous input settings are restored; reasoning stays unchanged." if t["stages"] else ""
            print(f"Trying {tuning._settings(t)} on {t['job_name']}. After {tuning.TRIAL_RUNS} answers it's compared "
                  f"with earlier runs and kept only if they hold up and it costs less.{later}")
    except lifecycle.LifecycleError as exc:
        print(str(exc))


def _handle(args: Any) -> None:
    sub = getattr(args, "crystal_command", None)
    if sub == "native-cron":
        _native_action(args)
        return
    if sub in ("reread", "blind", "rhythm", "auction", "auction-reconcile", "brake", "rent", "prices", "delegation", "tool-defer", "compatibility", "model-route", "rate-card"):
        _completion_action(sub, args)
        return
    if sub in ("contract", "compare", "receipt", "recipe", "cache", "chat-policy"):
        _improvement_action(sub, args)
        return
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
        print("Usage: hermes crystal history [job] | analyze <job> [--json] | try|test|promote|undo|show <job> | mode <job> ai|no-ai|tool | skip <job> auto|repeat|quiet|off | tune [job] [--undo]")


def _read_json(path: str) -> Any:
    import json
    from pathlib import Path
    source = Path(path)
    if source.stat().st_size > 500_000:
        raise ValueError("The JSON file exceeds 500 KB.")
    return json.loads(source.read_text(encoding="utf-8"))


def _native_action(args: Any) -> None:
    import json
    from . import native_cron, starter_script
    try:
        if args.draft:
            result = starter_script.draft(args.job)
        elif args.undo:
            result = native_cron.undo(args.job, expected_config_fingerprint=args.fingerprint, confirmed=args.confirm)
        elif args.source_script:
            values = {"mode": args.mode, "snapshot_only": args.snapshot_only, "verbatim_output": args.verbatim_output,
                      "skipped_delivery_ok": args.skipped_delivery_ok}
            result = native_cron.apply(args.job, args.source_script, expected_preview_sha256=args.fingerprint,
                                       confirmed=args.confirm, **values) if args.apply else native_cron.preview(args.job, args.source_script, **values)
        elif args.apply:
            raise native_cron.NativeCronError("Choose --source-script and preview the complete source before --apply.")
        else:
            result = native_cron.status(args.job)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, native_cron.NativeCronError) as exc:
        raise SystemExit(str(exc)) from exc


def _improvement_action(sub: str, args: Any) -> None:
    import json
    from . import chat_policy, contracts, experiments, fetch_cache, lifecycle, receipts, recipes, store

    try:
        if sub == "chat-policy":
            if args.mode is not None or args.focus is not None:
                mode = args.mode or chat_policy.policy_for(args.session_id, args.tool)["mode"]
                result = chat_policy.set_policy(args.session_id, args.tool, mode, args.focus)
            else:
                result = chat_policy.summary(args.session_id)
        elif sub == "cache" and args.clear:
            result = {"cleared_entries": fetch_cache.clear()}
        else:
            job = lifecycle.cron_job(args.job)
            if not job:
                raise ValueError("No such cron job.")
            job_id = job["id"]
            if sub == "contract":
                result = contracts.set(job_id, None if args.clear else _read_json(args.file)) if args.clear or args.file else contracts.get(job_id)
                lifecycle.refresh_drift()
            elif sub == "recipe":
                result = recipes.install(job_id, _read_json(args.file))
            elif sub == "receipt":
                result = receipts.receipt(job_id)
            elif sub == "compare":
                # The command itself is an explicit user action. A preview remains free.
                result = experiments.preview(job_id) if args.preview else experiments.compare(
                    job_id, execution_id=args.execution, sample_count=args.samples,
                    candidate=_read_json(args.candidate) if args.candidate else None, confirmed=True)
            elif sub == "cache":
                result = (fetch_cache.configure(job_id, args.seconds, args.namespace)
                          if args.seconds is not None else fetch_cache.validate(store.get_setting('fetch_cache:' + job_id)))
                lifecycle.refresh_drift()
        print(json.dumps(result, indent=2, default=str))
    except (ValueError, OSError, lifecycle.LifecycleError, experiments.ComparisonError) as exc:
        raise SystemExit(str(exc)) from exc


def _setup(subparser: Any) -> None:
    import argparse
    subs = subparser.add_subparsers(dest="crystal_command")
    h = subs.add_parser("history", help="Show recorded cron runs and what they cost")
    h.add_argument("job", nargs="?", help="Job name or id to show run by run")
    a = subs.add_parser("analyze", help="Find the steps a job repeats every run")
    a.add_argument("job", help="Job name or id")
    a.add_argument("--json", action="store_true", help="Print the full analysis as JSON")
    nc = subs.add_parser("native-cron", help="Review and explicitly attach a complete Python snapshot source through native cron")
    nc.add_argument("job", help="Exact stable job ID")
    nc.add_argument("--source-script", help="Python file relative to this profile's scripts directory; preview never executes it")
    nc.add_argument("--mode", choices=("monitor", "verbatim"), default="monitor")
    nc.add_argument("--snapshot-only", action="store_true", help="Declare the source complete and read-only, with no per-run action or deadline")
    nc.add_argument("--verbatim-output", action="store_true", help="Declare exact script stdout the complete requested final answer")
    nc.add_argument("--skipped-delivery-ok", action="store_true", help="Declare that nothing should be sent when the data hasn't changed")
    nc.add_argument("--draft", action="store_true", help="Draft a starter script from this job's repeated reads; it isn't run or attached")
    action = nc.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--undo", action="store_true")
    nc.add_argument("--fingerprint", default="", help="Preview SHA for Apply; current config fingerprint for Undo")
    nc.add_argument("--confirm", action="store_true", help="Approve the reviewed source/native-field change")
    for name, text in (("try", "Save a job's steps as a routine and start testing it"),
                       ("test", "Inspect fresh normal host observations; replay needs a verified guarded SDK"),
                       ("promote", "Switch the job over to its routine (needs 3 matching test runs)"),
                       ("undo", "Put the job's original prompt back"),
                       ("show", "Show a job's routine and its test results")):
        p = subs.add_parser(name, help=text)
        p.add_argument("job", help="Job name or id")
        if name == "promote":
            p.add_argument("--mode", choices=("prerun", "tool"), default="tool",
                           help="Defaults to normal host-tool guidance; prerun requires a verified guarded public SDK")
            p.add_argument("--no-ai", action="store_true",
                           help="Use the routine's output as the job's result, with no AI at all")
    sk = subs.add_parser("skip", help="What a live job does when its data hasn't changed (only on hosts with guarded replay)")
    sk.add_argument("job", help="Job name or id")
    sk.add_argument("state", choices=("auto", "repeat", "quiet", "off"),
                    help="repeat the last answer, stay quiet, off = run the AI, auto = choose for me")
    md = subs.add_parser("mode", help="Choose how a job runs (automatic modes need a host with guarded replay)")
    md.add_argument("job", help="Job name or id")
    md.add_argument("mode", choices=("ai", "no-ai", "tool"))
    tn = subs.add_parser("tune", help="Reduce repeated input and unused read schemas while preserving reasoning")
    tn.add_argument("job", nargs="?", help="Job name or id. Leave out to list every job")
    tn.add_argument("--level", help="Legacy option; rejected because reasoning settings are preserved")
    tn.add_argument("--reasoning", choices=("keep", "lower"), help="Use keep; the legacy lower option is refused")
    tn.add_argument("--all", action="store_true", help="Start on every job that can have it")
    tn.add_argument("--undo", action="store_true", help="Put the job's previous setting back")
    tn.add_argument("--status", action="store_true", help="Show the result, with an answer from before and after")
    pr = subs.add_parser("prerun", help="(used by a promoted job's pre-run script) run a routine and print its data")
    pr.add_argument("routine_id")
    ct = subs.add_parser("contract", help="Show or set a job's explicit outcome checks")
    ct.add_argument("job")
    files = ct.add_mutually_exclusive_group()
    files.add_argument("--file", help="JSON outcome contract")
    files.add_argument("--clear", action="store_true")
    cp = subs.add_parser("compare", help="Compare two answer configurations on identical recorded reads (uses the selected models)")
    cp.add_argument("job")
    cp.add_argument("--preview", action="store_true", help="Inspect available input and model-call count without calling a model")
    cp.add_argument("--execution", help="Use a particular recorded execution")
    cp.add_argument("--samples", type=int, default=1, help="Recorded inputs to compare; each makes two bounded model calls")
    cp.add_argument("--candidate", help="Optional JSON candidate prompt/model configuration")
    rc = subs.add_parser("receipt", help="Export a job's proof, overhead and payback receipt without making model calls")
    rc.add_argument("job")
    rp = subs.add_parser("recipe", help="Import an explicit parameterized read recipe and begin testing")
    rp.add_argument("job")
    rp.add_argument("file", help="Version 2 JSON recipe")
    ca = subs.add_parser("cache", help="Configure opt-in read caching or clear this profile's fetch cache")
    ca.add_argument("job", nargs="?")
    ca.add_argument("--seconds", type=int, help="TTL from 0 (off) to 3600")
    ca.add_argument("--namespace", default="", help="Explicit shared namespace; default isolates by job ID")
    ca.add_argument("--clear", action="store_true")
    ch = subs.add_parser("chat-policy", help="Show or set result handling and focus for a chat")
    ch.add_argument("session_id")
    ch.add_argument("--tool", default="*", help="Tool name, or * for this chat's default")
    ch.add_argument("--mode", choices=("exact", "clean", "summary"))
    ch.add_argument("--focus", help="User-provided facts this chat's summaries must preserve")
    rr = subs.add_parser("reread", help="Opt into cache-aware text replay savings; reasoning is untouched")
    rr.add_argument("session_id")
    for option in ("enabled", "writes", "results", "batching"):
        rr.add_argument("--" + option, action=argparse.BooleanOptionalAction, default=None)
    bl = subs.add_parser("blind", help="Inspect anonymous answers, vote, then reveal routes and cost")
    bl.add_argument("comparison_id")
    bl.add_argument("--choice", choices=("A", "B", "tie", "neither"))
    bl.add_argument("--reveal", action="store_true", help="Reveal without voting; ends eligibility for a blind vote")
    rh = subs.add_parser("rhythm", help="Preview observed change rhythm and explicitly change a noncritical schedule")
    rh.add_argument("job")
    rh.add_argument("--minutes", type=int)
    rh.add_argument("--timezone", default="UTC")
    rh.add_argument("--noncritical", action="store_true")
    rh.add_argument("--allowed-staleness-minutes", type=int)
    rh.add_argument("--apply", action="store_true")
    rh.add_argument("--undo", action="store_true")
    rh.add_argument("--fingerprint", default="")
    rh.add_argument("--confirm", action="store_true")
    ar = subs.add_parser("auction-reconcile", help="Clear an interrupted auction after checking what it cost")
    ar.add_argument("--confirm", action="store_true")
    au = subs.add_parser("auction", help="Explicitly compare models on frozen real evidence, with a request budget")
    au.add_argument("job")
    au.add_argument("--models", required=True, help="JSON array of explicit provider/model objects")
    au.add_argument("--cap-usd", type=float, required=True)
    au.add_argument("--max-tokens", type=int, default=1024)
    au.add_argument("--execution")
    au.add_argument("--run", action="store_true")
    au.add_argument("--confirm", action="store_true")
    au.add_argument("--accept-estimated-cap", action="store_true", help="Use an estimated request ledger; not a provider billing guarantee")
    de = subs.add_parser("delegation", help="Explicit profile-wide helper consent; never selects the active backend")
    flag = de.add_mutually_exclusive_group()
    flag.add_argument("--enable", action="store_true")
    flag.add_argument("--disable", action="store_true")
    de.add_argument("--provider", default="")
    de.add_argument("--model", default="")
    de.add_argument("--confirm", action="store_true")
    td = subs.add_parser("tool-defer", help="Preview or explicitly add profile-wide deferred tools; clarify stays direct")
    td.add_argument("--session-id", default="")
    td.add_argument("--tools", nargs="+")
    td.add_argument("--fingerprint", default="")
    td.add_argument("--confirm", action="store_true")
    subs.add_parser("compatibility", help="Report which Hermes features this install supports")
    bk = subs.add_parser("brake", help="Show or change Runaway Brake, which stops a cron run far past its normal use")
    bk.add_argument("state", nargs="?", choices=("on", "off"))
    rn = subs.add_parser("rent", help="See what a chat keeps paying to re-read, and remove or restore a result")
    rn.add_argument("session_id")
    rent_action = rn.add_mutually_exclusive_group()
    rent_action.add_argument("--remove", metavar="ID", help="Stop sending this tool result to the AI")
    rent_action.add_argument("--restore", metavar="ID", help="Send this tool result again")
    pc = subs.add_parser("prices", help="Check a route's price, or turn public price lookup on or off")
    pc.add_argument("--provider")
    pc.add_argument("--model")
    pc.add_argument("--lookup", choices=("on", "off"), help="Whether to look up list prices from public model lists")
    pc.add_argument("--allow-unpriced-helper", choices=("on", "off"), help="Whether a helper with no known price may spend")
    mr = subs.add_parser("model-route", help="Declare an exact model route; this does not sign in or select a helper")
    mr.add_argument("--provider")
    mr.add_argument("--model")
    pc = subs.add_parser("rate-card", help="Show or explicitly set an exact-route user price estimate")
    change = pc.add_mutually_exclusive_group()
    change.add_argument("--file", help="JSON rate card using documented units; never an invoice")
    change.add_argument("--delete", action="store_true")
    pc.add_argument("--provider")
    pc.add_argument("--model")
    pc.add_argument("--base-url")
    pc.add_argument("--confirm", action="store_true", help="Acknowledge user-supplied rates and their estimation limits")
    subparser.set_defaults(func=_handle)


def _completion_action(sub: str, args: Any) -> None:
    import json
    from . import auction, blind, configured_models, delegation, host_api, lifecycle, public_pricing, reread, rhythm, tool_discovery
    try:
        if sub == "model-route":
            if bool(args.provider) != bool(args.model):
                raise ValueError("Supply both provider and model, or neither to inspect available declarations.")
            result = configured_models.configure_route(args.provider, args.model) if args.provider else configured_models.inventory()
        elif sub == "rate-card":
            if args.file or args.delete:
                if not args.confirm:
                    raise ValueError("Use --confirm to acknowledge that explicit rates are user estimates, not proof of billing.")
                if args.delete:
                    if not args.provider or not args.model:
                        raise ValueError("Deleting a rate card requires an exact provider and model.")
                    result = public_pricing.delete_rate_card(args.provider, args.model, args.base_url)
                else:
                    result = public_pricing.save_rate_card(_read_json(args.file))
            else:
                result = public_pricing.rate_cards_report()
        elif sub == "reread":
            values = {k: getattr(args, k) for k in ("enabled", "writes", "results", "batching") if getattr(args, k) is not None}
            result = reread.configure(args.session_id, **values) if values else reread.policy(args.session_id)
        elif sub == "blind":
            if args.choice and args.reveal:
                raise ValueError("Choose a blind answer or reveal without voting, not both.")
            result = blind.choose(args.comparison_id, args.choice) if args.choice else blind.reveal(args.comparison_id) if args.reveal else blind.present(args.comparison_id)
        elif sub == "delegation":
            result = delegation.configure(args.enable, args.provider, args.model, confirmed=args.confirm) if args.enable or args.disable else delegation.summary()
        elif sub == "tool-defer":
            result = tool_discovery.configure(args.tools, confirmed=args.confirm, expected_config_fingerprint=args.fingerprint) if args.tools else tool_discovery.preview(args.session_id)
        elif sub == "compatibility":
            result = host_api.report()
        elif sub == "auction-reconcile":
            result = auction.reconcile(confirmed=args.confirm)
        elif sub == "brake":
            from . import brake
            result = brake.set_enabled(args.state == "on") if args.state else brake.summary(lifecycle.all_cron_jobs())
        elif sub == "rent":
            from . import rent
            result = (rent.evict(args.session_id, args.remove) if args.remove else
                      rent.restore(args.session_id, args.restore) if args.restore else rent.report(args.session_id))
        elif sub == "prices":
            from . import chat_savings, modelinfo, price_discovery, store
            if args.lookup:
                store.set_setting(price_discovery.SETTING, args.lookup == "on")
            if args.allow_unpriced_helper:
                store.set_setting(chat_savings.UNPRICED_SETTING, args.allow_unpriced_helper == "on")
            result = {"lookup": price_discovery.enabled(), "allow_unpriced_helper": chat_savings._unpriced_allowed()}
            if bool(args.provider) != bool(args.model):
                raise ValueError("Give both --provider and --model.")
            if args.provider:
                price_discovery.refresh(args.provider) if price_discovery.enabled() else None
                result["card"] = modelinfo.exact_token_prices(args.model, args.provider)
        else:
            job = lifecycle.cron_job(args.job)
            if not job:
                raise ValueError("No such job.")
            job_id = job["id"]
            if sub == "rhythm":
                if args.apply and args.undo:
                    raise ValueError("Choose Apply or Undo.")
                if args.undo:
                    result = rhythm.undo(job_id, expected_config_fingerprint=args.fingerprint, confirmed=args.confirm)
                elif args.apply:
                    result = rhythm.apply(job_id, args.minutes, expected_config_fingerprint=args.fingerprint,
                        noncritical=args.noncritical, allowed_staleness_minutes=args.allowed_staleness_minutes, confirmed=args.confirm)
                elif args.minutes is not None:
                    result = rhythm.preview(job_id, args.minutes, timezone=args.timezone,
                        noncritical=args.noncritical, allowed_staleness_minutes=args.allowed_staleness_minutes)
                else:
                    result = rhythm.report(job_id, timezone=args.timezone)
            elif sub == "auction":
                values = {"models": _read_json(args.models), "max_tokens": args.max_tokens,
                          "cost_cap_usd": args.cap_usd, "execution_id": args.execution,
                          "accept_estimated_cap": args.accept_estimated_cap}
                result = auction.run(job_id, confirmed=args.confirm, **values) if args.run else auction.preview(job_id, **values)
        print(json.dumps(result, indent=2, default=str))
    except (ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc


def register(ctx: Any) -> None:
    ctx.register_cli_command(
        name="crystal",
        help="Crystallize: inspect recorded runs and saved routines",
        setup_fn=_setup,
        handler_fn=_handle,
    )
