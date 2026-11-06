"""Line up runs of the same job and work out which steps never really change.

How it works:
  1. Find the backbone: the tool calls that happen, in the same order, in every
     run (a longest-common-subsequence over each run's tool names).
  2. For each backbone step, compare its inputs across runs:
       fixed    identical every run
       fill-in  identical once dates, ids and temp paths are blanked out
       varies   same tool, same place, different inputs (freezable, but must
                pass test runs before anything relies on it)
       setup    loads context a script doesn't need (skills, todo lists)
       side     sends or changes something: always stays with the agent
  3. Estimate how much of each run could skip the AI.

Pure functions over ``history.Run`` objects, so it's easy to test.
"""

from __future__ import annotations

import ast
import datetime as _dt
import json
import os
import re
import shlex
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import history

MIN_RUNS = 3

# Tools that only load context for the agent. A script doesn't need them.
SETUP_TOOLS = {"skill_view", "skills_list", "todo", "todo_write", "todo_read", "memory_view"}

# Tools that send or change something outside the run.
SIDE_EFFECT_TOOLS = {
    "send_message", "send_email", "post_message", "write_file", "patch", "file_edit",
    "delete_file", "cronjob_manage", "delegate_task",
}
_SIDE_EFFECT_NAME = re.compile(r"(send|post|delete|remove|create|update|publish|deploy|write|upload|edit|"
                               r"(^|_)(set|add|run|move|merge|close|cancel|execute|trigger|approve|invite|comment|assign)(_|$))",
                               re.IGNORECASE)
_READ_NAME = re.compile(r"(^|_)(get|list|read|search|fetch|query|find|view|describe|show|lookup|count)(_|$)", re.IGNORECASE)

# Shell commands that change things. Writing into a scratch/temp file is fine.
_MUTATING_SHELL = re.compile(
    r"(\brm\s|\bmv\s|\bchmod\b|\bchown\b|\bgit\s+(push|commit|reset|checkout|merge)\b|"
    r"\bnpm\s+(publish|install)\b|\bpip\s+install\b|\bsudo\b|\bkill\b|"
    r"curl\b[^|;&]*\s-X\s*(POST|PUT|PATCH|DELETE)|curl\b[^|;&]*\s(-d|--data)\b)",
    re.IGNORECASE,
)

# Writes the shell can do with any command, and commands known only to read.
_TEMP_ROOTS = tuple({"/tmp", "/private/tmp", tempfile.gettempdir(), str(Path(tempfile.gettempdir()).resolve())})
_TEMP_PREFIX = "|".join(re.escape(root.rstrip("/\\") + os.sep) for root in _TEMP_ROOTS)
_SCRATCH_WRITE = re.compile(r"(>>?|\s-[a-zA-Z]*o|\s--output)\s*[\"']?(" + _TEMP_PREFIX + r"|/dev/null|\$TMPDIR/)[^\s;&|]*")
_SHELL_WRITES = re.compile(r">(?!>?\s*/dev/null|&\d)|\$\(|`|\bsed\s[^|;&]*-i|\s-(delete|exec)\b|\bsort\b[^|;&]*\s-o\b|system\(|"
                           r"\bcurl\b[^|;&]*\s(-o|-O|-T|-F|--output|--remote-name|--upload-file|--form|--json|--request\s*(POST|PUT|PATCH|DELETE))\b|"
                           r"\bcurl\b[^|;&]*\s-[a-zA-Z]*[XdFTOoK]|\bwget\b(?![^|;&]*-[a-zA-Z]*O\s*-)|\bwget\b[^|;&]*--(post|method|body)|"
                           r"\bdate\s+-s\b|\s-fprint|\s-fls\b|\buniq\s+(-\S+\s+)*\S+\s+\S+")
_SAFE_CURL_METHOD = re.compile(r"\s-X\s*(GET|HEAD)\b")
_READ_ONLY_COMMANDS = {"curl", "wget", "cat", "head", "tail", "grep", "rg", "ls", "find", "jq", "date", "wc", "sort", "uniq",
                       "echo", "printf", "cut", "tr", "stat", "du", "df", "dig", "uname", "cd", "true"}
_READ_ONLY_SUBCOMMANDS = ("git log", "git status", "git diff", "git show", "gh issue list", "gh issue view", "gh pr list",
                          "gh pr view", "gh repo view", "gh release list")
_KNOWN_READ_TOOLS = {"web_search", "web_extract", "read_file", "search_files", "session_search", "browser_snapshot",
                     "browser_console", "browser_get_images", "web_crawl", "tool_describe", "tool_search", "vision_analyze", "crystal_list", "crystal_run",
                     "crystal_full_result", "__run__"}
# Python counts as read-only only if everything it imports and calls is known to only read.
_SAFE_IMPORTS = {"json", "re", "math", "datetime", "time", "statistics", "collections", "itertools", "functools", "html", "csv",
                 "string", "textwrap", "base64", "hashlib", "decimal", "fractions", "unicodedata", "urllib.parse", "xml",
                 "xml.etree", "xml.etree.ElementTree", "email.utils", "random", "typing", "dataclasses", "operator", "sys",
                 "os.path", "pathlib", "requests", "httpx", "urllib.request", "feedparser", "bs4"}
_BLOCKED_CALLS = {"exec", "eval", "compile", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars",
                  "breakpoint", "Request", "terminal", "write_file", "patch", "send_message", "delegate_task", "process"}
