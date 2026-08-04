from cifixagent.cli import main
from cifixagent.models import (
    BlastRadius,
    DependencyGraphReport,
    FailureObservation,
    FixProposal,
    RequirementChange,
)
from cifixagent.validation import CopyWorkspaceValidator, ValidationSettings


def test_package_entrypoint_exists():
    assert callable(main)


def test_cli_propose_json(tmp_path, capsys):
    (tmp_path / "requirements.txt").write_text("# demo\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("import yaml\n", encoding="utf-8")

    code = main(
        [
            "propose",
            "--repo",
            str(tmp_path),
            "--logs",
            "ModuleNotFoundError: No module named 'yaml'",
            "--format",
            "json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "PyYAML" in captured.out


def test_cli_analyze_unknown_exits_nonzero(capsys):
    code = main(["analyze", "--repo", ".", "--logs", "nothing useful"])
    assert code == 1


def test_cli_validate_refuses_apply_without_yes(tmp_path, capsys):
    (tmp_path / "requirements.txt").write_text("# demo\n", encoding="utf-8")

    code = main(
        [
            "validate",
            "--repo",
            str(tmp_path),
            "--logs",
            "ModuleNotFoundError: No module named 'yaml'",
            "--apply",
            "--command",
            "python -c print('ok')",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert "Refusing to apply without --yes" in captured.out


def test_copy_workspace_validator_timeout(tmp_path):
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    failure = FailureObservation(
        kind="ModuleNotFoundError",
        module="yaml",
        message="missing",
        excerpt="missing",
    )
    graph = DependencyGraphReport(
        repo_root=str(tmp_path),
        failure=failure,
        module="yaml",
        declared_distribution=None,
        proposed_distribution="PyYAML",
        confidence=0.97,
        rationale="mapping",
        is_stdlib=False,
        is_ambiguous=False,
        already_declared=False,
        blast_radius=BlastRadius(),
    )
    proposal = FixProposal(
        failure=failure,
        module="yaml",
        proposed_distribution="PyYAML",
        confidence=0.97,
        explanation="mapping",
        changed_files=["requirements.txt"],
        graph=graph,
        requirement_change=RequirementChange(
            path="requirements.txt",
            added_dependency="PyYAML",
            line_number=1,
            before="",
            after="PyYAML\n",
        ),
    )
    validator = CopyWorkspaceValidator()
    result = validator.validate(
        tmp_path,
        proposal,
        ValidationSettings(
            command=["python", "-c", "import time; time.sleep(5)"],
            timeout_seconds=1,
            install_dependencies=False,
        ),
    )
    assert result.success is False
    assert result.timed_out is True
