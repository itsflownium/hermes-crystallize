"""Text versions of the Crystallize panel for /crystallize in the CLI, the TUI and messaging chats.

Every view reads the same data as the Desktop panel, so the numbers match it.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

USAGE = """Crystallize commands (they work the same in the CLI, the TUI and Desktop):
  /crystallize                 what this chat and your cron jobs have saved
  /crystallize chat on|off     turn Crystallize on or off for this chat
  /crystallize chat default on|off  start every new chat with Crystallize on (or off)
  /crystallize rent            what this chat keeps paying to re-read
  /crystallize rent remove N   stop sending result N to the AI (it can still read it back)
  /crystallize rent restore N  send result N again
  /crystallize rent auto on|off  remove old results automatically when it pays
  /crystallize jobs            each cron job's cost and whether skipping would help
  /crystallize brake [on|off]  Runaway Brake for cron runs far past their normal use
  /crystallize prices [lookup on|off]  this chat's model price, and public price lookup
  /crystallize line on|off     the Crystallize line shown after each turn in the CLI and TUI
More controls: hermes crystal --help"""


def _money(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value == 0:
        return "$0"
    if value < 0.0001:
        return f"{sign}${value:.7f}".rstrip("0") if value >= 5e-8 else "under $0.0000001"
    if value < 0.01:
        return f"{sign}${value:.4f}"
    return f"{sign}${value:,.2f}"


def _tokens(value: Optional[float]) -> str:
    value = int(value or 0)
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def _short(text: Any, limit: int = 48) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _on(flag: Any) -> str:
    return "on" if flag else "off"


# ---------------------------------------------------------------- views

def _chat_lines(session_id: str) -> list[str]:
    from . import chat_savings, rent
    if not session_id:
        return ["This chat: send a message first, then Crystallize can work on it."]
    state = chat_savings.summary(session_id)
    lines = [f"This chat: Crystallize is {_on(state.get('enabled'))}."
             + ("" if state.get("enabled") else " Turn it on with /crystallize chat on.")]
    spent = state.get("chat_spent_usd")
    saved = state.get("this_chat") or {}
    if spent is not None:
        lines.append(f"  Spent so far: {_money(spent)} on {state.get('main_model') or 'the main model'}.")
    if saved.get("tokens_removed"):
        net = saved.get("net_once")
        lines.append(f"  Input removed: {_tokens(saved['tokens_removed'])} tokens"
                     + (f", worth about {_money(net)} net at today's price." if net is not None else "."))
    try:
        report = rent.report(session_id)
    except Exception:  # noqa: BLE001 — a chat with no readable history still shows its status
        report = {}
    if report.get("items"):
        removed = report.get("removed_tokens") or 0
        per_call = report.get("per_call_usd")
        cost = f" (about {_money(per_call)})" if per_call is not None else ""
        if removed:
            lines.append(f"  Removed {_tokens(removed)} tokens of old tool results from every AI call. "
                         f"Each call now re-reads {_tokens(report.get('tokens'))} tokens{cost}.")
        else:
            lines.append(f"  Each AI call re-reads {_tokens(report.get('tokens'))} tokens{cost}. See /crystallize rent.")
    return lines


def _job_rows(view: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    labels = (("live", "saving"), ("testing", "on trial"), ("watching", "learning"),
              ("spotted", "ready to try"), ("finished", "finished"))
    return [(label, job) for key, label in labels for job in view.get(key) or []]


def _cron_lines(view: dict[str, Any]) -> list[str]:
    rows = _job_rows(view)
    active = [row for row in rows if row[0] != "finished"]
    if not rows:
        return ["Cron jobs: none recorded yet. Crystallize learns from a job's normal runs."]
    lines = [f"Cron jobs: {len(active)} active, {len(rows) - len(active)} finished. "
             f"Saved so far: {_money(view.get('saved_usd') or 0)}."]
    weekly = view.get("live_saving_usd_per_week")
    if weekly:
        lines.append(f"  Saving about {_money(weekly)} a week. See /crystallize jobs.")
    return lines


def status(session_id: str) -> str:
    from . import brake, overview
    lines = _chat_lines(session_id)
    view = overview.build(session_id or None)
    lines += _cron_lines(view)
    lines.append(f"Runaway Brake: {_on(brake.enabled())}.")
    lines.append("Type /crystallize help for every command.")
    return "\n".join(lines)


def rent_view(session_id: str) -> str:
    from . import rent
    if not session_id:
        return "Context Rent needs a chat. Send a message first."
    report = rent.report(session_id)
    items = report.get("items") or []
    if not items:
        return "This chat has no tool results to re-read yet."
    per_call = report.get("per_call_usd")
    lines = [f"Every AI call in this chat re-sends {_tokens(report.get('tokens'))} tokens"
             + (f" (about {_money(per_call)} per call)." if per_call is not None else ".")]
    lines.append("Old tool results it keeps paying for:")
    for number, item in enumerate(items, 1):
        cost = item.get("per_call_usd")
        tag = " [removed]" if item.get("removed") else " [worth removing]" if item.get("worth_it") else ""
        lines.append(f"  {number}. {item.get('tool')}: {_short(item.get('what'))} · {_tokens(item.get('tokens'))} tokens"
                     + (f" · {_money(cost)} per call" if cost is not None else "") + tag)
    removed = report.get("removed_tokens") or 0
    if removed:
        saving = report.get("saving_per_call_usd")
        lines.append(f"Removed {_tokens(removed)} tokens"
                     + (f", saving about {_money(saving)} on every AI call." if saving is not None else "."))
    auto = report.get("auto") or {}
    lines.append(f"Automatic removal is {_on(report.get('auto_on'))}"
                 + (f": {auto.get('removed', 0)} removed, {auto.get('read_backs', 0)} read back." if report.get("auto_on") else "."))
    lines.append("Remove one with /crystallize rent remove N. The AI can still read it back if it needs it.")
    return "\n".join(lines)


def _rent_item(session_id: str, which: str) -> dict[str, Any]:
    from . import rent
    items = rent.report(session_id).get("items") or []
    if which.isdigit() and 1 <= int(which) <= len(items):
        return items[int(which) - 1]
    match = [item for item in items if str(item.get("id")) == which]
    if not match:
        raise ValueError(f"No result {which}. Use a number from /crystallize rent.")
    return match[0]


def rent_change(session_id: str, action: str, which: str) -> str:
    from . import rent
    if not session_id:
        return "Context Rent needs a chat. Send a message first."
    item = _rent_item(session_id, which)
    label = f"{item.get('tool')} ({_short(item.get('what'), 40)})"
    if action == "remove":
        rent.evict(session_id, str(item["id"]))
        return f"Removed {label}. It's no longer sent with each AI call; the AI can still read it back."
    rent.restore(session_id, str(item["id"]))
    return f"Restored {label}. It's sent with each AI call again."


def rent_auto(on: bool) -> str:
    from . import rent, store
    store.set_setting(rent.AUTO_SETTING, on)
    return ("Automatic removal is on: old results are removed only when that pays back." if on
            else "Automatic removal is off. You can still remove results yourself.")


def jobs_view(session_id: str) -> str:
    from . import overview
    view = overview.build(session_id or None)
    rows = _job_rows(view)
    if not rows:
        return "No cron jobs recorded yet. Crystallize learns from a job's normal runs."
    active = [row for row in rows if row[0] != "finished"]
    finished = [row for row in rows if row[0] == "finished"]
    lines = _cron_lines(view)
    shown = active[:10] if active else finished[:5]
    if not active:
        lines.append("  No active jobs. The most recent finished ones:")
    for label, job in shown:
        cost = job.get("cost_now")
        line = f"  • {job.get('name') or job.get('job_id')} ({label}): {job.get('runs', 0)} runs"
        if cost is not None:
            line += f", {_money(cost)} a run"
        if job.get("saved_usd"):
            line += f", saved {_money(job['saved_usd'])}"
        lines.append(line)
        fit = (job.get("skip_fit") or {}).get("message")
        if fit and label != "finished":
            lines.append(f"      {_short(fit, 110)}")
    hidden = len(rows) - len(shown)
    if hidden:
        lines.append(f"  {hidden} more" + (" (finished)" if active and hidden == len(finished) else "")
                     + ". Full detail: hermes crystal history")
    if active:
        lines.append("Skip unchanged runs: hermes crystal native-cron JOB --draft")
    return "\n".join(lines)


def brake_view(state: Optional[str]) -> str:
    from . import brake, lifecycle
    if state:
        brake.set_enabled(state == "on")
    summary = brake.summary(lifecycle.all_cron_jobs())
    lines = [f"Runaway Brake is {_on(summary.get('on'))}. It stops a cron run that goes far past that job's normal use."]
    jobs = summary.get("jobs") or []
    if summary.get("on") and jobs:
        lines.append(f"Watching {len(jobs)} job(s).")
    stops = summary.get("stops") or []
    if stops:
        lines.append(f"Stopped {len(stops)} run(s) so far.")
    return "\n".join(lines)


def prices_view(session_id: str, lookup: Optional[str]) -> str:
    from . import chat_savings, price_discovery, store
    if lookup:
        store.set_setting(price_discovery.SETTING, lookup == "on")
    lines = [f"Public price lookup is {_on(price_discovery.enabled())}."]
    pricing = (chat_savings.summary(session_id).get("projection_pricing") or {}) if session_id else {}
    model, inp, out = pricing.get("model"), pricing.get("input_usd_per_token"), pricing.get("output_usd_per_token")
    if model and inp is not None and out is not None:
        lines.append(f"{model}: {_money(inp * 1e6)} in · {_money(out * 1e6)} out per million tokens (list price).")
    elif model:
        lines.append(f"{model}: price not known yet. Add a rate card with hermes crystal rate-card.")
    lines.append("Savings are estimates at these prices, not your bill.")
    return "\n".join(lines)


# ---------------------------------------------------------------- dispatch

def handle(raw_args: str, session_id: str = "") -> str:
    from . import chat_savings
    parts = (raw_args or "").strip().split()
    words = [p.lower() for p in parts]
    head, rest = (words[0] if words else ""), words[1:]
    routes: dict[str, Callable[[], str]] = {
        "": lambda: status(session_id),
        "status": lambda: status(session_id),
        "help": lambda: USAGE,
    }
    try:
        if head in routes and not rest:
            return routes[head]()
        if head == "chat" and rest in (["on"], ["off"]):
            if not session_id:
                return ("There's no chat yet. Send your first message, then type /crystallize chat " + rest[0] + ".\n"
                        "To start every new chat with Crystallize on: /crystallize chat default on")
            return chat_savings.set_enabled(session_id, rest[0] == "on")["message"]
        if head == "chat" and rest in (["default", "on"], ["default", "off"]):
            from . import store
            store.set_setting(store.CHAT_DEFAULT_SETTING, rest[1] == "on")
            return ("New chats now start with Crystallize on. Turn it off for one chat with /crystallize chat off." if rest[1] == "on"
                    else "New chats start with Crystallize off. Turn it on per chat with /crystallize chat on.")
        if head == "rent":
            if not rest:
                return rent_view(session_id)
            if rest[0] in ("remove", "restore") and len(rest) == 2:
                return rent_change(session_id, rest[0], parts[2])
            if rest[0] == "auto" and rest[1:] in (["on"], ["off"]):
                return rent_auto(rest[1] == "on")
        if head == "line" and rest in (["on"], ["off"]):
            from . import live_notice
            return live_notice.set_enabled(rest[0] == "on")
        if head == "jobs" and not rest:
            return jobs_view(session_id)
        if head == "brake" and rest in ([], ["on"], ["off"]):
            return brake_view(rest[0] if rest else None)
        if head == "prices" and (not rest or rest[:1] == ["lookup"] and rest[1:] in (["on"], ["off"])):
            return prices_view(session_id, rest[1] if rest else None)
    except ValueError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 — a chat command must never break the chat
        logger.warning("crystallize: /crystallize %s failed: %s", raw_args, exc, exc_info=True)
        return "Crystallize couldn't read its data. Check `hermes logs` for details."
    return USAGE
