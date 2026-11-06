"""Independent static tripwires using the published Hermes plugin API manifest.

This imports no Hermes implementation. It does not replace the host validator,
execution tests, manual source review, publication checks or maintainer review.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

API_COMMIT = "90d6b98fd86d20981ee06ddd5b7d93e4320f26a4"
API_SHA256 = "aeafa3b0333bfbe01746774e3ffa74d46eee32478b398c38144956bd5be9b98b"
_HANDLES = {"ctx", "context", "agent", "server", "db", "gateway", "AIAgent", "PluginContext"}
_UNSAFE_FLAGS = {"--yolo", "--non-interactive", "--auto-approve", "--dangerously-skip-permissions"}
_CREDENTIAL_PATH = re.compile(r"(?:^|[/\\])(?:auth\.json|credentials\.json|token\.json|\.env(?:\.[^/\\]*)?)(?:$|[/\\])|keychains?[/\\]|(?:chrome|chromium)[/\\].*(?:login data|cookies)", re.I)


def policy() -> tuple[dict[str, Any], frozenset[str]]:
    folder = Path(__file__).resolve().parent
    raw = (folder / "public-api.json").read_bytes()
    provenance = json.loads((folder / "sdk-policy.json").read_text())
    if hashlib.sha256(raw).hexdigest() != API_SHA256 or provenance.get("source_sha256") != API_SHA256 or provenance.get("source_commit") != API_COMMIT:
        raise ValueError("Published API facts differ from the reviewed commit/hash; policy review is required.")
    data = json.loads(raw)
    return provenance, frozenset(entry for group in data["groups"] for entry in group["entries"])


def runtime_sources(root: Path) -> dict[str, str]:
    """Production Python + dashboard; standalone validation/generator/test code excluded."""
    sources = {}
    skipped = {"tests", "test", "scripts", "benchmarks", "work", "__pycache__", "node_modules", "venv", ".venv"}
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if any(p in skipped or p.startswith(".") for p in rel.parts[:-1]) or path.name.startswith("test_") or path.name == "conftest.py":
            continue
        if rel.parts[0] == "catalog" and rel.as_posix() not in {"catalog/public_api_audit.py", "catalog/__init__.py"}:
            continue
        sources[rel.as_posix()] = path.read_text(encoding="utf-8-sig")
    return sources


def _name(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _name(node.value, aliases)
        return base + "." + node.attr if base else ""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
        base = _name(node.args[0], aliases)
        return base + "." + node.args[1].value if base else ""
    return ""


def _text(node: ast.AST | None, constants: dict[str, str], *, prefix: bool = False) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left, constants, prefix=prefix), _text(node.right, constants, prefix=prefix)
        return left + right if left is not None and right is not None else left if prefix else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            text = _text(value.value if isinstance(value, ast.FormattedValue) else value, constants, prefix=prefix)
            if text is None:
                return "".join(parts) if prefix else None
            parts.append(text)
        return "".join(parts)
    return None


def audit_sources(sources: dict[str, str]) -> dict[str, Any]:
    provenance, published = policy()
    public = published
    core = set(provenance["core_roots"]) | {"agent", "tools", "hermes_cli", "gateway", "tui_gateway", "cron",
             "acp_adapter", "plugins", "providers", "hermes_platform", "pm", "hermes_plugins", "model_tools", "run_agent", "hermes_constants", "hermes_state"}
    findings: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []

    def allowed(module: str, name: str = "") -> bool:
        return (module + ":" + name in public or module + ("." + name if name else "") in public
                or module in public and not name.startswith("_"))

    for path, source in sorted(sources.items()):
        try:
            tree = ast.parse(source, filename=path)
        except (SyntaxError, ValueError):
            findings.append({"file": path, "line": 0, "kind": "unparsed_source", "target": "Python source"})
            continue
        aliases: dict[str, str] = {}
        constants: dict[str, str] = {}
        tainted = set(_HANDLES)
        own_keys: set[str] = set()
        path_fragments: dict[str, set[str]] = {}

        def hit(node: ast.AST, kind: str, target: str) -> None:
            value = {"file": path, "line": getattr(node, "lineno", 0), "kind": kind, "target": target[:180]}
            if value not in findings:
                findings.append(value)

        def owned(value: str | None) -> bool:
            return bool(value) and value.startswith(("crystallize_", "_crystallize_"))

        def own_filter(node: ast.AST, variable: str) -> bool:
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
                return isinstance(node.left, ast.Name) and node.left.id == variable and owned(_text(node.comparators[0], constants))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith" and isinstance(node.func.value, ast.Name) and node.func.value.id == variable and len(node.args) == 1:
                value = _text(node.args[0], constants)
                return owned(value) and bool(value.endswith("."))
            if isinstance(node, ast.BoolOp):
                return (all if isinstance(node.op, ast.Or) else any)(own_filter(v, variable) for v in node.values)
            return False

        def core_value(node: ast.AST) -> bool:
            if isinstance(node, ast.Name):
                return node.id in tainted or _name(node, aliases).split(".")[0] in core
            if isinstance(node, ast.Attribute):
                return core_value(node.value)
            if isinstance(node, ast.Call):
                return core_value(node.func) or (_name(node.func, aliases) == "getattr" and bool(node.args) and core_value(node.args[0]))
            return False

        # Resolve aliases and constant concatenations before inspecting access.
        for _ in range(5):
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for item in node.names:
                        aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    for item in node.names:
                        aliases[item.asname or item.name] = node.module + "." + item.name
                elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            if (value := _text(node.value, constants)) is not None:
                                constants[target.id] = value
                            if (value := _name(node.value, aliases)):
                                aliases[target.id] = value
                            if core_value(node.value):
                                tainted.add(target.id)
                            fragments = {n.value for n in ast.walk(node.value) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
                            fragments |= {value for n in ast.walk(node.value) if isinstance(n, ast.Name) for value in path_fragments.get(n.id, ())}
                            path_fragments[target.id] = fragments
                elif isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.ListComp):
                    # Dashboard cache eviction is limited to its own namespace.
                    comp = node.iter
                    generator = comp.generators[0] if len(comp.generators) == 1 else None
                    if (generator and isinstance(generator.target, ast.Name) and isinstance(comp.elt, ast.Name)
                            and comp.elt.id == generator.target.id and _name(generator.iter, aliases) == "sys.modules"
                            and any(own_filter(value, generator.target.id) for value in generator.ifs)):
                        own_keys.add(node.target.id)

        for node in ast.walk(tree):
            pairs = []
            if isinstance(node, ast.Import):
                pairs = [(a.name, "") for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                pairs = [(node.module, a.name) for a in node.names]
            for module, name in pairs:
                if module.split(".")[0] in core:
                    target = module + ("." + name if name else "")
                    imports.append({"file": path, "line": node.lineno, "module": module, "name": name, "public": allowed(module, name)})
                    if not allowed(module, name):
                        hit(node, "private_core_import", target)
            if isinstance(node, ast.Attribute) and node.attr.startswith("_") and not node.attr.startswith("__") and core_value(node.value):
                hit(node, "private_core_attribute", _name(node, aliases))
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
            for target in targets:
                if isinstance(target, ast.Attribute) and core_value(target.value):
                    hit(node, "core_override", _name(target, aliases))
                if isinstance(target, ast.Subscript) and _name(target.value, aliases) == "sys.modules":
                    key = _text(target.slice, constants)
                    if not owned(key) and not isinstance(target.slice, ast.Name) or isinstance(target.slice, ast.Name) and target.slice.id not in own_keys and not owned(key):
                        hit(node, "core_module_table_override", key or "unresolved sys.modules key")
                if isinstance(target, ast.Subscript) and _name(target.value, aliases) == "os.environ" and _text(target.slice, constants) == "HERMES_YOLO_MODE":
                    value = getattr(node, "value", None)
                    if not isinstance(value, ast.Constant) or str(value.value).lower() not in {"0", "false", "none", ""}:
                        hit(node, "approval_bypass_environment", "HERMES_YOLO_MODE")
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and _name(node.value, aliases) == "os.environ":
                key = _text(node.slice, constants)
                if key and re.search(r"(?:API_KEY|TOKEN|PASSWORD|SECRET)$", key):
                    hit(node, "credential_environment_read", key)
            if not isinstance(node, ast.Call):
                continue
            call = _name(node.func, aliases)
            if call in {"eval", "exec", "compile", "builtins.eval", "builtins.exec", "builtins.compile"}:
                hit(node, "dynamic_code_execution", call)
            if call in {"importlib.import_module", "__import__", "builtins.__import__"}:
                module = _text(node.args[0], constants) if node.args else None
                head = module or (_text(node.args[0], constants, prefix=True) if node.args else "")
                if module and module.split(".")[0] in core and not allowed(module):
                    hit(node, "private_dynamic_import", module)
                elif module is None and not owned(head):
                    hit(node, "unresolved_dynamic_import", head or "computed import")
            if call.endswith("spec_from_file_location"):
                module = _text(node.args[0], constants) if node.args else None
                if not owned(module):
                    hit(node, "unresolved_dynamic_loader", module or "computed module name")
            if call.endswith((".patch", ".patch.object")) and node.args:
                target = _text(node.args[0], constants)
                if target and target.split(".")[0] in core or core_value(node.args[0]):
                    hit(node, "core_override", target or "patched core object")
            if call in {"getattr", "builtins.getattr", "setattr", "builtins.setattr", "delattr", "builtins.delattr"} and len(node.args) >= 2 and core_value(node.args[0]):
                attribute = _text(node.args[1], constants)
                if call.endswith(("setattr", "delattr")):
                    hit(node, "core_override", attribute or "computed attribute")
                elif attribute is None or attribute.startswith("_"):
                    hit(node, "private_core_attribute", attribute or "unresolved getattr on core handle")
            if call.startswith("sys.modules.") and node.args:
                key = _text(node.args[0], constants)
                if key and key.split(".")[0] in core:
                    hit(node, "core_module_table_access", key)
                elif call.endswith(("setdefault", "update", "pop")) and not owned(key):
                    hit(node, "core_module_table_override", key or "computed sys.modules key")
            if call in {"os.getenv", "os.environ.get"} and node.args:
                key = _text(node.args[0], constants)
                if key and re.search(r"(?:API_KEY|TOKEN|PASSWORD|SECRET)$", key):
                    hit(node, "credential_environment_read", key)
            if call.startswith(("keyring.", "keychain.")) or call.endswith(("load_dotenv", "dotenv_values")):
                hit(node, "credential_store_read", call)
            if call == "open" or isinstance(node.func, ast.Attribute) and node.func.attr in {"read_text", "read_bytes"}:
                evidence = {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
                evidence |= {value for n in ast.walk(node) if isinstance(n, ast.Name) for value in path_fragments.get(n.id, ())}
                if any(_CREDENTIAL_PATH.search(value) for value in evidence):
                    hit(node, "credential_store_read", "credential-shaped file path")
            if call in {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call", "subprocess.check_output"}:
                flags = {n.value for n in ast.walk(node.args[0]) if isinstance(n, ast.Constant) and isinstance(n.value, str)} if node.args else set()
                if flags & _UNSAFE_FLAGS or any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords):
                    hit(node, "unsafe_child_execution", ",".join(sorted(flags & _UNSAFE_FLAGS)) or "shell=True")
                if not any(k.arg == "env" for k in node.keywords):
                    hit(node, "unspecified_child_environment", call)
                for keyword in node.keywords:
                    if keyword.arg == "env" and isinstance(keyword.value, ast.Dict):
                        for key, value in zip(keyword.value.keys, keyword.value.values):
                            if _text(key, constants) == "HERMES_YOLO_MODE" and (not isinstance(value, ast.Constant) or str(value.value).lower() not in {"0", "false", "none", ""}):
                                hit(node, "approval_bypass_environment", "HERMES_YOLO_MODE in child env")
    return {"ok": not findings, "findings": sorted(findings, key=lambda f: (f["file"], f["line"], f["kind"])),
            "public_core_imports": imports, "policy_commit": API_COMMIT, "policy_sha256": API_SHA256,
            "scope": "production Python and dashboard; standalone operator scripts/tests excluded",
            "limitations": ["Static tripwires are not a sandbox or full data-flow proof.", "Run the authoritative host validator and review all warnings at the exact pin."]}


def audit(root: str | Path) -> dict[str, Any]:
    try:
        return audit_sources(runtime_sources(Path(root)))
    except (OSError, ValueError, KeyError) as exc:
        return {"ok": False, "findings": [{"file": "catalog/public-api.json", "line": 0, "kind": "unverified_public_api_policy", "target": type(exc).__name__}],
                "policy_commit": API_COMMIT, "policy_sha256": API_SHA256}
