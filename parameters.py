"""Explicit, non-executable bindings for user-authored read routines.

Values can refer to an earlier JSON result, a relative date, or a run-local
artifact. Shell arguments are assembled with shlex, never evaluated as code.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_NODES = {"$result", "$date", "$artifact", "$argv", "$url"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
MAX_STEPS = 100


class ParameterError(ValueError):
    pass


def contains(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(set(value) & _NODES) or any(contains(v) for v in value.values())
    return isinstance(value, list) and any(contains(v) for v in value)


def _walk(value: Any, earlier: set[Any], depth: int = 0) -> None:
    if depth > 24:
        raise ParameterError("Bindings are nested too deeply.")
    if isinstance(value, list):
        for item in value:
            _walk(item, earlier, depth + 1)
        return
    if not isinstance(value, dict):
        return
    keys = set(value) & _NODES
    if keys:
        if len(value) != 1 or len(keys) != 1:
            raise ParameterError("A binding must contain exactly one expression.")
        kind = next(iter(keys))
        spec = value[kind]
        if kind == "$result":
            if not isinstance(spec, dict) or set(spec) - {"step", "path"}:
                raise ParameterError("A result binding needs step and optional path.")
            ref, path = spec.get("step"), spec.get("path", [])
            if isinstance(ref, bool) or not isinstance(ref, (str, int)) or ref not in earlier:
                raise ParameterError("A result binding must refer to an earlier step.")
            if not isinstance(path, list) or len(path) > 16 or any(
                isinstance(p, bool) or not isinstance(p, (str, int)) or isinstance(p, int) and p < 0 for p in path
            ):
                raise ParameterError("A result path is a list of keys and nonnegative indexes.")
        elif kind == "$date":
            if not isinstance(spec, dict) or set(spec) - {"offset_days", "timezone", "format"}:
                raise ParameterError("A date binding accepts offset_days, timezone and format.")
            offset = spec.get("offset_days", 0)
            if isinstance(offset, bool) or not isinstance(offset, int) or abs(offset) > 36500:
                raise ParameterError("Date offset must be a bounded integer number of days.")
            if spec.get("format", "date") not in ("date", "datetime"):
                raise ParameterError("Date format must be date or datetime.")
            try:
                ZoneInfo(spec.get("timezone", "UTC"))
            except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
                raise ParameterError("Unknown date timezone.") from exc
        elif kind == "$artifact":
            if not isinstance(spec, str) or not _NAME.fullmatch(spec) or spec in (".", ".."):
                raise ParameterError("An artifact needs a simple filename, without directory traversal.")
        elif kind == "$argv":
            if not isinstance(spec, list) or not spec or len(spec) > 256 or not isinstance(spec[0], str):
                raise ParameterError("argv must start with a literal executable and have at most 256 arguments.")
            if not spec[0] or any(c.isspace() for c in spec[0]):
                raise ParameterError("argv needs one literal executable name.")
            for item in spec[1:]:
                _walk(item, earlier, depth + 1)
        elif kind == "$url":
            if not isinstance(spec, dict) or set(spec) != {"template", "values"}:
                raise ParameterError("A URL binding needs template and values.")
            template, values = spec["template"], spec["values"]
            if not isinstance(template, str) or len(template) > 8192 or not isinstance(values, dict):
                raise ParameterError("Invalid URL template.")
            if urlsplit(template).scheme not in ("http", "https"):
                raise ParameterError("URL templates must use HTTP or HTTPS.")
            slots = re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", template)
            if set(slots) != set(values) or re.sub(r"\{[A-Za-z][A-Za-z0-9_]*\}", "", template).count("{"):
                raise ParameterError("URL template fields must match its values.")
            _walk(values, earlier, depth + 1)
        return
    for item in value.values():
        _walk(item, earlier, depth + 1)


def validate(routine: dict[str, Any]) -> None:
    steps = routine.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        raise ParameterError("A recipe needs 1–100 read steps.")
    if len(json.dumps(routine, default=str)) > 500_000:
        raise ParameterError("Recipe exceeds the size limit.")
    earlier: set[Any] = set()
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not isinstance(step.get("args"), dict):
            raise ParameterError("Each recipe step needs an argument object.")
        alias = step.get("id", str(index))
        if not isinstance(alias, str) or not _NAME.fullmatch(alias) or alias in earlier:
            raise ParameterError("Step ids must be unique simple names.")
        args = step["args"]
        if contains(args.get("code")):
            raise ParameterError("Executable code cannot be generated from result bindings.")
        if contains(args.get("command")) and not (
            isinstance(args["command"], dict) and set(args["command"]) == {"$argv"}
        ):
            raise ParameterError("Bound shell commands must use an argv expression.")
        _walk(args, earlier)
        earlier.update((index, alias))


class RunParameters:
    def __init__(self, artifact_dir: Path, now: dt.datetime | None = None):
        self.artifact_dir = artifact_dir.resolve()
        self.now = now or dt.datetime.now(dt.timezone.utc)
        self.results: dict[Any, Any] = {}
        self.artifacts: dict[str, Path] = {}

    def record(self, index: int, step: dict[str, Any], result: Any) -> None:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                pass
        self.results[index] = result
        self.results[step.get("id", str(index))] = result

    @staticmethod
    def _scalar(value: Any) -> str:
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ParameterError("An argv/URL value must resolve to a string or number.")
        text = str(value)
        if len(text) > 65536 or "\x00" in text:
            raise ParameterError("Resolved scalar is invalid or too long.")
        return text

    def resolve(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"$result"}:
            spec = value["$result"]
            try:
                result = self.results[spec["step"]]
                for key in spec.get("path", []):
                    if isinstance(result, dict) and isinstance(key, str) or isinstance(result, list) and isinstance(key, int):
                        result = result[key]
                    else:
                        raise KeyError(key)
                return result
            except (KeyError, IndexError, TypeError) as exc:
                raise ParameterError("An earlier result did not contain the bound value; Hermes must finish the task.") from exc
        if set(value) == {"$date"}:
            spec = value["$date"]
            date = self.now.astimezone(ZoneInfo(spec.get("timezone", "UTC"))) + dt.timedelta(days=spec.get("offset_days", 0))
            return date.date().isoformat() if spec.get("format", "date") == "date" else date.isoformat(timespec="seconds")
        if set(value) == {"$artifact"}:
            name = value["$artifact"]
            path = self.artifact_dir / name
            self.artifacts[name] = path
            return str(path)
        if set(value) == {"$argv"}:
            return shlex.join([self._scalar(self.resolve(v)) for v in value["$argv"]])
        if set(value) == {"$url"}:
            spec = value["$url"]
            result = spec["template"]
            for key, item in spec["values"].items():
                result = result.replace("{" + key + "}", quote(self._scalar(self.resolve(item)), safe=""))
            return result
        return {key: self.resolve(item) for key, item in value.items()}

    def snapshot_artifacts(self) -> list[dict[str, str]]:
        snapshots, remaining = [], 2_000_000
        for name, path in self.artifacts.items():
            try:
                if path.is_symlink() or not path.is_file() or path.resolve().parent != self.artifact_dir:
                    raise ParameterError(f"Artifact {name} is missing or is not a regular run-local file.")
                size = path.stat().st_size
                if size > min(1_000_000, remaining):
                    raise ParameterError(f"Artifact {name} exceeds the complete-result limit; Hermes must finish the task.")
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ParameterError(f"Artifact {name} cannot be completely read as text; Hermes must finish the task.") from exc
            snapshots.append({"name": name, "content": content})
            remaining -= size
        return snapshots
