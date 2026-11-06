#!/usr/bin/env python3
"""Build a reviewable catalog submission from an existing, exact Git commit.

No network, scanner bypass, guessed pin, git mutation, or self-referential pin.
The generated files are a submission bundle, never evidence of catalog admission.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CAPABILITIES = ("provides_tools", "provides_hooks", "provides_middleware", "requires_env")
REGISTRATIONS = {"register_tool": "provides_tools", "register_hook": "provides_hooks", "register_middleware": "provides_middleware"}
CORE_PREFIXES = ("agent", "hermes_cli", "cron", "model_tools", "tools.registry", "providers", "plugins.plugin_storage", "hermes_constants")


class BundleError(ValueError):
    pass


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=False, text=True, capture_output=True)
    if result.returncode:
        raise BundleError("Git could not read the requested local commit; no fetch was attempted.")
    return result.stdout


class GitTree:
    def __init__(self, repo: Path, sha: str):
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha) or set(sha) == {"0"}:
            raise BundleError("Use a real full 40-character commit SHA, not a ref, short SHA or placeholder.")
        self.repo, self.sha = repo.resolve(), sha.lower()
        resolved = _git(self.repo, "rev-parse", "--verify", self.sha + "^{commit}").strip()
        if resolved != self.sha:
            raise BundleError("The pin must identify the commit itself, not a tag object.")
        self.paths = _git(self.repo, "ls-tree", "-r", "--name-only", self.sha).splitlines()
        if "plugin.yaml" not in self.paths or not any(p in self.paths for p in ("__init__.py", "desktop/plugin.js", "plugin.json", "dashboard/manifest.json")):
            raise BundleError("The pinned tree is missing its manifest or a loadable entrypoint.")

    def read(self, path: str) -> str:
        if path not in self.paths:
            raise BundleError("Required file is absent at the pinned commit: " + path)
        return _git(self.repo, "show", self.sha + ":" + path)


def _scalar(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        value = json.loads(raw)
        if not isinstance(value, str):
            raise BundleError("Expected a manifest string.")
        return value
    if raw.startswith("'"):
        if not raw.endswith("'"):
            raise BundleError("Unterminated manifest string.")
        return raw[1:-1].replace("''", "'")
    if not raw or any(c in raw for c in "[]{}\n\r") or raw.startswith(("&", "*", "!", "|", ">")):
        raise BundleError("Unsupported manifest scalar; use a plain or quoted string.")
    return raw


def read_manifest(text: str) -> dict[str, Any]:
    """Read the small manifest surface needed here, fail closed on complex YAML.

    This is deliberately not a general YAML parser or a Hermes validator. It
    accepts scalar metadata and literal inline/block capability lists only.
    Other manifest sections are retained by Hermes, not interpreted here.
    """
    lines = text.splitlines()
    sections: dict[str, tuple[str, list[str]]] = {}
    for index, line in enumerate(lines):
        if not line or line.startswith((" ", "\t", "#")):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*):\s*(.*)", line)
        if not match:
            raise BundleError("Unsupported top-level manifest syntax.")
        key, raw = match.groups()
        if key in sections:
            raise BundleError("Duplicate manifest key: " + key)
        body = []
        for following in lines[index + 1:]:
            if following and not following.startswith((" ", "\t", "#")):
                break
            body.append(following)
        sections[key] = raw, body
    result: dict[str, Any] = {}
    for key in ("name", "version", "requires_hermes", "author", "homepage"):
        if key not in sections:
            raise BundleError("Pinned manifest is missing " + key)
        result[key] = _scalar(sections[key][0])
    description, body = sections.get("description", ("", []))
    result["description"] = " ".join(line.strip() for line in body if line.strip()) if description in {">", ">-", "|", "|-"} else _scalar(description)
    for key in CAPABILITIES:
        raw, body = sections.get(key, ("[]", []))
        if raw.strip().startswith("["):
            if not raw.strip().endswith("]"):
                raise BundleError("Capability lists must be on one line or use literal block items.")
            inner = raw.strip()[1:-1].strip()
            values = [_scalar(item) for item in inner.split(",")] if inner else []
        elif not raw.strip():
            values = []
            for line in body:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                match = re.fullmatch(r"\s+-\s+(.+?)\s*", line)
                if not match:
                    raise BundleError("Capability declarations must be literal name lists.")
                values.append(_scalar(match.group(1)))
        else:
            raise BundleError("Capability declarations must be literal name lists.")
        if any(not re.fullmatch(r"[A-Za-z0-9_.:-]+", value) for value in values) or len(values) != len(set(values)):
            raise BundleError("Invalid or duplicate capability in " + key)
        result[key] = sorted(values)
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", result["name"]):
        raise BundleError("Invalid catalog/plugin name.")
    if not re.fullmatch(r">=\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9.-]+)?", result["requires_hermes"]):
        raise BundleError("requires_hermes must be a SemVer floor.")
    return result


def analyze_sources(sources: dict[str, str]) -> dict[str, Any]:
    registered: dict[str, list[str]] = {key: [] for key in REGISTRATIONS.values()}
    imports: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for path, source in sorted(sources.items()):
        if not path.endswith(".py") or path.startswith(("tests/", "scripts/", "catalog/")):
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise BundleError("Pinned Python source could not be parsed: " + path) from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in REGISTRATIONS:
                candidate = next((k.value for k in node.keywords if k.arg == "name"), node.args[0] if node.args else None)
                if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                    registered[REGISTRATIONS[node.func.attr]].append(candidate.value)
                else:
                    unresolved.append({"file": path, "line": node.lineno, "registration": node.func.attr})
            modules = [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [a.name for a in node.names] if isinstance(node, ast.Import) else []
            for module in modules:
                if any(module == prefix or module.startswith(prefix + ".") for prefix in CORE_PREFIXES):
                    imports.append({"file": path, "line": node.lineno, "module": module})
    if unresolved:
        raise BundleError("Dynamic registrations require manual/runtime validation; a manifest match was not assumed.")
    if any(len(names) != len(set(names)) for names in registered.values()):
        raise BundleError("Duplicate runtime capability registrations were found.")
    return {"registered": {k: sorted(v) for k, v in registered.items()},
            "core_imports_requiring_public_api_review": imports,
            "requires_env_validation": "Manifest-declared mandatory names only; host load/secret-scope validation remains required."}


def validate_capabilities(manifest: dict[str, Any], analysis: dict[str, Any]) -> None:
    for key, values in analysis["registered"].items():
        if values != manifest[key]:
            raise BundleError(key + " differs between the pinned manifest and literal source registrations.")


def _repo_url(value: str) -> str:
    parsed = urlparse(value)
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if (parsed.scheme != "https" or parsed.hostname not in {"github.com", "gitlab.com"} or parsed.username
            or parsed.password or parsed.query or parsed.fragment or len(parts) != 2
            or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)):
        raise BundleError("Use a public HTTPS GitHub/GitLab owner/repository URL without credentials or query parameters.")
    return "https://" + parsed.hostname + "/" + "/".join(parts)


def build_entry(manifest: dict[str, Any], sha: str, repo_url: str, analysis: dict[str, Any]) -> dict[str, Any]:
    entry = {"name": manifest["name"], "repo": _repo_url(repo_url), "sha": sha,
             "description": "Reduces repeated Hermes chat and cron work with explicit controls and local receipts.",
             "maintainer": manifest["author"], "tier": "community", "category": "automation",
             "requires_hermes": manifest["requires_hermes"], "version": manifest["version"],
             "title": "Crystallize", "readme": True,
             "known_issues": ["Submission bundle only; no catalog listing or admission is claimed.",
                              "Frozen fixture checks do not prove semantic equivalence or universal provider coverage."],
             "capabilities": {key: manifest[key] for key in CAPABILITIES}}
    if analysis["core_imports_requiring_public_api_review"]:
        entry["known_issues"].append("Private Hermes imports remain; public SDK migration and compatibility review are required before admission.")
    return entry


def yaml_entry(entry: dict[str, Any]) -> str:
    lines = ["# Submission bundle only. Copy into hermes-agent/plugin-catalog/ in a maintainer-reviewed PR."]
    for key, value in entry.items():
        if key == "capabilities":
            lines.append("capabilities:")
            lines.extend("  " + name + ": " + json.dumps(values) for name, values in value.items())
        elif isinstance(value, bool):
            lines.append(key + ": " + str(value).lower())
        elif isinstance(value, list):
            lines.append(key + ":")
            lines.extend("  - " + json.dumps(item, ensure_ascii=False) for item in value)
        else:
            lines.append(key + ": " + json.dumps(value, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def submission_text(entry: dict[str, Any], readiness: dict[str, Any]) -> str:
    handoff_disclosure = ("- Explicit delegated file handoffs can read requested files and write a requested output\n"
                          "  through guarded host tools. They obey the host's tool policy and approval path.\n") if readiness["pinned_features"]["delegated_file_handoffs"] else ""
    sdk_document = ("See `docs/sdk-gaps.md` and the exact file/line/module inventory in `readiness.json`."
                    if readiness["pinned_features"]["sdk_gap_document"] else
                    "See the exact file/line/module inventory in `readiness.json`; no SDK-gap document exists at this pin.")
    return f"""# Crystallize catalog submission draft

