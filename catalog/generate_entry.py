#!/usr/bin/env python3
"""Build a reviewable catalog submission from an existing, exact Git commit.

No network, scanner bypass, guessed pin, git mutation, or self-referential pin.
The generated files are a submission bundle, never evidence of catalog admission.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from .public_api_audit import audit_sources as sdk_audit_sources
except ImportError:
    from public_api_audit import audit_sources as sdk_audit_sources

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

    def read_bytes(self, path: str) -> bytes:
        result = subprocess.run(["git", "-C", str(self.repo), "show", self.sha + ":" + path], check=False, capture_output=True)
        if result.returncode:
            raise BundleError("A pinned source/asset could not be read.")
        return result.stdout


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
    runtime = {p: s for p, s in sources.items() if not p.startswith(("tests/", "scripts/")) and (not p.startswith("catalog/") or p in {"catalog/public_api_audit.py", "catalog/__init__.py"})}
    audit = sdk_audit_sources(runtime)
    return {"registered": {k: sorted(v) for k, v in registered.items()},
            "public_dispatch_plan_fallback": "dispatch_support.py" in sources,
            "native_snapshot_sources": all(path in sources for path in ("native_cron.py", "native_cron_runtime.py")),
            "public_api_audit": audit,
            "core_imports_requiring_public_api_review": [f for f in audit["findings"] if "private" in f["kind"]],
            "documented_core_imports": audit.get("public_core_imports", []),
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
            or any(part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)):
        raise BundleError("Use a public HTTPS GitHub/GitLab owner/repository URL without credentials or query parameters.")
    return "https://" + parsed.hostname + "/" + "/".join(parts)


def payload_findings(tree: GitTree) -> list[dict[str, str]]:
    """Inspect tracked payloads at the pin; no extraction or executable loading."""
    findings = []
    magic = (b"\x7fELF", b"MZ", b"\x00asm", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
             b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
    for path in tree.paths:
        data = tree.read_bytes(path)
        if path.endswith((".pyc", ".pyo")) or data.startswith(magic):
            findings.append({"file": path, "kind": "unreviewable_executable_payload"})
        if path.endswith((".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
            try:
                if len(data) > 16_000_000:
                    raise ValueError("Archive exceeds review bound")
                if path.endswith(".zip"):
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        entries = [i for i in archive.infolist() if not i.is_dir()]
                        if len(entries) > 1000 or sum(i.file_size for i in entries) > 16_000_000 or any(i.file_size > 4_000_000 for i in entries):
                            raise ValueError("Oversized archive")
                        members = [(i.filename, archive.read(i)) for i in entries]
                else:
                    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                        entries = [i for i in archive.getmembers() if not i.isdir()]
                        if len(entries) > 1000 or sum(i.size for i in entries) > 16_000_000 or any(not i.isfile() or i.size > 4_000_000 for i in entries):
                            raise ValueError("Non-source member")
                        members = [(i.name, archive.extractfile(i).read()) for i in entries]
                if len(members) > 1000 or sum(len(b) for _, b in members) > 16_000_000:
                    raise ValueError("Archive exceeds review bound")
                for name, body in members:
                    if name.endswith((".pyc", ".pyo")) or body.startswith(magic):
                        raise ValueError("Executable member")
                    body.decode("utf-8")
            except (ValueError, OSError, UnicodeError, zipfile.BadZipFile, tarfile.TarError):
                findings.append({"file": path, "kind": "unreviewable_archive"})
    return findings


def screenshot_urls(tree: GitTree, repo_url: str, paths: list[str]) -> list[str]:
    if len(paths) > 6 or len(paths) != len(set(paths)):
        raise BundleError("Catalog screenshots must be unique and at most six.")
    if paths and not repo_url.startswith("https://github.com/"):
        raise BundleError("Catalog images must use the permitted GitHub hosts.")
    urls = []
    for path in paths:
        if path not in tree.paths or not re.fullmatch(r"[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|webp)", path) or any(p in {".", ".."} for p in path.split("/")):
            raise BundleError("A screenshot must be an ordinary image file present at the exact pin.")
        data = tree.read_bytes(path)
        if not (data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")) or data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
            raise BundleError("Screenshot content does not match a supported image header.")
        urls.append(repo_url.replace("https://github.com/", "https://raw.githubusercontent.com/") + "/" + tree.sha + "/" + path)
    return urls


def build_entry(manifest: dict[str, Any], sha: str, repo_url: str, analysis: dict[str, Any]) -> dict[str, Any]:
    entry = {"name": manifest["name"], "repo": _repo_url(repo_url), "sha": sha,
             "description": "Spend less on repeated work in Hermes chats and cron jobs, without making your model think less.",
             "maintainer": manifest["author"], "tier": "community", "category": "automation",
             "requires_hermes": manifest["requires_hermes"], "version": manifest["version"],
             "title": "Crystallize", "readme": True,
             "known_issues": ["Live savings at unchanged reasoning are measured on Nous and OpenAI Codex, with small samples and synthetic fixtures; other providers are untested. Hand-offs cost more in live tests and are off by default.",
                              "Frozen fixture checks do not prove semantic equivalence or universal provider coverage."],
             "capabilities": {key: manifest[key] for key in CAPABILITIES}}
    if analysis["core_imports_requiring_public_api_review"]:
        entry["known_issues"].append("Private Hermes imports remain; public SDK migration and compatibility review are required before admission.")
    if analysis.get("public_dispatch_plan_fallback"):
        entry["known_issues"].extend([
            "Automatic nested tool execution and pre-run/no-AI replay of learned tool plans are unavailable on the audited SDK; explicit tool mode returns pending normal-host-tool plans.",
            "Configured/declared routes have unverified authentication and availability. User rate cards and public provider quotes are estimates, not billing evidence.",
        ])
    if analysis.get("native_snapshot_sources"):
        entry["known_issues"].append("Optional native Python snapshot sources require explicit source approval, and a job that delivers needs confirmation that an unchanged run sends nothing; copied code/imports run with ordinary cron permissions, and host text trimming/redaction still apply.")
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
    handoff_disclosure = ""
    native_disclosure = ("- Explicit native snapshot sources use public cron CLI controls after complete-source review.\n"
                         "  Copies/wrappers/approval files stay under scripts/crystallize-native/; no nested tool replay.\n"
                         if readiness["pinned_features"].get("native_snapshot_sources") else "")
    if readiness["pinned_features"]["delegated_file_handoffs"]:
        if readiness["pinned_features"].get("public_dispatch_plan_fallback"):
            handoff_disclosure = ("- Explicit delegated file handoffs return pending ordinary-host read/write plans.\n"
                                  "  No raw registry/private adapter performs those operations.\n")
        else:
            handoff_disclosure = ("- Explicit delegated file handoffs in this older pin attempt requested file operations\n"
                                  "  through its guarded host adapter. Review its private dependency findings; this\n"
                                  "  older implementation is not the public-only plan fallback.\n")
    sdk_document = ("See `docs/sdk-gaps.md` and the exact file/line/module inventory in `readiness.json`."
                    if readiness["pinned_features"]["sdk_gap_document"] else
                    "See the exact file/line/module inventory in `readiness.json`; no SDK-gap document exists at this pin.")
    return f"""# Crystallize catalog submission draft

