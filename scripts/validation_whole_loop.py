#!/usr/bin/env python3
"""Paired live test of Crystallize Chat through the real Hermes agent loop.

Each pair runs one task twice in fresh sessions: Crystallize Chat off, and on. Model, provider, reasoning
and task are the same on both sides, and a pair whose recorded settings differ is reported as invalid,
never dropped. Order is randomised per pair. Every attempt is kept, including failures.

Run it with Python that can import Hermes and the installed `crystallize` plugin, with the gateway
already running the code under test. Paid calls happen only with --run.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sqlite3
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _api_events(n=40):
    return [{"id": 1000 + i, "type": "deployment", "service": f"svc-{i % 5}", "status": "failed" if i == 17 else "ok",
             "region": "eu-west", "duration_ms": 200 + i * 7, "actor": {"login": f"user{i % 3}", "team": "platform"},
             "labels": ["prod", "weekly"], "url": f"https://status.example.com/d/{1000 + i}"} for i in range(n)]


def _fixtures():
    return {
        "research": {"file": "sources.json", "text": json.dumps({"results": [
            {"title": f"Source {i}", "url": f"https://example.org/r/{i}", "published": f"2026-0{i % 9 + 1}-14",
             "summary": "Harbor capacity was revised." if i == 7 else "Background on regional shipping and storage."}
            for i in range(30)] + [{"title": "Port authority update", "url": "https://example.org/port", "capacity_teu": 41250}]}, indent=2),
            "ask": "Read {path} with the terminal tool (use cat). What is the capacity_teu in the port authority update, and its URL?",
            "follow": "Which source number mentions the revised capacity?", "follow_expect": ["7"], "expect": ["41250", "example.org/port"]},
        "writing": {"file": "brief.json", "text": json.dumps({"product": "Lumen desk lamp", "price_usd": 89, "launch": "March 3",
            "facts": [f"Fact {i}: warm light mode {i} tested with {i * 12} readers" for i in range(25)]}, indent=2),
            "ask": "Read {path} with the terminal tool (use cat). Write a two-sentence launch note with the product name, price and launch date.",
            "follow": "Shorten that note to one sentence.", "expect": ["Lumen", "89"]},
        "data": {"file": "sales.csv", "text": "region,units,price\n" + "\n".join(f"r{i},{i * 3 + 1},{10 + i % 4}" for i in range(120)),
            "ask": "Read {path} with the terminal tool (use cat). How many units does region r100 have?",
            "follow": "What is its price?", "follow_expect": ["10"], "expect": ["301"]},
        "analysis": {"file": "events.json", "text": json.dumps(_api_events(), indent=2),
            "ask": "Read {path} with the terminal tool (use cat). Which event id failed, and which service was it?",
            "follow": "What was that event's duration_ms?", "follow_expect": ["319"], "expect": ["1017", "svc-2"]},
        "coding": {"file": "module.py", "text": "\n".join(
            f"def handler_{i}(value):\n    \"\"\"Handle case {i}.\"\"\"\n    return value + {i}\n" for i in range(60)) + "\ndef clamp(v, low, high):\n    return max(low, min(v, high))\n",
            "ask": "Read {path} with the terminal tool (use cat). What does handler_42 add to its value? Quote the line.",
            "follow": "Write a one-line docstring for clamp.", "expect": ["42"]},
        "chat": {"file": None, "text": None, "ask": "Give me three short tips for a first-time visitor to Lisbon.",
            "follow": "Which one of those matters most in summer? One sentence.", "expect": []},
        "logs": {"file": "service.log", "text": "\n".join(f"2026-11-07T0{i % 9}:12:{i % 60:02d} INFO worker-{i % 4} processed batch {i}" for i in range(250))
                 + "\n2026-11-07T09:41:07 ERROR worker-2 disk full on /var/data\n",
            "ask": "Read {path} with the terminal tool (use cat). What is the one ERROR, and at what time?",
            "follow": "Which worker was it?", "follow_expect": ["worker-2"], "expect": ["disk full", "09:41:07"]},
    }


LONG_FILES = ("research", "analysis", "logs", "coding")
LONG_LATER = ("Name one risk in what you've read so far, in one sentence.", "Suggest one next step, in one sentence.",
              "What would you check first tomorrow? One sentence.", "Give a one-line title for this work.",
              "Which of the files was the longest? One sentence.", "Name one thing that's still unknown. One sentence.")
LONG_FINAL = ("Now give me the exact capacity_teu from the port authority update, and the event id that failed.", ["41250", "1017"])


def run_long(args, on, folder, rates, tasks):
    """A working chat: read several large files over several messages, keep talking, then need an old detail."""
    from crystallize import rent, request_settings, store

    nonce = os.urandom(4).hex()
    session, _, code = ask(args, f"Reply with the single word READY. Reference {nonce}.")
    if not session:
        return {"on": on, "ok": False, "why": f"no session (exit {code})"}
    if on:
        store.set_chat_enabled(session, True)
    start = usage(session)
    codes = []
    for name in LONG_FILES:
        path = folder / f"{nonce}-{tasks[name]['file']}"
        path.write_text(tasks[name]["text"], encoding="utf-8")
        codes.append(ask(args, tasks[name]["ask"].format(path=path), session)[2])
    for later in LONG_LATER * args.long_rounds:
        codes.append(ask(args, later, session)[2])
    _, final, final_code = ask(args, LONG_FINAL[0], session)
    codes.append(final_code)
    end = usage(session)
    delta = {key: (end.get(key) or 0) - (start.get(key) or 0) for key in COUNTERS}
    delta["prompt_tokens"] = delta["input_tokens"] + delta["cache_read_tokens"] + delta["cache_write_tokens"]
    return {"on": on, "ok": not any(codes), "session": session, "delta": delta,
            "checks_passed": all(text in final for text in LONG_FINAL[1]),
            "list_cost_usd": list_cost(delta, rates) if rates else None,
            "list_cost_without_reasoning_usd": list_cost({**delta, "output_tokens": max(0, delta["output_tokens"] - delta["reasoning_tokens"])}, rates) if rates else None,
            "host_estimated_delta_usd": (end.get("estimated_cost_usd") or 0) - (start.get("estimated_cost_usd") or 0),
            "recorded": {"model": end.get("model"), "provider": end.get("billing_provider"), "sent": (request_settings.for_run("chat:" + session) or {}).get("fingerprint")},
            "requested_on_the_wire": request_settings.for_run("chat:" + session),
            "auto_rent": rent.auto_state(session) if on else None, "answer_chars": len(final)}


HANDOFF_TEXT = " ".join(f"Item {i}: the shipment of {i * 7} crates reached port {i % 9} on day {i % 28 + 1}, and customs held {i % 3} for inspection." for i in range(160))
HANDOFF_TASK = ("Translate every line of this inventory log into French, keeping every number exactly, and give me the whole translation. "
                "It's routine work, so hand it off if you have a way to.\n\n" + HANDOFF_TEXT)


def run_handoff(args, on, folder, rates, tasks):
    """A long, routine, checkable job: the main model may hand it to the cheaper helper."""
    from crystallize import request_settings, store

    nonce = os.urandom(4).hex()
    session, _, code = ask(args, f"Reply with the single word READY. Reference {nonce}.")
    if not session:
        return {"on": on, "ok": False, "why": f"no session (exit {code})"}
    if on:
        store.set_chat_enabled(session, True)
    start = usage(session)
    _, answer, code1 = ask(args, HANDOFF_TASK, session)
    end = usage(session)
    delta = {key: (end.get(key) or 0) - (start.get(key) or 0) for key in COUNTERS}
    delta["prompt_tokens"] = delta["input_tokens"] + delta["cache_read_tokens"] + delta["cache_write_tokens"]
    events = store.chat_events(session) if on else []
    helper = [e for e in events if e.get("helper_call")]
    helper_cost = sum(e["cheap_cost"] or 0 for e in helper)
    main_cost = list_cost(delta, rates) if rates else None
    return {"on": on, "ok": code1 == 0, "session": session, "delta": delta,
            "checks_passed": all(str(n) in answer for n in (7 * 37, 7 * 120, 7 * 159)),
            "list_cost_usd": None if main_cost is None else main_cost + helper_cost,
            "list_cost_without_reasoning_usd": None if main_cost is None else list_cost({**delta, "output_tokens": max(0, delta["output_tokens"] - delta["reasoning_tokens"])}, rates) + helper_cost,
            "helper_calls": len(helper), "helper_cost_usd": helper_cost, "helper_unknown_cost_calls": sum(e["cheap_cost"] is None for e in helper),
            "recorded": {"model": end.get("model"), "provider": end.get("billing_provider"), "sent": (request_settings.for_run("chat:" + session) or {}).get("fingerprint")},
            "requested_on_the_wire": request_settings.for_run("chat:" + session), "answer_chars": len(answer)}


def ask(args, prompt, session=None):
    cmd = ["hermes", "chat", "-Q", "--oneshot", "-m", args.model, "--provider", args.provider, "--reasoning", args.reasoning,
           "-t", "terminal,crystallize", "-q", prompt] + (["--resume", session] if session else [])
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=args.turn_seconds)
    found = re.search(r"session_id: (\S+)", done.stdout + done.stderr)
    return (found.group(1) if found else session), done.stdout.split("session_id:")[0].strip(), done.returncode


def usage(session):
    conn = sqlite3.connect(f"file:{HOME}/state.db?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, api_call_count, "
                       "estimated_cost_usd, actual_cost_usd, model, billing_provider, model_config FROM sessions WHERE id = ?", (session,)).fetchone()
    conn.close()
    return dict(row) if row else {}


LATER_TURNS = ("Name one thing that would make that clearer.", "Give one more short example of the same idea.",
               "Summarise this whole chat in one sentence.")
COUNTERS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "api_call_count")


def list_cost(delta, rates):
    return (delta["input_tokens"] * rates["input"] + delta["output_tokens"] * rates["output"]
            + delta["cache_read_tokens"] * rates["cache_read"] + delta["cache_write_tokens"] * rates["cache_write"])


def run_arm(args, task, on, folder, rates):
    from crystallize import rent, request_settings, reread, store

    nonce = os.urandom(4).hex()
    path = None
    if task["file"]:
        path = folder / f"{nonce}-{task['file']}"
        path.write_text(task["text"], encoding="utf-8")
    session, _, code = ask(args, f"Reply with the single word READY. Reference {nonce}.")
    if not session:
        return {"on": on, "ok": False, "why": f"no session (exit {code})"}
    if on and args.mode == "clean":
        store.set_chat_enabled(session, True)
        if args.reread:
            reread.configure(session, enabled=True, results=True)
    start = usage(session)
    _, answer, code1 = ask(args, task["ask"].format(path=path), session)
    removed = None
    if args.mode == "rent":
        # Only what happens after the removal is measured, on both sides.
        start = usage(session)
        items = rent.report(session)["items"]
        if on and items:
            removed = items[0]["tokens"]
            rent.evict(session, items[0]["id"])
    _, follow, code2 = ask(args, task["follow"], session)
    for later in LATER_TURNS if args.mode == "rent" else ():
        _, _, later_code = ask(args, later, session)
        code2 = code2 or later_code
    end = usage(session)
    delta = {key: (end.get(key) or 0) - (start.get(key) or 0) for key in COUNTERS}
    delta["prompt_tokens"] = delta["input_tokens"] + delta["cache_read_tokens"] + delta["cache_write_tokens"]
    events = store.list_chat_savings(session) if on else []
    return {"on": on, "ok": code1 == 0 and code2 == 0, "session": session, "delta": delta,
            "checks_passed": (all(text.lower() in answer.lower() for text in task["expect"])
                               and all(text.lower() in follow.lower() for text in task.get("follow_expect", []))),
            "list_cost_usd": list_cost(delta, rates) if rates else None,
            "list_cost_without_reasoning_usd": list_cost({**delta, "output_tokens": max(0, delta["output_tokens"] - delta["reasoning_tokens"])}, rates) if rates else None,
            "host_estimated_delta_usd": (end.get("estimated_cost_usd") or 0) - (start.get("estimated_cost_usd") or 0),
            "recorded": {"model": end.get("model"), "provider": end.get("billing_provider"), "sent": (request_settings.for_run("chat:" + session) or {}).get("fingerprint")},
            "requested_on_the_wire": request_settings.for_run("chat:" + session),
            "tool_chars_removed": (events[0].get("chars") if events else 0) or 0, "rent_tokens_removed": removed, "answer_chars": len(answer), "follow_chars": len(follow)}


def _settings(recorded):
    """What must match. The session's config also holds usage counters that differ every run."""
    recorded = recorded or {}
    try:
        config = json.loads(recorded.get("model_config") or "{}")
    except ValueError:
        config = {}
    return recorded.get("model"), recorded.get("provider"), recorded.get("sent") or json.dumps(config.get("reasoning_config"), sort_keys=True)


