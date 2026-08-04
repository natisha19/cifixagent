from cifixagent.models import PackageChange
from cifixagent.projects import (
    LockedPackage,
    UvAdapter,
    add_pep621_dependency,
    diff_locked_packages,
    lock_dependency_paths,
    select_adapter,
)
from cifixagent.service import CIJanitorService
from cifixagent.validation import ValidationResult, Validator


class PassingValidator(Validator):
    def validate(self, repo_root, proposal, settings):
        return ValidationResult(True, settings.command, str(repo_root), 0, "", "")


def make_uv_project(root):
    (root / "pyproject.toml").write_text(
        """[project]
name = "demo"
version = "0.1.0"
dependencies = [
    "httpx>=0.27",
]

[tool.uv]
index = [{ name = "company", url = "https://packages.example.test/simple" }]
""",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(
        """version = 1

[[package]]
name = "httpx"
version = "0.27.0"
dependencies = [{ name = "httpcore" }]

[[package]]
name = "httpcore"
version = "1.0.5"
""",
        encoding="utf-8",
    )


def test_uv_adapter_reads_direct_and_resolved_dependencies(tmp_path):
    make_uv_project(tmp_path)
    adapter = select_adapter(tmp_path)

    assert isinstance(adapter, UvAdapter)
    metadata = adapter.metadata(tmp_path)
    assert metadata.adapter == "uv"
    assert metadata.index_configured is True
    assert metadata.lock_digest is not None
    assert adapter.declared_dependencies(tmp_path) == {"httpx"}
    assert [item.name for item in adapter.locked_packages(tmp_path)] == ["httpx", "httpcore"]


def test_uv_graph_paths_and_bounded_lockfile_diff():
    before = [LockedPackage("app", "1", ["lib"], None), LockedPackage("lib", "1", [], None)]
    after = [LockedPackage("app", "1", ["lib"], None), LockedPackage("lib", "2", [], None)]

    assert lock_dependency_paths(after, "lib") == [["app", "lib"]]
    assert diff_locked_packages(before, after) == [PackageChange("lib", "1", "2")]


def test_pep621_edit_is_minimal_and_does_not_duplicate():
    text = "[project]\ndependencies = [\n    \"httpx>=0.27\",\n]\n"

    updated, _, changed = add_pep621_dependency(text, "PyYAML")
    unchanged, _, duplicate = add_pep621_dependency(updated, "PyYAML")

    assert changed is True
    assert '"PyYAML",' in updated
    assert duplicate is False
    assert unchanged == updated


def test_uv_proposal_changes_only_pep621_manifest(tmp_path):
    make_uv_project(tmp_path)
    service = CIJanitorService(tmp_path, PassingValidator())

    proposal = service.propose("ModuleNotFoundError: No module named 'yaml'")

    assert proposal.project is not None
    assert proposal.project.adapter == "uv"
    assert proposal.requirement_change is not None
    assert proposal.requirement_change.path == "pyproject.toml"
    assert '"PyYAML",' in proposal.requirement_change.after
