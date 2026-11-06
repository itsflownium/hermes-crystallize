"""The pre-run step of a live routine.

When a job is promoted, Crystallize gives its cron job a small pre-run script
(Hermes's own `script` field). Before the AI starts, the script runs
`hermes crystal prerun <routine>`, which:

  1. runs the routine's tested steps (through Hermes's tool registry),
  2. cleans their output: HTML and feeds become plain text, JSON loses link
     fields nobody reads, files the steps wrote are included,
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

from . import history, lifecycle, runner, store

MAX_STEP_CHARS = 40000      # per step, after cleaning; cut only at a line (item) boundary
MAX_STRING_CHARS = 800      # one JSON string value (an issue body, an article)
MAX_FILE_BYTES = 2_000_000  # files a step wrote that we read back
SKIP_GATE = '{"wakeAgent": false}'


# ---------------------------------------------------------------- cleaning

_DROP_KEY = re.compile(r"(^node_id$|^gravatar_id$|^_links$|(?<!html)_url$)")


def _slim_json(value: Any, max_string: int = MAX_STRING_CHARS) -> Any:
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
            out[k] = _slim_json(v, max_string)
        return out
    if isinstance(value, list):
        return [_slim_json(v, max_string) for v in value]
    if isinstance(value, str) and len(value) > max_string:
        return value[:max_string] + f"… ({len(value)} characters)"
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


# ---------------------------------------------------------------- cleaning a live tool result

CLEANED_TOOLS = {"web_extract", "web_search", "web_crawl", "terminal", "execute_code"}
MIN_CLEAN_CHARS = 1500      # below this the saving isn't worth changing anything
MIN_CLEAN_GAIN = 0.05
_FULL_TEXT = 1_000_000      # the AI asked for this result itself, so long text is kept whole


def _clean_for_agent(text: str) -> str:
    """Like clean_text, but never shortens the text the AI might be reading it for."""
    s = text.strip()
    if s and s[0] in "[{":
        try:
            value = json.loads(s)
        except ValueError:
            return _tidy(s)
        slim = _slim_json(value, _FULL_TEXT)
        if isinstance(slim, list):
            return "\n".join(json.dumps(v, ensure_ascii=False, separators=(",", ":")) for v in slim)
        return json.dumps(slim, ensure_ascii=False, separators=(",", ":"))
    return clean_text(s)


_PASSING_LINE = re.compile(r"(\.\.\. ok|\bPASSED\b.*|^\s*(ok|✓|✔|PASS)\b.*)\s*$", re.IGNORECASE)
MIN_PASSING_LINES = 20      # fewer than this isn't a long test log


def collapse_passing(text: str) -> str:
    """Remove the passing lines from a long test log and say how many went. Failures, errors
    and everything else stay exactly as they were. Uses no AI."""
    lines = text.split("\n")
    kept = [line for line in lines if not _PASSING_LINE.search(line)]
    removed = len(lines) - len(kept)
    if removed < MIN_PASSING_LINES:
        return text
    return f"[{removed} passing test lines removed]\n" + "\n".join(kept)


def clean_result(tool: str, result: Any) -> Optional[str]:
    """A smaller version of a tool result, in its original shape, or None to leave it.

    Removes markup, API link fields and repeated whitespace, which every later AI call
    would otherwise pay to re-read. Uses no AI, and fails safe: anything unexpected
    leaves the result untouched.
    """
    if tool not in CLEANED_TOOLS or not isinstance(result, str) or len(result) < MIN_CLEAN_CHARS:
        return None
    try:
        try:
            data = json.loads(result)
        except ValueError:
            cleaned = _clean_for_agent(result)
        else:
            if not isinstance(data, dict):
                cleaned = _clean_for_agent(result)
            elif data.get("error"):
                return None  # the exact error may matter
            else:
                changed = False
                for page in data.get("results") or []:
                    if isinstance(page, dict):
                        for key in ("content", "text", "markdown", "raw_content", "description"):
                            if isinstance(page.get(key), str):
                                page[key], changed = _clean_for_agent(page[key]), True
                for key in ("output", "content", "result", "text"):
                    if isinstance(data.get(key), str):
                        data[key], changed = _clean_for_agent(collapse_passing(data[key])), True
                if not changed:
                    return None
                cleaned = json.dumps(data, ensure_ascii=False)
    except Exception:  # noqa: BLE001 — never break a run over a saving
        return None
    return cleaned if len(cleaned) <= len(result) * (1 - MIN_CLEAN_GAIN) else None


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


WHEN_UNCHANGED = ("repeat", "quiet", "run")
_SILENT = "[SILENT]"


def _recent_answers(name: str, limit: int = 8) -> list[str]:
    """The job's latest answers, oldest first."""
    runs = [r for r in history.load_runs(name)
            if r.scheduled and r.session_id and r.outcome in (None, "completed")][-limit:]
    answers = history.final_answers([r.session_id for r in runs])
    return [answers[r.session_id].strip() for r in runs if r.session_id in answers]


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
    return "quiet" if _SILENT in _recent_answers(routine["name"]) else "repeat"