def summarise(pairs):
    out = {}
    for domain in sorted({p["domain"] for p in pairs}):
        valid = [p for p in pairs if p["domain"] == domain and p["valid"]]
        ratios = [p["on"]["list_cost_usd"] / p["off"]["list_cost_usd"] for p in valid if p["off"].get("list_cost_usd")]
        without = [p["on"]["list_cost_without_reasoning_usd"] / p["off"]["list_cost_without_reasoning_usd"]
                   for p in valid if p["off"].get("list_cost_without_reasoning_usd")]
        prompts = [p["on"]["delta"]["prompt_tokens"] / p["off"]["delta"]["prompt_tokens"] for p in valid if p["off"]["delta"]["prompt_tokens"]]
        out[domain] = {"valid_pairs": len(valid), "invalid_pairs": len([p for p in pairs if p["domain"] == domain and not p["valid"]]),
                       "median_cost_ratio_on_vs_off": statistics.median(ratios) if ratios else None,
                       "median_cost_ratio_excluding_reasoning_output": statistics.median(without) if without else None,
                       "median_prompt_token_ratio": statistics.median(prompts) if prompts else None,
                       "checks_passed_off": sum(p["off"]["checks_passed"] for p in valid),
                       "checks_passed_on": sum(p["on"]["checks_passed"] for p in valid)}
    return out