Technical state: **{readiness.get('technical_readiness', 'exact-pin verification required')}**.
This file and the entry in this plugin repository are a preparation bundle. They do
not themselves establish an upstream PR, catalog listing, maintainer approval, or
marketplace compliance. Independently checked publication/review status is recorded
separately; only a maintainer-merged upstream catalog entry establishes listing.

Pin: `{entry['sha']}` in {entry['repo']}; version `{entry['version']}`;
Hermes floor `{entry['requires_hermes']}`. The pin is an existing plugin feature commit;
the later catalog-only bundle commit must not replace it with a self-reference.

Crystallize targets repeated context and read planning through explicit per-chat/per-job
controls and local receipts. Main reasoning is not reduced. Live paired runs at unchanged reasoning on Nous saved 6%
on short tasks, 20 to 32% on working chats on two of three models, 7% on the third and 60%
in one cron pair, with small samples on synthetic fixtures. Those fixtures have explicitly scoped validation,
not proof of universal live task/provider coverage or semantic equivalence. Automatic
pre-run/no-AI replay of learned tools remains unavailable on the reviewed SDK;
older savings from that path do not transfer.

Disclosures for reviewer and installer:

- Hermes reads outside plugin data: session history/costs in `state.db`, cron outcomes
  in `cron/executions.db`, job definitions in `cron/jobs.json`, and public host configuration,
  explicit route configuration and user pricing cards. These reads use current profile scope;
  they are not telemetry. Public provider profiles may supply price quotes.
