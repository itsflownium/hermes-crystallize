"""The pre-run step of a live routine.

When a job is promoted, Crystallize gives its cron job a small pre-run script
(Hermes's own `script` field). Before the AI starts, the script runs
`hermes crystal prerun <routine>`, which:

  1. runs the routine's tested steps (through Hermes's tool registry),
  2. cleans their output: HTML and feeds become plain text, JSON keeps its
     fields, and declared file artifacts are included,
  3. compares it with the data the AI saw last time. If nothing changed, the job's
     "when unchanged" choice decides (see `unchanged_action`): repeat the last answer,
     stay quiet, or run as normal. Otherwise it prints the data, which Hermes puts in
     the prompt.

If a step fails, it says so and the AI does the whole task itself.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from . import adaptive, history, lifecycle, runner, store

MAX_STEP_CHARS = 40000      # legacy API value; complete output is never cut to this value
MAX_STRING_CHARS = 800      # retained for compatibility; semantic strings are never cut here
MAX_FILE_BYTES = 2_000_000  # files a step wrote that we read back
SKIP_GATE = '{"wakeAgent": false}'


# ---------------------------------------------------------------- cleaning

def _slim_json(value: Any, max_string: int = MAX_STRING_CHARS) -> Any:
    """Keep every field and value; generic JSON has no disposable field names.

    The historical name and argument remain for callers. Size limits belong at the
    presentation boundary, after full semantic comparison, never inside an object.
    """
    if isinstance(value, dict):
        return {k: _slim_json(v, max_string) for k, v in value.items()}
    if isinstance(value, list):
        return [_slim_json(v, max_string) for v in value]
    return value


def _json_text(value: Any) -> str:
    value = _slim_json(value)
    if isinstance(value, list):
        # Valid JSON, with one item per line so presentation cuts have boundaries.
        return "[\n" + ",\n".join(json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str) for v in value) + "\n]"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _compact_json(text: str) -> str:
    """Remove only JSON's insignificant whitespace, preserving numeric tokens too."""
    inside, escaped, out = False, False, []
    for char in text:
        if inside:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                inside = False
        elif char == '"':
            inside = True
            out.append(char)
        elif char not in " \t\r\n":
            out.append(char)
    return "".join(out)


_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>")
_BREAK = re.compile(r"(?i)<\s*(br|/p|/div|/li|/h[1-6]|/tr|/article|/section)\b[^>]*>")


def _strip_html(text: str) -> str:
    text = _BLOCK.sub(" ", text)
    text = _BREAK.sub("\n", text)
    text = _TAG.sub(" ", text)
    return html.unescape(text)


def _field(block: str, *names: str) -> str:
    for n in names:
        m = re.search(rf"(?is)<{n}\b[^>]*?(?:href=\"([^\"]+)\"[^>]*)?(?:/>|>(.*?)</{n}>)", block)
        if m:
            raw = m.group(2) if m.group(2) is not None else (m.group(1) or "")
            raw = re.sub(r"(?s)<!\[CDATA\[(.*?)\]\]>", r"\1", raw)
            return " ".join(_strip_html(raw).split())
    return ""


def _feed_text(text: str) -> Optional[str]:
    """RSS / Atom → one short block per item (title, link, date, summary)."""
    items = re.findall(r"(?is)<(item|entry)\b.*?</\1>", text)
    if not items:
        return None
    blocks = re.findall(r"(?is)<(?:item|entry)\b.*?</(?:item|entry)>", text)
    out = []
    for b in blocks:
        title = _field(b, "title")
        link = _field(b, "link", "guid", "id")
        date = _field(b, "pubDate", "published", "updated", "dc:date")
        summary = _field(b, "description", "summary", "content")
        out.append(" | ".join(x for x in (title, link, date) if x) + (f"\n  {summary}" if summary else ""))
    return "\n".join(out)


_MD_SAME_LINK = re.compile(r"\[(https?://[^\]\s]+)\]\(\1\)")