def temporary_rate_cards(routes, catalog_provider):
    """Save rate cards for these exact routes from a public catalog's list prices, and return a
    function that removes them. For routes such as a subscription that publish no price list."""
    from crystallize import price_discovery, public_pricing

    catalog = price_discovery.parse_catalog(price_discovery._fetch_json(price_discovery.CATALOG_URL), catalog_provider)
    saved = []
    existing = {(card["provider"], card["model"]) for card in public_pricing.list_rate_cards()}
    for provider, model in routes:
        if (provider, model) in existing:
            continue  # someone else's card: use it, and leave it in place afterwards
        found = catalog.get(model)
        if not found:
            raise SystemExit(f"No list price for {model} under {catalog_provider}.")
        public_pricing.save_rate_card({"provider": provider, "model": model, "currency": "USD", "unit": "per_token",
                                       **{k: found[k] for k in ("input", "output", "cache_read", "cache_write")},
                                       "request_cost": 0, "tiers_known": True, "source_url": price_discovery.CATALOG_URL})
        saved.append((provider, model))

    def remove():
        for provider, model in saved:
            public_pricing.delete_rate_card(provider, model)
    return remove

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Make paid calls")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--mode", choices=("clean", "rent", "long", "handoff"), default="clean",
                        help="clean: Crystallize Chat on from the start. rent: remove the largest result, then measure later "
                             "turns. long: a working chat over many messages, with everything automatic")
    parser.add_argument("--long-rounds", type=int, default=1, help="How many times the later messages repeat in long mode")
    parser.add_argument("--reread", action="store_true", help="Also switch on Re-read savings in the Crystallize arm")
    parser.add_argument("--pairs", type=int, default=1, help="Pairs per task type")
    parser.add_argument("--task", action="append")
    parser.add_argument("--minutes", type=float, default=15)
    parser.add_argument("--turn-seconds", type=int, default=240)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--stop-recording", action="store_true", help="Turn off chat settings recording when done; leave it on while other tests run")
    parser.add_argument("--report", required=True)
    parser.add_argument("--prices-from", help="Price these routes with this catalog provider's list prices, for a subscription route with no price list")
    parser.add_argument("--helper", help="provider/model for the helper in hand-off mode, priced the same way")
    args = parser.parse_args()
    report_path = Path(args.report)
    if report_path.exists():
        raise SystemExit("Use a new report file; existing evidence is never overwritten.")
    tasks = _fixtures()
    chosen = ["working chat"] if args.mode == "long" else ["hand-off"] if args.mode == "handoff" else args.task or list(tasks)
    if not args.run:
        print(json.dumps({"planned_sessions": 2 * args.pairs * len(chosen), "tasks": chosen}))
        return
    from crystallize import chat_savings, modelinfo, price_discovery, request_settings, store

    store.set_setting(request_settings.RECORD_CHATS, True)
    remove_cards = lambda: None
    helper = args.helper.split("/", 1) if args.helper else None
    if args.prices_from:
        remove_cards = temporary_rate_cards([(args.provider, args.model)] + ([tuple(helper)] if helper else []), args.prices_from)
    if helper:
        chat_savings.set_model(*helper)
        handoffs_before = chat_savings.handoffs_allowed()
        store.set_setting(chat_savings.HANDOFF_SETTING, True)
        atexit_restore = lambda: store.set_setting(chat_savings.HANDOFF_SETTING, handoffs_before)
    else:
        atexit_restore = lambda: None
    import atexit
    atexit.register(remove_cards)  # also when interrupted, so no temporary card is left behind
    atexit.register(atexit_restore)
    price_discovery.refresh(args.provider)
    rates = modelinfo.token_prices(args.model, args.provider) or price_discovery.rates(args.model, args.provider)
    seed = args.seed if args.seed is not None else int(time.time())
    rng = random.Random(seed)
    deadline = time.time() + args.minutes * 60
    pairs, stopped = [], None
    with tempfile.TemporaryDirectory(prefix="crystal-evidence-") as folder:
        for repeat in range(args.pairs):
            for domain in chosen:
                if time.time() > deadline:
                    stopped = "deadline"
                    break
                order = [False, True]
                rng.shuffle(order)
                arms = {}
                for on in order:
                    try:
                        arms["on" if on else "off"] = (run_long(args, on, Path(folder), rates, tasks) if args.mode == "long" else
                                                       run_handoff(args, on, Path(folder), rates, tasks) if args.mode == "handoff"
                                                       else run_arm(args, tasks[domain], on, Path(folder), rates))
                    except subprocess.TimeoutExpired:
                        arms["on" if on else "off"] = {"on": on, "ok": False, "why": "timeout"}
                settings = [_settings(arms[k].get("recorded")) for k in ("off", "on")]
                valid = all(arms[k].get("ok") for k in ("off", "on")) and settings[0] == settings[1]
                pairs.append({"domain": domain, "repeat": repeat, "order": ["on" if o else "off" for o in order], "valid": valid, **arms})
                report_path.write_text(json.dumps({"partial": True, "pairs": pairs}, indent=1))
                print(domain, repeat, "valid" if valid else "invalid", flush=True)
    result = {"schema": 1, "mode": args.mode, "reread": args.reread, "model": args.model, "provider": args.provider, "requested_reasoning": args.reasoning, "seed": seed,
              "frozen_list_rates_per_token": rates, "stopped": stopped, "by_task": summarise(pairs), "pairs": pairs,
              "limits": ["Costs are list prices applied to recorded usage, not bills.", "Fixtures are synthetic; JSON is pretty-printed like API output.",
                         "One provider and model. It does not extend to other routes.", "The checks cover named facts, not overall answer quality."]}
    remove_cards()
    if helper:
        import subprocess as _subprocess
        from crystallize import lifecycle
        _subprocess.run([*lifecycle._hermes_cmd(), "config", "set", "--force", f"auxiliary.{chat_savings.TASK}",
                         json.dumps({"provider": "", "model": ""})], env=lifecycle.hermes_env(), capture_output=True)
    if args.stop_recording:
        store.set_setting(request_settings.RECORD_CHATS, False)
    report_path.write_text(json.dumps(result, indent=1))
    print(json.dumps(result["by_task"], indent=1))


if __name__ == "__main__":
    main()