MAX_LINKS = 4000
MAX_NEW_SHARE = 0.7         # mostly new: the AI might as well see it all


_LINK = re.compile(r"\[(https?://[^\]\s]+)\]\(\1\)")


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


# ---------------------------------------------------------------- cleaning a live tool result

CLEANED_TOOLS = {"web_extract", "web_search", "web_crawl", "terminal", "execute_code"}
MIN_CLEAN_CHARS = 1500      # below this the saving isn't worth changing anything
MIN_CLEAN_GAIN = 0.05
_FULL_TEXT = 1_000_000      # the AI asked for this result itself, so long text is kept whole


def _clean_for_agent(text: str) -> str:
    """Like clean_text, but never shortens the text the AI might be reading it for."""
    s = text.strip()
    if s and s[0] in "[{":
        try:
            value = json.loads(s)
        except ValueError:
            return _tidy(s)
        slim = _slim_json(value, _FULL_TEXT)
        if isinstance(slim, list):
            return "\n".join(json.dumps(v, ensure_ascii=False, separators=(",", ":")) for v in slim)
        return json.dumps(slim, ensure_ascii=False, separators=(",", ":"))
    return clean_text(s)


def clean_result(tool: str, result: Any) -> Optional[str]:
    """A smaller version of a tool result, in its original shape, or None to leave it.

    Removes markup, API link fields and repeated whitespace, which every later AI call
    would otherwise pay to re-read. Uses no AI, and fails safe: anything unexpected
    leaves the result untouched.
    """
    if tool not in CLEANED_TOOLS or not isinstance(result, str) or len(result) < MIN_CLEAN_CHARS:
        return None
    try:
        try:
            data = json.loads(result)
        except ValueError:
            cleaned = _clean_for_agent(result)
        else:
            if not isinstance(data, dict):
                cleaned = _clean_for_agent(result)
            elif data.get("error"):
                return None  # the exact error may matter
            else:
                changed = False
                for page in data.get("results") or []:
                    if isinstance(page, dict):
                        for key in ("content", "text", "markdown", "raw_content", "description"):
                            if isinstance(page.get(key), str):
                                page[key], changed = _clean_for_agent(page[key]), True
                for key in ("output", "content", "result", "text"):
                    if isinstance(data.get(key), str):
                        data[key], changed = _clean_for_agent(collapse_passing(data[key])), True
                if not changed:
                    return None
                cleaned = json.dumps(data, ensure_ascii=False)
    except Exception:  # noqa: BLE001 — never break a run over a saving
        return None
    return cleaned if len(cleaned) <= len(result) * (1 - MIN_CLEAN_GAIN) else None


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


WHEN_UNCHANGED = ("repeat", "quiet", "run")
_SILENT = "[SILENT]"


def _recent_answers(name: str, limit: int = 8) -> list[str]:
    """The job's latest answers, oldest first."""
    runs = [r for r in history.load_runs(name)
            if r.scheduled and r.session_id and r.outcome in (None, "completed")][-limit:]
    answers = history.final_answers([r.session_id for r in runs])
    return [answers[r.session_id].strip() for r in runs if r.session_id in answers]


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
    return "quiet" if _SILENT in _recent_answers(routine["name"]) else "repeat"


MAX_LINKS = 4000
MAX_NEW_SHARE = 0.7         # mostly new: the AI might as well see it all


def _line_hashes(text: str) -> list[str]:
    return [hashlib.sha1(fingerprint(line).encode()).hexdigest()[:12] for line in text.splitlines() if line.strip()]


_LINK = re.compile(r"https?://[^\s\"'<>)\]|]+")
MIN_ITEMS = 5               # fewer links than this isn't a list of items


def _links(text: str) -> set[str]:
    return {link.rstrip(".,;:").rstrip("/").lower() for link in _LINK.findall(text)}