- Helper/compare features can make paid network requests through public `ctx.llm` to
  the user-selected host route. Route choices are configured/declared, not credential-store probes.
  Explicit validation makes only fresh answer-generation calls; it does not replay tools,
  old reasoning, deliveries or schedules. No universal provider test claim is made.
- Cron tuning/promotion/undo can run public Hermes CLI children and edit supported selected
  fields. Unsupported toolset/dispatch operations remain with the normal host agent; automatic
  nested reads and promotion are disabled without an available documented guarded seam.
{handoff_disclosure}{native_disclosure}- Local plugin data can contain captured tool arguments/results, original results retained
  for recovery, cache entries, contracts, answers, receipts and usage diagnostics. Avoid
  sensitive shared cache namespaces. Helper calls have bounded background workers;
  timeout does not establish zero cost and late usage remains part of accounting.
- The plugin does not read credential stores, own/refresh tokens or self-update. Hermes
  owns authentication. No telemetry or independent credential store is introduced.
- Published public API audit found
  {len(readiness['source_analysis']['core_imports_requiring_public_api_review'])} private import/access sites requiring review.
  Missing public capabilities are explicit unsupported features with normal-agent fallback;
  they are not implemented by private imports or guessed future methods.
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


def generate(repo: Path, sha: str, output: Path, repo_url: str | None = None, *,
             screenshots: list[str] | None = None, verification: Path | None = None) -> dict[str, Any]:
    tree = GitTree(repo, sha)
    manifest = read_manifest(tree.read("plugin.yaml"))
    sources = {path: tree.read(path) for path in tree.paths if path.endswith(".py") and not path.startswith(("tests/", "scripts/"))
               and (not path.startswith("catalog/") or path in {"catalog/public_api_audit.py", "catalog/__init__.py"})}
    analysis = analyze_sources(sources)
    validate_capabilities(manifest, analysis)
    entry = build_entry(manifest, tree.sha, repo_url or manifest["homepage"], analysis)
    entry["screenshots"] = screenshot_urls(tree, entry["repo"], screenshots or [])
    entry["docs_url"] = f"{entry['repo']}/blob/{tree.sha}/README.md"
    if "assets/social-preview.png" in tree.paths:
        entry["image"] = screenshot_urls(tree, entry["repo"], ["assets/social-preview.png"])[0]
    payloads = payload_findings(tree)
    static_ready = analysis["public_api_audit"]["ok"] and not payloads
    verified: dict[str, Any] = {"status": "not_supplied", "ok": False}
    if verification:
        raw = verification.read_bytes()
        supplied = json.loads(raw)
        if (supplied.get("pinned_commit") != tree.sha or supplied.get("source_kind") != "hermes_plugins_validate"
                or not isinstance(supplied.get("host_source_commit"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", supplied["host_source_commit"])
                or supplied["host_source_commit"] == "0" * 40
                or supplied.get("ok") is not True or supplied.get("errors") != []
                or supplied.get("private_import_findings") != [] or supplied.get("private_attribute_findings") != []
                or supplied.get("security_status") not in {"safe", "caution"}
                or not isinstance(supplied.get("warnings"), list)
                or not isinstance(supplied.get("checks_passed"), int) or isinstance(supplied["checks_passed"], bool) or supplied["checks_passed"] < 1
                or supplied.get("warnings") and supplied.get("warnings_reviewed") is not True):
            raise BundleError("Validation evidence must identify this exact pin, passed host checks, zero private access findings, and reviewed warnings.")
        verified = {"status": "operator_supplied_exact_pin_host_validation", "ok": True,
                    "sha256": hashlib.sha256(raw).hexdigest(), "checks_passed": supplied["checks_passed"],
                    "host_source_commit": supplied.get("host_source_commit"), "warnings": supplied.get("warnings", []),
                    "security_status": supplied["security_status"], "independently_authenticated_by_generator": False}
    technical_ready = static_ready and verified["ok"] and bool(entry["screenshots"])
    readiness = {"schema_version": 1, "status": "submission_bundle_only", "catalog_ready": False,
                 "release_ready": False, "owner_submission_approval": None,
                 "release_ready_meaning": "Technical checks do not establish live effectiveness, installed-build activation or owner approval to submit.",
                 "catalog_ready_meaning": "Human admission/listing is not established by local technical checks.",
                 "pinned_commit": tree.sha, "pinned_manifest_version": manifest["version"],
                 "literal_capabilities_match_manifest": True, "source_analysis": analysis,
                 "technical_static_ready": static_ready, "technical_ready_for_submission": technical_ready,
                 "technical_readiness": "exact-pin technical checks passed" if technical_ready else "static checks passed; exact-pin validation/screenshots pending" if static_ready else "blocked by static source/payload findings",
                 "technical_checks": {"literal_capabilities": True, "public_api_only": analysis["public_api_audit"]["ok"],
                                      "reviewable_payloads": not payloads, "payload_findings": payloads,
                                      "manifest_semver_floor": manifest["requires_hermes"], "host_validation": verified},
                 "ui_evidence": {"status": "offline_fixture_captures" if entry["screenshots"] else "not_supplied", "screenshots": entry["screenshots"], "live_proof": False},
                 "admission": {"listed": False, "maintainer_merge": "pending", "public_pin_availability": "not_checked_by_generator"},
                 "pinned_features": {"delegated_file_handoffs": "handoffs.py" in tree.paths,
                                     "public_dispatch_plan_fallback": "dispatch_support.py" in tree.paths,
                                     "native_snapshot_sources": all(path in tree.paths for path in ("native_cron.py", "native_cron_runtime.py")),
                                     "sdk_gap_document": "docs/sdk-gaps.md" in tree.paths},
                 "publication_verified": False, "upstream_catalog_pr": None, "maintainer_approval": "not_obtained",
                 "hermes_plugins_validate": {"status": "not_run_by_generator", "warnings_suppressed": False,
                                             "command": "hermes plugins validate /path/to/pinned-checkout --install-deps"},
                 "technical_blockers": (["Static source/payload audit findings must be resolved."] if not static_ready else [])
                                       + ([] if verified["ok"] else ["Exact-pin host validation must be attached."])
                                       + ([] if entry["screenshots"] else ["Current offline UI screenshots must be attached."]),
                 "blockers": (["Static source/payload audit findings must be resolved."] if not static_ready else [])
                              + ([] if technical_ready else ["Exact-pin host validation and current UI evidence must be attached."]) + [
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
    parser.add_argument("--screenshot", action="append", default=[], help="Pinned tracked UI PNG/JPEG/WebP; label offline fixture captures honestly.")
    parser.add_argument("--verification", type=Path, help="Operator-supplied exact-pin host-validation JSON; provenance retained, never fabricated.")
    args = parser.parse_args(argv)
    try:
        readiness = generate(args.repo, args.sha, args.output, args.repo_url, screenshots=args.screenshot, verification=args.verification)
    except (BundleError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"pinned_commit": readiness["pinned_commit"], "catalog_ready": False,
                      "literal_capabilities_match_manifest": True, "output": str(args.output.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