_WRITE_METHODS = {"write", "writelines", "truncate", "write_text", "write_bytes", "unlink", "mkdir", "rmdir", "rename", "touch",
                  "chmod", "symlink_to", "hardlink_to", "post", "put", "patch", "delete", "request", "send", "stream"}

# Blanks, in the order we try them. Each is (kind, pattern, placeholder).
_FILL_INS: list[tuple[str, re.Pattern[str], str]] = [
    ("date", re.compile(
        r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"), "{date}"),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "{id}"),
    ("timestamp", re.compile(r"\b1\d{9}(?:\d{3})?\b"), "{timestamp}"),
    ("temp_path", re.compile(r"(?:/tmp|/var/folders|/private/tmp)/[\w./-]+"
                            r"|[A-Za-z]:(?:\\\\|\\|/)[^\"\s]*?\b(?:Temp|TEMP|tmp)(?:\\\\|\\|/)[\w.\\/-]+"),
     "{temp}"),
    ("hex_id", re.compile(r"\b[0-9a-f]{12,}\b", re.I), "{id}"),
]


@dataclass
class StepPlan:
    position: int
    tool_name: str
    kind: str                      # fixed | fill-in | varies | setup | side
    args: Any                      # from the most recent run
    fill_ins: list[str] = field(default_factory=list)
    distinct_inputs: int = 1
    note: str = ""
    result_lens: list[int] = field(default_factory=list)


@dataclass
class Analysis:
    job_id: Optional[str]
    job_name: Optional[str]
    runs_used: int
    ready: bool
    reason: str
    steps: list[StepPlan] = field(default_factory=list)
    extra_steps_per_run: float = 0.0     # calls outside the backbone, on average
    same_shape_share: float = 0.0        # runs whose tool sequence equals the backbone
    avg_tool_calls: Optional[float] = None
    avg_api_calls: Optional[float] = None
    avg_cost_usd: Optional[float] = None
    est_api_calls_after: Optional[float] = None
    est_cost_after_usd: Optional[float] = None
    agent_free: bool = False             # whole job could run with no AI at all
    strategy: str = "common-steps"       # or "best-run" when Hermes takes a new path each run
    source_execution_id: Optional[str] = None  # best-run: the run the steps were frozen from
    sources: list[str] = field(default_factory=list)  # URLs / repos the job's prompt names

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- helpers

def _canon(args: Any) -> str:
    if isinstance(args, str):
        return args.replace("\r\n", "\n")
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(args)


def _blank(text: str) -> tuple[str, list[str]]:
    kinds: list[str] = []
    for kind, pattern, placeholder in _FILL_INS:
        new = pattern.sub(placeholder, text)
        if new != text:
            kinds.append(kind)
            text = new
    return text, kinds


def _lcs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Index pairs of a longest common subsequence of ``a`` and ``b``."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _backbone(sequences: list[list[str]]) -> list[str]:
    common = list(sequences[0])
    for seq in sequences[1:]:
        common = [common[i] for i, _ in _lcs(common, seq)]
    return common


_PURE_BUILTINS = {"abs", "all", "any", "bool", "bytes", "dict", "enumerate", "filter", "float", "frozenset",
                  "int", "isinstance", "len", "list", "max", "min", "range", "repr", "reversed", "round", "set",
                  "sorted", "str", "sum", "tuple", "zip"}
_PURE_CALLS = {
    "json.loads": "data", "json.dumps": "text", "json.load": "data", "pathlib.Path": "path",
    "requests.get": "response", "requests.head": "response", "httpx.get": "response", "httpx.head": "response",
    "urllib.request.urlopen": "file", "urllib.parse.urlparse": "data", "urllib.parse.urlsplit": "data",
    "urllib.parse.urljoin": "text", "urllib.parse.urlencode": "text", "urllib.parse.quote": "text",
    "urllib.parse.unquote": "text", "urllib.parse.parse_qs": "data", "urllib.parse.parse_qsl": "data",
    "re.findall": "data", "re.finditer": "data", "re.split": "data", "re.sub": "text", "re.escape": "text",
    "re.search": "match", "re.match": "match", "re.fullmatch": "match", "re.compile": "regex",
    "html.escape": "text", "html.unescape": "text", "statistics.mean": "data", "statistics.median": "data",
    "math.ceil": "data", "math.floor": "data", "math.sqrt": "data", "math.log": "data", "math.isfinite": "data",
    "datetime.datetime.now": "date", "datetime.datetime.utcnow": "date", "datetime.datetime.fromisoformat": "date",
    "datetime.datetime.fromtimestamp": "date", "datetime.date.today": "date", "datetime.date.fromisoformat": "date",
    "time.time": "data", "time.monotonic": "data", "time.strftime": "text", "collections.Counter": "data",
    "collections.defaultdict": "data", "textwrap.dedent": "text", "textwrap.fill": "text",
}
_PURE_METHODS = {
    "text": {"strip", "lstrip", "rstrip", "lower", "upper", "casefold", "title", "capitalize", "replace", "split",
             "rsplit", "splitlines", "join", "startswith", "endswith", "find", "rfind", "count", "format", "encode",
             "removeprefix", "removesuffix", "isdigit", "isalpha", "isalnum"},
    "data": {"get", "keys", "values", "items", "count", "index", "copy"},
    "file": {"read", "readline", "readlines", "close"},
    "response": {"json", "raise_for_status", "close"},
    "path": {"read_text", "read_bytes", "exists", "is_file", "is_dir", "stat", "lstat", "resolve", "absolute",
             "joinpath", "glob", "rglob", "iterdir", "as_posix", "as_uri", "with_name", "with_suffix"},
    "date": {"isoformat", "strftime", "date", "time", "timestamp", "replace", "astimezone"},
    "regex": {"findall", "finditer", "search", "match", "fullmatch", "split", "sub"},
    "match": {"group", "groups", "groupdict", "start", "end", "span"},
}


class _UnknownPython(ValueError):
    pass


class _PythonReads:
    """Recognize a small expression language using known library read operations.

    This is a conservative classifier, not a sandbox or a proof about arbitrary Python.
    Callable aliases, user functions, unknown methods and reflective access stay with
    the agent and host tool policy instead of being executed again during a trial.
    """

    def __init__(self) -> None:
        self.names: dict[str, str] = {}

    def block(self, nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in _SAFE_IMPORTS:
                        raise _UnknownPython()
                    self.names[alias.asname or alias.name.split(".")[0]] = "ref:" + (alias.name if alias.asname else alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module or node.module not in _SAFE_IMPORTS | {"hermes_tools"}:
                    raise _UnknownPython()
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if alias.name == "*" or (node.module == "hermes_tools" and alias.name not in _KNOWN_READ_TOOLS):
                        raise _UnknownPython()
                    self.names[alias.asname or alias.name] = "ref:" + full
            elif isinstance(node, ast.Expr):
                self.expr(node.value)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = self.expr(node.value) if node.value else "data"
                # A callable imported directly is supported; assigning one to another
                # variable would otherwise let method aliases evade mode checks.
                if value.startswith("ref:"):
                    raise _UnknownPython()
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    self.bind(target, value)
            elif isinstance(node, ast.If):
                self.expr(node.test)
                before = dict(self.names)
                self.block(node.body)
                left = dict(self.names)
                self.names = dict(before)
                self.block(node.orelse)
                self.names = {key: value for key, value in left.items() if self.names.get(key) == value}
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                self.expr(node.iter)
                self.bind(node.target, "data")
                self.block(node.body)
                self.block(node.orelse)
            elif isinstance(node, ast.With):
                for item in node.items:
                    kind = self.expr(item.context_expr)
                    if kind != "file":
                        raise _UnknownPython()
                    if item.optional_vars:
                        self.bind(item.optional_vars, kind)
                self.block(node.body)
            elif isinstance(node, ast.Assert):
                self.expr(node.test)
                if node.msg:
                    self.expr(node.msg)
            elif not isinstance(node, (ast.Pass, ast.Break, ast.Continue)):
                raise _UnknownPython()

    def bind(self, node: ast.AST, kind: str) -> None:
        if isinstance(node, ast.Name):
            self.names[node.id] = kind
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in node.elts:
                self.bind(child, "data")
        else:
            raise _UnknownPython()

    def expr(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant):
            return "text" if isinstance(node.value, str) else "data"
        if isinstance(node, ast.Name):
            if node.id in self.names:
                return self.names[node.id]
            if node.id in _PURE_BUILTINS | {"open", "print"}:
                return "ref:" + node.id
            raise _UnknownPython()
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise _UnknownPython()
            base = self.expr(node.value)
            if base.startswith("ref:"):
                return base + "." + node.attr
            if base == "response" and node.attr in {"text", "content", "status_code", "headers", "url", "reason", "ok"}:
                return "text" if node.attr in {"text", "url", "reason"} else "data"
            if base == "path" and node.attr in {"parent", "parents", "name", "suffix", "stem", "parts"}:
                return "path" if node.attr == "parent" else "text" if node.attr in {"name", "suffix", "stem"} else "data"
            raise _UnknownPython()
        if isinstance(node, ast.Call):
            return self.call(node)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for child in node.elts:
                self.expr(child)
            return "data"
        if isinstance(node, ast.Dict):
            for child in [*node.keys, *node.values]:
                if child is not None:
                    self.expr(child)
            return "data"
        if isinstance(node, ast.Subscript):
            base = self.expr(node.value)
            if base not in {"text", "data"}:
                raise _UnknownPython()
            self.expr(node.slice)
            return base if base == "text" else "data"
        if isinstance(node, ast.Slice):
            for child in (node.lower, node.upper, node.step):
                if child:
                    self.expr(child)
            return "data"
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in node.generators:
                self.expr(gen.iter)
                self.bind(gen.target, "data")
                for cond in gen.ifs:
                    self.expr(cond)
            for child in (node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,):
                self.expr(child)
            return "data"
        if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.JoinedStr, ast.FormattedValue)):
            kinds = [self.expr(child) for child in ast.iter_child_nodes(node) if isinstance(child, ast.expr)]
            if any(kind.startswith("ref:") or kind in {"file", "response"} for kind in kinds):
                raise _UnknownPython()
            return "text" if "text" in kinds or isinstance(node, (ast.JoinedStr, ast.FormattedValue)) else "data"
        raise _UnknownPython()

    def call(self, node: ast.Call) -> str:
        if any(isinstance(arg, ast.Starred) for arg in node.args) or any(kw.arg is None for kw in node.keywords):
            raise _UnknownPython()
        for arg in [*node.args, *(kw.value for kw in node.keywords)]:
            if self.expr(arg).startswith("ref:"):
                raise _UnknownPython()
        if isinstance(node.func, ast.Attribute) and not node.func.attr.startswith("_"):
            base = self.expr(node.func.value)
            name = node.func.attr
            if not base.startswith("ref:"):
                if base == "path" and name == "open":
                    self.read_mode(node, 0)
                    return "file"
                if name not in _PURE_METHODS.get(base, set()):
                    raise _UnknownPython()
                if name in {"read_text", "as_posix", "as_uri", "isoformat", "strftime", "group"} or base == "text":
                    return "text"
                if base == "path" and name in {"resolve", "absolute", "joinpath", "with_name", "with_suffix"}:
                    return "path"
                if base == "date" and name in {"date", "time", "replace", "astimezone"}:
                    return "date"
                return "data"
        ref = self.expr(node.func)
        if not ref.startswith("ref:"):
            raise _UnknownPython()
        name = ref[4:]
        if name == "open":
            self.read_mode(node, 1)
            if any(kw.arg == "opener" for kw in node.keywords):
                raise _UnknownPython()
            return "file"
        if name == "print":
            if any(kw.arg == "file" for kw in node.keywords):
                raise _UnknownPython()
            return "data"
        if name in _PURE_BUILTINS:
            return "text" if name in {"str", "repr"} else "data"
        if name.startswith("hermes_tools.") and name.split(".", 1)[1] in _KNOWN_READ_TOOLS:
            return "data"
        if name not in _PURE_CALLS:
            raise _UnknownPython()
        if name == "urllib.request.urlopen" and (len(node.args) > 1 or any(kw.arg == "data" for kw in node.keywords)):
            raise _UnknownPython()
        if any(kw.arg in {"default", "cls", "object_hook", "object_pairs_hook", "hooks", "auth", "transport", "event_hooks"} for kw in node.keywords):
            raise _UnknownPython()
        return _PURE_CALLS[name]

    @staticmethod
    def read_mode(node: ast.Call, positional: int) -> None:
        modes = [node.args[positional]] if len(node.args) > positional else []
        modes += [kw.value for kw in node.keywords if kw.arg == "mode"]
        if len(modes) > 1 or any(not isinstance(mode, ast.Constant) or mode.value not in {"r", "rt", "rb", "tr", "br"} for mode in modes):
            raise _UnknownPython()


def _python_is_read_only(code: str) -> bool:
    """Recognized read operations only; unknown Python must stay with the agent."""
    try:
        _PythonReads().block(ast.parse(code).body)
        return True
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return False


def _inline_python_reads(part: str) -> bool:
    try:
        words = shlex.split(part, comments=True)
    except ValueError:
        return False
    return "-c" in words[:-1] and _python_is_read_only(words[words.index("-c") + 1])


def _shell_parts(command: str) -> Optional[list[str]]:
    """The commands in a line, split on operators outside quotes. None if the quotes don't close."""
    parts, current, quote, i = [], "", None, 0
    while i < len(command):
        char = command[i]
        if char == "\\" and quote != "'" and i + 1 < len(command):
            current += command[i:i + 2]
            i += 2
            continue
        # shlex is not a shell evaluator. Dynamic flags can turn a read into an
        # action (for example curl $'-XPOST', curl *, or curl <(some_program)).
        if (char == "$" and quote != "'") or (not quote and char in "{}*?[") or (not quote and command[i:i + 2] == "<("):
            return None
        if quote:
            quote = None if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char in ";|&\n":
            parts.append(current)
            current = ""
            i += 1
            continue
        current += char
        i += 1
    return None if quote else [*parts, current]


def scratch_destinations(command: str) -> list[str]:
    """Normalized output destinations for the runner's declared-artifact check.

    Parse redirections and common output options, including combined/attached curl
    options. This intentionally over-detects ambiguous output-like arguments. The
    caller must require each non-null destination to equal a declared run artifact;
    residing somewhere in a temporary directory alone is insufficient authorization.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        words = list(lexer)
    except ValueError as exc:
        raise ValueError("The shell output destinations could not be parsed.") from exc
    found = []
    for index, word in enumerate(words):
        raw = None
        redirected = word in {">", ">>", ">&", "&>"}
        if redirected or word in {"-o", "--output", "-O", "--output-document"} or re.fullmatch(r"-[A-Za-z]*o", word):
            if index + 1 >= len(words):
                raise ValueError("An output destination is missing.")
            raw = words[index + 1]
        elif word.startswith(("--output=", "--output-document=")):
            raw = word.partition("=")[2]
        else:
            attached = re.fullmatch(r"-[A-Za-z]*o(/.*|[A-Za-z]:[\\/].*)", word)
            if attached:
                raw = attached.group(1)
        if raw is None or not redirected and raw == "-":
            continue
        if redirected and raw.isdigit():
            continue  # descriptor duplication, such as 2>&1
        normalized = str(Path(os.path.expandvars(os.path.expanduser(raw))).resolve())
        if normalized not in found:
            found.append(normalized)
    return found


def _shell_is_read_only(command: str) -> bool:
    """Recognize a narrow set of read commands, rejecting executable extensions."""
    try:
        destinations = scratch_destinations(command)
    except (ValueError, OSError, RuntimeError):
        return False
    roots = [Path(root).resolve() for root in _TEMP_ROOTS]
    for destination in destinations:
        path = Path(destination)
        if path != Path(os.devnull).resolve() and not any(path.is_relative_to(root) and path != root for root in roots):
            return False
    command = _SCRATCH_WRITE.sub(" ", command)  # writing to a scratch file changes nothing that matters
    command = _SAFE_CURL_METHOD.sub(" ", command)
    if _MUTATING_SHELL.search(command) or _SHELL_WRITES.search(command):
        return False
    if re.search(r"\b(?:find)\b[^\n|;&]*\s-(?:exec\w*|ok\w*|delete|fprint\w*|fls)\b|"
                 r"--(?:pre|pre-glob|compress-program|ext-diff|textconv|output|open|web)\b|"
                 r"\b(?:git|gh)\b[^\n|;&]*\s-(?:c|C)\b", command):
        return False
    parts = _shell_parts(command)
    if parts is None:
        return False
    for part in parts:
        try:
            words = shlex.split(part, comments=True)
        except ValueError:
            return False
        if not words:
            continue
        if "=" in words[0]:
            return False  # shell environment overrides can change command semantics
        first = words[0].rsplit("/", 1)[-1]
        if "/" in words[0] and words[0] not in {f"/bin/{first}", f"/usr/bin/{first}"}:
            return False
        phrase = " ".join([first, *words[1:3]])
        normalized = _SAFE_CURL_METHOD.sub(" ", " ".join([first, *words[1:]]))
        if _MUTATING_SHELL.search(normalized) or _SHELL_WRITES.search(normalized):
            return False
        if first == "curl" and any(word.startswith(("--request=", "--data", "--json", "--config", "--upload", "--form", "--url-query")) for word in words[1:]):
            return False
        if first == "curl" and any(word == "--request" and (index + 1 >= len(words) or words[index + 1] not in {"GET", "HEAD"})
                                   for index, word in enumerate(words)):
            return False
        if first == "curl" and any(word.startswith(("--libcurl", "--cookie-jar", "--dump-header", "--trace", "--stderr",
                                                   "--etag-save", "--hsts", "--alt-svc", "--quote", "--prequote", "--postquote"))
                                   or re.match(r"^-[A-Za-z]*[cDQ]", word) for word in words[1:]):
            return False
        if first == "wget" and any(word.startswith(("--post", "--method", "--body", "--execute", "--config")) for word in words[1:]):
            return False
        if first == "wget" and any(word.startswith(("--save-cookies", "--hsts-file", "--output-file", "--append-output"))
                                   or re.match(r"^-[A-Za-z]*[eoa]", word) for word in words[1:]):
            return False
        if first == "date" and any(re.match(r"^-[^-]*s", word) or word.startswith("--set") for word in words[1:]):
            return False
        if first in {"rg", "grep"} and any(word.startswith(("--pre", "--exec", "--hostname-bin")) for word in words[1:]):
            return False
        if first in ("python", "python3") and words[1:2] == ["-c"] and _inline_python_reads(part):
            continue
        if first not in _READ_ONLY_COMMANDS and not any(phrase == p or phrase.startswith(p + " ") for p in _READ_ONLY_SUBCOMMANDS):
            return False
    return True


def _argument_action(args: Any) -> bool:
    """Read-named plugin tools may still carry an explicit mutating operation."""
    if isinstance(args, dict):
        for key, value in args.items():
            field = str(key).lower()
            if field in {"method", "http_method", "request_method"} and str(value).upper() not in {"GET", "HEAD", "OPTIONS"}:
                return True
            if field in {"action", "operation", "op", "mode"} and isinstance(value, str):
                if not re.fullmatch(r"(?:get|head|read|list|search|fetch|query|find|view|describe|show|lookup|count|select|ro|r|rb)", value, re.I):
                    return True
            if field in {"query", "sql", "statement"} and isinstance(value, str) and re.search(
                    r"\b(?:INSERT\s+INTO|DELETE\s+FROM|UPDATE\s+\w+\s+SET|DROP\s+(?:TABLE|DATABASE)|ALTER\s+TABLE|TRUNCATE\s+TABLE)\b", value, re.I):
                return True
            if _argument_action(value):
                return True
    elif isinstance(args, list):
        return any(_argument_action(value) for value in args)
    return False


def observation_safety_error(tool_name: str, args: Any) -> Optional[str]:
    """Reason an observed/resolved tool call is outside supported read semantics."""
    if not isinstance(tool_name, str) or not tool_name:
        return "The step has no tool name."
    if tool_name in ("terminal", "shell"):
        command = args.get("command") if isinstance(args, dict) else args
        return None if isinstance(command, str) and command.strip() and _shell_is_read_only(command) else "The shell command is outside the supported read-only subset."
    if tool_name == "execute_code":
        code = args.get("code") if isinstance(args, dict) else args
        return None if isinstance(code, str) and code.strip() and _python_is_read_only(code) else "The Python code is outside the supported read-only subset."
    if _argument_action(args):
        return "The tool arguments request an action rather than a read."
    if tool_name in {"skill_view", "skills_list", "todo_read", "memory_view"} or tool_name in _KNOWN_READ_TOOLS:
        return None
    if tool_name in SIDE_EFFECT_TOOLS or _SIDE_EFFECT_NAME.search(tool_name) or not _READ_NAME.search(tool_name):
        return "The tool is not recognized as a read."
    return None


def replay_safety_error(recipe: Any) -> Optional[str]:
    """Revalidate saved routines, including legacy ones, before any dispatch.

    Explicit binding metadata is validated and resolved by the runner. This helper
    checks concrete tool semantics and rejects implicit legacy date/id/temp rewriting.
    The runner must also call observation_safety_error on every resolved call.
    """
    if not isinstance(recipe, dict) or not isinstance(recipe.get("steps"), list) or not recipe["steps"]:
        return "The routine has no valid steps."
    for index, step in enumerate(recipe["steps"], 1):
        if not isinstance(step, dict) or "args" not in step or not isinstance(step.get("tool"), str):
            return f"Step {index} has an invalid saved shape."
        fill_ins = step.get("fill_ins") or []
        if not isinstance(fill_ins, list) or any(not isinstance(kind, str) for kind in fill_ins):
            return f"Step {index} has invalid fill-ins."
        if fill_ins:
            return f"Step {index} has legacy implicit fill-ins; replace them with explicit parameter bindings."
        args = step["args"]
        # The parameter validator constrains this expression to a literal executable
        # and individually quoted scalar arguments. Only the resolved call can be
        # classified, and runner.run must recheck it immediately before dispatch.
        bound_argv = (step["tool"] in {"terminal", "shell"} and isinstance(args, dict)
                      and isinstance(args.get("command"), dict) and set(args["command"]) == {"$argv"})
        reason = None if bound_argv else observation_safety_error(step["tool"], args)
        if reason:
            return f"Step {index}: {reason}"
    return None


def _is_side_effect(tool_name: str, args: Any) -> bool:
    """Would repeating this step change something? When unsure, yes, because tests repeat steps."""
    return observation_safety_error(tool_name, args) is not None


def _avg(values: list[Optional[float]]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


# ---------------------------------------------------------------- best run

_URL = re.compile(r"https?://[^\s)\"'<>]+")
_REPO = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b")


def prompt_sources(prompt: Optional[str]) -> list[str]:
    """The URLs and owner/repo names a job's prompt asks Hermes to read."""
    if not prompt:
        return []
    found: list[str] = []
    for url in _URL.findall(prompt):
        url = url.rstrip(".,;:!?")
        token = re.sub(r"^https?://", "", url).split("?")[0].rstrip("/").lower()
        if token and token not in found:
            found.append(token)
    text_without_urls = _URL.sub(" ", prompt)
    for repo in _REPO.findall(text_without_urls):
        if "." not in repo.split("/")[0] and repo.lower() not in found:
            found.append(repo.lower())
    return found


def _sources_in(args: Any, sources: list[str]) -> set[str]:
    text = _canon(args).lower()
    return {s for s in sources if s in text}


_ID_LIKE = re.compile(r"(?<![\w-])(?=[\w-]*\d)[\w-]{4,}(?![\w-])")
_DATE = _FILL_INS[0][1]


@dataclass
class _Only:
    steps: list[Any]


def _dates_are_today(args: Any, started: float) -> bool:
    """A routine can only fill in today's date, so a range or another day can't be repeated."""
    dates = {d[:10] for d in _DATE.findall(_canon(args))}
    if len(dates) > 1:
        return False
    if not dates or not started:
        return not dates
    ran = _dt.datetime.fromtimestamp(started, _dt.timezone.utc).date()
    try:
        return abs((_dt.date.fromisoformat(next(iter(dates))) - ran).days) <= 1
    except ValueError:
        return False


def _uses_earlier_result(step: Any, earlier: list[str]) -> bool:
    """Does this step use an id that an earlier step found? That id can be new on the next run."""
    if not earlier:
        return False
    args = _DATE.sub(" ", _canon(step.args))
    return any(token in text for token in _ID_LIKE.findall(args) for text in earlier)


def _depends_on_earlier(run: Any) -> bool:
    earlier: list[str] = []
    for s in run.steps:
        if s.tool_name in SETUP_TOOLS or _is_side_effect(s.tool_name, s.args):
            continue
        if _uses_earlier_result(s, earlier):
            return True
        if s.result_preview:
            earlier.append(s.result_preview)
    return False


def _unstable(plans: list[StepPlan], per_position: list[list[Any]], runs: list[Any]) -> Optional[str]:
    """Why these steps can't safely repeat by themselves. Routines start with independent, stable reads."""
    started_of = {id(s): r.started_ts for r in runs for s in r.steps}
    for plan, steps in zip(plans, per_position):
        if plan.kind == "varies":
            return f"Its {plan.tool_name} step changes from run to run, so repeating it could fetch the wrong thing."
        if plan.kind in ("fill-in", "fixed"):
            if set(plan.fill_ins) - {"date"}:
                return f"Its {plan.tool_name} step uses ids or temporary paths that change each run."
            if plan.kind == "fill-in" and not all(_dates_are_today(s.args, started_of.get(id(s), 0)) for s in steps):
                return f"Its {plan.tool_name} step uses a date range or another day, which can't be filled in safely."
    if any(_depends_on_earlier(r) for r in runs):
        return "One step uses an id found by an earlier step, and that id can change, so Hermes keeps doing it."
    return None


def _best_run(runs: list[Any], sources: list[str]) -> Optional[tuple[Any, list[Any]]]:
    """The cheapest run whose fetch steps cover the most sources, and those steps."""
    best_key, best = None, None
    for run in runs:
        steps, covered = [], set()
        for s in run.steps:
            if s.tool_name in SETUP_TOOLS or _is_side_effect(s.tool_name, s.args):
                continue
            if s.status not in (None, "ok") or not s.result_len:
                continue
            # Only a stable read can be frozen: nothing here may change from run to run.
            if set(_blank(_canon(s.args))[1]) - {"date"} or not _dates_are_today(s.args, run.started_ts):
                continue
            if _depends_on_earlier(_Only(run.steps[: run.steps.index(s) + 1])):
                continue
            hit = _sources_in(s.args, sources)
            if hit:
                steps.append(s)
                covered |= hit
        if not steps or covered != set(sources):
            continue
        key = (-len(covered), run.cost_usd if run.cost_usd is not None else 1e9, len(steps))
        if best_key is None or key < best_key:
            best_key, best = key, (run, steps)
    return best


def _plan_best_run(base: Analysis, runs: list[Any], sources: list[str]) -> bool:
    picked = _best_run(runs, sources)
    if not picked:
        return False
    run, steps = picked
    plans = []
    for i, s in enumerate(steps):
        _, kinds = _blank(_canon(s.args))
        plans.append(StepPlan(
            position=i, tool_name=s.tool_name, kind="frozen", args=s.args, fill_ins=kinds,
            result_lens=[s.result_len] if s.result_len else [],
            note=f"Frozen from the run on {_when(run)} that fetched every source for the least money.",
        ))
    covered = set().union(*(_sources_in(s.args, sources) for s in steps))
    costs = [r.cost_usd for r in runs if r.cost_usd is not None]
    base.strategy = "best-run"
    base.source_execution_id = run.execution_id
    base.steps = plans
    base.est_api_calls_after = max(1.0, (base.avg_api_calls or 2.0) - len(plans))
    if base.avg_api_calls and base.avg_cost_usd is not None:
        base.est_cost_after_usd = base.avg_cost_usd * min(1.0, base.est_api_calls_after / base.avg_api_calls)
    spread = f" (cost ${min(costs):.4f}–${max(costs):.4f})" if costs else ""
    base.ready = True
    base.reason = (f"Hermes takes a different path each run{spread}. Crystallize can freeze the "
                   f"{len(plans)} fetch step{'s' if len(plans) != 1 else ''} from its cheapest run, "
                   f"covering {len(covered)} of {len(sources)} sources.")
    return True


def _when(run: Any) -> str:
    import datetime as _dt

    try:
        return _dt.datetime.fromtimestamp(run.started_ts).strftime("%b %d %H:%M")
    except (TypeError, ValueError, OSError):
        return "an earlier run"


# ---------------------------------------------------------------- main entry

def analyze_runs(runs: list[Any], prompt: Optional[str] = None) -> Analysis:
    """Analyze usable runs of one job (``history.Run`` objects, oldest first).

    ``prompt`` is the job's own prompt. When Hermes takes a different path every
    run, its URLs and repo names let us fall back to the "best run" strategy.
    """
    runs = [r for r in runs if getattr(r, "usable", True) and r.steps]
    job_id = runs[-1].job_id if runs else None
    job_name = runs[-1].job_name if runs else None
    base = Analysis(job_id=job_id, job_name=job_name, runs_used=len(runs), ready=False, reason="")
    base.avg_tool_calls = _avg([len(r.steps) for r in runs])
    base.avg_api_calls = _avg([r.api_calls for r in runs])
    base.avg_cost_usd = _avg([r.cost_usd for r in runs])

    if len(runs) < MIN_RUNS:
        base.reason = f"Needs at least {MIN_RUNS} finished scheduled runs, has {len(runs)}."
        return base

    # A tool called twice may read two different resources. Matching only its name
    # can pair a newly inserted diagnostic with the old parser/fetch. For repeated
    # tools require the same normalized arguments when aligning backbone positions.
    repeated = {s.tool_name for r in runs for s in r.steps
                if sum(other.tool_name == s.tool_name for other in r.steps) > 1}
    sequences = [[(s.tool_name + "\0" + _blank(_canon(s.args))[0]) if s.tool_name in repeated else s.tool_name
                  for s in r.steps] for r in runs]
    backbone = _backbone(sequences)
    sources = prompt_sources(prompt)
    base.sources = sources
    if not backbone:
        if sources and _plan_best_run(base, runs, sources):
            return base
        base.reason = "The runs have no tool calls in common, so there's nothing stable to save."
        return base

    # Map each run's steps onto the backbone positions.
    per_position: list[list[Any]] = [[] for _ in backbone]
    extra = 0
    for run, seq in zip(runs, sequences):
        matched = _lcs(backbone, seq)
        for b_idx, s_idx in matched:
            per_position[b_idx].append(run.steps[s_idx])
        extra += len(seq) - len(matched)

    plans: list[StepPlan] = []
    for pos, steps in enumerate(per_position):
        tool = steps[-1].tool_name
        latest = steps[-1].args
        raw = [_canon(s.args) for s in steps]
        distinct = len(set(raw))
        plan = StepPlan(position=pos, tool_name=tool, kind="fixed", args=latest, distinct_inputs=distinct,
                        result_lens=[s.result_len for s in steps if s.result_len is not None])

        if any(_is_side_effect(tool, s.args) for s in steps):
            plan.kind, plan.note = "side", "Sends or changes something, so the agent keeps this step."
        elif tool in SETUP_TOOLS:
            plan.kind, plan.note = "setup", "Loads context for the agent; a script can skip it."
        elif distinct == 1:
            plan.kind = "fixed"
        else:
            blanked = [_blank(r) for r in raw]
            if len({b for b, _ in blanked}) == 1:
                plan.kind = "fill-in"
                plan.fill_ins = sorted({k for _, kinds in blanked for k in kinds})
            else:
                plan.kind = "varies"
                plan.note = (f"{distinct} different inputs across {len(steps)} runs. "
                             "Freezing the latest version is allowed only after it passes test runs.")
        plans.append(plan)

    base.steps = plans
    base.extra_steps_per_run = extra / len(runs)
    base.same_shape_share = sum(1 for s in sequences if s == backbone) / len(runs)

    # Rough estimate until test runs measure it: the agent still makes one call to
    # write the answer, plus one for each step it has to keep doing itself.
    agent_steps = sum(1 for p in plans if p.kind == "side") + round(base.extra_steps_per_run)
    base.est_api_calls_after = 1.0 + agent_steps
    if base.avg_api_calls and base.avg_cost_usd is not None:
        ratio = min(1.0, base.est_api_calls_after / base.avg_api_calls)
        base.est_cost_after_usd = base.avg_cost_usd * ratio

    scriptable = [p for p in plans if p.kind in ("fixed", "fill-in", "varies")]
    base.agent_free = (
        bool(scriptable) and agent_steps == 0
        and all(p.kind in ("fixed", "fill-in", "setup") for p in plans)
    )

    unstable = _unstable(plans, per_position, runs)
    if not scriptable:
        if sources and _plan_best_run(base, runs, sources):
            return base
        base.reason = "Every common step needs the agent, so there's nothing to save yet."
    elif unstable:
        if sources and _plan_best_run(base, runs, sources):
            return base
        base.agent_free = False
        base.reason = unstable
    elif base.same_shape_share < 0.5:
        if sources and _plan_best_run(base, runs, sources):
            return base
        base.reason = ("Runs take different paths more than half the time. "
                       "Wait for more runs before saving a step list.")
    else:
        base.ready = True
        base.reason = f"{len(scriptable)} of {len(plans)} common steps can run without the AI."
    return base


def run_actions(run: Any) -> set[str]:
    """What a run sent or changed, by tool. Failed attempts don't count."""
    return {s.tool_name for s in run.steps
            if _is_side_effect(s.tool_name, s.args) and s.status in (None, "ok", "success")}


def regular_actions(runs: list[Any]) -> set[str]:
    """Actions most of these runs took. One-off actions aren't expected every time. Only runs
    with recorded steps can show this, so older runs known just from Hermes's records don't count."""
    runs = [r for r in runs if r.steps]
    tools = {t for r in runs for t in run_actions(r)}
    return {t for t in tools if sum(t in run_actions(r) for r in runs) * 2 > len(runs)}


def job_prompt(job_id: Optional[str]) -> Optional[str]:
    """The job's own task text (the original one if a routine has taken over)."""
    if not job_id:
        return None
    from . import lifecycle

    job = lifecycle.cron_job(job_id)
    prompt = (job or {}).get("prompt")
    if prompt and lifecycle.MARKER in prompt and "\nTask:\n" in prompt:
        prompt = prompt.split("\nTask:\n", 1)[1]
    return prompt


def analyze_job(job: str) -> Analysis:
    from . import history

    runs = history.load_runs(job, only_usable=True)
    job_id = runs[-1].job_id if runs else None
    return analyze_runs(runs, job_prompt(job_id or job))


# ---------------------------------------------------------------- no-AI check

_DATA_TOKEN = re.compile(r"https?://[^\s)\]>,]+|#?\d+(?:\.\d+)?")
NO_AI_MAX_ANSWER = 1500


def no_ai_fit(runs: list[history.Run], last: int = 5) -> tuple[bool, str]:
    """Is this job's answer just its data, restated? Then it could run with no AI.

    Looks at the last few real answers: they must be short, and nearly every
    link and number in them must come straight from the tool outputs. A digest
    or summary fails this (it adds judgement); a status check passes.
    """
    meta_tools = {"tool_describe", "tool_search", "tool_call", "crystal_run", "crystal_list"}
    runs = [r for r in runs if r.usable and r.session_id
            and any(s.tool_name not in meta_tools for s in r.steps)][-last:]
    if len(runs) < 3:
        return False, "needs at least 3 runs to judge"
    answers = history.final_answers([r.session_id for r in runs])
    covered = []
    for r in runs:
        answer = answers.get(r.session_id or "")
        if not answer:
            return False, "some runs have no recorded answer"
        if len(answer) > NO_AI_MAX_ANSWER:
            return False, "its answers are longer than the data they restate"
        # Dates and clock times are the AI stamping its answer, not facts from the data.
        plain = re.sub(r"\d{4}-\d\d-\d\d([T ]\d\d:\d\d(:\d\d)?)?|\b\d{1,2}:\d\d(:\d\d)?\b", " ", answer)
        tokens = [t.rstrip(".") for t in _DATA_TOKEN.findall(plain)]
        if len(tokens) < 2:
            return False, "its answers aren't made of data"
        data = " ".join(s.result_preview or "" for s in r.steps)
        covered.append(sum(1 for t in tokens if t.lstrip("#") in data) / len(tokens))
    share = min(covered)
    if share < 0.8:
        return False, f"only {share:.0%} of the facts in its answers come from the data"
    return True, f"{share:.0%} of the links and numbers in its answers come straight from the data"