Submission status: **blocked on public SDK migration and admission validation**.
This file and the entry in this plugin repository are a preparation bundle. No upstream
catalog PR, catalog listing, maintainer approval, or marketplace compliance is claimed.

Pin: `{entry['sha']}` in {entry['repo']}; version `{entry['version']}`;
Hermes floor `{entry['requires_hermes']}`. The pin is an existing plugin feature commit;
the later catalog-only bundle commit must not replace it with a self-reference.

Crystallize reduces repeated chat and cron work through explicit per-chat/per-job
controls, guarded routines and local receipts. Main reasoning is not reduced by this
validation. Observable contracts and frozen fixtures do not establish semantic equivalence.

Disclosures for reviewer and installer:

- Hermes reads outside plugin data: session history/costs in `state.db`, cron outcomes
  in `cron/executions.db`, job definitions in `cron/jobs.json`, and public host configuration,
  pricing and inventory. These reads use current profile scope; they are not telemetry.
- Helper/compare features can make paid network requests through public `ctx.llm` to
  the user-selected host route. Public model/rate catalogs can also refresh through Hermes.
  Explicit validation makes only fresh answer-generation calls; it does not replay tools,
  old reasoning, deliveries or schedules. No universal provider test claim is made.
- Cron tuning/promotion/undo can run Hermes CLI children and edit explicitly selected
  job fields. Read routines use the host's guarded execution seam, can run shell read
  commands, and run again during routine validation. A read recognizer is not a sandbox.
{handoff_disclosure}- Local plugin data can contain captured tool arguments/results, original results retained
  for recovery, cache entries, contracts, answers, receipts and usage diagnostics. Avoid
  sensitive shared cache namespaces. Helper calls have bounded background workers;
  timeout does not establish zero cost and late usage remains part of accounting.
