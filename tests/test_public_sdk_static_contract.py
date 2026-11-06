"""Offline guard against returning to undocumented Hermes integration seams."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from catalog import public_api_audit as sdk

ROOT = Path(__file__).resolve().parents[1]


def inspect(source):
    return sdk.audit_sources({"plugin.py": source})


def test_published_api_facts_are_exactly_pinned_and_not_an_installed_private_allowlist():
    provenance, public = sdk.policy()
    assert provenance["source_commit"] == "90d6b98fd86d20981ee06ddd5b7d93e4320f26a4"
    raw = (ROOT / "catalog/public-api.json").read_bytes()
    assert len(raw) == 5044
    assert hashlib.sha256(raw).hexdigest() == "aeafa3b0333bfbe01746774e3ffa74d46eee32478b398c38144956bd5be9b98b"
    assert len(public) == 89
    assert "agent.usage_pricing:CostResult" in public
    assert "agent.usage_pricing" not in public


def test_production_source_stays_within_reviewed_public_contract():
    result = sdk.audit(ROOT)
    assert result["ok"], json.dumps(result["findings"], indent=2)
    assert result["scope"].startswith("production Python")


@pytest.mark.parametrize("source,kind", [
    ("from agent.usage_pricing import CanonicalUsage", "private_core_import"),
    ("import hermes_cli.inventory as harmless", "private_core_import"),
    ("from providers import _private", "private_core_import"),
    ("from agent import usage_pricing as prices", "private_core_import"),
    ("import importlib as il\nload = il.import_module\nload('agent.' + 'usage_pricing')", "private_dynamic_import"),
    ("from importlib import import_module as load\nload('model_tools')", "private_dynamic_import"),
    ("load = __import__\nload('cron.' + 'jobs')", "private_dynamic_import"),
    ("import importlib\nload=getattr(importlib,'import_module')\nload('hermes_cli.inventory')", "private_dynamic_import"),
    ("import importlib\nimportlib.import_module('agent.' + user_choice)", "unresolved_dynamic_import"),
    ("import importlib.util\nimportlib.util.spec_from_file_location('run_agent','elsewhere.py')", "unresolved_dynamic_loader"),
    ("alias = ctx\ngetattr(alias, '_' + 'dispatch')()", "private_core_attribute"),
    ("ctx._agent.run()", "private_core_attribute"),
    ("from providers import get_provider_profile\np=get_provider_profile('x')\np._cache", "private_core_attribute"),
    ("import providers as p\np.get_provider_profile = replacement", "core_override"),
    ("alias = ctx\nsetattr(alias, 'dispatch_tool', replacement)", "core_override"),
    ("from unittest import mock\nmock.patch('run_agent.AIAgent.run', replacement)", "core_override"),
    ("import sys\ntable=sys.modules\ntable['run_agent']=replacement", "core_module_table_override"),
    ("import sys\ntable=getattr(sys,'modules')\ntable['cron.jobs']=replacement", "core_module_table_override"),
    ("from pathlib import Path\n(Path.home()/'.hermes'/'auth.json').read_text()", "credential_store_read"),
    ("from pathlib import Path\np=Path.home()/'.env'\np.read_text()", "credential_store_read"),
    ("import os\nkey='NOUS_'+'API_KEY'\nos.environ.get(key)", "credential_environment_read"),
    ("import os\nos.environ['OPENAI_API_KEY']", "credential_environment_read"),
    ("import keyring\nkeyring.get_password('vendor','user')", "credential_store_read"),
    ("from dotenv import load_dotenv as read\nread()", "credential_store_read"),
    ("exec('import model_tools')", "dynamic_code_execution"),
    ("import os\nos.environ['HERMES_YOLO_MODE']='1'", "approval_bypass_environment"),
    ("import subprocess\nsubprocess.run(['hermes','--yolo'],env={})", "unsafe_child_execution"),
    ("import subprocess\nsubprocess.run('hermes cron edit',shell=True,env={})", "unsafe_child_execution"),
    ("import subprocess\nsubprocess.run(['hermes','cron','list'])", "unspecified_child_environment"),
    ("import subprocess\nsubprocess.run(['hermes','cron','list'],env={'HERMES_YOLO_MODE':'1'})", "approval_bypass_environment"),
    ("import sys\nPKG='crystallize_panel_pkg'\nfor key in [m for m in sys.modules if True or m.startswith(PKG+'.')]:\n    del sys.modules[key]", "core_module_table_override"),
])
def test_private_access_aliases_overrides_credentials_and_unsafe_children_are_rejected(source, kind):
    result = inspect(source)
    assert not result["ok"]
    assert kind in {f["kind"] for f in result["findings"]}, result


@pytest.mark.parametrize("source", [
    "from providers import get_provider_profile\nprofile=get_provider_profile('custom')\nprofile.get_usage_cost(usage)",
    "from agent.usage_pricing import CostResult\nresult=CostResult()",
    "from hermes_cli.config import load_config_readonly\nsettings=load_config_readonly()",
    "ctx.llm.complete(messages=[{'role':'user','content':'fresh input'}])",
    "agent.secret_scope.get_secret('DECLARED_SECRET')",
    "import subprocess\nsubprocess.run(['hermes','cron','edit','id','--prompt','x'],env=profile_scoped_env())",
    "import sys\nname='_crystallize_own_registry'\nsys.modules.setdefault(name, own_module)",
])
def test_documented_public_surfaces_and_owned_state_are_allowed(source):
    assert inspect(source)["ok"], inspect(source)["findings"]


def test_unparsed_source_is_an_audit_failure_not_a_clean_pass():
    result = inspect("def broken(")
    assert result["findings"][0]["kind"] == "unparsed_source"


def test_catalog_install_disclosures_follow_pinned_public_dispatch_feature():
    from catalog import generate_entry as generator
    manifest = generator.read_manifest((ROOT / "plugin.yaml").read_text())
    analysis = {"core_imports_requiring_public_api_review": [], "public_dispatch_plan_fallback": False}
    old = generator.build_entry(manifest, "a" * 40, manifest["homepage"], analysis)
    assert not any("Automatic nested" in issue for issue in old["known_issues"])
    analysis["public_dispatch_plan_fallback"] = True
    current = generator.build_entry(manifest, "a" * 40, manifest["homepage"], analysis)
    assert any("pre-run/no-AI" in issue and "normal-host-tool plans" in issue for issue in current["known_issues"])
    assert any("unverified authentication" in issue and "not billing evidence" in issue for issue in current["known_issues"])


def test_public_cli_child_environment_drops_verified_yolo_bypass_and_retains_profile(monkeypatch):
    from crystallize import lifecycle
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    env = lifecycle.hermes_env()
    assert "HERMES_YOLO_MODE" not in env
    assert env["HERMES_HOME"] == os.environ["HERMES_HOME"]


@pytest.mark.parametrize("private", [False, True], ids=["public-real-git-pin", "private-real-git-pin"])
def test_real_git_submission_distinguishes_public_and_private_source(tmp_path, private):
    from catalog import generate_entry as generator
    # All objects/commits below are confined to an ephemeral fixture repository.
    # The production checkout, branches and published pins are never mutated.
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, text=True, capture_output=True).stdout.strip()
    git("-c", "init.templateDir=", "init", "--quiet")
    (repo / "plugin.yaml").write_text("name: crystallize\nversion: 0.4.0\nrequires_hermes: '>=0.22.7'\nauthor: itsflownium\nhomepage: https://github.com/itsflownium/hermes-crystallize\ndescription: Fixture\nprovides_tools: []\nprovides_hooks: []\nprovides_middleware: []\n")
    (repo / "__init__.py").write_text("from agent.usage_pricing import CanonicalUsage\n" if private else "from providers import get_provider_profile\n")
    git("add", "plugin.yaml", "__init__.py")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fixture source")
    sha = git("rev-parse", "HEAD")
    report = generator.generate(repo, sha, tmp_path / "bundle")
    assert report["technical_static_ready"] is not private
    assert report["technical_checks"]["public_api_only"] is not private
    assert bool(report["source_analysis"]["core_imports_requiring_public_api_review"]) is private
    assert report["technical_ready_for_submission"] is False
    assert report["admission"]["listed"] is False
