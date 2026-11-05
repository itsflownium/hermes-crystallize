"""The pre-run step of a live routine.

When a job is promoted, Crystallize gives its cron job a small pre-run script
(Hermes's own `script` field). Before the AI starts, the script runs
`hermes crystal prerun <routine>`, which:

  1. runs the routine's tested steps (through Hermes's tool registry),
  2. cleans their output: HTML and feeds become plain text, JSON loses link
     fields nobody reads, files the steps wrote are included,
  3. compares it with the data the AI saw last time. If nothing changed, it
     prints Hermes's `{"wakeAgent": false}` gate and the AI is skipped
     entirely. Otherwise it prints the data, which Hermes puts in the prompt.

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

from . import history, lifecycle, runner, store

MAX_STEP_CHARS = 40000      # per step, after cleaning; cut only at a line (item) boundary
MAX_STRING_CHARS = 800      # one JSON string value (an issue body, an article)
MAX_FILE_BYTES = 2_000_000  # files a step wrote that we read back
SKIP_GATE = '{"wakeAgent": false}'


# ---------------------------------------------------------------- cleaning

_DROP_KEY = re.compile(r"(^node_id$|^gravatar_id$|^_links$|(?<!html)_url$)")


def _slim_json(value: Any) -> Any:
    """Drop API link fields and cap long strings. A GitHub issue keeps its
    title, number, labels, body and dates but loses ~20 *_url fields."""
    if isinstance(value, dict):
        if "login" in value and "id" in value and len(value) > 4:
            return value["login"]  # a user object: the name is what matters
        out = {}
        for k, v in value.items():
            if _DROP_KEY.search(str(k)) or v is None or v == "" or v == [] or v == {}:
                continue
            if k == "url" and "html_url" in value:
                continue  # the API link next to the web link
            if k == "reactions" and isinstance(v, dict):
                v = v.get("total_count", 0)
                if not v:
                    continue
            out[k] = _slim_json(v)
        return out
    if isinstance(value, list):
        return [_slim_json(v) for v in value]
    if isinstance(value, str) and len(value) > MAX_STRING_CHARS:
        return value[:MAX_STRING_CHARS] + f"… ({len(value)} characters)"
    return value


def _json_text(value: Any) -> str:
    value = _slim_json(value)
    if isinstance(value, list):
        # One item per line, so a cut never splits an item.
        return "\n".join(json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str) for v in value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


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
        if len(summary) > 400:
            summary = summary[:400] + "…"
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
    """Make one piece of tool output small and easy to read."""
    s = text.strip()
    if not s:
        return ""
    if s[0] in "[{":
        try:
            return _json_text(json.loads(s))
        except ValueError:
            pass
    head = s[:2000].lower()
    if "<rss" in head or "<feed" in head or ("<item" in s.lower() and "<?xml" in head):
        feed = _feed_text(s)
        if feed:
            return feed
    if "<html" in head or "<body" in head or len(_TAG.findall(s[:5000])) > 30:
        return _tidy(_strip_html(s))
    return _tidy(s)


def _unwrap(result: str) -> tuple[str, list[tuple[str, str]]]:
    """A tool's JSON wrapper → (main text, [(title, text) for each page it fetched])."""
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        return result, []
    if not isinstance(data, dict):
        return result, []
    pages = data.get("results")
    if isinstance(pages, list) and pages and all(isinstance(p, dict) for p in pages):
        parts = []
        for p in pages:
            title = p.get("title") or p.get("url") or "page"
            body = p.get("content") or p.get("text") or p.get("markdown") or p.get("raw_content") or ""
            if p.get("url") and p.get("url") != title:
                title = f"{title} ({p['url']})"
            parts.append((str(title), body if isinstance(body, str) else json.dumps(body, default=str)))
        return "", parts
    for key in ("output", "content", "result", "text"):
        body = data.get(key)
        if isinstance(body, str):
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
        if p.is_file() and st.st_mtime >= started - 1 and st.st_size <= MAX_FILE_BYTES and p not in found:
            found.append(p)
    return found