- The plugin does not read credential stores, own/refresh tokens or self-update. Hermes
  owns authentication. No telemetry or independent credential store is introduced.
- Public API gaps remain: guarded tool dispatch, atomic cron toolset edits, model/rate-card/
  observed-cost and inventory access. Static analysis found
  {len(readiness['source_analysis']['core_imports_requiring_public_api_review'])} core import sites requiring review.
  {sdk_document}

Validation to attach before submission: public-repository clone at this SHA; local
`hermes plugins validate /path/to/pinned-checkout --install-deps` output, including every
warning; source/SDK review; current UI screenshots; deterministic tests; and any separately
labelled live reports. The generator did not run an install scanner, test credentials,
verify publication, install dependencies, open an upstream PR, or establish admission.

The catalog's canonical rules require a human-reviewed PR adding this entry to
`NousResearch/hermes-agent/plugin-catalog/`. Only a maintainer merge lists it. An owner or
major contributor must submit it. Each future SHA change requires another reviewed PR.
"""


def generate(repo: Path, sha: str, output: Path, repo_url: str | None = None) -> dict[str, Any]:
    tree = GitTree(repo, sha)
    manifest = read_manifest(tree.read("plugin.yaml"))
    sources = {path: tree.read(path) for path in tree.paths if path.endswith(".py") and not path.startswith(("tests/", "scripts/", "catalog/"))}
    analysis = analyze_sources(sources)
    validate_capabilities(manifest, analysis)
    entry = build_entry(manifest, tree.sha, repo_url or manifest["homepage"], analysis)
    readiness = {"schema_version": 1, "status": "submission_bundle_only", "catalog_ready": False,
                 "pinned_commit": tree.sha, "pinned_manifest_version": manifest["version"],
                 "literal_capabilities_match_manifest": True, "source_analysis": analysis,
                 "pinned_features": {"delegated_file_handoffs": "handoffs.py" in tree.paths,
                                     "sdk_gap_document": "docs/sdk-gaps.md" in tree.paths},
                 "publication_verified": False, "upstream_catalog_pr": None, "maintainer_approval": "not_obtained",
                 "hermes_plugins_validate": {"status": "not_run_by_generator", "warnings_suppressed": False,
                                             "command": "hermes plugins validate /path/to/pinned-checkout --install-deps"},
                 "blockers": ["Public SDK migration/review of remaining core imports.",
                              "Hermes install/scanner validation at the exact pin, with warnings reviewed.",
                              "Public clone/pin availability and owner-or-major-contributor submission.",
                              "Upstream hermes-agent catalog PR and maintainer review/merge."],
                 "limitations": ["Static literal registration comparison is not a runtime-load or security scan.",
                                 "The local host catalog policy was used; re-read it at actual submission.",
                                 "No live provider, semantic-equivalence or actual-billing proof is manufactured."]}
    output.mkdir(parents=True, exist_ok=True)
    (output / (manifest["name"] + ".yaml")).write_text(yaml_entry(entry))
    (output / "readiness.json").write_text(json.dumps(readiness, indent=2) + "\n")
    (output / "submission.md").write_text(submission_text(entry, readiness))
    return readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--sha", required=True, help="Existing full 40-hex plugin feature commit; never a guessed/future hash.")
    parser.add_argument("--repo-url", help="Public HTTPS repository URL; defaults to the pinned manifest homepage.")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    try:
        readiness = generate(args.repo, args.sha, args.output, args.repo_url)
    except (BundleError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"pinned_commit": readiness["pinned_commit"], "catalog_ready": False,
                      "literal_capabilities_match_manifest": True, "output": str(args.output.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