def _new_only_text(data: str, seen_links: set[str], answer: str) -> Optional[str]:
    """Only the items that weren't there last time, with the last answer to update. Items are
    matched by their links, because a fetch tool can format the same feed differently each run.
    None when there's nothing to gain: too few links to compare, mostly new, or nothing to go on."""
    links = _links(data)
    fresh = links - seen_links
    if not answer or len(links) < MIN_ITEMS or len(seen_links) < MIN_ITEMS or len(fresh) > len(links) * MAX_NEW_SHARE:
        return None
    lines = data.splitlines()
    keep: list[str] = []
    for i, line in enumerate(lines):
        if _links(line) & fresh:
            if i and lines[i - 1].strip() and not _LINK.search(lines[i - 1]) and lines[i - 1] not in keep:
                keep.append(lines[i - 1])  # a title often sits on the line above its link
            keep.append(line)
    if not keep:
        return ("Crystallize: this job's items are the same as last run, so its last answer still stands. Reply with "
                "exactly the text between the markers, without the markers.\n<<<\n" + answer + "\n>>>")
    return ("Crystallize: most of this job's items are the same as last run, so only the new ones are below "
            f"({len(fresh)} of {len(links)} links). Update your last answer using them: keep what still stands, and "
            "bring in anything new that matters more. Don't fetch anything.\n\n"
            "Your last answer:\n<<<\n" + answer + "\n>>>\n\nNew items:\n" + "\n".join(keep))


def _repeat_text(answer: str) -> str:
    return ("Crystallize: this job's data hasn't changed since the last run, so the last answer still "
            "stands. Don't use any tools. Reply with exactly the text between the markers, without "
            f"the markers.\n<<<\n{answer}\n>>>")


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
    # A job's own pre-run script is part of what the AI would see, so its output counts too.
    fp = fingerprint(os.environ.get("CRYSTALLIZE_OWN_OUTPUT", "") + data)
    if meta.get("last_fingerprint") == fp:
        action = unchanged_action(routine)
        last = (_recent_answers(routine["name"]) or [""])[-1] if action == "repeat" else ""
        if action == "quiet" or last == _SILENT:
            _record_skip(routine)
            return f"Crystallize: no change since the last run ({time.time() - t0:.1f}s).\n{SKIP_GATE}", 0
        if last:
            return _repeat_text(last), 0
    seen = set(meta.get("last_links") or [])
    meta["last_fingerprint"] = fp
    meta["last_data_chars"] = len(data)
    links_now = _links(data)
    if len(links_now) >= MIN_ITEMS:  # a run that came back in a link-poor format says nothing about the items
        meta["last_links"] = sorted(links_now)[:MAX_LINKS]
    store.update_routine(routine_id, meta=meta)
    if meta.get("new_only") and not no_ai:
        recent = _recent_answers(routine["name"])
        shorter = _new_only_text(data, seen, recent[-1] if recent else "")
        if shorter:
            return shorter, 0
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


def write_script(routine_id: str, name: str, own_script: Optional[str] = None, no_ai: bool = False) -> str:
    """Write the tiny pre-run script for a routine and return its `script` value.

    Hermes starts cron scripts on the Python that matches its current packages, so the
    script uses that same Python. A path saved at Promote time can be a different Python
    whose Hermes startup then loads packages built for this one, which breaks compiled
    ones (it made web_extract fail on every cron run, though it worked from a terminal).

    A job that already had a pre-run script keeps it: it runs first, can still stop the
    run with its own wakeAgent gate, and its output stays in the AI's prompt.
    """
    no_ai_literal = "True" if no_ai else "False"
    body = f'''"""Crystallize pre-run for {name!r}. Created by the Crystallize plugin.

Runs the routine's tested steps and prints their data for the AI.
To stop using it: `hermes crystal undo {name}`.
"""
import json
import os
import subprocess
import sys

OWN_SCRIPT = {own_script!r}
env = dict(os.environ)
if OWN_SCRIPT:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), OWN_SCRIPT)
    own_cmd = [sys.executable, path] if path.endswith(".py") else [path]
    own = subprocess.run(own_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=os.path.dirname(path))
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
        env["CRYSTALLIZE_OWN_OUTPUT"] = own.stdout

CMD = [sys.executable, "-m", "hermes_cli.main", "crystal", "prerun", {json.dumps(routine_id)}]
proc = subprocess.run(CMD, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
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
    return script_rel(routine_id)


def remove_script(routine_id: str) -> None:
    try:
        (scripts_dir() / f"{routine_id}.py").unlink()
    except OSError:
        pass