def _cap(text: str, limit: int = MAX_STEP_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    cut = cut if cut > limit // 2 else limit
    return text[:cut] + f"\n… ({len(text) - cut} more characters not shown)"


def step_block(step: dict, output: dict) -> str:
    """One step's data. Every page and file gets a fair share of the space, so
    no source is ever left out because an earlier one was long."""
    main, pages = _unwrap(output.get("output") or "")
    sources = [(f"### {title}", body) for title, body in pages]
    for path in written_files(output.get("args", step.get("args")), output.get("started") or 0):
        try:
            sources.append((f"### File {path.name}", path.read_text(errors="replace")))
        except OSError:
            continue
    parts = []
    if main.strip():
        # When the step saved its data to a file, what it printed is only a preview.
        parts.append(_cap(clean_text(main), 1500 if sources else MAX_STEP_CHARS))
    share = MAX_STEP_CHARS // max(1, len(sources))
    for heading, body in sources:
        parts.append(f"{heading}\n{_cap(clean_text(body), share)}")
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------- change check

_VOLATILE = [
    re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?"),   # ISO times
    re.compile(r"(?i)\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*,? \d{1,2} [a-z]{3} \d{4}[^|\n]*?(gmt|[+-]\d{4})"),
    re.compile(r"\b\d{1,2}:\d\d(:\d\d)?\b"),                     # clock times
    re.compile(r"\b\d+\.\d+\s*(ms|s|sec|seconds)?\b"),           # timings, response times
    re.compile(r"\b\d+\s*ms\b"),
    re.compile(r"\b[0-9a-f]{24,}\b"),                            # request ids, hashes
]


def fingerprint(text: str) -> str:
    """Hash of the data with timings and timestamps removed: same data, same hash."""
    for rx in _VOLATILE:
        text = rx.sub("#", text)
    return hashlib.sha256(" ".join(text.split()).encode("utf-8", "replace")).hexdigest()


# ---------------------------------------------------------------- the pre-run

def _record_skip(routine: dict, key: str = "skips") -> None:
    """Note a run the AI never took part in. `skips`: nothing changed;
    `no_ai_runs`: the routine's output was the result."""
    meta = dict(routine.get("meta") or {})
    stamps = list(meta.get(key) or [])[-199:]
    stamps.append(time.time())
    meta[key] = stamps
    store.update_routine(routine["id"], meta=meta)


def run(routine_id: str, dispatch: runner.Dispatch) -> tuple[str, int]:
    """(text to print, exit code). The text is what Hermes puts in the prompt."""
    routine = store.get_routine(routine_id)
    no_ai = bool(routine and (routine.get("meta") or {}).get("mode") == "no_ai")
    if not routine or routine["status"] != "live":
        state = routine["status"] if routine else "missing"
        return (f"Crystallize: routine {routine_id} is {state}. Do the whole task yourself.",
                1 if no_ai else 0)

    t0 = time.time()
    result = runner.run(routine["steps"], dispatch)
    lifecycle.live_result(routine_id, bool(result.get("ok")), result.get("reason", ""))
    if not result.get("ok"):
        return (f"Crystallize: the routine failed at step {result.get('failed_step')} "
                f"({result.get('tool')}): {result.get('reason')}. Do the whole task yourself.",
                1 if no_ai else 0)

    steps = routine["steps"]["steps"]
    blocks = []
    for step, out in zip(steps, result["outputs"]):
        body = step_block(step, out)
        blocks.append(body if no_ai else f"## Step {out['step']}: {step['tool']}\n{body}")
    data = "\n\n".join(blocks)

    routine = store.get_routine(routine_id) or routine  # live_result updated it
    meta = dict(routine.get("meta") or {})
    fp = fingerprint(data)
    if meta.get("skip_unchanged", True) and meta.get("last_fingerprint") == fp:
        _record_skip(routine)
        return f"Crystallize: no change since the last run ({time.time() - t0:.1f}s).\n{SKIP_GATE}", 0
    meta["last_fingerprint"] = fp
    meta["last_data_chars"] = len(data)
    store.update_routine(routine_id, meta=meta)
    if no_ai:
        _record_skip(store.get_routine(routine_id) or routine, "no_ai_runs")
        return data, 0
    return (f"Collected by Crystallize routine {routine_id} at {time.strftime('%Y-%m-%d %H:%M')} "
            f"in {time.time() - t0:.1f}s. Current and complete.\n\n{data}"), 0


# ---------------------------------------------------------------- the script file

def scripts_dir() -> Path:
    return history._hermes_home() / "scripts" / "crystallize"


def script_rel(routine_id: str) -> str:
    """The job's `script` value: relative to ~/.hermes/scripts/, as Hermes requires."""
    return f"crystallize/{routine_id}.py"


def write_script(routine_id: str, name: str) -> str:
    """Write the tiny pre-run script for a routine and return its `script` value."""
    cmd = lifecycle._hermes_cmd()
    body = f'''"""Crystallize pre-run for {name!r}. Created by the Crystallize plugin.

Runs the routine's tested steps and prints their data for the AI.
To stop using it: `hermes crystal undo {name}`.
"""
import subprocess
import sys

CMD = {json.dumps(cmd)} + ["crystal", "prerun", {json.dumps(routine_id)}]
proc = subprocess.run(CMD, capture_output=True, text=True, encoding="utf-8", errors="replace")
sys.stdout.write(proc.stdout)
if proc.returncode != 0:
    sys.stderr.write(proc.stderr[-2000:])
sys.exit(proc.returncode)
'''
    d = scripts_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{routine_id}.py").write_text(body, encoding="utf-8")
    return script_rel(routine_id)


def remove_script(routine_id: str) -> None:
    try:
        (scripts_dir() / f"{routine_id}.py").unlink()
    except OSError:
        pass