def _tidy(text: str) -> str:
    # Feeds that a fetch tool already turned into Markdown keep escaped XML markers.
    text = re.sub(r"<!\\?\[CDATA\\?\[", "\n• ", text)
    text = re.sub(r"\\?\]\\?\]>", "\n", text)
    text = re.sub(r"\\([#\[\]()_*])", r"\1", text)
    text = _MD_SAME_LINK.sub(r"\1", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def clean_text(text: str) -> str:
    """Compact JSON presentation without discarding any fields or string content.

    Plain text, markup, feeds, code, status and test logs remain exact. Their
    formatting or apparently repetitive values can be part of the task.
    """
    if text.lstrip()[:1] in ("[", "{"):
        try:
            json.loads(text)
            return _compact_json(text)
        except ValueError:
            pass
    return text


def _unwrap(result: str) -> tuple[str, list[tuple[str, str]]]:
    """A tool's JSON wrapper → (main text, [(title, text) for each page it fetched])."""
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        return result, []
    if not isinstance(data, dict):
        return result, []
    pages = data.get("results")
    content_keys = ("content", "text", "markdown", "raw_content", "snippet", "description")
    if isinstance(pages, list) and pages and all(isinstance(p, dict) and any(k in p for k in content_keys) for p in pages):
        parts = []
        for p in pages:
            title = p.get("title") or p.get("url") or "page"
            key = next((k for k in content_keys if isinstance(p.get(k), str)), None)
            metadata = {k: v for k, v in p.items() if k != key}
            body = (_json_text(metadata) + "\n" if metadata else "") + (p[key] if key else _json_text(p))
            if p.get("url") and p.get("url") != title:
                title = f"{title} ({p['url']})"
            parts.append((str(title), body if isinstance(body, str) else json.dumps(body, default=str)))
        outer = {k: v for k, v in data.items() if k != "results"}
        return _json_text(outer) if outer else "", parts
    for key in ("output", "content", "result", "text"):
        body = data.get(key)
        if isinstance(body, str) and set(data) <= {key, "exit_code", "error"} and not data.get("error"):
            return body, []
    return result, []


_PATH = re.compile(r"""(?:(?<=[\s"'=>])|^)((?:~|/|[A-Za-z]:\\)[^\s"'|;&<>()]+)""")


def written_files(args: Any, started: float) -> list[Path]:
    """Files a step's arguments name that exist and were written during the step."""
    text = args if isinstance(args, str) else json.dumps(args, default=str)
    found: list[Path] = []
    for raw in _PATH.findall(text):
        try:
            p = Path(os.path.expanduser(raw.rstrip(".,"))).resolve()
            st = p.stat()
        except (OSError, ValueError, RuntimeError):
            continue
        if p.is_file() and st.st_mtime >= started - 1 and p not in found:
            found.append(p)
    return found


def _cap(text: str, limit: int = MAX_STEP_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    cut = cut if cut > limit // 2 else limit
    return text[:cut] + f"\n… ({len(text) - cut} more characters not shown)"


def step_block(step: dict, output: dict, *, limit: Optional[int] = None) -> str:
    """Complete step data and artifact contents; a learned budget is advisory.

    Oversized or unfamiliar data falls back to the whole original, so the model
    can inspect every source without guessing what a display limit omitted.
    """
    raw = output.get("output")
    main = raw if isinstance(raw, str) else _json_text(raw) if raw is not None else ""
    sources = []
    for artifact in output.get("artifacts") or []:
        if (not isinstance(artifact, dict) or not isinstance(artifact.get("content"), str)
                or artifact.get("truncated") or artifact.get("omitted")):
            raise ValueError("An artifact lacks complete current contents; Hermes must finish the whole task.")
        sources.append((f"### File {artifact.get('name') or 'artifact'}", artifact["content"]))
    for path in written_files(output.get("args", step.get("args")), output.get("started") or 0):
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"File {path.name} exceeds the complete-result limit; Hermes must finish the whole task.")
        sources.append((f"### File {path.name}", path.read_bytes().decode("utf-8")))
    parts = []
    if main.strip():
        parts.append(clean_text(main))
    for heading, body in sources:
        parts.append(f"{heading}\n{clean_text(body)}")
    text = "\n\n".join(p for p in parts if p)
    if limit is not None and len(text) > limit:
        return f"[Crystallize: learned presentation budget {limit} characters exceeded; complete output follows.]\n{text}"
    return text


# ---------------------------------------------------------------- cleaning a live tool result

CLEANED_TOOLS = {"web_extract", "web_search", "web_crawl", "terminal", "execute_code"}
MIN_CLEAN_CHARS = 1500      # below this the saving isn't worth changing anything
MIN_CLEAN_GAIN = 0.05
_FULL_TEXT = 1_000_000      # the AI asked for this result itself, so long text is kept whole


def _trim_lines(text: str) -> str:
    """Remove trailing spaces and runs of blank lines. Indentation can carry meaning, so it stays."""
    text = _MD_SAME_LINK.sub(r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+(?=\r?\n)", "", text)).strip("\r\n")


def _clean_for_agent(text: str, prose: bool = False) -> str:
    """Like clean_text, but never shortens the text the AI might be reading it for. Indentation
    stays, because it can carry meaning. Only prose from the web has its spacing evened out."""
    s = text.strip("\r\n")
    if s.lstrip()[:1] in ("[", "{"):
        try:
            value = json.loads(s)
        except ValueError:
            return _trim_lines(s)
        return _json_text(value)
    head = s[:2000].lower()
    if "<rss" in head or "<feed" in head or "<html" in head or "<body" in head or len(_TAG.findall(s[:5000])) > 30:
        return clean_text(s)
    if prose:
        # Evens out spacing inside lines, as _tidy does, but leaves each line's indentation.
        return _trim_lines(re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", re.sub(r"\\([#\[\]()_*])", r"\1", s)))
    return _trim_lines(s)


# One test per line, such as "name ... ok", "path::name PASSED [ 10%]" or "ok 12 - name". A summary
# such as "1 failed, 30 passed" never matches, because it has to stay.
_PASSING_LINE = re.compile(r"(\s\.\.\.\s+ok\s*$|^\S+\s+PASSED(\s+\[\s*\d+%\])?\s*$|^ok\s+\d+\b|^ok\s+\S+\s+[\d.]+s\s*$|^\s*PASS\s|^\s*[✓✔]\s)")
MIN_PASSING_LINES = 20      # fewer than this isn't a long test log


def collapse_passing(text: str) -> str:
    """Compatibility helper: test names, status and timing are semantic evidence."""
    return text


def clean_result(tool: str, result: Any) -> Optional[str]:
    """A smaller version of a tool result, in its original shape, or None to leave it.

    Only JSON serialization changes. Every key, nested value and exact string
    remains available; no status, number, timestamp or passing line is suppressed.
    """
    if tool not in CLEANED_TOOLS or not isinstance(result, str) or len(result) < MIN_CLEAN_CHARS:
        return None
    try:
        try:
            data = json.loads(result)
        except ValueError:
            return None
        else:
            if isinstance(data, dict) and data.get("error"):
                return None  # the exact error may matter
            cleaned = _compact_json(result)
    except Exception:  # noqa: BLE001 — never break a run over a saving
        return None
    return cleaned if len(cleaned) <= len(result) * (1 - MIN_CLEAN_GAIN) else None


# ---------------------------------------------------------------- change check

_VOLATILE: list[re.Pattern[str]] = []  # fields are meaningful unless a contract explicitly says otherwise


class _ExactNumber(str):
    pass


class _ExactObject(list):
    pass


def _canonical_content(text: str) -> str:
    """Normalize JSON presentation while keeping values, order and plain-text indentation."""
    try:
        value = json.loads(text, parse_int=_ExactNumber, parse_float=_ExactNumber, object_pairs_hook=_ExactObject)
    except (ValueError, TypeError):
        return text

    def exact(value):
        if isinstance(value, _ExactNumber):
            return ["number", str(value)]
        if isinstance(value, _ExactObject):
            return ["object", [[key, exact(child)] for key, child in sorted(value, key=lambda pair: pair[0])]]
        if isinstance(value, list):
            return ["array", [exact(child) for child in value]]
        return [type(value).__name__, value]

    return json.dumps(exact(value), ensure_ascii=False, separators=(",", ":"))


def fingerprint(text: str) -> str:
    """Hash full meaningful content. Dates, timings, numbers and hashes can all matter."""
    return hashlib.sha256(_canonical_content(text).encode("utf-8", "replace")).hexdigest()


# ---------------------------------------------------------------- the pre-run

def _record_skip(routine: dict, key: str = "skips") -> None:
    """Note a run the AI never took part in. `skips`: nothing changed;
    `no_ai_runs`: the routine's output was the result."""
    meta = dict(routine.get("meta") or {})
    stamps = list(meta.get(key) or [])[-199:]
    stamps.append(time.time())
    meta[key] = stamps
    store.update_routine(routine["id"], meta=meta)


WHEN_UNCHANGED = ("repeat", "quiet", "run")
_SILENT = "[SILENT]"


def _recent_answers(name: str, limit: int = 8) -> list[str]:
    """The job's latest answers, oldest first."""
    runs = [r for r in history.load_runs(name)
            if r.scheduled and r.session_id and r.outcome in (None, "completed")][-limit:]
    answers = history.final_answers([r.session_id for r in runs])
    return [answers[r.session_id].strip() for r in runs if r.session_id in answers]


_NOT_FAILED = {None, "completed", "running", "claimed", "no_change", "skipped", "silent"}


def _last_run_failed(name: str) -> bool:
    """A failed run never answered what it fetched, so that data still counts as new."""
    runs = [r for r in history.load_runs(name) if r.scheduled and r.outcome not in ("running", "claimed")]
    return bool(runs) and runs[-1].outcome not in _NOT_FAILED


def unchanged_action(routine: dict[str, Any]) -> str:
    """What a run does when its data hasn't changed.

      repeat  One short AI call repeats the last answer, so the message still goes out.
      quiet   No AI and no message: the run costs nothing. Hermes can't deliver anything
              from a skipped run, so this is only the default when no one is left waiting:
              the job only saves its results locally, or already stays silent at times.
      run     The AI runs as normal.

    A no-AI job's output is delivered for free, so it only goes quiet if asked to.
    """
    meta = routine.get("meta") or {}
    choice = meta.get("when_unchanged")
    steps = routine.get("steps")
    if isinstance(steps, dict) and steps.get("leaves_to_ai"):
        return "run"  # it has something to send or change, whatever the data says
    if meta.get("mode") == "no_ai":
        return "quiet" if choice == "quiet" else "run"
    if choice in WHEN_UNCHANGED:
        return choice
    if meta.get("skip_unchanged") is False:  # set before this choice existed
        return "run"
    job = lifecycle.cron_job(routine.get("source_ref") or "") or lifecycle.cron_job(routine.get("name") or "")
    if job and job.get("deliver") == "local":
        return "quiet"
    return "quiet" if any(history.is_silent(a) for a in _recent_answers(routine["name"])) else "repeat"


MAX_ITEMS = 300             # a longer list isn't compared item by item
MAX_NEW_SHARE = 0.7         # mostly new: the AI might as well see it all
_LINK = re.compile(r"https?://[^\s\"'<>)\]|{}]+")
MIN_ITEMS = 5               # fewer links than this isn't a list of items


def _links(text: str) -> set[str]:
    # URL paths, queries and fragments can be case-sensitive. Do not merge distinct items.
    return {link.rstrip(".,;:").rstrip("/") for link in _LINK.findall(text)}


def _words(text: str) -> list[str]:
    """Versioned digest of the complete ordered content, including negations/repetitions.

    Older saved word-set signatures deliberately compare as changed once after upgrade.
    """
    return ["content-v2", fingerprint(text)]


def _item_blocks(data: str) -> dict[str, tuple[int, int]]:
    """Each link and the lines that belong to it: the line above it, its own line and any details
    below. The line just above the next link is that item's title, so it belongs to that item."""
    lines = data.splitlines()
    linked = [i for i, line in enumerate(lines) if _links(line)]
    blocks: dict[str, tuple[int, int]] = {}
    starts = []
    for i in linked:
        separate_link = bool(re.fullmatch(r"\s*(?:(?:Article URL|Link|URL)\s*:\s*)?https?://\S+\s*", lines[i], re.I))
        starts.append(i - 1 if separate_link and i and lines[i - 1].strip() and i - 1 not in linked else i)
    if starts:
        starts[0] = 0  # preserve any heading or summary outside the individual items
    for n, i in enumerate(linked):
        start = starts[n]
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        for link in _links(lines[i]):
            # Repeated identities must include every occurrence in the comparison.
            blocks[link] = (blocks[link][0], end) if link in blocks else (start, end)
    return blocks


def _items(data: str) -> Optional[dict[str, list[str]]]:
    """A compact record of what each item said, to spot changes next run. None if there are too many."""
    blocks = _item_blocks(data)
    if len(blocks) > MAX_ITEMS:
        return None
    lines = data.splitlines()
    return {link: _words("\n".join(lines[a:b])) for link, (a, b) in blocks.items()}


def _item_changed(old: list[str], new: list[str]) -> bool:
    """Any meaningful content change counts, including added or removed words/fields."""
    return old != new


def _new_only_text(data: str, seen: dict[str, list[str]], answer: str) -> Optional[str]:
    """Only what's new or changed since last time, what went away, and the last answer to update.
    None sends everything: too few items to compare, most of them changed, or nothing to go on."""
    now = _items(data)
    if not answer or now is None or len(now) < MIN_ITEMS or len(seen) < MIN_ITEMS:
        return None
    if [link for link in now if link in seen] != [link for link in seen if link in now]:
        return None  # ranking/order changed: the complete list carries that information
    fresh = {link for link, words in now.items() if link not in seen or _item_changed(seen[link], words)}
    gone = sorted(set(seen) - set(now))
    if len(fresh) > len(now) * MAX_NEW_SHARE or len(gone) > len(seen) * MAX_NEW_SHARE:
        return None
    lines = data.splitlines()
    blocks = _item_blocks(data)
    keep_lines = sorted({i for link in fresh for i in range(*blocks[link])})
    keep = [lines[i] for i in keep_lines]
    if not keep and not gone:
        return None  # the links match but something else changed, so send it all
    removed = ("\n\nNo longer listed, so drop these if your answer mentions them:\n" + "\n".join(gone)) if gone else ""
    return ("Crystallize: most of this job's items are the same as last run, so only the new and changed ones are below "
            f"({len(fresh)} of {len(now)} items). Update your last answer using them: replace anything they change, keep what "
            "still stands, and bring in anything new that matters more. Don't fetch anything.\n\n"
            "Your last answer:\n<<<\n" + answer + "\n>>>\n\nNew and changed items:\n" + "\n".join(keep) + removed)


def _repeat_text(answer: str) -> str:
    return ("Crystallize: this job's data hasn't changed since the last run, so the last answer still "
            "stands. Don't use any tools. Reply with exactly the text between the markers, without "
            f"the markers.\n<<<\n{answer}\n>>>")


def _source_schema(value: Any, depth: int = 0) -> Any:
    """A bounded structural signature; values and list lengths remain free to change."""
    if depth > 12:
        return "nested"
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return "text"
        # A plain numeric/string result is still text, whereas structured JSON has
        # an observable field schema that a changed endpoint can invalidate.
        return _source_schema(parsed, depth + 1) if isinstance(parsed, (dict, list)) else "text"
    if isinstance(value, dict):
        return {str(key): _source_schema(child, depth + 1) for key, child in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        shapes = {json.dumps(_source_schema(child, depth + 1), sort_keys=True) for child in value}
        return {"list": sorted(shapes)}
    return "null" if value is None else "bool" if isinstance(value, bool) else "number" if isinstance(value, (float, int)) else type(value).__name__


def _run(routine_id: str, dispatch: runner.Dispatch) -> tuple[str, int]:
    """(text to print, exit code). The text is what Hermes puts in the prompt."""
    routine = store.get_routine(routine_id)
    no_ai = bool(routine and (routine.get("meta") or {}).get("mode") == "no_ai")
    if not routine or routine["status"] != "live":
        state = routine["status"] if routine else "missing"
        return (f"Crystallize: routine {routine_id} is {state}. Do the whole task yourself.",
                1 if no_ai else 0)
    if not lifecycle._replay_available():
        lifecycle.restore_unsupported_replay(routine)
        return ("Crystallize: automatic routine execution is unavailable because the public Hermes SDK has no verified "
                "guarded nested-tool dispatcher. No routine tools ran. The job's owned attachment is being restored; "
                "Do the whole task yourself using normal host tools and the configured model and reasoning.",
                1 if no_ai else 0)
    if not lifecycle.check_drift(routine):
        return ("Crystallize: this job's configuration changed, so the routine must be tested again. "
                "Do the whole task yourself.", 1 if no_ai else 0)

    t0 = time.time()
    result = runner.run(routine["steps"], dispatch)
    if not result.get("ok"):
        lifecycle.live_result(routine_id, False, result.get("reason", ""))
        return (f"Crystallize: the routine failed at step {result.get('failed_step')} "
                f"({result.get('tool')}): {result.get('reason')}. Do the whole task yourself.",
                1 if no_ai else 0)
    schemas = [_source_schema(out.get("output")) for out in result["outputs"]]
    prior_schema = (routine.get("meta") or {}).get("source_schemas")
    if prior_schema is not None and prior_schema != schemas:
        lifecycle.invalidate(routine_id, "source schema changed")
        return ("Crystallize: a source's fields or value types changed, so the routine must be tested again. "
                "Do the whole task yourself.", 1 if no_ai else 0)

    steps = routine["steps"]["steps"]
    campaign = store.get_tuning(routine["name"])
    if campaign and (campaign.get("job_id") != routine.get("source_ref") or campaign.get("status") not in {"trial", "kept"}):
        campaign = None
    learned = (campaign or {}).get("presentation_budgets")
    if not learned:
        learned = adaptive.presentation_budgets(history.load_runs(routine.get("source_ref") or routine["name"]))
    budgets = learned.get("by_tool", {}) if learned.get("ready") else {}
    blocks, full_blocks, semantic = [], [], []
    for step, out in zip(steps, result["outputs"]):
        complete_body = step_block(step, out, limit=None)
        full_blocks.append(complete_body if no_ai else f"## Step {out['step']}: {step['tool']}\n{complete_body}")
        limit = (budgets.get(step["tool"]) or {}).get("chars")
        presented = step_block(step, out, limit=limit) if isinstance(limit, int) and limit > 0 else complete_body
        blocks.append(complete_body if no_ai else f"## Step {out['step']}: {step['tool']}\n{presented}")
        semantic.append({"tool": step["tool"], "output": _canonical_content(out.get("output") or ""),
                         "artifacts": out.get("artifacts") or []})
    data = "\n\n".join(blocks)
    full_data = "\n\n".join(full_blocks)
    if no_ai and routine.get("source_ref"):
        from . import contracts

        report = contracts.evaluate(contracts.get(routine["source_ref"]), answer=data, sources=result["outputs"])
        if not report["ok"]:
            lifecycle.invalidate(routine_id, "direct output failed its configured outcome contract")
            return f"Crystallize: direct output failed its outcome contract: {report['why']}.", 1
    lifecycle.live_result(routine_id, True, "")

    routine = store.get_routine(routine_id) or routine  # live_result updated it
    meta = dict(routine.get("meta") or {})
    # A job's own pre-run script is part of what the AI would see, so its output counts too.
    own_output = os.environ.get("CRYSTALLIZE_OWN_OUTPUT", "")
    own_digest = os.environ.get("CRYSTALLIZE_OWN_DIGEST", "")
    fp = fingerprint(json.dumps({"own_output": own_digest or fingerprint(own_output), "steps": semantic,
                                 "full_data": full_data}, ensure_ascii=False))
    last_failed = _last_run_failed(routine["name"])
    if meta.get("last_fingerprint") == fp and not last_failed:
        action = unchanged_action(routine)
        last = (_recent_answers(routine["name"]) or [""])[-1] if action == "repeat" else ""
        if action == "quiet" or history.is_silent(last):
            _record_skip(routine)
            return f"Crystallize: no change since the last run ({time.time() - t0:.1f}s).\n{SKIP_GATE}", 0
        if last:
            return _repeat_text(last), 0
    seen = {} if last_failed else (meta.get("last_items") or {})
    meta["last_fingerprint"] = fp
    meta["last_data_chars"] = len(data)
    meta["source_schemas"] = schemas
    items_now = _items(full_data)
    if items_now is None or len(items_now) >= MIN_ITEMS:  # a link-poor run says nothing about the items
        meta["last_items"] = items_now or {}
    store.update_routine(routine_id, meta=meta)
    # Changed input always presents complete current evidence. A historical
    # new_only flag cannot omit an item the current task may need to reconsider.
    if no_ai:
        _record_skip(store.get_routine(routine_id) or routine, "no_ai_runs")
        return data, 0
    return (f"Collected by Crystallize routine {routine_id} at {time.strftime('%Y-%m-%d %H:%M')} "
            f"in {time.time() - t0:.1f}s. Complete captured data follows.\n\n{data}"), 0


def run(routine_id: str, dispatch: runner.Dispatch) -> tuple[str, int]:
    """Fail back to the normal agent, or report a failure when the job has no agent."""
    try:
        return _run(routine_id, dispatch)
    except Exception as exc:  # noqa: BLE001 — the script must keep its fallback contract
        try:
            routine = store.get_routine(routine_id)
            no_ai = bool(routine and (routine.get("meta") or {}).get("mode") == "no_ai")
            if routine:
                lifecycle.live_result(routine_id, False, f"pre-run raised {type(exc).__name__}")
        except Exception:  # noqa: BLE001
            no_ai = False
        return (f"Crystallize: the routine could not finish ({type(exc).__name__}). Do the whole task yourself.",
                1 if no_ai else 0)


# ---------------------------------------------------------------- the script file

def scripts_dir() -> Path:
    return history._hermes_home() / "scripts" / "crystallize"


def script_rel(routine_id: str) -> str:
    """The job's `script` value: relative to ~/.hermes/scripts/, as Hermes requires."""
    if not isinstance(routine_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", routine_id):
        raise ValueError("Invalid routine id for a generated script.")
    return f"crystallize/{routine_id}.py"


def write_script(routine_id: str, name: str, own_script: Optional[str] = None, no_ai: bool = False,
                 *, own_interpreter: Any = None, own_workdir: Optional[str] = None,
                 hermes_python: Optional[str] = None) -> str:
    """Write the tiny pre-run script for a routine and return its `script` value.

    Hermes starts cron scripts on the Python that matches its current packages, so the
    script uses that same Python. A path saved at Promote time can be a different Python
    whose Hermes startup then loads packages built for this one, which breaks compiled
    ones (it made web_extract fail on every cron run, though it worked from a terminal).

    A job that already had a pre-run script keeps it: it runs first, can still stop the
    run with its own wakeAgent gate, and its output stays in the AI's prompt.
    """
    relative = script_rel(routine_id)
    no_ai_literal = "True" if no_ai else "False"
    body = f'''"""Generated Crystallize pre-run. Job metadata below is serialized as data."""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys

JOB_NAME = {name!r}
OWN_SCRIPT = {own_script!r}
OWN_INTERPRETER = {own_interpreter!r}
OWN_WORKDIR = {own_workdir!r}
HERMES_PYTHON = {hermes_python!r}
def sanitized_child_env():
    child = dict(os.environ)
    child.pop("HERMES_YOLO_MODE", None)
    return child

env = sanitized_child_env()
if OWN_SCRIPT:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.abspath(os.path.join(root, os.path.expanduser(OWN_SCRIPT)))
    if OWN_INTERPRETER:
        if isinstance(OWN_INTERPRETER, (list, tuple)):
            interpreter = list(OWN_INTERPRETER)
        else:
            interpreter = [OWN_INTERPRETER] if os.path.isfile(OWN_INTERPRETER) else shlex.split(OWN_INTERPRETER)
    else:
        interpreter = [shutil.which("bash") or "bash"] if path.lower().endswith((".sh", ".bash")) else [sys.executable]
    own_cmd = [*interpreter, path]
    own_cwd = os.path.expanduser(OWN_WORKDIR) if OWN_WORKDIR else os.path.dirname(path)
    try:
        own = subprocess.run(own_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=own_cwd,
                             env=sanitized_child_env())
    except OSError as exc:
        sys.stderr.write("Crystallize: the original pre-run script could not start: " + str(exc) + "\\n")
        sys.exit(1)
    if own.returncode != 0:
        sys.stdout.write(own.stdout)
        sys.stderr.write(own.stderr[-2000:])
        sys.exit(own.returncode)
    last = next((line for line in reversed(own.stdout.splitlines()) if line.strip()), "")
    try:
        if json.loads(last).get("wakeAgent") is False:
            sys.stdout.write(own.stdout)
            sys.exit(0)
    except (ValueError, AttributeError):
        pass
    if own.stdout.strip():
        sys.stdout.write(own.stdout.rstrip() + "\\n\\n")
        env.pop("CRYSTALLIZE_OWN_OUTPUT", None)
        env["CRYSTALLIZE_OWN_DIGEST"] = hashlib.sha256(own.stdout.encode("utf-8", "replace")).hexdigest()

CMD = [HERMES_PYTHON or sys.executable, "-m", "hermes_cli.main", "crystal", "prerun", {json.dumps(routine_id)}]
try:
    proc = subprocess.run(CMD, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
except OSError as exc:
    sys.stderr.write("Crystallize: the routine process could not start: " + str(exc) + "\\n")
    if {no_ai_literal}:
        sys.exit(1)
    print("Crystallize: the routine couldn't run. Do the whole task yourself.")
    sys.exit(0)
if proc.returncode != 0:
    sys.stderr.write(proc.stderr[-2000:])
    if not proc.stdout.strip() and {no_ai_literal}:
        sys.exit(proc.returncode)  # a no-AI job has no one to hand over to: report the failure
    if not proc.stdout.strip():
        print("Crystallize: the routine couldn't run. Do the whole task yourself.")
        sys.exit(0)
sys.stdout.write(proc.stdout)
sys.exit(proc.returncode if {no_ai_literal} else 0)
'''
    d = scripts_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{routine_id}.py").write_text(body, encoding="utf-8")
    return relative


def remove_script(routine_id: str) -> None:
    script_rel(routine_id)  # validate before constructing a filesystem path
    try:
        (scripts_dir() / f"{routine_id}.py").unlink()
    except OSError:
        pass
